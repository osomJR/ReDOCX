from __future__ import annotations

"""Redis Stream-backed fanout for team realtime events.

Every FastAPI process keeps only its own WebSocket objects. A bounded Redis
Stream distributes event envelopes to every process and lets a temporarily
disconnected process resume from its last stream ID. PostgreSQL remains the
durable source of truth and the outbox retries failed stream appends.
"""

import asyncio
import hashlib
import inspect
import json
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4


logger = logging.getLogger(__name__)

TEAM_REALTIME_REDIS_URL_ENV = "TEAM_REALTIME_REDIS_URL"
TEAM_REALTIME_REDIS_CHANNEL_ENV = "TEAM_REALTIME_REDIS_CHANNEL"
TEAM_REALTIME_REQUIRE_SHARED_BROKER_ENV = "TEAM_REALTIME_REQUIRE_SHARED_BROKER"
TEAM_REALTIME_CONNECTION_LEASE_SECONDS_ENV = "TEAM_REALTIME_CONNECTION_LEASE_SECONDS"
TEAM_REALTIME_STREAM_MAX_LENGTH_ENV = "TEAM_REALTIME_STREAM_MAX_LENGTH"
DEFAULT_TEAM_REALTIME_REDIS_CHANNEL = "redocx:team-realtime:v1"
DEFAULT_TEAM_REALTIME_CONNECTION_LEASE_SECONDS = 90
DEFAULT_TEAM_REALTIME_STREAM_MAX_LENGTH = 20_000


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


async def _close_async_resource(resource: Any) -> None:
    if resource is None:
        return

    close = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if close is None:
        return

    result = close()
    if inspect.isawaitable(result):
        await result


