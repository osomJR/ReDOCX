from __future__ import annotations

"""Authenticated routes for secure team-message attachments only."""

import concurrent.futures
import logging
import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Request, UploadFile
from fastapi.responses import Response
from psycopg import errors as psycopg_errors
from psycopg.types.json import Jsonb

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db
from backend.team_audit import insert_team_audit_event, record_team_audit_event_best_effort
from backend.team_attachment_security import (
    TeamAttachmentSecurityError,
    decrypt_team_attachment,
    get_team_secure_attachment_org_quota_bytes,
    stage_team_attachment,
)
from backend.team_communications import (
    SendMessageRequest,
    add_attachments_to_message,
    compact_conversation_payload,
    attachment_request_id,
    attachment_security_http_exception,
    build_attachment_content_disposition,
    dispatch_realtime_event,
    get_active_conversation_member_ids,
    get_conversation,
    insert_attachment_security_event,
    mark_realtime_outbox_published_sync,
    normalize_client_message_id,
    normalize_realtime_event,
    normalize_realtime_payload,
    record_attachment_security_event_best_effort,
    require_active_conversation_member,
    require_business_or_enterprise_organization,
    row_to_attachment,
    row_to_message,
    row_to_secured_attachment_record,
    user_public_payload,
)


router = APIRouter(tags=["team_attachments"])
logger = logging.getLogger(__name__)


def _audit_upload_failure(
    *,
    organization_id: int | None,
    conversation_id: int,
    actor_user_id: str,
    request_id: str,
    outcome: str,
    reason_code: str,
) -> None:
    if organization_id is None:
        return
    record_attachment_security_event_best_effort(
        organization_id=organization_id,
        conversation_id=conversation_id,
        actor_user_id=actor_user_id,
        action="upload",
        outcome=outcome,
        reason_code=reason_code,
        request_id=request_id,
    )


MAX_ATTACHMENTS_PER_MESSAGE = 50
DEFAULT_ATTACHMENT_PREPARE_CONCURRENCY = 4
MAX_ATTACHMENT_PREPARE_CONCURRENCY = 8


def _attachment_prepare_concurrency(file_count: int) -> int:
    raw = os.getenv(
        "TEAM_ATTACHMENT_PREPARE_CONCURRENCY",
        str(DEFAULT_ATTACHMENT_PREPARE_CONCURRENCY),
    ).strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = DEFAULT_ATTACHMENT_PREPARE_CONCURRENCY
    return max(1, min(file_count, configured, MAX_ATTACHMENT_PREPARE_CONCURRENCY))


def _normalize_attachment_uploads(
    *,
    file: UploadFile | None,
    files: list[UploadFile] | None,
) -> list[UploadFile]:
    uploads = [upload for upload in (files or []) if upload is not None]
    if file is not None:
        uploads.insert(0, file)

    if not uploads:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "attachment_required",
                "message": "Choose at least one attachment.",
            },
        )
    if len(uploads) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "too_many_attachments",
                "message": (
                    "A message may contain at most "
                    f"{MAX_ATTACHMENTS_PER_MESSAGE} attachments."
                ),
                "maximum_attachments": MAX_ATTACHMENTS_PER_MESSAGE,
            },
        )
    return uploads


def _stage_attachment_uploads(
    *,
    uploads: list[UploadFile],
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
):
    staged = []
    workers = _attachment_prepare_concurrency(len(uploads))

    def prepare(upload: UploadFile):
        return stage_team_attachment(
            upload=upload,
            organization_id=organization_id,
            conversation_id=conversation_id,
            uploaded_by_user_id=uploaded_by_user_id,
        )

    try:
        if workers == 1:
            for upload in uploads:
                staged.append(prepare(upload))
            return staged

        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(prepare, upload) for upload in uploads]
            try:
                for future in futures:
                    staged.append(future.result())
            except Exception:
                for future in futures:
                    if not future.done():
                        future.cancel()
                        continue
                    try:
                        item = future.result()
                    except Exception:
                        continue
                    if item not in staged:
                        item.cleanup()
                raise
        return staged
    except Exception:
        for item in staged:
            item.cleanup()
        raise


def _attachment_signature(item) -> tuple[str, int, str, str]:
    return (
        str(item.original_filename),
        int(item.file_size_bytes),
        str(item.checksum_sha256),
        "secured",
    )


