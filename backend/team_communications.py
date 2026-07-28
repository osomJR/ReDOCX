from __future__ import annotations

"""Enterprise governance, read state, search and notification registration."""

import base64
import os
from pathlib import Path as FileSystemPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, field_validator
from psycopg.types.json import Jsonb

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db
from backend.team_audit import insert_team_audit_event
from backend.team_attachment_security import load_team_attachment_keyring
from backend import team_communications as communications

router = APIRouter(tags=["team_governance"])


class CommunicationPolicyUpdate(BaseModel):
    message_retention_days: int | None = None
    attachment_retention_days: int | None = None
    legal_hold: bool | None = None
    active_encryption_key_id: str | None = None
    push_notifications_enabled: bool | None = None
    push_notification_previews_enabled: bool | None = None

    @field_validator("message_retention_days")
    @classmethod
    def validate_message_retention(cls, value: int | None) -> int | None:
        if value is not None:
            raise ValueError(
                "Team messages are retained until the organization is deleted and "
                "do not accept a time-based retention value."
            )
        return None

    @field_validator("attachment_retention_days")
    @classmethod
    def validate_attachment_retention(cls, value: int | None) -> int | None:
        if value is not None and value != 365:
            raise ValueError("Team attachment retention is fixed at 365 days.")
        return value

    @field_validator("active_encryption_key_id")
    @classmethod
    def normalize_key_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if len(normalized) > 64 or not all(
            character.isalnum() or character in {".", "_", "-"}
            for character in normalized
        ):
            raise ValueError("active_encryption_key_id is invalid.")
        return normalized


class ReadStateUpdate(BaseModel):
    last_read_message_id: int

    @field_validator("last_read_message_id")
    @classmethod
    def validate_message_id(cls, value: int) -> int:
        if value < 1:
            raise ValueError("last_read_message_id must be positive.")
        return value


class PushSubscriptionRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None = None
    locale: str = "en"

    @field_validator("endpoint", "p256dh", "auth")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Push subscription fields are required.")
        return normalized

    @field_validator("locale")
    @classmethod
    def validate_locale(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"en", "fr"}:
            raise ValueError("locale must be en or fr.")
        return normalized


def _decode_vapid_key(value: str) -> bytes:
    normalized = value.strip().encode("ascii")
    padding = b"=" * ((4 - len(normalized) % 4) % 4)
    return base64.urlsafe_b64decode(normalized + padding)


def _encode_vapid_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _normalize_vapid_public_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("VAPID public key is empty.")

    if "BEGIN PUBLIC KEY" in normalized:
        public_key = serialization.load_pem_public_key(
            normalized.replace("\\n", "\n").encode("utf-8")
        )
        if not isinstance(public_key, ec.EllipticCurvePublicKey):
            raise ValueError("VAPID public key must be an elliptic-curve key.")
        encoded = public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    else:
        encoded = _decode_vapid_key(normalized)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), encoded)

    if len(encoded) != 65 or encoded[0] != 4:
        raise ValueError("VAPID public key must be an uncompressed P-256 point.")
    return _encode_vapid_key(encoded)


def _derive_vapid_public_key(private_value: str) -> str:
    normalized = private_value.strip().replace("\\n", "\n")
    if not normalized:
        raise ValueError("VAPID private key is empty.")

    if "\n" not in normalized and len(normalized) <= 1024:
        key_path = FileSystemPath(normalized).expanduser()
        if key_path.is_file():
            if key_path.stat().st_size > 16 * 1024:
                raise ValueError("VAPID private key file is unexpectedly large.")
            normalized = key_path.read_text(encoding="utf-8").strip()

    if "BEGIN" in normalized:
        private_key = serialization.load_pem_private_key(
            normalized.encode("utf-8"),
            password=None,
        )
    else:
        raw = _decode_vapid_key(normalized)
        if len(raw) == 32:
            private_key = ec.derive_private_key(
                int.from_bytes(raw, "big"),
                ec.SECP256R1(),
            )
        else:
            private_key = serialization.load_der_private_key(raw, password=None)

    if not isinstance(private_key, ec.EllipticCurvePrivateKey):
        raise ValueError("VAPID private key must be an elliptic-curve key.")
    if not isinstance(private_key.curve, ec.SECP256R1):
        raise ValueError("VAPID private key must use the P-256 curve.")

    encoded = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _encode_vapid_key(encoded)


