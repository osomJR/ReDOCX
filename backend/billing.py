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

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, field_validator

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.billing_provider import (
    BillingCheckoutRequest,
    BillingProviderError,
    CheckoutNotConfiguredError,
    cancel_provider_subscription,
    change_provider_subscription_plan,
    create_checkout_session,
    normalize_provider_name,
    resume_provider_subscription,
    verify_provider_transaction,
)
from backend.database import get_db
from backend.routes.billing_webhooks import (
    BillingWebhookProcessingError,
    process_verified_billing_event,
)
from backend.subscriptions import (
    UserEntitlement,
    get_user_entitlement,
    normalize_organization_name,
    normalize_plan,
)


router = APIRouter(prefix="/billing", tags=["billing-v1"])
logger = logging.getLogger(__name__)

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
BillingAction = Literal["current", "upgrade", "downgrade", "none"]
BillingProviderName = Literal["paystack", "stripe"]

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
PAYSTACK_RECONCILIATION_LOOKBACK_DAYS = min(
    30,
    max(1, int(os.getenv("PAYSTACK_RECONCILIATION_LOOKBACK_DAYS", "7"))),
)
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
        "account_count_label": "2–19 users",
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
        "account_count_label": "20+ users",
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
        "summary": "Recommended for Nigeria and African cards, bank transfers, USSD, and local rails.",
        "region_label": "Nigeria / Africa",
    },
    "stripe": {
        "name": "Stripe",
        "summary": "Recommended for US and European cards and international checkout.",
        "region_label": "US / Europe",
    },
}

