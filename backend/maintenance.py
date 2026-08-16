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
    cancel_provider_subscription,
    is_redocx_paystack_transaction_reference,
    resolve_paystack_subscription_reference,
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
                       us.user_id::text AS owner_id,
                       us.plan,
                       us.status,
                       us.provider,
                       us.provider_customer_id,
                       us.provider_subscription_id,
                       us.current_period_end,
                       us.grace_period_end,
                       us.access_revoked_at,
                       us.pending_plan,
                       us.plan_change_effective_at,
                       us.last_reconciled_at,
                       us.cancel_at_period_end,
                       (us.user_id LIKE 'deleted:%%') AS owner_deleted
                FROM user_subscriptions us
                WHERE us.provider IN ('stripe', 'paystack')
                  AND us.provider_subscription_id IS NOT NULL
                UNION ALL
                SELECT 'organization_subscriptions' AS table_name,
                       os.organization_id::text AS owner_id,
                       os.plan,
                       os.status,
                       os.provider,
                       os.provider_customer_id,
                       os.provider_subscription_id,
                       os.current_period_end,
                       os.grace_period_end,
                       os.access_revoked_at,
                       os.pending_plan,
                       os.plan_change_effective_at,
                       os.last_reconciled_at,
                       os.cancel_at_period_end,
                       (o.owner_user_id LIKE 'deleted:%%') AS owner_deleted
                FROM organization_subscriptions os
                JOIN organizations o ON o.id = os.organization_id
                WHERE os.provider IN ('stripe', 'paystack')
                  AND os.provider_subscription_id IS NOT NULL
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
                        "provider_customer_id": row[5],
                        "provider_subscription_id": row[6],
                        "current_period_end": row[7],
                        "grace_period_end": row[8],
                        "access_revoked_at": row[9],
                        "pending_plan": row[10],
                        "plan_change_effective_at": row[11],
                        "last_reconciled_at": row[12],
                        "cancel_at_period_end": bool(row[13]),
                        "owner_deleted": bool(row[14]),
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



def _subscription_owner_column(row: dict[str, Any]) -> tuple[SubscriptionTable, str, str | int]:
    table: SubscriptionTable = row["table"]
    owner_column = "user_id" if table == "user_subscriptions" else "organization_id"
    owner_value: str | int = row["owner_id"]
    if table == "organization_subscriptions":
        owner_value = int(owner_value)
    return table, owner_column, owner_value


def _paystack_legacy_reference_context(row: dict[str, Any]) -> dict[str, Any]:
    stored_id = str(row.get("provider_subscription_id") or "").strip()
    context = {
        "provider_reference": stored_id if is_redocx_paystack_transaction_reference(stored_id) else None,
        "provider_customer_id": row.get("provider_customer_id"),
        "target_plan": row.get("plan"),
    }
    if not context["provider_reference"]:
        return context

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider_reference, provider_customer_id, target_plan
                FROM billing_checkout_sessions
                WHERE provider = 'paystack'
                  AND (
                        provider_reference = %s
                     OR provider_subscription_id = %s
                     OR replaced_provider_subscription_id = %s
                  )
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (stored_id, stored_id, stored_id),
            )
            ledger = cur.fetchone()
    if ledger is not None:
        context["provider_reference"] = ledger[0] or context["provider_reference"]
        context["provider_customer_id"] = (
            context["provider_customer_id"] or ledger[1]
        )
        context["target_plan"] = context["target_plan"] or ledger[2]
    return context


def _repair_subscription_reference(
    row: dict[str, Any],
    *,
    provider_subscription_id: str,
    provider_customer_id: str | None,
) -> dict[str, Any]:
    table, owner_column, owner_value = _subscription_owner_column(row)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET provider_subscription_id = %s,
                    provider_customer_id = COALESCE(%s, provider_customer_id),
                    reconciliation_error = NULL,
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (provider_subscription_id, provider_customer_id, owner_value),
            )
    repaired = dict(row)
    repaired["provider_subscription_id"] = provider_subscription_id
    repaired["provider_customer_id"] = provider_customer_id or row.get("provider_customer_id")
    return repaired


