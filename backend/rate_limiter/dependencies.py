from __future__ import annotations

"""FastAPI dependency bridge for feature-based rate limiting.

The tier/wrapper structure remains unchanged:
- anonymous users -> anonymous light/heavy wrappers
- authenticated free users -> authenticated-free light/heavy wrappers
- paid users -> Personal/Business/Enterprise guards

Paid guards now add high-watermark throughput, AI fair-use and concurrency safety
rails while preserving unlimited normal document operations.
"""

from collections.abc import Callable, Iterator
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
from backend.rate_limiter.shared import (
    HEAVY_FEATURES,
    LIGHT_FEATURES,
    PDF_TOOL_FEATURES,
    PaidLease,
    get_shared_rate_limiter,
)


def _is_supported_feature(feature: FeatureType) -> bool:
    return feature in LIGHT_FEATURES or feature in HEAVY_FEATURES


def _apply_anonymous_limit(request: Request, response: Response, feature: FeatureType) -> None:
    if feature in LIGHT_FEATURES:
        rate_limit_anonymous_light(request=request, response=response, feature=feature)
        return
    rate_limit_anonymous_heavy(request=request, response=response, feature=feature)


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
    scope_id: str,
    feature: FeatureType,
    plan: str,
    account_count: int,
) -> PaidLease | None:
    if plan == "personal":
        return rate_limit_authenticated_paid_personal(
            request=request,
            user_id=user_id,
            scope_id=scope_id,
            feature=feature,
            account_count=account_count,
        )
    if plan == "business":
        return rate_limit_authenticated_paid_business(
            request=request,
            user_id=user_id,
            scope_id=scope_id,
            feature=feature,
            account_count=account_count,
        )
    if plan == "enterprise":
        return rate_limit_authenticated_paid_enterprise(
            request=request,
            user_id=user_id,
            scope_id=scope_id,
            feature=feature,
            account_count=account_count,
        )
    raise ValueError(f"Unsupported paid subscription plan for rate limiting: {plan}")


def rate_limit_for_feature(feature: FeatureType) -> Callable[..., Iterator[None]]:
    """Build the existing feature dependency with post-response paid lease cleanup."""

    def dependency(
        request: Request,
        response: Response,
        current_user: AuthenticatedUser | None = Depends(get_current_user_optional),
    ) -> Iterator[None]:
        if not _is_supported_feature(feature):
            raise ValueError(f"Unsupported feature for rate limiting: {feature}")

        paid_lease: PaidLease | None = None
        request_path = str(getattr(request.url, "path", "") or "")
        is_single_pdf_tool_request = (
            feature in PDF_TOOL_FEATURES and "/batch/" not in request_path
        )
        limiter = get_shared_rate_limiter()

        # Single-file conversion is intentionally unlimited for every account
        # category. Batch conversion remains on the existing paid batch path.
        if feature == FeatureType.convert and "/batch/" not in request_path:
            yield
            return

        if current_user is None:
            if is_single_pdf_tool_request:
                limiter.enforce_anonymous_pdf_tool_trial(
                    request=request,
                    response=response,
                )
            else:
                _apply_anonymous_limit(request=request, response=response, feature=feature)
        else:
            entitlement = get_user_entitlement(current_user.user_id)
            if entitlement.is_paid:
                # Business/Enterprise safety rails are pooled per organization;
                # Personal remains scoped to the individual user.
                organization_id = getattr(entitlement, "organization_id", None)
                scope_id = (
                    f"org:{organization_id}"
                    if organization_id and entitlement.plan in {"business", "enterprise"}
                    else f"user:{current_user.user_id}"
                )
                paid_lease = _apply_authenticated_paid_guard(
                    request=request,
                    user_id=current_user.user_id,
                    scope_id=scope_id,
                    feature=feature,
                    plan=entitlement.plan,
                    account_count=entitlement.account_count,
                )
            elif is_single_pdf_tool_request:
                limiter.enforce_authenticated_free_pdf_tool_window(
                    request=request,
                    response=response,
                    user_id=current_user.user_id,
                )
            else:
                _apply_authenticated_free_limit(
                    request=request,
                    response=response,
                    user_id=current_user.user_id,
                    feature=feature,
                )

        try:
            yield
        finally:
            if paid_lease is not None:
                get_shared_rate_limiter().release_paid_lease(paid_lease)

    return dependency


__all__ = ["rate_limit_for_feature"]
