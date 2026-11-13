"""Durable billing collection and seat purchases.

Organization renewals use active membership snapshots. Before this collector
owns a renewal, a durable preparing row is committed and the native recurring
schedule is stopped and verified. Uncertain payments retain the SAME reference.
Personal Stripe remains provider-managed; failed Personal Paystack invoices use
the recovery queue without changing their normal recurring schedule.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import hashlib
import os
from uuid import uuid4

from fastapi import HTTPException
from psycopg import connect
from psycopg.rows import dict_row

from backend.database import get_db
from backend.account_lifecycle import ACTIVE_STATUS, account_operation, get_account_lifecycle, lock_account_lifecycle
from backend.billing_policy import (
    next_month,
    prorated_amount,
    retry_at,
    retryable_decline,
    seat_count,
    utc,
)
from backend.billing_provider import (
    BillingProviderError,
    PaystackBillingProvider,
    BillingSubscriptionChange,
    BillingSubscriptionState,
    plan_unit_amount_kobo,
    parse_timestamp,
    paystack_checkout_is_closed,
    paystack_safe_timeout,
)
from backend import billing_collection_provider as provider_io

logger = logging.getLogger(__name__)
OPEN = ("open", "pending", "review")


def enabled() -> bool:
    return os.getenv("BILLING_ACTIVE_SEAT_COLLECTION_ENABLED", "false").lower() in {
        "true",
        "1",
        "yes",
    }


def now() -> datetime:
    return datetime.now(timezone.utc)


def schema_ready(conn=None) -> bool:
    if conn is None:
        with get_db() as connection:
            return schema_ready(connection)
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.billing_collection_jobs') IS NOT NULL")
        return bool(cur.fetchone()[0])


def _query(sql: str, params=(), *, one=False, conn=None):
    if conn is None:
        with get_db() as connection:
            return _query(sql, params, one=one, conn=connection)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return None
        return cur.fetchone() if one else cur.fetchall()


def lock_membership(conn, organization_id: int) -> None:
    """Same transaction lock used by membership writers and collection snapshots."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"billing-org:{organization_id}",),
        )


@contextmanager
def _lock_connection():
    # Use a dedicated connection so concurrent lock holders cannot exhaust the
    # application pool while waiting for a second connection to persist a job.
    # Transaction-scoped locks also work through transaction-mode poolers.
    with connect(os.environ["DATABASE_URL"], connect_timeout=5) as conn:
        yield conn


@contextmanager
def collection_lock(
    organization_id: int | None, provider: str = "", subscription_id: str = ""
):
    key = (
        f"billing-org:{organization_id}"
        if organization_id
        else f"billing-sub:{provider}:{subscription_id}"
    )
    with _lock_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
            )
            if not cur.fetchone()[0]:
                raise HTTPException(
                    409,
                    detail="A billing operation is already in progress. Check its status shortly.",
                )
        yield


def _account(provider: str, subscription_id: str):
    if not schema_ready():
        return None
    return _query(
        "SELECT * FROM billing_collection_accounts WHERE provider=%s AND provider_subscription_id=%s",
        (provider, subscription_id),
        one=True,
    )


def payment_identity(provider: str, subscription_id: str):
    identity = provider_io.payer(provider, subscription_id)
    account = _account(provider, subscription_id)
    if account and account.get("authorization_payment_reference"):
        identity = provider_io.payment_authorization(
            provider, account["authorization_payment_reference"], identity
        )
    return identity


def _stop_and_ready(account: dict):
    provider_io.stop_native(account["provider"], account["provider_subscription_id"])
    native = provider_io.native_subscription(
        account["provider"], account["provider_subscription_id"]
    )
    if not native.cancel_at_period_end and str(native.status).lower() not in {
        "cancelled",
        "canceled",
        "completed",
    }:
        raise BillingProviderError(
            "The native renewal schedule has not confirmed cancellation."
        )
    return _query(
        """UPDATE billing_collection_accounts SET state='ready', native_stopped_at=NOW(),
        last_error=NULL, updated_at=NOW() WHERE id=%s RETURNING *""",
        (account["id"],),
        one=True,
    )


def managed_state(
    provider: str, subscription_id: str
) -> BillingSubscriptionState | None:
    account = _account(provider, subscription_id)
    if not account:
        return None
    subscription = _query(
        "SELECT status, cancel_at_period_end FROM organization_subscriptions WHERE organization_id=%s",
        (account["organization_id"],),
        one=True,
    )
    status = (subscription or {}).get("status", "inactive")
    return BillingSubscriptionState(
        provider=provider,
        provider_subscription_id=subscription_id,
        provider_customer_id=account["provider_customer_id"],
        status=status,
        current_period_start=account["period_start"],
        current_period_end=account["period_end"],
        cancel_at_period_end=not account["renewal_enabled"],
        plan=account["plan"],
        raw={"redocx_collection": True, "collection_state": account["state"]},
    )


def managed_change(
    provider: str, subscription_id: str, *, resume: bool
) -> BillingSubscriptionChange | None:
    account = _account(provider, subscription_id)
    if not account:
        return None
    with collection_lock(account["organization_id"]):
        account = _account(provider, subscription_id)
        if resume:
            successor = _query(
                "SELECT id FROM billing_collection_accounts WHERE organization_id=%s AND id<>%s AND handoff_id IS NOT NULL AND renewal_enabled",
                (account["organization_id"], account["id"]),
                one=True,
            )
            if successor or utc(account["period_end"]) <= now():
                raise BillingProviderError(
                    "This payer cannot be resumed after handoff or paid-period expiry."
                )
        # Cancellation remains durable even if a native stop still needs recovery.
        _query(
            "UPDATE billing_collection_accounts SET renewal_enabled=%s, updated_at=NOW() WHERE id=%s",
            (resume, account["id"]),
        )
        if account["state"] == "preparing":
            account = _stop_and_ready(account)
        if not resume:
            # A future successor payer belongs to the same organization. A
            # customer cancellation must stop that schedule as well.
            _query(
                "UPDATE billing_collection_accounts SET renewal_enabled=FALSE, updated_at=NOW() WHERE organization_id=%s AND handoff_id IS NOT NULL",
                (account["organization_id"],),
            )
            _query(
                "UPDATE billing_collection_jobs SET status='cancelled', updated_at=NOW() WHERE account_id=%s AND status='open' AND attempts=0",
                (account["id"],),
            )
    return BillingSubscriptionChange(
        provider=provider,
        provider_subscription_id=subscription_id,
        status="active" if resume else "cancellation_scheduled",
        current_period_end=account["period_end"],
    )


def _organization(organization_id: int):
    return _query(
        """SELECT os.*, o.owner_user_id, o.name AS organization_name,
        (SELECT COUNT(*) FROM organization_members om WHERE om.organization_id=os.organization_id AND om.status='active') AS active_members
        FROM organization_subscriptions os JOIN organizations o ON o.id=os.organization_id
        WHERE os.organization_id=%s""",
        (organization_id,),
        one=True,
    )


