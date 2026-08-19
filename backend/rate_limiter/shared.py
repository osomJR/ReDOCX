from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Optional, Sequence
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time

from fastapi import HTTPException, Request, Response

from backend.src.schema import FeatureType

logger = logging.getLogger(__name__)

SECONDS_IN_DAY = 24 * 60 * 60

DEFAULT_BURST_LIMIT = int(os.getenv("RATE_LIMIT_BURST_LIMIT", "2"))
DEFAULT_BURST_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_BURST_WINDOW_SECONDS", "10"))
# Network limiting is an abuse ceiling, not subscription metering. Keep the
# default deliberately broad for carrier NATs, campuses, offices and cafés.
DEFAULT_NETWORK_BURST_LIMIT = int(os.getenv("RATE_LIMIT_NETWORK_BURST_LIMIT", "60"))
DEFAULT_NETWORK_BURST_WINDOW_SECONDS = int(
    os.getenv("RATE_LIMIT_NETWORK_BURST_WINDOW_SECONDS", "10")
)
DEFAULT_DEVICE_HEADER_NAME = os.getenv("RATE_LIMIT_DEVICE_HEADER_NAME", "x-device-id")
DEFAULT_SESSION_HEADER_NAME = os.getenv("RATE_LIMIT_SESSION_HEADER_NAME", "x-session-id")
DEFAULT_FAIL_CLOSED = os.getenv("RATE_LIMIT_FAIL_CLOSED", "true").strip().lower() not in {
    "0",
    "false",
    "no",
}

# Anonymous device identity. Anonymous quotas are keyed to this signed browser
# identity first and only fall back to request headers/IP if a response object is
# unavailable (mainly tests or non-standard integrations).
ANONYMOUS_DEVICE_COOKIE_NAME = os.getenv(
    "RATE_LIMIT_ANONYMOUS_DEVICE_COOKIE_NAME",
    "redocx_anon_device",
)
ANONYMOUS_DEVICE_COOKIE_MAX_AGE_SECONDS = int(
    os.getenv(
        "RATE_LIMIT_ANONYMOUS_DEVICE_COOKIE_MAX_AGE_SECONDS",
        str(30 * SECONDS_IN_DAY),
    )
)
ANONYMOUS_DEVICE_COOKIE_SAMESITE = os.getenv(
    "RATE_LIMIT_ANONYMOUS_DEVICE_COOKIE_SAMESITE",
    "lax",
).strip().lower()
ANONYMOUS_DEVICE_COOKIE_SECURE = os.getenv(
    "RATE_LIMIT_ANONYMOUS_DEVICE_COOKIE_SECURE",
    "true",
).strip().lower() not in {"0", "false", "no"}
ANONYMOUS_DEVICE_COOKIE_DOMAIN = (
    os.getenv("RATE_LIMIT_ANONYMOUS_DEVICE_COOKIE_DOMAIN", "").strip() or None
)

AUTH_FREE_DEVICE_COOKIE_NAME = os.getenv(
    "RATE_LIMIT_AUTH_FREE_DEVICE_COOKIE_NAME",
    "redocx_auth_free_device",
)
AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS = int(
    os.getenv(
        "RATE_LIMIT_AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS",
        str(30 * SECONDS_IN_DAY),
    )
)
AUTH_FREE_BINDING_TTL_SECONDS = int(
    os.getenv(
        "RATE_LIMIT_AUTH_FREE_BINDING_TTL_SECONDS",
        str(AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS),
    )
)
DEVICE_RECOVERY_TTL_SECONDS = max(
    SECONDS_IN_DAY,
    int(
        os.getenv(
            "RATE_LIMIT_DEVICE_RECOVERY_TTL_SECONDS",
            str(
                max(
                    ANONYMOUS_DEVICE_COOKIE_MAX_AGE_SECONDS,
                    AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS,
                )
            ),
        )
    ),
)
DEVICE_RECOVERY_HEADER_NAMES = (
    "user-agent",
    "accept-language",
    "sec-ch-ua",
    "sec-ch-ua-platform",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-full-version-list",
)
AUTH_FREE_MAX_ACTIVE_DEVICES = max(
    1,
    int(os.getenv("RATE_LIMIT_AUTH_FREE_MAX_ACTIVE_DEVICES", "3")),
)
AUTH_FREE_DEVICE_COOKIE_SAMESITE = os.getenv(
    "RATE_LIMIT_AUTH_FREE_DEVICE_COOKIE_SAMESITE",
    "lax",
).strip().lower()
AUTH_FREE_DEVICE_COOKIE_SECURE = os.getenv(
    "RATE_LIMIT_AUTH_FREE_DEVICE_COOKIE_SECURE",
    "true",
).strip().lower() not in {"0", "false", "no"}
AUTH_FREE_DEVICE_COOKIE_DOMAIN = (
    os.getenv("RATE_LIMIT_AUTH_FREE_DEVICE_COOKIE_DOMAIN", "").strip() or None
)
RATE_LIMIT_DEVICE_SECRET = (
    os.getenv("RATE_LIMIT_AUTH_FREE_DEVICE_SECRET")
    or os.getenv("RATE_LIMIT_DEVICE_SECRET")
    or os.getenv("SECRET_KEY")
)
ANONYMOUS_DEVICE_SECRET = (
    os.getenv("RATE_LIMIT_ANONYMOUS_DEVICE_SECRET") or RATE_LIMIT_DEVICE_SECRET
)
AUTH_FREE_ENFORCE_NETWORK_ACCOUNT_BINDING = os.getenv(
    "RATE_LIMIT_AUTH_FREE_ENFORCE_NETWORK_ACCOUNT_BINDING",
    "false",
).strip().lower() not in {"0", "false", "no"}
TRUST_CLIENT_DEVICE_HEADERS = os.getenv(
    "RATE_LIMIT_TRUST_CLIENT_DEVICE_HEADERS",
    "false",
).strip().lower() not in {"0", "false", "no"}

REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL") or os.getenv("REDIS_URL")
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = float(
    os.getenv("RATE_LIMIT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS", "3")
)
REDIS_SOCKET_TIMEOUT_SECONDS = float(
    os.getenv("RATE_LIMIT_REDIS_SOCKET_TIMEOUT_SECONDS", "1.5")
)
REDIS_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("RATE_LIMIT_REDIS_CONNECT_TIMEOUT_SECONDS", "1.5")
)
REPLAY_MAX_EVENTS_PER_BUCKET = int(
    os.getenv("RATE_LIMIT_REPLAY_MAX_EVENTS_PER_BUCKET", "10000")
)

LIGHT_FEATURES = frozenset(
    {
        FeatureType.summarize,
        FeatureType.explain,
        FeatureType.translate,
        FeatureType.grammar_correct,
    }
)

HEAVY_FEATURES = frozenset(
    {
        FeatureType.convert,
        FeatureType.generate_questions,
        FeatureType.generate_answers,
        FeatureType.transcribe,
        FeatureType.redact,
        FeatureType.data_mask,
        FeatureType.compliance,
        FeatureType.structured_extract,
        FeatureType.e_signature,
        FeatureType.edit_pdf,
        FeatureType.combine_pdf,
        FeatureType.compress_pdf,
        FeatureType.split_pdf,
    }
)

ANONYMOUS_ALLOWED_LIGHT_FEATURES = LIGHT_FEATURES
ANONYMOUS_ALLOWED_HEAVY_FEATURES = frozenset({FeatureType.convert})
ANONYMOUS_BLOCKED_FEATURES = frozenset(
    {
        FeatureType.generate_questions,
        FeatureType.generate_answers,
        FeatureType.transcribe,
        FeatureType.redact,
        FeatureType.data_mask,
        FeatureType.compliance,
        FeatureType.structured_extract,
        FeatureType.e_signature,
        FeatureType.edit_pdf,
        FeatureType.combine_pdf,
        FeatureType.compress_pdf,
        FeatureType.split_pdf,
    }
)

AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES = LIGHT_FEATURES
AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES = HEAVY_FEATURES
AUTHENTICATED_FREE_BLOCKED_FEATURES = frozenset()

# A logical workflow can involve more than one HTTP endpoint. Auxiliary review /
# preview / layout endpoints are protected by their own daily ceiling and burst
# limits but do not consume the user's main total/heavy credits.
AUXILIARY_ROUTE_SUFFIXES = frozenset(
    {
        "/redact/review",
        "/data-mask/review",
        "/compliance/preview",
        "/e-signature/layout",
    }
)

# Authenticated-free weighted heavy credits. Cheap/local operations cost 1;
# expensive model/media workflows cost more. This keeps the free tier useful
# while preserving a strong reason to subscribe.
AUTH_FREE_HEAVY_CREDIT_COST = {
    FeatureType.convert: 1,
    FeatureType.generate_questions: 2,
    FeatureType.generate_answers: 2,
    FeatureType.transcribe: 3,
    FeatureType.redact: 2,
    FeatureType.data_mask: 2,
    FeatureType.compliance: 4,
    FeatureType.structured_extract: 3,
    FeatureType.e_signature: 2,
    FeatureType.edit_pdf: 1,
    FeatureType.combine_pdf: 1,
    FeatureType.compress_pdf: 1,
    FeatureType.split_pdf: 1,
}

# Paid plans retain unlimited normal document operations. Only variable-cost AI /
# media workloads participate in a very generous fair-use safety ceiling.
PAID_AI_CREDIT_COST = {
    FeatureType.summarize: 1,
    FeatureType.explain: 1,
    FeatureType.translate: 1,
    FeatureType.grammar_correct: 1,
    FeatureType.generate_questions: 3,
    FeatureType.generate_answers: 3,
    FeatureType.transcribe: 5,
    FeatureType.redact: 2,
    FeatureType.data_mask: 2,
    FeatureType.compliance: 5,
    FeatureType.structured_extract: 4,
}
PAID_EXPENSIVE_FEATURES = frozenset(PAID_AI_CREDIT_COST)
PAID_BATCH_COST_MULTIPLIER = {
    "personal": 10,
    "business": 25,
    "enterprise": 50,
}


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    return max(minimum, int(os.getenv(name, str(default))))


