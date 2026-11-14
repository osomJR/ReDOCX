from __future__ import annotations

"""
Billing and upgrade-plan API.

This module owns billing-plan presentation and upgrade eligibility, then delegates
checkout-session creation to backend.billing_provider.

Security model:
- frontend can only request an upgrade checkout
- checkout success pages do not grant entitlement
- verified provider webhooks activate/cancel/past-due subscriptions
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, field_validator

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.account_lifecycle import account_operation, require_active_account
from backend.billing_provider import (
    BillingCheckoutRequest,
    BillingProviderError,
    BillingWebhookEvent,
    CheckoutNotConfiguredError,
    PaystackBillingProvider,
    PaystackTransactionNotFoundError,
    cancel_provider_subscription,
    change_provider_subscription_plan,
    create_checkout_session,
    expected_checkout_amount_kobo,
    normalize_provider_name,
    parse_timestamp,
    paystack_checkout_is_closed,
    paystack_checkout_reference,
    paystack_checkout_retry_at,
    paystack_payment_outcome,
    paystack_safe_timeout,
    plan_unit_amount_kobo,
    resume_provider_subscription,
    retrieve_provider_subscription,
    verify_provider_transaction,
)
from backend.database import get_db
from backend.billing_schema import BillingSchemaError, assert_billing_schema_ready
from backend.routes.billing_webhooks import (
    BillingWebhookProcessingError,
    settle_paystack_checkout,
)
from backend.subscriptions import (
    UserEntitlement,
    get_user_entitlement,
    normalize_organization_name,
    normalize_plan,
    validate_account_count,
)


router = APIRouter(prefix="/billing", tags=["billing-v1"])
logger = logging.getLogger(__name__)

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
BillingAction = Literal["current", "upgrade", "downgrade", "none"]
BillingProviderName = Literal["paystack", "stripe"]


def _require_billing_schema_ready() -> None:
    """Fail before any provider checkout when billing storage is unsafe."""
    try:
        assert_billing_schema_ready()
    except BillingSchemaError as exc:
        logger.error("Billing schema readiness check failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "billing_schema_not_ready",
                "message": (
                    "Billing is temporarily unavailable. No payment was started. "
                    "Please try again shortly."
                ),
            },
        ) from exc

PLAN_ORDER: list[BillingPlanName] = ["free", "personal", "business", "enterprise"]
PLAN_RANK: dict[BillingPlanName, int] = {
    "free": 0,
    "personal": 1,
    "business": 2,
    "enterprise": 3,
}

VISIBLE_PLANS_BY_CURRENT: dict[BillingPlanName, list[BillingPlanName]] = {
    "free": ["free", "personal", "business", "enterprise"],
    "personal": ["free", "personal", "business", "enterprise"],
    "business": ["free", "personal", "business", "enterprise"],
    "enterprise": ["free", "personal", "business", "enterprise"],
}

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
PAYSTACK_REFERENCE_RE = re.compile(r"^[A-Za-z0-9._=-]{3,128}$")
PAYSTACK_OPEN_CHECKOUT_STATUSES = ("started", "created", "failed")
BILLING_OPERATION_DEDUPLICATION_HOURS = max(
    1, int(os.getenv("BILLING_OPERATION_DEDUPLICATION_HOURS", "24"))
)
BILLING_OPERATION_STALE_MINUTES = max(
    5, int(os.getenv("BILLING_OPERATION_STALE_MINUTES", "15"))
)
# Stripe documents a minimum 24-hour idempotency retention window. Only retry a
# stale in-progress provider operation while the original provider key is still
# inside that window; after it expires, reconciliation/manual review is safer
# than risking a duplicate recurring subscription.
BILLING_PROVIDER_IDEMPOTENCY_RETRY_HOURS = min(
    23,
    max(1, int(os.getenv("BILLING_PROVIDER_IDEMPOTENCY_RETRY_HOURS", "23"))),
)

UPGRADE_TARGETS_BY_CURRENT: dict[BillingPlanName, set[BillingPlanName]] = {
    "free": {"personal", "business", "enterprise"},
    "personal": {"business", "enterprise"},
    "business": {"enterprise"},
    "enterprise": set(),
}

PLAN_CATALOG: dict[BillingPlanName, dict[str, Any]] = {
    "free": {
        "name": "Free",
        "summary": "Authenticated free access with daily usage limits.",
        "price_label": "$0",
        "billing_period": "forever",
        "account_count_label": "1 user",
        "features": [
            "Authenticated access to supported AI tools",
            "Free-tier request limits",
            "Heavy-feature quota applies",
        ],
    },
    "personal": {
        "name": "Personal",
        "summary": "Unlimited individual access for one user.",
        "price_label": "Personal plan",
        "billing_period": "per user",
        "account_count_label": "1 user",
        "features": [
            "Unlimited supported feature use",
            "PDF tools and e-signature access",
            "Best for one professional user",
        ],
    },
    "business": {
        "name": "Business",
        "summary": "Team access for growing organizations.",
        "price_label": "Business plan",
        "billing_period": "team plan",
        "account_count_label": "1–19 users",
        "features": [
            "Unlimited supported feature use",
            "Team projects and organization workspace",
            "Business team messaging and calls",
        ],
    },
    "enterprise": {
        "name": "Enterprise",
        "summary": "Advanced team access for large organizations.",
        "price_label": "Enterprise plan",
        "billing_period": "organization plan",
        "account_count_label": "1+ users",
        "features": [
            "Unlimited supported feature use",
            "Enterprise organization capacity",
            "Designed for larger teams and governed deployments",
        ],
    },
}

CHECKOUT_PROVIDER_ORDER: list[BillingProviderName] = ["paystack", "stripe"]
CHECKOUT_PROVIDER_CATALOG: dict[BillingProviderName, dict[str, Any]] = {
    "paystack": {
        "name": "Paystack",
        "summary": "Recurring payments by card or eligible Nigerian Direct Debit.",
        "region_label": "Nigeria / Africa",
    },
    "stripe": {
        "name": "Stripe",
        "summary": "Recommended for US and European cards and international checkout.",
        "region_label": "US / Europe",
    },
}

def _normalize_billing_plan(value: str) -> BillingPlanName:
    normalized = normalize_plan(value)
    return normalized  # type: ignore[return-value]


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _normalize_checkout_provider(provider_name: str | None = None) -> BillingProviderName:
    normalized = normalize_provider_name(provider_name)
    if normalized not in CHECKOUT_PROVIDER_ORDER:
        raise ValueError("provider must be one of: paystack, stripe.")
    return normalized  # type: ignore[return-value]


def _provider_display_name(provider_name: str | None) -> str:
    normalized = str(provider_name or "").strip().lower()
    catalog = CHECKOUT_PROVIDER_CATALOG.get(normalized)  # type: ignore[arg-type]
    if catalog:
        return str(catalog["name"])
    return normalized.replace("_", " ").title() or "the current billing provider"


def _checkout_configured_for_provider(plan: BillingPlanName, provider_name: str | None = None) -> bool:
    """
    Best-effort UI hint only. The real authority is create_checkout_session(),
    which validates provider configuration and returns a checkout URL.
    """
    if plan == "free":
        return False

    try:
        provider = _normalize_checkout_provider(provider_name)
    except ValueError:
        return False

    if provider == "paystack":
        has_plan = bool(_env(f"PAYSTACK_{plan.upper()}_PLAN_CODE"))
        has_redirect = bool(_env("PAYSTACK_CALLBACK_URL") or _env("BILLING_SUCCESS_URL"))
        return bool(_env("PAYSTACK_SECRET_KEY") and has_redirect and has_plan)

    if provider == "stripe":
        return bool(
            _env("STRIPE_SECRET_KEY")
            and _env(f"STRIPE_{plan.upper()}_PRICE_ID")
            and _env("BILLING_SUCCESS_URL")
            and _env("BILLING_CANCEL_URL")
        )

    return False


def _provider_configured_for_any_upgrade(provider_name: BillingProviderName, upgrade_targets: set[BillingPlanName]) -> bool:
    return any(_checkout_configured_for_provider(plan, provider_name) for plan in upgrade_targets)


def _provider_options(current_plan: BillingPlanName) -> list[dict[str, Any]]:
    return [{
        "key": "paystack",
        **CHECKOUT_PROVIDER_CATALOG["paystack"],
        "recommended": True,
        "configured": _provider_configured_for_any_upgrade("paystack", UPGRADE_TARGETS_BY_CURRENT[current_plan]),
    }]


def _current_plan_from_entitlement(entitlement: UserEntitlement) -> BillingPlanName:
    return _normalize_billing_plan(entitlement.plan)


def _future_timestamp(value: Any) -> bool:
    if not isinstance(value, datetime):
        return False
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized > datetime.now(tz=timezone.utc)


def _subscription_has_current_provider_obligation(
    subscription: dict[str, Any] | None,
) -> bool:
    """Return whether starting another recurring checkout is unsafe.

    Active and past-due provider subscriptions can still renew or recover. A
    cancelled subscription remains current until its paid period ends. Paystack
    renewal can remain live after application access expires.
    """
    if not subscription:
        return False

    status = str(subscription.get("status") or "").strip().lower()
    if not str(subscription.get("provider_subscription_id") or "").strip():
        return bool(subscription.get("provider") == "paystack"
                    and subscription.get("provider_customer_id")
                    and not subscription.get("cancel_at_period_end")
                    and status in {"active", "past_due", "inactive"})
    if status in {"active", "past_due"}:
        return True
    if status == "inactive" and subscription.get("provider") == "paystack":
        return not subscription.get("cancel_at_period_end")
    if status == "cancelled":
        period_end = subscription.get("current_period_end")
        # Missing period data is ambiguous; block a replacement rather than risk
        # a second live provider subscription. Reconciliation can resolve it.
        return period_end is None or _future_timestamp(period_end)
    return False


def _subscription_period_lock(
    subscription: dict[str, Any] | None,
    current_plan: BillingPlanName,
) -> tuple[bool, datetime | None]:
    """Fail closed for paid plan changes until the provider period ends."""
    if current_plan not in {"personal", "business", "enterprise"}:
        return False, None
    if not _subscription_has_current_provider_obligation(subscription):
        return False, None
    period_end = (subscription or {}).get("current_period_end")
    if period_end is None or not isinstance(period_end, datetime):
        return True, None
    normalized = period_end if period_end.tzinfo else period_end.replace(tzinfo=timezone.utc)
    return normalized > datetime.now(tz=timezone.utc), normalized


def _raise_if_subscription_period_locked(
    subscription: dict[str, Any] | None,
    current_plan: BillingPlanName,
) -> None:
    locked, period_end = _subscription_period_lock(subscription, current_plan)
    if not locked:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "error": "subscription_period_locked",
            "message": (
                "The subscribed plan is locked for the current paid period. "
                "Cancellation may stop the next renewal, but the plan cannot change until this period ends."
            ),
            "current_plan": current_plan,
            "current_period_end": period_end.isoformat() if period_end is not None else None,
        },
    )


def _plan_action(current_plan: BillingPlanName, plan: BillingPlanName) -> BillingAction:
    if plan == current_plan:
        return "current"
    if plan in UPGRADE_TARGETS_BY_CURRENT[current_plan]:
        return "upgrade"
    if PLAN_RANK[plan] < PLAN_RANK[current_plan]:
        return "downgrade"
    return "none"


def _plan_reason(current_plan: BillingPlanName, plan: BillingPlanName, action: BillingAction) -> str:
    if action == "current":
        return "This is your current plan."
    if action == "upgrade":
        return f"You can upgrade from {PLAN_CATALOG[current_plan]['name']} to {PLAN_CATALOG[plan]['name']}."
    if action == "downgrade":
        return (
            f"You can choose {PLAN_CATALOG[plan]['name']} after the current paid period ends."
        )
    return "This plan is visible for comparison, but no upgrade action is available from your current plan."


def build_billing_state(
    entitlement: UserEntitlement,
    *,
    current_user: AuthenticatedUser | None = None,
    subscription: dict[str, Any] | None = None,
) -> dict[str, Any]:
    persisted_plan = (
        str(subscription.get("plan") or "").strip().lower()
        if _subscription_has_current_provider_obligation(subscription)
        else ""
    )
    current_plan = (
        _normalize_billing_plan(persisted_plan)
        if persisted_plan in {"personal", "business", "enterprise"}
        else _current_plan_from_entitlement(entitlement)
    )
    visible_plans = VISIBLE_PLANS_BY_CURRENT[current_plan]
    recommended_provider = "paystack"

    can_manage_subscription = not (
        subscription
        and subscription.get("scope") == "organization"
        and subscription.get("organization_role") != "owner"
    )
    access_revoked = bool(subscription and subscription.get("access_revoked_at"))
    period_locked, locked_until = _subscription_period_lock(subscription, current_plan)
    can_change_plan = can_manage_subscription and not access_revoked and not period_locked

    cards: list[dict[str, Any]] = []
    for plan in visible_plans:
        catalog = PLAN_CATALOG[plan]
        action = _plan_action(current_plan, plan)
        provider_checkout_configured = {
            "paystack": _checkout_configured_for_provider(plan, "paystack")
        }
        cards.append(
            {
                "key": plan,
                "name": catalog["name"],
                "summary": catalog["summary"],
                "price_label": catalog["price_label"],
                "unit_amount_kobo": (
                    plan_unit_amount_kobo(plan)
                    if plan in {"personal", "business", "enterprise"}
                    else 0
                ),
                "billing_period": catalog["billing_period"],
                "account_count_label": catalog["account_count_label"],
                "features": catalog["features"],
                "is_current": action == "current",
                "can_upgrade": action == "upgrade" and can_change_plan,
                "can_downgrade": action == "downgrade" and can_change_plan,
                "action": action,
                "reason": (
                    "Billing access is suspended while a refund, dispute, or chargeback review is active."
                    if action in {"upgrade", "downgrade"} and access_revoked
                    else "Only the organization owner can change this subscription."
                    if action in {"upgrade", "downgrade"}
                    and not can_manage_subscription
                    else (
                        "Your subscribed plan is locked until the current paid period ends. "
                        "Cancellation only stops the next renewal; it does not change the current plan."
                    )
                    if action in {"upgrade", "downgrade"} and period_locked
                    else _plan_reason(current_plan, plan, action)
                ),
                "checkout_configured": any(provider_checkout_configured.values()),
                "provider_checkout_configured": provider_checkout_configured,
            }
        )

    from backend.billing_collection import billing_status
    personal = None
    if current_user and subscription and subscription.get("scope") == "organization":
        personal = _billing_subscription_record(entitlement, user_id=current_user.user_id, personal_only=True)
    personal_management = None
    if personal and personal.get("plan") == "personal" and personal.get("provider") == "paystack":
        personal_management = {
            "status": personal["status"],
            "current_period_end": personal["current_period_end"],
            "cancel_at_period_end": personal["cancel_at_period_end"],
            "can_cancel": _subscription_has_current_provider_obligation(personal) and not personal["cancel_at_period_end"],
            "can_resume": bool(personal["cancel_at_period_end"] and not personal["access_revoked_at"]
                               and personal["provider_subscription_id"] and _future_timestamp(personal["current_period_end"])),
            "collection": billing_status(personal),
        }
    return {
        "collection": billing_status(subscription),
        "personal_management": personal_management,
        "paystack_checkout": _paystack_checkout_summary(current_user.user_id) if current_user else None,
        "current_plan": current_plan,
        "current_plan_name": PLAN_CATALOG[current_plan]["name"],
        "provider": recommended_provider,
        "recommended_provider": recommended_provider,
        "providers": _provider_options(current_plan),
        "entitlement": {
            "plan": entitlement.plan,
            "account_count": entitlement.account_count,
            "status": entitlement.status,
            "is_paid": entitlement.is_paid,
            "source": entitlement.source,
            "organization_id": entitlement.organization_id,
            "organization_name": entitlement.organization_name,
            "organization_role": entitlement.organization_role,
        },
        "plans": cards,
        "upgrade_targets": sorted(UPGRADE_TARGETS_BY_CURRENT[current_plan], key=PLAN_RANK.get),
        "management": {
            "can_cancel": bool(
                can_manage_subscription
                and _subscription_has_current_provider_obligation(subscription)
                and subscription
                and subscription.get("provider_subscription_id")
                and not subscription.get("cancel_at_period_end")
            ),
            "can_resume": bool(
                can_manage_subscription
                and not access_revoked
                and subscription
                and subscription.get("cancel_at_period_end")
                and subscription.get("provider_subscription_id")
                and _future_timestamp(subscription.get("current_period_end"))
            ),
            "provider": subscription.get("provider") if subscription else None,
            "provider_subscription_id": (
                subscription.get("provider_subscription_id") if subscription else None
            ),
            "current_period_end": (
                subscription.get("current_period_end") if subscription else None
            ),
            "period_locked": period_locked,
            "plan_change_available_at": locked_until,
            "cancel_at_period_end": bool(
                subscription and subscription.get("cancel_at_period_end")
            ),
            "pending_plan": subscription.get("pending_plan") if subscription else None,
            "plan_change_effective_at": (
                subscription.get("plan_change_effective_at") if subscription else None
            ),
            "grace_period_end": (
                subscription.get("grace_period_end") if subscription else None
            ),
            "payment_failure_count": int(
                subscription.get("payment_failure_count") or 0
            )
            if subscription
            else 0,
            "access_revoked_at": (
                subscription.get("access_revoked_at") if subscription else None
            ),
            "access_revocation_reason": (
                subscription.get("access_revocation_reason")
                if subscription
                else None
            ),
        },
    }


class SeatChangeRequest(BaseModel):
    organization_id: int
    seat_count: int
    expected_amount: int

    @field_validator("organization_id", "seat_count", "expected_amount", mode="before")
    @classmethod
    def positive_integer(cls, value):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("A positive integer is required.")
        return value


@router.get("/seat-changes")
def get_seat_change(organization_id: int | None = None, seat_count: int | None = None,
                    job_id: int | None = None, current_user: AuthenticatedUser = Depends(get_current_user)):
    from backend.billing_collection import quote_seats, public_job
    if job_id is not None:
        return public_job(job_id, current_user.user_id)
    if organization_id is None or seat_count is None:
        raise HTTPException(422, detail="organization_id and seat_count are required.")
    try:
        return quote_seats(organization_id, current_user.user_id, seat_count)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/seat-changes")
def purchase_seats(payload: SeatChangeRequest, request: Request,
                   current_user: AuthenticatedUser = Depends(get_current_user)):
    from backend.billing_collection import create_seat_job
    _require_billing_schema_ready()
    try:
        with account_operation(current_user.user_id) as lock_conn:
            require_active_account(lock_conn, current_user.user_id)
            return create_seat_job(payload.organization_id, current_user.user_id, payload.seat_count,
                                   _require_idempotency_key(request), payload.expected_amount, account_locked=True)
    except BillingProviderError as exc:
        raise HTTPException(503, detail={"error":"billing_operation_pending","message":str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


class PaymentMethodRequest(BaseModel):
    scope: Literal["personal", "current"] = "current"


@router.post("/payment-method")
def update_payment_method(payload: PaymentMethodRequest | None = None,
                          current_user: AuthenticatedUser = Depends(get_current_user)):
    from backend.billing_collection_provider import payment_method_url
    from backend.billing_collection import managed_state
    with account_operation(current_user.user_id) as lock_conn:
        require_active_account(lock_conn, current_user.user_id)
        entitlement = get_user_entitlement(current_user.user_id)
        subscription = _billing_subscription_record(
            entitlement, user_id=current_user.user_id,
            personal_only=bool(payload and payload.scope == "personal"),
        )
        if (not subscription or not subscription["provider_subscription_id"]
                or (subscription["scope"] == "organization" and subscription["organization_role"] != "owner")):
            raise HTTPException(403, detail="Only the subscription payer may update the payment method.")
        if managed_state(subscription["provider"], subscription["provider_subscription_id"]):
            raise HTTPException(409, detail="Use the outstanding renewal's secure payment button to replace its payment method.")
        try:
            return {"url": payment_method_url(subscription["provider"], subscription["provider_subscription_id"])}
        except BillingProviderError as exc:
            raise HTTPException(503, detail=str(exc)) from exc


class CollectionRetryRequest(BaseModel):
    job_id: int
    mode: Literal["updated_method", "checkout", "check"] = "updated_method"


@router.post("/collection-retry")
def retry_collection(payload: CollectionRetryRequest, current_user: AuthenticatedUser = Depends(get_current_user)):
    from backend.billing_collection import retry_after_method_update, checkout_declined_renewal, process_job, public_job
    try:
        if payload.mode == "check":
            public_job(payload.job_id, current_user.user_id)
            process_job(payload.job_id, verify_only=True)
            return public_job(payload.job_id, current_user.user_id)
        with account_operation(current_user.user_id) as lock_conn:
            require_active_account(lock_conn, current_user.user_id)
            if payload.mode == "checkout":
                return checkout_declined_renewal(payload.job_id, current_user.user_id, account_locked=True)
            return retry_after_method_update(payload.job_id, current_user.user_id, account_locked=True)
    except BillingProviderError as exc:
        raise HTTPException(503,detail="The payment could not be confirmed. Check its status before retrying.") from exc


def _current_user_email(current_user: AuthenticatedUser) -> str | None:
    email = current_user.claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


def _billing_subscription_record(
    entitlement: UserEntitlement,
    *,
    user_id: str,
    personal_only: bool = False,
) -> dict[str, Any] | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            if not personal_only and entitlement.source == "organization" and entitlement.organization_id:
                cur.execute(
                    """
                    SELECT
                        'organization' AS scope,
                        os.organization_id,
                        os.plan,
                        os.status,
                        os.provider,
                        os.provider_customer_id,
                        os.provider_subscription_id,
                        os.current_period_start,
                        os.current_period_end,
                        os.cancel_at_period_end,
                        os.pending_plan,
                        os.plan_change_effective_at,
                        os.grace_period_end,
                        os.payment_failure_count,
                        os.access_revoked_at,
                        os.access_revocation_reason,
                        os.updated_at,
                        o.name AS organization_name,
                        om.role AS organization_role,
                        os.max_accounts,
                        (
                            SELECT COUNT(*)
                            FROM organization_members active_om
                            WHERE active_om.organization_id = os.organization_id
                              AND active_om.status = 'active'
                        ) AS active_members
                    FROM organization_subscriptions os
                    JOIN organizations o ON o.id = os.organization_id
                    LEFT JOIN organization_members om
                      ON om.organization_id = os.organization_id
                     AND om.user_id = %s
                     AND om.status = 'active'
                    WHERE os.organization_id = %s
                    """,
                    (user_id, entitlement.organization_id),
                )
                row = cur.fetchone()
            else:
                cur.execute(
                    """
                    SELECT
                        'user' AS scope,
                        NULL::BIGINT AS organization_id,
                        plan,
                        status,
                        provider,
                        provider_customer_id,
                        provider_subscription_id,
                        current_period_start,
                        current_period_end,
                        cancel_at_period_end,
                        pending_plan,
                        plan_change_effective_at,
                        grace_period_end,
                        payment_failure_count,
                        access_revoked_at,
                        access_revocation_reason,
                        updated_at,
                        NULL::TEXT AS organization_name,
                        NULL::TEXT AS organization_role,
                        1::INTEGER AS max_accounts,
                        1::BIGINT AS active_members
                    FROM user_subscriptions
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()

                # Suspended/expired organization entitlements normalize to Free.
                # Still find an owned provider subscription so the customer can
                # cancel it and cannot accidentally create a second checkout.
                if not personal_only and (
                    row is None
                    or str(row[2] or "").strip().lower() == "free"
                    or not str(row[6] or "").strip()
                    or (str(row[3]).lower() == "cancelled" and not _future_timestamp(row[8]))
                ):
                    cur.execute(
                        """
                        SELECT
                            'organization' AS scope,
                            os.organization_id,
                            os.plan,
                            os.status,
                            os.provider,
                            os.provider_customer_id,
                            os.provider_subscription_id,
                            os.current_period_start,
                            os.current_period_end,
                            os.cancel_at_period_end,
                            os.pending_plan,
                            os.plan_change_effective_at,
                            os.grace_period_end,
                            os.payment_failure_count,
                            os.access_revoked_at,
                            os.access_revocation_reason,
                            os.updated_at,
                            o.name AS organization_name,
                            'owner'::TEXT AS organization_role,
                            os.max_accounts,
                            (
                                SELECT COUNT(*)
                                FROM organization_members active_om
                                WHERE active_om.organization_id = os.organization_id
                                  AND active_om.status = 'active'
                            ) AS active_members
                        FROM organization_subscriptions os
                        JOIN organizations o ON o.id = os.organization_id
                        WHERE o.owner_user_id = %s
                          AND os.provider IN ('stripe', 'paystack')
                          AND os.provider_subscription_id IS NOT NULL
                          AND (os.status IN ('active', 'past_due', 'cancelled')
                              OR (os.provider='paystack' AND os.status='inactive' AND NOT os.cancel_at_period_end))
                        ORDER BY os.updated_at DESC, os.organization_id DESC
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    owner_row = cur.fetchone()
                    if owner_row is not None:
                        row = owner_row

    if row is None:
        return None
    return {
        "user_id": user_id,
        "scope": row[0],
        "organization_id": int(row[1]) if row[1] is not None else None,
        "plan": row[2],
        "status": row[3],
        "provider": row[4],
        "provider_customer_id": row[5],
        "provider_subscription_id": row[6],
        "current_period_start": row[7],
        "current_period_end": row[8],
        "cancel_at_period_end": bool(row[9]),
        "pending_plan": row[10],
        "plan_change_effective_at": row[11],
        "grace_period_end": row[12],
        "payment_failure_count": int(row[13] or 0),
        "access_revoked_at": row[14],
        "access_revocation_reason": row[15],
        "updated_at": row[16],
        "organization_name": row[17],
        "organization_role": row[18],
        "max_accounts": int(row[19]) if row[19] is not None else None,
        "active_members": int(row[20] or 0),
    }


def _require_idempotency_key(request: Request) -> str:
    value = str(request.headers.get("idempotency-key") or "").strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(value):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_idempotency_key",
                "message": (
                    "Idempotency-Key is required and must contain 8-128 letters, "
                    "numbers, dots, underscores, colons, or hyphens."
                ),
            },
        )
    return value


def _operation_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _begin_billing_operation(
    *,
    current_user: AuthenticatedUser,
    provider: BillingProviderName,
    idempotency_key: str,
    request_fingerprint: str,
    operation: str,
    target_plan: BillingPlanName,
    current_plan: BillingPlanName,
    organization_id: int | None,
    organization_name: str | None,
    operation_metadata: dict[str, Any] | None = None,
    provider_reference: str | None = None,
) -> dict[str, Any]:
    def resolve_existing(row: Any) -> dict[str, Any]:
        if row is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "billing_operation_conflict",
                    "message": "Could not reserve the billing operation.",
                },
            )
        if row[1] != request_fingerprint:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "idempotency_key_reused",
                    "message": "This Idempotency-Key was already used with different billing parameters.",
                },
            )
        if provider == "paystack" and operation == "checkout" and row[2] == "created":
            # A saved checkout must pass the provider status check before its
            # URL is returned. An unreconciled row cannot replay a cached URL.
            return {
                "id": int(row[0]),
                "replayed": True,
                "api_response": {
                    "status": "checkout_pending", "provider": "paystack",
                    "checkout_status": "review", "reference": row[8], "target_plan": target_plan,
                },
            }
        raw_response = row[3] if isinstance(row[3], dict) else {}
        api_response = (
            raw_response.get("api_response")
            if isinstance(raw_response, dict)
            else None
        )
        if row[2] in {"created", "completed"} and isinstance(api_response, dict):
            if provider == "paystack" and operation == "checkout" and row[2] == "completed":
                api_response = {"success": True, "status": "payment_pending", "provider": "paystack", "reference": row[8]}
            return {
                "id": int(row[0]),
                "replayed": True,
                "api_response": api_response,
            }
        finished = row[2] in {"failed", "expired", "cancelled"}
        error = "billing_operation_failed" if finished else "billing_operation_in_progress"
        message = (
            "This billing operation previously failed. Start a new attempt."
            if finished
            else "This billing operation is already being processed."
        )
        raise HTTPException(
            status_code=409,
            detail={"error": error, "message": message},
        )

    with get_db() as conn:
        with conn.cursor() as cur:
            # Serialize billing mutations for this customer/provider. This closes
            # the gap where repeated clicks use different idempotency keys.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{current_user.user_id}:{provider}",),
            )

            cur.execute(
                """
                SELECT id, request_fingerprint, status, raw_response, checkout_url,
                       provider_session_id, provider_customer_id,
                       provider_subscription_id, provider_reference,
                       created_at, updated_at
                FROM billing_checkout_sessions
                WHERE user_id = %s
                  AND provider = %s
                  AND idempotency_key = %s
                LIMIT 1
                """,
                (current_user.user_id, provider, idempotency_key),
            )
            exact_row = cur.fetchone()
            if exact_row is not None:
                if exact_row[1] != request_fingerprint:
                    return resolve_existing(exact_row)

                created_at = exact_row[9]
                updated_at = exact_row[10]
                now = datetime.now(tz=timezone.utc)
                stale_before = now - timedelta(minutes=BILLING_OPERATION_STALE_MINUTES)
                provider_retry_after = now - timedelta(
                    hours=BILLING_PROVIDER_IDEMPOTENCY_RETRY_HOURS
                )
                if (
                    exact_row[2] == "started"
                    and isinstance(created_at, datetime)
                    and isinstance(updated_at, datetime)
                    and (updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc))
                    <= stale_before
                    and (created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc))
                    >= provider_retry_after
                ):
                    cur.execute(
                        """
                        UPDATE billing_checkout_sessions
                        SET updated_at = NOW(),
                    raw_response = '{}'::jsonb
                        WHERE id = %s
                          AND status = 'started'
                        RETURNING id
                        """,
                        (int(exact_row[0]),),
                    )
                    if cur.fetchone() is not None:
                        return {"id": int(exact_row[0]), "replayed": False}
                return resolve_existing(exact_row)

            cur.execute(
                """
                SELECT id, request_fingerprint, status, raw_response, checkout_url,
                       provider_session_id, provider_customer_id,
                       provider_subscription_id, provider_reference,
                       created_at, updated_at
                FROM billing_checkout_sessions
                WHERE user_id = %s
                  AND provider = %s
                  AND request_fingerprint = %s
                  AND status IN ('started', 'created', 'completed')
                  AND (provider <> 'paystack' OR operation <> 'checkout' OR status <> 'completed')
                  AND created_at >= NOW() - make_interval(hours => %s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (
                    current_user.user_id,
                    provider,
                    request_fingerprint,
                    BILLING_OPERATION_DEDUPLICATION_HOURS,
                ),
            )
            equivalent_row = cur.fetchone()
            if equivalent_row is not None:
                return resolve_existing(equivalent_row)

            cur.execute(
                """
                INSERT INTO billing_checkout_sessions (
                    provider,
                    user_id,
                    email,
                    target_plan,
                    current_plan,
                    organization_id,
                    organization_name,
                    status,
                    operation,
                    idempotency_key,
                    request_fingerprint,
                    metadata,
                    provider_reference
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'started', %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    provider,
                    current_user.user_id,
                    _current_user_email(current_user),
                    target_plan,
                    current_plan,
                    organization_id,
                    organization_name,
                    operation,
                    idempotency_key,
                    request_fingerprint,
                    Jsonb({
                        "source": "redocx_billing_page",
                        **dict(operation_metadata or {}),
                    }),
                    provider_reference,
                ),
            )
            inserted = cur.fetchone()
            if inserted is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "billing_operation_conflict",
                        "message": "Could not reserve the billing operation.",
                    },
                )
            return {"id": int(inserted[0]), "replayed": False}


def _refresh_paystack_checkout(checkout: dict[str, Any]) -> BillingWebhookEvent | None:
    """Verify and record checkout state while the caller holds the account lock."""
    reference = checkout["provider_reference"]
    if not reference:
        return None
    checked_at = datetime.now(tz=timezone.utc)
    try:
        payment = verify_provider_transaction("paystack", reference)
    except PaystackTransactionNotFoundError:
        payment = None
    if checkout["status"] not in PAYSTACK_OPEN_CHECKOUT_STATUSES or checkout["paystack_settled_at"]:
        return payment

    previous = checkout["recovery"] or {}
    observed_status = str(payment.status or "").strip().lower() if payment else "not_found"
    closed = paystack_checkout_is_closed(payment, None, 0)
    already_paid = previous.get("status") == "success"
    # A missing reference after the session window can close an initialization
    # that never verified. Once a transaction was seen, a later missing response
    # is an inconsistency and requires review.
    previously_seen = (
        previous.get("verified") is True
        or previous.get("status") not in {None, "not_found"}
    )
    can_expire_by_time = observed_status in {"failed", "abandoned"} or (
        payment is None and not previously_seen
    )
    retry_at = None
    if not closed and not already_paid and can_expire_by_time:
        submitted_at = checkout["paystack_submitted_at"]
        recorded_timeout = checkout["paystack_timeout_seconds"]
        if submitted_at and recorded_timeout and recorded_timeout > 0:
            data = (payment.raw.get("data") or {}) if payment else {}
            created_at = parse_timestamp(data.get("created_at") or data.get("createdAt"))
            if created_at and created_at > submitted_at:
                submitted_at = created_at
            try:
                current_timeout = PaystackBillingProvider().payment_session_timeout()
            except BillingProviderError:
                logger.warning("Paystack checkout expiry could not be verified.")
                current_timeout = 0
            timeout = paystack_safe_timeout(current_timeout, recorded_timeout)
            retry_at = paystack_checkout_retry_at(submitted_at, timeout)
            closed = paystack_checkout_is_closed(payment, submitted_at, timeout, checked_at=checked_at)

    # A failed charge can leave the hosted URL payable. Hide it without
    # releasing the payer's checkout until Paystack's outcome permits that.
    recovery = {
        "status": "success" if already_paid and not closed else observed_status,
        "verified": previously_seen or payment is not None,
        "declined": not closed and (observed_status == "failed" or previous.get("declined") is True),
        "checked_at": checked_at.isoformat(),
        "retry_at": retry_at.isoformat() if retry_at and not closed else None,
    }
    checkout["recovery"] = recovery
    if closed:
        checkout["status"] = "expired"
        checkout["checkout_url"] = None
    with get_db() as conn, conn.cursor() as cur:
        # Local checkout writers take the same account lock. The guarded update
        # also prevents a stale check from overriding a settled row.
        cur.execute(
            """
            UPDATE billing_checkout_sessions
            SET status = %s, checkout_url = %s, updated_at = NOW(),
                raw_response = jsonb_set(raw_response, '{paystack_recovery}', %s)
            WHERE id = %s AND status = ANY(%s) AND paystack_settled_at IS NULL
            """,
            (
                checkout["status"], checkout["checkout_url"], Jsonb(recovery), checkout["id"],
                list(PAYSTACK_OPEN_CHECKOUT_STATUSES),
            ),
        )
        if cur.rowcount != 1:
            raise BillingProviderError("The saved checkout changed during verification.")
    return payment


def _paystack_checkout_view(checkout: dict[str, Any]) -> dict[str, Any]:
    recovery = checkout["recovery"] or {}
    observed_status = recovery.get("status")
    # A fresh checkout URL is returned by initialization. A saved URL is only
    # offered after verification and while the known session window is open.
    declined = recovery.get("declined") or observed_status == "failed"
    retry_at = recovery.get("retry_at")
    can_resume = (
        observed_status == "abandoned" and not declined and retry_at
        and datetime.now(tz=timezone.utc) < datetime.fromisoformat(retry_at)
    )
    url = checkout["checkout_url"] if can_resume else None
    status = "checkout_pending"
    if observed_status == "success":
        status = "payment_pending"
    elif declined:
        status = "checkout_declined"
    elif not checkout["provider_reference"] or (
        observed_status == "abandoned" and not url and not retry_at
    ) or (observed_status == "not_found" and not retry_at):
        status = "review"
    return {
        "status": status,
        "provider": "paystack",
        "reference": checkout["provider_reference"],
        "target_plan": checkout["target_plan"],
        "checkout_url": url,
        "retry_at": retry_at,
        "checked_at": recovery.get("checked_at"),
    }


def _paystack_open_checkouts(user_id: str) -> list[dict[str, Any]]:
    with get_db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, request_fingerprint, provider_reference, checkout_url,
                   paystack_submitted_at, paystack_timeout_seconds, paystack_settled_at,
                   status, target_plan, idempotency_key, raw_response->'paystack_recovery' AS recovery
            FROM billing_checkout_sessions
            WHERE user_id = %s AND provider = 'paystack' AND operation = 'checkout'
              AND paystack_settled_at IS NULL
              AND status IN ('started', 'created', 'failed')
              AND (paystack_submitted_at IS NOT NULL OR checkout_url IS NOT NULL
                   OR provider_session_id IS NOT NULL OR status = 'created')
            ORDER BY (provider_reference IS NOT NULL) DESC,
                     (raw_response->'paystack_recovery'->>'status' = 'success') DESC NULLS LAST,
                     raw_response->'paystack_recovery'->>'checked_at' ASC NULLS FIRST,
                     id DESC
            LIMIT 2
            """,
            (user_id,),
        )
        return cur.fetchall()


def _paystack_checkout_summary(user_id: str) -> dict[str, Any] | None:
    rows = _paystack_open_checkouts(user_id)
    if len(rows) > 1:
        return {"status": "review", "reference": rows[0]["provider_reference"]}
    return _paystack_checkout_view(rows[0]) if rows else None


def _reserve_or_resume_paystack_checkout(
    user_id: str, fingerprint: str, idempotency_key: str,
) -> dict[str, Any] | None:
    """Resolve saved checkouts before reserving a new payable reference."""
    with get_db() as conn, conn.cursor() as cur:
        # The account lock excludes any live initialization for this payer.
        cur.execute(
            """
            UPDATE billing_checkout_sessions SET status = 'failed', updated_at = NOW()
            WHERE user_id = %s AND provider = 'paystack' AND operation = 'checkout'
              AND status = 'started' AND paystack_submitted_at IS NULL
              AND checkout_url IS NULL AND provider_session_id IS NULL
            """,
            (user_id,),
        )
    rows = _paystack_open_checkouts(user_id)
    if not rows:
        return None
    checkout = rows[0]
    if len(rows) > 1:
        return {
            "status": "checkout_pending", "provider": "paystack", "checkout_status": "review",
            "reference": checkout["provider_reference"],
        }
    if checkout["idempotency_key"] == idempotency_key and checkout["request_fingerprint"] != fingerprint:
        raise HTTPException(409, detail={
            "error": "idempotency_key_reused", "message": "Start a new upgrade request for the changed plan.",
        })
    try:
        _refresh_paystack_checkout(checkout)
    except BillingProviderError:
        logger.warning("Paystack could not verify a saved checkout before an upgrade.")
        return {
            "status": "checkout_pending", "provider": "paystack", "checkout_status": "review",
            "reference": checkout["provider_reference"], "target_plan": checkout["target_plan"],
        }
    if checkout["status"] == "expired":
        return None
    view = _paystack_checkout_view(checkout)
    if view["checkout_url"] and checkout["request_fingerprint"] == fingerprint:
        return {**view, "success": True, "status": "checkout_ready"}
    return {
        **view,
        "status": "payment_pending" if view["status"] == "payment_pending" else "checkout_pending",
        "checkout_status": view["status"],
    }


def _mark_paystack_checkout_submitted(operation_id: int, plan_code: str, amount: int, timeout: int) -> None:
    with get_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE billing_checkout_sessions SET paystack_submitted_at = clock_timestamp(),
                paystack_plan_code = %s, paystack_amount_kobo = %s,
                paystack_timeout_seconds = %s, updated_at = NOW()
            WHERE id = %s AND status = 'started' AND paystack_submitted_at IS NULL
            """,
            (plan_code, amount, timeout, operation_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("Paystack checkout was already submitted.")


def _finish_billing_operation(
    operation_id: int,
    *,
    status: Literal["started", "created", "completed", "failed"],
    api_response: dict[str, Any],
    checkout_session: Any | None = None,
    provider_subscription_id: str | None = None,
    replaced_provider_subscription_id: str | None = None,
    provider_response: dict[str, Any] | None = None,
) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE billing_checkout_sessions
                SET status = CASE
                        WHEN status = 'completed' THEN 'completed'
                        ELSE %s
                    END,
                    checkout_url = COALESCE(%s, checkout_url),
                    provider_session_id = COALESCE(%s, provider_session_id),
                    provider_reference = COALESCE(%s, provider_reference),
                    provider_customer_id = COALESCE(%s, provider_customer_id),
                    provider_subscription_id = COALESCE(%s, provider_subscription_id),
                    replaced_provider_subscription_id = COALESCE(
                        %s, replaced_provider_subscription_id
                    ),
                    raw_response = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    status,
                    getattr(checkout_session, "checkout_url", None),
                    getattr(checkout_session, "provider_session_id", None),
                    getattr(checkout_session, "reference", None),
                    getattr(checkout_session, "provider_customer_id", None),
                    provider_subscription_id
                    or getattr(checkout_session, "provider_subscription_id", None),
                    replaced_provider_subscription_id,
                    Jsonb(
                        {
                            "api_response": _json_safe(api_response),
                            "provider_response": _json_safe(
                                provider_response
                                if provider_response is not None
                                else getattr(checkout_session, "raw", {}) or {}
                            ),
                        }
                    ),
                    operation_id,
                ),
            )


def _mark_subscription_change(
    subscription: dict[str, Any],
    *,
    status: str | None = None,
    current_period_end: Any = None,
    cancel_at_period_end: bool | None = None,
    pending_plan: BillingPlanName | None = None,
    effective_at: Any = None,
) -> None:
    table = (
        "organization_subscriptions"
        if subscription.get("scope") == "organization"
        else "user_subscriptions"
    )
    owner_column = "organization_id" if table == "organization_subscriptions" else "user_id"
    owner_value = (
        subscription.get("organization_id")
        if table == "organization_subscriptions"
        else subscription.get("user_id")
    )
    if owner_value is None:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE {table}
                SET status = COALESCE(%s, status),
                    current_period_end = COALESCE(%s, current_period_end),
                    cancel_at_period_end = COALESCE(%s, cancel_at_period_end),
                    pending_plan = %s,
                    plan_change_effective_at = %s,
                    updated_at = NOW()
                WHERE {owner_column} = %s
                """,
                (
                    status,
                    current_period_end,
                    cancel_at_period_end,
                    pending_plan,
                    effective_at,
                    owner_value,
                ),
            )


