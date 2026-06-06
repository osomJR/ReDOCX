from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Optional
import logging
import os
import time

from fastapi import HTTPException, Request

from src.schema import FeatureType
logger = logging.getLogger(__name__)

SECONDS_IN_DAY = 24 * 60 * 60

DEFAULT_BURST_LIMIT = int(os.getenv("RATE_LIMIT_BURST_LIMIT", "2"))
DEFAULT_BURST_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_BURST_WINDOW_SECONDS", "10"))
DEFAULT_NETWORK_BURST_LIMIT = int(os.getenv("RATE_LIMIT_NETWORK_BURST_LIMIT", "10"))
DEFAULT_NETWORK_BURST_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_NETWORK_BURST_WINDOW_SECONDS", "10"))
DEFAULT_DEVICE_HEADER_NAME = os.getenv("RATE_LIMIT_DEVICE_HEADER_NAME", "x-device-id")
DEFAULT_SESSION_HEADER_NAME = os.getenv("RATE_LIMIT_SESSION_HEADER_NAME", "x-session-id")
DEFAULT_FAIL_CLOSED = os.getenv("RATE_LIMIT_FAIL_CLOSED", "true").strip().lower() not in {"0", "false", "no"}

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

        # PDF Tools + E-Signature are treated as heavy features.
        # They follow the same tier behavior as compliance, structured extraction,
        # redaction, and data masking:
        # - blocked for anonymous users
        # - allowed for authenticated-free users within the heavy quota
        # - unlimited for paid users through the paid-plan guards
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
    total_window_seconds: int = SECONDS_IN_DAY
    heavy_window_seconds: int = SECONDS_IN_DAY
    fail_closed: bool = DEFAULT_FAIL_CLOSED

    def __post_init__(self) -> None:
        for field_name in (
            "total_limit",
            "heavy_limit",
            "burst_limit",
            "burst_window_seconds",
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
        ):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, int) or value < 1):
                raise ValueError(f"{field_name} must be None or an int >= 1.")

        if not isinstance(self.tier_name, str) or not self.tier_name.strip():
            raise ValueError("tier_name must be a non-empty string.")


@dataclass(frozen=True)
class LimitOutcome:
    allowed: bool
    current_count: int
    retry_after_seconds: int


class RedisSlidingWindowLimiter:
    """
    Atomic Redis-backed sliding-window limiter.

    Uses one sorted set per logical bucket and enforces limits through Lua so the
    prune/count/add sequence is atomic across concurrent workers.
    """

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

    def __init__(self, redis_client) -> None:
        self.redis = redis_client
        self._enforce_script = self.redis.register_script(self._LUA_ENFORCE)

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


class InMemorySlidingWindowLimiter:
    """
    Single-process in-memory sliding window.

    This is used as:
    - a hot mirror while Redis is healthy, so failover preserves current windows
    - the active limiter when Redis is unavailable
    """

    def __init__(self) -> None:
        self._buckets: dict[str, deque[int]] = defaultdict(deque)
        self._lock = Lock()

    def enforce(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        now_seconds: Optional[int] = None,
    ) -> LimitOutcome:
        now = int(now_seconds if now_seconds is not None else time.time())

        with self._lock:
            bucket = self._buckets[key]
            cutoff = now - window_seconds

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            current_count = len(bucket)
            if current_count >= limit:
                retry_after = max(1, (bucket[0] + window_seconds) - now)
                return LimitOutcome(
                    allowed=False,
                    current_count=current_count,
                    retry_after_seconds=retry_after,
                )

            bucket.append(now)
            return LimitOutcome(
                allowed=True,
                current_count=current_count + 1,
                retry_after_seconds=0,
            )

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
            cutoff = now - window_seconds

            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            bucket.append(now)

    def prune(self) -> None:
        now = int(time.time())

        with self._lock:
            empty_keys = []

            for key, bucket in self._buckets.items():
                max_known_window = SECONDS_IN_DAY
                cutoff = now - max_known_window

                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()

                if not bucket:
                    empty_keys.append(key)

            for key in empty_keys:
                self._buckets.pop(key, None)


