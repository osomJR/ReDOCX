from __future__ import annotations

"""
Account lifecycle helpers for soft deactivation, restore, and delayed purge.

This module intentionally contains no FastAPI router. It is shared by account,
organization, and authentication layers so account deletion rules remain
backend-enforced instead of being only a frontend warning.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import os
from typing import Any

from fastapi import HTTPException
from psycopg import connect
from psycopg.types.json import Jsonb

ACTIVE_STATUS = "active"
DEACTIVATION_REQUESTED_STATUS = "deactivation_requested"
DEACTIVATED_PENDING_DELETION_STATUS = "deactivated_pending_deletion"
PURGE_DUE_STATUS = "purge_due"
PURGED_STATUS = "purged"
RESTORABLE_STATUSES = {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}
ACCOUNT_ACCESS_RESTRICTED_STATUSES = {
    DEACTIVATION_REQUESTED_STATUS,
    *RESTORABLE_STATUSES,
}

DEFAULT_RESTORE_DAYS = max(
    int(
        os.getenv(
            "ACCOUNT_DELETION_RECOVERY_DAYS",
            os.getenv("ACCOUNT_DELETION_FALLBACK_RESTORE_DAYS", "30"),
        )
    ),
    1,
)


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


def try_lock_account_lifecycle(conn, user_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            (f"account-deletion:{normalize_user_id(user_id)}",),
        )
        return bool(cur.fetchone()[0])


def lock_account_lifecycle(conn, user_id: str) -> None:
    if not try_lock_account_lifecycle(conn, user_id):
        raise HTTPException(409, detail={
            "error": "account_operation_pending",
            "message": "An account operation is in progress. Check its status and try again.",
        })


def require_active_account(conn, user_id: str) -> None:
    if not account_lifecycle_table_exists(conn):
        raise HTTPException(503, detail="Account status is temporarily unavailable.")
    lifecycle = get_account_lifecycle(conn, user_id)
    if lifecycle and lifecycle["status"] != ACTIVE_STATUS:
        raise HTTPException(
            409,
            detail={
                "error": "account_lifecycle_restricted",
                "message": "This action is unavailable while your account is deactivated.",
            },
        )


@contextmanager
def account_operation(user_id: str):
    """Serialize external side effects without occupying an application pool slot."""
    with connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        lock_account_lifecycle(conn, user_id)
        yield conn


def account_subject_tombstone_id(user_id: str) -> str:
    """Return the irreversible stable identifier used after permanent deletion."""

    normalized = normalize_user_id(user_id)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return f"deleted:{digest}"

def resolve_restore_deadline(period_end: datetime | None = None) -> tuple[datetime, bool]:
    """
    Give every account a recovery window before permanent deletion.

    Free accounts receive the recovery window from the deletion request time.
    Paid accounts receive the remainder of their current paid period plus the
    same recovery window. Legacy/provider rows without a future period end use
    the bounded free-account window so they cannot become non-purgeable.
    """

    now = utc_now()
    recovery_window = timedelta(days=DEFAULT_RESTORE_DAYS)
    if isinstance(period_end, datetime):
        normalized = period_end if period_end.tzinfo else period_end.replace(tzinfo=timezone.utc)
        if normalized > now:
            return normalized + recovery_window, False

    return now + recovery_window, True


def get_account_lifecycle(conn, user_id: str) -> dict[str, Any] | None:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return None

    tombstone_user_id = account_subject_tombstone_id(normalized_user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id, status, deletion_reason, deactivated_at,
                   restore_deadline, purge_after, restored_at, purged_at,
                   metadata, created_at, updated_at
            FROM account_lifecycle
            WHERE user_id IN (%s, %s)
            ORDER BY CASE WHEN user_id = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (normalized_user_id, tombstone_user_id, normalized_user_id),
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

    existing = get_account_lifecycle(conn, normalized_user_id)
    if existing is not None:
        return existing

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


def create_account_deactivation_request(
    conn,
    *,
    user_id: str,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Durably record deletion intent before any external provider mutation."""

    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        raise HTTPException(
            status_code=503,
            detail={
                "error": "account_lifecycle_not_configured",
                "message": "Account lifecycle migration has not been applied.",
            },
        )

    lock_account_lifecycle(conn, normalized_user_id)

    existing = get_account_lifecycle(conn, normalized_user_id)
    if existing is not None and existing.get("status") != ACTIVE_STATUS:
        return existing

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO account_lifecycle (
                user_id, status, deletion_reason, metadata
            )
            VALUES (%s, 'deactivation_requested', %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                status = 'deactivation_requested',
                deletion_reason = EXCLUDED.deletion_reason,
                deactivated_at = NULL,
                restore_deadline = NULL,
                purge_after = NULL,
                restored_at = NULL,
                purged_at = NULL,
                purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_attempts = 0,
                purge_last_error = NULL,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            WHERE account_lifecycle.status = 'active'
            RETURNING user_id, status, deletion_reason, deactivated_at,
                      restore_deadline, purge_after, restored_at, purged_at,
                      metadata, created_at, updated_at
            """,
            (normalized_user_id, reason, Jsonb(metadata or {})),
        )
        row = cur.fetchone()

    if row is None:
        lifecycle = get_account_lifecycle(conn, normalized_user_id)
        if lifecycle is not None:
            return lifecycle
        raise RuntimeError("Failed to create account deactivation request.")

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
                purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_attempts = 0,
                purge_last_error = NULL,
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


def mark_account_purged(
    conn,
    user_id: str,
    *,
    worker_id: str | None = None,
) -> None:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return

    tombstone_user_id = account_subject_tombstone_id(normalized_user_id)
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET user_id = %s,
                status = 'purged',
                deletion_reason = 'account_permanently_deleted',
                restore_deadline = NULL,
                purge_after = NULL,
                restored_at = NULL,
                purged_at = COALESCE(purged_at, NOW()),
                purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_last_error = NULL,
                metadata = '{}'::jsonb,
                updated_at = NOW()
            WHERE user_id = %s
              AND (
                   (%s::text IS NULL AND status = 'purged')
                   OR (status = 'purge_due' AND purge_locked_by = %s)
              )
            """,
            (
                tombstone_user_id,
                normalized_user_id,
                worker_id,
                worker_id,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                "The account purge lease was lost before completion."
            )


def lock_purge_claim(conn, user_id: str, worker_id: str):
    """Recheck the lease under the same lock used by restore and billing."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(metadata, '{}'::jsonb)
            FROM account_lifecycle
            WHERE user_id = %s AND status = 'purge_due'
              AND purge_locked_by = %s AND purge_after <= clock_timestamp()
            FOR UPDATE
            """,
            (user_id, worker_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def account_is_deactivated(lifecycle: dict[str, Any] | None) -> bool:
    return lifecycle is not None and lifecycle.get("status") in RESTORABLE_STATUSES


def account_access_is_restricted(lifecycle: dict[str, Any] | None) -> bool:
    return (
        lifecycle is not None
        and lifecycle.get("status") in ACCOUNT_ACCESS_RESTRICTED_STATUSES
    )


def normalize_optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def restore_account_if_allowed(conn, user_id: str) -> dict[str, Any] | None:
    normalized_user_id = normalize_user_id(user_id)
    lock_account_lifecycle(conn, normalized_user_id)
    lifecycle = get_account_lifecycle(conn, normalized_user_id)
    status = (lifecycle or {}).get("status")
    if status == DEACTIVATION_REQUESTED_STATUS:
        raise HTTPException(409, detail={
            "error": "account_deactivation_in_progress",
            "message": "Account deactivation is still being finalized. Try again after it completes.",
        })
    if status in {None, ACTIVE_STATUS}:
        return lifecycle

    with conn.cursor() as cur:
        # Claim restoration before touching any related rows. A competing purge
        # either claimed the row already or will see this change after commit.
        cur.execute(
            """
            UPDATE account_lifecycle
            SET status = 'active', restored_at = NOW(),
                deletion_reason = NULL, deactivated_at = NULL,
                restore_deadline = NULL, purge_after = NULL, metadata = '{}'::jsonb,
                purge_locked_at = NULL, purge_locked_by = NULL,
                purge_last_error = NULL, updated_at = NOW()
            WHERE user_id = %s AND status = 'deactivated_pending_deletion'
              AND restore_deadline > clock_timestamp() AND purge_after > clock_timestamp()
              AND purge_locked_by IS NULL
            RETURNING restored_at
            """,
            (normalized_user_id,),
        )
        if cur.fetchone() is None:
            raise HTTPException(409, detail={
                "error": "account_restore_window_elapsed",
                "message": "The recovery deadline has passed. This account can no longer be restored.",
            })

        # The paid period remains usable under the cancelled-at-period-end row.
        # Renewal can only be restarted explicitly from Billing after restore.
        for organization in ((lifecycle.get("metadata") or {}).get("organizations") or []):
            if not isinstance(organization, dict) or not isinstance(organization.get("organization_id"), int):
                continue
            cur.execute(
                """
                UPDATE organization_members m
                SET status = 'active', updated_at = NOW()
                FROM organizations o
                WHERE m.organization_id = o.id AND o.id = %s
                  AND o.owner_user_id = %s AND m.user_id = %s
                  AND m.role = 'owner' AND m.status = 'removed'
                """,
                (organization["organization_id"], normalized_user_id, normalized_user_id),
            )

    return get_account_lifecycle(conn, normalized_user_id)


def list_legacy_purged_user_ids(conn, *, limit: int = 100) -> list[str]:
    """Return pre-hardening purged rows that still contain the raw auth subject."""

    if not account_lifecycle_table_exists(conn):
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM account_lifecycle
            WHERE status = 'purged'
              AND user_id NOT LIKE 'deleted:%%'
            ORDER BY purged_at ASC NULLS FIRST, updated_at ASC
            LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        rows = cur.fetchall()

    return [str(row[0]) for row in rows]


def list_deactivation_requested_user_ids(conn, *, limit: int = 100) -> list[str]:
    if not account_lifecycle_table_exists(conn):
        return []

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM account_lifecycle
            WHERE status = 'deactivation_requested'
            ORDER BY updated_at ASC
            LIMIT %s
            """,
            (max(1, min(int(limit), 1000)),),
        )
        rows = cur.fetchall()

    return [str(row[0]) for row in rows]


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


def claim_purge_due_user_ids(
    conn,
    *,
    worker_id: str,
    limit: int = 100,
    lease_seconds: int = 1800,
) -> list[str]:
    normalized_worker_id = str(worker_id or "").strip()
    if not normalized_worker_id:
        raise ValueError("worker_id is required.")
    if not account_lifecycle_table_exists(conn):
        return []

    normalized_limit = max(1, min(int(limit), 1000))
    normalized_lease_seconds = max(60, min(int(lease_seconds), 86400))

    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
                SELECT user_id
                FROM account_lifecycle
                WHERE status IN ('deactivated_pending_deletion', 'purge_due')
                  AND purge_after <= NOW()
                  AND (
                    purge_locked_at IS NULL
                    OR purge_locked_at <= (
                        NOW() - MAKE_INTERVAL(secs => %s)
                    )
                  )
                ORDER BY purge_after ASC, updated_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE account_lifecycle lifecycle
            SET status = 'purge_due',
                purge_locked_at = NOW(),
                purge_locked_by = %s,
                purge_attempts = purge_attempts + 1,
                purge_last_error = NULL,
                updated_at = NOW()
            FROM candidates
            WHERE lifecycle.user_id = candidates.user_id
            RETURNING lifecycle.user_id
            """,
            (
                normalized_lease_seconds,
                normalized_limit,
                normalized_worker_id,
            ),
        )
        rows = cur.fetchall()

    return [str(row[0]) for row in rows]


def release_account_purge_claim(
    conn,
    *,
    user_id: str,
    worker_id: str,
    error: str,
) -> None:
    normalized_user_id = normalize_user_id(user_id)
    normalized_worker_id = str(worker_id or "").strip()
    if not normalized_worker_id:
        raise ValueError("worker_id is required.")

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_last_error = %s,
                updated_at = NOW()
            WHERE user_id = %s
              AND status = 'purge_due'
              AND purge_locked_by = %s
            """,
            (
                str(error or "")[:2000] or "Account purge failed.",
                normalized_user_id,
                normalized_worker_id,
            ),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                "The account purge lease could not be released because its ownership changed."
            )


__all__ = [
    "ACTIVE_STATUS",
    "ACCOUNT_ACCESS_RESTRICTED_STATUSES",
    "DEACTIVATION_REQUESTED_STATUS",
    "DEACTIVATED_PENDING_DELETION_STATUS",
    "PURGE_DUE_STATUS",
    "PURGED_STATUS",
    "RESTORABLE_STATUSES",
    "account_access_is_restricted",
    "account_is_deactivated",
    "account_lifecycle_table_exists",
    "account_subject_tombstone_id",
    "claim_purge_due_user_ids",
    "create_account_deactivation_request",
    "create_pending_account_deletion",
    "ensure_account_lifecycle_active",
    "get_account_lifecycle",
    "list_deactivation_requested_user_ids",
    "list_legacy_purged_user_ids",
    "list_purge_due_user_ids",
    "mark_account_purged",
    "normalize_optional_datetime",
    "relation_exists",
    "release_account_purge_claim",
    "resolve_restore_deadline",
    "restore_account_if_allowed",
    "utc_now",
]
