from __future__ import annotations

"""Authenticated-free heavy-feature limiter using weighted workflow credits."""

from fastapi import Request, Response
from backend.src.schema import FeatureType
from backend.rate_limiter.shared import (
    AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES,
    AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES,
    AUTHENTICATED_FREE_BLOCKED_FEATURES,
    AUTHENTICATED_FREE_POLICY,
    get_shared_rate_limiter,
)


def rate_limit_authenticated_free_heavy(
    request: Request,
    response: Response,
    user_id: str,
    feature: FeatureType,
) -> None:
    get_shared_rate_limiter().enforce_authenticated_free(
        request=request,
        response=response,
        user_id=user_id,
        feature=feature,
        policy=AUTHENTICATED_FREE_POLICY,
        allowed_light_features=AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES,
        allowed_heavy_features=AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES,
        blocked_features=AUTHENTICATED_FREE_BLOCKED_FEATURES,
        family="heavy",
    )


__all__ = ["rate_limit_authenticated_free_heavy"]