def prepare_account(organization_id: int, *, handoff: dict | None = None):
    if not enabled() or not schema_ready():
        raise BillingProviderError(
            "Active-seat collection requires migration 027 and the configured collection worker."
        )
    with collection_lock(organization_id):
        subscription = _organization(organization_id)
        if not subscription:
            raise BillingProviderError("Organization subscription is unavailable.")
        provider = (handoff or subscription)["provider"]
        subscription_id = (
            handoff["new_provider_subscription_id"]
            if handoff
            else subscription["provider_subscription_id"]
        )
        account = _account(provider, subscription_id)
        if account and account["state"] == "ready":
            return account
        native = provider_io.native_subscription(provider, subscription_id)
        customer = native.provider_customer_id
        period_end = (
            utc(handoff["effective_at"])
            if handoff
            else parse_timestamp(subscription.get("current_period_end"))
        )
        period_start = (
            parse_timestamp(subscription.get("current_period_start"))
            or native.current_period_start
        )
        invoice = native.raw.get("most_recent_invoice") or {}
        invoice_start = parse_timestamp(invoice.get("period_start"))
        if invoice_start and period_end and invoice_start < period_end:
            period_start = invoice_start
        if (
            not customer
            or not period_start
            or not period_end
            or not timedelta(0) < period_end - period_start <= timedelta(days=32)
        ):
            raise BillingProviderError(
                "Verified customer and current billing-period boundaries are required."
            )
        if not account:
            if period_end <= now() + timedelta(minutes=10):
                raise BillingProviderError(
                    "Renewal is already near or underway. Reconcile it before enabling active-seat collection."
                )
            if not handoff and (
                subscription["status"] != "active"
                or subscription["cancel_at_period_end"]
                or subscription["access_revoked_at"]
            ):
                raise BillingProviderError(
                    "Only an active, undisputed, renewing subscription can enter active-seat collection."
                )
            if provider == "stripe" and (
                native.raw.get("discount")
                or native.raw.get("discounts")
                or (native.raw.get("automatic_tax") or {}).get("enabled")
            ):
                raise BillingProviderError(
                    "This subscription has provider discounts or tax rules; reconcile its pricing before active-seat collection."
                )
            account = _query(
                """INSERT INTO billing_collection_accounts
                (provider, provider_subscription_id, provider_customer_id, organization_id,
                 payer_user_id, plan, period_start, period_end, handoff_id, billing_day)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (provider,provider_subscription_id) DO UPDATE SET updated_at=NOW() RETURNING *""",
                (
                    provider,
                    subscription_id,
                    customer,
                    organization_id,
                    (
                        handoff["new_owner_user_id"]
                        if handoff
                        else subscription["owner_user_id"]
                    ),
                    subscription["plan"],
                    period_start,
                    period_end,
                    handoff["id"] if handoff else None,
                    (
                        parse_timestamp(native.raw.get("billing_cycle_anchor"))
                        or period_end
                    ).day,
                ),
                one=True,
            )
        # The preparing row was COMMITTED before the provider mutation. A crash
        # anywhere below resumes this phase and cannot create a second schedule.
        return _stop_and_ready(account)


def _active_payer(user_id: str) -> bool:
    with get_db() as conn:
        lifecycle = get_account_lifecycle(conn, user_id)
    return lifecycle is None or lifecycle["status"] == ACTIVE_STATUS


def _allowed(job: dict) -> bool:
    if not _active_payer(job["payer_user_id"]):
        return False
    if job["organization_id"]:
        subscription = _organization(job["organization_id"])
        if (
            not subscription
            or subscription["access_revoked_at"]
            or subscription["owner_user_id"] != job["payer_user_id"]
        ):
            return False
        account = _query(
            "SELECT * FROM billing_collection_accounts WHERE id=%s",
            (job["account_id"],),
            one=True,
        )
        if (
            not account
            or account["state"] != "ready"
            or not account["native_stopped_at"]
        ):
            return False
        if job["purpose"] == "seats":
            return bool(
                subscription["status"] == "active"
                and utc(subscription["current_period_end"]) == utc(job["period_end"])
            )
        if not account["renewal_enabled"]:
            return False
        if account["handoff_id"]:
            handoff = _query(
                "SELECT status FROM organization_billing_handoffs WHERE id=%s",
                (account["handoff_id"],),
                one=True,
            )
            return bool(handoff and handoff["status"] in {"scheduled", "activated"})
        return (
            subscription["provider_subscription_id"] == job["provider_subscription_id"]
            and not subscription["cancel_at_period_end"]
        )
    subscription = _query(
        "SELECT * FROM user_subscriptions WHERE user_id=%s",
        (job["payer_user_id"],),
        one=True,
    )
    return bool(
        subscription
        and subscription["provider_subscription_id"] == job["provider_subscription_id"]
        and not subscription["cancel_at_period_end"]
        and not subscription["access_revoked_at"]
    )


def _set_job(
    job_id: int, status: str, message: str = "", next_at: datetime | None = None
):
    _query(
        "UPDATE billing_collection_jobs SET status=%s, last_error=%s, next_attempt_at=COALESCE(%s,next_attempt_at), updated_at=NOW() WHERE id=%s",
        (status, message[:500] or None, next_at, job_id),
    )


def require_no_pending_collection(organization_id: int):
    if schema_ready():
        job = _query(
            "SELECT id FROM billing_collection_jobs WHERE organization_id=%s AND status IN ('open','pending','review') LIMIT 1",
            (organization_id,),
            one=True,
        )
        if job:
            raise HTTPException(
                409,
                detail={
                    "error": "billing_operation_pending",
                    "message": "Finish the pending payment before transferring its payer.",
                },
            )


