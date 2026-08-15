from __future__ import annotations

"""Authenticated-paid Enterprise plan access and pooled safety guard."""

from fastapi import HTTPException, Request
from backend.src.schema import FeatureType
from backend.rate_limiter.shared import (
    HEAVY_FEATURES,
    LIGHT_FEATURES,
    PaidLease,
    get_shared_rate_limiter,
)

PLAN_NAME = "authenticated_paid_enterprise"
PLAN_KEY = "enterprise"
MIN_ACCOUNTS = 20
MAX_ACCOUNTS = None
ALLOWED_FEATURES = LIGHT_FEATURES.union(HEAVY_FEATURES)


def _feature_name(feature: FeatureType) -> str:
    return getattr(feature, "value", str(feature))


def _validate_user_id(user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authorization_required",
                "message": "Authenticated paid access requires a valid user.",
            },
        )


def _validate_feature(feature: FeatureType) -> None:
    if feature not in ALLOWED_FEATURES:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "feature_not_available",
                "message": f"Feature '{_feature_name(feature)}' is not available for this plan.",
                "plan": PLAN_NAME,
            },
        )


def _validate_account_count(account_count: int | None) -> None:
    if account_count is None:
        return
    if not isinstance(account_count, int) or account_count < MIN_ACCOUNTS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "paid_plan_account_limit_exceeded",
                "message": "The Enterprise plan supports 20 or more accounts/users.",
                "plan": PLAN_NAME,
                "min_accounts": MIN_ACCOUNTS,
                "max_accounts": MAX_ACCOUNTS,
                "account_count": account_count,
            },
        )


def rate_limit_authenticated_paid_enterprise(
    request: Request,
    user_id: str,
    feature: FeatureType,
    account_count: int | None = None,
    scope_id: str = "",
) -> PaidLease | None:
    _validate_user_id(user_id)
    _validate_feature(feature)
    _validate_account_count(account_count)
    return get_shared_rate_limiter().enforce_authenticated_paid(
        request=request,
        user_id=user_id,
        scope_id=scope_id or f"user:{user_id}",
        feature=feature,
        plan=PLAN_KEY,
    )


__all__ = [
    "PLAN_NAME",
    "MIN_ACCOUNTS",
    "MAX_ACCOUNTS",
    "ALLOWED_FEATURES",
    "rate_limit_authenticated_paid_enterprise",
]