def _retire_deleted_owner_subscription(row: dict[str, Any]) -> None:
    table, owner_column, owner_value = _subscription_owner_column(row)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = 'inactive',
                    provider_customer_id = NULL,
                    provider_subscription_id = NULL,
                    current_period_start = NULL,
                    current_period_end = NULL,
                    cancel_at_period_end = TRUE,
                    pending_plan = NULL,
                    plan_change_effective_at = NULL,
                    grace_period_end = NULL,
                    reconciliation_error = NULL,
                    last_reconciled_at = NOW(),
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (owner_value,),
            )


def _mark_verified_non_recurring_paystack(row: dict[str, Any]) -> None:
    table, owner_column, owner_value = _subscription_owner_column(row)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET provider_subscription_id = NULL,
                    cancel_at_period_end = TRUE,
                    reconciliation_error = NULL,
                    last_reconciled_at = NOW(),
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (owner_value,),
            )


def _prepare_paystack_reconciliation_row(
    row: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    stored_id = str(row.get("provider_subscription_id") or "").strip()
    if row.get("provider") != "paystack" or not is_redocx_paystack_transaction_reference(stored_id):
        return "unchanged", row

    context = _paystack_legacy_reference_context(row)
    resolution = resolve_paystack_subscription_reference(
        provider_subscription_id=stored_id,
        provider_customer_id=context.get("provider_customer_id"),
        provider_reference=context.get("provider_reference"),
        expected_plan=context.get("target_plan"),
    )

    if resolution.outcome == "not_recurring":
        if row.get("owner_deleted"):
            _retire_deleted_owner_subscription(row)
            return "retired", None
        _mark_verified_non_recurring_paystack(row)
        return "non_recurring", None

    state = resolution.state
    if state is None:
        raise BillingProviderError(
            "Paystack reconciliation resolved no usable subscription state."
        )

    repaired = _repair_subscription_reference(
        row,
        provider_subscription_id=state.provider_subscription_id,
        provider_customer_id=state.provider_customer_id or resolution.provider_customer_id,
    )

    if row.get("owner_deleted"):
        # A deleted owner must never retain a live provider renewal. The provider
        # operation is idempotent: already non-renewing/terminal subscriptions
        # are treated as successfully converged by the Paystack adapter.
        cancel_provider_subscription("paystack", state.provider_subscription_id)
        _retire_deleted_owner_subscription(repaired)
        return "retired", None

    return "repaired", repaired

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
    summary = {
        "checked": 0,
        "updated": 0,
        "repaired": 0,
        "non_recurring": 0,
        "retired": 0,
        "failed": 0,
    }
    for original_row in _load_reconciliation_rows():
        summary["checked"] += 1
        row = original_row
        try:
            preparation, prepared_row = _prepare_paystack_reconciliation_row(row)
            if preparation == "repaired":
                summary["repaired"] += 1
                row = prepared_row or row
            elif preparation == "non_recurring":
                summary["non_recurring"] += 1
                logger.info(
                    "Verified legacy Paystack transaction is non-recurring owner=%s table=%s",
                    row["owner_id"],
                    row["table"],
                )
                continue
            elif preparation == "retired":
                summary["retired"] += 1
                logger.info(
                    "Retired deleted-owner billing binding provider=paystack owner=%s table=%s",
                    row["owner_id"],
                    row["table"],
                )
                continue

            state = retrieve_provider_subscription(
                row["provider"],
                row["provider_subscription_id"],
            )

            if row.get("owner_deleted"):
                if row["provider"] == "paystack":
                    cancel_provider_subscription(
                        row["provider"],
                        state.provider_subscription_id,
                    )
                _retire_deleted_owner_subscription(row)
                summary["retired"] += 1
                continue

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
        except Exception:  # defensive isolation per subscription
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