class ResilientSwitchingLimiterBackend:
    """
    Preferred behavior:
    - use Redis when healthy
    - keep an in-memory hot mirror of accepted requests
    - if Redis goes down, enforce with memory immediately
    - record outage-era accepted requests in a replay buffer
    - when Redis returns, replay buffered events back into Redis, then switch back

    This preserves rate-limit continuity within a running process.
    It is not crash-durable during an outage.
    """

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

        self._maybe_refresh_redis(force=True)

    def enforce(self, *, key: str, limit: int, window_seconds: int) -> LimitOutcome:
        now_seconds = int(time.time())

        self._maybe_refresh_redis()

        redis_backend = self._get_redis_backend()
        if redis_backend is not None:
            try:
                outcome = redis_backend.enforce(
                    key=key,
                    limit=limit,
                    window_seconds=window_seconds,
                    now_seconds=now_seconds,
                )
                if outcome.allowed:
                    self._memory.record(
                        key=key,
                        window_seconds=window_seconds,
                        timestamp_seconds=now_seconds,
                    )
                return outcome
            except Exception as exc:
                logger.warning(
                    "Rate limiter Redis backend failed during enforce; switching to memory fallback: %s",
                    exc,
                    exc_info=True,
                )
                self._demote_to_memory()

        outcome = self._memory.enforce(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
            now_seconds=now_seconds,
        )

        if outcome.allowed:
            self._append_replay_event(
                key=key,
                window_seconds=window_seconds,
                timestamp_seconds=now_seconds,
            )

        return outcome

    def _append_replay_event(
        self,
        *,
        key: str,
        window_seconds: int,
        timestamp_seconds: int,
    ) -> None:
        with self._lock:
            queue = self._replay_events[key]
            queue.append(timestamp_seconds)
            self._replay_windows[key] = window_seconds

            while len(queue) > self._replay_max_events_per_bucket:
                queue.popleft()

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
        with self._lock:
            if not self._replay_events:
                return

            snapshots = [
                (key, self._replay_windows[key], list(events))
                for key, events in self._replay_events.items()
            ]

            for key, window_seconds, event_timestamps in snapshots:
                redis_backend.replay_events(
                    key=key,
                    window_seconds=window_seconds,
                    event_timestamps=event_timestamps,
                )

            self._replay_events.clear()
            self._replay_windows.clear()

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

    def enforce_anonymous(
        self,
        *,
        request: Request,
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

        identity = self._anonymous_identity(request)

        self._enforce_bucket(
            key=f"rate:{policy.tier_name}:anon:{identity}:total",
            limit=policy.total_limit,
            window_seconds=policy.total_window_seconds,
            message="Daily request limit exceeded.",
        )

        if family == "heavy":
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:anon:{identity}:heavy",
                limit=policy.heavy_limit,
                window_seconds=policy.heavy_window_seconds,
                message="Daily heavy-feature limit exceeded.",
            )

        self._enforce_bucket(
            key=f"rate:{policy.tier_name}:anon:{identity}:burst",
            limit=policy.burst_limit,
            window_seconds=policy.burst_window_seconds,
            message="Too many requests in a short time.",
        )

        network_id = self._network_identity(request)
        if policy.network_total_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:total",
                limit=policy.network_total_limit,
                window_seconds=policy.total_window_seconds,
                message="Network daily request limit exceeded.",
            )

        if family == "heavy" and policy.network_heavy_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:heavy",
                limit=policy.network_heavy_limit,
                window_seconds=policy.heavy_window_seconds,
                message="Network daily heavy-feature limit exceeded.",
            )

        if policy.network_burst_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:burst",
                limit=policy.network_burst_limit,
                window_seconds=policy.network_burst_window_seconds,
                message="Too many network requests in a short time.",
            )

    def enforce_authenticated_free(
        self,
        *,
        request: Request,
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

        self._enforce_bucket(
            key=f"rate:{policy.tier_name}:user:{user_key}:total",
            limit=policy.total_limit,
            window_seconds=policy.total_window_seconds,
            message="Daily request limit exceeded.",
        )

        if family == "heavy":
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:user:{user_key}:heavy",
                limit=policy.heavy_limit,
                window_seconds=policy.heavy_window_seconds,
                message="Daily heavy-feature limit exceeded.",
            )

        self._enforce_bucket(
            key=f"rate:{policy.tier_name}:user:{user_key}:burst",
            limit=policy.burst_limit,
            window_seconds=policy.burst_window_seconds,
            message="Too many requests in a short time.",
        )

        network_id = self._network_identity(request)
        if policy.network_total_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:total",
                limit=policy.network_total_limit,
                window_seconds=policy.total_window_seconds,
                message="Network daily request limit exceeded.",
            )

        if family == "heavy" and policy.network_heavy_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:heavy",
                limit=policy.network_heavy_limit,
                window_seconds=policy.heavy_window_seconds,
                message="Network daily heavy-feature limit exceeded.",
            )

        if policy.network_burst_limit:
            self._enforce_bucket(
                key=f"rate:{policy.tier_name}:network:{network_id}:burst",
                limit=policy.network_burst_limit,
                window_seconds=policy.network_burst_window_seconds,
                message="Too many network requests in a short time.",
            )

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

    def _enforce_bucket(self, *, key: str, limit: int, window_seconds: int, message: str) -> None:
        outcome = self.backend.enforce(
            key=key,
            limit=limit,
            window_seconds=window_seconds,
        )
        if not outcome.allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limit_exceeded",
                    "message": message,
                    "retry_after_seconds": outcome.retry_after_seconds,
                },
                headers={"Retry-After": str(outcome.retry_after_seconds)},
            )

    def _anonymous_identity(self, request: Request) -> str:
        device_id = request.headers.get(DEFAULT_DEVICE_HEADER_NAME)
        if device_id:
            return f"device:{device_id}"

        session_id = request.headers.get(DEFAULT_SESSION_HEADER_NAME)
        if session_id:
            return f"session:{session_id}"

        return f"ip:{self._network_identity(request)}"

    def _network_identity(self, request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        client_host = request.client.host if request.client else None
        return client_host or "unknown"

    def _feature_name(self, feature: FeatureType) -> str:
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
)

AUTHENTICATED_FREE_POLICY = RateLimitPolicy(
    tier_name="authenticated_free",
    total_limit=7,
    heavy_limit=3,
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
    "LIGHT_FEATURES",
    "HEAVY_FEATURES",
    "ANONYMOUS_ALLOWED_LIGHT_FEATURES",
    "ANONYMOUS_ALLOWED_HEAVY_FEATURES",
    "ANONYMOUS_BLOCKED_FEATURES",
    "AUTHENTICATED_FREE_ALLOWED_LIGHT_FEATURES",
    "AUTHENTICATED_FREE_ALLOWED_HEAVY_FEATURES",
    "AUTHENTICATED_FREE_BLOCKED_FEATURES",
    "RateLimitPolicy",
    "LimitOutcome",
    "RedisSlidingWindowLimiter",
    "InMemorySlidingWindowLimiter",
    "ResilientSwitchingLimiterBackend",
    "SharedRateLimiter",
    "get_shared_rate_limiter",
    "ANONYMOUS_POLICY",
    "AUTHENTICATED_FREE_POLICY",
]