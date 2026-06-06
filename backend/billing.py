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
from pydantic import BaseModel, field_validator

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.billing_provider import (
    BillingCheckoutRequest,
    BillingProviderError,
    CheckoutNotConfiguredError,
    create_checkout_session,
    normalize_provider_name,
)
from backend.subscriptions import UserEntitlement, get_user_entitlement, normalize_plan


router = APIRouter(prefix="/billing", tags=["billing-v1"])

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
BillingAction = Literal["current", "upgrade", "none"]
BillingProviderName = Literal["paystack", "stripe", "static", "generic", "flutterwave"]

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


def _normalize_billing_plan(value: str) -> BillingPlanName:
    normalized = normalize_plan(value)
    return normalized  # type: ignore[return-value]


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def _current_provider_name(provider_name: str | None = None) -> str:
    return normalize_provider_name(provider_name)


def _checkout_configured_for_provider(plan: BillingPlanName, provider_name: str | None = None) -> bool:
    """
    Best-effort UI hint only. The real authority is create_checkout_session(),
    which validates provider configuration and returns a checkout URL.
    """
    provider = _current_provider_name(provider_name)

    if provider == "paystack":
        has_price = bool(_env(f"PAYSTACK_{plan.upper()}_PLAN_CODE") or _env(f"PAYSTACK_{plan.upper()}_AMOUNT_KOBO"))
        has_redirect = bool(_env("PAYSTACK_CALLBACK_URL") or _env("BILLING_SUCCESS_URL"))
        return bool(_env("PAYSTACK_SECRET_KEY") and has_redirect and has_price)

    if provider == "stripe":
        return bool(
            _env("STRIPE_SECRET_KEY")
            and _env(f"STRIPE_{plan.upper()}_PRICE_ID")
            and _env("BILLING_SUCCESS_URL")
            and _env("BILLING_CANCEL_URL")
        )

    if provider == "static":
        return bool(_env(f"BILLING_{plan.upper()}_CHECKOUT_URL"))

    if provider == "generic":
        return bool(
            _env(f"BILLING_GENERIC_{plan.upper()}_CHECKOUT_URL")
            or _env(f"BILLING_{plan.upper()}_CHECKOUT_URL")
        )

    if provider == "flutterwave":
        return bool(
            _env("FLUTTERWAVE_SECRET_KEY")
            and _env(f"FLUTTERWAVE_{plan.upper()}_AMOUNT")
            and (_env("FLUTTERWAVE_REDIRECT_URL") or _env("BILLING_SUCCESS_URL"))
        )

    return False


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


def build_billing_state(entitlement: UserEntitlement) -> dict[str, Any]:
    current_plan = _current_plan_from_entitlement(entitlement)
    visible_plans = VISIBLE_PLANS_BY_CURRENT[current_plan]
    provider = _current_provider_name()

    cards: list[dict[str, Any]] = []
    for plan in visible_plans:
        catalog = PLAN_CATALOG[plan]
        action = _plan_action(current_plan, plan)
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
                "checkout_configured": _checkout_configured_for_provider(plan, provider),
            }
        )

    return {
        "current_plan": current_plan,
        "current_plan_name": PLAN_CATALOG[current_plan]["name"],
        "provider": provider,
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
    # Optional override. If omitted, BILLING_PROVIDER controls checkout provider.
    provider: BillingProviderName | None = None

    @field_validator("target_plan")
    @classmethod
    def validate_target_plan(cls, value: str) -> str:
        return _normalize_billing_plan(value)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _current_provider_name(value)
        if normalized not in {"paystack", "stripe", "static", "generic", "flutterwave"}:
            raise ValueError("provider must be one of: paystack, stripe, static, generic, flutterwave.")
        return normalized


@router.get("/plans")
def get_billing_plans(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        entitlement = get_user_entitlement(current_user.user_id)
        return build_billing_state(entitlement)
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
        provider = _current_provider_name(payload.provider)

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

        checkout_session = create_checkout_session(
            BillingCheckoutRequest(
                user_id=current_user.user_id,
                email=_current_user_email(current_user),
                target_plan=target_plan,
                current_plan=current_plan,
                organization_id=entitlement.organization_id if entitlement.source == "organization" else None,
                organization_name=entitlement.organization_name,
                metadata={
                    "source": "redocx_billing_page",
                    "entitlement_source": entitlement.source,
                    "organization_role": entitlement.organization_role,
                },
            ),
            provider_name=provider,
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
            "message": f"Continue to checkout for {PLAN_CATALOG[target_plan]['name']}.",
        }
    except HTTPException:
        raise
    except CheckoutNotConfiguredError as exc:
        return {
            "success": False,
            "provider": _current_provider_name(payload.provider),
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
