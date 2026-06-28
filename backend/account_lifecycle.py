from __future__ import annotations

"""
Account lifecycle helpers for soft deactivation, restore, and delayed purge.

This module intentionally contains no FastAPI router. It is shared by account,
organization, and authentication layers so account deletion rules remain
backend-enforced instead of being only a frontend warning.
"""

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Iterable

from fastapi import HTTPException
from psycopg.types.json import Jsonb


ACTIVE_STATUS = "active"
DEACTIVATED_PENDING_DELETION_STATUS = "deactivated_pending_deletion"
PURGE_DUE_STATUS = "purge_due"
PURGED_STATUS = "purged"
RESTORABLE_STATUSES = {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}

DEFAULT_RESTORE_DAYS = int(os.getenv("ACCOUNT_DELETION_FALLBACK_RESTORE_DAYS", "30"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def relation_exists(conn, relation_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (relation_name,))
        row = cur.fetchone()
    return bool(row and row[0])


def account_lifecycle_table_exists(conn) -> bool:
    return relation_exists(conn, "account_lifecycle")


def normalize_user_id(user_id: str) -> str:
    normalized = (user_id or "").strip()
    if not normalized:
        raise ValueError("user_id is required.")
    return normalized


def resolve_restore_deadline(period_end: datetime | None = None) -> tuple[datetime, bool]:
    """
    Prefer the paid subscription's current_period_end. Fall back to a bounded
    restore window so legacy/provider records without period fields do not get
    stuck in a non-purgeable state.
    """

    now = utc_now()
    if isinstance(period_end, datetime):
        normalized = period_end if period_end.tzinfo else period_end.replace(tzinfo=timezone.utc)
        if normalized > now:
            return normalized, False

    return now + timedelta(days=max(DEFAULT_RESTORE_DAYS, 1)), True


def get_account_lifecycle(conn, user_id: str) -> dict[str, Any] | None:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id, status, deletion_reason, deactivated_at,
                   restore_deadline, purge_after, restored_at, purged_at,
                   metadata, created_at, updated_at
            FROM account_lifecycle
            WHERE user_id = %s
            """,
            (normalized_user_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "user_id": row[0],
        "status": row[1],
        "deletion_reason": row[2],
        "deactivated_at": row[3],
        "restore_deadline": row[4],
        "purge_after": row[5],
        "restored_at": row[6],
        "purged_at": row[7],
        "metadata": row[8] or {},
        "created_at": row[9],
        "updated_at": row[10],
    }


def ensure_account_lifecycle_active(conn, user_id: str) -> dict[str, Any]:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return {"user_id": normalized_user_id, "status": ACTIVE_STATUS}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO account_lifecycle (user_id, status)
            VALUES (%s, 'active')
            ON CONFLICT (user_id) DO NOTHING
            """,
            (normalized_user_id,),
        )

    lifecycle = get_account_lifecycle(conn, normalized_user_id)
    return lifecycle or {"user_id": normalized_user_id, "status": ACTIVE_STATUS}


def create_pending_account_deletion(
    conn,
    *,
    user_id: str,
    reason: str,
    restore_deadline: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "account_lifecycle_not_configured",
                "message": "Account lifecycle migration has not been applied.",
            },
        )

    now = utc_now()
    normalized_deadline = restore_deadline if restore_deadline.tzinfo else restore_deadline.replace(tzinfo=timezone.utc)
    if normalized_deadline <= now:
        normalized_deadline = now

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO account_lifecycle (
                user_id,
                status,
                deletion_reason,
                deactivated_at,
                restore_deadline,
                purge_after,
                metadata
            )
            VALUES (%s, 'deactivated_pending_deletion', %s, NOW(), %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                status = 'deactivated_pending_deletion',
                deletion_reason = EXCLUDED.deletion_reason,
                deactivated_at = COALESCE(account_lifecycle.deactivated_at, NOW()),
                restore_deadline = EXCLUDED.restore_deadline,
                purge_after = EXCLUDED.purge_after,
                restored_at = NULL,
                purged_at = NULL,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING user_id, status, deletion_reason, deactivated_at,
                      restore_deadline, purge_after, restored_at, purged_at,
                      metadata, created_at, updated_at
            """,
            (
                normalized_user_id,
                reason,
                normalized_deadline,
                normalized_deadline,
                Jsonb(metadata or {}),
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to create pending account deletion.")

    return {
        "user_id": row[0],
        "status": row[1],
        "deletion_reason": row[2],
        "deactivated_at": row[3],
        "restore_deadline": row[4],
        "purge_after": row[5],
        "restored_at": row[6],
        "purged_at": row[7],
        "metadata": row[8] or {},
        "created_at": row[9],
        "updated_at": row[10],
    }


def mark_account_purged(conn, user_id: str) -> None:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET status = 'purged',
                purged_at = COALESCE(purged_at, NOW()),
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (normalized_user_id,),
        )


def account_is_deactivated(lifecycle: dict[str, Any] | None) -> bool:
    return lifecycle is not None and lifecycle.get("status") in RESTORABLE_STATUSES


def restore_account_if_allowed(conn, user_id: str) -> dict[str, Any] | None:
    normalized_user_id = normalize_user_id(user_id)
    lifecycle = get_account_lifecycle(conn, normalized_user_id)
    if not account_is_deactivated(lifecycle):
        return lifecycle

    now = utc_now()
    restore_deadline = lifecycle.get("restore_deadline")
    if isinstance(restore_deadline, datetime):
        normalized_deadline = restore_deadline if restore_deadline.tzinfo else restore_deadline.replace(tzinfo=timezone.utc)
    else:
        normalized_deadline = now

    if normalized_deadline <= now:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE account_lifecycle
                SET status = 'purge_due',
                    updated_at = NOW()
                WHERE user_id = %s
                  AND status = 'deactivated_pending_deletion'
                """,
                (normalized_user_id,),
            )
        raise HTTPException(
            status_code=401,
            detail={
                "error": "account_restore_window_elapsed",
                "message": "This account can no longer be restored because its subscription period has elapsed.",
            },
        )

    metadata = lifecycle.get("metadata") if isinstance(lifecycle.get("metadata"), dict) else {}

    if metadata.get("personal_subscription"):
        used_fallback_deadline = bool(metadata.get("used_fallback_restore_deadline"))
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = 'active',
                    updated_at = NOW()
                WHERE user_id = %s
                  AND plan = 'personal'
                  AND (
                    current_period_end > NOW()
                    OR (%s AND current_period_end IS NULL)
                  )
                """,
                (normalized_user_id, used_fallback_deadline),
            )

    organizations = metadata.get("organizations")
    if isinstance(organizations, list):
        for organization in organizations:
            if not isinstance(organization, dict):
                continue
            organization_id = organization.get("organization_id")
            if not isinstance(organization_id, int):
                continue
            used_fallback_deadline = bool(metadata.get("used_fallback_restore_deadline"))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organizations
                    SET owner_user_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (normalized_user_id, organization_id),
                )
                cur.execute(
                    """
                    INSERT INTO organization_members (
                        organization_id,
                        user_id,
                        role,
                        status,
                        joined_at
                    )
                    VALUES (%s, %s, 'owner', 'active', NOW())
                    ON CONFLICT (organization_id, user_id) DO UPDATE SET
                        role = 'owner',
                        status = 'active',
                        joined_at = COALESCE(organization_members.joined_at, NOW()),
                        updated_at = NOW()
                    """,
                    (organization_id, normalized_user_id),
                )
                cur.execute(
                    """
                    UPDATE organization_subscriptions
                    SET status = 'active',
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND (
                        current_period_end > NOW()
                        OR (%s AND current_period_end IS NULL)
                      )
                    """,
                    (organization_id, used_fallback_deadline),
                )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET status = 'active',
                restored_at = NOW(),
                updated_at = NOW()
            WHERE user_id = %s
            RETURNING user_id, status, deletion_reason, deactivated_at,
                      restore_deadline, purge_after, restored_at, purged_at,
                      metadata, created_at, updated_at
            """,
            (normalized_user_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "user_id": row[0],
        "status": row[1],
        "deletion_reason": row[2],
        "deactivated_at": row[3],
        "restore_deadline": row[4],
        "purge_after": row[5],
        "restored_at": row[6],
        "purged_at": row[7],
        "metadata": row[8] or {},
        "created_at": row[9],
        "updated_at": row[10],
    }


def list_purge_due_user_ids(conn, *, limit: int = 100) -> list[str]:
    if not account_lifecycle_table_exists(conn):
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM account_lifecycle
            WHERE status IN ('deactivated_pending_deletion', 'purge_due')
              AND purge_after <= NOW()
            ORDER BY purge_after ASC, updated_at ASC
            LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        rows = cur.fetchall()

    return [row[0] for row in rows]


__all__ = [
    "ACTIVE_STATUS",
    "DEACTIVATED_PENDING_DELETION_STATUS",
    "PURGE_DUE_STATUS",
    "PURGED_STATUS",
    "RESTORABLE_STATUSES",
    "account_is_deactivated",
    "account_lifecycle_table_exists",
    "create_pending_account_deletion",
    "ensure_account_lifecycle_active",
    "get_account_lifecycle",
    "list_purge_due_user_ids",
    "mark_account_purged",
    "relation_exists",
    "resolve_restore_deadline",
    "restore_account_if_allowed",
    "utc_now",
]