def _insert_staged_attachment(
    conn,
    *,
    message_id: int,
    conversation_id: int,
    organization_id: int,
    uploaded_by_user_id: str,
    staged,
) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_message_attachments (
                message_id,
                conversation_id,
                organization_id,
                uploaded_by_user_id,
                kind,
                original_filename,
                stored_filename,
                storage_key,
                content_type,
                file_size_bytes,
                checksum_sha256,
                storage_backend,
                security_status,
                malware_scan_status,
                malware_scanner,
                malware_scanner_version,
                scan_completed_at,
                detected_content_type,
                validation_version,
                encryption_algorithm,
                encryption_key_id,
                encryption_nonce,
                encryption_aad_version,
                encrypted_content,
                secured_at,
                security_metadata
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'postgres_encrypted', 'secured', %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, message_id, conversation_id, organization_id,
                      uploaded_by_user_id, kind, original_filename,
                      content_type, file_size_bytes, checksum_sha256,
                      security_status, malware_scan_status,
                      secured_at, created_at
            """,
            (
                message_id,
                conversation_id,
                organization_id,
                uploaded_by_user_id,
                staged.kind,
                staged.original_filename,
                staged.stored_filename,
                staged.storage_key,
                staged.content_type,
                staged.file_size_bytes,
                staged.checksum_sha256,
                staged.malware_scan_status,
                staged.malware_scanner,
                staged.malware_scanner_version,
                staged.scan_completed_at,
                staged.detected_content_type,
                staged.validation_version,
                staged.encryption_algorithm,
                staged.encryption_key_id,
                staged.encryption_nonce,
                staged.encryption_aad_version,
                staged.read_encrypted_content(),
                staged.scan_completed_at,
                Jsonb(staged.security_metadata),
            ),
        )
        return row_to_attachment(cur.fetchone())


@router.post("/conversations/{conversation_id}/attachments")
def send_attachment_message(
    request: Request,
    conversation_id: int = Path(..., ge=1),
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    caption: str = Form(""),
    client_message_id: str | None = Form(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_id = attachment_request_id(request)
    organization_id: int | None = None
    staged_attachments = []
    try:
        client_id = normalize_client_message_id(client_message_id)
        uploads = _normalize_attachment_uploads(file=file, files=files)

        # Authorize before consuming scanner resources.
        with get_db() as conn:
            conversation = get_conversation(conn, conversation_id)
            organization_id = int(conversation["organization_id"])
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            require_active_conversation_member(
                conn,
                conversation_id,
                current_user.user_id,
            )

        staged_attachments = _stage_attachment_uploads(
            uploads=uploads,
            organization_id=organization_id,
            conversation_id=conversation_id,
            uploaded_by_user_id=current_user.user_id,
        )
        fallback_body = (
            staged_attachments[0].original_filename
            if len(staged_attachments) == 1
            else (
                f"{staged_attachments[0].original_filename} and "
                f"{len(staged_attachments) - 1} more attachments"
            )
        )
        message_payload = SendMessageRequest(
            body=(caption or "").strip() or fallback_body,
            client_message_id=client_id,
        )
        message_body = message_payload.body
        created = False
        committed_event: dict | None = None
        member_ids: list[str] = []

        with get_db() as conn:
            # Serialize only this organization's attachment quota and
            # idempotency decisions. Scanning and validation already completed.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s::bigint)",
                    (organization_id,),
                )

            # Membership can be revoked while scanning, so authorize again in
            # the transaction that persists the secured attachments.
            conversation = get_conversation(conn, conversation_id)
            if int(conversation["organization_id"]) != organization_id:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "conversation_access_denied",
                        "message": "This conversation does not belong to this organization.",
                    },
                )
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            require_active_conversation_member(
                conn,
                conversation_id,
                current_user.user_id,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, conversation_id, organization_id,
                           sender_user_id, message_type, body, metadata,
                           edited_at, deleted_at, created_at, updated_at
                    FROM conversation_messages
                    WHERE conversation_id = %s
                      AND sender_user_id = %s
                      AND client_message_id = %s
                    """,
                    (conversation_id, current_user.user_id, client_id),
                )
                existing_message_row = cur.fetchone()

            if existing_message_row is not None:
                message = add_attachments_to_message(
                    conn,
                    row_to_message(existing_message_row),
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT original_filename, file_size_bytes,
                               checksum_sha256, security_status
                        FROM conversation_message_attachments
                        WHERE message_id = %s
                        ORDER BY id ASC
                        """,
                        (message["id"],),
                    )
                    existing_attachment_rows = cur.fetchall()

                exact_retry = (
                    message["message_type"] == "attachment"
                    and message["body"] == message_body
                    and [tuple(row) for row in existing_attachment_rows]
                    == [_attachment_signature(item) for item in staged_attachments]
                )
                if not exact_retry:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "client_message_id_conflict",
                            "message": (
                                "client_message_id was already used for a different "
                                "message. Generate a new identifier and retry."
                            ),
                        },
                    )

                conversation = get_conversation(conn, conversation_id)
                conversation_payload = compact_conversation_payload(conversation)
                for attachment in message["metadata"].get("attachments", []):
                    insert_attachment_security_event(
                        conn,
                        organization_id=organization_id,
                        conversation_id=conversation_id,
                        message_id=int(message["id"]),
                        attachment_id=int(attachment["id"]),
                        actor_user_id=current_user.user_id,
                        action="upload",
                        outcome="succeeded",
                        reason_code="idempotent_retry",
                        request_id=request_id,
                        details={"duplicate": True},
                    )
            else:
                total_file_size = sum(
                    int(item.file_size_bytes) for item in staged_attachments
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(file_size_bytes), 0)
                        FROM conversation_message_attachments
                        WHERE organization_id = %s
                          AND security_status = 'secured'
                        """,
                        (organization_id,),
                    )
                    used_bytes = int(cur.fetchone()[0] or 0)

                quota_bytes = get_team_secure_attachment_org_quota_bytes()
                if used_bytes + total_file_size > quota_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "attachment_storage_quota_exceeded",
                            "message": "The organization secure attachment storage quota has been reached.",
                        },
                    )

                preliminary_metadata = {
                    "client_message_id": client_id,
                    "transport": "http_upload",
                    "attachment_count": len(staged_attachments),
                    "attachments": [],
                }

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO conversation_messages (
                            conversation_id,
                            organization_id,
                            sender_user_id,
                            message_type,
                            body,
                            metadata,
                            client_message_id
                        )
                        VALUES (%s, %s, %s, 'attachment', %s, %s, %s)
                        RETURNING id, conversation_id, organization_id,
                                  sender_user_id, message_type, body, metadata,
                                  edited_at, deleted_at, created_at, updated_at
                        """,
                        (
                            conversation_id,
                            organization_id,
                            current_user.user_id,
                            message_body,
                            Jsonb(preliminary_metadata),
                            client_id,
                        ),
                    )
                    message = row_to_message(cur.fetchone())

                attachments = []
                for staged in staged_attachments:
                    attachment = _insert_staged_attachment(
                        conn,
                        message_id=int(message["id"]),
                        conversation_id=conversation_id,
                        organization_id=organization_id,
                        uploaded_by_user_id=current_user.user_id,
                        staged=staged,
                    )
                    attachments.append(attachment)
                    insert_attachment_security_event(
                        conn,
                        organization_id=organization_id,
                        conversation_id=conversation_id,
                        message_id=int(message["id"]),
                        attachment_id=int(attachment["id"]),
                        actor_user_id=current_user.user_id,
                        action="upload",
                        outcome="succeeded",
                        reason_code=(
                            "secured_clean"
                            if staged.malware_scan_status == "clean"
                            else "secured_best_effort_scan_unavailable"
                        ),
                        request_id=request_id,
                        details={
                            "file_size_bytes": staged.file_size_bytes,
                            "kind": staged.kind,
                            "validation_version": staged.validation_version,
                            "malware_scan_status": staged.malware_scan_status,
                            "attachment_count": len(staged_attachments),
                        },
                    )

                final_metadata = {
                    "client_message_id": client_id,
                    "transport": "http_upload",
                    "attachment_count": len(attachments),
                    "attachments": attachments,
                }
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE conversation_messages
                        SET metadata = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, conversation_id, organization_id,
                                  sender_user_id, message_type, body, metadata,
                                  edited_at, deleted_at, created_at, updated_at
                        """,
                        (
                            Jsonb(normalize_realtime_payload(final_metadata)),
                            message["id"],
                        ),
                    )
                    message = row_to_message(cur.fetchone())
                    cur.execute(
                        """
                        UPDATE organization_conversations
                        SET last_message_at = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (message["created_at"], conversation_id),
                    )

                conversation = get_conversation(conn, conversation_id)
                conversation_payload = compact_conversation_payload(conversation)
                member_ids = get_active_conversation_member_ids(
                    conn,
                    conversation_id,
                )
                message = {
                    **message,
                    "client_message_id": client_id,
                    "pending": False,
                }
                committed_event = {
                    "event_id": f"message.created:{message['id']}",
                    "type": "message.created",
                    "organization_id": organization_id,
                    "client_message_id": client_id,
                    "message": message,
                    "conversation": conversation_payload,
                    "sender": user_public_payload(current_user),
                    "delivery": "committed",
                }

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO team_realtime_outbox (
                            organization_id,
                            aggregate_type,
                            aggregate_id,
                            event_type,
                            event_key,
                            recipient_user_ids,
                            payload
                        )
                        VALUES (%s, 'message', %s, 'message.created', %s, %s, %s)
                        ON CONFLICT (event_key) DO NOTHING
                        """,
                        (
                            organization_id,
                            str(message["id"]),
                            committed_event["event_id"],
                            member_ids,
                            Jsonb(normalize_realtime_event(committed_event)),
                        ),
                    )
                created = True

        if created and committed_event is not None:
            dispatched = dispatch_realtime_event(
                organization_id=organization_id,
                user_ids=member_ids,
                event=committed_event,
            )
            if dispatched:
                try:
                    mark_realtime_outbox_published_sync(committed_event["event_id"])
                except Exception:
                    pass

        return {
            "success": True,
            "message": message,
            "conversation": conversation_payload,
            "client_message_id": client_id,
            "delivery": "committed",
            "duplicate": not created,
        }

    except TeamAttachmentSecurityError as exc:
        _audit_upload_failure(
            organization_id=organization_id,
            conversation_id=conversation_id,
            actor_user_id=current_user.user_id,
            request_id=request_id,
            outcome="rejected" if exc.status_code == 422 else "failed",
            reason_code=exc.code,
        )
        raise attachment_security_http_exception(exc) from exc
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if exc.status_code in {403, 409, 413}:
            _audit_upload_failure(
                organization_id=organization_id,
                conversation_id=conversation_id,
                actor_user_id=current_user.user_id,
                request_id=request_id,
                outcome="denied" if exc.status_code == 403 else "rejected",
                reason_code=str(detail.get("error") or "upload_denied"),
            )
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_attachment", "message": str(exc)},
        ) from exc
    except (
        psycopg_errors.UndefinedTable,
        psycopg_errors.UndefinedColumn,
        psycopg_errors.CheckViolation,
    ) as exc:
        logger.exception(
            "Secure attachment persistence failed. error_type=%s constraint=%s",
            type(exc).__name__,
            getattr(getattr(exc, "diag", None), "constraint_name", None),
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_security_schema_not_ready",
                "message": "Secure attachment persistence migrations are not current.",
            },
        ) from exc
    except psycopg_errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "client_message_id_conflict",
                "message": "The attachment request conflicts with an existing message. Retry with a new identifier.",
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "Could not securely persist team attachments. request_id=%s",
            request_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "attachment_send_failed",
                "message": "Could not send attachments securely.",
            },
        ) from exc
    finally:
        for staged in staged_attachments:
            staged.cleanup()