def _settle(
    job: dict, attempt: dict, result: provider_io.PaymentResult, identity: dict
):
    if (
        result.amount != job["amount"]
        or result.currency != job["currency"]
        or result.customer != identity["customer"]
        or result.reference != attempt["reference"]
    ):
        _set_job(
            job["id"],
            "review",
            "Verified payment does not match the immutable amount, currency, customer and reference.",
        )
        return
    replacement_can_renew = True
    if job["provider"] == "paystack" and job["purpose"] == "renewal" and attempt["kind"] == "checkout":
        replacement_can_renew = bool(provider_io.payment_authorization(
            "paystack", attempt["reference"], identity,
        ).get("authorization"))
    with get_db() as conn:
        locked = _query(
            "SELECT * FROM billing_collection_jobs WHERE id=%s FOR UPDATE",
            (job["id"],),
            one=True,
            conn=conn,
        )
        if not locked or locked["status"] == "paid":
            return
        if job["purpose"] == "seats":
            subscription = _query(
                "SELECT * FROM organization_subscriptions WHERE organization_id=%s FOR UPDATE",
                (job["organization_id"],),
                one=True,
                conn=conn,
            )
            owner = _query(
                "SELECT owner_user_id FROM organizations WHERE id=%s",
                (job["organization_id"],),
                one=True,
                conn=conn,
            )
            if (
                not subscription
                or subscription["plan"] != job["plan"]
                or subscription["access_revoked_at"]
                or not owner
                or owner["owner_user_id"] != job["payer_user_id"]
                or subscription["provider_subscription_id"]
                != job["provider_subscription_id"]
                or utc(subscription["current_period_end"]) != utc(job["period_end"])
                or subscription["max_accounts"] != job["previous_quantity"]
                or now() >= utc(job["period_end"])
            ):
                _query(
                    "UPDATE billing_collection_jobs SET status='review', last_error='Paid seat checkout outlived its quote or subscription. Refund review required.', updated_at=NOW() WHERE id=%s",
                    (job["id"],),
                    conn=conn,
                )
                return
            _query(
                "UPDATE organization_subscriptions SET max_accounts=%s, updated_at=NOW() WHERE organization_id=%s",
                (job["quantity"], job["organization_id"]),
                conn=conn,
            )
        elif job["organization_id"]:
            # All membership mutations share the organization advisory lock.
            # Never replace the current provider binding after an unrelated payer change.
            account = _query(
                "SELECT * FROM billing_collection_accounts WHERE id=%s FOR UPDATE",
                (job["account_id"],),
                one=True,
                conn=conn,
            )
            subscription = _query(
                "SELECT * FROM organization_subscriptions WHERE organization_id=%s FOR UPDATE",
                (job["organization_id"],),
                one=True,
                conn=conn,
            )
            owner = _query(
                "SELECT owner_user_id FROM organizations WHERE id=%s",
                (job["organization_id"],),
                one=True,
                conn=conn,
            )
            binding_valid = bool(
                subscription
                and (
                    subscription["provider_subscription_id"]
                    == job["provider_subscription_id"]
                    or (account and account["handoff_id"])
                )
            )
            if (
                not account
                or not subscription
                or subscription["access_revoked_at"]
                or not binding_valid
                or not owner
                or owner["owner_user_id"] != job["payer_user_id"]
                or subscription["plan"] != job["plan"]
                or utc(subscription["current_period_end"]) > utc(job["period_end"])
            ):
                _query(
                    "UPDATE billing_collection_jobs SET status='review', last_error='Payment succeeded after access was revoked; settlement review required.' WHERE id=%s",
                    (job["id"],),
                    conn=conn,
                )
                return
            _query(
                """UPDATE organization_subscriptions SET plan=%s, max_accounts=%s,
                status=CASE WHEN cancel_at_period_end THEN 'cancelled' ELSE 'active' END,
                provider=%s, provider_subscription_id=%s, provider_customer_id=%s,
                current_period_start=%s, current_period_end=%s, grace_period_end=NULL,
                payment_failure_count=0, updated_at=NOW() WHERE organization_id=%s""",
                (
                    job["plan"],
                    job["quantity"],
                    job["provider"],
                    job["provider_subscription_id"],
                    identity["customer"],
                    job["period_start"],
                    job["period_end"],
                    job["organization_id"],
                ),
                conn=conn,
            )
            _query(
                "UPDATE billing_collection_accounts SET period_start=%s, period_end=%s, updated_at=NOW() WHERE id=%s",
                (job["period_start"], job["period_end"], job["account_id"]),
                conn=conn,
            )
            if attempt["kind"] == "checkout":
                _query(
                    "UPDATE billing_collection_accounts SET authorization_payment_reference=%s WHERE id=%s",
                    (
                        (
                            result.provider_id
                            if job["provider"] == "stripe"
                            else attempt["reference"]
                        ),
                        job["account_id"],
                    ),
                    conn=conn,
                )
                if not replacement_can_renew:
                    # Keep the verified paid period. Future collection will
                    # request a new method instead of using an old authorization.
                    _query("UPDATE billing_collection_accounts SET last_error=%s WHERE id=%s",
                           ("The replacement card cannot be reused for renewal.", job["account_id"]), conn=conn)
            if account["handoff_id"]:
                _query(
                    "UPDATE organization_billing_handoffs SET status='activated', activated_at=NOW(), last_error=NULL WHERE id=%s",
                    (account["handoff_id"],),
                    conn=conn,
                )
                _query(
                    "UPDATE organization_subscriptions SET cancel_at_period_end=%s,status=%s WHERE organization_id=%s",
                    (
                        not account["renewal_enabled"],
                        "active" if account["renewal_enabled"] else "cancelled",
                        job["organization_id"],
                    ),
                    conn=conn,
                )
        else:
            applied = _query(
                """UPDATE user_subscriptions SET status=CASE WHEN cancel_at_period_end THEN 'cancelled' ELSE 'active' END,
                current_period_start=%s,current_period_end=%s,grace_period_end=NULL,payment_failure_count=0,updated_at=NOW()
                WHERE user_id=%s AND provider=%s AND provider_subscription_id=%s AND plan=%s
                AND access_revoked_at IS NULL AND COALESCE(current_period_end,%s)<=%s RETURNING user_id""",
                (
                    job["period_start"],
                    job["period_end"],
                    job["payer_user_id"],
                    job["provider"],
                    job["provider_subscription_id"],
                    job["plan"],
                    job["period_end"],
                    job["period_end"],
                ),
                conn=conn,
                one=True,
            )
            if not applied:
                _query(
                    "UPDATE billing_collection_jobs SET status='review',last_error='Verified recovery no longer matches the current subscription.' WHERE id=%s",
                    (job["id"],),
                    conn=conn,
                )
                return
        _query(
            "UPDATE billing_collection_jobs SET status='paid', paid_at=NOW(),last_error=%s,updated_at=NOW() WHERE id=%s",
            (None if replacement_can_renew else "Payment verified. A new payment method may be needed at renewal.", job["id"]),
            conn=conn,
        )
        _query(
            "UPDATE billing_collection_attempts SET status='success',provider_id=COALESCE(%s,provider_id),updated_at=NOW() WHERE id=%s",
            (result.provider_id, attempt["id"]),
            conn=conn,
        )


