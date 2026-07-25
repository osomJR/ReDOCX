from __future__ import annotations

"""Attachment-only retention for ReDOCX team communications."""

from typing import Any

from backend.database import get_db


TEAM_ATTACHMENT_RETENTION_DAYS = 365


def purge_expired_team_attachments(*, limit: int = 500) -> dict[str, Any]:
    """Delete attachment rows older than 365 days while retaining messages.

    Organizations under legal hold are excluded. Deleting the attachment row
    removes its encrypted payload through the same database record; the parent
    conversation message remains until the organization itself is deleted.
    """
    bounded_limit = max(1, min(int(limit), 5000))
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('conversation_message_attachments'), "
                "to_regclass('organization_communication_policies')"
            )
            tables = cur.fetchone()
            if not tables or not all(tables):
                return {"deleted": 0, "configured": False}

            cur.execute(
                """
                WITH candidates AS (
                    SELECT attachment.id
                    FROM conversation_message_attachments AS attachment
                    JOIN organization_communication_policies AS policy
                      ON policy.organization_id = attachment.organization_id
                    WHERE policy.legal_hold = FALSE
                      AND attachment.created_at < NOW() - INTERVAL '365 days'
                    ORDER BY attachment.created_at, attachment.id
                    LIMIT %s
                    FOR UPDATE OF attachment SKIP LOCKED
                )
                DELETE FROM conversation_message_attachments AS attachment
                USING candidates
                WHERE attachment.id = candidates.id
                RETURNING attachment.id
                """,
                (bounded_limit,),
            )
            deleted = len(cur.fetchall())
    return {"deleted": deleted, "configured": True}


__all__ = ["TEAM_ATTACHMENT_RETENTION_DAYS", "purge_expired_team_attachments"]