def _configured_vapid_public_key() -> str:
    configured_public_key = (
        os.getenv("WEB_PUSH_VAPID_PUBLIC_KEY", "").strip()
        or os.getenv("NEXT_PUBLIC_WEB_PUSH_VAPID_PUBLIC_KEY", "").strip()
    )
    if configured_public_key:
        return _normalize_vapid_public_key(configured_public_key)

    private_key = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "").strip()
    if private_key:
        return _derive_vapid_public_key(private_key)

    raise ValueError("No VAPID key material is configured.")


def _require_org_admin(conn, organization_id: int, current_user: AuthenticatedUser) -> dict[str, Any]:
    access = communications.require_business_or_enterprise_organization(
        conn, organization_id, current_user
    )
    role = str(access["membership"].get("role") or "")
    if role not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_admin_required",
                "message": "Organization administrator access is required.",
            },
        )
    return access


@router.get("/organizations/{organization_id}/communication-policy")
def get_communication_policy(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        _require_org_admin(conn, organization_id, current_user)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO organization_communication_policies (organization_id)
                VALUES (%s)
                ON CONFLICT (organization_id) DO NOTHING
                """,
                (organization_id,),
            )
            cur.execute(
                """
                SELECT organization_id, message_retention_days,
                       attachment_retention_days, legal_hold,
                       active_encryption_key_id, push_notifications_enabled,
                       push_notification_previews_enabled, created_at, updated_at
                FROM organization_communication_policies
                WHERE organization_id = %s
                """,
                (organization_id,),
            )
            row = cur.fetchone()
    return {
        "success": True,
        "policy": {
            "organization_id": row[0],
            "message_retention_days": None,
            "messages_retained_until_organization_deletion": True,
            "attachment_retention_days": 365,
            "legal_hold": row[3],
            "active_encryption_key_id": row[4],
            "push_notifications_enabled": row[5],
            "push_notification_previews_enabled": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        },
    }


@router.patch("/organizations/{organization_id}/communication-policy")
def update_communication_policy(
    payload: CommunicationPolicyUpdate,
    request: Request,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    updates = payload.model_dump(exclude_unset=True)
    # Retention semantics are fixed: messages remain until organization deletion
    # and attachments expire after 365 days. Explicit nulls are ignored rather
    # than written into NOT NULL database columns.
    updates.pop("message_retention_days", None)
    if updates.get("attachment_retention_days") is None:
        updates.pop("attachment_retention_days", None)
    if not updates:
        raise HTTPException(
            status_code=422,
            detail={"error": "empty_policy_update", "message": "No policy changes were provided."},
        )

    key_field_changed = "active_encryption_key_id" in updates
    requested_key_id = updates.get("active_encryption_key_id")
    if key_field_changed and requested_key_id is not None:
        _, available_keys = load_team_attachment_keyring()
        if requested_key_id not in available_keys:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unknown_attachment_encryption_key",
                    "message": (
                        "The selected attachment encryption key is not available "
                        "in TEAM_ATTACHMENT_ENCRYPTION_KEYS_JSON."
                    ),
                },
            )

    with get_db() as conn:
        _require_org_admin(conn, organization_id, current_user)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO organization_communication_policies (organization_id)
                VALUES (%s)
                ON CONFLICT (organization_id) DO NOTHING
                """,
                (organization_id,),
            )
            assignments = []
            params: list[Any] = []
            for column, value in updates.items():
                assignments.append(f"{column} = %s")
                params.append(value)
            params.append(organization_id)
            cur.execute(
                f"""
                UPDATE organization_communication_policies
                SET {', '.join(assignments)}, updated_at = NOW()
                WHERE organization_id = %s
                RETURNING organization_id, message_retention_days,
                          attachment_retention_days, legal_hold,
                          active_encryption_key_id, push_notifications_enabled,
                          push_notification_previews_enabled, created_at, updated_at
                """,
                tuple(params),
            )
            row = cur.fetchone()
            if key_field_changed:
                cur.execute(
                    """
                    UPDATE organization_encryption_key_versions
                    SET status = 'decrypt_only', retired_at = NULL
                    WHERE organization_id = %s
                      AND status = 'active'
                      AND (%s::TEXT IS NULL OR key_id <> %s::TEXT)
                    """,
                    (organization_id, requested_key_id, requested_key_id),
                )
                if requested_key_id is not None:
                    cur.execute(
                        """
                        INSERT INTO organization_encryption_key_versions (
                            organization_id, key_id, provider, status, activated_at
                        )
                        VALUES (%s, %s, 'environment_keyring', 'active', NOW())
                        ON CONFLICT (organization_id, key_id) DO UPDATE SET
                            provider = EXCLUDED.provider,
                            status = 'active',
                            activated_at = NOW(),
                            retired_at = NULL
                        """,
                        (organization_id, requested_key_id),
                    )
        insert_team_audit_event(
            conn,
            organization_id=organization_id,
            event_type="organization.communication_policy.updated",
            actor_user_id=current_user.user_id,
            request_id=str(request.headers.get("x-request-id") or "") or None,
            metadata={"changed_fields": sorted(updates.keys())},
        )
    return {
        "success": True,
        "policy": {
            "organization_id": row[0],
            "message_retention_days": None,
            "messages_retained_until_organization_deletion": True,
            "attachment_retention_days": 365,
            "legal_hold": row[3],
            "active_encryption_key_id": row[4],
            "push_notifications_enabled": row[5],
            "push_notification_previews_enabled": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        },
    }