def process_job(job_id: int, *, verify_only: bool = False):
    job = _query("SELECT payer_user_id FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True)
    if not job:
        return
    try:
        with account_operation(job["payer_user_id"]):
            _process_job(job_id, verify_only=verify_only)
    except HTTPException as exc:
        if not isinstance(exc.detail, dict) or exc.detail.get("error") != "account_operation_pending":
            raise


def resolve_paystack_deletion_payments(user_id: str) -> None:
    """Drain already-submitted payments for a restricted payer before purge."""
    if not schema_ready():
        return
    attempts = _query(
        """
        SELECT a.*, j.id AS collection_job_id, j.amount AS job_amount, j.currency AS job_currency
        FROM billing_collection_attempts a
        JOIN billing_collection_jobs j ON j.id = a.job_id
        WHERE j.payer_user_id = %s AND j.provider = 'paystack'
          AND a.submitted_at IS NOT NULL AND a.status <> 'success' AND a.paystack_resolved_at IS NULL
        ORDER BY a.id
        """,
        (user_id,),
    )
    if not attempts:
        return
    paystack = PaystackBillingProvider()
    waiting = False
    for attempt in attempts:
        result = provider_io.lookup("paystack", attempt)
        if result.status == "success":
            payment = paystack.verify_transaction(attempt["reference"])
            if payment.amount != attempt["job_amount"] or payment.currency != attempt["job_currency"]:
                raise BillingProviderError("The collection payment does not match its saved invoice.")
            try:
                paystack.refund_unfulfilled_checkout(payment)
            except BillingProviderError:
                waiting = True
                continue
        elif attempt["kind"] == "checkout":
            if result.status not in {"review", "expired"}:  # Still awaiting reversal or expiry.
                timeout = paystack.payment_session_timeout()
                if result.status not in {"not_found", "failed", "abandoned"} or not paystack_checkout_is_closed(
                    None, attempt["submitted_at"], paystack_safe_timeout(timeout, attempt["paystack_timeout_seconds"])
                ):
                    waiting = True
                    continue
        elif result.status == "not_found" and attempt["response_code"] != "payment_method_required":
            # A missing transaction can follow an interrupted charge request.
            # Keep the reference if Paystack never confirms what happened.
            waiting = True
            continue
        elif result.status not in {"not_found", "failed", "review", "expired"}:
            waiting = True
            continue
        with get_db() as conn:
            _query("UPDATE billing_collection_attempts SET status='failed',checkout_url=NULL,paystack_resolved_at=NOW() WHERE id=%s",
                   (attempt["id"],), conn=conn)
            _query("UPDATE billing_collection_jobs SET status='cancelled', updated_at=NOW() WHERE id=%s AND status<>'paid'",
                   (attempt["collection_job_id"],), conn=conn)
    if waiting:
        raise BillingProviderError("Paystack collection payments are still being resolved.")


def _process_job(job_id: int, *, verify_only: bool = False):
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
    )
    if not job:
        return
    with collection_lock(
        job["organization_id"], job["provider"], job["provider_subscription_id"]
    ):
        job = _query(
            "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
        )
        if job["status"] not in {"open", "pending", "requires_action", "review"}:
            return
        verify_only = verify_only or job["status"] in {"requires_action", "review"}
        attempt = _query(
            "SELECT * FROM billing_collection_attempts WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1",
            (job_id,),
            one=True,
        )
        try:
            if attempt and (attempt["status"] in {"prepared", "pending", "requires_action"}
                            or (verify_only and attempt["submitted_at"] and attempt["status"] == "failed")):
                result = provider_io.lookup(job["provider"], attempt)
            else:
                result = provider_io.PaymentResult("not_found")
            if result.status == "success":
                if job["provider"] == "paystack" and not _active_payer(job["payer_user_id"]):
                    _set_job(job_id, "review", "Payment completed after deactivation. Refund confirmation is pending.")
                    return
                _settle(job, attempt, result, payment_identity(job["provider"], job["provider_subscription_id"]))
                return
            if result.status == "review":
                _set_job(
                    job_id,
                    "review",
                    "Provider returned an ambiguous or reversed payment; review required.",
                )
                return
            if (job["provider"] == "paystack" and attempt and attempt["kind"] == "checkout"
                    and attempt["submitted_at"] and result.status in {"expired", "failed", "abandoned", "not_found"}):
                expired = result.status == "expired"
                if not expired:
                    timeout = PaystackBillingProvider().payment_session_timeout()
                    expired = paystack_checkout_is_closed(
                        None, attempt["submitted_at"], paystack_safe_timeout(timeout, attempt["paystack_timeout_seconds"]),
                    )
                if expired:
                    _query("UPDATE billing_collection_attempts SET status='failed', checkout_url=NULL, paystack_resolved_at=NOW(), updated_at=NOW() WHERE id=%s",
                           (attempt["id"],))
                    status = "requires_action" if job["purpose"] == "renewal" else "failed"
                    _set_job(job_id, status, "The previous checkout expired. Start a new payment only if it is still available.")
                elif not verify_only:
                    _set_job(job_id, "pending", "Check or complete the existing Paystack checkout.", now() + timedelta(minutes=5))
                return
            if verify_only:
                if job["status"] in {"requires_action", "review"}:
                    _query("UPDATE billing_collection_jobs SET next_attempt_at=%s WHERE id=%s",
                           (now() + timedelta(minutes=5), job_id))
                return
            # Continue VERIFYING a submitted payment even after cancellation or
            # a deadline. Never issue another debit in those circumstances.
            if not _allowed(job) or now() >= utc(job["deadline"]):
                _set_job(
                    job_id,
                    (
                        "review"
                        if attempt
                        and attempt["submitted_at"]
                        and result.status in {"pending", "not_found"}
                        else "cancelled"
                    ),
                    "Collection stopped: cancellation, account state, or collection deadline.",
                )
                return
            identity = payment_identity(job["provider"], job["provider_subscription_id"])
            if job["purpose"] == "recovery":
                invoice = identity["state"].raw.get("most_recent_invoice") or {}
                if (
                    identity["state"].cancel_at_period_end
                    or str(identity["state"].status).lower() != "attention"
                    or invoice.get("invoice_code") != job["invoice_code"]
                    or invoice.get("paid") in (True, 1)
                ):
                    _set_job(
                        job_id,
                        "cancelled",
                        "Native invoice was paid or replaced; no further recovery debit.",
                    )
                    return
            if attempt and result.status in {"failed", "expired", "requires_action"}:
                _query(
                    "UPDATE billing_collection_attempts SET status=%s,response_code=%s,updated_at=NOW() WHERE id=%s",
                    ("failed" if result.status == "expired" else result.status, result.code[:150], attempt["id"]),
                )
                if job["organization_id"] and job["purpose"] == "renewal":
                    _query(
                        "UPDATE organization_subscriptions SET payment_failure_count=GREATEST(payment_failure_count,%s) WHERE organization_id=%s",
                        (job["attempts"], job["organization_id"]),
                    )
                next_at = retry_at(
                    job["due_at"], job["attempts"], now(), job["deadline"]
                )
                if (
                    job["purpose"] == "seats"
                    or result.status == "requires_action"
                    or not retryable_decline(result.code, result.message)
                ):
                    _set_job(
                        job_id,
                        "requires_action",
                        "Update the payment method; automatic retries are paused.",
                    )
                elif next_at:
                    _set_job(
                        job_id,
                        "open",
                        "Temporary payment failure; next retry is scheduled.",
                        next_at,
                    )
                else:
                    _set_job(
                        job_id,
                        "failed",
                        "Automatic retry limit or collection deadline reached.",
                    )
                return
            if result.status in {"pending", "abandoned"}:
                if attempt and attempt["kind"] == "automatic" and result.status == "abandoned":
                    _set_job(job_id, "requires_action", "Verify the previous payment before continuing.")
                    return
                _set_job(
                    job_id,
                    "pending",
                    "Payment confirmation is pending.",
                    now() + timedelta(minutes=5),
                )
                return
            if (
                attempt
                and attempt["kind"] == "checkout"
                and attempt.get("checkout_url")
            ):
                _set_job(
                    job_id,
                    "pending",
                    "Complete the existing secure checkout.",
                    now() + timedelta(minutes=5),
                )
                return
            if now() < utc(job["next_attempt_at"]):
                return
            if (job["provider"] == "paystack" and attempt and attempt["submitted_at"]
                    and attempt["status"] != "failed"):
                _set_job(job_id, "pending", "Verifying the original Paystack payment reference.", now() + timedelta(minutes=5))
                return
            if (
                job["organization_id"]
                and not identity["state"].cancel_at_period_end
                and str(identity["state"].status).lower()
                not in {"cancelled", "canceled", "completed"}
            ):
                _query(
                    "UPDATE billing_collection_accounts SET state='blocked',last_error='Native renewal was re-enabled externally.' WHERE id=%s",
                    (job["account_id"],),
                )
                _set_job(
                    job_id,
                    "review",
                    "Native renewal is active again; collection paused to prevent duplicate billing.",
                )
                return
            if not attempt or attempt["status"] == "failed":
                if job["attempts"] >= 5:
                    _set_job(job_id, "failed", "Retry limit reached.")
                    return
                ordinal = job["attempts"] + 1
                with get_db() as conn:
                    attempt = _query(
                        """INSERT INTO billing_collection_attempts(job_id,ordinal,reference,kind)
                        VALUES (%s,%s,%s,%s) RETURNING *""",
                        (
                            job_id,
                            ordinal,
                            f"redocx-bill-{uuid4().hex}",
                            "checkout" if job["purpose"] == "seats" else "automatic",
                        ),
                        one=True,
                        conn=conn,
                    )
                    _query(
                        "UPDATE billing_collection_jobs SET attempts=%s,status='pending',updated_at=NOW() WHERE id=%s",
                        (ordinal, job_id),
                        conn=conn,
                    )
                    job["attempts"] = ordinal
            if (
                attempt["submitted_at"]
                and job["provider"] == "stripe"
                and now() - utc(attempt["submitted_at"]) >= timedelta(hours=23)
            ):
                _set_job(
                    job_id,
                    "review",
                    "Stripe response missing beyond the safe idempotency window; do not create another payment.",
                )
                return
            checkout_timeout = None
            if job["provider"] == "paystack" and attempt["kind"] == "checkout":
                checkout_timeout = PaystackBillingProvider().payment_session_timeout()
                if checkout_timeout <= 0:
                    raise BillingProviderError("Paystack checkout expiry is unavailable.")
            fingerprint = hashlib.sha256(
                str(identity.get("authorization") or "").encode()
            ).hexdigest()
            _query(
                """UPDATE billing_collection_attempts SET submitted_at=COALESCE(submitted_at,NOW()),
                    paystack_timeout_seconds=COALESCE(paystack_timeout_seconds,%s),
                    authorization_fingerprint=%s,status='pending',updated_at=NOW() WHERE id=%s""",
                (checkout_timeout, fingerprint, attempt["id"]),
            )
            if attempt["kind"] == "checkout":
                result = provider_io.seat_checkout(job, attempt, identity)
            else:
                result = provider_io.automatic_charge(job, attempt, identity)
            _query(
                "UPDATE billing_collection_attempts SET provider_id=COALESCE(%s,provider_id),checkout_url=COALESCE(%s,checkout_url),updated_at=NOW() WHERE id=%s",
                (result.provider_id, result.checkout_url, attempt["id"]),
            )
            # All successful charges are subsequently verified independently.
            if result.status == "requires_action":
                _query(
                    "UPDATE billing_collection_attempts SET status='requires_action',response_code=%s WHERE id=%s",
                    (result.code, attempt["id"]),
                )
                _set_job(
                    job_id,
                    "requires_action",
                    "The payment requires your attention. Check the current payment before continuing.",
                )
            else:
                _set_job(
                    job_id,
                    "pending",
                    "Awaiting verified payment.",
                    now() + timedelta(minutes=1),
                )
        except BillingProviderError:
            if verify_only:
                raise
            logger.warning(
                "Collection job %s has an unresolved provider outcome.", job_id
            )
            _set_job(
                job_id,
                "pending",
                "Provider confirmation is pending; the same payment reference will be verified.",
                now() + timedelta(minutes=5),
            )


