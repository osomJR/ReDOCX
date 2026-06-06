from __future__ import annotations

"""
Authenticated-paid Personal plan access guard.

Plan contract:
- unlimited use of every supported feature
- exactly 1 subscribed account/user

This module intentionally does not consume rate-limit buckets. Subscription
status and team membership lookup should happen before this guard is called;
this guard only validates the feature and the plan's account-count boundary.
"""

from fastapi import HTTPException, Request

from backend.src.schema import FeatureType
from backend.rate_limiter.shared import HEAVY_FEATURES, LIGHT_FEATURES

PLAN_NAME = "authenticated_paid_personal"
MIN_ACCOUNTS = 1
MAX_ACCOUNTS = 1
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
    """
    Validate the subscribed account/user count when the caller has it.

    Passing None leaves account-count enforcement to the subscription layer.
    """
    if account_count is None:
        return

    if not isinstance(account_count, int) or account_count != 1:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "paid_plan_account_limit_exceeded",
                "message": "The Personal plan supports exactly 1 account/user.",
                "plan": PLAN_NAME,
                "min_accounts": MIN_ACCOUNTS,
                "max_accounts": MAX_ACCOUNTS,
                "account_count": account_count,
            },
        )


def rate_limit_authenticated_paid_personal(
    request: Request,
    user_id: str,
    feature: FeatureType,
    account_count: int | None = None,
) -> None:
    """
    Validate Personal-plan access without applying any usage limit.

    The request argument is accepted to keep the signature aligned with the
    existing rate-limiter wrapper style used by other tiers.
    """
    del request

    _validate_user_id(user_id)
    _validate_feature(feature)
    _validate_account_count(account_count)


__all__ = [
    "PLAN_NAME",
    "MIN_ACCOUNTS",
    "MAX_ACCOUNTS",
    "ALLOWED_FEATURES",
    "rate_limit_authenticated_paid_personal",
]