@dataclass(frozen=True)
class RateLimitPolicy:
    tier_name: str
    total_limit: int
    heavy_limit: int
    burst_limit: int = DEFAULT_BURST_LIMIT
    burst_window_seconds: int = DEFAULT_BURST_WINDOW_SECONDS
    network_total_limit: Optional[int] = None
    network_heavy_limit: Optional[int] = None
    network_burst_limit: Optional[int] = DEFAULT_NETWORK_BURST_LIMIT
    network_burst_window_seconds: int = DEFAULT_NETWORK_BURST_WINDOW_SECONDS
    auxiliary_limit: Optional[int] = None
    auxiliary_window_seconds: int = SECONDS_IN_DAY
    total_window_seconds: int = SECONDS_IN_DAY
    heavy_window_seconds: int = SECONDS_IN_DAY
    fail_closed: bool = DEFAULT_FAIL_CLOSED

    def __post_init__(self) -> None:
        for field_name in (
            "total_limit",
            "heavy_limit",
            "burst_limit",
            "burst_window_seconds",
            "network_burst_window_seconds",
            "auxiliary_window_seconds",
            "total_window_seconds",
            "heavy_window_seconds",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be an int >= 1.")

        for field_name in (
            "network_total_limit",
            "network_heavy_limit",
            "network_burst_limit",
            "auxiliary_limit",
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(f"{field_name} must be None or an int >= 1.")

        if not isinstance(self.tier_name, str) or not self.tier_name.strip():
            raise ValueError("tier_name must be a non-empty string.")


@dataclass(frozen=True)
class PaidPlanPolicy:
    plan: str
    tier_name: str
    burst_limit: int
    ai_credit_limit: int
    concurrent_expensive_jobs: int
    burst_window_seconds: int = 10
    ai_window_seconds: int = SECONDS_IN_DAY
    concurrency_lease_seconds: int = 30 * 60


PAID_PLAN_POLICIES = {
    "personal": PaidPlanPolicy(
        plan="personal",
        tier_name="authenticated_paid_personal",
        burst_limit=_env_int("RATE_LIMIT_PERSONAL_BURST_LIMIT", 20),
        ai_credit_limit=_env_int("RATE_LIMIT_PERSONAL_AI_CREDITS_PER_DAY", 300),
        concurrent_expensive_jobs=_env_int("RATE_LIMIT_PERSONAL_CONCURRENT_EXPENSIVE", 3),
    ),
    "business": PaidPlanPolicy(
        plan="business",
        tier_name="authenticated_paid_business",
        burst_limit=_env_int("RATE_LIMIT_BUSINESS_BURST_LIMIT", 100),
        ai_credit_limit=_env_int("RATE_LIMIT_BUSINESS_AI_CREDITS_PER_DAY", 5000),
        concurrent_expensive_jobs=_env_int("RATE_LIMIT_BUSINESS_CONCURRENT_EXPENSIVE", 20),
    ),
    "enterprise": PaidPlanPolicy(
        plan="enterprise",
        tier_name="authenticated_paid_enterprise",
        burst_limit=_env_int("RATE_LIMIT_ENTERPRISE_BURST_LIMIT", 500),
        ai_credit_limit=_env_int("RATE_LIMIT_ENTERPRISE_AI_CREDITS_PER_DAY", 25000),
        concurrent_expensive_jobs=_env_int("RATE_LIMIT_ENTERPRISE_CONCURRENT_EXPENSIVE", 100),
    ),
}


@dataclass(frozen=True)
class LimitOutcome:
    allowed: bool
    current_count: int
    retry_after_seconds: int


@dataclass(frozen=True)
class BindingOutcome:
    allowed: bool
    existing_value: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class BucketSpec:
    key: str
    limit: int
    window_seconds: int
    cost: int = 1
    message: str = "Rate limit exceeded."


@dataclass(frozen=True)
class MultiLimitOutcome:
    allowed: bool
    rejected_index: Optional[int] = None
    current_count: int = 0
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class LeaseOutcome:
    allowed: bool
    token: Optional[str] = None
    retry_after_seconds: int = 0


@dataclass(frozen=True)
class PaidLease:
    key: str
    token: str


class RedisSlidingWindowLimiter:
    """Atomic Redis-backed sliding windows, multi-bucket admission and leases."""

    _LUA_ENFORCE = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window_seconds = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local member = ARGV[4]

    redis.call('ZREMRANGEBYSCORE', key, 0, now - window_seconds)
    local current_count = redis.call('ZCARD', key)

    if current_count >= limit then
      local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
      local retry_after = 1
      if oldest[2] ~= nil then
        retry_after = math.max(1, math.floor((oldest[2] + window_seconds) - now))
      end
      return {0, current_count, retry_after}
    end

    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, window_seconds)
    return {1, current_count + 1, 0}
    """

    # Check every quota/burst bucket first. Only when every bucket can admit the
    # request are all counters updated. A rejection can therefore never consume
    # an earlier daily/heavy bucket.
    _LUA_ENFORCE_MANY = """
    local now = tonumber(ARGV[1])
    local offset = 2

    for i, key in ipairs(KEYS) do
      local window_seconds = tonumber(ARGV[offset])
      local limit = tonumber(ARGV[offset + 1])
      local cost = tonumber(ARGV[offset + 2])

      redis.call('ZREMRANGEBYSCORE', key, 0, now - window_seconds)
      local current_count = redis.call('ZCARD', key)

      if current_count + cost > limit then
        local needed = current_count + cost - limit
        local expiry_event = redis.call('ZRANGE', key, needed - 1, needed - 1, 'WITHSCORES')
        local retry_after = 1
        if expiry_event[2] ~= nil then
          retry_after = math.max(1, math.floor((expiry_event[2] + window_seconds) - now))
        end
        return {0, i, current_count, retry_after}
      end

      offset = offset + 4
    end

    offset = 2
    for i, key in ipairs(KEYS) do
      local window_seconds = tonumber(ARGV[offset])
      local cost = tonumber(ARGV[offset + 2])
      local member = ARGV[offset + 3]
      for n = 1, cost do
        redis.call('ZADD', key, now, member .. ':' .. tostring(n))
      end
      redis.call('EXPIRE', key, window_seconds)
      offset = offset + 4
    end

    return {1, 0, 0, 0}
    """

    _LUA_BIND_DEVICE = """
    local device_key = KEYS[1]
    local user_value = ARGV[1]
    local device_value = ARGV[2]
    local ttl = tonumber(ARGV[3])

    local bound_user = redis.call('GET', device_key)
    if bound_user and bound_user ~= user_value then
      return {0, 1}
    end

    local free_slot = 0
    for i = 2, #KEYS do
      local slot_value = redis.call('GET', KEYS[i])
      if slot_value == device_value then
        redis.call('SET', device_key, user_value, 'EX', ttl)
        redis.call('EXPIRE', KEYS[i], ttl)
        return {1, 0}
      end
      if not slot_value and free_slot == 0 then
        free_slot = i
      end
    end

    if free_slot == 0 then
      return {0, 2}
    end

    redis.call('SET', device_key, user_value, 'EX', ttl)
    redis.call('SET', KEYS[free_slot], device_value, 'EX', ttl)
    return {1, 0}
    """

    _LUA_ACQUIRE_LEASE = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local limit = tonumber(ARGV[2])
    local lease_seconds = tonumber(ARGV[3])
    local token = ARGV[4]

    redis.call('ZREMRANGEBYSCORE', key, 0, now)
    local current_count = redis.call('ZCARD', key)
    if current_count >= limit then
      local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
      local retry_after = 1
      if oldest[2] ~= nil then
        retry_after = math.max(1, math.floor(oldest[2] - now))
      end
      return {0, retry_after}
    end

    local expires_at = now + lease_seconds
    redis.call('ZADD', key, expires_at, token)
    redis.call('EXPIRE', key, lease_seconds + 60)
    return {1, 0}
    """

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        self._enforce_script = self.redis.register_script(self._LUA_ENFORCE)
        self._enforce_many_script = self.redis.register_script(self._LUA_ENFORCE_MANY)
        self._bind_device_script = self.redis.register_script(self._LUA_BIND_DEVICE)
        self._acquire_lease_script = self.redis.register_script(self._LUA_ACQUIRE_LEASE)

    def enforce(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now_seconds: Optional[int] = None,
    ) -> LimitOutcome:
        now = int(now_seconds if now_seconds is not None else time.time())
        member = f"{now}:{os.urandom(8).hex()}"
        allowed, current_count, retry_after = self._enforce_script(
            keys=[key],
            args=[now, window_seconds, limit, member],
        )
        return LimitOutcome(
            allowed=bool(int(allowed)),
            current_count=int(current_count),
            retry_after_seconds=int(retry_after),
        )

    def enforce_many(
        self,
        buckets: Sequence[BucketSpec],
        *,
        now_seconds: Optional[int] = None,
    ) -> MultiLimitOutcome:
        if not buckets:
            return MultiLimitOutcome(allowed=True)

        now = int(now_seconds if now_seconds is not None else time.time())
        keys: list[str] = []
        args: list[object] = [now]
        request_nonce = os.urandom(8).hex()
        for index, bucket in enumerate(buckets):
            keys.append(bucket.key)
            args.extend(
                [
                    int(bucket.window_seconds),
                    int(bucket.limit),
                    int(bucket.cost),
                    f"{now}:{request_nonce}:{index}",
                ]
            )

        allowed, rejected_index, current_count, retry_after = self._enforce_many_script(
            keys=keys,
            args=args,
        )
        return MultiLimitOutcome(
            allowed=bool(int(allowed)),
            rejected_index=(int(rejected_index) - 1) if int(rejected_index) else None,
            current_count=int(current_count),
            retry_after_seconds=int(retry_after),
        )

    def replay_events(
        self,
        *,
        key: str,
        window_seconds: int,
        event_timestamps: list[int],
    ) -> None:
        if not event_timestamps:
            return
        now = int(time.time())
        cutoff = now - window_seconds
        fresh_events = [ts for ts in event_timestamps if ts > cutoff]
        if not fresh_events:
            return
        pipeline = self.redis.pipeline()
        pipeline.zremrangebyscore(key, 0, cutoff)
        mapping = {
            f"{ts}:{idx}:{os.urandom(4).hex()}": ts
            for idx, ts in enumerate(fresh_events)
        }
        pipeline.zadd(key, mapping)
        pipeline.expire(key, window_seconds)
        pipeline.execute()

    def bind_once(self, *, key: str, value: str, ttl_seconds: int) -> BindingOutcome:
        encoded_value = value.encode("utf-8")
        created = self.redis.set(key, encoded_value, nx=True, ex=ttl_seconds)
        if created:
            return BindingOutcome(allowed=True)
        existing = self.redis.get(key)
        if isinstance(existing, bytes):
            existing_value = existing.decode("utf-8", errors="replace")
        elif existing is None:
            existing_value = None
        else:
            existing_value = str(existing)
        if existing_value == value:
            self.redis.expire(key, ttl_seconds)
            return BindingOutcome(allowed=True, existing_value=existing_value)
        return BindingOutcome(allowed=False, existing_value=existing_value)

    def bind_device_to_user(
        self,
        *,
        device_key: str,
        user_slot_prefix: str,
        user_id: str,
        device_id: str,
        ttl_seconds: int,
        max_devices: int,
    ) -> BindingOutcome:
        keys = [device_key] + [
            f"{user_slot_prefix}:{index}" for index in range(1, max_devices + 1)
        ]
        allowed, reason_code = self._bind_device_script(
            keys=keys,
            args=[user_id, device_id, int(ttl_seconds)],
        )
        if int(allowed):
            return BindingOutcome(allowed=True)
        reason = "device_bound_to_other_account" if int(reason_code) == 1 else "too_many_devices"
        return BindingOutcome(allowed=False, reason=reason)

    def acquire_lease(
        self,
        *,
        key: str,
        limit: int,
        lease_seconds: int,
        token: Optional[str] = None,
        now_seconds: Optional[int] = None,
    ) -> LeaseOutcome:
        now = int(now_seconds if now_seconds is not None else time.time())
        resolved_token = token or secrets.token_urlsafe(24)
        allowed, retry_after = self._acquire_lease_script(
            keys=[key],
            args=[now, int(limit), int(lease_seconds), resolved_token],
        )
        return LeaseOutcome(
            allowed=bool(int(allowed)),
            token=resolved_token if int(allowed) else None,
            retry_after_seconds=int(retry_after),
        )

    def release_lease(self, *, key: str, token: str) -> None:
        self.redis.zrem(key, token)


class InMemorySlidingWindowLimiter:
    """Single-process fallback with the same atomic admission semantics."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[int]] = defaultdict(deque)
        self._bindings: dict[str, tuple[str, int]] = {}
        self._leases: dict[str, dict[str, int]] = defaultdict(dict)
        self._lock = Lock()

    @staticmethod
    def _prune_bucket(bucket: deque[int], *, now: int, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def enforce(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now_seconds: Optional[int] = None,
    ) -> LimitOutcome:
        outcome = self.enforce_many(
            [BucketSpec(key=key, limit=limit, window_seconds=window_seconds)],
            now_seconds=now_seconds,
        )
        return LimitOutcome(
            allowed=outcome.allowed,
            current_count=outcome.current_count,
            retry_after_seconds=outcome.retry_after_seconds,
        )

    def enforce_many(
        self,
        buckets: Sequence[BucketSpec],
        *,
        now_seconds: Optional[int] = None,
    ) -> MultiLimitOutcome:
        if not buckets:
            return MultiLimitOutcome(allowed=True)
        now = int(now_seconds if now_seconds is not None else time.time())
        with self._lock:
            for index, spec in enumerate(buckets):
                bucket = self._buckets[spec.key]
                self._prune_bucket(bucket, now=now, window_seconds=spec.window_seconds)
                current_count = len(bucket)
                if current_count + spec.cost > spec.limit:
                    needed = current_count + spec.cost - spec.limit
                    expiry_index = min(max(needed - 1, 0), max(current_count - 1, 0))
                    retry_after = 1
                    if current_count:
                        retry_after = max(
                            1,
                            (bucket[expiry_index] + spec.window_seconds) - now,
                        )
                    return MultiLimitOutcome(
                        allowed=False,
                        rejected_index=index,
                        current_count=current_count,
                        retry_after_seconds=retry_after,
                    )
            for spec in buckets:
                bucket = self._buckets[spec.key]
                for _ in range(spec.cost):
                    bucket.append(now)
            return MultiLimitOutcome(allowed=True)

    def record(
        self,
        *,
        key: str,
        window_seconds: int,
        timestamp_seconds: int,
    ) -> None:
        now = int(timestamp_seconds)
        with self._lock:
            bucket = self._buckets[key]
            self._prune_bucket(bucket, now=now, window_seconds=window_seconds)
            bucket.append(now)

    def record_many(self, buckets: Sequence[BucketSpec], *, timestamp_seconds: int) -> None:
        now = int(timestamp_seconds)
        with self._lock:
            for spec in buckets:
                bucket = self._buckets[spec.key]
                self._prune_bucket(bucket, now=now, window_seconds=spec.window_seconds)
                for _ in range(spec.cost):
                    bucket.append(now)

    def bind_once(self, *, key: str, value: str, ttl_seconds: int) -> BindingOutcome:
        now = int(time.time())
        expires_at = now + int(ttl_seconds)
        with self._lock:
            existing = self._bindings.get(key)
            if existing is None or existing[1] <= now:
                self._bindings[key] = (value, expires_at)
                return BindingOutcome(allowed=True)
            existing_value, _ = existing
            if existing_value == value:
                self._bindings[key] = (value, expires_at)
                return BindingOutcome(allowed=True, existing_value=existing_value)
            return BindingOutcome(allowed=False, existing_value=existing_value)

    def bind_device_to_user(
        self,
        *,
        device_key: str,
        user_slot_prefix: str,
        user_id: str,
        device_id: str,
        ttl_seconds: int,
        max_devices: int,
    ) -> BindingOutcome:
        now = int(time.time())
        expires_at = now + int(ttl_seconds)
        slot_keys = [f"{user_slot_prefix}:{index}" for index in range(1, max_devices + 1)]
        with self._lock:
            for key in [device_key, *slot_keys]:
                existing = self._bindings.get(key)
                if existing is not None and existing[1] <= now:
                    self._bindings.pop(key, None)

            device_binding = self._bindings.get(device_key)
            if device_binding is not None and device_binding[0] != user_id:
                return BindingOutcome(allowed=False, reason="device_bound_to_other_account")

            free_slot: Optional[str] = None
            for slot_key in slot_keys:
                slot_binding = self._bindings.get(slot_key)
                if slot_binding is not None and slot_binding[0] == device_id:
                    self._bindings[device_key] = (user_id, expires_at)
                    self._bindings[slot_key] = (device_id, expires_at)
                    return BindingOutcome(allowed=True)
                if slot_binding is None and free_slot is None:
                    free_slot = slot_key

            if free_slot is None:
                return BindingOutcome(allowed=False, reason="too_many_devices")

            self._bindings[device_key] = (user_id, expires_at)
            self._bindings[free_slot] = (device_id, expires_at)
            return BindingOutcome(allowed=True)

    def acquire_lease(
        self,
        *,
        key: str,
        limit: int,
        lease_seconds: int,
        token: Optional[str] = None,
        now_seconds: Optional[int] = None,
    ) -> LeaseOutcome:
        now = int(now_seconds if now_seconds is not None else time.time())
        resolved_token = token or secrets.token_urlsafe(24)
        with self._lock:
            leases = self._leases[key]
            for existing_token, expires_at in list(leases.items()):
                if expires_at <= now:
                    leases.pop(existing_token, None)
            if len(leases) >= limit:
                retry_after = max(1, min(leases.values()) - now) if leases else 1
                return LeaseOutcome(allowed=False, retry_after_seconds=retry_after)
            leases[resolved_token] = now + int(lease_seconds)
            return LeaseOutcome(allowed=True, token=resolved_token)

    def release_lease(self, *, key: str, token: str) -> None:
        with self._lock:
            leases = self._leases.get(key)
            if leases is None:
                return
            leases.pop(token, None)
            if not leases:
                self._leases.pop(key, None)

    def prune(self) -> None:
        now = int(time.time())
        with self._lock:
            for key, binding in list(self._bindings.items()):
                if binding[1] <= now:
                    self._bindings.pop(key, None)
            for key, leases in list(self._leases.items()):
                for token, expires_at in list(leases.items()):
                    if expires_at <= now:
                        leases.pop(token, None)
                if not leases:
                    self._leases.pop(key, None)
            empty_keys = []
            for key, bucket in self._buckets.items():
                cutoff = now - SECONDS_IN_DAY
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if not bucket:
                    empty_keys.append(key)
            for key in empty_keys:
                self._buckets.pop(key, None)


class ResilientSwitchingLimiterBackend:
    """Redis first, in-memory hot mirror/failover, replay on recovery."""

    def __init__(
        self,
        *,
        redis_client_factory,
        health_check_interval_seconds: float = REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
        replay_max_events_per_bucket: int = REPLAY_MAX_EVENTS_PER_BUCKET,
    ) -> None:
        self._redis_client_factory = redis_client_factory
        self._health_check_interval_seconds = max(0.5, float(health_check_interval_seconds))
        self._replay_max_events_per_bucket = max(1, int(replay_max_events_per_bucket))
        self._memory = InMemorySlidingWindowLimiter()
        self._lock = Lock()
        self._redis_client = None
        self._redis_backend: Optional[RedisSlidingWindowLimiter] = None
        self._last_health_check_monotonic = 0.0
        self._replay_events: dict[str, deque[int]] = defaultdict(deque)
        self._replay_windows: dict[str, int] = {}
        self._replay_bindings: dict[str, tuple[str, int]] = {}
        self._replay_device_bindings: dict[
            tuple[str, str], tuple[str, str, str, int, int]
        ] = {}
        self._maybe_refresh_redis(force=True)

    def enforce(self, *, key: str, limit: int, window_seconds: int) -> LimitOutcome:
        outcome = self.enforce_many(
            [BucketSpec(key=key, limit=limit, window_seconds=window_seconds)]
        )
        return LimitOutcome(
            allowed=outcome.allowed,
            current_count=outcome.current_count,
            retry_after_seconds=outcome.retry_after_seconds,
        )

    def enforce_many(self, buckets: Sequence[BucketSpec]) -> MultiLimitOutcome:
        now_seconds = int(time.time())
        self._maybe_refresh_redis()
        redis_backend = self._get_redis_backend()
        if redis_backend is not None:
            try:
                outcome = redis_backend.enforce_many(buckets, now_seconds=now_seconds)
                if outcome.allowed:
                    self._memory.record_many(buckets, timestamp_seconds=now_seconds)
                return outcome
            except Exception as exc:
                logger.warning(
                    "Rate limiter Redis backend failed during atomic admission; switching to memory fallback: %s",
                    exc,
                    exc_info=True,
                )
                self._demote_to_memory()

        outcome = self._memory.enforce_many(buckets, now_seconds=now_seconds)
        if outcome.allowed:
            for spec in buckets:
                for _ in range(spec.cost):
                    self._append_replay_event(
                        key=spec.key,
                        window_seconds=spec.window_seconds,
                        timestamp_seconds=now_seconds,
                    )
        return outcome

    def bind_once(self, *, key: str, value: str, ttl_seconds: int) -> BindingOutcome:
        self._maybe_refresh_redis()
        redis_backend = self._get_redis_backend()
        if redis_backend is not None:
            try:
                outcome = redis_backend.bind_once(key=key, value=value, ttl_seconds=ttl_seconds)
                if outcome.allowed:
                    self._memory.bind_once(key=key, value=value, ttl_seconds=ttl_seconds)
                return outcome
            except Exception as exc:
                logger.warning(
                    "Rate limiter Redis backend failed during bind_once; switching to memory fallback: %s",
                    exc,
                    exc_info=True,
                )
                self._demote_to_memory()
        outcome = self._memory.bind_once(key=key, value=value, ttl_seconds=ttl_seconds)
        if outcome.allowed:
            self._append_replay_binding(key=key, value=value, ttl_seconds=ttl_seconds)
        return outcome

    def bind_device_to_user(
        self,
        *,
        device_key: str,
        user_slot_prefix: str,
        user_id: str,
        device_id: str,
        ttl_seconds: int,
        max_devices: int,
    ) -> BindingOutcome:
        self._maybe_refresh_redis()
        redis_backend = self._get_redis_backend()
        kwargs = dict(
            device_key=device_key,
            user_slot_prefix=user_slot_prefix,
            user_id=user_id,
            device_id=device_id,
            ttl_seconds=ttl_seconds,
            max_devices=max_devices,
        )
        if redis_backend is not None:
            try:
                outcome = redis_backend.bind_device_to_user(**kwargs)
                if outcome.allowed:
                    self._memory.bind_device_to_user(**kwargs)
                return outcome
            except Exception as exc:
                logger.warning(
                    "Rate limiter Redis backend failed during device binding; switching to memory fallback: %s",
                    exc,
                    exc_info=True,
                )
                self._demote_to_memory()
        outcome = self._memory.bind_device_to_user(**kwargs)
        if outcome.allowed:
            with self._lock:
                self._replay_device_bindings[(user_slot_prefix, device_id)] = (
                    device_key,
                    user_slot_prefix,
                    user_id,
                    ttl_seconds,
                    max_devices,
                )
        return outcome

    def acquire_lease(
        self,
        *,
        key: str,
        limit: int,
        lease_seconds: int,
    ) -> LeaseOutcome:
        token = secrets.token_urlsafe(24)
        now_seconds = int(time.time())
        self._maybe_refresh_redis()
        redis_backend = self._get_redis_backend()
        kwargs = dict(
            key=key,
            limit=limit,
            lease_seconds=lease_seconds,
            token=token,
            now_seconds=now_seconds,
        )
        if redis_backend is not None:
            try:
                outcome = redis_backend.acquire_lease(**kwargs)
                if outcome.allowed:
                    self._memory.acquire_lease(**kwargs)
                return outcome
            except Exception as exc:
                logger.warning(
                    "Rate limiter Redis backend failed during concurrency admission; switching to memory fallback: %s",
                    exc,
                    exc_info=True,
                )
                self._demote_to_memory()
        return self._memory.acquire_lease(**kwargs)

    def release_lease(self, *, key: str, token: str) -> None:
        self._memory.release_lease(key=key, token=token)
        redis_backend = self._get_redis_backend()
        if redis_backend is not None:
            try:
                redis_backend.release_lease(key=key, token=token)
            except Exception:
                logger.warning("Could not release Redis rate-limit lease.", exc_info=True)

    def _append_replay_event(self, *, key: str, window_seconds: int, timestamp_seconds: int) -> None:
        with self._lock:
            queue = self._replay_events[key]
            queue.append(timestamp_seconds)
            self._replay_windows[key] = window_seconds
            while len(queue) > self._replay_max_events_per_bucket:
                queue.popleft()

    def _append_replay_binding(self, *, key: str, value: str, ttl_seconds: int) -> None:
        with self._lock:
            self._replay_bindings[key] = (value, int(ttl_seconds))

    def _maybe_refresh_redis(self, *, force: bool = False) -> None:
        now_monotonic = time.monotonic()
        with self._lock:
            if (
                not force
                and now_monotonic - self._last_health_check_monotonic
                < self._health_check_interval_seconds
            ):
                return
            self._last_health_check_monotonic = now_monotonic
        try:
            redis_client = self._redis_client_factory()
            if redis_client is None:
                raise RuntimeError("Redis client factory returned None.")
            redis_client.ping()
            candidate_backend = RedisSlidingWindowLimiter(redis_client)
            self._replay_buffer_to_redis(candidate_backend)
            with self._lock:
                self._redis_client = redis_client
                self._redis_backend = candidate_backend
            logger.info("Rate limiter switched to Redis backend.")
        except Exception as exc:
            if self._get_redis_backend() is not None:
                logger.warning(
                    "Rate limiter lost Redis connectivity; continuing on memory fallback: %s",
                    exc,
                    exc_info=True,
                )
            self._demote_to_memory()

    def _replay_buffer_to_redis(self, redis_backend: RedisSlidingWindowLimiter) -> None:
        # Recovery is rare; hold the replay lock through the transfer so events
        # accepted concurrently cannot be appended and then accidentally cleared.
        with self._lock:
            snapshots = [
                (key, self._replay_windows[key], list(events))
                for key, events in self._replay_events.items()
            ]
            binding_snapshots = list(self._replay_bindings.items())
            device_snapshots = list(self._replay_device_bindings.items())

            for key, window_seconds, event_timestamps in snapshots:
                redis_backend.replay_events(
                    key=key,
                    window_seconds=window_seconds,
                    event_timestamps=event_timestamps,
                )
            for key, (value, ttl_seconds) in binding_snapshots:
                redis_backend.bind_once(key=key, value=value, ttl_seconds=ttl_seconds)
            for (_, device_id), (
                device_key,
                user_slot_prefix,
                user_id,
                ttl_seconds,
                max_devices,
            ) in device_snapshots:
                redis_backend.bind_device_to_user(
                    device_key=device_key,
                    user_slot_prefix=user_slot_prefix,
                    user_id=user_id,
                    device_id=device_id,
                    ttl_seconds=ttl_seconds,
                    max_devices=max_devices,
                )

            self._replay_events.clear()
            self._replay_windows.clear()
            self._replay_bindings.clear()
            self._replay_device_bindings.clear()

    def _get_redis_backend(self) -> Optional[RedisSlidingWindowLimiter]:
        with self._lock:
            return self._redis_backend

    def _demote_to_memory(self) -> None:
        with self._lock:
            self._redis_client = None
            self._redis_backend = None


class SharedRateLimiter:
    def __init__(self, backend) -> None:
        self.backend = backend

    @staticmethod
    def _path(request: Request) -> str:
        return str(getattr(request.url, "path", "") or "").rstrip("/")

    def _is_auxiliary_request(self, request: Request) -> bool:
        path = self._path(request)
        return any(path.endswith(suffix) for suffix in AUXILIARY_ROUTE_SUFFIXES)

    def _free_usage_cost(self, request: Request, feature: FeatureType, family: str) -> tuple[int, int, bool]:
        if self._is_auxiliary_request(request):
            return 0, 0, True
        if family == "light":
            return 1, 0, False
        heavy_cost = int(AUTH_FREE_HEAVY_CREDIT_COST.get(feature, 1))
        return heavy_cost, heavy_cost, False

    def _paid_ai_cost(self, request: Request, feature: FeatureType, plan: str) -> int:
        cost = int(PAID_AI_CREDIT_COST.get(feature, 0))
        if not cost:
            return 0
        path = self._path(request)
        if path.endswith("/e-signature/layout"):
            return 0
        if "/batch/" in path:
            cost *= int(PAID_BATCH_COST_MULTIPLIER.get(plan, 1))
        return cost

    def _build_free_buckets(
        self,
        *,
        identity_kind: str,
        identity: str,
        network_id: str,
        policy: RateLimitPolicy,
        total_cost: int,
        heavy_cost: int,
        auxiliary: bool,
    ) -> list[BucketSpec]:
        prefix = f"rate:{policy.tier_name}:{identity_kind}:{identity}"
        is_guest = identity_kind == "anon"
        total_message = (
            "Daily guest limit reached. Sign in for a larger free allowance."
            if is_guest
            else "Daily free-plan allowance reached. Upgrade to Personal for effectively unlimited normal document processing."
        )
        heavy_message = (
            "Daily guest conversion allowance reached. Sign in to unlock the full free feature set."
            if is_guest
            else "Daily free high-cost processing allowance reached. Upgrade to Personal to continue with paid-plan capacity."
        )
        auxiliary_message = (
            "Daily preview/review allowance reached. Upgrade to Personal to continue without the free workflow ceiling."
        )
        buckets: list[BucketSpec] = []
        if total_cost > 0:
            buckets.append(
                BucketSpec(
                    key=f"{prefix}:total",
                    limit=policy.total_limit,
                    window_seconds=policy.total_window_seconds,
                    cost=total_cost,
                    message=total_message,
                )
            )
        if heavy_cost > 0:
            buckets.append(
                BucketSpec(
                    key=f"{prefix}:heavy",
                    limit=policy.heavy_limit,
                    window_seconds=policy.heavy_window_seconds,
                    cost=heavy_cost,
                    message=heavy_message,
                )
            )
        if auxiliary and policy.auxiliary_limit:
            buckets.append(
                BucketSpec(
                    key=f"{prefix}:auxiliary",
                    limit=policy.auxiliary_limit,
                    window_seconds=policy.auxiliary_window_seconds,
                    cost=1,
                    message=auxiliary_message,
                )
            )
        buckets.append(
            BucketSpec(
                key=f"{prefix}:burst",
                limit=policy.burst_limit,
                window_seconds=policy.burst_window_seconds,
                cost=1,
                message="Too many requests in a short time.",
            )
        )
        if policy.network_total_limit and total_cost > 0:
            buckets.append(
                BucketSpec(
                    key=f"rate:{policy.tier_name}:network:{network_id}:total",
                    limit=policy.network_total_limit,
                    window_seconds=policy.total_window_seconds,
                    cost=total_cost,
                    message="Network daily request limit exceeded.",
                )
            )
        if policy.network_heavy_limit and heavy_cost > 0:
            buckets.append(
                BucketSpec(
                    key=f"rate:{policy.tier_name}:network:{network_id}:heavy",
                    limit=policy.network_heavy_limit,
                    window_seconds=policy.heavy_window_seconds,
                    cost=heavy_cost,
                    message="Network daily heavy-feature limit exceeded.",
                )
            )
        if policy.network_burst_limit:
            buckets.append(
                BucketSpec(
                    key=f"rate:{policy.tier_name}:network:{network_id}:burst",
                    limit=policy.network_burst_limit,
                    window_seconds=policy.network_burst_window_seconds,
                    cost=1,
                    message="Too many network requests in a short time.",
                )
            )
        return buckets

    def _enforce_atomic(self, buckets: Sequence[BucketSpec]) -> None:
        outcome = self.backend.enforce_many(buckets)
        if outcome.allowed:
            return
        rejected_index = outcome.rejected_index
        spec = buckets[rejected_index] if rejected_index is not None else None
        message = spec.message if spec is not None else "Rate limit exceeded."
        raise HTTPException(
            status_code=429,
            detail={
                "error": "rate_limit_exceeded",
                "message": message,
                "retry_after_seconds": outcome.retry_after_seconds,
            },
            headers={"Retry-After": str(outcome.retry_after_seconds)},
        )

    def enforce_anonymous(
        self,
        *,
        request: Request,
        response: Response,
        feature: FeatureType,
        policy: RateLimitPolicy,
        allowed_light_features,
        allowed_heavy_features,
        blocked_features,
        family: str,
    ) -> None:
        self._validate_feature(
            feature=feature,
            family=family,
            allowed_light_features=allowed_light_features,
            allowed_heavy_features=allowed_heavy_features,
            blocked_features=blocked_features,
        )
        identity = self._anonymous_identity(request, response)
        network_id = self._network_identity(request)
        total_cost, heavy_cost, auxiliary = self._free_usage_cost(request, feature, family)
        buckets = self._build_free_buckets(
            identity_kind="anon",
            identity=identity,
            network_id=network_id,
            policy=policy,
            total_cost=total_cost,
            heavy_cost=heavy_cost,
            auxiliary=auxiliary,
        )
        self._enforce_atomic(buckets)

    def enforce_authenticated_free(
        self,
        *,
        request: Request,
        response: Response,
        user_id: str,
        feature: FeatureType,
        policy: RateLimitPolicy,
        allowed_light_features,
        allowed_heavy_features,
        blocked_features,
        family: str,
    ) -> None:
        self._validate_feature(
            feature=feature,
            family=family,
            allowed_light_features=allowed_light_features,
            allowed_heavy_features=allowed_heavy_features,
            blocked_features=blocked_features,
        )
        user_key = user_id.strip() or "unknown-user"
        device_id = self._authenticated_free_device_identity(request=request, response=response)
        network_id = self._network_identity(request)
        self._enforce_authenticated_free_bindings(
            policy=policy,
            user_key=user_key,
            device_id=device_id,
            network_id=network_id,
        )
        total_cost, heavy_cost, auxiliary = self._free_usage_cost(request, feature, family)
        buckets = self._build_free_buckets(
            identity_kind="user",
            identity=user_key,
            network_id=network_id,
            policy=policy,
            total_cost=total_cost,
            heavy_cost=heavy_cost,
            auxiliary=auxiliary,
        )
        self._enforce_atomic(buckets)

    def enforce_authenticated_paid(
        self,
        *,
        request: Request,
        user_id: str,
        scope_id: str,
        feature: FeatureType,
        plan: str,
    ) -> Optional[PaidLease]:
        policy = PAID_PLAN_POLICIES.get(plan)
        if policy is None:
            raise ValueError(f"Unsupported paid plan: {plan}")
        scope = (scope_id or user_id).strip() or user_id.strip()
        prefix = f"rate:{policy.tier_name}:scope:{scope}"
        lease: Optional[PaidLease] = None

        if feature in PAID_EXPENSIVE_FEATURES:
            lease_key = f"{prefix}:concurrency"
            lease_outcome = self.backend.acquire_lease(
                key=lease_key,
                limit=policy.concurrent_expensive_jobs,
                lease_seconds=policy.concurrency_lease_seconds,
            )
            if not lease_outcome.allowed or not lease_outcome.token:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "paid_concurrency_limit_exceeded",
                        "message": (
                            "This plan has reached its concurrent high-cost processing safety limit. "
                            "Retry shortly."
                        ),
                        "retry_after_seconds": lease_outcome.retry_after_seconds,
                        "plan": plan,
                    },
                    headers={"Retry-After": str(lease_outcome.retry_after_seconds)},
                )
            lease = PaidLease(key=lease_key, token=lease_outcome.token)

        ai_cost = self._paid_ai_cost(request, feature, plan)
        buckets = [
            BucketSpec(
                key=f"{prefix}:burst",
                limit=policy.burst_limit,
                window_seconds=policy.burst_window_seconds,
                message="Paid-plan safety throughput exceeded. Retry shortly.",
            )
        ]
        if ai_cost > 0:
            buckets.append(
                BucketSpec(
                    key=f"{prefix}:ai_fair_use",
                    limit=policy.ai_credit_limit,
                    window_seconds=policy.ai_window_seconds,
                    cost=ai_cost,
                    message=(
                        "High-cost AI/media fair-use safety ceiling reached for this plan. "
                        "Normal document tools remain unlimited; contact ReDOCX for higher processing capacity."
                    ),
                )
            )
        try:
            self._enforce_atomic(buckets)
        except Exception:
            if lease is not None:
                self.release_paid_lease(lease)
            raise
        return lease

    def release_paid_lease(self, lease: Optional[PaidLease]) -> None:
        if lease is None:
            return
        self.backend.release_lease(key=lease.key, token=lease.token)

    def _enforce_authenticated_free_bindings(
        self,
        *,
        policy: RateLimitPolicy,
        user_key: str,
        device_id: str,
        network_id: str,
    ) -> None:
        ttl_seconds = max(1, int(AUTH_FREE_BINDING_TTL_SECONDS))
        outcome = self.backend.bind_device_to_user(
            device_key=f"rate:{policy.tier_name}:device_binding:{device_id}",
            user_slot_prefix=f"rate:{policy.tier_name}:user_device_binding:{user_key}",
            user_id=user_key,
            device_id=device_id,
            ttl_seconds=ttl_seconds,
            max_devices=AUTH_FREE_MAX_ACTIVE_DEVICES,
        )
        if not outcome.allowed:
            if outcome.reason == "too_many_devices":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "free_account_device_limit_exceeded",
                        "message": (
                            f"This free account is already active on {AUTH_FREE_MAX_ACTIVE_DEVICES} devices. "
                            "Continue on one of those devices or upgrade for unrestricted device access."
                        ),
                        "max_devices": AUTH_FREE_MAX_ACTIVE_DEVICES,
                    },
                )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "free_device_already_bound",
                    "message": (
                        "This device is already linked to a different free account. "
                        "Please use the original free account or upgrade to a paid plan."
                    ),
                },
            )

        if AUTH_FREE_ENFORCE_NETWORK_ACCOUNT_BINDING:
            self._enforce_binding(
                key=f"rate:{policy.tier_name}:network_account_binding:{network_id}",
                value=user_key,
                ttl_seconds=ttl_seconds,
                error="free_network_already_bound",
                message=(
                    "This network is already linked to a different free account. "
                    "Please use the original free account or upgrade to a paid plan."
                ),
            )

    def _enforce_binding(
        self,
        *,
        key: str,
        value: str,
        ttl_seconds: int,
        error: str,
        message: str,
    ) -> None:
        outcome = self.backend.bind_once(key=key, value=value, ttl_seconds=ttl_seconds)
        if outcome.allowed:
            return
        raise HTTPException(status_code=403, detail={"error": error, "message": message})

    def _anonymous_identity(self, request: Request, response: Optional[Response] = None) -> str:
        secret = self._anonymous_device_secret()
        device_id = self._recoverable_device_identity(
            request=request,
            cookie_value=request.cookies.get(ANONYMOUS_DEVICE_COOKIE_NAME),
            purpose="anonymous",
            secret=secret,
        )
        if response is not None:
            response.set_cookie(
                key=ANONYMOUS_DEVICE_COOKIE_NAME,
                value=self._signed_device_cookie(
                    device_id,
                    purpose="anonymous",
                    secret=secret,
                ),
                max_age=ANONYMOUS_DEVICE_COOKIE_MAX_AGE_SECONDS,
                httponly=True,
                secure=ANONYMOUS_DEVICE_COOKIE_SECURE,
                samesite=ANONYMOUS_DEVICE_COOKIE_SAMESITE,
                domain=ANONYMOUS_DEVICE_COOKIE_DOMAIN,
                path="/",
            )
            return f"device:{device_id}"

        if TRUST_CLIENT_DEVICE_HEADERS:
            header_device_id = request.headers.get(DEFAULT_DEVICE_HEADER_NAME)
            if header_device_id:
                return f"device:{header_device_id[:256]}"
            session_id = request.headers.get(DEFAULT_SESSION_HEADER_NAME)
            if session_id:
                return f"session:{session_id[:256]}"
        return f"device:{device_id}"

    def _authenticated_free_device_identity(self, *, request: Request, response: Response) -> str:
        secret = self._authenticated_free_device_secret()
        device_id = self._recoverable_device_identity(
            request=request,
            cookie_value=request.cookies.get(AUTH_FREE_DEVICE_COOKIE_NAME),
            purpose="auth-free",
            secret=secret,
        )
        response.set_cookie(
            key=AUTH_FREE_DEVICE_COOKIE_NAME,
            value=self._signed_device_cookie(device_id, purpose="auth-free", secret=secret),
            max_age=AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            secure=AUTH_FREE_DEVICE_COOKIE_SECURE,
            samesite=AUTH_FREE_DEVICE_COOKIE_SAMESITE,
            domain=AUTH_FREE_DEVICE_COOKIE_DOMAIN,
            path="/",
        )
        return device_id

    def _recoverable_device_identity(
        self,
        *,
        request: Request,
        cookie_value: str | None,
        purpose: str,
        secret: bytes,
    ) -> str:
        """Resolve a browser identity that survives ordinary cookie deletion.

        The signed cookie remains the primary identity. In addition, the server keeps
        a short-lived recovery mapping keyed by an HMAC of trusted network context and
        passive browser headers. Raw IP/User-Agent values are never persisted in rate
        limiter keys. This closes the trivial DevTools "delete cookie, get a new free
        device" bypass while retaining the existing signed-cookie trust boundary.

        This is abuse resistance, not hardware attestation: a determined attacker can
        still change network/browser characteristics. Account-scoped quotas therefore
        remain authoritative for an authenticated user, and Redis should be configured
        in production so recovery bindings survive process restarts.
        """
        signed_device_id = self._verify_signed_device_cookie(
            cookie_value,
            purpose=purpose,
            secret=secret,
        )
        fingerprint = self._device_recovery_fingerprint(
            request=request,
            purpose=purpose,
            secret=secret,
        )
        recovery_key = f"rate:device_recovery:v1:{purpose}:{fingerprint}"
        ttl_seconds = int(DEVICE_RECOVERY_TTL_SECONDS)

        if signed_device_id is not None:
            # Best-effort seed/refresh. A collision must never replace a valid signed
            # cookie, because the cookie is the stronger identity signal.
            self.backend.bind_once(
                key=recovery_key,
                value=signed_device_id,
                ttl_seconds=ttl_seconds,
            )
            return signed_device_id

        candidate = secrets.token_urlsafe(32)
        outcome = self.backend.bind_once(
            key=recovery_key,
            value=candidate,
            ttl_seconds=ttl_seconds,
        )
        if outcome.allowed:
            return candidate

        recovered = str(outcome.existing_value or "").strip()
        if recovered:
            return recovered

        # The only expected way to reach this branch is a binding expiring between
        # the atomic create attempt and the subsequent read. Retry once rather than
        # silently rotating to an unrelated device identity.
        retry = self.backend.bind_once(
            key=recovery_key,
            value=candidate,
            ttl_seconds=ttl_seconds,
        )
        recovered = str(retry.existing_value or "").strip()
        if retry.allowed or not recovered:
            return candidate
        return recovered

    def _device_recovery_fingerprint(
        self,
        *,
        request: Request,
        purpose: str,
        secret: bytes,
    ) -> str:
        components = [f"purpose={purpose}", f"network={self._network_identity(request)}"]
        for header_name in DEVICE_RECOVERY_HEADER_NAMES:
            value = str(request.headers.get(header_name) or "").strip()[:512]
            components.append(f"{header_name}={value}")
        payload = "\n".join(components).encode("utf-8")
        digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        return digest[:40]

    @staticmethod
    def _signed_device_cookie(device_id: str, *, purpose: str, secret: bytes) -> str:
        payload = f"v2.{purpose}.{device_id}"
        signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).digest()
        encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return f"{payload}.{encoded_signature}"

    def _verify_signed_device_cookie(
        self,
        cookie_value: str | None,
        *,
        purpose: str,
        secret: bytes,
    ) -> Optional[str]:
        if not isinstance(cookie_value, str) or not cookie_value.strip():
            return None
        parts = cookie_value.strip().split(".")
        if len(parts) != 4:
            return None
        version, actual_purpose, device_id, signature = parts
        if version != "v2" or actual_purpose != purpose or not device_id:
            return None
        expected = self._signed_device_cookie(
            device_id,
            purpose=purpose,
            secret=secret,
        ).rsplit(".", 1)[1]
        return device_id if hmac.compare_digest(signature, expected) else None

    @staticmethod
    def _authenticated_free_device_secret() -> bytes:
        if isinstance(RATE_LIMIT_DEVICE_SECRET, str) and RATE_LIMIT_DEVICE_SECRET.strip():
            return RATE_LIMIT_DEVICE_SECRET.strip().encode("utf-8")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rate_limit_device_secret_missing",
                "message": (
                    "Device-bound free-tier rate limiting requires RATE_LIMIT_AUTH_FREE_DEVICE_SECRET "
                    "or RATE_LIMIT_DEVICE_SECRET to be configured."
                ),
            },
        )

    @staticmethod
    def _anonymous_device_secret() -> bytes:
        if isinstance(ANONYMOUS_DEVICE_SECRET, str) and ANONYMOUS_DEVICE_SECRET.strip():
            return ANONYMOUS_DEVICE_SECRET.strip().encode("utf-8")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "rate_limit_anonymous_device_secret_missing",
                "message": (
                    "Anonymous device rate limiting requires RATE_LIMIT_ANONYMOUS_DEVICE_SECRET, "
                    "RATE_LIMIT_DEVICE_SECRET, or SECRET_KEY to be configured."
                ),
            },
        )

    @staticmethod
    def _clean_ip(value: str | None) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        candidate = raw.split(",", 1)[0].strip()[:128]
        return candidate

    def _network_identity(self, request: Request) -> str:
        # The Next.js bridge sanitizes the forwarded address before forwarding.
        # Backend deployments that bypass that bridge must ensure their trusted
        # reverse proxy strips client-supplied forwarding headers.
        forwarded_for = self._clean_ip(request.headers.get("x-forwarded-for"))
        if forwarded_for:
            return forwarded_for
        real_ip = self._clean_ip(request.headers.get("x-real-ip"))
        if real_ip:
            return real_ip
        client_host = request.client.host if request.client else None
        return self._clean_ip(client_host) or "unknown"

    def _validate_feature(
        self,
        *,
        feature: FeatureType,
        family: str,
        allowed_light_features,
        allowed_heavy_features,
        blocked_features,
    ) -> None:
        if feature in blocked_features:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_not_available",
                    "message": f"Feature '{self._feature_name(feature)}' is not available for this tier.",
                },
            )
        if family == "light" and feature not in allowed_light_features:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_not_available",
                    "message": f"Feature '{self._feature_name(feature)}' is not available in the light family for this tier.",
                },
            )
        if family == "heavy" and feature not in allowed_heavy_features:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_not_available",
                    "message": f"Feature '{self._feature_name(feature)}' is not available in the heavy family for this tier.",
                },
            )

    @staticmethod
    def _feature_name(feature: FeatureType) -> str:
        return getattr(feature, "value", str(feature))


def _build_redis_client():
    if not REDIS_URL:
        return None
    try:
        import redis
    except Exception as exc:
        logger.warning("redis package is not installed; using memory fallback: %s", exc)
        return None
    return redis.Redis.from_url(
        REDIS_URL,
        decode_responses=False,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=REDIS_CONNECT_TIMEOUT_SECONDS,
        health_check_interval=30,
    )


_shared_rate_limiter: Optional[SharedRateLimiter] = None
_shared_rate_limiter_lock = Lock()


def get_shared_rate_limiter() -> SharedRateLimiter:
    global _shared_rate_limiter
    if _shared_rate_limiter is None:
        with _shared_rate_limiter_lock:
            if _shared_rate_limiter is None:
                backend = ResilientSwitchingLimiterBackend(
                    redis_client_factory=_build_redis_client,
                    health_check_interval_seconds=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
                    replay_max_events_per_bucket=REPLAY_MAX_EVENTS_PER_BUCKET,
                )
                _shared_rate_limiter = SharedRateLimiter(backend)
    return _shared_rate_limiter


ANONYMOUS_POLICY = RateLimitPolicy(
    tier_name="anonymous",
    total_limit=5,
    heavy_limit=2,
    burst_limit=_env_int("RATE_LIMIT_ANONYMOUS_BURST_LIMIT", 3),
    network_burst_limit=_env_int("RATE_LIMIT_ANONYMOUS_NETWORK_BURST_LIMIT", 60),
)

AUTHENTICATED_FREE_POLICY = RateLimitPolicy(
    tier_name="authenticated_free",
    total_limit=_env_int("RATE_LIMIT_AUTH_FREE_TOTAL_CREDITS", 12),
    heavy_limit=_env_int("RATE_LIMIT_AUTH_FREE_HEAVY_CREDITS", 8),
    burst_limit=_env_int("RATE_LIMIT_AUTH_FREE_BURST_LIMIT", 4),
    network_burst_limit=_env_int("RATE_LIMIT_AUTH_FREE_NETWORK_BURST_LIMIT", 120),
    auxiliary_limit=_env_int("RATE_LIMIT_AUTH_FREE_AUXILIARY_PER_DAY", 8),
)


__all__ = [
    "SECONDS_IN_DAY",
    "DEFAULT_BURST_LIMIT",
    "DEFAULT_BURST_WINDOW_SECONDS",
    "DEFAULT_NETWORK_BURST_LIMIT",
    "DEFAULT_NETWORK_BURST_WINDOW_SECONDS",
    "DEFAULT_DEVICE_HEADER_NAME",
    "DEFAULT_SESSION_HEADER_NAME",
    "DEFAULT_FAIL_CLOSED",
    "ANONYMOUS_DEVICE_COOKIE_NAME",
    "ANONYMOUS_DEVICE_COOKIE_MAX_AGE_SECONDS",
    "AUTH_FREE_DEVICE_COOKIE_NAME",
    "AUTH_FREE_DEVICE_COOKIE_MAX_AGE_SECONDS",
    "AUTH_FREE_BINDING_TTL_SECONDS",
    "DEVICE_RECOVERY_TTL_SECONDS",
    "DEVICE_RECOVERY_HEADER_NAMES",
    "AUTH_FREE_MAX_ACTIVE_DEVICES",
    "AUTH_FREE_DEVICE_COOKIE_SAMESITE",
    "AUTH_FREE_DEVICE_COOKIE_SECURE",
    "AUTH_FREE_DEVICE_COOKIE_DOMAIN",
    "AUTH_FREE_ENFORCE_NETWORK_ACCOUNT_BINDING",
    "LIGHT_FEATURES",
    "HEAVY_FEATURES",
    "ANONYMOUS_ALLOWED_LIGHT_FEATURES",
    "ANONYMOUS_ALLOWED_HEAVY_FEATURES",
    "ANONYMOUS_BLOCKED_FEATURES",
    "AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES",
    "AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES",
    "AUTHENTICATED_FREE_BLOCKED_FEATURES",
    "RateLimitPolicy",
    "PaidPlanPolicy",
    "PAID_PLAN_POLICIES",
    "LimitOutcome",
    "BindingOutcome",
    "BucketSpec",
    "MultiLimitOutcome",
    "LeaseOutcome",
    "PaidLease",
    "RedisSlidingWindowLimiter",
    "InMemorySlidingWindowLimiter",
    "ResilientSwitchingLimiterBackend",
    "SharedRateLimiter",
    "get_shared_rate_limiter",
    "ANONYMOUS_POLICY",
    "AUTHENTICATED_FREE_POLICY",
]
