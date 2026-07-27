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

from backend.billing_provider import BillingProviderError, resume_provider_subscription


ACTIVE_STATUS = "active"
DEACTIVATED_PENDING_DELETION_STATUS = "deactivated_pending_deletion"
PURGE_DUE_STATUS = "purge_due"
PURGED_STATUS = "purged"
RESTORABLE_STATUSES = {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}

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


def mark_account_purged(
    conn,
    user_id: str,
    *,
    worker_id: str | None = None,
) -> None:
    normalized_user_id = normalize_user_id(user_id)
    if not account_lifecycle_table_exists(conn):
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET status = 'purged',
                purged_at = COALESCE(purged_at, NOW()),
                purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_last_error = NULL,
                updated_at = NOW()
            WHERE user_id = %s
              AND (%s IS NULL OR purge_locked_by = %s)
            """,
            (normalized_user_id, worker_id, worker_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError(
                "The account purge lease was lost before completion."
            )


def account_is_deactivated(lifecycle: dict[str, Any] | None) -> bool:
    return lifecycle is not None and lifecycle.get("status") in RESTORABLE_STATUSES


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


def resume_external_subscription_if_needed(
    *,
    provider: Any,
    provider_subscription_id: Any,
    current_period_end: Any,
    used_fallback_deadline: bool,
    cancellation_was_requested: bool,
) -> datetime | None:
    period_end = normalize_optional_datetime(current_period_end)
    if not cancellation_was_requested:
        return period_end

    provider_name = str(provider or "").strip()
    subscription_id = str(provider_subscription_id or "").strip()
    if not provider_name or not subscription_id:
        return period_end

    now = utc_now()
    if period_end is not None and period_end <= now:
        # The paid term already ended. Restoring the ReDOCX account is still
        # allowed during the 30-day recovery window, but the expired paid plan
        # must not be restarted automatically.
        return period_end
    if period_end is None and not used_fallback_deadline:
        return None

    try:
        result = resume_provider_subscription(provider_name, subscription_id)
    except BillingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "subscription_resume_failed",
                "message": (
                    "Your account is still deactivated because its billing "
                    "subscription could not be resumed. Please retry."
                ),
            },
        ) from exc

    return result.current_period_end or period_end


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
                "message": "This account can no longer be restored because its recovery window has elapsed.",
            },
        )

    metadata = lifecycle.get("metadata") if isinstance(lifecycle.get("metadata"), dict) else {}

    cancellation_was_requested = bool(
        metadata.get("external_cancellation_requested")
    )
    used_fallback_deadline = bool(metadata.get("used_fallback_restore_deadline"))

    if metadata.get("personal_subscription"):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider, provider_subscription_id, current_period_end
                FROM user_subscriptions
                WHERE user_id = %s
                  AND plan = 'personal'
                LIMIT 1
                """,
                (normalized_user_id,),
            )
            subscription_row = cur.fetchone()

        resumed_period_end = None
        if subscription_row is not None:
            resumed_period_end = resume_external_subscription_if_needed(
                provider=subscription_row[0],
                provider_subscription_id=subscription_row[1],
                current_period_end=subscription_row[2],
                used_fallback_deadline=used_fallback_deadline,
                cancellation_was_requested=cancellation_was_requested,
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = 'active',
                    current_period_end = COALESCE(%s, current_period_end),
                    updated_at = NOW()
                WHERE user_id = %s
                  AND plan = 'personal'
                  AND (
                    current_period_end > NOW()
                    OR (%s AND current_period_end IS NULL)
                  )
                """,
                (
                    resumed_period_end,
                    normalized_user_id,
                    used_fallback_deadline,
                ),
            )

    organizations = metadata.get("organizations")
    if isinstance(organizations, list):
        for organization in organizations:
            if not isinstance(organization, dict):
                continue
            organization_id = organization.get("organization_id")
            if not isinstance(organization_id, int):
                continue

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT provider, provider_subscription_id, current_period_end
                    FROM organization_subscriptions
                    WHERE organization_id = %s
                    LIMIT 1
                    """,
                    (organization_id,),
                )
                subscription_row = cur.fetchone()

            resumed_period_end = None
            if subscription_row is not None:
                resumed_period_end = resume_external_subscription_if_needed(
                    provider=subscription_row[0],
                    provider_subscription_id=subscription_row[1],
                    current_period_end=subscription_row[2],
                    used_fallback_deadline=used_fallback_deadline,
                    cancellation_was_requested=cancellation_was_requested,
                )

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
                        current_period_end = COALESCE(%s, current_period_end),
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND (
                        current_period_end > NOW()
                        OR (%s AND current_period_end IS NULL)
                      )
                    """,
                    (
                        resumed_period_end,
                        organization_id,
                        used_fallback_deadline,
                    ),
                )

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE account_lifecycle
            SET status = 'active',
                restored_at = NOW(),
                purge_locked_at = NULL,
                purge_locked_by = NULL,
                purge_last_error = NULL,
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
    "DEACTIVATED_PENDING_DELETION_STATUS",
    "PURGE_DUE_STATUS",
    "PURGED_STATUS",
    "RESTORABLE_STATUSES",
    "account_is_deactivated",
    "account_lifecycle_table_exists",
    "claim_purge_due_user_ids",
    "create_pending_account_deletion",
    "ensure_account_lifecycle_active",
    "get_account_lifecycle",
    "list_purge_due_user_ids",
    "mark_account_purged",
    "normalize_optional_datetime",
    "relation_exists",
    "release_account_purge_claim",
    "resolve_restore_deadline",
    "restore_account_if_allowed",
    "utc_now",
]