class SharedRealtimeBroker:
    """Cross-process Redis Stream transport with resumable reads."""

    def __init__(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.instance_id = os.getenv("TEAM_REALTIME_INSTANCE_ID", "").strip() or str(
            uuid4()
        )
        self.channel = (
            os.getenv(
                TEAM_REALTIME_REDIS_CHANNEL_ENV,
                DEFAULT_TEAM_REALTIME_REDIS_CHANNEL,
            ).strip()
            or DEFAULT_TEAM_REALTIME_REDIS_CHANNEL
        )
        self.redis_url = (
            os.getenv(TEAM_REALTIME_REDIS_URL_ENV, "").strip()
            or os.getenv("REDIS_URL", "").strip()
        )
        self.required = _env_bool(
            TEAM_REALTIME_REQUIRE_SHARED_BROKER_ENV,
            True,
        )
        self.connection_lease_seconds = _env_int(
            TEAM_REALTIME_CONNECTION_LEASE_SECONDS_ENV,
            DEFAULT_TEAM_REALTIME_CONNECTION_LEASE_SECONDS,
            minimum=45,
            maximum=600,
        )
        self.stream_max_length = _env_int(
            TEAM_REALTIME_STREAM_MAX_LENGTH_ENV,
            DEFAULT_TEAM_REALTIME_STREAM_MAX_LENGTH,
            minimum=1_000,
            maximum=1_000_000,
        )
        self._handler = handler
        self._redis_module: Any = None
        self._publisher: Any = None
        self._subscriber: Any = None
        self._last_stream_id: str | None = None
        self._listener_task: asyncio.Task[None] | None = None
        self._publisher_lock = asyncio.Lock()
        self._stopping = asyncio.Event()

    @property
    def shared_enabled(self) -> bool:
        return bool(self.redis_url and self._publisher is not None)

    async def start(self) -> None:
        if self._listener_task is not None:
            return

        self._stopping.clear()

        if not self.redis_url:
            message = (
                "Team realtime requires TEAM_REALTIME_REDIS_URL or REDIS_URL. "
                "Set TEAM_REALTIME_REQUIRE_SHARED_BROKER=false only for local "
                "single-process development."
            )
            if self.required:
                raise RuntimeError(message)
            logger.warning("%s Falling back to process-local realtime.", message)
            return

        try:
            import redis.asyncio as redis_asyncio
        except ImportError as exc:
            message = (
                "The redis package with asyncio support is required for shared "
                "team realtime."
            )
            if self.required:
                raise RuntimeError(message) from exc
            logger.warning("%s Falling back to process-local realtime.", message)
            return

        self._redis_module = redis_asyncio

        try:
            await self._connect_publisher()
            await self._connect_subscriber()
        except Exception:
            await self.stop()
            if self.required:
                raise
            logger.exception(
                "Shared team realtime broker is unavailable; using local fanout."
            )
            return

        self._listener_task = asyncio.create_task(
            self._listener_loop(),
            name="team-realtime-redis-listener",
        )

    async def stop(self) -> None:
        self._stopping.set()

        listener = self._listener_task
        self._listener_task = None
        if listener is not None:
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass

        await self._close_subscriber()
        await _close_async_resource(self._publisher)
        self._publisher = None

    async def publish(self, envelope: dict[str, Any]) -> bool:
        if not self.redis_url or self._redis_module is None:
            return False

        payload = json.dumps(
            {
                **envelope,
                "broker_protocol": 1,
                "origin_instance_id": self.instance_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

        async with self._publisher_lock:
            for attempt in range(2):
                try:
                    if self._publisher is None:
                        await self._connect_publisher()
                    await self._publisher.xadd(
                        self.channel,
                        {"payload": payload},
                        maxlen=self.stream_max_length,
                        approximate=True,
                    )
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Could not publish team realtime event (attempt %s).",
                        attempt + 1,
                    )
                    await _close_async_resource(self._publisher)
                    self._publisher = None

        return False

    def _connection_key(self, organization_id: int, user_id: str) -> str:
        user_digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        return f"{self.channel}:connections:{organization_id}:{user_digest}"

    async def register_connection(
        self,
        *,
        organization_id: int,
        user_id: str,
        connection_id: str,
    ) -> bool:
        return await self._write_connection_lease(
            organization_id=organization_id,
            user_id=user_id,
            connection_id=connection_id,
        )

    async def refresh_connection(
        self,
        *,
        organization_id: int,
        user_id: str,
        connection_id: str,
    ) -> bool:
        return await self._write_connection_lease(
            organization_id=organization_id,
            user_id=user_id,
            connection_id=connection_id,
        )

    async def _write_connection_lease(
        self,
        *,
        organization_id: int,
        user_id: str,
        connection_id: str,
    ) -> bool:
        if not self.redis_url or self._redis_module is None:
            return False

        key = self._connection_key(organization_id, user_id)
        now = time.time()
        expires_at = now + self.connection_lease_seconds
        key_ttl = int(math.ceil(self.connection_lease_seconds * 2))

        async with self._publisher_lock:
            for attempt in range(2):
                try:
                    if self._publisher is None:
                        await self._connect_publisher()
                    async with self._publisher.pipeline(transaction=True) as pipe:
                        pipe.zremrangebyscore(key, "-inf", now)
                        pipe.zadd(key, {connection_id: expires_at})
                        pipe.expire(key, key_ttl)
                        await pipe.execute()
                    return True
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Could not update team realtime connection lease (attempt %s).",
                        attempt + 1,
                    )
                    await _close_async_resource(self._publisher)
                    self._publisher = None

        return False

    async def unregister_connection_and_check_remaining(
        self,
        *,
        organization_id: int,
        user_id: str,
        connection_id: str,
    ) -> bool | None:
        """Remove a lease and return whether any live cross-process lease remains."""

        if not self.redis_url or self._redis_module is None:
            return None

        key = self._connection_key(organization_id, user_id)
        now = time.time()
        key_ttl = int(math.ceil(self.connection_lease_seconds * 2))

        async with self._publisher_lock:
            for attempt in range(2):
                try:
                    if self._publisher is None:
                        await self._connect_publisher()
                    async with self._publisher.pipeline(transaction=True) as pipe:
                        pipe.zrem(key, connection_id)
                        pipe.zremrangebyscore(key, "-inf", now)
                        pipe.zcard(key)
                        pipe.expire(key, key_ttl)
                        results = await pipe.execute()
                    return int(results[2] or 0) > 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Could not remove team realtime connection lease (attempt %s).",
                        attempt + 1,
                    )
                    await _close_async_resource(self._publisher)
                    self._publisher = None

        return None

    def _new_client(self) -> Any:
        return self._redis_module.from_url(
            self.redis_url,
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
            health_check_interval=30,
            retry_on_timeout=True,
        )

    async def _connect_publisher(self) -> None:
        publisher = self._new_client()
        try:
            await publisher.ping()
        except Exception:
            await _close_async_resource(publisher)
            raise
        self._publisher = publisher

    async def _connect_subscriber(self) -> None:
        subscriber = self._new_client()

        try:
            await subscriber.ping()
            if self._last_stream_id is None:
                latest_entries = await subscriber.xrevrange(
                    self.channel,
                    count=1,
                )
                self._last_stream_id = (
                    str(latest_entries[0][0]) if latest_entries else "0-0"
                )
        except Exception:
            await _close_async_resource(subscriber)
            raise

        self._subscriber = subscriber

    async def _close_subscriber(self) -> None:
        await _close_async_resource(self._subscriber)
        self._subscriber = None

    async def _reconnect_subscriber(self) -> None:
        await self._close_subscriber()
        delay = 0.5

        while not self._stopping.is_set():
            try:
                await self._connect_subscriber()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Could not reconnect team realtime subscriber.")
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 15)

    async def _listener_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                if self._subscriber is None:
                    await self._reconnect_subscriber()
                    continue

                streams = await self._subscriber.xread(
                    {self.channel: self._last_stream_id or "0-0"},
                    count=100,
                    block=1_000,
                )
                if not streams:
                    continue

                for _stream_name, entries in streams:
                    for stream_id, fields in entries:
                        self._last_stream_id = str(stream_id)
                        raw_data = fields.get("payload")
                        if isinstance(raw_data, bytes):
                            raw_data = raw_data.decode("utf-8")
                        envelope = json.loads(str(raw_data))

                        if not isinstance(envelope, dict):
                            continue
                        if envelope.get("origin_instance_id") == self.instance_id:
                            continue

                        try:
                            await self._handler(envelope)
                        except Exception:
                            logger.exception(
                                "Could not deliver shared team realtime event."
                            )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Team realtime subscriber connection failed.")
                await self._reconnect_subscriber()
