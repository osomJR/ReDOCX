from __future__ import annotations

"""One-shot ReDOCX billing/account maintenance command.

Run from a Railway Cron service with:
    python -m backend.maintenance

The command is deliberately finite: repair legacy purged-account privacy state,
resume interrupted account-deactivation sagas, reconcile provider state, enforce
expired access, purge accounts whose restoration window elapsed, print a JSON
summary, and exit. A PostgreSQL advisory lock prevents concurrent executions.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from backend.billing_provider import (
    BillingProviderError,
    BillingSubscriptionState,
    BillingWebhookEvent,
    retrieve_provider_subscription,
)
from backend.account_deletion_jobs import (
    complete_pending_deactivations as run_pending_deactivation_job,
    purge_due_accounts as run_account_deletion_job,
    repair_legacy_purged_accounts as run_legacy_purge_repair_job,
)
from backend.database import get_db
from backend.routes.billing_webhooks import apply_verified_billing_event

logger = logging.getLogger(__name__)

MAINTENANCE_ADVISORY_LOCK_ID = int(
    os.getenv("BILLING_MAINTENANCE_ADVISORY_LOCK_ID", "731924681")
)
RECONCILIATION_BATCH_SIZE = max(
    1, int(os.getenv("BILLING_RECONCILIATION_BATCH_SIZE", "100"))
)
ACCOUNT_PURGE_BATCH_SIZE = max(
    1, int(os.getenv("ACCOUNT_PURGE_BATCH_SIZE", "50"))
)
ACCOUNT_DEACTIVATION_BATCH_SIZE = max(
    1, int(os.getenv("ACCOUNT_DEACTIVATION_BATCH_SIZE", "50"))
)
ACCOUNT_PURGE_LEASE_SECONDS = max(
    60, int(os.getenv("ACCOUNT_DELETION_PURGE_LEASE_SECONDS", "1800"))
)
PAYMENT_GRACE_DAYS = max(
    0, int(os.getenv("BILLING_PAYMENT_GRACE_DAYS", "7"))
)

SubscriptionTable = Literal["user_subscriptions", "organization_subscriptions"]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _provider_status(
    provider: str,
    state: BillingSubscriptionState,
) -> str:
    status = (state.status or "").strip().lower().replace("_", "-")

    if provider == "stripe":
        if status in {"active", "trialing"}:
            return "cancelled" if state.cancel_at_period_end else "active"
        if status in {"past-due", "unpaid", "incomplete", "incomplete-expired", "paused"}:
            return "past_due"
        if status in {"canceled", "cancelled"}:
            return "cancelled"
        return "inactive"

    if provider == "paystack":
        if status == "active":
            return "active"
        if status in {"non-renewing", "cancelled", "canceled", "completed"}:
            return "cancelled"
        if status in {"attention", "past-due", "failed"}:
            return "past_due"
        return "inactive"

    return "inactive"


def _load_reconciliation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 'user_subscriptions' AS table_name,
                       user_id::text AS owner_id,
                       plan,
                       status,
                       provider,
                       provider_subscription_id,
                       current_period_end,
                       grace_period_end,
                       access_revoked_at,
                       pending_plan,
                       plan_change_effective_at,
                       last_reconciled_at
                FROM user_subscriptions
                WHERE provider IN ('stripe', 'paystack')
                  AND provider_subscription_id IS NOT NULL
                UNION ALL
                SELECT 'organization_subscriptions' AS table_name,
                       organization_id::text AS owner_id,
                       plan,
                       status,
                       provider,
                       provider_subscription_id,
                       current_period_end,
                       grace_period_end,
                       access_revoked_at,
                       pending_plan,
                       plan_change_effective_at,
                       last_reconciled_at
                FROM organization_subscriptions
                WHERE provider IN ('stripe', 'paystack')
                  AND provider_subscription_id IS NOT NULL
                ORDER BY last_reconciled_at ASC NULLS FIRST, table_name, owner_id
                LIMIT %s
                """,
                (RECONCILIATION_BATCH_SIZE,),
            )
            for row in cur.fetchall():
                rows.append(
                    {
                        "table": row[0],
                        "owner_id": row[1],
                        "plan": row[2],
                        "status": row[3],
                        "provider": row[4],
                        "provider_subscription_id": row[5],
                        "current_period_end": row[6],
                        "grace_period_end": row[7],
                        "access_revoked_at": row[8],
                        "pending_plan": row[9],
                        "plan_change_effective_at": row[10],
                        "last_reconciled_at": row[11],
                    }
                )
    return rows


def _record_reconciliation_error(row: dict[str, Any], message: str) -> None:
    table: SubscriptionTable = row["table"]
    owner_column = "user_id" if table == "user_subscriptions" else "organization_id"
    owner_value: str | int = row["owner_id"]
    if table == "organization_subscriptions":
        owner_value = int(owner_value)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET last_reconciled_at = NOW(),
                    reconciliation_error = %s,
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (message[:1000], owner_value),
            )