AFRICAN_COUNTRY_CODES = {
    "DZ", "AO", "BJ", "BW", "BF", "BI", "CV", "CM", "CF", "TD", "KM", "CG",
    "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM", "GH", "GN",
    "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR", "MU", "MA", "MZ",
    "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL", "SO", "ZA", "SS", "SD",
    "TZ", "TG", "TN", "UG", "ZM", "ZW",
}
STRIPE_DEFAULT_COUNTRY_CODES = {
    "US", "CA", "GB", "IE", "FR", "DE", "ES", "IT", "NL", "BE", "PT", "AT",
    "CH", "SE", "NO", "DK", "FI", "PL", "CZ", "GR", "RO", "BG", "HR", "HU",
    "LU", "LT", "LV", "EE", "SK", "SI", "CY", "MT",
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


def _configured_default_provider() -> BillingProviderName | None:
    raw = os.getenv("BILLING_PROVIDER", "").strip()
    if not raw:
        return None
    try:
        return _normalize_checkout_provider(raw)
    except ValueError:
        return None


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
        has_price = bool(
            _env(f"PAYSTACK_{plan.upper()}_PLAN_CODE")
            or _env(f"PAYSTACK_{plan.upper()}_AMOUNT_KOBO")
        )
        has_redirect = bool(_env("PAYSTACK_CALLBACK_URL") or _env("BILLING_SUCCESS_URL"))
        return bool(_env("PAYSTACK_SECRET_KEY") and has_redirect and has_price)

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


def _country_code_candidates(*values: Any) -> list[str]:
    candidates: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip().replace("_", "-")
        if not text:
            continue

        pieces = [piece.strip().upper() for piece in text.split("-") if piece.strip()]
        if len(pieces) >= 2 and len(pieces[-1]) == 2:
            candidates.append(pieces[-1])
        if len(text) == 2:
            candidates.append(text.upper())
    return candidates


def _claims_region_values(current_user: AuthenticatedUser | None) -> list[Any]:
    if current_user is None:
        return []
    claims = current_user.claims or {}
    app_metadata = claims.get("app_metadata") if isinstance(claims.get("app_metadata"), dict) else {}
    user_metadata = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
    return [
        claims.get("country"),
        claims.get("country_code"),
        claims.get("locale"),
        claims.get("lang"),
        claims.get("language"),
        app_metadata.get("country"),
        app_metadata.get("country_code"),
        user_metadata.get("country"),
        user_metadata.get("country_code"),
        user_metadata.get("locale"),
    ]


def _recommended_provider(
    *,
    current_user: AuthenticatedUser | None = None,
    region_hint: str | None = None,
    current_plan: BillingPlanName = "free",
) -> BillingProviderName:
    configured_default = _configured_default_provider()
    if configured_default:
        return configured_default

    values = [region_hint, *_claims_region_values(current_user)]
    normalized_region_text = " ".join(str(value).strip().lower() for value in values if value)
    country_codes = _country_code_candidates(*values)

    if "africa" in normalized_region_text or any(code in AFRICAN_COUNTRY_CODES for code in country_codes):
        return "paystack"

    if "europe" in normalized_region_text or any(code in STRIPE_DEFAULT_COUNTRY_CODES for code in country_codes):
        return "stripe"

    upgrade_targets = UPGRADE_TARGETS_BY_CURRENT.get(current_plan, set())
    paystack_ready = _provider_configured_for_any_upgrade("paystack", upgrade_targets)
    stripe_ready = _provider_configured_for_any_upgrade("stripe", upgrade_targets)

    if stripe_ready:
        return "stripe"
    if paystack_ready:
        return "paystack"
    return "stripe"


def _provider_options(current_plan: BillingPlanName, recommended_provider: BillingProviderName) -> list[dict[str, Any]]:
    upgrade_targets = UPGRADE_TARGETS_BY_CURRENT[current_plan]
    options: list[dict[str, Any]] = []
    for provider in CHECKOUT_PROVIDER_ORDER:
        catalog = CHECKOUT_PROVIDER_CATALOG[provider]
        options.append(
            {
                "key": provider,
                "name": catalog["name"],
                "summary": catalog["summary"],
                "region_label": catalog["region_label"],
                "recommended": provider == recommended_provider,
                "configured": _provider_configured_for_any_upgrade(provider, upgrade_targets),
            }
        )
    return options


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
    cancelled subscription remains current until its paid period ends. Inactive
    and fully elapsed cancelled rows are historical and may be replaced.
    """
    if not subscription or not str(subscription.get("provider_subscription_id") or "").strip():
        return False

    status = str(subscription.get("status") or "").strip().lower()
    if status in {"active", "past_due"}:
        return True
    if status == "cancelled":
        period_end = subscription.get("current_period_end")
        # Missing period data is ambiguous; block a replacement rather than risk
        # a second live provider subscription. Reconciliation can resolve it.
        return period_end is None or _future_timestamp(period_end)
    return False


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
            f"You can schedule a change from {PLAN_CATALOG[current_plan]['name']} "
            f"to {PLAN_CATALOG[plan]['name']}."
        )
    return "This plan is visible for comparison, but no upgrade action is available from your current plan."


def build_billing_state(
    entitlement: UserEntitlement,
    *,
    current_user: AuthenticatedUser | None = None,
    region_hint: str | None = None,
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
    persisted_provider = str((subscription or {}).get("provider") or "").strip().lower()
    recommended_provider = (
        persisted_provider
        if _subscription_has_current_provider_obligation(subscription)
        and persisted_provider in CHECKOUT_PROVIDER_ORDER
        else _recommended_provider(
            current_user=current_user,
            region_hint=region_hint,
            current_plan=current_plan,
        )
    )

    can_manage_subscription = not (
        subscription
        and subscription.get("scope") == "organization"
        and subscription.get("organization_role") != "owner"
    )
    access_revoked = bool(subscription and subscription.get("access_revoked_at"))
    can_change_plan = can_manage_subscription and not access_revoked

    cards: list[dict[str, Any]] = []
    for plan in visible_plans:
        catalog = PLAN_CATALOG[plan]
        action = _plan_action(current_plan, plan)
        provider_checkout_configured = {
            provider: _checkout_configured_for_provider(plan, provider)
            for provider in CHECKOUT_PROVIDER_ORDER
        }
        cards.append(
            {
                "key": plan,
                "name": catalog["name"],
                "summary": catalog["summary"],
                "price_label": catalog["price_label"],
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
                    else _plan_reason(current_plan, plan, action)
                ),
                "checkout_configured": any(provider_checkout_configured.values()),
                "provider_checkout_configured": provider_checkout_configured,
            }
        )

    return {
        "current_plan": current_plan,
        "current_plan_name": PLAN_CATALOG[current_plan]["name"],
        "provider": recommended_provider,
        "recommended_provider": recommended_provider,
        "providers": _provider_options(current_plan, recommended_provider),
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


def _current_user_email(current_user: AuthenticatedUser) -> str | None:
    email = current_user.claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


def _billing_subscription_record(
    entitlement: UserEntitlement,
    *,
    user_id: str,
) -> dict[str, Any] | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            if entitlement.source == "organization" and entitlement.organization_id:
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
                        om.role AS organization_role
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
                        NULL::TEXT AS organization_role
                    FROM user_subscriptions
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()

                # Suspended/expired organization entitlements normalize to Free.
                # Still find an owned provider subscription so the customer can
                # cancel it and cannot accidentally create a second checkout.
                if (
                    row is None
                    or str(row[2] or "").strip().lower() == "free"
                    or not str(row[6] or "").strip()
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
                            'owner'::TEXT AS organization_role
                        FROM organization_subscriptions os
                        JOIN organizations o ON o.id = os.organization_id
                        WHERE o.owner_user_id = %s
                          AND os.provider IN ('stripe', 'paystack')
                          AND os.provider_subscription_id IS NOT NULL
                          AND os.status IN ('active', 'past_due', 'cancelled')
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
        raw_response = row[3] if isinstance(row[3], dict) else {}
        api_response = (
            raw_response.get("api_response")
            if isinstance(raw_response, dict)
            else None
        )
        if row[2] in {"created", "completed"} and isinstance(api_response, dict):
            return {
                "id": int(row[0]),
                "replayed": True,
                "api_response": api_response,
            }
        error = (
            "billing_operation_failed"
            if row[2] == "failed"
            else "billing_operation_in_progress"
        )
        message = (
            "This billing operation previously failed. Start a new attempt."
            if row[2] == "failed"
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
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'started', %s, %s, %s, %s)
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
                    Jsonb({"source": "redocx_billing_page"}),
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


def _finish_billing_operation(
    operation_id: int,
    *,
    status: Literal["created", "completed", "failed"],
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
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, target_plan, current_plan, email,
                       organization_id, organization_name, status
                FROM billing_checkout_sessions
                WHERE provider = 'paystack'
                  AND provider_reference = %s
                  AND user_id = %s
                  AND operation = 'checkout'
                ORDER BY id DESC
                LIMIT 1
                """,
                (reference, user_id),
            )
            row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "paystack_checkout_not_found",
                "message": "This Paystack checkout does not belong to the signed-in account.",
            },
        )

    return {
        "id": int(row[0]),
        "target_plan": _normalize_billing_plan(str(row[1])),
        "current_plan": _normalize_billing_plan(str(row[2] or "free")),
        "email": str(row[3] or "").strip().lower() or None,
        "organization_id": int(row[4]) if row[4] is not None else None,
        "organization_name": row[5],
        "status": row[6],
    }


