from __future__ import annotations

"""
Anonymous heavy-feature rate limiter.

Allowed heavy feature:
- convert

Blocked for anonymous users:
- generate_questions
- generate_answers
- transcribe
- redact
- data_mask
- compliance
- structured_extract
- e_signature
- edit_pdf
- combine_pdf
- compress_pdf
- split_pdf

Tier policy:
- total requests per day: 5
- heavy requests per day: 2
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


def rate_limit_anonymous_heavy(request: Request, feature: FeatureType) -> None:
    get_shared_rate_limiter().enforce_anonymous(
        request=request,
        feature=feature,
        policy=ANONYMOUS_POLICY,
        allowed_light_features=ANONYMOUS_ALLOWED_LIGHT_FEATURES,
        allowed_heavy_features=ANONYMOUS_ALLOWED_HEAVY_FEATURES,
        blocked_features=ANONYMOUS_BLOCKED_FEATURES,
        family="heavy",
    )


__all__ = ["rate_limit_anonymous_heavy"]
