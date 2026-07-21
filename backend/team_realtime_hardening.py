from __future__ import annotations

"""Security and protocol controls shared by ReDOCX realtime endpoints."""

import asyncio
import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

from backend.team_realtime_contract_generated import (
    REALTIME_CLIENT_EVENT_TYPES,
    REALTIME_EVENT_VERSION,
)

REALTIME_AUTH_MESSAGE_TYPES = frozenset({"auth", "authenticate", "auth.refresh"})


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


def is_production() -> bool:
    environment = (
        os.getenv("APP_ENV", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
        or os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip()
    ).lower()
    return environment in {"production", "prod"}


def realtime_max_frame_bytes() -> int:
    return _env_int(
        "TEAM_REALTIME_MAX_FRAME_BYTES",
        64 * 1024,
        minimum=1024,
        maximum=1024 * 1024,
    )


def realtime_max_outgoing_frame_bytes() -> int:
    return _env_int(
        "TEAM_REALTIME_MAX_OUTGOING_FRAME_BYTES",
        256 * 1024,
        minimum=4096,
        maximum=2 * 1024 * 1024,
    )


def realtime_heartbeat_timeout_seconds() -> int:
    return _env_int(
        "TEAM_REALTIME_HEARTBEAT_TIMEOUT_SECONDS",
        70,
        minimum=30,
        maximum=300,
    )


def realtime_token_refresh_skew_seconds() -> int:
    return _env_int(
        "TEAM_REALTIME_TOKEN_REFRESH_SKEW_SECONDS",
        90,
        minimum=30,
        maximum=600,
    )


def realtime_connections_per_user() -> int:
    return _env_int(
        "TEAM_REALTIME_MAX_CONNECTIONS_PER_USER",
        6,
        minimum=1,
        maximum=50,
    )


def account_realtime_connections_per_user() -> int:
    return _env_int(
        "ACCOUNT_REALTIME_MAX_CONNECTIONS_PER_USER",
        4,
        minimum=1,
        maximum=25,
    )


def realtime_queue_size() -> int:
    return _env_int(
        "TEAM_REALTIME_OUTGOING_QUEUE_SIZE",
        128,
        minimum=8,
        maximum=4096,
    )


def websocket_allowed_origins() -> set[str]:
    raw = (
        os.getenv("WEBSOCKET_ALLOWED_ORIGINS", "").strip()
        or os.getenv("CORS_ALLOW_ORIGINS", "").strip()
    )
    origins = {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}
    origins.discard("*")

    for name in ("APP_BASE_URL", "FRONTEND_URL", "NEXT_PUBLIC_APP_URL"):
        value = os.getenv(name, "").strip().rstrip("/")
        if value.startswith(("http://", "https://")):
            origins.add(value)

    if is_production():
        origins.update({"https://redocx.app", "https://www.redocx.app"})
    else:
        origins.update({
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://localhost:3000",
        })
    return origins


def normalize_origin(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def validate_websocket_origin(websocket: WebSocket) -> None:
    origin = normalize_origin(websocket.headers.get("origin"))
    allow_missing = _env_bool(
        "WEBSOCKET_ALLOW_MISSING_ORIGIN",
        not is_production(),
    )
    if not origin:
        if allow_missing:
            return
        raise HTTPException(
            status_code=403,
            detail={
                "error": "websocket_origin_required",
                "message": "A valid WebSocket Origin header is required.",
            },
        )

    if origin not in websocket_allowed_origins():
        raise HTTPException(
            status_code=403,
            detail={
                "error": "websocket_origin_denied",
                "message": "This WebSocket origin is not allowed.",
            },
        )


async def receive_json_frame(
    websocket: WebSocket,
    *,
    timeout: float | None = None,
) -> dict[str, Any]:
    async def _receive() -> dict[str, Any]:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            raise WebSocketDisconnect(code=int(message.get("code") or 1000))
        if message_type != "websocket.receive":
            raise ValueError("Unsupported WebSocket message type.")

        raw_text = message.get("text")
        raw_bytes = message.get("bytes")
        if raw_text is not None:
            encoded = raw_text.encode("utf-8")
            payload_text = raw_text
        elif raw_bytes is not None:
            encoded = bytes(raw_bytes)
            try:
                payload_text = encoded.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Realtime frames must contain UTF-8 JSON.") from exc
        else:
            raise ValueError("Realtime frame is empty.")

        if len(encoded) > realtime_max_frame_bytes():
            await websocket.close(code=1009, reason="Realtime frame is too large")
            raise WebSocketDisconnect(code=1009)

        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ValueError("Realtime frame must contain valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Realtime frame must be a JSON object.")
        return payload

    if timeout is None:
        return await _receive()
    return await asyncio.wait_for(_receive(), timeout=timeout)


def event_with_contract(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("event_version", REALTIME_EVENT_VERSION)
    normalized.setdefault("occurred_at", time.time())
    return normalized


def validate_client_event_contract(payload: dict[str, Any]) -> None:
    raw_version = payload.get("event_version", REALTIME_EVENT_VERSION)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError("event_version must be an integer.") from exc
    if version != REALTIME_EVENT_VERSION:
        raise ValueError(
            f"Unsupported realtime event_version {version}; expected {REALTIME_EVENT_VERSION}."
        )
    event_type = str(payload.get("type") or "").strip().lower()
    if event_type not in REALTIME_CLIENT_EVENT_TYPES:
        raise ValueError(f"Unsupported realtime client event type: {event_type or 'missing'}.")


def token_expiry_epoch(current_user: Any) -> int | None:
    claims = getattr(current_user, "claims", None)
    if not isinstance(claims, dict):
        return None
    try:
        return int(claims.get("exp"))
    except (TypeError, ValueError):
        return None


def token_is_expired(current_user: Any, *, now: float | None = None) -> bool:
    expiry = token_expiry_epoch(current_user)
    return bool(expiry is not None and expiry <= int(now or time.time()))


def token_needs_refresh(current_user: Any, *, now: float | None = None) -> bool:
    expiry = token_expiry_epoch(current_user)
    if expiry is None:
        return False
    return expiry - int(now or time.time()) <= realtime_token_refresh_skew_seconds()


def ensure_same_reauthenticated_user(existing_user: Any, refreshed_user: Any) -> None:
    existing_id = str(getattr(existing_user, "user_id", "") or "")
    refreshed_id = str(getattr(refreshed_user, "user_id", "") or "")
    if not existing_id or existing_id != refreshed_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "realtime_identity_change_denied",
                "message": "A realtime connection cannot change authenticated identity.",
            },
        )