@router.get("/conversations/{conversation_id}/messages/{message_id}/attachments/{attachment_id}/download")
def download_conversation_attachment(
    request: Request,
    conversation_id: int = Path(..., ge=1),
    message_id: int = Path(..., ge=1),
    attachment_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_id = attachment_request_id(request)
    organization_id: int | None = None
    try:
        with get_db() as conn:
            conversation = get_conversation(conn, conversation_id)
            organization_id = int(conversation["organization_id"])
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            require_active_conversation_member(
                conn,
                conversation_id,
                current_user.user_id,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, message_id, conversation_id, organization_id,
                           uploaded_by_user_id, kind, original_filename,
                           stored_filename, storage_key, content_type,
                           file_size_bytes, checksum_sha256, storage_backend,
                           security_status, malware_scan_status,
                           detected_content_type, validation_version,
                           encryption_algorithm, encryption_key_id,
                           encryption_nonce, encryption_aad_version,
                           encrypted_content, secured_at,
                           last_integrity_verified_at, created_at
                    FROM conversation_message_attachments
                    WHERE id = %s
                      AND message_id = %s
                      AND conversation_id = %s
                      AND organization_id = %s
                    """,
                    (attachment_id, message_id, conversation_id, organization_id),
                )
                row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "attachment_not_found",
                        "message": "Attachment was not found.",
                    },
                )

            attachment = row_to_secured_attachment_record(row)
            scan_status = str(attachment.get("malware_scan_status") or "")
            best_effort = (
                os.getenv("TEAM_ATTACHMENT_SCAN_POLICY", "strict").strip().lower()
                in {"best_effort", "best-effort", "besteffort"}
            )
            if scan_status != "clean" and not (
                scan_status == "failed" and best_effort
            ):
                raise TeamAttachmentSecurityError(
                    409,
                    "attachment_scan_not_approved",
                    "This attachment is not available under the current malware-scan policy.",
                )
            plaintext = decrypt_team_attachment(attachment)

            # Recheck after integrity verification so an access revocation that
            # completed during decryption prevents this response.
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            require_active_conversation_member(
                conn,
                conversation_id,
                current_user.user_id,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE conversation_message_attachments
                    SET last_integrity_verified_at = NOW()
                    WHERE id = %s
                    """,
                    (attachment_id,),
                )
            insert_attachment_security_event(
                conn,
                organization_id=organization_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                actor_user_id=current_user.user_id,
                action="download",
                outcome="succeeded",
                reason_code="authorized_and_verified",
                request_id=request_id,
                details={"file_size_bytes": len(plaintext)},
            )
            insert_team_audit_event(
                conn,
                organization_id=organization_id,
                event_type="attachment.downloaded",
                actor_user_id=current_user.user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                request_id=request_id,
                metadata={
                    "file_size_bytes": len(plaintext),
                    "malware_scan_status": scan_status,
                    "integrity_verified": True,
                },
            )

        return Response(
            content=plaintext,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": build_attachment_content_disposition(
                    attachment["original_filename"]
                ),
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "X-Download-Options": "noopen",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )

    except TeamAttachmentSecurityError as exc:
        if organization_id is not None:
            record_attachment_security_event_best_effort(
                organization_id=organization_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                actor_user_id=current_user.user_id,
                action="download",
                outcome="denied" if exc.status_code == 409 else "failed",
                reason_code=exc.code,
                request_id=request_id,
            )
            record_team_audit_event_best_effort(
                organization_id=organization_id,
                event_type="attachment.download.denied",
                actor_user_id=current_user.user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                request_id=request_id,
                metadata={"reason_code": exc.code, "status_code": exc.status_code},
            )
        if exc.code == "attachment_integrity_failure":
            logger.critical(
                "Integrity verification failed for team attachment %s.",
                attachment_id,
            )
        raise attachment_security_http_exception(exc) from exc
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if organization_id is not None and exc.status_code in {403, 404}:
            record_attachment_security_event_best_effort(
                organization_id=organization_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                actor_user_id=current_user.user_id,
                action="download",
                outcome="denied",
                reason_code=str(detail.get("error") or "download_denied"),
                request_id=request_id,
            )
            record_team_audit_event_best_effort(
                organization_id=organization_id,
                event_type="attachment.download.denied",
                actor_user_id=current_user.user_id,
                conversation_id=conversation_id,
                message_id=message_id,
                attachment_id=attachment_id,
                request_id=request_id,
                metadata={
                    "reason_code": str(detail.get("error") or "download_denied"),
                    "status_code": exc.status_code,
                },
            )
        raise
    except (
        psycopg_errors.UndefinedTable,
        psycopg_errors.UndefinedColumn,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_security_schema_not_ready",
                "message": (
                    "Secure attachment persistence is not ready. Apply migration "
                    "013_secure_team_message_attachments.sql and try again."
                ),
            },
        ) from exc
    except Exception as exc:
        logger.exception("Could not securely download team attachment %s.", attachment_id)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "attachment_download_failed",
                "message": "Could not download attachment securely.",
            },
        ) from exc


__all__ = ["router"]
