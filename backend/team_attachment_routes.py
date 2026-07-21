from __future__ import annotations

"""Authenticated routes for secure team-message attachments only."""

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
    prepare_team_attachment,
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


@router.post("/conversations/{conversation_id}/attachments")
def send_attachment_message(
    request: Request,
    conversation_id: int = Path(..., ge=1),
    file: UploadFile = File(...),
    caption: str = Form(""),
    client_message_id: str | None = Form(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_id = attachment_request_id(request)
    organization_id: int | None = None
    try:
        client_id = normalize_client_message_id(client_message_id)

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

        prepared = prepare_team_attachment(
            upload=file,
            organization_id=organization_id,
            conversation_id=conversation_id,
            uploaded_by_user_id=current_user.user_id,
        )
        message_payload = SendMessageRequest(
            body=(caption or "").strip() or prepared.original_filename,
            client_message_id=client_id,
        )
        message_body = message_payload.body
        created = False
        committed_event: dict | None = None
        member_ids: list[str] = []

        with get_db() as conn:
            # Serialize only team attachment quota/idempotency decisions for this
            # organization. Other feature uploads use neither this lock nor route.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s::bigint)",
                    (organization_id,),
                )

            # Membership can be revoked while scanning, so authorize again in
            # the same transaction that persists the secured attachment.
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
                    and len(existing_attachment_rows) == 1
                    and existing_attachment_rows[0][0] == prepared.original_filename
                    and int(existing_attachment_rows[0][1]) == prepared.file_size_bytes
                    and existing_attachment_rows[0][2] == prepared.checksum_sha256
                    and existing_attachment_rows[0][3] == "secured"
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
                attachment = message["metadata"]["attachments"][0]
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
                if used_bytes + prepared.file_size_bytes > quota_bytes:
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
                    "attachment_count": 1,
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
                            message["id"],
                            conversation_id,
                            organization_id,
                            current_user.user_id,
                            prepared.kind,
                            prepared.original_filename,
                            prepared.stored_filename,
                            prepared.storage_key,
                            prepared.content_type,
                            prepared.file_size_bytes,
                            prepared.checksum_sha256,
                            prepared.malware_scan_status,
                            prepared.malware_scanner,
                            prepared.malware_scanner_version,
                            prepared.scan_completed_at,
                            prepared.detected_content_type,
                            prepared.validation_version,
                            prepared.encryption_algorithm,
                            prepared.encryption_key_id,
                            prepared.encryption_nonce,
                            prepared.encryption_aad_version,
                            prepared.encrypted_content,
                            prepared.scan_completed_at,
                            Jsonb(prepared.security_metadata),
                        ),
                    )
                    attachment = row_to_attachment(cur.fetchone())

                    final_metadata = {
                        "client_message_id": client_id,
                        "transport": "http_upload",
                        "attachment_count": 1,
                        "attachments": [attachment],
                    }
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
                        (Jsonb(normalize_realtime_payload(final_metadata)), message["id"]),
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
                    conn, conversation_id
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
                        if prepared.malware_scan_status == "clean"
                        else "secured_best_effort_scan_unavailable"
                    ),
                    request_id=request_id,
                    details={
                        "file_size_bytes": prepared.file_size_bytes,
                        "kind": prepared.kind,
                        "validation_version": prepared.validation_version,
                        "malware_scan_status": prepared.malware_scan_status,
                    },
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
                    # The durable dispatcher retains and retries this event.
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
        logger.exception("Could not securely persist a team attachment. request_id=%s", request_id)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "attachment_send_failed",
                "message": "Could not send attachment securely.",
            },
        ) from exc


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