def _assert_verified_paystack_checkout(
    *,
    checkout: dict[str, Any],
    event: Any,
    current_user: AuthenticatedUser,
) -> None:
    target_plan = checkout["target_plan"]
    if event.action != "activate" or str(event.status or "").lower() != "success":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "paystack_payment_not_confirmed",
                "message": "Paystack has not confirmed this payment as successful yet.",
                "payment_status": event.status,
            },
        )

    if event.user_id and str(event.user_id).strip() != current_user.user_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "paystack_checkout_identity_mismatch",
                "message": "Paystack checkout identity does not match the signed-in account.",
            },
        )

    if event.plan and str(event.plan).strip().lower() != target_plan:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "paystack_checkout_plan_mismatch",
                "message": "The verified Paystack payment does not match the requested plan.",
            },
        )

    checkout_email = checkout.get("email")
    event_email = str(event.email or "").strip().lower()
    if checkout_email and event_email and checkout_email != event_email:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "paystack_checkout_email_mismatch",
                "message": "The verified Paystack customer does not match this checkout.",
            },
        )

@router.post("/checkout-confirmations/paystack")
def confirm_paystack_checkout(
    payload: PaystackCheckoutConfirmationRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    """Confirm a Paystack redirect without trusting browser-supplied payment state."""
    try:
        checkout = _paystack_checkout_for_user(
            user_id=current_user.user_id,
            reference=payload.reference,
        )
        verified_event = verify_provider_transaction("paystack", payload.reference)
        _assert_verified_paystack_checkout(
            checkout=checkout,
            event=verified_event,
            current_user=current_user,
        )

        authoritative_event = replace(
            verified_event,
            user_id=current_user.user_id,
            email=checkout.get("email") or verified_event.email,
            plan=checkout["target_plan"],
            organization_id=checkout.get("organization_id"),
            organization_name=checkout.get("organization_name"),
            provider_reference=payload.reference,
        )
        processing = process_verified_billing_event(authoritative_event)

        entitlement = get_user_entitlement(current_user.user_id)
        subscription = _billing_subscription_record(
            entitlement,
            user_id=current_user.user_id,
        )
        billing_state = build_billing_state(
            entitlement,
            current_user=current_user,
            subscription=subscription,
        )
        if billing_state["current_plan"] != checkout["target_plan"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "paystack_activation_not_reflected",
                    "message": (
                        "Paystack confirmed the payment, but the paid entitlement "
                        "could not be activated for this account."
                    ),
                },
            )

        return {
            "success": True,
            "confirmed": True,
            "provider": "paystack",
            "reference": payload.reference,
            "target_plan": checkout["target_plan"],
            "current_plan": billing_state["current_plan"],
            "billing_state": billing_state,
            "duplicate": bool(processing.get("duplicate")),
            "message": (
                f"Payment verified. Your {PLAN_CATALOG[checkout['target_plan']]['name']} "
                "plan is active."
            ),
        }
    except HTTPException:
        raise
    except BillingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "paystack_verification_failed",
                "message": str(exc),
            },
        ) from exc
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
        raise HTTPException(
            status_code=500,
            detail={
                "error": "paystack_confirmation_failed",
                "message": "Could not confirm the Paystack payment.",
            },
        ) from exc


