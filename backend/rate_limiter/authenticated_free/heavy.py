from __future__ import annotations

"""
Authenticated-free heavy-feature rate limiter.

Allowed heavy features:
- convert
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
- total requests per day: 7
- heavy requests per day: 3
- burst protection: 2 requests / 10 seconds
"""

from fastapi import Request

from src.schema import FeatureType
from backend.rate_limiter.shared import (
    AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES,
    AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES,
    AUTHENTICATED_FREE_BLOCKED_FEATURES,
    AUTHENTICATED_FREE_POLICY,
    get_shared_rate_limiter,
)


def rate_limit_authenticated_free_heavy(
    request: Request,
    user_id: str,
    feature: FeatureType,
) -> None:
    get_shared_rate_limiter().enforce_authenticated_free(
        request=request,
        user_id=user_id,
        feature=feature,
        policy=AUTHENTICATED_FREE_POLICY,
        allowed_light_features=AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES,
        allowed_heavy_features=AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES,
        blocked_features=AUTHENTICATED_FREE_BLOCKED_FEATURES,
        family="heavy",
    )


__all__ = ["rate_limit_authenticated_free_heavy"]
