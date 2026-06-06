from __future__ import annotations

"""
Anonymous light-feature rate limiter.

Allowed features:
- summarize
- explain
- translate
- grammar_correct

Tier policy:
- total requests per day: 5
- heavy requests per day: 2 (tracked globally in shared policy, though light endpoints do not consume it)
- burst protection: 2 requests / 10 seconds
"""

from fastapi import Request

from backend.src.schema import FeatureType
from backend.rate_limiter.shared import (
    ANONYMOUS_ALLOWED_HEAVY_FEATURES,
    ANONYMOUS_ALLOWED_LIGHT_FEATURES,
    ANONYMOUS_BLOCKED_FEATURES,
    ANONYMOUS_POLICY,
    get_shared_rate_limiter,
)


def rate_limit_anonymous_light(request: Request, feature: FeatureType) -> None:
    get_shared_rate_limiter().enforce_anonymous(
        request=request,
        feature=feature,
        policy=ANONYMOUS_POLICY,
        allowed_light_features=ANONYMOUS_ALLOWED_LIGHT_FEATURES,
        allowed_heavy_features=ANONYMOUS_ALLOWED_HEAVY_FEATURES,
        blocked_features=ANONYMOUS_BLOCKED_FEATURES,
        family="light",
    )


__all__ = ["rate_limit_anonymous_light"]
