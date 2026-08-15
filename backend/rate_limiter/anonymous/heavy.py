from __future__ import annotations

"""Anonymous heavy-feature rate limiter. Convert remains the only allowed heavy feature."""

from fastapi import Request, Response
from backend.src.schema import FeatureType
from backend.rate_limiter.shared import (
    ANONYMOUS_ALLOWED_HEAVY_FEATURES,
    ANONYMOUS_ALLOWED_LIGHT_FEATURES,
    ANONYMOUS_BLOCKED_FEATURES,
    ANONYMOUS_POLICY,
    get_shared_rate_limiter,
)


def rate_limit_anonymous_heavy(request: Request, response: Response, feature: FeatureType) -> None:
    get_shared_rate_limiter().enforce_anonymous(
        request=request,
        response=response,
        feature=feature,
        policy=ANONYMOUS_POLICY,
        allowed_light_features=ANONYMOUS_ALLOWED_LIGHT_FEATURES,
        allowed_heavy_features=ANONYMOUS_ALLOWED_HEAVY_FEATURES,
        blocked_features=ANONYMOUS_BLOCKED_FEATURES,
        family="heavy",
    )


__all__ = ["rate_limit_anonymous_heavy"]