@router.get("/organizations/{organization_id}/encryption-key-versions")
def list_encryption_key_versions(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        _require_org_admin(conn, organization_id, current_user)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT key_id, provider, external_key_reference, status,
                       activated_at, retired_at, created_at
                FROM organization_encryption_key_versions
                WHERE organization_id = %s
                ORDER BY activated_at DESC, key_id
                """,
                (organization_id,),
            )
            rows = cur.fetchall()
    return {
        "success": True,
        "keys": [
            {
                "key_id": row[0],
                "provider": row[1],
                "external_key_reference": row[2],
                "status": row[3],
                "activated_at": row[4],
                "retired_at": row[5],
                "created_at": row[6],
            }
            for row in rows
        ],
    }


@router.get("/organizations/{organization_id}/audit-events")
def list_audit_events(
    organization_id: int = Path(..., ge=1),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        _require_org_admin(conn, organization_id, current_user)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, organization_id, event_type, actor_user_id,
                       target_user_id, conversation_id, message_id,
                       attachment_id, call_session_id, request_id, metadata,
                       occurred_at, ENCODE(previous_hash, 'hex'),
                       ENCODE(event_hash, 'hex')
                FROM team_audit_events
                WHERE organization_id = %s
                  AND (%s::BIGINT IS NULL OR id < %s::BIGINT)
                ORDER BY id DESC
                LIMIT %s
                """,
                (organization_id, before_id, before_id, limit),
            )
            rows = cur.fetchall()
    return {
        "success": True,
        "events": [
            {
                "id": row[0],
                "organization_id": row[1],
                "event_type": row[2],
                "actor_user_id": row[3],
                "target_user_id": row[4],
                "conversation_id": row[5],
                "message_id": row[6],
                "attachment_id": row[7],
                "call_session_id": row[8],
                "request_id": row[9],
                "metadata": row[10],
                "occurred_at": row[11],
                "previous_hash": row[12],
                "event_hash": row[13],
            }
            for row in rows
        ],
    }


