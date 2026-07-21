from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def insert_team_audit_event(
    conn,
    *,
    organization_id: int,
    event_type: str,
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    conversation_id: int | None = None,
    message_id: int | None = None,
    attachment_id: int | None = None,
    call_session_id: int | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_audit_events (
                organization_id, event_type, actor_user_id, target_user_id,
                conversation_id, message_id, attachment_id, call_session_id,
                request_id, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                organization_id,
                event_type,
                actor_user_id,
                target_user_id,
                conversation_id,
                message_id,
                attachment_id,
                call_session_id,
                request_id,
                Jsonb(_json_safe(metadata or {})),
            ),
        )


def record_team_audit_event_best_effort(**kwargs: Any) -> bool:
    """Write an immutable audit event without masking the primary operation."""

    import logging

    from backend.database import get_db

    try:
        with get_db() as conn:
            insert_team_audit_event(conn, **kwargs)
        return True
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not persist team audit event type=%s organization_id=%s.",
            kwargs.get("event_type"),
            kwargs.get("organization_id"),
        )
        return False


__all__ = ["insert_team_audit_event", "record_team_audit_event_best_effort"]