def _confirm_paystack_renewal_setting(provider: str, subscription_id: str, *, stopped: bool) -> None:
    if provider != "paystack":
        return
    state = retrieve_provider_subscription(provider, subscription_id)
    if state.cancel_at_period_end != stopped or (not stopped and state.status != "active"):
        raise BillingProviderError("Paystack has yet to confirm the change to renewal.")


class PaystackCheckoutConfirmationRequest(BaseModel):
    reference: str

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not PAYSTACK_REFERENCE_RE.fullmatch(normalized):
            raise ValueError("Invalid Paystack transaction reference.")
        return normalized


def _paystack_checkout_for_user(
    *,
    user_id: str,
    reference: str,
) -> dict[str, Any]:
    with get_db() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_plan, status, provider_reference, checkout_url,
                   paystack_submitted_at, paystack_timeout_seconds, paystack_settled_at,
                   raw_response->'paystack_recovery' AS recovery
            FROM billing_checkout_sessions
            WHERE provider = 'paystack' AND provider_reference = %s
              AND user_id = %s AND operation = 'checkout'
            ORDER BY id DESC
            LIMIT 2
            """,
            (reference, user_id),
        )
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "paystack_checkout_not_found",
                "message": "This Paystack checkout does not belong to the signed-in account.",
            },
        )

    if len(rows) != 1:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "paystack_verification_failed",
                "message": "This Paystack reference needs reconciliation.",
            },
        )
    return rows[0]


@router.post("/checkout-confirmations/paystack")
def confirm_paystack_checkout(
    payload: PaystackCheckoutConfirmationRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    return _confirm_paystack_checkout(payload, current_user)


def _confirm_paystack_checkout(
    payload: PaystackCheckoutConfirmationRequest, current_user: AuthenticatedUser,
) -> dict[str, Any]:
    """Confirm a Paystack redirect without trusting browser-supplied payment state."""
    try:
        _require_billing_schema_ready()
        with account_operation(current_user.user_id) as lock_conn:
            require_active_account(lock_conn, current_user.user_id)
            checkout = _paystack_checkout_for_user(
                user_id=current_user.user_id, reference=payload.reference,
            )
            payment = _refresh_paystack_checkout(checkout)
            outcome = paystack_payment_outcome(payment.status if payment else None)
            if outcome != "success":
                outstanding = _paystack_checkout_summary(current_user.user_id)
                if outstanding and (
                    outstanding["status"] == "review" or outstanding.get("reference") != payload.reference
                ):
                    status = "review"
                elif checkout["status"] == "expired" and outstanding is None:
                    status = "retry_ready"
                elif checkout["status"] == "cancelled":
                    status = "payment_refunded"
                else:
                    status = "failed" if outcome == "expired" else outcome
                return {
                    "confirmed": False,
                    "status": status,
                    "reference": payload.reference,
                    "paystack_checkout": outstanding,
                }
            with get_db() as conn:
                settlement = settle_paystack_checkout(conn, payment, checkout["id"])
        if not settlement.get("confirmed"):
            return {
                "confirmed": False,
                "status": settlement.get("reason", "pending"),
                "reference": payload.reference,
                "paystack_checkout": _paystack_checkout_summary(current_user.user_id),
            }

        entitlement = get_user_entitlement(current_user.user_id, ensure_subscription=False)
        subscription = _billing_subscription_record(entitlement, user_id=current_user.user_id)
        billing_state = build_billing_state(entitlement, current_user=current_user, subscription=subscription)
        return {"success": True, "confirmed": True, "status": "verified", "provider": "paystack",
                "reference": payload.reference, "target_plan": checkout["target_plan"],
                "seat_count": settlement["seat_count"], "duplicate": bool(settlement.get("duplicate")),
                "billing_state": billing_state}
    except HTTPException:
        raise
    except BillingProviderError as exc:
        raise HTTPException(503, detail={
            "error": "paystack_verification_failed",
            "message": "Paystack verification is temporarily unavailable. Check the same payment again shortly.",
        }) from exc
    except BillingWebhookProcessingError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "paystack_activation_failed",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_paystack_confirmation",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        reference_hash = hashlib.sha256(
            str(payload.reference or "").encode("utf-8")
        ).hexdigest()[:12]
        logger.exception(
            "Paystack checkout confirmation failed reference_hash=%s exception_type=%s",
            reference_hash,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "paystack_confirmation_failed",
                "message": (
                    "Your payment could not be activated automatically. "
                    "Do not make another payment. ReDOCX will reconcile the existing transaction."
                ),
            },
        ) from exc


class UpgradeIntentRequest(BaseModel):
    target_plan: BillingPlanName
    provider: BillingProviderName | None = None
    organization_name: str | None = None
    seat_count: int | None = None
    # Optional client hint such as "Africa/Lagos", "Europe/Paris", "en-NG", or "US".
    # It is used only when provider is omitted.
    region_hint: str | None = None

    @field_validator("target_plan")
    @classmethod
    def validate_target_plan(cls, value: str) -> str:
        return _normalize_billing_plan(value)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_checkout_provider(value)

    @field_validator("seat_count")
    @classmethod
    def validate_seat_count(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("seat_count must be an integer greater than or equal to 1.")
        return value

    @field_validator("organization_name")
    @classmethod
    def validate_organization_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_organization_name(value)

    @field_validator("region_hint")
    @classmethod
    def normalize_region_hint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized[:128] or None



@router.get("/plans")
def get_billing_plans(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        # Confirmation and webhooks own subscription mutations. Catalog requests
        # can read committed state while a checkout or account refresh is running.
        with get_db() as conn:
            require_active_account(conn, current_user.user_id)
            entitlement = get_user_entitlement(
                current_user.user_id, conn=conn, ensure_subscription=False,
            )
        subscription = _billing_subscription_record(
            entitlement,
            user_id=current_user.user_id,
        )
        return build_billing_state(
            entitlement,
            current_user=current_user,
            subscription=subscription,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_billing_state", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "billing_plans_failed",
                "message": "Could not load billing plans.",
            },
        ) from exc


@router.post("/upgrade-intents")
def create_upgrade_intent(
    payload: UpgradeIntentRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    with account_operation(current_user.user_id) as lock_conn:
        require_active_account(lock_conn, current_user.user_id)
        return _create_upgrade_intent(payload, request, current_user)


def _create_upgrade_intent(payload, request, current_user) -> dict[str, Any]:
    operation_id: int | None = None
    paystack_submitted = False
    try:
        _require_billing_schema_ready()
        idempotency_key = _require_idempotency_key(request)
        entitlement = get_user_entitlement(current_user.user_id)
        subscription = _billing_subscription_record(
            entitlement,
            user_id=current_user.user_id,
        )
        stored_plan = str((subscription or {}).get("plan") or "").strip().lower()
        current_plan = (
            _normalize_billing_plan(stored_plan)
            if stored_plan in {"personal", "business", "enterprise"}
            and _subscription_has_current_provider_obligation(subscription)
            else _current_plan_from_entitlement(entitlement)
        )
        _raise_if_subscription_period_locked(subscription, current_plan)

        target_plan = payload.target_plan
        if target_plan in {"business", "enterprise"}:
            requested_seats = 1 if payload.seat_count is None else payload.seat_count
            try:
                seat_count = validate_account_count(target_plan, requested_seats)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_seat_count",
                        "message": str(exc),
                        "target_plan": target_plan,
                    },
                ) from exc
        else:
            seat_count = 1

        provider = "paystack"
        if payload.provider and payload.provider != provider:
            raise HTTPException(409, detail={"error": "checkout_provider_unavailable", "message": "Checkout is available through Paystack."})

        if subscription and subscription.get("access_revoked_at"):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "billing_access_revoked",
                    "message": (
                        "Billing access is suspended because of a refund, dispute, "
                        "or chargeback. Resolve the provider case before changing plans."
                    ),
                    "reason": subscription.get("access_revocation_reason"),
                },
            )

        if target_plan == current_plan:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "already_on_plan",
                    "message": f"You are already on the {PLAN_CATALOG[target_plan]['name']} plan.",
                    "current_plan": current_plan,
                    "target_plan": target_plan,
                },
            )

        if target_plan not in UPGRADE_TARGETS_BY_CURRENT[current_plan]:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "upgrade_not_allowed",
                    "message": f"Upgrade from {PLAN_CATALOG[current_plan]['name']} to {PLAN_CATALOG[target_plan]['name']} is not available.",
                    "current_plan": current_plan,
                    "target_plan": target_plan,
                },
            )

        organization_name: str | None = None
        if target_plan in {"business", "enterprise"}:
            if subscription and subscription.get("scope") == "organization":
                if subscription.get("organization_role") != "owner":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "organization_owner_required",
                            "message": "Only the organization owner can upgrade an organization plan.",
                        },
                    )
                organization_name = normalize_organization_name(
                    str(subscription.get("organization_name") or "")
                )
                active_members = int(subscription.get("active_members") or 0)
                if seat_count < active_members:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "seat_count_below_active_members",
                            "message": (
                                "Selected seats cannot be lower than the organization's "
                                "current number of active members."
                            ),
                            "seat_count": seat_count,
                            "active_members": active_members,
                        },
                    )
            elif payload.organization_name is None:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "organization_name_required",
                        "message": "Organization name is required for Business and Enterprise subscriptions.",
                    },
                )
            else:
                organization_name = payload.organization_name

        operation_kind = "subscription_update" if current_plan != "free" else "checkout"
        fingerprint = _operation_fingerprint(
            {
                "operation": "upgrade",
                "user_id": current_user.user_id,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "provider": provider,
                "organization_id": (subscription or {}).get("organization_id"),
                "organization_name": organization_name,
                "seat_count": seat_count,
                "provider_subscription_id": (subscription or {}).get(
                    "provider_subscription_id"
                ),
            }
        )
        provider_reference = None
        if provider == "paystack" and operation_kind == "checkout":
            existing = _reserve_or_resume_paystack_checkout(
                current_user.user_id, fingerprint, idempotency_key,
            )
            if existing:
                return existing
            provider_reference = paystack_checkout_reference(target_plan, uuid4().hex)

        operation = _begin_billing_operation(
            current_user=current_user,
            provider=provider,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            operation=operation_kind,
            target_plan=target_plan,
            current_plan=current_plan,
            organization_id=(
                (subscription or {}).get("organization_id")
                if (subscription or {}).get("scope") == "organization"
                else None
            ),
            organization_name=organization_name,
            operation_metadata={"seat_count": seat_count},
            provider_reference=provider_reference,
        )
        operation_id = int(operation["id"])
        if operation.get("replayed"):
            return operation["api_response"]

        metadata = {
            "source": "redocx_billing_page",
            "entitlement_source": (subscription or {}).get("scope") or entitlement.source,
            "organization_role": (subscription or {}).get("organization_role") or entitlement.organization_role,
            "organization_name": organization_name,
            "checkout_provider": provider,
            "user_id": current_user.user_id,
            "target_plan": target_plan,
            "current_plan": current_plan,
            "seat_count": seat_count,
            "max_accounts": (
                seat_count if target_plan in {"business", "enterprise"} else None
            ),
        }
        if (subscription or {}).get("organization_id") is not None:
            metadata["organization_id"] = str(subscription["organization_id"])

        current_provider = str((subscription or {}).get("provider") or "").strip().lower()
        current_subscription_id = str(
            (subscription or {}).get("provider_subscription_id") or ""
        ).strip()
        has_current_provider_obligation = _subscription_has_current_provider_obligation(
            subscription
        )
        if not has_current_provider_obligation:
            # A terminal/elapsed provider record is historical. It remains in the
            # ledger for audit, but must not block a clean replacement checkout.
            current_provider = ""
            current_subscription_id = ""

        if current_plan != "free" and (not current_provider or not current_subscription_id):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "billing_subscription_reference_missing",
                    "message": (
                        "The current paid subscription is missing its provider reference. "
                        "ReDOCX will not create another recurring subscription until this is reconciled."
                    ),
                },
            )

        if current_subscription_id and current_provider == provider == "stripe":
            change = change_provider_subscription_plan(
                provider,
                current_subscription_id,
                target_plan=target_plan,
                metadata=metadata,
                quantity=seat_count,
                idempotency_key=idempotency_key,
                effective_at_period_end=False,
            )
            if subscription:
                _mark_subscription_change(
                    subscription,
                    pending_plan=target_plan,
                    effective_at=change.effective_at,
                    cancel_at_period_end=False,
                )
            response_payload = {
                "success": True,
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "seat_count": seat_count,
                "checkout_url": None,
                "provider_subscription_id": change.provider_subscription_id,
                "subscription_updated": True,
                "message": (
                    f"Your existing Stripe subscription was updated to "
                    f"{PLAN_CATALOG[target_plan]['name']}."
                ),
            }
            _finish_billing_operation(
                operation_id,
                status="completed",
                api_response=response_payload,
                provider_subscription_id=change.provider_subscription_id,
                replaced_provider_subscription_id=current_subscription_id,
                provider_response=change.raw,
            )
            return response_payload

        if current_subscription_id and current_provider != provider:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "provider_switch_requires_cancellation",
                    "message": (
                        f"This subscription is managed by {_provider_display_name(current_provider)}. "
                        "Cancel its renewal first and wait for the paid period to end before starting a subscription with another provider."
                    ),
                    "current_provider": current_provider,
                    "requested_provider": provider,
                    "current_period_end": (subscription or {}).get("current_period_end"),
                },
            )

        if current_subscription_id and current_provider == provider == "paystack":
            if (
                subscription
                and subscription.get("cancel_at_period_end")
                and subscription.get("pending_plan") == target_plan
            ):
                cancellation_period_end = subscription.get("current_period_end")
            else:
                cancellation = cancel_provider_subscription(
                    current_provider,
                    current_subscription_id,
                )
                _confirm_paystack_renewal_setting(current_provider, current_subscription_id, stopped=True)
                cancellation_period_end = cancellation.current_period_end or (
                    subscription or {}
                ).get("current_period_end")
                if subscription:
                    _mark_subscription_change(
                        subscription,
                        status="cancelled",
                        current_period_end=cancellation_period_end,
                        cancel_at_period_end=True,
                        pending_plan=target_plan,
                        effective_at=cancellation_period_end,
                    )

            response_payload = {
                "success": True,
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "seat_count": seat_count,
                "checkout_url": None,
                "provider_subscription_id": current_subscription_id,
                "upgrade_scheduled": True,
                "status": "scheduled",
                "requires_checkout_at_period_end": True,
                "current_period_end": cancellation_period_end,
                "message": (
                    "Paystack does not provide a safe per-customer plan replacement API. "
                    f"Renewal has been stopped to prevent overlapping charges. After the paid period ends, start {PLAN_CATALOG[target_plan]['name']} checkout from this page."
                ),
            }
            _finish_billing_operation(
                operation_id,
                status="completed",
                api_response=response_payload,
                provider_subscription_id=current_subscription_id,
                replaced_provider_subscription_id=current_subscription_id,
            )
            return response_payload

        replaced_subscription_id: str | None = None
        cancellation = None
        if current_subscription_id:
            cancellation = cancel_provider_subscription(
                current_provider,
                current_subscription_id,
            )
            replaced_subscription_id = current_subscription_id
            if subscription:
                _mark_subscription_change(
                    subscription,
                    status="cancelled",
                    current_period_end=cancellation.current_period_end,
                    cancel_at_period_end=True,
                    pending_plan=target_plan,
                    effective_at=cancellation.current_period_end,
                )

        try:
            checkout_request = BillingCheckoutRequest(
                    user_id=current_user.user_id,
                    email=_current_user_email(current_user),
                    target_plan=target_plan,
                    current_plan=current_plan,
                    organization_id=(
                        (subscription or {}).get("organization_id")
                        if (subscription or {}).get("scope") == "organization"
                        else None
                    ),
                    organization_name=organization_name,
                    seat_count=seat_count,
                    idempotency_key=idempotency_key,
                    metadata=metadata,
                    provider_reference=provider_reference,
            )
            if provider_reference:
                paystack = PaystackBillingProvider()
                plan_code = paystack.checkout_plan_code(checkout_request)
                timeout = paystack.payment_session_timeout()
                if timeout <= 0:
                    raise CheckoutNotConfiguredError("Paystack payment session expiry must be enabled.")
                _mark_paystack_checkout_submitted(
                    operation_id, plan_code, expected_checkout_amount_kobo(target_plan, seat_count), timeout
                )
                paystack_submitted = True
                checkout_request = replace(checkout_request, paystack_plan_code=plan_code)
            checkout_session = create_checkout_session(checkout_request, provider_name=provider)
        except Exception:
            if cancellation is not None:
                try:
                    resumed = resume_provider_subscription(
                        current_provider,
                        current_subscription_id,
                    )
                    if subscription:
                        _mark_subscription_change(
                            subscription,
                            status="active",
                            current_period_end=resumed.current_period_end,
                            cancel_at_period_end=False,
                            pending_plan=None,
                            effective_at=None,
                        )
                except Exception:
                    pass
            raise

        response_payload = {
            "success": True,
            "status": "checkout_ready",
            "provider": checkout_session.provider,
            "current_plan": current_plan,
            "target_plan": target_plan,
            "seat_count": seat_count,
            "checkout_url": checkout_session.checkout_url,
            "provider_session_id": checkout_session.provider_session_id,
            "provider_customer_id": checkout_session.provider_customer_id,
            "provider_subscription_id": checkout_session.provider_subscription_id,
            "reference": checkout_session.reference,
            "replaces_provider_subscription_id": replaced_subscription_id,
            "message": (
                f"Continue to {CHECKOUT_PROVIDER_CATALOG[provider]['name']} checkout for "
                f"{PLAN_CATALOG[target_plan]['name']}. The previous subscription has been "
                "stopped from renewing before the replacement checkout was created."
                if replaced_subscription_id
                else f"Continue to {CHECKOUT_PROVIDER_CATALOG[provider]['name']} checkout for {PLAN_CATALOG[target_plan]['name']}."
            ),
        }
        _finish_billing_operation(
            operation_id,
            status="created",
            api_response=response_payload,
            checkout_session=checkout_session,
            replaced_provider_subscription_id=replaced_subscription_id,
        )
        return response_payload
    except HTTPException as exc:
        if operation_id is not None:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            _finish_billing_operation(
                operation_id,
                status="failed",
                api_response={"success": False, **detail},
            )
        raise
    except CheckoutNotConfiguredError as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="failed",
                api_response={"success": False, "message": str(exc)},
            )
        provider = payload.provider or "paystack"
        return {
            "success": False,
            "provider": provider,
            "current_plan": None,
            "target_plan": payload.target_plan,
            "checkout_url": None,
            "message": str(exc),
        }
    except BillingProviderError as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="started" if paystack_submitted else "failed",
                api_response={"success": False, "message": "Paystack checkout requires verification."}
                if paystack_submitted else {"success": False, "message": str(exc)},
            )
        if paystack_submitted:
            return {
                "status": "checkout_pending", "provider": "paystack",
                "reference": provider_reference, "target_plan": payload.target_plan,
            }
        raise HTTPException(
            status_code=502,
            detail={
                "error": "checkout_provider_failed",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="started" if paystack_submitted else "failed",
                api_response={"success": False, "message": str(exc)},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_billing_request", "message": str(exc)},
        ) from exc
    except Exception as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="started" if paystack_submitted else "failed",
                api_response={"success": False, "message": "Could not create upgrade intent."},
            )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "upgrade_intent_failed",
                "message": "Could not create upgrade intent.",
            },
        ) from exc


class SubscriptionActionRequest(BaseModel):
    action: Literal["cancel", "resume", "downgrade"]
    target_plan: BillingPlanName | None = None
    scope: Literal["personal", "current"] = "current"

    @field_validator("target_plan")
    @classmethod
    def validate_target_plan(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_billing_plan(value)


@router.post("/subscription-actions")
def manage_subscription(
    payload: SubscriptionActionRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    with account_operation(current_user.user_id) as lock_conn:
        require_active_account(lock_conn, current_user.user_id)
        return _manage_subscription(payload, request, current_user)


def _manage_subscription(payload, request, current_user) -> dict[str, Any]:
    operation_id: int | None = None
    try:
        idempotency_key = _require_idempotency_key(request)
        entitlement = get_user_entitlement(current_user.user_id)
        subscription = _billing_subscription_record(
            entitlement,
            user_id=current_user.user_id,
            personal_only=payload.scope == "personal",
        )
        if payload.scope == "personal" and (
            not subscription or subscription.get("plan") != "personal"
            or subscription.get("provider") != "paystack" or payload.action == "downgrade"
        ):
            raise HTTPException(409, detail="There is no Personal Paystack subscription for this action.")
        stored_plan = str((subscription or {}).get("plan") or "").strip().lower()
        current_plan = (
            _normalize_billing_plan(stored_plan)
            if stored_plan in {"personal", "business", "enterprise"}
            and _subscription_has_current_provider_obligation(subscription)
            else _current_plan_from_entitlement(entitlement)
        )
        if (
            not subscription
            or current_plan == "free"
            or not _subscription_has_current_provider_obligation(subscription)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "paid_subscription_required",
                    "message": "There is no paid subscription to manage.",
                },
            )
        if (
            subscription.get("scope") == "organization"
            and subscription.get("organization_role") != "owner"
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "organization_owner_required",
                    "message": "Only the organization owner can manage the organization subscription.",
                },
            )

        provider = str((subscription or {}).get("provider") or "").strip().lower()
        provider_subscription_id = str(
            (subscription or {}).get("provider_subscription_id") or ""
        ).strip()
        if provider not in CHECKOUT_PROVIDER_ORDER or not provider_subscription_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "billing_subscription_reference_missing",
                    "message": "The paid subscription is missing a supported provider reference.",
                },
            )

        if subscription.get("access_revoked_at") and payload.action != "cancel":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "billing_access_revoked",
                    "message": (
                        "Billing access is suspended because of a refund, dispute, "
                        "or chargeback. Cancellation remains available to stop future billing."
                    ),
                    "reason": subscription.get("access_revocation_reason"),
                },
            )

        target_plan = payload.target_plan
        if payload.action == "cancel":
            target_plan = "free"
        elif payload.action == "resume":
            target_plan = current_plan
        elif target_plan is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "target_plan_required",
                    "message": "target_plan is required for a downgrade.",
                },
            )

        if payload.action == "downgrade":
            _raise_if_subscription_period_locked(subscription, current_plan)
            assert target_plan is not None
            if PLAN_RANK[target_plan] >= PLAN_RANK[current_plan]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "downgrade_not_allowed",
                        "message": "The requested target is not lower than the current plan.",
                    },
                )

        # State-based idempotency covers retries that arrive after the provider
        # mutation succeeded and the local subscription row was already updated.
        if payload.action == "cancel" and subscription.get("cancel_at_period_end"):
            return {
                "success": True,
                "action": "cancel",
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": "free",
                "cancel_at_period_end": True,
                "current_period_end": subscription.get("current_period_end"),
                "message": "Subscription renewal is already cancelled.",
            }
        if payload.action == "resume" and not subscription.get("cancel_at_period_end"):
            return {
                "success": True,
                "action": "resume",
                "provider": provider,
                "current_plan": current_plan,
                "cancel_at_period_end": False,
                "message": "Subscription renewal is already active.",
            }
        if (
            payload.action == "downgrade"
            and subscription.get("pending_plan") == target_plan
            and subscription.get("plan_change_effective_at") is not None
        ):
            return {
                "success": True,
                "action": "downgrade",
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "effective_at": subscription.get("plan_change_effective_at"),
                "requires_checkout_at_period_end": provider == "paystack" and target_plan != "free",
                "message": "This downgrade is already scheduled.",
            }

        fingerprint = _operation_fingerprint(
            {
                "operation": payload.action,
                "user_id": current_user.user_id,
                "provider": provider,
                "provider_subscription_id": provider_subscription_id,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "subscription_version": (subscription or {}).get("updated_at"),
            }
        )
        operation = _begin_billing_operation(
            current_user=current_user,
            provider=provider,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            operation=f"subscription_{payload.action}",
            target_plan=target_plan or current_plan,
            current_plan=current_plan,
            organization_id=subscription.get("organization_id"),
            organization_name=subscription.get("organization_name"),
        )
        operation_id = int(operation["id"])
        if operation.get("replayed"):
            return operation["api_response"]

        if payload.action == "resume":
            change = resume_provider_subscription(provider, provider_subscription_id)
            _confirm_paystack_renewal_setting(provider, provider_subscription_id, stopped=False)
            _mark_subscription_change(
                subscription or {},
                status="active",
                current_period_end=change.current_period_end,
                cancel_at_period_end=False,
                pending_plan=None,
                effective_at=None,
            )
            response_payload = {
                "success": True,
                "action": "resume",
                "provider": provider,
                "current_plan": current_plan,
                "cancel_at_period_end": False,
                "message": "Subscription renewal has been resumed.",
            }
            _finish_billing_operation(
                operation_id,
                status="completed",
                api_response=response_payload,
                provider_subscription_id=change.provider_subscription_id,
                provider_response=change.raw,
            )
            return response_payload

        if payload.action == "cancel" or target_plan == "free":
            change = cancel_provider_subscription(provider, provider_subscription_id)
            _confirm_paystack_renewal_setting(provider, provider_subscription_id, stopped=True)
            _mark_subscription_change(
                subscription or {},
                status="cancelled",
                current_period_end=change.current_period_end,
                cancel_at_period_end=True,
                pending_plan="free",
                effective_at=change.current_period_end,
            )
            response_payload = {
                "success": True,
                "action": payload.action,
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": "free",
                "cancel_at_period_end": True,
                "current_period_end": change.current_period_end,
                "message": "Renewal has been stopped. Paid access remains available until the current paid period ends.",
            }
            _finish_billing_operation(
                operation_id,
                status="completed",
                api_response=response_payload,
                provider_subscription_id=change.provider_subscription_id,
                provider_response=change.raw,
            )
            return response_payload

        assert target_plan is not None
        if provider == "stripe":
            change = change_provider_subscription_plan(
                provider,
                provider_subscription_id,
                target_plan=target_plan,
                metadata={
                    "source": "redocx_billing_page",
                    "user_id": current_user.user_id,
                    "current_plan": current_plan,
                    "target_plan": target_plan,
                    "organization_id": subscription.get("organization_id"),
                    "organization_name": subscription.get("organization_name"),
                    "seat_count": 1 if target_plan == "personal" else subscription.get("max_accounts"),
                    "max_accounts": (
                        subscription.get("max_accounts")
                        if target_plan in {"business", "enterprise"}
                        else None
                    ),
                },
                quantity=(1 if target_plan == "personal" else subscription.get("max_accounts")),
                idempotency_key=idempotency_key,
                effective_at_period_end=True,
            )
            _mark_subscription_change(
                subscription or {},
                pending_plan=target_plan,
                effective_at=change.effective_at or change.current_period_end,
                cancel_at_period_end=False,
            )
            response_payload = {
                "success": True,
                "action": "downgrade",
                "provider": provider,
                "current_plan": current_plan,
                "target_plan": target_plan,
                "effective_at": change.effective_at or change.current_period_end,
                "message": f"The downgrade to {PLAN_CATALOG[target_plan]['name']} is scheduled for the end of the current paid period.",
            }
            _finish_billing_operation(
                operation_id,
                status="completed",
                api_response=response_payload,
                provider_subscription_id=change.provider_subscription_id,
                provider_response=change.raw,
            )
            return response_payload

        change = cancel_provider_subscription(provider, provider_subscription_id)
        _confirm_paystack_renewal_setting(provider, provider_subscription_id, stopped=True)
        _mark_subscription_change(
            subscription or {},
            status="cancelled",
            current_period_end=change.current_period_end,
            cancel_at_period_end=True,
            pending_plan=target_plan,
            effective_at=change.current_period_end,
        )
        response_payload = {
            "success": True,
            "action": "downgrade",
            "provider": provider,
            "current_plan": current_plan,
            "target_plan": target_plan,
            "effective_at": change.current_period_end,
            "requires_checkout_at_period_end": True,
            "message": (
                f"The current Paystack subscription will not renew. After the paid period ends, "
                f"start {PLAN_CATALOG[target_plan]['name']} checkout from this page."
            ),
        }
        _finish_billing_operation(
            operation_id,
            status="completed",
            api_response=response_payload,
            provider_response=change.raw,
        )
        return response_payload
    except HTTPException as exc:
        if operation_id is not None:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            _finish_billing_operation(
                operation_id,
                status="failed",
                api_response={"success": False, **detail},
            )
        raise
    except BillingProviderError as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="failed",
                api_response={"success": False, "message": str(exc)},
            )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "subscription_change_failed",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_billing_request", "message": str(exc)},
        ) from exc
    except Exception as exc:
        if operation_id is not None:
            _finish_billing_operation(
                operation_id,
                status="failed",
                api_response={"success": False, "message": "Could not update the subscription."},
            )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "subscription_change_failed",
                "message": "Could not update the subscription.",
            },
        ) from exc