@router.get("/organizations/{organization_id}/audit-events/verify")
def verify_audit_chain(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        _require_org_admin(conn, organization_id, current_user)
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH ordered AS (
                    SELECT event.*,
                           LAG(event_hash) OVER (ORDER BY id) AS expected_previous_hash
                    FROM team_audit_events event
                    WHERE organization_id = %s
                ), checked AS (
                    SELECT id,
                           previous_hash IS NOT DISTINCT FROM expected_previous_hash
                               AS link_valid,
                           event_hash = DIGEST(
                               CONVERT_TO(
                                   CONCAT_WS(
                                       '|',
                                       organization_id::TEXT,
                                       event_type,
                                       COALESCE(actor_user_id, ''),
                                       COALESCE(target_user_id, ''),
                                       COALESCE(conversation_id::TEXT, ''),
                                       COALESCE(message_id::TEXT, ''),
                                       COALESCE(attachment_id::TEXT, ''),
                                       COALESCE(call_session_id::TEXT, ''),
                                       COALESCE(request_id, ''),
                                       occurred_at::TEXT,
                                       metadata::TEXT,
                                       COALESCE(ENCODE(previous_hash, 'hex'), '')
                                   ),
                                   'UTF8'
                               ),
                               'sha256'
                           ) AS hash_valid
                    FROM ordered
                )
                SELECT COUNT(*)::BIGINT,
                       COUNT(*) FILTER (WHERE NOT link_valid OR NOT hash_valid)::BIGINT,
                       MIN(id) FILTER (WHERE NOT link_valid OR NOT hash_valid)
                FROM checked
                """,
                (organization_id,),
            )
            row = cur.fetchone()
    invalid_count = int(row[1] or 0)
    return {
        "success": invalid_count == 0,
        "organization_id": organization_id,
        "event_count": int(row[0] or 0),
        "invalid_count": invalid_count,
        "first_invalid_event_id": row[2],
    }


@router.put("/conversations/{conversation_id}/read-state")
def update_read_state(
    payload: ReadStateUpdate,
    request: Request,
    conversation_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        conversation = communications.get_conversation(conn, conversation_id)
        communications.require_business_or_enterprise_organization(
            conn, conversation["organization_id"], current_user
        )
        communications.require_active_conversation_member(
            conn, conversation_id, current_user.user_id
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM conversation_messages
                WHERE id = %s AND conversation_id = %s AND deleted_at IS NULL
                """,
                (payload.last_read_message_id, conversation_id),
            )
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail={"error": "message_not_found", "message": "Read cursor message was not found."},
                )
            cur.execute(
                """
                INSERT INTO conversation_read_state (
                    organization_id, conversation_id, user_id,
                    last_read_message_id, last_read_at, updated_at
                )
                VALUES (%s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                    last_read_message_id = GREATEST(
                        COALESCE(conversation_read_state.last_read_message_id, 0),
                        EXCLUDED.last_read_message_id
                    ),
                    last_read_at = NOW(),
                    updated_at = NOW()
                RETURNING last_read_message_id, last_read_at
                """,
                (
                    conversation["organization_id"],
                    conversation_id,
                    current_user.user_id,
                    payload.last_read_message_id,
                ),
            )
            row = cur.fetchone()
        insert_team_audit_event(
            conn,
            organization_id=int(conversation["organization_id"]),
            event_type="conversation.read.updated",
            actor_user_id=current_user.user_id,
            conversation_id=conversation_id,
            message_id=int(row[0]),
            request_id=str(request.headers.get("x-request-id") or "") or None,
            metadata={"last_read_message_id": int(row[0])},
        )
    return {
        "success": True,
        "conversation_id": conversation_id,
        "last_read_message_id": row[0],
        "last_read_at": row[1],
    }