def retry_after_method_update(job_id: int, payer_user_id: str, *, account_locked: bool = False):
    public_job(job_id, payer_user_id)
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
    )
    with collection_lock(
        job["organization_id"], job["provider"], job["provider_subscription_id"]
    ):
        job = _query(
            "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
        )
        if (
            job["status"] not in {"requires_action", "failed"}
            or job["attempts"] >= 5
            or not _allowed(job)
            or now() >= utc(job["deadline"])
        ):
            raise HTTPException(
                409,
                detail="This payment cannot be retried. Check its current state or start a new subscription after expiry.",
            )
        attempt = _query(
            "SELECT * FROM billing_collection_attempts WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1",
            (job_id,),
            one=True,
        )
        identity = payment_identity(job["provider"], job["provider_subscription_id"])
        fingerprint = hashlib.sha256(
            str(identity.get("authorization") or "").encode()
        ).hexdigest()
        if not identity.get("authorization") or fingerprint == (
            attempt["authorization_fingerprint"]
            if attempt
            else job["initial_authorization_fingerprint"]
        ):
            raise HTTPException(
                409,
                detail="Update the payment method before retrying this declined payment.",
            )
        if attempt is None:
            invoice = identity["state"].raw.get("most_recent_invoice") or {}
            if (
                job["purpose"] != "recovery"
                or job["attempts"] != 1
                or str(identity["state"].status).lower() != "attention"
                or invoice.get("invoice_code") != job["invoice_code"]
                or invoice.get("paid") in (True, 1)
            ):
                raise HTTPException(
                    409, detail="The failed native invoice could not be confirmed."
                )
            # The original native invoice is the first attempt. Paystack does
            # not retry it in this cycle; only the newly verified card is used.
            result = provider_io.PaymentResult("failed")
        else:
            result = provider_io.lookup(job["provider"], attempt)
        if result.status == "success":
            _settle(job, attempt, result, identity)
            return public_job(job_id, payer_user_id)
        if result.status not in {"failed", "not_found"}:
            raise HTTPException(
                409,
                detail="The previous payment is not definitively closed. Do not submit a second charge.",
            )
        if (
            result.status == "not_found"
            and (not attempt or attempt["response_code"] != "payment_method_required")
        ):
            raise HTTPException(
                409,
                detail="The previous payment outcome needs verification before retrying.",
            )
        if attempt:
            _query(
                "UPDATE billing_collection_attempts SET status='failed' WHERE id=%s",
                (attempt["id"],),
            )
        _set_job(
            job_id,
            "open",
            "The updated payment method will be used for the next attempt.",
            now(),
        )
    (_process_job if account_locked else process_job)(job_id)
    return public_job(job_id, payer_user_id)


def checkout_declined_renewal(job_id: int, payer_user_id: str, *, account_locked: bool = False):
    """Recover an existing organization invoice through an interactive checkout.

    This does not create a new subscription or change its billing anchor. The
    customer sees the same immutable active-seat invoice and authorizes its new
    payment method for future renewals.
    """
    public_job(job_id, payer_user_id)
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
    )
    with collection_lock(
        job["organization_id"], job["provider"], job["provider_subscription_id"]
    ):
        job = _query(
            "SELECT * FROM billing_collection_jobs WHERE id=%s", (job_id,), one=True
        )
        attempt = _query(
            "SELECT * FROM billing_collection_attempts WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1",
            (job_id,),
            one=True,
        )
        if (
            attempt
            and attempt["kind"] == "checkout"
            and job["status"] in {"open", "pending", "paid"}
        ):
            return public_job(job_id, payer_user_id)
        if (
            job["purpose"] != "renewal"
            or not job["organization_id"]
            or job["status"] != "requires_action"
            or not _allowed(job)
            or job["attempts"] >= 5
            or now() + timedelta(minutes=31) >= utc(job["deadline"])
        ):
            raise HTTPException(
                409,
                detail="This renewal cannot start another payment. Check its status before proceeding.",
            )
        if not attempt:
            raise HTTPException(409, detail="No previous payment attempt is available.")
        result = provider_io.close_unconfirmed_payment(job["provider"], attempt)
        if result.status == "success":
            _settle(job, attempt, result, payment_identity(job["provider"], job["provider_subscription_id"]))
            return public_job(job_id, payer_user_id)
        closed = result.status in {"failed", "expired"} or bool(attempt.get("paystack_resolved_at"))
        if job["provider"] == "paystack" and attempt["submitted_at"] and not attempt.get("paystack_resolved_at") and (
            result.status == "abandoned" or (attempt["kind"] == "checkout" and result.status == "failed")
        ):
            timeout = PaystackBillingProvider().payment_session_timeout()
            closed = paystack_checkout_is_closed(
                None, attempt["submitted_at"], paystack_safe_timeout(timeout, attempt["paystack_timeout_seconds"]),
            )
        if not closed and not (
            result.status == "not_found"
            and attempt["response_code"] == "payment_method_required"
        ):
            if job["provider"] == "paystack" and attempt.get("checkout_url") and result.status in {"pending", "requires_action", "abandoned"}:
                return public_job(job_id, payer_user_id)
            raise HTTPException(
                409,
                detail="The previous payment is not definitively closed. A second charge cannot be started.",
            )
        with get_db() as conn:
            _query(
                "UPDATE billing_collection_attempts SET status='failed' WHERE id=%s",
                (attempt["id"],),
                conn=conn,
            )
            _query(
                "INSERT INTO billing_collection_attempts(job_id,ordinal,reference,kind) VALUES (%s,%s,%s,'checkout')",
                (job_id, job["attempts"] + 1, f"redocx-bill-{uuid4().hex}"),
                conn=conn,
            )
            _query(
                "UPDATE billing_collection_jobs SET status='open',attempts=attempts+1,next_attempt_at=NOW(),updated_at=NOW() WHERE id=%s",
                (job_id,),
                conn=conn,
            )
    (_process_job if account_locked else process_job)(job_id)
    return public_job(job_id, payer_user_id)