def _reconciliation_identity(
    row: dict[str, Any],
    state: BillingSubscriptionState,
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "user_id": row["owner_id"] if row["table"] == "user_subscriptions" else None,
        "organization_id": (
            int(row["owner_id"])
            if row["table"] == "organization_subscriptions"
            else None
        ),
        "organization_name": None,
    }

    raw_metadata = (
        state.raw.get("metadata")
        if isinstance(state.raw.get("metadata"), dict)
        else {}
    )
    identity["user_id"] = raw_metadata.get("user_id") or identity["user_id"]
    identity["organization_name"] = raw_metadata.get("organization_name")
    raw_organization_id = raw_metadata.get("organization_id")
    if raw_organization_id not in (None, ""):
        try:
            identity["organization_id"] = int(raw_organization_id)
        except (TypeError, ValueError):
            pass

    with get_db() as conn:
        with conn.cursor() as cur:
            if row["table"] == "organization_subscriptions":
                cur.execute(
                    "SELECT owner_user_id, name FROM organizations WHERE id = %s",
                    (int(row["owner_id"]),),
                )
                organization_row = cur.fetchone()
                if organization_row is not None:
                    identity["user_id"] = identity["user_id"] or organization_row[0]
                    identity["organization_name"] = (
                        identity["organization_name"] or organization_row[1]
                    )

            cur.execute(
                """
                SELECT user_id, organization_id, organization_name, target_plan
                FROM billing_checkout_sessions
                WHERE provider = %s
                  AND (
                        provider_subscription_id = %s
                     OR replaced_provider_subscription_id = %s
                  )
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (
                    state.provider,
                    state.provider_subscription_id,
                    state.provider_subscription_id,
                ),
            )
            ledger_row = cur.fetchone()
            if ledger_row is not None:
                identity["user_id"] = identity["user_id"] or ledger_row[0]
                identity["organization_id"] = (
                    identity["organization_id"]
                    if identity["organization_id"] is not None
                    else ledger_row[1]
                )
                identity["organization_name"] = (
                    identity["organization_name"] or ledger_row[2]
                )
                identity["target_plan"] = ledger_row[3]

    return identity


def _reconcile_plan_scope(
    row: dict[str, Any],
    state: BillingSubscriptionState,
) -> None:
    provider_plan = state.plan
    if row.get("access_revoked_at") is not None:
        return
    if provider_plan not in {"personal", "business", "enterprise"}:
        return
    if _provider_status(row["provider"], state) != "active":
        return

    expected_table = (
        "user_subscriptions"
        if provider_plan == "personal"
        else "organization_subscriptions"
    )
    if row["table"] == expected_table and row["plan"] == provider_plan:
        return

    identity = _reconciliation_identity(row, state)
    user_id = str(identity.get("user_id") or "").strip()
    if not user_id:
        raise BillingProviderError(
            "Could not resolve the subscription owner during plan reconciliation."
        )

    event = BillingWebhookEvent(
        provider=state.provider,
        event_id=(
            f"reconcile:{state.provider}:{state.provider_subscription_id}:"
            f"{provider_plan}"
        ),
        event_type="provider.reconciliation",
        action="activate",
        plan=provider_plan,
        status=state.status,
        user_id=user_id,
        organization_id=identity.get("organization_id"),
        organization_name=identity.get("organization_name"),
        provider_customer_id=state.provider_customer_id,
        provider_subscription_id=state.provider_subscription_id,
        current_period_start=state.current_period_start,
        current_period_end=state.current_period_end,
        raw=state.raw,
    )
    with get_db() as conn:
        result = apply_verified_billing_event(conn, event)
    if result.get("ignored"):
        logger.info(
            "Plan reconciliation ignored provider=%s subscription=%s reason=%s",
            state.provider,
            state.provider_subscription_id,
            result.get("reason"),
        )


def _store_reconciled_state(
    row: dict[str, Any],
    state: BillingSubscriptionState,
) -> None:
    table: SubscriptionTable = row["table"]
    owner_column = "user_id" if table == "user_subscriptions" else "organization_id"
    owner_value: str | int = row["owner_id"]
    if table == "organization_subscriptions":
        owner_value = int(owner_value)

    resolved_status = _provider_status(row["provider"], state)
    # An explicit refund/dispute revocation is only cleared by a verified
    # restore webhook, never by a generic provider-status poll.
    preserve_revocation = row.get("access_revoked_at") is not None
    if preserve_revocation:
        resolved_status = str(row.get("status") or "past_due")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = %s,
                    provider_customer_id = COALESCE(%s, provider_customer_id),
                    provider_subscription_id = %s,
                    current_period_start = COALESCE(%s, current_period_start),
                    current_period_end = COALESCE(%s, current_period_end),
                    cancel_at_period_end = %s,
                    grace_period_end = CASE
                        WHEN %s = 'past_due' AND access_revoked_at IS NULL THEN
                            COALESCE(
                                grace_period_end,
                                GREATEST(COALESCE(%s, current_period_end, NOW()), NOW())
                                + make_interval(days => %s)
                            )
                        WHEN %s = 'active' THEN NULL
                        ELSE grace_period_end
                    END,
                    payment_failure_count = CASE
                        WHEN %s = 'active' THEN 0
                        WHEN %s = 'past_due' AND status <> 'past_due'
                            THEN payment_failure_count + 1
                        ELSE payment_failure_count
                    END,
                    last_reconciled_at = NOW(),
                    reconciliation_error = NULL,
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (
                    resolved_status,
                    state.provider_customer_id,
                    state.provider_subscription_id,
                    state.current_period_start,
                    state.current_period_end,
                    state.cancel_at_period_end,
                    resolved_status,
                    state.current_period_end,
                    PAYMENT_GRACE_DAYS,
                    resolved_status,
                    resolved_status,
                    resolved_status,
                    owner_value,
                ),
            )


def reconcile_subscriptions() -> dict[str, int]:
    summary = {"checked": 0, "updated": 0, "failed": 0}
    for row in _load_reconciliation_rows():
        summary["checked"] += 1
        try:
            state = retrieve_provider_subscription(
                row["provider"],
                row["provider_subscription_id"],
            )
            _store_reconciled_state(row, state)
            _reconcile_plan_scope(row, state)
            summary["updated"] += 1
        except (BillingProviderError, ValueError, TypeError) as exc:
            _record_reconciliation_error(row, str(exc))
            summary["failed"] += 1
            logger.warning(
                "Billing reconciliation failed provider=%s subscription=%s error=%s",
                row["provider"],
                row["provider_subscription_id"],
                str(exc),
            )
        except Exception as exc:  # defensive isolation per subscription
            _record_reconciliation_error(row, "Unexpected reconciliation failure.")
            summary["failed"] += 1
            logger.exception(
                "Unexpected billing reconciliation failure provider=%s subscription=%s",
                row["provider"],
                row["provider_subscription_id"],
            )
    return summary


def enforce_access_expiration() -> dict[str, int]:
    summary = {"user_rows": 0, "organization_rows": 0}
    with get_db() as conn:
        for table, key in (
            ("user_subscriptions", "user_rows"),
            ("organization_subscriptions", "organization_rows"),
        ):
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {table}
                    SET status = 'inactive',
                        updated_at = NOW()
                    WHERE (
                            status IN ('active', 'cancelled')
                            AND current_period_end IS NOT NULL
                            AND current_period_end <= NOW()
                          )
                       OR (
                            status = 'past_due'
                            AND grace_period_end IS NOT NULL
                            AND grace_period_end <= NOW()
                          )
                    """
                )
                summary[key] = int(cur.rowcount or 0)
    return summary


