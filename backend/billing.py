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

import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, field_validator

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.billing_provider import (
    BillingCheckoutRequest,
    BillingProviderError,
    CheckoutNotConfiguredError,
    create_checkout_session,
    normalize_provider_name,
)
from backend.database import get_db
from backend.subscriptions import (
    UserEntitlement,
    get_user_entitlement,
    normalize_organization_name,
    normalize_plan,
)


router = APIRouter(prefix="/billing", tags=["billing-v1"])

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
BillingAction = Literal["current", "upgrade", "none"]
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
    "personal": ["personal", "business", "enterprise"],
    "business": ["personal", "business", "enterprise"],
    "enterprise": ["personal", "business", "enterprise"],
}

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


def _plan_action(current_plan: BillingPlanName, plan: BillingPlanName) -> BillingAction:
    if plan == current_plan:
        return "current"
    if plan in UPGRADE_TARGETS_BY_CURRENT[current_plan]:
        return "upgrade"
    return "none"


def _plan_reason(current_plan: BillingPlanName, plan: BillingPlanName, action: BillingAction) -> str:
    if action == "current":
        return "This is your current plan."
    if action == "upgrade":
        return f"You can upgrade from {PLAN_CATALOG[current_plan]['name']} to {PLAN_CATALOG[plan]['name']}."
    if current_plan == "business" and plan == "personal":
        return "Business users can view Personal, but this is not an upgrade path."
    if current_plan == "enterprise" and plan in {"personal", "business"}:
        return "Enterprise is already the highest plan, so no upgrade action is available."
    return "This plan is visible for comparison, but no upgrade action is available from your current plan."


def build_billing_state(
    entitlement: UserEntitlement,
    *,
    current_user: AuthenticatedUser | None = None,
    region_hint: str | None = None,
) -> dict[str, Any]:
    current_plan = _current_plan_from_entitlement(entitlement)
    visible_plans = VISIBLE_PLANS_BY_CURRENT[current_plan]
    recommended_provider = _recommended_provider(
        current_user=current_user,
        region_hint=region_hint,
        current_plan=current_plan,
    )

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
                "can_upgrade": action == "upgrade",
                "action": action,
                "reason": _plan_reason(current_plan, plan, action),
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
    }


def _current_user_email(current_user: AuthenticatedUser) -> str | None:
    email = current_user.claims.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


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


def _record_checkout_session(
    *,
    current_user: AuthenticatedUser,
    entitlement: UserEntitlement,
    current_plan: BillingPlanName,
    target_plan: BillingPlanName,
    provider: BillingProviderName,
    organization_name: str | None,
    checkout_session,
) -> None:
    """
    Best-effort checkout ledger write. Webhooks remain the entitlement source of
    truth, so a ledger write failure must not grant or deny access by itself.
    """
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO billing_checkout_sessions (
                        provider,
                        provider_session_id,
                        provider_reference,
                        user_id,
                        email,
                        target_plan,
                        current_plan,
                        organization_id,
                        organization_name,
                        checkout_url,
                        status,
                        provider_customer_id,
                        provider_subscription_id,
                        metadata,
                        raw_response
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'created', %s, %s, %s, %s)
                    ON CONFLICT (provider, provider_session_id)
                    WHERE provider_session_id IS NOT NULL
                    DO UPDATE SET
                        provider_reference = EXCLUDED.provider_reference,
                        checkout_url = EXCLUDED.checkout_url,
                        provider_customer_id = EXCLUDED.provider_customer_id,
                        provider_subscription_id = EXCLUDED.provider_subscription_id,
                        organization_name = EXCLUDED.organization_name,
                        metadata = EXCLUDED.metadata,
                        raw_response = EXCLUDED.raw_response,
                        updated_at = NOW()
                    """,
                    (
                        provider,
                        checkout_session.provider_session_id,
                        checkout_session.reference,
                        current_user.user_id,
                        _current_user_email(current_user),
                        target_plan,
                        current_plan,
                        entitlement.organization_id if entitlement.source == "organization" else None,
                        organization_name,
                        checkout_session.checkout_url,
                        checkout_session.provider_customer_id,
                        checkout_session.provider_subscription_id,
                        Jsonb(
                            {
                                "source": "redocx_billing_page",
                                "entitlement_source": entitlement.source,
                                "organization_role": entitlement.organization_role,
                                "organization_name": organization_name,
                            }
                        ),
                        Jsonb(checkout_session.raw or {}),
                    ),
                )
    except Exception:
        return


@router.get("/plans")
def get_billing_plans(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        entitlement = get_user_entitlement(current_user.user_id)
        return build_billing_state(entitlement, current_user=current_user)
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
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        entitlement = get_user_entitlement(current_user.user_id)
        current_plan = _current_plan_from_entitlement(entitlement)
        target_plan = payload.target_plan
        provider = payload.provider or _recommended_provider(
            current_user=current_user,
            region_hint=payload.region_hint,
            current_plan=current_plan,
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
            if entitlement.source == "organization":
                if entitlement.organization_role != "owner":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "organization_owner_required",
                            "message": "Only the organization owner can upgrade an organization plan.",
                        },
                    )
                organization_name = normalize_organization_name(
                    entitlement.organization_name or ""
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

        checkout_session = create_checkout_session(
            BillingCheckoutRequest(
                user_id=current_user.user_id,
                email=_current_user_email(current_user),
                target_plan=target_plan,
                current_plan=current_plan,
                organization_id=entitlement.organization_id if entitlement.source == "organization" else None,
                organization_name=organization_name,
                metadata={
                    "source": "redocx_billing_page",
                    "entitlement_source": entitlement.source,
                    "organization_role": entitlement.organization_role,
                    "organization_name": organization_name,
                    "checkout_provider": provider,
                },
            ),
            provider_name=provider,
        )
        _record_checkout_session(
            current_user=current_user,
            entitlement=entitlement,
            current_plan=current_plan,
            target_plan=target_plan,
            provider=provider,
            organization_name=organization_name,
            checkout_session=checkout_session,
        )

        return {
            "success": True,
            "provider": checkout_session.provider,
            "current_plan": current_plan,
            "target_plan": target_plan,
            "checkout_url": checkout_session.checkout_url,
            "provider_session_id": checkout_session.provider_session_id,
            "provider_customer_id": checkout_session.provider_customer_id,
            "provider_subscription_id": checkout_session.provider_subscription_id,
            "reference": checkout_session.reference,
            "message": f"Continue to {CHECKOUT_PROVIDER_CATALOG[provider]['name']} checkout for {PLAN_CATALOG[target_plan]['name']}.",
        }
    except HTTPException:
        raise
    except CheckoutNotConfiguredError as exc:
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
        raise HTTPException(
            status_code=502,
            detail={
                "error": "checkout_provider_failed",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_billing_request", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "upgrade_intent_failed",
                "message": "Could not create upgrade intent.",
            },
        ) from exc