def queue_renewal(account: dict):
    with collection_lock(account["organization_id"]):
        account = _query(
            "SELECT * FROM billing_collection_accounts WHERE id=%s",
            (account["id"],),
            one=True,
        )
        if (
            account["state"] != "ready"
            or not account["renewal_enabled"]
            or utc(account["period_end"]) > now()
        ):
            return
        subscription = _organization(account["organization_id"])
        if (
            not subscription
            or subscription["owner_user_id"] != account["payer_user_id"]
            or subscription["access_revoked_at"]
        ):
            return
        if not account["handoff_id"] and (
            subscription["provider_subscription_id"]
            != account["provider_subscription_id"]
            or subscription["cancel_at_period_end"]
        ):
            return
        quantity = seat_count(account["plan"], int(subscription["active_members"]))
        start = utc(account["period_end"])
        end = next_month(start, account["provider"], account["billing_day"])
        deadline = min(
            end,
            start
            + timedelta(
                days=max(1, min(7, int(os.getenv("BILLING_PAYMENT_GRACE_DAYS", "7"))))
            ),
        )
        job = _query(
            """INSERT INTO billing_collection_jobs(account_id,organization_id,payer_user_id,provider,
            provider_subscription_id,purpose,plan,period_start,period_end,quantity,amount,idempotency_key,due_at,next_attempt_at,deadline)
            VALUES (%s,%s,%s,%s,%s,'renewal',%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING *""",
            (
                account["id"],
                account["organization_id"],
                account["payer_user_id"],
                account["provider"],
                account["provider_subscription_id"],
                account["plan"],
                start,
                end,
                quantity,
                plan_unit_amount_kobo(account["plan"]) * quantity,
                f"renewal:{account['id']}:{start.isoformat()}",
                start,
                start,
                deadline,
            ),
            one=True,
        )
        if job and _allowed(job):
            _query(
                "UPDATE organization_subscriptions SET status='past_due',grace_period_end=%s,updated_at=NOW() WHERE organization_id=%s AND access_revoked_at IS NULL",
                (deadline, account["organization_id"]),
            )


def quote_seats(organization_id: int, payer_user_id: str, target: int):
    subscription = _organization(organization_id)
    if not subscription or subscription["owner_user_id"] != payer_user_id:
        raise HTTPException(
            403,
            detail={
                "error": "organization_owner_required",
                "message": "Only the owner can purchase organization seats.",
            },
        )
    target = seat_count(subscription["plan"], target)
    current = subscription["max_accounts"]
    end = parse_timestamp(subscription["current_period_end"])
    start = parse_timestamp(subscription["current_period_start"])
    account = _account(
        subscription["provider"], subscription["provider_subscription_id"]
    )
    if (
        account
        and account["state"] == "ready"
        and end
        and utc(account["period_end"]) == end
    ):
        start = utc(account["period_start"])
    if not start or not end or not timedelta(0) < end - start <= timedelta(days=32):
        raise HTTPException(
            409,
            detail="The current monthly billing period needs reconciliation before seats can be priced.",
        )
    if (
        subscription["status"] != "active"
        or subscription["access_revoked_at"]
        or not start
        or not end
        or end <= now() + timedelta(hours=1)
    ):
        raise HTTPException(
            409,
            detail={
                "error": "seat_change_unavailable",
                "message": "Seat purchases require an active verified period with at least one hour remaining.",
            },
        )
    if target <= current:
        raise HTTPException(
            422,
            detail={
                "error": "invalid_seat_count",
                "message": "Choose more seats than your current paid capacity. Removals reduce the next renewal automatically.",
            },
        )
    handoff = _query(
        "SELECT id FROM organization_billing_handoffs WHERE organization_id=%s AND status IN ('authorization_required','authorization_pending','scheduled')",
        (organization_id,),
        one=True,
    )
    if handoff:
        raise HTTPException(
            409,
            detail={
                "error": "billing_handoff_pending",
                "message": "Complete the payer handoff before purchasing additional seats.",
            },
        )
    amount = prorated_amount(
        plan_unit_amount_kobo(subscription["plan"]), target - current, start, end, now()
    )
    minimum = int(
        os.getenv(
            f"BILLING_{subscription['provider'].upper()}_MIN_SEAT_CHARGE_KOBO",
            "5000" if subscription["provider"] == "paystack" else "0",
        )
    )
    if amount < minimum:
        raise HTTPException(
            422,
            detail={
                "error": "seat_charge_below_minimum",
                "message": "This prorated purchase is below the payment provider's supported minimum. Choose more seats or purchase at renewal; no payment has been taken.",
            },
        )
    return {
        "organization_id": organization_id,
        "provider": subscription["provider"],
        "plan": subscription["plan"],
        "previous_quantity": current,
        "quantity": target,
        "active_members": int(subscription["active_members"]),
        "amount": amount,
        "unit_amount": plan_unit_amount_kobo(subscription["plan"]),
        "currency": "NGN",
        "period_start": start,
        "period_end": end,
        "provider_subscription_id": subscription["provider_subscription_id"],
    }


