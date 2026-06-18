from __future__ import annotations

"""
FastAPI dependency bridge for feature-based rate limiting.

Responsibilities:
- detect whether the caller is anonymous or authenticated
- map each FeatureType to the correct anonymous, authenticated-free, or authenticated-paid guard
- route paid subscriptions through Personal, Business, or Enterprise entitlement rules
- keep router modules thin and free of tier-selection logic

Notes:
- anonymous users use anonymous light/heavy wrappers
- authenticated users without an active paid subscription use authenticated-free light/heavy wrappers
- active paid users use authenticated-paid plan guards with unlimited feature use
"""

from collections.abc import Callable
from fastapi import Depends, Request, Response

from backend.auth0_dependencies import AuthenticatedUser, get_current_user_optional
from backend.subscriptions import get_user_entitlement

from backend.src.schema import FeatureType
from backend.rate_limiter.anonymous.light import rate_limit_anonymous_light
from backend.rate_limiter.anonymous.heavy import rate_limit_anonymous_heavy
from backend.rate_limiter.authenticated_free.light import rate_limit_authenticated_free_light
from backend.rate_limiter.authenticated_free.heavy import rate_limit_authenticated_free_heavy
from backend.rate_limiter.authenticated_paid.personal import rate_limit_authenticated_paid_personal
from backend.rate_limiter.authenticated_paid.business import rate_limit_authenticated_paid_business
from backend.rate_limiter.authenticated_paid.enterprise import rate_limit_authenticated_paid_enterprise
from backend.rate_limiter.shared import LIGHT_FEATURES, HEAVY_FEATURES


def _is_supported_feature(feature: FeatureType) -> bool:
    return feature in LIGHT_FEATURES or feature in HEAVY_FEATURES


def _apply_anonymous_limit(request: Request, feature: FeatureType) -> None:
    if feature in LIGHT_FEATURES:
        rate_limit_anonymous_light(
            request=request,
            feature=feature,
        )
        return

    rate_limit_anonymous_heavy(
        request=request,
        feature=feature,
    )


def _apply_authenticated_free_limit(
    request: Request,
    response: Response,
    *,
    user_id: str,
    feature: FeatureType,
) -> None:
    if feature in LIGHT_FEATURES:
        rate_limit_authenticated_free_light(
            request=request,
            response=response,
            user_id=user_id,
            feature=feature,
        )
        return

    rate_limit_authenticated_free_heavy(
        request=request,
        response=response,
        user_id=user_id,
        feature=feature,
    )


def _apply_authenticated_paid_guard(
    request: Request,
    *,
    user_id: str,
    feature: FeatureType,
    plan: str,
    account_count: int,
) -> None:
    if plan == "personal":
        rate_limit_authenticated_paid_personal(
            request=request,
            user_id=user_id,
            feature=feature,
            account_count=account_count,
        )
        return

    if plan == "business":
        rate_limit_authenticated_paid_business(
            request=request,
            user_id=user_id,
            feature=feature,
            account_count=account_count,
        )
        return

    if plan == "enterprise":
        rate_limit_authenticated_paid_enterprise(
            request=request,
            user_id=user_id,
            feature=feature,
            account_count=account_count,
        )
        return

    raise ValueError(f"Unsupported paid subscription plan for rate limiting: {plan}")


def rate_limit_for_feature(feature: FeatureType) -> Callable[..., None]:
    """
    Build a FastAPI dependency that enforces the correct access guard for a feature.

    Usage:
        @router.post(
            "/summarize",
            dependencies=[Depends(rate_limit_for_feature(FeatureType.summarize))]
        )
        async def summarize_route(...):
            ...
    """

    def dependency(
        request: Request,
        response: Response,
        current_user: AuthenticatedUser | None = Depends(get_current_user_optional),
    ) -> None:
        if not _is_supported_feature(feature):
            raise ValueError(f"Unsupported feature for rate limiting: {feature}")

        if current_user is None:
            _apply_anonymous_limit(
                request=request,
                feature=feature,
            )
            return

        entitlement = get_user_entitlement(current_user.user_id)

        if entitlement.is_paid:
            _apply_authenticated_paid_guard(
                request=request,
                user_id=current_user.user_id,
                feature=feature,
                plan=entitlement.plan,
                account_count=entitlement.account_count,
            )
            return

        _apply_authenticated_free_limit(
            request=request,
            response=response,
            user_id=current_user.user_id,
            feature=feature,
        )

    return dependency


__all__ = ["rate_limit_for_feature"]