def _reconcile_recent_paystack_checkout(
    current_user: AuthenticatedUser,
) -> bool:
    """Repair a recent successful checkout when its webhook was delayed.

    This runs only while the account still resolves to Free and only against a
    server-created, incomplete checkout belonging to that authenticated user.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT provider_reference
                FROM billing_checkout_sessions
                WHERE provider = 'paystack'
                  AND user_id = %s
                  AND operation = 'checkout'
                  AND status IN ('started', 'created')
                  AND provider_reference IS NOT NULL
                  AND created_at >= NOW() - make_interval(days => %s)
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (
                    current_user.user_id,
                    PAYSTACK_RECONCILIATION_LOOKBACK_DAYS,
                ),
            )
            row = cur.fetchone()

    if row is None:
        return False

    try:
        confirm_paystack_checkout(
            PaystackCheckoutConfirmationRequest(reference=str(row[0])),
            current_user,
        )
        return True
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("error") or "")
        if code != "paystack_payment_not_confirmed":
            logger.warning(
                "Recent Paystack checkout reconciliation failed status=%s code=%s",
                exc.status_code,
                code or "unknown",
            )
        return False
    except Exception:
        logger.exception("Recent Paystack checkout reconciliation failed unexpectedly.")
        return False


class UpgradeIntentRequest(BaseModel):
    target_plan: BillingPlanName
    provider: BillingProviderName | None = None
    organization_name: str | None = None
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
        entitlement = get_user_entitlement(current_user.user_id)
        if entitlement.plan == "free" and _reconcile_recent_paystack_checkout(
            current_user
        ):
            entitlement = get_user_entitlement(current_user.user_id)
        subscription = _billing_subscription_record(
            entitlement,
            user_id=current_user.user_id,
        )
        return build_billing_state(
            entitlement,
            current_user=current_user,
            subscription=subscription,
        )
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
    operation_id: int | None = None
    try:
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
        target_plan = payload.target_plan
        provider = payload.provider or _recommended_provider(
            current_user=current_user,
            region_hint=payload.region_hint,
            current_plan=current_plan,
        )

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
                "provider_subscription_id": (subscription or {}).get(
                    "provider_subscription_id"
                ),
            }
        )
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
                "checkout_url": None,
                "provider_subscription_id": current_subscription_id,
                "upgrade_scheduled": True,
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
            checkout_session = create_checkout_session(
                BillingCheckoutRequest(
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
                    idempotency_key=idempotency_key,
                    metadata=metadata,
                ),
                provider_name=provider,
            )
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
            "provider": checkout_session.provider,
            "current_plan": current_plan,
            "target_plan": target_plan,
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
        provider = payload.provider or "stripe"
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
                status="failed",
                api_response={"success": False, "message": str(exc)},
            )
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
                status="failed",
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
                status="failed",
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
    operation_id: int | None = None
    try:
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
                },
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