def create_seat_job(
    organization_id: int,
    payer_user_id: str,
    target: int,
    key: str,
    expected_amount: int,
    *,
    account_locked: bool = False,
):
    if not key or len(key) > 128:
        raise HTTPException(422, detail="A bounded Idempotency-Key is required.")
    subscription = _organization(organization_id)
    if not subscription or subscription["owner_user_id"] != payer_user_id:
        raise HTTPException(
            403, detail="Only the owner can purchase organization seats."
        )
    previous = _query(
        "SELECT * FROM billing_collection_jobs WHERE payer_user_id=%s AND idempotency_key=%s",
        (payer_user_id, key),
        one=True,
    )
    if previous:
        if (
            previous["organization_id"] != organization_id
            or previous["quantity"] != target
            or previous["purpose"] != "seats"
        ):
            raise HTTPException(
                409, detail="The idempotency key belongs to a different operation."
            )
        return public_job(previous["id"], payer_user_id)
    quote_seats(organization_id, payer_user_id, target)  # authorize before provider IO
    prepare_account(organization_id)
    with collection_lock(organization_id):
        previous = _query(
            "SELECT * FROM billing_collection_jobs WHERE payer_user_id=%s AND idempotency_key=%s",
            (payer_user_id, key),
            one=True,
        )
        if previous:
            if (
                previous["organization_id"] != organization_id
                or previous["quantity"] != target
                or previous["purpose"] != "seats"
            ):
                raise HTTPException(
                    409, detail="The idempotency key belongs to a different operation."
                )
            return public_job(previous["id"], payer_user_id)
        quote = quote_seats(organization_id, payer_user_id, target)
        # Quote totals may decrease by a kobo while the owner confirms. Never
        # charge more than the owner approved, and pin the displayed amount.
        if expected_amount < quote["amount"] or expected_amount - quote[
            "amount"
        ] > plan_unit_amount_kobo(quote["plan"]):
            raise HTTPException(
                409, detail="The quote changed. Review a fresh quote before paying."
            )
        account = _account(quote["provider"], quote["provider_subscription_id"])
        outstanding = _query(
            """SELECT id FROM billing_collection_jobs WHERE organization_id=%s
               AND (status IN ('open','pending','review') OR (purpose='seats' AND status='requires_action'))""",
            (organization_id,),
            one=True,
        )
        if outstanding:
            raise HTTPException(
                409,
                detail={
                    "error": "billing_operation_pending",
                    "message": "A payment is still pending. Check its result before starting another purchase.",
                },
            )
        time_now = now()
        job = _query(
            """INSERT INTO billing_collection_jobs(account_id,organization_id,payer_user_id,provider,provider_subscription_id,
            purpose,plan,period_start,period_end,quantity,previous_quantity,amount,idempotency_key,due_at,next_attempt_at,deadline)
            VALUES (%s,%s,%s,%s,%s,'seats',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                account["id"],
                organization_id,
                payer_user_id,
                quote["provider"],
                quote["provider_subscription_id"],
                quote["plan"],
                quote["period_start"],
                quote["period_end"],
                target,
                quote["previous_quantity"],
                quote["amount"],
                key,
                time_now,
                time_now,
                min(time_now + timedelta(minutes=45), quote["period_end"]),
            ),
            one=True,
        )
    (_process_job if account_locked else process_job)(job["id"])
    return public_job(job["id"], payer_user_id)


def public_job(job_id: int, payer_user_id: str):
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE id=%s AND payer_user_id=%s",
        (job_id, payer_user_id),
        one=True,
    )
    if not job:
        raise HTTPException(404, detail="Payment operation not found.")
    attempt = _query(
        "SELECT checkout_url FROM billing_collection_attempts WHERE job_id=%s ORDER BY ordinal DESC LIMIT 1",
        (job_id,),
        one=True,
    )
    return {
        "id": job["id"],
        "status": job["status"],
        "purpose": job["purpose"],
        "amount": job["amount"],
        "currency": job["currency"],
        "quantity": job["quantity"],
        "attempts": job["attempts"],
        "next_attempt_at": job["next_attempt_at"] if job["status"] == "open" else None,
        "message": job["last_error"],
        "checkout_url": (
            (attempt or {}).get("checkout_url") if job["status"] in {"pending", "requires_action"} else None
        ),
    }


def billing_status(subscription: dict | None):
    if not subscription or not schema_ready():
        return None
    provider, sub = subscription.get("provider"), subscription.get(
        "provider_subscription_id"
    )
    account = _account(provider, sub) if provider and sub else None
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE provider=%s AND provider_subscription_id=%s ORDER BY id DESC LIMIT 1",
        (provider, sub),
        one=True,
    )
    return {
        "managed": bool(account),
        "state": account["state"] if account else "provider_managed",
        "active_seats": subscription.get("active_members"),
        "paid_seats": subscription.get("max_accounts"),
        "can_add_seats": bool(
            enabled()
            and subscription.get("scope") == "organization"
            and subscription.get("organization_role") == "owner"
            and subscription.get("status") == "active"
            and not subscription.get("access_revoked_at")
            and (
                subscription.get("plan") != "business"
                or int(subscription.get("max_accounts") or 0) < 19
            )
        ),
        "renewal_basis": "active_members" if account else "purchased_capacity",
        "organization_id": subscription.get("organization_id"),
        "job": (
            public_job(job["id"], job["payer_user_id"])
            if job
            and job["payer_user_id"] == subscription.get("user_id")
            and (
                subscription.get("scope") != "organization"
                or subscription.get("organization_role") == "owner"
            )
            else None
        ),
    }


def route_event(event, *, conn=None):
    """Keep one-off collection payments out of normal full-plan activation."""
    if not schema_ready(conn):
        return event, None
    raw = event.raw or {}
    data = raw.get("data") or {}
    if not isinstance(data, dict):
        return event, None
    obj = data.get("object") if isinstance(data.get("object"), dict) else data
    metadata = obj.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    reference = (
        metadata.get("redocx_collection_reference")
        or obj.get("reference")
        or event.provider_reference
    )
    attempt = _query(
        """SELECT a.*,j.provider_subscription_id,j.organization_id,j.payer_user_id,j.plan
        FROM billing_collection_attempts a JOIN billing_collection_jobs j ON j.id=a.job_id
        WHERE j.provider=%s AND (a.reference=%s OR a.provider_id=%s) LIMIT 1""",
        (event.provider, reference, event.provider_reference),
        one=True,
        conn=conn,
    )
    if attempt:
        if event.provider == "paystack" and conn is not None:
            lock_account_lifecycle(conn, attempt["payer_user_id"])
            event = replace(event, user_id=attempt["payer_user_id"])
        if event.action in {"suspend", "revoke", "restore"}:
            event = replace(
                event,
                provider_subscription_id=attempt["provider_subscription_id"],
                organization_id=attempt["organization_id"],
                user_id=attempt["payer_user_id"],
                plan=attempt["plan"],
            )
            return event, None
        if event.provider == "paystack" and event.action == "activate":
            _query(
                """UPDATE billing_collection_jobs
                   SET next_attempt_at=NOW(), updated_at=NOW()
                   WHERE id=%s AND status IN ('open','pending','requires_action','review')""",
                (attempt["job_id"],), conn=conn,
            )
        return event, {
            "success": True,
            "collection_job_id": attempt["job_id"],
            "message": "Collection payment queued for independent verification.",
        }
    account = (
        _query("SELECT id FROM billing_collection_accounts WHERE provider=%s AND provider_subscription_id=%s",
               (event.provider, event.provider_subscription_id), one=True, conn=conn)
        if event.provider_subscription_id
        else None
    )
    if account and event.action in {"activate", "cancel", "past_due"}:
        return event, {
            "success": True,
            "ignored": True,
            "message": "Native subscription event retained by provider; ReDOCX collection owns this renewal schedule.",
        }
    if event.provider == "paystack" and event.action == "past_due":
        recovered = _query(
            "SELECT id FROM billing_collection_jobs WHERE provider='paystack' AND provider_subscription_id=%s AND invoice_code=%s AND purpose='recovery' AND status='paid' AND period_end>NOW()",
            (event.provider_subscription_id, obj.get("invoice_code")),
            one=True,
            conn=conn,
        )
        if recovered:
            return event, {
                "success": True,
                "ignored": True,
                "message": "This failed native invoice has already been recovered.",
            }
    return event, None


def queue_personal_recovery(subscription: dict):
    native = provider_io.native_subscription(
        "paystack", subscription["provider_subscription_id"]
    )
    invoice = native.raw.get("most_recent_invoice") or {}
    if str(native.status).lower() != "attention" or invoice.get("paid") in (True, 1):
        return
    start = parse_timestamp(invoice.get("period_start"))
    end = native.current_period_end
    code = invoice.get("invoice_code")
    if (
        not start
        or not end
        or not start <= now() < end
        or not code
        or not timedelta(days=25) < end - start < timedelta(days=33)
    ):
        return
    amount = invoice.get("amount")
    if amount != plan_unit_amount_kobo("personal"):
        return
    recoverable = retryable_decline(str(invoice.get("description") or ""))
    authorization = (native.raw.get("authorization") or {}).get(
        "authorization_code"
    ) or ""
    deadline = min(end, start + timedelta(days=7))
    _query(
        """INSERT INTO billing_collection_jobs(payer_user_id,provider,provider_subscription_id,purpose,plan,
        period_start,period_end,quantity,amount,invoice_code,idempotency_key,attempts,due_at,next_attempt_at,deadline,
        initial_authorization_fingerprint,status,last_error)
        VALUES (%s,'paystack',%s,'recovery','personal',%s,%s,1,%s,%s,%s,1,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
        (
            subscription["user_id"],
            subscription["provider_subscription_id"],
            start,
            end,
            amount,
            code,
            f"recovery:{subscription['provider_subscription_id']}:{code}",
            start,
            start + timedelta(days=1),
            deadline,
            hashlib.sha256(str(authorization).encode()).hexdigest(),
            "open" if recoverable else "requires_action",
            (
                "Temporary payment failure; a retry is scheduled."
                if recoverable
                else "Update the payment method before retrying this renewal."
            ),
        ),
    )


def recovered_state(provider: str, subscription_id: str, state):
    if provider != "paystack" or not schema_ready():
        return state
    invoice = state.raw.get("most_recent_invoice") or {}
    job = _query(
        "SELECT * FROM billing_collection_jobs WHERE provider='paystack' AND provider_subscription_id=%s AND purpose='recovery' AND status='paid' AND invoice_code=%s AND period_end>NOW() ORDER BY id DESC LIMIT 1",
        (subscription_id, invoice.get("invoice_code")),
        one=True,
    )
    if job:
        return replace(
            state,
            status=state.status if state.cancel_at_period_end else "active",
            current_period_start=job["period_start"],
            current_period_end=job["period_end"],
        )
    return (
        preserve_paid_end(provider, subscription_id, state)
        if state.cancel_at_period_end
        else state
    )


def preserve_paid_end(provider: str, subscription_id: str, state):
    """Cancelling an unpaid invoice must not turn its next charge date into access."""
    if provider != "paystack":
        return state
    row = _query(
        """SELECT current_period_end FROM user_subscriptions
        WHERE provider=%s AND provider_subscription_id=%s AND status='past_due'
        UNION ALL SELECT current_period_end FROM organization_subscriptions
        WHERE provider=%s AND provider_subscription_id=%s AND status='past_due' LIMIT 1""",
        (provider, subscription_id, provider, subscription_id),
        one=True,
    )
    if (
        row
        and row["current_period_end"]
        and state.current_period_end
        and utc(state.current_period_end) > utc(row["current_period_end"])
    ):
        return replace(state, current_period_end=row["current_period_end"])
    return state


def run_collection() -> dict[str, int]:
    result = {"prepared": 0, "processed": 0, "failed": 0, "review": 0}
    if not schema_ready():
        return result
    if enabled():
        candidates = _query(
            """SELECT os.organization_id, os.provider, o.owner_user_id FROM organization_subscriptions os
            JOIN organizations o ON o.id=os.organization_id
            LEFT JOIN billing_collection_accounts a ON a.provider=os.provider AND a.provider_subscription_id=os.provider_subscription_id
            WHERE os.provider IN ('stripe','paystack') AND os.provider_subscription_id IS NOT NULL
            AND ((os.status='active' AND NOT os.cancel_at_period_end AND os.current_period_end>NOW()+INTERVAL '10 minutes' AND os.access_revoked_at IS NULL AND a.id IS NULL)
                 OR a.state='preparing') ORDER BY os.current_period_end LIMIT 50"""
        )
        for candidate in candidates:
            try:
                if candidate["provider"] == "paystack":
                    with account_operation(candidate["owner_user_id"]):
                        if not _active_payer(candidate["owner_user_id"]):
                            continue
                        prepare_account(candidate["organization_id"])
                else:
                    prepare_account(candidate["organization_id"])
                result["prepared"] += 1
            except Exception as exc:
                result["failed"] += 1
                logger.warning(
                    "Active-seat preparation pending for organization %s: %s",
                    candidate["organization_id"],
                    type(exc).__name__,
                )
        handoffs = _query(
            "SELECT * FROM organization_billing_handoffs WHERE status='scheduled' AND new_provider_subscription_id IS NOT NULL AND effective_at>NOW() LIMIT 50"
        )
        for handoff in handoffs:
            try:
                if handoff["provider"] == "paystack":
                    with account_operation(handoff["new_owner_user_id"]):
                        if not _active_payer(handoff["new_owner_user_id"]):
                            continue
                        prepare_account(handoff["organization_id"], handoff=handoff)
                else:
                    prepare_account(handoff["organization_id"], handoff=handoff)
            except Exception:
                result["failed"] += 1
    if os.getenv("BILLING_PAYSTACK_RECOVERY_ENABLED", "false").lower() in {
        "1",
        "true",
        "yes",
    }:
        for subscription in _query(
            "SELECT * FROM user_subscriptions WHERE plan='personal' AND provider='paystack' AND provider_subscription_id IS NOT NULL AND status='past_due' AND NOT cancel_at_period_end AND access_revoked_at IS NULL ORDER BY updated_at LIMIT 100"
        ):
            try:
                with account_operation(subscription["user_id"]):
                    if _active_payer(subscription["user_id"]):
                        queue_personal_recovery(subscription)
            except Exception:
                result["failed"] += 1
    # Existing managed accounts MUST continue collecting even if the enrollment
    # flag is later disabled: their native schedules have already been stopped.
    # Repair a persisted enrollment even if admission of new accounts is off.
    for account in _query(
        "SELECT * FROM billing_collection_accounts WHERE state='preparing' ORDER BY updated_at LIMIT 50"
    ):
        try:
            with collection_lock(account["organization_id"]):
                _stop_and_ready(account)
        except Exception:
            _query(
                "UPDATE billing_collection_accounts SET last_error='Native schedule cancellation needs verification.',updated_at=NOW() WHERE id=%s",
                (account["id"],),
            )
            result["failed"] += 1
    for account in _query("""SELECT a.* FROM billing_collection_accounts a
        WHERE a.state='ready' AND a.renewal_enabled AND a.period_end<=NOW()
        AND NOT EXISTS (SELECT 1 FROM billing_collection_jobs j WHERE j.account_id=a.id
                        AND j.purpose='renewal' AND j.period_start=a.period_end)
        ORDER BY a.period_end LIMIT 100"""):
        try:
            if account["provider"] == "paystack":
                with account_operation(account["payer_user_id"]):
                    if _active_payer(account["payer_user_id"]):
                        queue_renewal(account)
            else:
                queue_renewal(account)
        except Exception:
            logger.exception(
                "Could not queue organization renewal account=%s", account["id"]
            )
            result["failed"] += 1
    for job in _query(
        """SELECT j.id FROM billing_collection_jobs j
        WHERE j.next_attempt_at<=NOW() AND (j.status IN ('open','pending') OR (
            j.status IN ('requires_action','review') AND EXISTS (
                SELECT 1 FROM billing_collection_attempts a WHERE a.job_id=j.id AND a.submitted_at IS NOT NULL
                AND a.paystack_resolved_at IS NULL
                AND (a.status IN ('prepared','pending','requires_action') OR (j.provider='paystack' AND a.status='failed'))
            ) AND j.deadline + INTERVAL '7 days' > NOW()
        )) ORDER BY j.next_attempt_at,j.id LIMIT 100"""
    ):
        try:
            process_job(job["id"])
            result["processed"] += 1
        except Exception:
            logger.exception("Collection job failed job=%s", job["id"])
            result["failed"] += 1
    counts = _query(
        "SELECT COUNT(*) AS count FROM billing_collection_jobs WHERE status='review'",
        one=True,
    )
    result["review"] = int(counts["count"])
    return result