def repair_legacy_purged_accounts() -> dict[str, int]:
    result = run_legacy_purge_repair_job(limit=ACCOUNT_PURGE_BATCH_SIZE)
    return {
        "legacy": int(result.get("legacy_count") or 0),
        "repaired": int(result.get("repaired_count") or 0),
        "failed": int(result.get("failed_count") or 0),
    }


def complete_pending_deactivations() -> dict[str, int]:
    result = run_pending_deactivation_job(limit=ACCOUNT_DEACTIVATION_BATCH_SIZE)
    return {
        "requested": int(result.get("requested_count") or 0),
        "completed": int(result.get("completed_count") or 0),
        "failed": int(result.get("failed_count") or 0),
    }


def purge_due_accounts() -> dict[str, int]:
    result = run_account_deletion_job(
        limit=ACCOUNT_PURGE_BATCH_SIZE,
        lease_seconds=ACCOUNT_PURGE_LEASE_SECONDS,
    )
    return {
        "claimed": int(result.get("claimed_count") or 0),
        "purged": int(result.get("purged_count") or 0),
        "failed": int(result.get("failed_count") or 0),
    }


def run_maintenance() -> dict[str, Any]:
    started_at = _utcnow()
    with get_db() as lock_conn:
        with lock_conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s)",
                (MAINTENANCE_ADVISORY_LOCK_ID,),
            )
            lock_row = cur.fetchone()
            if not lock_row or lock_row[0] is not True:
                return {
                    "success": True,
                    "skipped": True,
                    "reason": "maintenance_already_running",
                    "started_at": started_at.isoformat(),
                }

        try:
            legacy_purge_repair = repair_legacy_purged_accounts()
            account_deactivation = complete_pending_deactivations()
            reconciliation = reconcile_subscriptions()
            expiration = enforce_access_expiration()
            account_purge = purge_due_accounts()
        finally:
            with lock_conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (MAINTENANCE_ADVISORY_LOCK_ID,),
                )

    failed = (
        legacy_purge_repair["failed"]
        + account_deactivation["failed"]
        + reconciliation["failed"]
        + account_purge["failed"]
    )
    return {
        "success": failed == 0,
        "skipped": False,
        "started_at": started_at.isoformat(),
        "finished_at": _utcnow().isoformat(),
        "legacy_purge_repair": legacy_purge_repair,
        "account_deactivation": account_deactivation,
        "reconciliation": reconciliation,
        "expiration": expiration,
        "account_purge": account_purge,
    }


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    summary = run_maintenance()
    print(json.dumps(summary, default=str, sort_keys=True))
    return 0 if summary.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