@router.get("/organizations/{organization_id}/unread-counts")
def get_unread_counts(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT oc.id,
                       COUNT(cm.id) FILTER (
                           WHERE cm.id > COALESCE(crs.last_read_message_id, 0)
                             AND cm.sender_user_id <> %s
                             AND cm.deleted_at IS NULL
                       )::INTEGER AS unread_count,
                       MAX(cm.id) FILTER (WHERE cm.deleted_at IS NULL) AS latest_message_id
                FROM organization_conversations oc
                JOIN conversation_members member
                  ON member.conversation_id = oc.id
                 AND member.user_id = %s
                 AND member.status = 'active'
                LEFT JOIN conversation_read_state crs
                  ON crs.conversation_id = oc.id
                 AND crs.user_id = %s
                LEFT JOIN conversation_messages cm
                  ON cm.conversation_id = oc.id
                WHERE oc.organization_id = %s
                  AND oc.status = 'active'
                GROUP BY oc.id, crs.last_read_message_id
                ORDER BY oc.id
                """,
                (
                    current_user.user_id,
                    current_user.user_id,
                    current_user.user_id,
                    organization_id,
                ),
            )
            rows = cur.fetchall()
    return {
        "success": True,
        "counts": [
            {
                "conversation_id": row[0],
                "unread_count": row[1],
                "latest_message_id": row[2],
            }
            for row in rows
        ],
        "total_unread": sum(int(row[1] or 0) for row in rows),
    }


@router.get("/organizations/{organization_id}/messages/search")
def search_messages(
    organization_id: int = Path(..., ge=1),
    q: str = Query(..., min_length=2, max_length=200),
    conversation_id: int | None = Query(None, ge=1),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT cm.id, cm.conversation_id, cm.organization_id,
                       cm.sender_user_id, cm.message_type, cm.body, cm.metadata,
                       cm.edited_at, cm.deleted_at, cm.created_at, cm.updated_at,
                       TS_RANK_CD(cm.search_document, query.search_query) AS rank
                FROM conversation_messages cm
                JOIN conversation_members member
                  ON member.conversation_id = cm.conversation_id
                 AND member.user_id = %s
                 AND member.status = 'active'
                CROSS JOIN LATERAL WEBSEARCH_TO_TSQUERY('simple', %s) AS query(search_query)
                WHERE cm.organization_id = %s
                  AND cm.deleted_at IS NULL
                  AND cm.search_document @@ query.search_query
                  AND (%s::BIGINT IS NULL OR cm.conversation_id = %s::BIGINT)
                  AND (%s::BIGINT IS NULL OR cm.id < %s::BIGINT)
                ORDER BY rank DESC, cm.id DESC
                LIMIT %s
                """,
                (
                    current_user.user_id,
                    q,
                    organization_id,
                    conversation_id,
                    conversation_id,
                    before_id,
                    before_id,
                    limit,
                ),
            )
            rows = cur.fetchall()
        messages = [communications.row_to_message(row[:11]) for row in rows]
        messages = communications.add_attachments_to_messages(conn, messages)
        messages = communications.add_call_states_to_messages(conn, messages)
    return {
        "success": True,
        "query": q,
        "messages": [
            {**message, "search_rank": float(row[11])}
            for message, row in zip(messages, rows)
        ],
    }


@router.get("/account/push-subscriptions/public-key")
def get_push_public_key(
    _current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        public_key = _configured_vapid_public_key()
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "web_push_not_configured",
                "message": (
                    "Web-push key material is missing or invalid on the server."
                ),
            },
        ) from exc

    return JSONResponse(
        {"success": True, "public_key": public_key},
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/account/push-subscriptions")
def register_push_subscription(
    payload: PushSubscriptionRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_push_subscriptions (
                    user_id, endpoint, p256dh, auth_secret,
                    user_agent, locale, status, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'active', NOW())
                ON CONFLICT (endpoint) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    p256dh = EXCLUDED.p256dh,
                    auth_secret = EXCLUDED.auth_secret,
                    user_agent = EXCLUDED.user_agent,
                    locale = EXCLUDED.locale,
                    status = 'active',
                    updated_at = NOW()
                RETURNING id, created_at, updated_at
                """,
                (
                    current_user.user_id,
                    payload.endpoint,
                    payload.p256dh,
                    payload.auth,
                    payload.user_agent,
                    payload.locale,
                ),
            )
            row = cur.fetchone()
    return {"success": True, "subscription_id": row[0], "created_at": row[1], "updated_at": row[2]}


@router.delete("/account/push-subscriptions")
def revoke_push_subscription(
    endpoint: str = Query(..., min_length=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_push_subscriptions
                SET status = 'revoked', updated_at = NOW()
                WHERE user_id = %s AND endpoint = %s
                """,
                (current_user.user_id, endpoint),
            )
    return {"success": True}


__all__ = ["router"]