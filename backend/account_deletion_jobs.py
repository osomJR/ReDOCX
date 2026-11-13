from __future__ import annotations

"""
Scheduled account deletion jobs.

Run this from your production scheduler after applying
009_create_account_lifecycle.sql, 019_billing_reliability_hardening.sql,
022_account_deletion_privacy_hardening.sql, and
029_paystack_account_consistency.sql, for example once per hour. It
repairs privacy state for legacy purged accounts, resumes interrupted
deactivation sagas, and permanently deletes accounts whose recovery window has
elapsed. Durable database leases prevent overlapping purge workers from deleting
the same account concurrently.
"""

import argparse
import json
import os
import socket
import sys
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.routes.account import (
    _reconcile_paystack_deactivation_checkouts,
    cancel_external_subscription_for_account_deletion,
    delete_auth0_user,
    delete_local_account_data,
    resume_pending_account_deactivation,
)
from backend.account_lifecycle import (
    account_operation,
    claim_purge_due_user_ids,
    get_account_lifecycle,
    lock_purge_claim,
    normalize_optional_datetime,
    list_deactivation_requested_user_ids,
    list_legacy_purged_user_ids,
    mark_account_purged,
    release_account_purge_claim,
)
from backend.database import get_db


def _stop_unconfirmed_paystack_renewals(conn, user_id: str, metadata: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT provider_subscription_id, provider_customer_id, plan, current_period_end
            FROM user_subscriptions WHERE user_id=%s AND provider='paystack'
            UNION
            SELECT s.provider_subscription_id, s.provider_customer_id, s.plan, s.current_period_end
            FROM organization_subscriptions s JOIN organizations o ON o.id=s.organization_id
            WHERE o.owner_user_id=%s AND s.provider='paystack'
            """,
            (user_id, user_id),
        )
        candidates = cur.fetchall()
    conn.commit()
    requested_at = normalize_optional_datetime(metadata.get("deactivation_requested_at"))
    for subscription_id, customer_id, plan, period_end in candidates:
        if subscription_id or customer_id:
            cancel_external_subscription_for_account_deletion({
                "provider": "paystack", "plan": plan, "current_period_end": period_end,
                "provider_subscription_id": subscription_id, "provider_customer_id": customer_id,
            }, requested_at=requested_at)


def build_worker_id() -> str:
    configured_label = os.getenv("ACCOUNT_DELETION_WORKER_ID", "").strip()
    worker_label = configured_label or socket.gethostname()
    unique_suffix = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
    return f"{worker_label[:100]}:{unique_suffix}"[:200]


def repair_legacy_purged_accounts(*, limit: int = 100) -> dict[str, Any]:
    """Finish privacy cleanup for accounts purged before migration 022."""

    repaired: list[str] = []
    failed: list[dict[str, str]] = []

    with get_db() as conn:
        user_ids = list_legacy_purged_user_ids(conn, limit=limit)

    for user_id in user_ids:
        try:
            with account_operation(user_id), get_db() as conn:
                lifecycle = get_account_lifecycle(conn, user_id)
                if not lifecycle or lifecycle["status"] != "purged" or lifecycle["user_id"] != user_id:
                    continue
                email = (lifecycle.get("metadata") or {}).get("email")
                delete_local_account_data(conn, user_id=user_id, email=email)
                from backend.routes.route_v1 import purge_account_vault
                purge_account_vault(user_id)
                mark_account_purged(conn, user_id, worker_id=None)
                delete_auth0_user(user_id)
            repaired.append(user_id)
        except Exception as exc:  # noqa: BLE001 - isolate each legacy repair.
            failed.append({"user_id": user_id, "error": str(exc)})

    return {
        "success": not failed,
        "legacy_count": len(user_ids),
        "repaired_count": len(repaired),
        "failed_count": len(failed),
        "repaired_user_ids": repaired,
        "failed": failed,
    }


def complete_pending_deactivations(*, limit: int = 100) -> dict[str, Any]:
    completed: list[str] = []
    deferred: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    with get_db() as conn:
        user_ids = list_deactivation_requested_user_ids(conn, limit=limit)

    for user_id in user_ids:
        try:
            with get_db() as conn:
                result = resume_pending_account_deactivation(
                    conn,
                    user_id=user_id,
                )
            if result.get("deactivated"):
                completed.append(user_id)
            else:
                failed.append(
                    {
                        "user_id": user_id,
                        "error": "Account deactivation did not reach a deactivated state.",
                    }
                )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            error_code = str(detail.get("error") or "").strip()
            if error_code in {"account_deactivation_pending_retry", "account_operation_pending"}:
                # Provider availability/legacy identity resolution is an expected
                # durable-saga retry state, not a worker crash. The account remains
                # access-restricted in deactivation_requested and no permanent
                # deletion occurs until provider safety is established.
                deferred.append(
                    {
                        "user_id": user_id,
                        "error": str(detail.get("message") or error_code),
                    }
                )
            else:
                failed.append({"user_id": user_id, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - isolate each durable saga.
            failed.append({"user_id": user_id, "error": str(exc)})

    return {
        "success": not failed,
        "requested_count": len(user_ids),
        "completed_count": len(completed),
        "deferred_count": len(deferred),
        "failed_count": len(failed),
        "completed_user_ids": completed,
        "deferred": deferred,
        "failed": failed,
    }


def purge_due_accounts(
    *,
    limit: int = 100,
    lease_seconds: int = 1800,
    worker_id: str | None = None,
) -> dict[str, Any]:
    purged: list[str] = []
    failed: list[dict[str, str]] = []
    resolved_worker_id = worker_id or build_worker_id()

    with get_db() as conn:
        user_ids = claim_purge_due_user_ids(
            conn,
            worker_id=resolved_worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    for user_id in user_ids:
        try:
            with account_operation(user_id), get_db() as conn:
                metadata = lock_purge_claim(conn, user_id, resolved_worker_id)
                if metadata is None:
                    continue
                _stop_unconfirmed_paystack_renewals(conn, user_id, metadata)
                _reconcile_paystack_deactivation_checkouts(conn, user_id)
                from backend.billing_collection import resolve_paystack_deletion_payments
                resolve_paystack_deletion_payments(user_id)
                metadata = lock_purge_claim(conn, user_id, resolved_worker_id)
                if metadata is None:
                    continue
                delete_local_account_data(conn, user_id=user_id, email=metadata.get("email"))
                from backend.routes.route_v1 import purge_account_vault
                purge_account_vault(user_id)
                mark_account_purged(
                    conn,
                    user_id,
                    worker_id=resolved_worker_id,
                )
                delete_auth0_user(user_id)
            purged.append(user_id)
        except Exception as exc:  # noqa: BLE001 - job should continue purging other accounts.
            error = str(exc)
            failed.append({"user_id": user_id, "error": error})
            try:
                with get_db() as conn:
                    release_account_purge_claim(
                        conn,
                        user_id=user_id,
                        worker_id=resolved_worker_id,
                        error=error,
                    )
            except Exception as release_exc:  # noqa: BLE001
                failed[-1]["claim_release_error"] = str(release_exc)

    return {
        "success": not failed,
        "claimed_count": len(user_ids),
        "purged_count": len(purged),
        "failed_count": len(failed),
        "purged_user_ids": purged,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge due ReDOCX accounts.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=int(os.getenv("ACCOUNT_DELETION_PURGE_LEASE_SECONDS", "1800")),
    )
    args = parser.parse_args()
    legacy_repair = repair_legacy_purged_accounts(limit=args.limit)
    deactivation = complete_pending_deactivations(limit=args.limit)
    purge = purge_due_accounts(
        limit=args.limit,
        lease_seconds=args.lease_seconds,
    )
    result = {
        "success": (
            bool(legacy_repair.get("success"))
            and bool(deactivation.get("success"))
            and bool(purge.get("success"))
        ),
        "legacy_purge_repair": legacy_repair,
        "deactivation": deactivation,
        "purge": purge,
    }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
