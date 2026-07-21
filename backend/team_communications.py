from __future__ import annotations

"""
Team communications API.

Responsibilities:
- Business/Enterprise-only entitlement guard for team communication features
- direct message and group conversation records
- conversation membership
- message persistence
- call-session records for 1-to-1 and group calls
- call participant state
- member presence

Notes:
- LiveKit access tokens are generated server-side from LIVEKIT_API_KEY,
  LIVEKIT_API_SECRET, and LIVEKIT_URL.
- Messaging/calls are organization-scoped and require an active organization
  membership plus an active Business/Enterprise organization entitlement.
"""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, field_validator, model_validator
from psycopg import errors as psycopg_errors
from psycopg.types.json import Jsonb

from backend.auth0_dependencies import AuthenticatedUser, authenticate_access_token, get_current_user
from backend.database import get_db
from backend.team_attachment_security import (
    TeamAttachmentSecurityError,
    get_team_secure_attachment_org_quota_bytes,
    stage_forwarded_team_attachment,
)
from backend.team_realtime_broker import SharedRealtimeBroker
from backend.team_realtime_hardening import (
    REALTIME_EVENT_VERSION,
    account_realtime_connections_per_user,
    ensure_same_reauthenticated_user,
    event_with_contract,
    receive_json_frame,
    realtime_connections_per_user,
    realtime_heartbeat_timeout_seconds,
    realtime_max_outgoing_frame_bytes,
    realtime_queue_size,
    token_is_expired,
    token_needs_refresh,
    validate_client_event_contract,
    validate_websocket_origin,
)


router = APIRouter(tags=["team_communications"])
logger = logging.getLogger(__name__)

LIVEKIT_API_KEY_ENV = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET_ENV = "LIVEKIT_API_SECRET"
LIVEKIT_URL_ENV = "LIVEKIT_URL"
LIVEKIT_TOKEN_TTL_MINUTES_ENV = "LIVEKIT_TOKEN_TTL_MINUTES"
DEFAULT_LIVEKIT_TOKEN_TTL_MINUTES = 10
TEAM_CALL_RING_TIMEOUT_SECONDS_ENV = "TEAM_CALL_RING_TIMEOUT_SECONDS"
TEAM_CALL_REAPER_INTERVAL_SECONDS_ENV = "TEAM_CALL_REAPER_INTERVAL_SECONDS"
DEFAULT_TEAM_CALL_RING_TIMEOUT_SECONDS = 90
DEFAULT_TEAM_CALL_REAPER_INTERVAL_SECONDS = 15
MAX_LIVEKIT_WEBHOOK_BYTES = 256 * 1024

TEAM_REALTIME_AUTH_RECHECK_SECONDS_ENV = "TEAM_REALTIME_AUTH_RECHECK_SECONDS"
TEAM_REALTIME_OUTBOX_POLL_SECONDS_ENV = "TEAM_REALTIME_OUTBOX_POLL_SECONDS"
TEAM_REALTIME_OUTBOX_BATCH_SIZE_ENV = "TEAM_REALTIME_OUTBOX_BATCH_SIZE"
TEAM_REALTIME_OUTBOX_MAX_ATTEMPTS_ENV = "TEAM_REALTIME_OUTBOX_MAX_ATTEMPTS"
TEAM_REALTIME_OUTBOX_LEASE_SECONDS_ENV = "TEAM_REALTIME_OUTBOX_LEASE_SECONDS"
TEAM_REALTIME_OUTBOX_CONCURRENCY_ENV = "TEAM_REALTIME_OUTBOX_CONCURRENCY"
DEFAULT_TEAM_REALTIME_AUTH_RECHECK_SECONDS = 10
DEFAULT_TEAM_REALTIME_OUTBOX_POLL_SECONDS = 0.5
DEFAULT_TEAM_REALTIME_OUTBOX_BATCH_SIZE = 10
DEFAULT_TEAM_REALTIME_OUTBOX_MAX_ATTEMPTS = 50
DEFAULT_TEAM_REALTIME_OUTBOX_LEASE_SECONDS = 300
DEFAULT_TEAM_REALTIME_OUTBOX_CONCURRENCY = 5
TEAM_REALTIME_WEBSOCKET_SEND_TIMEOUT_SECONDS = 3

ConversationType = Literal["dm", "group"]
ConversationStatus = Literal["active", "archived"]
ConversationRole = Literal["owner", "admin", "member"]
PresenceStatus = Literal["online", "offline", "in_call"]


@dataclass
class _ManagedRealtimeSocket:
    websocket: WebSocket
    queue: asyncio.Queue[dict[str, Any]]
    writer_task: asyncio.Task[None] | None = None


class RealtimeConnectionManager:
    """Process-local sockets with bounded per-socket outgoing queues.

    Redis owns cross-process connection truth. This manager only owns the
    WebSocket objects that physically exist in this worker.
    """

    def __init__(self) -> None:
        self._connections: dict[int, dict[str, dict[WebSocket, _ManagedRealtimeSocket]]] = {}
        self._account_connections_by_user_id: dict[str, dict[WebSocket, _ManagedRealtimeSocket]] = {}
        self._account_connections_by_email: dict[str, dict[WebSocket, _ManagedRealtimeSocket]] = {}
        self._account_connection_index: dict[WebSocket, tuple[str, str | None]] = {}
        self._socket_state_index: dict[WebSocket, _ManagedRealtimeSocket] = {}
        self._lock = anyio.Lock()

    async def _writer_loop(self, state: _ManagedRealtimeSocket) -> None:
        try:
            while True:
                event = await state.queue.get()
                await asyncio.wait_for(
                    state.websocket.send_json(event),
                    timeout=TEAM_REALTIME_WEBSOCKET_SEND_TIMEOUT_SECONDS,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await state.websocket.close(
                    code=1013,
                    reason="Realtime client is not consuming events fast enough",
                )
            except Exception:
                pass
            await self._discard_websocket(state.websocket, cancel_writer=False)

    def _new_state(self, websocket: WebSocket) -> _ManagedRealtimeSocket:
        state = _ManagedRealtimeSocket(
            websocket=websocket,
            queue=asyncio.Queue(maxsize=realtime_queue_size()),
        )
        state.writer_task = asyncio.create_task(
            self._writer_loop(state),
            name=f"team-realtime-writer:{id(websocket)}",
        )
        return state

    async def _discard_websocket(
        self,
        websocket: WebSocket,
        *,
        cancel_writer: bool = True,
    ) -> None:
        writer: asyncio.Task[None] | None = None
        async with self._lock:
            indexed_state = self._socket_state_index.pop(websocket, None)
            if indexed_state is not None:
                writer = indexed_state.writer_task
            for organization_id, organization_connections in list(self._connections.items()):
                for user_id, user_connections in list(organization_connections.items()):
                    state = user_connections.pop(websocket, None)
                    if state is not None:
                        writer = state.writer_task
                    if not user_connections:
                        organization_connections.pop(user_id, None)
                if not organization_connections:
                    self._connections.pop(organization_id, None)

            indexed = self._account_connection_index.pop(websocket, None)
            if indexed:
                user_id, email = indexed
                user_connections = self._account_connections_by_user_id.get(user_id)
                if user_connections:
                    state = user_connections.pop(websocket, None)
                    if state is not None:
                        writer = state.writer_task
                    if not user_connections:
                        self._account_connections_by_user_id.pop(user_id, None)
                if email:
                    email_connections = self._account_connections_by_email.get(email)
                    if email_connections:
                        email_connections.pop(websocket, None)
                        if not email_connections:
                            self._account_connections_by_email.pop(email, None)

        current = asyncio.current_task()
        if cancel_writer and writer is not None and writer is not current:
            writer.cancel()
            try:
                await writer
            except asyncio.CancelledError:
                pass

    async def _enqueue(
        self,
        state: _ManagedRealtimeSocket,
        event: dict[str, Any],
    ) -> bool:
        normalized_event = normalize_realtime_event(event)
        encoded_size = len(
            json.dumps(normalized_event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        if encoded_size > realtime_max_outgoing_frame_bytes():
            logger.error(
                "Realtime event exceeded outgoing frame limit. type=%s bytes=%s",
                normalized_event.get("type"),
                encoded_size,
            )
            return False
        try:
            state.queue.put_nowait(normalized_event)
            return True
        except asyncio.QueueFull:
            try:
                await state.websocket.close(
                    code=1013,
                    reason="Realtime outgoing queue exceeded",
                )
            except Exception:
                pass
            await self._discard_websocket(state.websocket)
            return False

    async def send_socket(
        self,
        websocket: WebSocket,
        event: dict[str, Any],
    ) -> bool:
        async with self._lock:
            state = self._socket_state_index.get(websocket)
        if state is not None:
            return await self._enqueue(state, event)
        await websocket.send_json(normalize_realtime_event(event))
        return True

    async def connect(
        self,
        organization_id: int,
        user_id: str,
        websocket: WebSocket,
    ) -> bool:
        async with self._lock:
            organization_connections = self._connections.setdefault(organization_id, {})
            user_connections = organization_connections.setdefault(user_id, {})
            if len(user_connections) >= realtime_connections_per_user():
                return False
            state = self._new_state(websocket)
            user_connections[websocket] = state
            self._socket_state_index[websocket] = state
        return True

    async def disconnect(
        self,
        organization_id: int,
        user_id: str,
        websocket: WebSocket,
    ) -> None:
        del organization_id, user_id
        await self._discard_websocket(websocket)

    async def connect_account(
        self,
        *,
        user_id: str,
        email: str | None,
        websocket: WebSocket,
    ) -> bool:
        normalized_email = normalize_realtime_email(email)
        async with self._lock:
            user_connections = self._account_connections_by_user_id.setdefault(user_id, {})
            if len(user_connections) >= account_realtime_connections_per_user():
                return False
            state = self._new_state(websocket)
            user_connections[websocket] = state
            self._socket_state_index[websocket] = state
            if normalized_email:
                self._account_connections_by_email.setdefault(normalized_email, {})[websocket] = state
            self._account_connection_index[websocket] = (user_id, normalized_email)
        return True

    async def disconnect_account(self, websocket: WebSocket) -> None:
        await self._discard_websocket(websocket)

    async def has_user_connections(self, organization_id: int, user_id: str) -> bool:
        async with self._lock:
            return bool(self._connections.get(organization_id, {}).get(user_id, {}))

    async def local_connection_counts(
        self,
        organization_id: int,
        user_ids: list[str] | set[str],
    ) -> dict[str, int]:
        async with self._lock:
            organization_connections = self._connections.get(organization_id, {})
            return {
                str(user_id): len(organization_connections.get(str(user_id), {}))
                for user_id in user_ids
                if str(user_id).strip()
            }

    async def revoke_organization_user(
        self,
        organization_id: int,
        user_id: str,
        event: dict[str, Any],
    ) -> int:
        async with self._lock:
            states = list(
                self._connections.get(organization_id, {}).get(user_id, {}).values()
            )
        for state in states:
            await self._enqueue(state, event)
            try:
                await state.websocket.close(code=1008, reason="Organization access revoked")
            except Exception:
                pass
            await self._discard_websocket(state.websocket)
        return len(states)

    async def broadcast_to_users(
        self,
        organization_id: int,
        user_ids: list[str] | set[str],
        event: dict[str, Any],
        *,
        exclude_user_ids: set[str] | None = None,
    ) -> None:
        excluded = exclude_user_ids or set()
        async with self._lock:
            organization_connections = self._connections.get(organization_id, {})
            states = [
                state
                for user_id in set(user_ids)
                if user_id not in excluded
                for state in organization_connections.get(user_id, {}).values()
            ]
        if states:
            await asyncio.gather(
                *(self._enqueue(state, event) for state in states),
                return_exceptions=True,
            )

    async def broadcast_organization(
        self,
        organization_id: int,
        event: dict[str, Any],
        *,
        exclude_user_ids: set[str] | None = None,
    ) -> None:
        async with self._lock:
            user_ids = list(self._connections.get(organization_id, {}).keys())
        await self.broadcast_to_users(
            organization_id, user_ids, event, exclude_user_ids=exclude_user_ids
        )

    async def broadcast_account_email(self, email: str, event: dict[str, Any]) -> None:
        normalized_email = normalize_realtime_email(email)
        if not normalized_email:
            return
        async with self._lock:
            states = list(
                self._account_connections_by_email.get(normalized_email, {}).values()
            )
        if states:
            await asyncio.gather(
                *(self._enqueue(state, event) for state in states),
                return_exceptions=True,
            )

    async def broadcast_account_user(self, user_id: str, event: dict[str, Any]) -> None:
        normalized_user_id = normalize_user_id(user_id)
        async with self._lock:
            states = list(
                self._account_connections_by_user_id.get(normalized_user_id, {}).values()
            )
        if states:
            await asyncio.gather(
                *(self._enqueue(state, event) for state in states),
                return_exceptions=True,
            )


TEAM_REALTIME_MANAGER = RealtimeConnectionManager()


def normalize_realtime_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or "@" not in normalized:
        return None
    return normalized


def normalize_realtime_payload(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: normalize_realtime_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_realtime_payload(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_realtime_payload(item) for item in value]
    return value


def normalize_realtime_event(event: dict[str, Any]) -> dict[str, Any]:
    return normalize_realtime_payload(event_with_contract(event))


def build_realtime_envelope(
    *,
    scope: str,
    event: dict[str, Any],
    organization_id: int | None = None,
    user_ids: list[str] | set[str] | None = None,
    exclude_user_ids: set[str] | None = None,
    recipient_email: str | None = None,
    recipient_user_id: str | None = None,
) -> dict[str, Any]:
    normalized_event = normalize_realtime_event(event)
    if not normalized_event.get("event_id"):
        normalized_event["event_id"] = f"realtime:{uuid4().hex}"
    if organization_id is not None:
        normalized_event["organization_id"] = int(organization_id)

    return {
        "scope": scope,
        "organization_id": int(organization_id) if organization_id else None,
        "user_ids": sorted({str(user_id) for user_id in (user_ids or [])}),
        "exclude_user_ids": sorted(
            {str(user_id) for user_id in (exclude_user_ids or set())}
        ),
        "recipient_email": recipient_email,
        "recipient_user_id": recipient_user_id,
        "event": normalized_event,
    }


async def deliver_realtime_envelope(envelope: dict[str, Any]) -> None:
    scope = str(envelope.get("scope") or "").strip()
    event = envelope.get("event")
    if not isinstance(event, dict):
        raise ValueError("Realtime envelope event must be an object.")

    organization_id_value = envelope.get("organization_id")
    organization_id = (
        int(organization_id_value) if organization_id_value is not None else None
    )
    user_ids = {
        str(user_id)
        for user_id in envelope.get("user_ids") or []
        if str(user_id).strip()
    }
    exclude_user_ids = {
        str(user_id)
        for user_id in envelope.get("exclude_user_ids") or []
        if str(user_id).strip()
    }

    if scope == "organization_users" and organization_id is not None:
        await TEAM_REALTIME_MANAGER.broadcast_to_users(
            organization_id,
            user_ids,
            event,
            exclude_user_ids=exclude_user_ids,
        )
        return

    if scope == "organization" and organization_id is not None:
        await TEAM_REALTIME_MANAGER.broadcast_organization(
            organization_id,
            event,
            exclude_user_ids=exclude_user_ids,
        )
        return

    if scope == "account_email":
        email = normalize_realtime_email(envelope.get("recipient_email"))
        if email:
            await TEAM_REALTIME_MANAGER.broadcast_account_email(email, event)
        return

    if scope == "account_user":
        user_id = str(envelope.get("recipient_user_id") or "").strip()
        if user_id:
            await TEAM_REALTIME_MANAGER.broadcast_account_user(user_id, event)
        return

    if scope == "organization_revoke" and organization_id is not None:
        user_id = str(envelope.get("recipient_user_id") or "").strip()
        if user_id:
            await TEAM_REALTIME_MANAGER.revoke_organization_user(
                organization_id,
                user_id,
                event,
            )
        return

    raise ValueError(f"Unsupported realtime envelope scope: {scope}")


TEAM_REALTIME_BROKER = SharedRealtimeBroker(deliver_realtime_envelope)


async def publish_realtime_envelope(envelope: dict[str, Any]) -> bool:
    """Deliver locally and publish to every other FastAPI process."""

    await deliver_realtime_envelope(envelope)
    shared_published = await TEAM_REALTIME_BROKER.publish(envelope)
    if shared_published:
        return True

    # This opt-out is deliberately explicit and intended only for local,
    # single-process development.
    return not TEAM_REALTIME_BROKER.required


async def publish_users_realtime_event(
    *,
    organization_id: int,
    user_ids: list[str] | set[str],
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> bool:
    return await publish_realtime_envelope(
        build_realtime_envelope(
            scope="organization_users",
            organization_id=organization_id,
            user_ids=user_ids,
            exclude_user_ids=exclude_user_ids,
            event=event,
        )
    )


async def publish_organization_realtime_event(
    *,
    organization_id: int,
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> bool:
    return await publish_realtime_envelope(
        build_realtime_envelope(
            scope="organization",
            organization_id=organization_id,
            exclude_user_ids=exclude_user_ids,
            event=event,
        )
    )


def dispatch_realtime_event(
    *,
    organization_id: int,
    user_ids: list[str] | set[str],
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> bool:
    """Publish an organization-user event from a synchronous route handler."""

    async def _publish() -> bool:
        return await publish_users_realtime_event(
            organization_id=organization_id,
            user_ids=user_ids,
            event=event,
            exclude_user_ids=exclude_user_ids or set(),
        )

    try:
        return bool(anyio.from_thread.run(_publish))
    except RuntimeError:
        return False


def dispatch_organization_realtime_event(
    *,
    organization_id: int,
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> bool:
    async def _publish() -> bool:
        return await publish_organization_realtime_event(
            organization_id=organization_id,
            event=event,
            exclude_user_ids=exclude_user_ids or set(),
        )

    try:
        return bool(anyio.from_thread.run(_publish))
    except RuntimeError:
        return False


def dispatch_account_realtime_event_by_email(
    *,
    email: str,
    event: dict[str, Any],
) -> bool:
    """Send a user-scoped realtime event to a signed-in user by email.

    Used for team invitations because pending invitees are not yet active
    organization members and therefore cannot connect to the org-scoped socket.
    """

    normalized_email = normalize_realtime_email(email)
    if not normalized_email:
        return False

    async def _publish() -> bool:
        return await publish_realtime_envelope(
            build_realtime_envelope(
                scope="account_email",
                recipient_email=normalized_email,
                event={
                    **event,
                    "recipient_email": normalized_email,
                },
            )
        )

    try:
        return bool(anyio.from_thread.run(_publish))
    except RuntimeError:
        return False


class CreateConversationRequest(BaseModel):
    type: ConversationType
    name: str | None = None
    member_user_ids: list[str] = []

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"dm", "group"}:
            raise ValueError("type must be one of: dm, group.")
        return normalized

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("member_user_ids")
    @classmethod
    def normalize_member_user_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized_ids: list[str] = []

        for raw_user_id in value or []:
            user_id = normalize_user_id(raw_user_id)
            if user_id in seen:
                continue
            seen.add(user_id)
            normalized_ids.append(user_id)

        return normalized_ids

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "dm" and len(self.member_user_ids) != 1:
            raise ValueError("Direct messages require exactly one target member.")
        if self.type == "group" and not self.name:
            raise ValueError("The organization group conversation requires a name.")
        return self


class SendMessageRequest(BaseModel):
    body: str
    client_message_id: str | None = None

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("Message body is required.")
        if len(normalized) > 5000:
            raise ValueError("Message body cannot exceed 5000 characters.")
        return normalized

    @field_validator("client_message_id")
    @classmethod
    def validate_client_message_id(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_client_message_id(value)


class ForwardMessageRequest(BaseModel):
    recipient_user_ids: list[str]
    client_message_id: str | None = None

    @field_validator("recipient_user_ids")
    @classmethod
    def normalize_recipient_user_ids(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for raw_user_id in value or []:
            user_id = normalize_user_id(raw_user_id)
            if user_id in seen:
                continue
            seen.add(user_id)
            normalized.append(user_id)
        if not normalized:
            raise ValueError("Choose at least one recipient.")
        if len(normalized) > 50:
            raise ValueError("A message may be forwarded to at most 50 members at once.")
        return normalized

    @field_validator("client_message_id")
    @classmethod
    def validate_client_message_id(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_client_message_id(value)


class UpdatePresenceRequest(BaseModel):
    status: PresenceStatus = "online"

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"online", "offline", "in_call"}:
            raise ValueError("status must be one of: online, offline, in_call.")
        return normalized


def normalize_user_id(value: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError("user_id is required.")
    return normalized


def conversation_creation_lock_key(
    *,
    organization_id: int,
    conversation_type: str,
    member_user_ids: list[str] | tuple[str, ...] = (),
) -> int:
    canonical_members = ",".join(sorted(str(item) for item in member_user_ids))
    material = (
        f"redocx:conversation-create:{int(organization_id)}:"
        f"{str(conversation_type)}:{canonical_members}"
    ).encode("utf-8")
    # PostgreSQL advisory locks accept signed BIGINT. Keep the top bit clear.
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") & ((1 << 63) - 1)


def acquire_conversation_creation_lock(
    conn,
    *,
    organization_id: int,
    conversation_type: str,
    member_user_ids: list[str] | tuple[str, ...] = (),
) -> None:
    lock_key = conversation_creation_lock_key(
        organization_id=organization_id,
        conversation_type=conversation_type,
        member_user_ids=member_user_ids,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s::bigint)", (lock_key,))


def entitlement_value(entitlement: Any, key: str, default: Any = None) -> Any:
    if isinstance(entitlement, dict):
        return entitlement.get(key, default)
    return getattr(entitlement, key, default)


def parse_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_public_payload(current_user: AuthenticatedUser) -> dict[str, Any]:
    return {
        "id": current_user.user_id,
        "name": current_user.claims.get("name"),
        "email": current_user.claims.get("email"),
        "picture": current_user.claims.get("picture"),
    }


def get_participant_display_name(current_user: AuthenticatedUser) -> str:
    for key in ("name", "email", "nickname", "preferred_username"):
        value = current_user.claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return current_user.user_id


def get_livekit_config() -> dict[str, str | int]:
    api_key = os.getenv(LIVEKIT_API_KEY_ENV, "").strip()
    api_secret = os.getenv(LIVEKIT_API_SECRET_ENV, "").strip()
    server_url = os.getenv(LIVEKIT_URL_ENV, "").strip()

    if not api_key or not api_secret or not server_url:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "livekit_not_configured",
                "message": (
                    "LiveKit is not configured. Set LIVEKIT_API_KEY, "
                    "LIVEKIT_API_SECRET, and LIVEKIT_URL."
                ),
            },
        )

    try:
        ttl_minutes = int(
            os.getenv(
                LIVEKIT_TOKEN_TTL_MINUTES_ENV,
                str(DEFAULT_LIVEKIT_TOKEN_TTL_MINUTES),
            )
        )
    except ValueError:
        ttl_minutes = DEFAULT_LIVEKIT_TOKEN_TTL_MINUTES

    # A short-lived token limits the value of a copied token. Explicit leave,
    # membership revocation, and host termination also revoke issued tokens.
    ttl_minutes = max(2, min(ttl_minutes, 30))

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "server_url": server_url,
        "ttl_minutes": ttl_minutes,
    }


def generate_livekit_join_payload(
    *,
    current_user: AuthenticatedUser,
    room_name: str,
    call_session_id: int,
    organization_id: int,
    media_type: str,
) -> dict[str, Any]:
    config = get_livekit_config()

    try:
        from livekit import api as livekit_api
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "livekit_sdk_missing",
                "message": "Install the LiveKit server SDK with: pip install livekit-api",
            },
        ) from exc

    participant_identity = current_user.user_id
    participant_name = get_participant_display_name(current_user)
    ttl = timedelta(minutes=int(config["ttl_minutes"]))
    normalized_media_type = "audio" if media_type == "audio" else "video"
    allowed_publish_sources = (
        ["microphone", "screen_share", "screen_share_audio"]
        if normalized_media_type == "audio"
        else None
    )

    token_builder = (
        livekit_api.AccessToken(
            str(config["api_key"]),
            str(config["api_secret"]),
        )
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_metadata(
            json.dumps(
                {
                    "call_session_id": int(call_session_id),
                    "organization_id": int(organization_id),
                    "media_type": normalized_media_type,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        .with_ttl(ttl)
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_publish_sources=allowed_publish_sources,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
    )
    token = token_builder.to_jwt()

    return {
        "server_url": config["server_url"],
        "room_name": room_name,
        "token": token,
        "token_status": "configured",
        "participant_identity": participant_identity,
        "participant_name": participant_name,
        "expires_in_seconds": int(ttl.total_seconds()),
        "media_type": normalized_media_type,
    }


async def revoke_livekit_participant_access(
    *,
    room_names: list[str] | set[str],
    user_id: str,
    revoked_at_epoch: int,
) -> bool:
    """Disconnect a member from rooms and revoke reusable room tokens."""

    normalized_rooms = sorted(
        {str(room_name).strip() for room_name in room_names if str(room_name).strip()}
    )
    if not normalized_rooms:
        return True

    try:
        config = get_livekit_config()
    except HTTPException:
        logger.exception("LiveKit is unavailable during membership revocation.")
        return False

    try:
        from livekit import api as livekit_api
    except ImportError:
        logger.exception("LiveKit server SDK is unavailable during revocation.")
        return False

    success = True

    try:
        async with livekit_api.LiveKitAPI(
            str(config["server_url"]),
            api_key=str(config["api_key"]),
            api_secret=str(config["api_secret"]),
        ) as livekit_client:
            for room_name in normalized_rooms:
                request = livekit_api.RoomParticipantIdentity(
                    room=room_name,
                    identity=user_id,
                )

                # Newer LiveKit Cloud versions use this timestamp as a
                # not-before cutoff so a previously issued token cannot reconnect.
                if hasattr(request, "revoke_token_ts"):
                    request.revoke_token_ts = int(revoked_at_epoch)

                try:
                    await livekit_client.room.remove_participant(request)
                except Exception as exc:
                    code = str(getattr(exc, "code", "")).lower()
                    message = str(getattr(exc, "message", exc)).lower()
                    if code == "not_found" or "participant does not exist" in message:
                        # LiveKit Cloud still applies the token cutoff when the
                        # participant already left the room.
                        continue
                    success = False
                    logger.exception(
                        "Could not revoke LiveKit participant from room %s.",
                        room_name,
                    )
    except Exception:
        logger.exception("Could not initialize LiveKit revocation client.")
        return False

    return success


async def terminate_livekit_rooms(
    *,
    room_names: list[str] | set[str],
    participant_user_ids: list[str] | set[str],
    revoked_at_epoch: int,
) -> bool:
    """Revoke every issued participant token and close each LiveKit room."""

    normalized_rooms = sorted(
        {str(room_name).strip() for room_name in room_names if str(room_name).strip()}
    )
    normalized_user_ids = sorted(
        {str(user_id).strip() for user_id in participant_user_ids if str(user_id).strip()}
    )
    if not normalized_rooms:
        return True

    success = True
    for user_id in normalized_user_ids:
        revoked = await revoke_livekit_participant_access(
            room_names=normalized_rooms,
            user_id=user_id,
            revoked_at_epoch=revoked_at_epoch,
        )
        success = bool(success and revoked)

    try:
        config = get_livekit_config()
        from livekit import api as livekit_api

        async with livekit_api.LiveKitAPI(
            str(config["server_url"]),
            api_key=str(config["api_key"]),
            api_secret=str(config["api_secret"]),
        ) as livekit_client:
            for room_name in normalized_rooms:
                try:
                    await livekit_client.room.delete_room(
                        livekit_api.DeleteRoomRequest(room=room_name)
                    )
                except Exception as exc:
                    code = str(getattr(exc, "code", "")).lower()
                    message = str(getattr(exc, "message", exc)).lower()
                    if code == "not_found" or "room does not exist" in message:
                        continue
                    success = False
                    logger.exception("Could not close LiveKit room %s.", room_name)
    except Exception:
        logger.exception("Could not initialize LiveKit room termination client.")
        return False

    return success


def row_to_conversation(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "organization_id": row[1],
        "type": row[2],
        "name": row[3],
        "created_by_user_id": row[4],
        "status": row[5],
        "last_message_at": row[6],
        "created_at": row[7],
        "updated_at": row[8],
        "membership_version": row[9] if len(row) > 9 else 1,
    }


def row_to_conversation_member(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "conversation_id": row[1],
        "organization_id": row[2],
        "user_id": row[3],
        "role": row[4],
        "status": row[5],
        "joined_at": row[6],
        "removed_at": row[7],
        "created_at": row[8],
        "updated_at": row[9],
    }


def row_to_message(row) -> dict[str, Any]:
    metadata = row[6] if isinstance(row[6], dict) else {}
    client_message_id = metadata.get("client_message_id")

    return {
        "id": row[0],
        "conversation_id": row[1],
        "organization_id": row[2],
        "sender_user_id": row[3],
        "message_type": row[4],
        "body": row[5],
        "metadata": metadata,
        "client_message_id": client_message_id,
        "edited_at": row[7],
        "deleted_at": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


def build_attachment_download_url(
    *,
    conversation_id: int,
    message_id: int,
    attachment_id: int,
) -> str:
    return (
        f"/api/conversations/{conversation_id}/messages/"
        f"{message_id}/attachments/{attachment_id}/download"
    )


def row_to_attachment(row) -> dict[str, Any]:
    """Return client-safe metadata; never expose storage or encryption fields."""

    security_status = str(row[10] or "legacy_unverified")
    malware_scan_status = str(row[11] or "")
    best_effort_enabled = (
        os.getenv("TEAM_ATTACHMENT_SCAN_POLICY", "strict").strip().lower()
        in {"best_effort", "best-effort", "besteffort"}
    )
    available = security_status == "secured" and (
        malware_scan_status == "clean"
        or (best_effort_enabled and malware_scan_status == "failed")
    )
    attachment = {
        "id": row[0],
        "message_id": row[1],
        "conversation_id": row[2],
        "organization_id": row[3],
        "uploaded_by_user_id": row[4],
        "kind": row[5],
        "original_filename": row[6],
        "content_type": row[7],
        "file_size_bytes": row[8],
        "security_status": security_status,
        "malware_scan_status": malware_scan_status,
        "secured_at": normalize_realtime_payload(row[12]),
        "created_at": normalize_realtime_payload(row[13]),
        "available_for_download": available,
        "download_url": None,
        "security_warning": (
            "Malware scanning was unavailable; structural validation and encryption completed."
            if malware_scan_status == "failed"
            else None
        ),
    }
    if available:
        attachment["download_url"] = build_attachment_download_url(
            conversation_id=int(attachment["conversation_id"]),
            message_id=int(attachment["message_id"]),
            attachment_id=int(attachment["id"]),
        )
    return attachment


def row_to_secured_attachment_record(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "message_id": row[1],
        "conversation_id": row[2],
        "organization_id": row[3],
        "uploaded_by_user_id": row[4],
        "kind": row[5],
        "original_filename": row[6],
        "stored_filename": row[7],
        "storage_key": row[8],
        "content_type": row[9],
        "file_size_bytes": row[10],
        "checksum_sha256": row[11],
        "storage_backend": row[12],
        "security_status": row[13],
        "malware_scan_status": row[14],
        "detected_content_type": row[15],
        "validation_version": row[16],
        "encryption_algorithm": row[17],
        "encryption_key_id": row[18],
        "encryption_nonce": row[19],
        "encryption_aad_version": row[20],
        "encrypted_content": row[21],
        "secured_at": row[22],
        "last_integrity_verified_at": row[23],
        "created_at": row[24],
    }


def attachment_record_to_public_payload(record: dict[str, Any]) -> dict[str, Any]:
    return row_to_attachment(
        (
            record["id"],
            record["message_id"],
            record["conversation_id"],
            record["organization_id"],
            record["uploaded_by_user_id"],
            record["kind"],
            record["original_filename"],
            record["content_type"],
            record["file_size_bytes"],
            record["checksum_sha256"],
            record["security_status"],
            record["malware_scan_status"],
            record["secured_at"],
            record["created_at"],
        )
    )


def attachment_security_http_exception(exc: TeamAttachmentSecurityError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.public_message},
    )


def attachment_request_id(request: Request) -> str:
    value = str(request.headers.get("x-request-id") or "").strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if value and len(value) <= 160 and all(character in allowed for character in value):
        return value
    return f"attachment:{uuid4().hex}"


def insert_attachment_security_event(
    conn,
    *,
    organization_id: int,
    actor_user_id: str,
    action: str,
    outcome: str,
    request_id: str,
    conversation_id: int | None = None,
    message_id: int | None = None,
    attachment_id: int | None = None,
    reason_code: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_attachment_security_events (
                organization_id,
                conversation_id,
                message_id,
                attachment_id,
                actor_user_id,
                action,
                outcome,
                reason_code,
                request_id,
                details
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                organization_id,
                conversation_id,
                message_id,
                attachment_id,
                actor_user_id,
                action,
                outcome,
                reason_code,
                request_id,
                Jsonb(details or {}),
            ),
        )


def record_attachment_security_event_best_effort(**kwargs: Any) -> None:
    try:
        with get_db() as conn:
            insert_attachment_security_event(conn, **kwargs)
    except Exception:
        logger.exception("Could not persist a team attachment security audit event.")


def build_attachment_content_disposition(filename: str) -> str:
    fallback = "".join(
        character if 32 <= ord(character) < 127 and character not in {'"', "\\"} else "_"
        for character in filename
    ).strip() or "attachment"
    fallback = fallback[:180]
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def fetch_message_attachments(conn, message_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, message_id, conversation_id, organization_id,
                   uploaded_by_user_id, kind, original_filename, content_type,
                   file_size_bytes, checksum_sha256, security_status,
                   malware_scan_status, secured_at, created_at
            FROM conversation_message_attachments
            WHERE message_id = %s
            ORDER BY id ASC
            """,
            (message_id,),
        )
        rows = cur.fetchall()

    return [row_to_attachment(row) for row in rows]


def add_attachments_to_message(
    conn,
    message: dict[str, Any],
) -> dict[str, Any]:
    if message.get("message_type") != "attachment":
        return message

    attachments = fetch_message_attachments(conn, int(message["id"]))
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}

    return {
        **message,
        "metadata": {
            **metadata,
            "attachments": attachments,
        },
    }


def add_call_state_to_message(conn, message: dict[str, Any]) -> dict[str, Any]:
    if message.get("message_type") != "call_event":
        return message
    # Imported lazily to avoid a module cycle while the call router is attached
    # to this module's parent router at import time.
    from backend.team_call_lifecycle import add_call_state_to_message as enrich

    return enrich(conn, message)


def add_call_states_to_messages(
    conn,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Batch-enrich call-event messages without one call query per message."""

    call_ids: set[int] = set()
    message_call_ids: dict[int, int] = {}
    for message in messages:
        if message.get("message_type") != "call_event":
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            continue
        call_id = parse_optional_int(
            metadata.get("call_session_id") or metadata.get("callSessionId")
        )
        if call_id:
            call_ids.add(call_id)
            message_call_ids[int(message["id"])] = call_id

    if not call_ids:
        return messages

    from backend.team_call_lifecycle import CALL_COLUMNS, _call_to_public

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {CALL_COLUMNS} FROM call_sessions WHERE id = ANY(%s)",
            (sorted(call_ids),),
        )
        calls = {
            int(row[0]): _call_to_public(row_to_call_session(row))
            for row in cur.fetchall()
        }

    enriched: list[dict[str, Any]] = []
    for message in messages:
        call_id = message_call_ids.get(int(message["id"]))
        call = calls.get(call_id) if call_id is not None else None
        if call is None:
            enriched.append(message)
            continue
        metadata = message.get("metadata")
        enriched.append(
            {
                **message,
                "metadata": {
                    **(metadata if isinstance(metadata, dict) else {}),
                    "call": call,
                },
            }
        )
    return enriched


def row_to_call_session(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "organization_id": row[1],
        "conversation_id": row[2],
        "type": row[3],
        "status": row[4],
        "created_by_user_id": row[5],
        "livekit_room_name": row[6],
        "started_at": row[7],
        "ended_at": row[8],
        "created_at": row[9],
        "updated_at": row[10],
        "media_type": row[11],
        "ended_by_user_id": row[12],
        "end_reason": row[13],
        "ringing_expires_at": row[14],
        "provider_room_sid": row[15],
        "provider_started_at": row[16],
        "provider_finished_at": row[17],
        "last_provider_event_at": row[18],
        "lifecycle_version": row[19],
    }


def row_to_call_participant(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "call_session_id": row[1],
        "organization_id": row[2],
        "user_id": row[3],
        "status": row[4],
        "invited_at": row[5],
        "joined_at": row[6],
        "left_at": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "accepted_at": row[10],
        "token_issued_at": row[11],
        "provider_participant_sid": row[12],
        "provider_joined_at": row[13],
        "provider_left_at": row[14],
        "last_provider_event_at": row[15],
    }


def row_to_presence(row) -> dict[str, Any]:
    return {
        "organization_id": row[0],
        "user_id": row[1],
        "status": row[2],
        "last_seen_at": row[3],
        "updated_at": row[4],
    }


def get_active_organization_membership(
    conn,
    organization_id: int,
    user_id: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, user_id, role, status
            FROM organization_members
            WHERE organization_id = %s
              AND user_id = %s
              AND status = 'active'
            FOR SHARE
            """,
            (organization_id, user_id),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "role": row[3],
        "status": row[4],
    }


def require_business_or_enterprise_organization(
    conn,
    organization_id: int,
    current_user: AuthenticatedUser,
) -> dict[str, Any]:
    """
    Require the current user to be an active member of the requested
    Business/Enterprise organization.

    Important: this uses the caller's existing DB connection instead of calling
    get_user_entitlement(), which opens a second DB connection. The team
    messages page polls these routes, so avoiding nested connections prevents
    connection-pool exhaustion under normal usage.
    """

    membership = get_active_organization_membership(
        conn,
        organization_id,
        current_user.user_id,
    )

    if membership is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_access_denied",
                "message": "You are not an active member of this organization.",
            },
        )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plan, status
            FROM organization_subscriptions
            WHERE organization_id = %s
              AND status = 'active'
              AND plan IN ('business', 'enterprise')
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (organization_id,),
        )
        subscription_row = cur.fetchone()

    if subscription_row is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "team_communications_unavailable",
                "message": "Team messaging and calls are available only for active Business or Enterprise organizations.",
            },
        )

    entitlement_plan = subscription_row[0]
    entitlement_status = subscription_row[1]

    return {
        "membership": membership,
        "entitlement": {
            "plan": entitlement_plan,
            "status": entitlement_status,
            "source": "organization",
            "organization_id": organization_id,
            "is_paid": True,
        },
    }


def require_org_admin_or_owner(membership: dict[str, Any]) -> None:
    if membership["role"] not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_admin_required",
                "message": "Only organization owners or admins can perform this action.",
            },
        )


def require_org_owner(membership: dict[str, Any]) -> None:
    if membership["role"] != "owner":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_owner_required",
                "message": "Only the organization owner can perform this action.",
            },
        )


def require_active_org_members(conn, organization_id: int, user_ids: list[str]) -> None:
    if not user_ids:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM organization_members
            WHERE organization_id = %s
              AND user_id = ANY(%s)
              AND status = 'active'
            """,
            (organization_id, user_ids),
        )
        active_rows = cur.fetchall()

    active_user_ids = {row[0] for row in active_rows}
    missing_user_ids = [user_id for user_id in user_ids if user_id not in active_user_ids]

    if missing_user_ids:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_conversation_members",
                "message": "All conversation members must be active members of the organization.",
                "invalid_user_ids": missing_user_ids,
            },
        )


def get_conversation(conn, conversation_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, type, name, created_by_user_id, status,
                   last_message_at, created_at, updated_at
            FROM organization_conversations
            WHERE id = %s
            """,
            (conversation_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "conversation_not_found",
                "message": "Conversation was not found.",
            },
        )

    return row_to_conversation(row)


def require_active_conversation_member(
    conn,
    conversation_id: int,
    user_id: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, conversation_id, organization_id, user_id, role, status,
                   joined_at, removed_at, created_at, updated_at
            FROM conversation_members
            WHERE conversation_id = %s
              AND user_id = %s
              AND status = 'active'
            """,
            (conversation_id, user_id),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "conversation_access_denied",
                "message": "You are not an active member of this conversation.",
            },
        )

    return row_to_conversation_member(row)


def fetch_conversation_members(conn, conversation_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, conversation_id, organization_id, user_id, role, status,
                   joined_at, removed_at, created_at, updated_at
            FROM conversation_members
            WHERE conversation_id = %s
            ORDER BY
                CASE role
                    WHEN 'owner' THEN 1
                    WHEN 'admin' THEN 2
                    ELSE 3
                END,
                created_at ASC,
                id ASC
            """,
            (conversation_id,),
        )
        rows = cur.fetchall()

    return [row_to_conversation_member(row) for row in rows]


def add_members_to_conversation_payload(
    conn,
    conversation: dict[str, Any],
) -> dict[str, Any]:
    members = fetch_conversation_members(conn, conversation["id"])
    active_member_ids = [
        member["user_id"]
        for member in members
        if member["status"] == "active"
    ]

    return {
        **conversation,
        "members": members,
        "member_user_ids": active_member_ids,
    }


def compact_conversation_payload(conversation: dict[str, Any]) -> dict[str, Any]:
    """Compact event contract; membership is loaded separately and versioned."""

    return {
        key: normalize_realtime_payload(conversation.get(key))
        for key in (
            "id",
            "organization_id",
            "type",
            "name",
            "status",
            "last_message_at",
            "updated_at",
            "membership_version",
        )
        if key in conversation
    }


def get_active_conversation_member_ids(
    conn,
    conversation_id: int,
) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM conversation_members
            WHERE conversation_id = %s
              AND status = 'active'
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def fetch_message_attachments_batch(
    conn,
    message_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    if not message_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, message_id, conversation_id, organization_id,
                   uploaded_by_user_id, kind, original_filename, content_type,
                   file_size_bytes, checksum_sha256, security_status,
                   malware_scan_status, secured_at, created_at
            FROM conversation_message_attachments
            WHERE message_id = ANY(%s)
            ORDER BY message_id ASC, id ASC
            """,
            (message_ids,),
        )
        rows = cur.fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row[1]), []).append(row_to_attachment(row))
    return grouped


def add_attachments_to_messages(
    conn,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attachment_message_ids = [
        int(message["id"])
        for message in messages
        if message.get("message_type") == "attachment"
    ]
    grouped = fetch_message_attachments_batch(conn, attachment_message_ids)
    enriched: list[dict[str, Any]] = []
    for message in messages:
        if message.get("message_type") != "attachment":
            enriched.append(message)
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        enriched.append(
            {
                **message,
                "metadata": {
                    **metadata,
                    "attachments": grouped.get(int(message["id"]), []),
                },
            }
        )
    return enriched


def get_active_organization_member_ids(conn, organization_id: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM organization_members
            WHERE organization_id = %s
              AND status = 'active'
            ORDER BY
                CASE role
                    WHEN 'owner' THEN 1
                    WHEN 'admin' THEN 2
                    ELSE 3
                END,
                joined_at ASC NULLS LAST,
                created_at ASC,
                id ASC
            """,
            (organization_id,),
        )
        rows = cur.fetchall()

    return [row[0] for row in rows]


def get_existing_group_conversation(
    conn,
    organization_id: int,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, type, name,
                   created_by_user_id, status, last_message_at,
                   created_at, updated_at
            FROM organization_conversations
            WHERE organization_id = %s
              AND type = 'group'
              AND status = 'active'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    return row_to_conversation(row) if row is not None else None


def sync_group_conversation_members(
    conn,
    organization_id: int,
    conversation_id: int,
) -> None:
    active_member_ids = get_active_organization_member_ids(conn, organization_id)

    if not active_member_ids:
        return

    with conn.cursor() as cur:
        for member_user_id in active_member_ids:
            cur.execute(
                """
                INSERT INTO conversation_members (
                    conversation_id,
                    organization_id,
                    user_id,
                    role,
                    status,
                    joined_at
                )
                VALUES (%s, %s, %s, 'member', 'active', NOW())
                ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                    status = 'active',
                    removed_at = NULL,
                    updated_at = NOW()
                """,
                (conversation_id, organization_id, member_user_id),
            )


def sync_organization_group_conversations(conn, organization_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM organization_conversations
            WHERE organization_id = %s
              AND type = 'group'
              AND status = 'active'
            """,
            (organization_id,),
        )
        group_rows = cur.fetchall()

    for row in group_rows:
        sync_group_conversation_members(conn, organization_id, int(row[0]))


def get_existing_dm_conversation(
    conn,
    organization_id: int,
    user_a: str,
    user_b: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT oc.id, oc.organization_id, oc.type, oc.name,
                   oc.created_by_user_id, oc.status, oc.last_message_at,
                   oc.created_at, oc.updated_at
            FROM organization_conversations oc
            JOIN conversation_members cm_a
              ON cm_a.conversation_id = oc.id
             AND cm_a.user_id = %s
             AND cm_a.status = 'active'
            JOIN conversation_members cm_b
              ON cm_b.conversation_id = oc.id
             AND cm_b.user_id = %s
             AND cm_b.status = 'active'
            WHERE oc.organization_id = %s
              AND oc.type = 'dm'
              AND oc.status = 'active'
              AND (
                  SELECT COUNT(*)
                  FROM conversation_members cm_count
                  WHERE cm_count.conversation_id = oc.id
                    AND cm_count.status = 'active'
              ) = 2
            LIMIT 1
            """,
            (user_a, user_b, organization_id),
        )
        row = cur.fetchone()

    return row_to_conversation(row) if row is not None else None


def get_or_create_dm_conversation(
    conn,
    *,
    organization_id: int,
    sender_user_id: str,
    target_user_id: str,
) -> tuple[dict[str, Any], bool]:
    acquire_conversation_creation_lock(
        conn,
        organization_id=organization_id,
        conversation_type="dm",
        member_user_ids=[sender_user_id, target_user_id],
    )
    existing = get_existing_dm_conversation(
        conn,
        organization_id,
        sender_user_id,
        target_user_id,
    )
    if existing is not None:
        return existing, False

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organization_conversations (
                organization_id,
                type,
                name,
                created_by_user_id
            )
            VALUES (%s, 'dm', NULL, %s)
            RETURNING id, organization_id, type, name,
                      created_by_user_id, status, last_message_at,
                      created_at, updated_at
            """,
            (organization_id, sender_user_id),
        )
        conversation = row_to_conversation(cur.fetchone())
        for member_user_id in (sender_user_id, target_user_id):
            cur.execute(
                """
                INSERT INTO conversation_members (
                    conversation_id, organization_id, user_id, role, status, joined_at
                )
                VALUES (%s, %s, %s, %s, 'active', NOW())
                ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                    role = EXCLUDED.role,
                    status = 'active',
                    removed_at = NULL,
                    updated_at = NOW()
                """,
                (
                    conversation["id"],
                    organization_id,
                    member_user_id,
                    "owner" if member_user_id == sender_user_id else "member",
                ),
            )
    return conversation, True


def get_call_session(conn, call_session_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, conversation_id, type, status,
                   created_by_user_id, livekit_room_name, started_at, ended_at,
                   created_at, updated_at, media_type, ended_by_user_id,
                   end_reason, ringing_expires_at, provider_room_sid,
                   provider_started_at, provider_finished_at,
                   last_provider_event_at, lifecycle_version
            FROM call_sessions
            WHERE id = %s
            """,
            (call_session_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "call_not_found",
                "message": "Call session was not found.",
            },
        )

    return row_to_call_session(row)


def upsert_presence(
    conn,
    organization_id: int,
    user_id: str,
    status: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO member_presence (
                organization_id,
                user_id,
                status,
                last_seen_at,
                updated_at
            )
            VALUES (%s, %s, %s, NOW(), NOW())
            ON CONFLICT (organization_id, user_id) DO UPDATE SET
                status = EXCLUDED.status,
                last_seen_at = NOW(),
                updated_at = NOW()
            RETURNING organization_id, user_id, status, last_seen_at, updated_at
            """,
            (organization_id, user_id, status),
        )
        row = cur.fetchone()

    return row_to_presence(row)


def touch_presence(
    conn,
    organization_id: int,
    user_id: str,
) -> None:
    """Persist last-seen only; online/offline authority lives in Redis leases."""

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO member_presence (
                organization_id, user_id, status, last_seen_at, updated_at
            )
            VALUES (%s, %s, 'offline', NOW(), NOW())
            ON CONFLICT (organization_id, user_id) DO UPDATE SET
                last_seen_at = NOW(),
                updated_at = NOW()
            """,
            (organization_id, user_id),
        )


def effective_presence_sync(
    *,
    organization_id: int,
    user_id: str,
    online: bool,
) -> dict[str, Any]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1
                        FROM call_participants cp
                        JOIN call_sessions cs ON cs.id = cp.call_session_id
                        WHERE cp.organization_id = %s
                          AND cp.user_id = %s
                          AND cp.provider_joined_at IS NOT NULL
                          AND (
                              cp.provider_left_at IS NULL
                              OR cp.provider_left_at < cp.provider_joined_at
                          )
                          AND (
                              cs.provider_finished_at IS NULL
                              OR cs.provider_finished_at < cp.provider_joined_at
                          )
                    ),
                    mp.last_seen_at,
                    mp.updated_at
                FROM (SELECT 1) seed
                LEFT JOIN member_presence mp
                  ON mp.organization_id = %s
                 AND mp.user_id = %s
                """,
                (organization_id, user_id, organization_id, user_id),
            )
            row = cur.fetchone()
    in_call = bool(row and row[0])
    return {
        "organization_id": organization_id,
        "user_id": user_id,
        "status": "in_call" if in_call else ("online" if online else "offline"),
        "last_seen_at": row[1] if row else None,
        "updated_at": row[2] if row else None,
        "connection_count": 1 if online else 0,
        "source": "livekit" if in_call else "redis",
    }



def authenticate_websocket_user(token: str | None) -> AuthenticatedUser:
    normalized_token = (token or "").strip()
    if not normalized_token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "authorization_required",
                "message": "A realtime access token is required.",
            },
        )

    return authenticate_access_token(normalized_token)


def websocket_auth_failure_payload(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    return {
        "type": "auth_failed",
        "error": detail.get("error") or "authorization_failed",
        "message": detail.get("message") or "Realtime authentication failed.",
    }


async def receive_websocket_auth_token(websocket: WebSocket) -> str:
    """Read the first-message token; URL/query tokens are intentionally rejected."""

    try:
        message = await receive_json_frame(websocket, timeout=10.0)
    except (asyncio.TimeoutError, ValueError):
        return ""
    try:
        validate_client_event_contract(message)
    except ValueError:
        return ""
    if str(message.get("type") or "").strip().lower() not in {
        "auth",
        "authenticate",
    }:
        return ""
    return str(message.get("token") or "").strip()


def realtime_error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return (
                str(detail.get("message") or detail.get("error") or "Request failed")
            )
        if isinstance(detail, str):
            return detail

    if isinstance(exc, ValueError):
        return str(exc)

    return "Realtime message failed."


def realtime_error_code(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and detail.get("error"):
            return str(detail["error"])

    if isinstance(exc, ValueError):
        return "invalid_realtime_message"

    return "realtime_message_failed"


def parse_realtime_positive_int(value: Any, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer.") from exc

    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer.")

    return parsed


def normalize_client_message_id(value: Any) -> str:
    raw = str(value or "").strip()

    if not raw:
        return f"client:{uuid4().hex}"

    if len(raw) > 160:
        raise ValueError("client_message_id cannot exceed 160 characters.")

    return raw


def persist_text_message_sync(
    *,
    organization_id: int,
    conversation_id: int,
    current_user: AuthenticatedUser,
    body: str,
    client_message_id: str | None,
    transport: Literal["websocket", "http"],
) -> dict[str, Any]:
    payload = SendMessageRequest(
        body=body,
        client_message_id=client_message_id,
    )
    resolved_client_message_id = normalize_client_message_id(
        payload.client_message_id
    )

    with get_db() as conn:
        require_business_or_enterprise_organization(
            conn,
            organization_id,
            current_user,
        )

        conversation = get_conversation(conn, conversation_id)

        if int(conversation["organization_id"]) != int(organization_id):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "conversation_access_denied",
                    "message": "This conversation does not belong to this organization.",
                },
            )

        require_active_conversation_member(
            conn,
            conversation_id,
            current_user.user_id,
        )

        message_metadata = {
            "client_message_id": resolved_client_message_id,
            "transport": transport,
        }

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_messages (
                    conversation_id,
                    organization_id,
                    sender_user_id,
                    message_type,
                    body,
                    metadata,
                    client_message_id
                )
                VALUES (%s, %s, %s, 'text', %s, %s, %s)
                ON CONFLICT (
                    conversation_id,
                    sender_user_id,
                    client_message_id
                ) WHERE client_message_id IS NOT NULL
                DO NOTHING
                RETURNING id, conversation_id, organization_id,
                          sender_user_id, message_type, body, metadata,
                          edited_at, deleted_at, created_at, updated_at
                """,
                (
                    conversation_id,
                    organization_id,
                    current_user.user_id,
                    payload.body,
                    Jsonb(message_metadata),
                    resolved_client_message_id,
                ),
            )
            row = cur.fetchone()
            created = row is not None

            if row is None:
                cur.execute(
                    """
                    SELECT id, conversation_id, organization_id,
                           sender_user_id, message_type, body, metadata,
                           edited_at, deleted_at, created_at, updated_at
                    FROM conversation_messages
                    WHERE conversation_id = %s
                      AND sender_user_id = %s
                      AND client_message_id = %s
                    """,
                    (
                        conversation_id,
                        current_user.user_id,
                        resolved_client_message_id,
                    ),
                )
                row = cur.fetchone()

            if row is None:
                raise RuntimeError("Idempotent message lookup failed after insert.")

            message = row_to_message(row)

            if not created and (
                message["message_type"] != "text"
                or message["body"] != payload.body
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "client_message_id_conflict",
                        "message": (
                            "client_message_id was already used for a different "
                            "message. Generate a new identifier and retry."
                        ),
                    },
                )

            if created:
                cur.execute(
                    """
                    UPDATE organization_conversations
                    SET last_message_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (message["created_at"], conversation_id),
                )

        conversation = get_conversation(conn, conversation_id)
        conversation_payload = compact_conversation_payload(conversation)
        member_ids = get_active_conversation_member_ids(conn, conversation_id)
        committed_message = {
            **message,
            "client_message_id": resolved_client_message_id,
            "pending": False,
        }
        committed_event = {
            "event_id": f"message.created:{message['id']}",
            "type": "message.created",
            "organization_id": organization_id,
            "client_message_id": resolved_client_message_id,
            "message": committed_message,
            "conversation": conversation_payload,
            "sender": user_public_payload(current_user),
            "delivery": "committed",
        }

        if created:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO team_realtime_outbox (
                        organization_id,
                        aggregate_type,
                        aggregate_id,
                        event_type,
                        event_key,
                        recipient_user_ids,
                        payload
                    )
                    VALUES (%s, 'message', %s, 'message.created', %s, %s, %s)
                    ON CONFLICT (event_key) DO NOTHING
                    """,
                    (
                        organization_id,
                        str(message["id"]),
                        committed_event["event_id"],
                        member_ids,
                        Jsonb(normalize_realtime_event(committed_event)),
                    ),
                )

    return {
        "created": created,
        "client_message_id": resolved_client_message_id,
        "message": committed_message,
        "conversation": conversation_payload,
        "member_ids": member_ids,
        "event": committed_event,
    }


def revoke_organization_member_communications(
    conn,
    *,
    organization_id: int,
    user_id: str,
    actor_user_id: str,
    reason: str,
) -> dict[str, Any]:
    """Revoke conversation, presence, call, realtime, and media access atomically."""

    normalized_user_id = normalize_user_id(user_id)
    normalized_actor_user_id = normalize_user_id(actor_user_id)
    normalized_reason = (
        str(reason or "membership_removed").strip()[:120]
        or "membership_removed"
    )
    revoked_at = datetime.now(timezone.utc)
    revoked_at_epoch = int(revoked_at.timestamp())

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT cs.livekit_room_name
            FROM call_sessions cs
            JOIN call_participants cp
              ON cp.call_session_id = cs.id
             AND cp.organization_id = cs.organization_id
            WHERE cs.organization_id = %s
              AND cp.user_id = %s
              AND cs.status IN ('ringing', 'active')
              AND NULLIF(BTRIM(cs.livekit_room_name), '') IS NOT NULL
            ORDER BY cs.livekit_room_name
            """,
            (organization_id, normalized_user_id),
        )
        media_room_names = [str(row[0]) for row in cur.fetchall()]

        cur.execute(
            """
            UPDATE conversation_members
            SET status = 'removed',
                removed_at = COALESCE(removed_at, NOW()),
                updated_at = NOW()
            WHERE organization_id = %s
              AND user_id = %s
              AND status = 'active'
            """,
            (organization_id, normalized_user_id),
        )
        revoked_conversation_count = int(cur.rowcount or 0)

        cur.execute(
            """
            UPDATE call_participants
            SET status = 'removed',
                left_at = COALESCE(left_at, NOW()),
                revoked_at = COALESCE(revoked_at, NOW()),
                revoked_by_user_id = %s,
                revocation_reason = %s,
                updated_at = NOW()
            WHERE organization_id = %s
              AND user_id = %s
              AND status IN ('invited', 'connecting', 'joined')
            """,
            (
                normalized_actor_user_id,
                normalized_reason,
                organization_id,
                normalized_user_id,
            ),
        )
        revoked_call_count = int(cur.rowcount or 0)

        cur.execute(
            """
            INSERT INTO member_presence (
                organization_id,
                user_id,
                status,
                last_seen_at,
                updated_at
            )
            VALUES (%s, %s, 'offline', NOW(), NOW())
            ON CONFLICT (organization_id, user_id) DO UPDATE SET
                status = 'offline',
                last_seen_at = NOW(),
                updated_at = NOW()
            """,
            (organization_id, normalized_user_id),
        )

        event_key = (
            f"organization.access_revoked:{organization_id}:"
            f"{normalized_user_id}:{uuid4().hex}"
        )
        event = {
            "event_id": event_key,
            "type": "organization.access.revoked",
            "organization_id": organization_id,
            "user_id": normalized_user_id,
            "actor_user_id": normalized_actor_user_id,
            "reason": normalized_reason,
            "revoked_at": revoked_at.isoformat(),
            "revoked_at_epoch": revoked_at_epoch,
        }

        cur.execute(
            """
            INSERT INTO team_realtime_outbox (
                organization_id,
                aggregate_type,
                aggregate_id,
                event_type,
                event_key,
                delivery_scope,
                recipient_user_ids,
                exclude_user_ids,
                media_room_names,
                payload
            )
            VALUES (
                %s,
                'organization_member',
                %s,
                'organization.access.revoked',
                %s,
                'organization_revoke',
                %s,
                ARRAY[]::TEXT[],
                %s,
                %s
            )
            """,
            (
                organization_id,
                f"{organization_id}:{normalized_user_id}",
                event_key,
                [normalized_user_id],
                media_room_names,
                Jsonb(event),
            ),
        )

    return {
        "event_key": event_key,
        "event": event,
        "organization_id": organization_id,
        "user_id": normalized_user_id,
        "media_room_names": media_room_names,
        "revoked_at_epoch": revoked_at_epoch,
        "revoked_conversation_count": revoked_conversation_count,
        "revoked_call_count": revoked_call_count,
    }


def mark_realtime_outbox_published_sync(event_key: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE team_realtime_outbox
                SET status = 'published',
                    published_at = COALESCE(published_at, NOW()),
                    locked_at = NULL,
                    locked_by = NULL,
                    last_error = NULL
                WHERE event_key = %s
                  AND status = 'pending'
                """,
                (event_key,),
            )


def _positive_int_env(name: str, default: int, *, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _positive_float_env(name: str, default: float, *, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(0.1, min(value, maximum))


def validate_team_realtime_schema_sync() -> None:
    """Fail startup when the required realtime/revocation migration is absent."""

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT delivery_scope, exclude_user_ids, media_room_names
                FROM team_realtime_outbox
                LIMIT 0
                """
            )
            cur.execute(
                """
                SELECT revoked_at, revoked_by_user_id, revocation_reason
                FROM call_participants
                LIMIT 0
                """
            )
            cur.execute(
                """
                SELECT ringing_expires_at, provider_room_sid,
                       last_provider_event_at, lifecycle_version
                FROM call_sessions
                LIMIT 0
                """
            )
            cur.execute(
                """
                SELECT accepted_at, token_issued_at, provider_participant_sid,
                       provider_joined_at, provider_left_at,
                       last_provider_event_at
                FROM call_participants
                LIMIT 0
                """
            )
            cur.execute("SELECT event_id FROM livekit_webhook_events LIMIT 0")


def claim_pending_realtime_outbox_sync(
    *,
    worker_id: str,
    batch_size: int,
    lease_seconds: int,
) -> list[dict[str, Any]]:
    """Lease due outbox rows without blocking other API processes."""

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due_events AS (
                    SELECT candidate.id
                    FROM team_realtime_outbox AS candidate
                    WHERE candidate.status = 'pending'
                      AND candidate.available_at <= NOW()
                      AND (
                          candidate.locked_at IS NULL
                          OR candidate.locked_at <
                             NOW() - (%s * INTERVAL '1 second')
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM team_realtime_outbox AS earlier
                          WHERE earlier.status = 'pending'
                            AND earlier.aggregate_type = candidate.aggregate_type
                            AND earlier.aggregate_id = candidate.aggregate_id
                            AND earlier.id < candidate.id
                      )
                    ORDER BY candidate.available_at ASC, candidate.id ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE team_realtime_outbox AS outbox
                SET locked_at = NOW(),
                    locked_by = %s,
                    attempts = outbox.attempts + 1
                FROM due_events
                WHERE outbox.id = due_events.id
                RETURNING outbox.id,
                          outbox.organization_id,
                          outbox.event_key,
                          outbox.delivery_scope,
                          outbox.recipient_user_ids,
                          outbox.exclude_user_ids,
                          outbox.media_room_names,
                          outbox.payload,
                          outbox.attempts
                """,
                (lease_seconds, batch_size, worker_id),
            )
            rows = cur.fetchall()

    return [
        {
            "id": int(row[0]),
            "organization_id": int(row[1]),
            "event_key": str(row[2]),
            "delivery_scope": str(row[3]),
            "recipient_user_ids": list(row[4] or []),
            "exclude_user_ids": list(row[5] or []),
            "media_room_names": list(row[6] or []),
            "payload": dict(row[7] or {}),
            "attempts": int(row[8]),
        }
        for row in rows
    ]


def mark_realtime_outbox_failed_sync(
    *,
    outbox_id: int,
    attempts: int,
    delivery_scope: str,
    error: str,
) -> None:
    max_attempts = _positive_int_env(
        TEAM_REALTIME_OUTBOX_MAX_ATTEMPTS_ENV,
        DEFAULT_TEAM_REALTIME_OUTBOX_MAX_ATTEMPTS,
        maximum=1_000,
    )
    is_critical_revocation = delivery_scope in {
        "organization_revoke",
        "call_media_revoke",
        "call_room_terminate",
    }
    should_dead_letter = attempts >= max_attempts and not is_critical_revocation
    retry_seconds = min(2 ** min(max(attempts - 1, 0), 8), 300)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE team_realtime_outbox
                SET status = %s,
                    available_at = CASE
                        WHEN %s THEN available_at
                        ELSE NOW() + (%s * INTERVAL '1 second')
                    END,
                    locked_at = NULL,
                    locked_by = NULL,
                    last_error = %s
                WHERE id = %s
                  AND status = 'pending'
                """,
                (
                    "dead_letter" if should_dead_letter else "pending",
                    should_dead_letter,
                    retry_seconds,
                    str(error or "Realtime delivery failed.")[:2_000],
                    outbox_id,
                ),
            )


def realtime_outbox_row_to_envelope(row: dict[str, Any]) -> dict[str, Any]:
    scope = str(row.get("delivery_scope") or "organization_users")
    recipient_user_ids = {
        str(user_id)
        for user_id in row.get("recipient_user_ids") or []
        if str(user_id).strip()
    }
    recipient_user_id = (
        sorted(recipient_user_ids)[0]
        if scope == "organization_revoke" and recipient_user_ids
        else None
    )

    return build_realtime_envelope(
        scope=scope,
        organization_id=int(row["organization_id"]),
        user_ids=recipient_user_ids,
        exclude_user_ids={
            str(user_id)
            for user_id in row.get("exclude_user_ids") or []
            if str(user_id).strip()
        },
        recipient_user_id=recipient_user_id,
        event=dict(row.get("payload") or {}),
    )


def call_media_revocation_is_current_sync(
    *,
    call_session_id: int,
    user_id: str,
    revoked_at_epoch: int,
) -> bool:
    """Do not let a delayed leave job disconnect a later authorized rejoin."""

    if call_session_id <= 0 or not user_id or revoked_at_epoch <= 0:
        return True

    revoked_at = datetime.fromtimestamp(revoked_at_epoch, tz=timezone.utc)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, token_issued_at
                FROM call_participants
                WHERE call_session_id = %s
                  AND user_id = %s
                """,
                (call_session_id, user_id),
            )
            row = cur.fetchone()

    if row is None:
        return True
    participant_status = str(row[0] or "")
    token_issued_at = row[1]
    return not (
        participant_status in {"connecting", "joined"}
        and token_issued_at is not None
        and token_issued_at > revoked_at
    )


async def deliver_claimed_realtime_outbox_row(row: dict[str, Any]) -> bool:
    """Deliver one leased event; revocations include media-token invalidation."""

    delivery_scope = str(row.get("delivery_scope") or "")
    if delivery_scope == "call_media_revoke":
        event = row.get("payload") or {}
        success = True
        for user_id in row.get("recipient_user_ids") or []:
            should_revoke = await anyio.to_thread.run_sync(
                lambda resolved_user_id=str(user_id): call_media_revocation_is_current_sync(
                    call_session_id=int(event.get("call_session_id") or 0),
                    user_id=resolved_user_id,
                    revoked_at_epoch=int(event.get("revoked_at_epoch") or 0),
                )
            )
            if not should_revoke:
                continue
            revoked = await revoke_livekit_participant_access(
                room_names=row.get("media_room_names") or [],
                user_id=str(user_id),
                revoked_at_epoch=int(event.get("revoked_at_epoch") or 0),
            )
            success = bool(success and revoked)
        return success

    if delivery_scope == "call_room_terminate":
        event = row.get("payload") or {}
        return await terminate_livekit_rooms(
            room_names=row.get("media_room_names") or [],
            participant_user_ids=row.get("recipient_user_ids") or [],
            revoked_at_epoch=int(event.get("revoked_at_epoch") or 0),
        )

    envelope = realtime_outbox_row_to_envelope(row)
    realtime_delivered = await publish_realtime_envelope(envelope)
    media_delivered = True

    if delivery_scope == "organization_revoke":
        event = row.get("payload") or {}
        media_delivered = await revoke_livekit_participant_access(
            room_names=row.get("media_room_names") or [],
            user_id=str(event.get("user_id") or ""),
            revoked_at_epoch=int(event.get("revoked_at_epoch") or 0),
        )

    return bool(media_delivered and realtime_delivered)


async def process_realtime_outbox_row(row: dict[str, Any]) -> None:
    try:
        delivered = await asyncio.wait_for(
            deliver_claimed_realtime_outbox_row(row),
            timeout=30,
        )
        if not delivered:
            raise RuntimeError("Realtime broker or media revocation is unavailable.")

        await anyio.to_thread.run_sync(
            lambda: mark_realtime_outbox_published_sync(str(row["event_key"]))
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            "Could not deliver team realtime outbox event %s.",
            row.get("event_key"),
        )
        try:
            await anyio.to_thread.run_sync(
                lambda: mark_realtime_outbox_failed_sync(
                    outbox_id=int(row["id"]),
                    attempts=int(row.get("attempts") or 1),
                    delivery_scope=str(row.get("delivery_scope") or ""),
                    error=str(exc),
                )
            )
        except Exception:
            logger.exception(
                "Could not release team realtime outbox event %s.",
                row.get("event_key"),
            )


_TEAM_REALTIME_OUTBOX_TASK: asyncio.Task[None] | None = None
_TEAM_REALTIME_OUTBOX_STOP = asyncio.Event()


async def team_realtime_outbox_worker() -> None:
    worker_id = f"{TEAM_REALTIME_BROKER.instance_id}:{uuid4().hex}"
    poll_seconds = _positive_float_env(
        TEAM_REALTIME_OUTBOX_POLL_SECONDS_ENV,
        DEFAULT_TEAM_REALTIME_OUTBOX_POLL_SECONDS,
        maximum=60,
    )
    batch_size = _positive_int_env(
        TEAM_REALTIME_OUTBOX_BATCH_SIZE_ENV,
        DEFAULT_TEAM_REALTIME_OUTBOX_BATCH_SIZE,
        maximum=100,
    )
    lease_seconds = _positive_int_env(
        TEAM_REALTIME_OUTBOX_LEASE_SECONDS_ENV,
        DEFAULT_TEAM_REALTIME_OUTBOX_LEASE_SECONDS,
        maximum=3_600,
    )
    concurrency = _positive_int_env(
        TEAM_REALTIME_OUTBOX_CONCURRENCY_ENV,
        DEFAULT_TEAM_REALTIME_OUTBOX_CONCURRENCY,
        maximum=25,
    )

    while not _TEAM_REALTIME_OUTBOX_STOP.is_set():
        try:
            rows = await anyio.to_thread.run_sync(
                lambda: claim_pending_realtime_outbox_sync(
                    worker_id=worker_id,
                    batch_size=batch_size,
                    lease_seconds=max(60, lease_seconds),
                )
            )
            if rows:
                for offset in range(0, len(rows), concurrency):
                    await asyncio.gather(
                        *(
                            process_realtime_outbox_row(row)
                            for row in rows[offset : offset + concurrency]
                        )
                    )
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Team realtime outbox polling failed.")

        try:
            await asyncio.wait_for(
                _TEAM_REALTIME_OUTBOX_STOP.wait(),
                timeout=poll_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def start_team_realtime_services() -> None:
    global _TEAM_REALTIME_OUTBOX_TASK

    if _TEAM_REALTIME_OUTBOX_TASK is not None:
        return

    await anyio.to_thread.run_sync(validate_team_realtime_schema_sync)
    await TEAM_REALTIME_BROKER.start()

    from backend.team_call_lifecycle import start_call_lifecycle_services

    await start_call_lifecycle_services()

    _TEAM_REALTIME_OUTBOX_STOP.clear()
    _TEAM_REALTIME_OUTBOX_TASK = asyncio.create_task(
        team_realtime_outbox_worker(),
        name="team-realtime-outbox-worker",
    )


async def stop_team_realtime_services() -> None:
    global _TEAM_REALTIME_OUTBOX_TASK

    _TEAM_REALTIME_OUTBOX_STOP.set()

    from backend.team_call_lifecycle import stop_call_lifecycle_services

    await stop_call_lifecycle_services()
    task = _TEAM_REALTIME_OUTBOX_TASK
    _TEAM_REALTIME_OUTBOX_TASK = None

    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await TEAM_REALTIME_BROKER.stop()


def dispatch_organization_member_revocation(
    revocation: dict[str, Any],
) -> bool:
    """Try the committed revocation immediately; the outbox retries failures."""

    async def _dispatch() -> bool:
        row = {
            "organization_id": revocation["organization_id"],
            "event_key": revocation["event_key"],
            "delivery_scope": "organization_revoke",
            "recipient_user_ids": [revocation["user_id"]],
            "exclude_user_ids": [],
            "media_room_names": revocation.get("media_room_names") or [],
            "payload": revocation["event"],
        }
        delivered = await asyncio.wait_for(
            deliver_claimed_realtime_outbox_row(row),
            timeout=10,
        )
        if delivered:
            await anyio.to_thread.run_sync(
                lambda: mark_realtime_outbox_published_sync(
                    str(revocation["event_key"])
                )
            )
        return delivered

    try:
        return bool(anyio.from_thread.run(_dispatch))
    except Exception:
        logger.exception(
            "Immediate organization member revocation delivery failed for %s.",
            revocation.get("event_key"),
        )
        return False


def revalidate_organization_realtime_access_sync(
    *,
    organization_id: int,
    current_user: AuthenticatedUser,
) -> None:
    with get_db() as conn:
        require_business_or_enterprise_organization(
            conn,
            organization_id,
            current_user,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE member_presence
                SET last_seen_at = NOW(),
                    updated_at = NOW()
                WHERE organization_id = %s
                  AND user_id = %s
                """,
                (organization_id, current_user.user_id),
            )


def realtime_access_revoked_payload(
    *,
    organization_id: int,
    user_id: str,
) -> dict[str, Any]:
    return {
        "event_id": (
            f"organization.access.revoked:{organization_id}:"
            f"{user_id}:{uuid4().hex}"
        ),
        "type": "organization.access.revoked",
        "organization_id": organization_id,
        "user_id": user_id,
        "reason": "authorization_recheck_failed",
        "revoked_at": datetime.now(timezone.utc).isoformat(),
    }


async def active_organization_connection_counts(
    *,
    organization_id: int,
    user_ids: list[str] | set[str],
) -> dict[str, int]:
    counts = await TEAM_REALTIME_BROKER.active_connection_counts(
        organization_id=organization_id,
        user_ids=user_ids,
        scope="organization",
    )
    if counts is not None:
        return counts
    if TEAM_REALTIME_BROKER.required:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "presence_service_unavailable",
                "message": "Authoritative presence is temporarily unavailable.",
            },
        )
    return await TEAM_REALTIME_MANAGER.local_connection_counts(
        organization_id, user_ids
    )


async def account_realtime(websocket: WebSocket):
    """Shared, origin-validated account realtime channel."""

    current_user: AuthenticatedUser | None = None
    connected = False
    lease_registered = False
    connection_id = f"{TEAM_REALTIME_BROKER.instance_id}:{uuid4().hex}"
    last_heartbeat = asyncio.get_running_loop().time()
    refresh_requested = False

    try:
        validate_websocket_origin(websocket)
    except (HTTPException, RuntimeError):
        await websocket.close(code=1008, reason="WebSocket origin denied")
        return

    await websocket.accept()

    try:
        token = await receive_websocket_auth_token(websocket)
        current_user = authenticate_websocket_user(token)

        registration = await TEAM_REALTIME_BROKER.register_connection_limited(
            organization_id=0,
            user_id=current_user.user_id,
            connection_id=connection_id,
            max_connections=account_realtime_connections_per_user(),
            scope="account",
        )
        if registration == "limit":
            await websocket.close(code=1008, reason="Account realtime connection limit exceeded")
            return
        if registration == "unavailable" and TEAM_REALTIME_BROKER.required:
            await websocket.close(code=1013, reason="Shared realtime service unavailable")
            return
        lease_registered = registration == "registered"

        email = current_user.claims.get("email")
        connected = await TEAM_REALTIME_MANAGER.connect_account(
            user_id=current_user.user_id,
            email=email if isinstance(email, str) else None,
            websocket=websocket,
        )
        if not connected:
            await websocket.close(code=1008, reason="Account realtime connection limit exceeded")
            return

        await TEAM_REALTIME_MANAGER.send_socket(websocket, 
            normalize_realtime_event(
                {
                    "type": "account.realtime.connected",
                    "user": user_public_payload(current_user),
                }
            )
        )

        heartbeat_timeout = realtime_heartbeat_timeout_seconds()
        while True:
            now = asyncio.get_running_loop().time()
            if now - last_heartbeat > heartbeat_timeout:
                await websocket.close(code=1008, reason="Realtime heartbeat expired")
                return
            if token_is_expired(current_user):
                await websocket.close(code=1008, reason="Realtime token expired")
                return
            if token_needs_refresh(current_user) and not refresh_requested:
                refresh_requested = True
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "reauth.required",
                            "scope": "account",
                        }
                    )
                )

            try:
                event = await receive_json_frame(websocket, timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except ValueError as exc:
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "error",
                            "error": "invalid_realtime_frame",
                            "message": str(exc),
                        }
                    )
                )
                continue

            validate_client_event_contract(event)
            event_type = str(event.get("type") or "").strip().lower()

            if event_type == "ping":
                last_heartbeat = asyncio.get_running_loop().time()
                if lease_registered:
                    lease_registered = await TEAM_REALTIME_BROKER.refresh_connection(
                        organization_id=0,
                        user_id=current_user.user_id,
                        connection_id=connection_id,
                        scope="account",
                    )
                    if TEAM_REALTIME_BROKER.required and not lease_registered:
                        await websocket.close(code=1013, reason="Shared realtime service unavailable")
                        return
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event({"type": "pong", "scope": "account"})
                )
                continue

            if event_type == "auth.refresh":
                refreshed = authenticate_websocket_user(str(event.get("token") or ""))
                ensure_same_reauthenticated_user(current_user, refreshed)
                current_user = refreshed
                refresh_requested = False
                last_heartbeat = asyncio.get_running_loop().time()
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {"type": "reauth.succeeded", "scope": "account"}
                    )
                )
                continue

            await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                normalize_realtime_event(
                    {
                        "type": "error",
                        "error": "unsupported_account_realtime_event",
                        "message": "Unsupported account realtime event type.",
                    }
                )
            )

    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await TEAM_REALTIME_MANAGER.send_socket(websocket, websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
        except Exception:
            pass
    except Exception:
        logger.exception("Account realtime connection failed.")
        try:
            await websocket.close(code=1011, reason="Account realtime connection failed")
        except Exception:
            pass
    finally:
        if connected:
            await TEAM_REALTIME_MANAGER.disconnect_account(websocket)
        if current_user is not None and lease_registered:
            await TEAM_REALTIME_BROKER.unregister_connection_and_check_remaining(
                organization_id=0,
                user_id=current_user.user_id,
                connection_id=connection_id,
                scope="account",
            )


@router.websocket("/organizations/{organization_id}/realtime")
async def organization_realtime(
    websocket: WebSocket,
    organization_id: int,
):
    current_user: AuthenticatedUser | None = None
    connected = False
    lease_registered = False
    connection_id = f"{TEAM_REALTIME_BROKER.instance_id}:{uuid4().hex}"
    loop = asyncio.get_running_loop()
    last_heartbeat = loop.time()
    last_auth_recheck = 0.0
    last_presence_touch = 0.0
    refresh_requested = False
    auth_recheck_seconds = _positive_float_env(
        TEAM_REALTIME_AUTH_RECHECK_SECONDS_ENV,
        DEFAULT_TEAM_REALTIME_AUTH_RECHECK_SECONDS,
        maximum=60,
    )

    try:
        validate_websocket_origin(websocket)
    except (HTTPException, RuntimeError):
        await websocket.close(code=1008, reason="WebSocket origin denied")
        return

    await websocket.accept()

    try:
        token = await receive_websocket_auth_token(websocket)
        current_user = authenticate_websocket_user(token)

        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn, organization_id, current_user
            )
            touch_presence(conn, organization_id, current_user.user_id)

        registration = await TEAM_REALTIME_BROKER.register_connection_limited(
            organization_id=organization_id,
            user_id=current_user.user_id,
            connection_id=connection_id,
            max_connections=realtime_connections_per_user(),
            scope="organization",
        )
        if registration == "limit":
            await websocket.close(code=1008, reason="Organization realtime connection limit exceeded")
            return
        if registration == "unavailable" and TEAM_REALTIME_BROKER.required:
            await websocket.close(code=1013, reason="Shared realtime service unavailable")
            return
        lease_registered = registration == "registered"

        connected = await TEAM_REALTIME_MANAGER.connect(
            organization_id, current_user.user_id, websocket
        )
        if not connected:
            await websocket.close(code=1008, reason="Organization realtime connection limit exceeded")
            return

        last_heartbeat = loop.time()
        last_auth_recheck = last_heartbeat
        last_presence_touch = last_heartbeat

        await TEAM_REALTIME_MANAGER.send_socket(websocket, 
            normalize_realtime_event(
                {
                    "type": "realtime.connected",
                    "organization_id": organization_id,
                    "user": user_public_payload(current_user),
                }
            )
        )

        connection_counts = await active_organization_connection_counts(
            organization_id=organization_id,
            user_ids=[current_user.user_id],
        )
        connection_count = int((connection_counts or {}).get(current_user.user_id, 1))
        presence = await anyio.to_thread.run_sync(
            lambda: effective_presence_sync(
                organization_id=organization_id,
                user_id=current_user.user_id,
                online=connection_count > 0,
            )
        )
        presence["connection_count"] = connection_count
        await publish_organization_realtime_event(
            organization_id=organization_id,
            event={
                "type": "presence.updated",
                "organization_id": organization_id,
                "presence": presence,
                "user": user_public_payload(current_user),
            },
        )

        heartbeat_timeout = realtime_heartbeat_timeout_seconds()
        while True:
            now = loop.time()
            if now - last_heartbeat > heartbeat_timeout:
                await websocket.close(code=1008, reason="Realtime heartbeat expired")
                return
            if token_is_expired(current_user):
                await websocket.close(code=1008, reason="Realtime token expired")
                return
            if token_needs_refresh(current_user) and not refresh_requested:
                refresh_requested = True
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "reauth.required",
                            "scope": "organization",
                            "organization_id": organization_id,
                        }
                    )
                )

            if now - last_auth_recheck >= auth_recheck_seconds:
                try:
                    await anyio.to_thread.run_sync(
                        lambda: revalidate_organization_realtime_access_sync(
                            organization_id=organization_id,
                            current_user=current_user,
                        )
                    )
                except HTTPException:
                    await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                        normalize_realtime_event(
                            realtime_access_revoked_payload(
                                organization_id=organization_id,
                                user_id=current_user.user_id,
                            )
                        )
                    )
                    await websocket.close(code=1008, reason="Organization access revoked")
                    return
                last_auth_recheck = now

            try:
                event = await receive_json_frame(websocket, timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except ValueError as exc:
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "error",
                            "organization_id": organization_id,
                            "error": "invalid_realtime_frame",
                            "message": str(exc),
                        }
                    )
                )
                continue

            validate_client_event_contract(event)
            event_type = str(event.get("type") or "").strip().lower()

            if event_type == "auth.refresh":
                refreshed = authenticate_websocket_user(str(event.get("token") or ""))
                ensure_same_reauthenticated_user(current_user, refreshed)
                await anyio.to_thread.run_sync(
                    lambda: revalidate_organization_realtime_access_sync(
                        organization_id=organization_id,
                        current_user=refreshed,
                    )
                )
                current_user = refreshed
                refresh_requested = False
                last_heartbeat = loop.time()
                last_auth_recheck = last_heartbeat
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "reauth.succeeded",
                            "scope": "organization",
                            "organization_id": organization_id,
                        }
                    )
                )
                continue

            # Only an actual client frame renews the Redis lease. Server timers do
            # not keep dead browsers online.
            last_heartbeat = loop.time()
            if lease_registered:
                lease_registered = await TEAM_REALTIME_BROKER.refresh_connection(
                    organization_id=organization_id,
                    user_id=current_user.user_id,
                    connection_id=connection_id,
                    scope="organization",
                )
                if TEAM_REALTIME_BROKER.required and not lease_registered:
                    await websocket.close(code=1013, reason="Shared realtime service unavailable")
                    return

            if last_heartbeat - last_presence_touch >= 30:
                await anyio.to_thread.run_sync(
                    lambda: _touch_presence_sync(
                        organization_id, current_user.user_id
                    )
                )
                last_presence_touch = last_heartbeat

            if event_type == "ping":
                await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                    normalize_realtime_event(
                        {
                            "type": "pong",
                            "organization_id": organization_id,
                        }
                    )
                )
                continue

            if event_type == "presence.update":
                connection_counts = await active_organization_connection_counts(
                    organization_id=organization_id,
                    user_ids=[current_user.user_id],
                )
                connection_count = int(
                    (connection_counts or {}).get(current_user.user_id, 1)
                )
                presence = await anyio.to_thread.run_sync(
                    lambda: effective_presence_sync(
                        organization_id=organization_id,
                        user_id=current_user.user_id,
                        online=connection_count > 0,
                    )
                )
                presence["connection_count"] = connection_count
                await publish_organization_realtime_event(
                    organization_id=organization_id,
                    event={
                        "type": "presence.updated",
                        "organization_id": organization_id,
                        "presence": presence,
                        "user": user_public_payload(current_user),
                    },
                )
                continue

            if event_type == "message.send":
                raw_client_message_id = event.get("client_message_id") or event.get("clientMessageId")
                client_message_id = str(raw_client_message_id or "").strip()[:160]
                try:
                    conversation_id = parse_realtime_positive_int(
                        event.get("conversation_id") or event.get("conversationId"),
                        "conversation_id",
                    )
                    payload = SendMessageRequest(
                        body=event.get("body", ""),
                        client_message_id=raw_client_message_id,
                    )
                    saved = await anyio.to_thread.run_sync(
                        lambda: persist_text_message_sync(
                            organization_id=organization_id,
                            conversation_id=conversation_id,
                            current_user=current_user,
                            body=payload.body,
                            client_message_id=payload.client_message_id,
                            transport="websocket",
                        )
                    )
                except Exception as exc:
                    await TEAM_REALTIME_MANAGER.broadcast_to_users(
                        organization_id,
                        [current_user.user_id],
                        {
                            "type": "message.failed",
                            "organization_id": organization_id,
                            "client_message_id": client_message_id,
                            "error": realtime_error_code(exc),
                            "message": realtime_error_message(exc),
                        },
                    )
                    continue

                if saved["created"]:
                    dispatched = await publish_users_realtime_event(
                        organization_id=organization_id,
                        user_ids=saved["member_ids"],
                        event=saved["event"],
                    )
                    if dispatched:
                        try:
                            await anyio.to_thread.run_sync(
                                lambda: mark_realtime_outbox_published_sync(
                                    saved["event"]["event_id"]
                                )
                            )
                        except Exception:
                            pass

                await TEAM_REALTIME_MANAGER.broadcast_to_users(
                    organization_id,
                    [current_user.user_id],
                    {
                        "type": "message.ack",
                        "organization_id": organization_id,
                        "client_message_id": saved["client_message_id"],
                        "message": saved["message"],
                        "conversation": saved["conversation"],
                        "delivery": "committed",
                        "duplicate": not saved["created"],
                    },
                )
                continue

            await TEAM_REALTIME_MANAGER.send_socket(websocket, 
                normalize_realtime_event(
                    {
                        "type": "error",
                        "organization_id": organization_id,
                        "error": "unsupported_realtime_event",
                        "message": "Unsupported realtime event type.",
                    }
                )
            )

    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await TEAM_REALTIME_MANAGER.send_socket(websocket, websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
        except Exception:
            pass
    except Exception:
        logger.exception(
            "Organization realtime connection failed.",
            extra={"organization_id": organization_id},
        )
        try:
            await websocket.close(code=1011, reason="Realtime connection failed")
        except Exception:
            pass
    finally:
        if current_user is not None:
            if connected:
                await TEAM_REALTIME_MANAGER.disconnect(
                    organization_id, current_user.user_id, websocket
                )
            remaining: bool | None = None
            if lease_registered:
                remaining = await TEAM_REALTIME_BROKER.unregister_connection_and_check_remaining(
                    organization_id=organization_id,
                    user_id=current_user.user_id,
                    connection_id=connection_id,
                    scope="organization",
                )
            elif not TEAM_REALTIME_BROKER.required:
                remaining = await TEAM_REALTIME_MANAGER.has_user_connections(
                    organization_id, current_user.user_id
                )
            if remaining is False:
                try:
                    presence = await anyio.to_thread.run_sync(
                        lambda: effective_presence_sync(
                            organization_id=organization_id,
                            user_id=current_user.user_id,
                            online=False,
                        )
                    )
                    await publish_organization_realtime_event(
                        organization_id=organization_id,
                        event={
                            "type": "presence.updated",
                            "organization_id": organization_id,
                            "presence": presence,
                            "user": user_public_payload(current_user),
                        },
                    )
                except Exception:
                    logger.exception(
                        "Could not publish derived presence after disconnect.",
                        extra={"organization_id": organization_id},
                    )


def _touch_presence_sync(organization_id: int, user_id: str) -> None:
    with get_db() as conn:
        touch_presence(conn, organization_id, user_id)


@router.get("/organizations/{organization_id}/conversations")
def list_conversations(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn, organization_id, current_user
            )
            sync_organization_group_conversations(conn, organization_id)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        oc.id, oc.organization_id, oc.type, oc.name,
                        oc.created_by_user_id, oc.status, oc.last_message_at,
                        oc.created_at, oc.updated_at,
                        COALESCE(oc.membership_version, 1),
                        COALESCE(
                            JSONB_AGG(
                                JSONB_BUILD_OBJECT(
                                    'id', cm_all.id,
                                    'conversation_id', cm_all.conversation_id,
                                    'organization_id', cm_all.organization_id,
                                    'user_id', cm_all.user_id,
                                    'role', cm_all.role,
                                    'status', cm_all.status,
                                    'joined_at', cm_all.joined_at,
                                    'removed_at', cm_all.removed_at,
                                    'created_at', cm_all.created_at,
                                    'updated_at', cm_all.updated_at
                                )
                                ORDER BY
                                    CASE cm_all.role
                                        WHEN 'owner' THEN 1
                                        WHEN 'admin' THEN 2
                                        ELSE 3
                                    END,
                                    cm_all.created_at ASC,
                                    cm_all.id ASC
                            ) FILTER (WHERE cm_all.id IS NOT NULL),
                            '[]'::JSONB
                        ) AS members
                    FROM organization_conversations oc
                    JOIN conversation_members cm_self
                      ON cm_self.conversation_id = oc.id
                     AND cm_self.user_id = %s
                     AND cm_self.status = 'active'
                    LEFT JOIN conversation_members cm_all
                      ON cm_all.conversation_id = oc.id
                    WHERE oc.organization_id = %s
                      AND oc.status = 'active'
                    GROUP BY oc.id
                    ORDER BY COALESCE(oc.last_message_at, oc.updated_at) DESC,
                             oc.id DESC
                    """,
                    (current_user.user_id, organization_id),
                )
                rows = cur.fetchall()

            conversations = []
            for row in rows:
                conversation = row_to_conversation(row[:10])
                members = row[10] if isinstance(row[10], list) else []
                conversation["members"] = members
                conversation["member_user_ids"] = [
                    str(member.get("user_id"))
                    for member in members
                    if isinstance(member, dict)
                    and member.get("status") == "active"
                    and member.get("user_id")
                ]
                conversations.append(conversation)

        return {"success": True, "conversations": conversations}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "conversations_load_failed",
                "message": "Could not load conversations.",
            },
        ) from exc


@router.post("/organizations/{organization_id}/conversations")
def create_conversation(
    payload: CreateConversationRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            access = require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            org_membership = access["membership"]

            if payload.type == "group":
                require_org_owner(org_membership)

            member_user_ids = [
                user_id
                for user_id in payload.member_user_ids
                if user_id != current_user.user_id
            ]

            if payload.type == "dm":
                target_user_id = member_user_ids[0]
                require_active_org_members(conn, organization_id, [target_user_id])
                acquire_conversation_creation_lock(
                    conn,
                    organization_id=organization_id,
                    conversation_type="dm",
                    member_user_ids=[current_user.user_id, target_user_id],
                )

                existing_dm = get_existing_dm_conversation(
                    conn,
                    organization_id,
                    current_user.user_id,
                    target_user_id,
                )

                if existing_dm is not None:
                    return {
                        "success": True,
                        "conversation": add_members_to_conversation_payload(
                            conn,
                            existing_dm,
                        ),
                        "members": fetch_conversation_members(conn, existing_dm["id"]),
                        "already_exists": True,
                    }

                final_member_ids = [current_user.user_id, target_user_id]
                conversation_name = None
            else:
                acquire_conversation_creation_lock(
                    conn,
                    organization_id=organization_id,
                    conversation_type="group",
                )
                existing_group = get_existing_group_conversation(
                    conn,
                    organization_id,
                )

                if existing_group is not None:
                    sync_group_conversation_members(
                        conn,
                        organization_id,
                        existing_group["id"],
                    )
                    return {
                        "success": True,
                        "conversation": add_members_to_conversation_payload(
                            conn,
                            existing_group,
                        ),
                        "members": fetch_conversation_members(conn, existing_group["id"]),
                        "already_exists": True,
                    }

                final_member_ids = get_active_organization_member_ids(
                    conn,
                    organization_id,
                )
                if current_user.user_id not in final_member_ids:
                    final_member_ids.insert(0, current_user.user_id)
                conversation_name = payload.name

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO organization_conversations (
                        organization_id,
                        type,
                        name,
                        created_by_user_id
                    )
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, organization_id, type, name,
                              created_by_user_id, status, last_message_at,
                              created_at, updated_at
                    """,
                    (
                        organization_id,
                        payload.type,
                        conversation_name,
                        current_user.user_id,
                    ),
                )
                conversation_row = cur.fetchone()
                conversation = row_to_conversation(conversation_row)

                for member_user_id in final_member_ids:
                    role = "owner" if member_user_id == current_user.user_id else "member"
                    cur.execute(
                        """
                        INSERT INTO conversation_members (
                            conversation_id,
                            organization_id,
                            user_id,
                            role,
                            status,
                            joined_at
                        )
                        VALUES (%s, %s, %s, %s, 'active', NOW())
                        ON CONFLICT (conversation_id, user_id) DO UPDATE SET
                            role = EXCLUDED.role,
                            status = 'active',
                            removed_at = NULL,
                            updated_at = NOW()
                        """,
                        (
                            conversation["id"],
                            organization_id,
                            member_user_id,
                            role,
                        ),
                    )

            conversation_payload = add_members_to_conversation_payload(conn, conversation)
            conversation_member_rows = fetch_conversation_members(conn, conversation["id"])

        member_ids = [
            member["user_id"]
            for member in conversation_member_rows
            if member.get("status") == "active"
        ]
        dispatch_realtime_event(
            organization_id=organization_id,
            user_ids=member_ids,
            event={
                "type": "conversation.created",
                "conversation": compact_conversation_payload(conversation_payload),
                "membership_version": int(
                    conversation_payload.get("membership_version") or 1
                ),
                "created_by_user_id": current_user.user_id,
                "user": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "conversation": conversation_payload,
            "members": conversation_member_rows,
            "already_exists": False,
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_conversation",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "conversation_create_failed",
                "message": "Could not create conversation.",
            },
        ) from exc



MAX_FORWARD_RECIPIENTS = 50
DEFAULT_FORWARD_PREPARE_CONCURRENCY = 4
MAX_FORWARD_PREPARE_CONCURRENCY = 8


def _forward_prepare_concurrency(item_count: int) -> int:
    raw = os.getenv(
        "TEAM_ATTACHMENT_PREPARE_CONCURRENCY",
        str(DEFAULT_FORWARD_PREPARE_CONCURRENCY),
    ).strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = DEFAULT_FORWARD_PREPARE_CONCURRENCY
    return max(1, min(item_count, configured, MAX_FORWARD_PREPARE_CONCURRENCY))


def _load_forward_source_attachment(
    attachment_id: int,
    *,
    source_message_id: int,
    organization_id: int,
) -> dict[str, Any]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, message_id, conversation_id, organization_id,
                       uploaded_by_user_id, kind, original_filename,
                       stored_filename, storage_key, content_type,
                       file_size_bytes, checksum_sha256, storage_backend,
                       security_status, malware_scan_status,
                       detected_content_type, validation_version,
                       encryption_algorithm, encryption_key_id,
                       encryption_nonce, encryption_aad_version,
                       encrypted_content, secured_at,
                       last_integrity_verified_at, created_at,
                       malware_scanner, malware_scanner_version,
                       scan_completed_at, security_metadata
                FROM conversation_message_attachments
                WHERE id = %s
                  AND message_id = %s
                  AND organization_id = %s
                  AND security_status = 'secured'
                """,
                (attachment_id, source_message_id, organization_id),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "attachment_not_found",
                "message": "A forwarded attachment was not found.",
            },
        )
    record = row_to_secured_attachment_record(row[:25])
    record.update(
        {
            "malware_scanner": row[25],
            "malware_scanner_version": row[26],
            "scan_completed_at": row[27],
            "security_metadata": row[28] if isinstance(row[28], dict) else {},
        }
    )
    return record


def _stage_forwarded_attachments(
    *,
    attachment_ids: list[int],
    source_message_id: int,
    organization_id: int,
    target_conversation_id: int,
    forwarded_by_user_id: str,
):
    if not attachment_ids:
        return []
    staged = []

    def prepare(attachment_id: int):
        source = _load_forward_source_attachment(
            attachment_id,
            source_message_id=source_message_id,
            organization_id=organization_id,
        )
        return stage_forwarded_team_attachment(
            source=source,
            target_conversation_id=target_conversation_id,
            forwarded_by_user_id=forwarded_by_user_id,
        )

    workers = _forward_prepare_concurrency(len(attachment_ids))
    try:
        if workers == 1:
            for attachment_id in attachment_ids:
                staged.append(prepare(attachment_id))
            return staged

        futures = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(prepare, item) for item in attachment_ids]
            try:
                for future in futures:
                    staged.append(future.result())
            except Exception:
                for future in futures:
                    if not future.done():
                        future.cancel()
                        continue
                    try:
                        item = future.result()
                    except Exception:
                        continue
                    if item not in staged:
                        item.cleanup()
                raise
        return staged
    except Exception:
        for item in staged:
            item.cleanup()
        raise


def _insert_forwarded_staged_attachment(
    conn,
    *,
    message_id: int,
    conversation_id: int,
    organization_id: int,
    forwarded_by_user_id: str,
    staged,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_message_attachments (
                message_id, conversation_id, organization_id,
                uploaded_by_user_id, kind, original_filename,
                stored_filename, storage_key, content_type,
                file_size_bytes, checksum_sha256, storage_backend,
                security_status, malware_scan_status, malware_scanner,
                malware_scanner_version, scan_completed_at,
                detected_content_type, validation_version,
                encryption_algorithm, encryption_key_id, encryption_nonce,
                encryption_aad_version, encrypted_content, secured_at,
                security_metadata
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'postgres_encrypted', 'secured', %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id, message_id, conversation_id, organization_id,
                      uploaded_by_user_id, kind, original_filename,
                      content_type, file_size_bytes, checksum_sha256,
                      security_status, malware_scan_status,
                      secured_at, created_at
            """,
            (
                message_id,
                conversation_id,
                organization_id,
                forwarded_by_user_id,
                staged.kind,
                staged.original_filename,
                staged.stored_filename,
                staged.storage_key,
                staged.content_type,
                staged.file_size_bytes,
                staged.checksum_sha256,
                staged.malware_scan_status,
                staged.malware_scanner,
                staged.malware_scanner_version,
                staged.scan_completed_at,
                staged.detected_content_type,
                staged.validation_version,
                staged.encryption_algorithm,
                staged.encryption_key_id,
                staged.encryption_nonce,
                staged.encryption_aad_version,
                staged.read_encrypted_content(),
                staged.scan_completed_at,
                Jsonb(staged.security_metadata),
            ),
        )
        return row_to_attachment(cur.fetchone())


def _ensure_forward_dm(
    *,
    organization_id: int,
    recipient_user_id: str,
    current_user: AuthenticatedUser,
) -> dict[str, Any]:
    with get_db() as conn:
        require_business_or_enterprise_organization(
            conn,
            organization_id,
            current_user,
        )
        require_active_org_members(conn, organization_id, [recipient_user_id])
        conversation, created = get_or_create_dm_conversation(
            conn,
            organization_id=organization_id,
            sender_user_id=current_user.user_id,
            target_user_id=recipient_user_id,
        )
        conversation_payload = add_members_to_conversation_payload(conn, conversation)
        members = fetch_conversation_members(conn, int(conversation["id"]))
    return {
        "conversation": conversation_payload,
        "members": members,
        "created": created,
    }


def _persist_forwarded_message(
    *,
    organization_id: int,
    recipient_user_id: str,
    source_message: dict[str, Any],
    target_conversation: dict[str, Any],
    staged_attachments: list[Any],
    client_message_id: str,
    current_user: AuthenticatedUser,
    request_id: str,
) -> dict[str, Any]:
    conversation_id = int(target_conversation["id"])
    with get_db() as conn:
        require_business_or_enterprise_organization(
            conn,
            organization_id,
            current_user,
        )
        require_active_org_members(conn, organization_id, [recipient_user_id])
        require_active_conversation_member(
            conn,
            conversation_id,
            current_user.user_id,
        )
        require_active_conversation_member(
            conn,
            conversation_id,
            recipient_user_id,
        )
        require_active_conversation_member(
            conn,
            int(source_message["conversation_id"]),
            current_user.user_id,
        )
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM conversation_messages
                WHERE id = %s
                  AND conversation_id = %s
                  AND organization_id = %s
                  AND deleted_at IS NULL
                """,
                (
                    int(source_message["id"]),
                    int(source_message["conversation_id"]),
                    organization_id,
                ),
            )
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "message_not_forwardable",
                        "message": "The source message is no longer available to forward.",
                    },
                )

        if staged_attachments:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(%s::bigint)",
                    (organization_id,),
                )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, conversation_id, organization_id,
                       sender_user_id, message_type, body, metadata,
                       edited_at, deleted_at, created_at, updated_at
                FROM conversation_messages
                WHERE conversation_id = %s
                  AND sender_user_id = %s
                  AND client_message_id = %s
                """,
                (conversation_id, current_user.user_id, client_message_id),
            )
            existing_row = cur.fetchone()

        if existing_row is not None:
            existing_message = add_attachments_to_message(
                conn,
                row_to_message(existing_row),
            )
            source_id = int(
                (existing_message.get("metadata") or {}).get(
                    "forwarded_from_message_id"
                )
                or 0
            )
            if source_id != int(source_message["id"]):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "client_message_id_conflict",
                        "message": "The forward request identifier is already in use.",
                    },
                )
            return {
                "created": False,
                "message": existing_message,
                "conversation": compact_conversation_payload(
                    get_conversation(conn, conversation_id)
                ),
                "member_ids": get_active_conversation_member_ids(
                    conn,
                    conversation_id,
                ),
                "event": None,
            }

        if staged_attachments:
            forwarded_bytes = sum(
                int(item.file_size_bytes) for item in staged_attachments
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(file_size_bytes), 0)
                    FROM conversation_message_attachments
                    WHERE organization_id = %s
                      AND security_status = 'secured'
                    """,
                    (organization_id,),
                )
                used_bytes = int(cur.fetchone()[0] or 0)
            if used_bytes + forwarded_bytes > get_team_secure_attachment_org_quota_bytes():
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "attachment_storage_quota_exceeded",
                        "message": "The organization secure attachment storage quota has been reached.",
                    },
                )

        metadata = {
            "client_message_id": client_message_id,
            "transport": "forward",
            "forwarded_from_message_id": int(source_message["id"]),
            "forwarded_from_conversation_id": int(source_message["conversation_id"]),
            "forwarded_by_user_id": current_user.user_id,
            "attachment_count": len(staged_attachments),
            "attachments": [],
        }
        message_type = "attachment" if staged_attachments else "text"
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_messages (
                    conversation_id, organization_id, sender_user_id,
                    message_type, body, metadata, client_message_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id, conversation_id, organization_id,
                          sender_user_id, message_type, body, metadata,
                          edited_at, deleted_at, created_at, updated_at
                """,
                (
                    conversation_id,
                    organization_id,
                    current_user.user_id,
                    message_type,
                    source_message["body"],
                    Jsonb(metadata),
                    client_message_id,
                ),
            )
            message = row_to_message(cur.fetchone())

        attachments = []
        for staged in staged_attachments:
            attachment = _insert_forwarded_staged_attachment(
                conn,
                message_id=int(message["id"]),
                conversation_id=conversation_id,
                organization_id=organization_id,
                forwarded_by_user_id=current_user.user_id,
                staged=staged,
            )
            attachments.append(attachment)
            insert_attachment_security_event(
                conn,
                organization_id=organization_id,
                conversation_id=conversation_id,
                message_id=int(message["id"]),
                attachment_id=int(attachment["id"]),
                actor_user_id=current_user.user_id,
                action="upload",
                outcome="succeeded",
                reason_code="forwarded_secured_source",
                request_id=request_id,
                details={
                    "source_message_id": int(source_message["id"]),
                    "file_size_bytes": staged.file_size_bytes,
                    "integrity_verified_before_forward": True,
                },
            )

        metadata["attachments"] = attachments
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE conversation_messages
                SET metadata = %s,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING id, conversation_id, organization_id,
                          sender_user_id, message_type, body, metadata,
                          edited_at, deleted_at, created_at, updated_at
                """,
                (Jsonb(normalize_realtime_payload(metadata)), message["id"]),
            )
            message = row_to_message(cur.fetchone())

        conversation = get_conversation(conn, conversation_id)
        conversation_payload = compact_conversation_payload(conversation)
        member_ids = get_active_conversation_member_ids(conn, conversation_id)
        message = {
            **message,
            "client_message_id": client_message_id,
            "pending": False,
        }
        event = {
            "event_id": f"message.created:{message['id']}",
            "type": "message.created",
            "organization_id": organization_id,
            "client_message_id": client_message_id,
            "message": message,
            "conversation": conversation_payload,
            "sender": user_public_payload(current_user),
            "delivery": "committed",
        }
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO team_realtime_outbox (
                    organization_id, aggregate_type, aggregate_id,
                    event_type, event_key, recipient_user_ids, payload
                )
                VALUES (%s, 'message', %s, 'message.created', %s, %s, %s)
                ON CONFLICT (event_key) DO NOTHING
                """,
                (
                    organization_id,
                    str(message["id"]),
                    event["event_id"],
                    member_ids,
                    Jsonb(normalize_realtime_event(event)),
                ),
            )

    return {
        "created": True,
        "message": message,
        "conversation": conversation_payload,
        "member_ids": member_ids,
        "event": event,
    }


@router.post("/organizations/{organization_id}/messages/{message_id}/forward")
def forward_message_to_members(
    payload: ForwardMessageRequest,
    request: Request,
    organization_id: int = Path(..., ge=1),
    message_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_id = attachment_request_id(request)
    try:
        recipient_user_ids = [
            user_id
            for user_id in payload.recipient_user_ids
            if user_id != current_user.user_id
        ]
        if not recipient_user_ids:
            raise ValueError("Choose at least one other organization member.")
        if len(recipient_user_ids) > MAX_FORWARD_RECIPIENTS:
            raise ValueError(
                f"A message may be forwarded to at most {MAX_FORWARD_RECIPIENTS} members at once."
            )
        client_message_id = normalize_client_message_id(payload.client_message_id)

        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            require_active_org_members(conn, organization_id, recipient_user_ids)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, conversation_id, organization_id,
                           sender_user_id, message_type, body, metadata,
                           edited_at, deleted_at, created_at, updated_at
                    FROM conversation_messages
                    WHERE id = %s
                      AND organization_id = %s
                      AND deleted_at IS NULL
                    """,
                    (message_id, organization_id),
                )
                source_row = cur.fetchone()
            if source_row is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "message_not_found",
                        "message": "The message to forward was not found.",
                    },
                )
            source_message = row_to_message(source_row)
            require_active_conversation_member(
                conn,
                int(source_message["conversation_id"]),
                current_user.user_id,
            )
            if source_message["message_type"] not in {"text", "attachment"}:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "message_not_forwardable",
                        "message": "Only text and attachment messages can be forwarded.",
                    },
                )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, security_status
                    FROM conversation_message_attachments
                    WHERE message_id = %s
                      AND organization_id = %s
                    ORDER BY id ASC
                    """,
                    (message_id, organization_id),
                )
                attachment_rows = cur.fetchall()
            if source_message["message_type"] == "attachment":
                if not attachment_rows or any(
                    str(row[1] or "") != "secured" for row in attachment_rows
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "attachment_not_forwardable",
                            "message": (
                                "Every attachment in this message must be secured before "
                                "the message can be forwarded."
                            ),
                        },
                    )
                if len(attachment_rows) > 50:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "too_many_attachments",
                            "message": "A forwarded message may contain at most 50 attachments.",
                        },
                    )
            attachment_ids = [int(row[0]) for row in attachment_rows]

        deliveries = []
        failures = []
        for recipient_user_id in recipient_user_ids:
            staged = []
            try:
                dm = _ensure_forward_dm(
                    organization_id=organization_id,
                    recipient_user_id=recipient_user_id,
                    current_user=current_user,
                )
                target_conversation = dm["conversation"]
                if dm["created"]:
                    member_ids = [
                        member["user_id"]
                        for member in dm["members"]
                        if member.get("status") == "active"
                    ]
                    dispatch_realtime_event(
                        organization_id=organization_id,
                        user_ids=member_ids,
                        event={
                            "type": "conversation.created",
                            "conversation": compact_conversation_payload(
                                target_conversation
                            ),
                            "membership_version": int(
                                target_conversation.get("membership_version") or 1
                            ),
                            "created_by_user_id": current_user.user_id,
                            "user": user_public_payload(current_user),
                        },
                    )

                staged = _stage_forwarded_attachments(
                    attachment_ids=attachment_ids,
                    source_message_id=int(source_message["id"]),
                    organization_id=organization_id,
                    target_conversation_id=int(target_conversation["id"]),
                    forwarded_by_user_id=current_user.user_id,
                )
                saved = _persist_forwarded_message(
                    organization_id=organization_id,
                    recipient_user_id=recipient_user_id,
                    source_message=source_message,
                    target_conversation=target_conversation,
                    staged_attachments=staged,
                    client_message_id=client_message_id,
                    current_user=current_user,
                    request_id=request_id,
                )
                if saved["created"] and saved["event"] is not None:
                    dispatched = dispatch_realtime_event(
                        organization_id=organization_id,
                        user_ids=saved["member_ids"],
                        event=saved["event"],
                    )
                    if dispatched:
                        try:
                            mark_realtime_outbox_published_sync(
                                saved["event"]["event_id"]
                            )
                        except Exception:
                            pass
                deliveries.append(
                    {
                        "recipient_user_id": recipient_user_id,
                        "conversation": saved["conversation"],
                        "message": saved["message"],
                        "duplicate": not saved["created"],
                    }
                )
            except Exception as exc:
                if isinstance(exc, HTTPException):
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    code = str(detail.get("error") or "forward_failed")
                    message = str(detail.get("message") or "Could not forward message.")
                elif isinstance(exc, TeamAttachmentSecurityError):
                    code = exc.code
                    message = exc.public_message
                else:
                    logger.exception(
                        "Could not forward message to member.",
                        extra={
                            "organization_id": organization_id,
                            "recipient_user_id": recipient_user_id,
                            "source_message_id": message_id,
                        },
                    )
                    code = "forward_failed"
                    message = "Could not forward message to this member."
                failures.append(
                    {
                        "recipient_user_id": recipient_user_id,
                        "error": code,
                        "message": message,
                    }
                )
            finally:
                for item in staged:
                    item.cleanup()

        return {
            "success": not failures,
            "partial": bool(deliveries and failures),
            "source_message_id": message_id,
            "client_message_id": client_message_id,
            "delivered_count": len(deliveries),
            "failed_count": len(failures),
            "deliveries": deliveries,
            "failures": failures,
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_forward_request",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "Could not forward team message.",
            extra={
                "organization_id": organization_id,
                "source_message_id": message_id,
            },
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "message_forward_failed",
                "message": "Could not forward message.",
            },
        ) from exc


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: int = Path(..., ge=1),
    limit: int = Query(50, ge=1, le=200),
    before_message_id: int | None = Query(None, ge=1),
    after_message_id: int | None = Query(None, ge=0),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if before_message_id is not None and after_message_id is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_message_cursor",
                "message": "Use either before_message_id or after_message_id, not both.",
            },
        )

    try:
        with get_db() as conn:
            conversation = get_conversation(conn, conversation_id)
            require_business_or_enterprise_organization(
                conn, conversation["organization_id"], current_user
            )
            require_active_conversation_member(
                conn, conversation_id, current_user.user_id
            )

            conditions = [
                "conversation_id = %s",
                "deleted_at IS NULL",
            ]
            params: list[Any] = [conversation_id]
            order = "DESC"
            if before_message_id is not None:
                conditions.append("id < %s")
                params.append(before_message_id)
            elif after_message_id is not None:
                conditions.append("id > %s")
                params.append(after_message_id)
                order = "ASC"
            params.append(limit)

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, conversation_id, organization_id,
                           sender_user_id, message_type, body, metadata,
                           edited_at, deleted_at, created_at, updated_at
                    FROM conversation_messages
                    WHERE {' AND '.join(conditions)}
                    ORDER BY id {order}
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()

            messages = [row_to_message(row) for row in rows]
            messages = add_attachments_to_messages(conn, messages)
            messages = add_call_states_to_messages(conn, messages)

        if order == "DESC":
            messages.reverse()
        return {
            "success": True,
            "conversation": compact_conversation_payload(conversation),
            "messages": messages,
            "latest_message_id": max(
                [int(message["id"]) for message in messages],
                default=int(after_message_id or 0),
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "messages_load_failed",
                "message": "Could not load messages.",
            },
        ) from exc


@router.get("/organizations/{organization_id}/messages/replay")
def replay_organization_messages(
    organization_id: int = Path(..., ge=1),
    after_message_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Replay committed messages missed while the organization socket was down."""

    try:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn, organization_id, current_user
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        cm.id, cm.conversation_id, cm.organization_id,
                        cm.sender_user_id, cm.message_type, cm.body, cm.metadata,
                        cm.edited_at, cm.deleted_at, cm.created_at, cm.updated_at,
                        oc.type, oc.name, oc.status, oc.last_message_at,
                        oc.updated_at, COALESCE(oc.membership_version, 1),
                        om.member_name, om.member_email
                    FROM conversation_messages cm
                    JOIN organization_conversations oc
                      ON oc.id = cm.conversation_id
                     AND oc.organization_id = cm.organization_id
                    JOIN conversation_members receiver
                      ON receiver.conversation_id = cm.conversation_id
                     AND receiver.user_id = %s
                     AND receiver.status = 'active'
                    LEFT JOIN organization_members om
                      ON om.organization_id = cm.organization_id
                     AND om.user_id = cm.sender_user_id
                    WHERE cm.organization_id = %s
                      AND cm.deleted_at IS NULL
                      AND cm.id > %s
                    ORDER BY cm.id ASC
                    LIMIT %s
                    """,
                    (current_user.user_id, organization_id, after_message_id, limit),
                )
                rows = cur.fetchall()

            messages = [row_to_message(row[:11]) for row in rows]
            messages = add_attachments_to_messages(conn, messages)
            messages = add_call_states_to_messages(conn, messages)
            message_by_id = {int(message["id"]): message for message in messages}
            events = []
            for row in rows:
                message = message_by_id[int(row[0])]
                conversation = {
                    "id": row[1],
                    "organization_id": row[2],
                    "type": row[11],
                    "name": row[12],
                    "status": row[13],
                    "last_message_at": row[14],
                    "updated_at": row[15],
                    "membership_version": row[16],
                }
                events.append(
                    normalize_realtime_event(
                        {
                            "event_id": f"message.created:{message['id']}",
                            "type": "message.created",
                            "organization_id": organization_id,
                            "client_message_id": message.get("client_message_id"),
                            "message": message,
                            "conversation": compact_conversation_payload(conversation),
                            "sender": {
                                "id": message.get("sender_user_id"),
                                "name": row[17],
                                "email": row[18],
                            },
                            "delivery": "replayed",
                        }
                    )
                )

        return {
            "success": True,
            "events": events,
            "latest_message_id": max(
                [int(event["message"]["id"]) for event in events],
                default=after_message_id,
            ),
            "has_more": len(events) == limit,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "message_replay_failed",
                "message": "Could not replay missed messages.",
            },
        ) from exc


@router.post("/conversations/{conversation_id}/messages")
def send_message(
    payload: SendMessageRequest,
    conversation_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            conversation = get_conversation(conn, conversation_id)
            organization_id = int(conversation["organization_id"])

        saved = persist_text_message_sync(
            organization_id=organization_id,
            conversation_id=conversation_id,
            current_user=current_user,
            body=payload.body,
            client_message_id=payload.client_message_id,
            transport="http",
        )

        if saved["created"]:
            dispatched = dispatch_realtime_event(
                organization_id=organization_id,
                user_ids=saved["member_ids"],
                event=saved["event"],
            )
            if dispatched:
                try:
                    mark_realtime_outbox_published_sync(
                        saved["event"]["event_id"]
                    )
                except Exception:
                    # Keep the event pending for the durable dispatcher.
                    pass

        return {
            "success": True,
            "message": saved["message"],
            "conversation": saved["conversation"],
            "client_message_id": saved["client_message_id"],
            "delivery": "committed",
            "duplicate": not saved["created"],
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_message",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "message_send_failed",
                "message": "Could not send message.",
            },
        ) from exc


def _legacy_start_call_unregistered(
    conversation_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            conversation = get_conversation(conn, conversation_id)
            require_business_or_enterprise_organization(
                conn,
                conversation["organization_id"],
                current_user,
            )
            require_active_conversation_member(
                conn,
                conversation_id,
                current_user.user_id,
            )

            conversation_members = [
                member
                for member in fetch_conversation_members(conn, conversation_id)
                if member["status"] == "active"
            ]

            if len(conversation_members) < 2:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "not_enough_call_participants",
                        "message": "A call requires at least two active conversation members.",
                    },
                )

            call_type = "group" if conversation["type"] == "group" else "one_to_one"
            livekit_room_name = (
                f"org-{conversation['organization_id']}-"
                f"conv-{conversation_id}-"
                f"call-{uuid4().hex}"
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO call_sessions (
                        organization_id,
                        conversation_id,
                        type,
                        status,
                        created_by_user_id,
                        livekit_room_name
                    )
                    VALUES (%s, %s, %s, 'ringing', %s, %s)
                    RETURNING id, organization_id, conversation_id, type, status,
                              created_by_user_id, livekit_room_name, started_at,
                              ended_at, created_at, updated_at
                    """,
                    (
                        conversation["organization_id"],
                        conversation_id,
                        call_type,
                        current_user.user_id,
                        livekit_room_name,
                    ),
                )
                call_row = cur.fetchone()
                call = row_to_call_session(call_row)

                for member in conversation_members:
                    participant_status = (
                        "joined"
                        if member["user_id"] == current_user.user_id
                        else "invited"
                    )
                    cur.execute(
                        """
                        INSERT INTO call_participants (
                            call_session_id,
                            organization_id,
                            user_id,
                            status,
                            joined_at
                        )
                        VALUES (%s, %s, %s, %s, CASE WHEN %s = 'joined' THEN NOW() ELSE NULL END)
                        ON CONFLICT (call_session_id, user_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            joined_at = COALESCE(call_participants.joined_at, EXCLUDED.joined_at),
                            updated_at = NOW()
                        """,
                        (
                            call["id"],
                            conversation["organization_id"],
                            member["user_id"],
                            participant_status,
                            participant_status,
                        ),
                    )

                cur.execute(
                    """
                    UPDATE call_sessions
                    SET status = 'active',
                        started_at = COALESCE(started_at, NOW()),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, organization_id, conversation_id, type, status,
                              created_by_user_id, livekit_room_name, started_at,
                              ended_at, created_at, updated_at
                    """,
                    (call["id"],),
                )
                call = row_to_call_session(cur.fetchone())

                cur.execute(
                    """
                    INSERT INTO conversation_messages (
                        conversation_id,
                        organization_id,
                        sender_user_id,
                        message_type,
                        body,
                        metadata
                    )
                    VALUES (%s, %s, %s, 'call_event', %s, %s::jsonb)
                    RETURNING id, conversation_id, organization_id,
                              sender_user_id, message_type, body, metadata,
                              edited_at, deleted_at, created_at, updated_at
                    """,
                    (
                        conversation_id,
                        conversation["organization_id"],
                        current_user.user_id,
                        "Call started.",
                        f'{{"call_session_id": {call["id"]}}}',
                    ),
                )
                call_message = row_to_message(cur.fetchone())

                cur.execute(
                    """
                    UPDATE organization_conversations
                    SET last_message_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (conversation_id,),
                )

                upsert_presence(
                    conn,
                    conversation["organization_id"],
                    current_user.user_id,
                    "in_call",
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, call_session_id, organization_id, user_id,
                           status, invited_at, joined_at, left_at,
                           created_at, updated_at
                    FROM call_participants
                    WHERE call_session_id = %s
                    ORDER BY id ASC
                    """,
                    (call["id"],),
                )
                participant_rows = cur.fetchall()

            participants = [
                row_to_call_participant(row) for row in participant_rows
            ]
            conversation_payload = add_members_to_conversation_payload(conn, conversation)
            livekit_payload = generate_livekit_join_payload(
                current_user=current_user,
                room_name=call["livekit_room_name"],
            )

        member_ids = [member["user_id"] for member in conversation_members]
        dispatch_realtime_event(
            organization_id=conversation["organization_id"],
            user_ids=member_ids,
            event={
                "type": "call.started",
                "call": call,
                "participants": participants,
                "conversation": conversation_payload,
                "message": call_message,
                "sender": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "call": call,
            "participants": participants,
            "message": call_message,
            "livekit": livekit_payload,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "call_start_failed",
                "message": "Could not start call.",
            },
        ) from exc


def _legacy_join_call_unregistered(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = get_call_session(conn, call_session_id)
            require_business_or_enterprise_organization(
                conn,
                call["organization_id"],
                current_user,
            )

            if call["conversation_id"] is not None:
                require_active_conversation_member(
                    conn,
                    call["conversation_id"],
                    current_user.user_id,
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO call_participants (
                        call_session_id,
                        organization_id,
                        user_id,
                        status,
                        joined_at
                    )
                    VALUES (%s, %s, %s, 'joined', NOW())
                    ON CONFLICT (call_session_id, user_id) DO UPDATE SET
                        status = 'joined',
                        joined_at = COALESCE(call_participants.joined_at, NOW()),
                        left_at = NULL,
                        updated_at = NOW()
                    RETURNING id, call_session_id, organization_id, user_id,
                              status, invited_at, joined_at, left_at,
                              created_at, updated_at
                    """,
                    (
                        call_session_id,
                        call["organization_id"],
                        current_user.user_id,
                    ),
                )
                participant = row_to_call_participant(cur.fetchone())

                cur.execute(
                    """
                    UPDATE call_sessions
                    SET status = 'active',
                        started_at = COALESCE(started_at, NOW()),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, organization_id, conversation_id, type, status,
                              created_by_user_id, livekit_room_name, started_at,
                              ended_at, created_at, updated_at
                    """,
                    (call_session_id,),
                )
                call = row_to_call_session(cur.fetchone())

                presence = upsert_presence(
                    conn,
                    call["organization_id"],
                    current_user.user_id,
                    "in_call",
                )
                livekit_payload = generate_livekit_join_payload(
                    current_user=current_user,
                    room_name=call["livekit_room_name"],
                )

        if call.get("conversation_id"):
            with get_db() as realtime_conn:
                conversation_member_rows = fetch_conversation_members(
                    realtime_conn,
                    call["conversation_id"],
                )
            member_ids = [
                member["user_id"]
                for member in conversation_member_rows
                if member.get("status") == "active"
            ]
        else:
            member_ids = [current_user.user_id]

        dispatch_realtime_event(
            organization_id=call["organization_id"],
            user_ids=member_ids,
            event={
                "type": "call.joined",
                "call": call,
                "participant": participant,
                "presence": presence,
                "user": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "call": call,
            "participant": participant,
            "presence": presence,
            "livekit": livekit_payload,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "call_join_failed",
                "message": "Could not join call.",
            },
        ) from exc


def _legacy_leave_call_unregistered(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = get_call_session(conn, call_session_id)
            require_business_or_enterprise_organization(
                conn,
                call["organization_id"],
                current_user,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_participants
                    SET status = 'left',
                        left_at = NOW(),
                        updated_at = NOW()
                    WHERE call_session_id = %s
                      AND user_id = %s
                    RETURNING id, call_session_id, organization_id, user_id,
                              status, invited_at, joined_at, left_at,
                              created_at, updated_at
                    """,
                    (call_session_id, current_user.user_id),
                )
                participant_row = cur.fetchone()

                if participant_row is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "call_participant_not_found",
                            "message": "You are not a participant in this call.",
                        },
                    )

                participant = row_to_call_participant(participant_row)

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM call_participants
                    WHERE call_session_id = %s
                      AND status = 'joined'
                    """,
                    (call_session_id,),
                )
                joined_count = int(cur.fetchone()[0])

                if joined_count == 0:
                    cur.execute(
                        """
                        UPDATE call_sessions
                        SET status = 'ended',
                            ended_at = COALESCE(ended_at, NOW()),
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING id, organization_id, conversation_id, type, status,
                                  created_by_user_id, livekit_room_name, started_at,
                                  ended_at, created_at, updated_at
                        """,
                        (call_session_id,),
                    )
                    call = row_to_call_session(cur.fetchone())

                presence = upsert_presence(
                    conn,
                    call["organization_id"],
                    current_user.user_id,
                    "online",
                )

        if call.get("conversation_id"):
            with get_db() as realtime_conn:
                conversation_member_rows = fetch_conversation_members(
                    realtime_conn,
                    call["conversation_id"],
                )
            member_ids = [
                member["user_id"]
                for member in conversation_member_rows
                if member.get("status") == "active"
            ]
        else:
            member_ids = [current_user.user_id]

        dispatch_realtime_event(
            organization_id=call["organization_id"],
            user_ids=member_ids,
            event={
                "type": "call.left",
                "call": call,
                "participant": participant,
                "presence": presence,
                "user": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "call": call,
            "participant": participant,
            "presence": presence,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "call_leave_failed",
                "message": "Could not leave call.",
            },
        ) from exc


def _legacy_decline_call_unregistered(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = get_call_session(conn, call_session_id)
            require_business_or_enterprise_organization(
                conn,
                call["organization_id"],
                current_user,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_participants
                    SET status = 'declined',
                        updated_at = NOW()
                    WHERE call_session_id = %s
                      AND user_id = %s
                    RETURNING id, call_session_id, organization_id, user_id,
                              status, invited_at, joined_at, left_at,
                              created_at, updated_at
                    """,
                    (call_session_id, current_user.user_id),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "call_participant_not_found",
                            "message": "You are not invited to this call.",
                        },
                    )

        participant = row_to_call_participant(row)

        if call.get("conversation_id"):
            with get_db() as realtime_conn:
                conversation_member_rows = fetch_conversation_members(
                    realtime_conn,
                    call["conversation_id"],
                )
            member_ids = [
                member["user_id"]
                for member in conversation_member_rows
                if member.get("status") == "active"
            ]
        else:
            member_ids = [current_user.user_id]

        dispatch_realtime_event(
            organization_id=call["organization_id"],
            user_ids=member_ids,
            event={
                "type": "call.declined",
                "call": call,
                "participant": participant,
                "user": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "call": call,
            "participant": participant,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "call_decline_failed",
                "message": "Could not decline call.",
            },
        ) from exc


@router.get("/organizations/{organization_id}/message-notifications")
def list_message_notifications(
    organization_id: int = Path(..., ge=1),
    after_message_id: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=20),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Return recent incoming text messages for the current user.

    This is intentionally a single lightweight polling endpoint for the global
    notification toast. It returns only messages sent by other users in
    conversations where the current user is an active conversation member.
    """

    try:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(MAX(cm.id), 0)
                    FROM conversation_messages cm
                    JOIN organization_conversations oc
                      ON oc.id = cm.conversation_id
                     AND oc.organization_id = cm.organization_id
                     AND oc.status = 'active'
                    JOIN conversation_members receiver_member
                      ON receiver_member.conversation_id = cm.conversation_id
                     AND receiver_member.user_id = %s
                     AND receiver_member.status = 'active'
                    WHERE cm.organization_id = %s
                      AND cm.sender_user_id <> %s
                      AND cm.message_type = 'text'
                      AND cm.deleted_at IS NULL
                    """,
                    (current_user.user_id, organization_id, current_user.user_id),
                )
                latest_row = cur.fetchone()
                latest_message_id = int(latest_row[0] if latest_row else 0)

                cur.execute(
                    """
                    SELECT
                        cm.id,
                        cm.conversation_id,
                        cm.organization_id,
                        cm.sender_user_id,
                        cm.body,
                        cm.created_at,
                        oc.type AS conversation_type,
                        oc.name AS conversation_name,
                        om.member_name AS sender_name,
                        om.member_email AS sender_email
                    FROM conversation_messages cm
                    JOIN organization_conversations oc
                      ON oc.id = cm.conversation_id
                     AND oc.organization_id = cm.organization_id
                     AND oc.status = 'active'
                    JOIN conversation_members receiver_member
                      ON receiver_member.conversation_id = cm.conversation_id
                     AND receiver_member.user_id = %s
                     AND receiver_member.status = 'active'
                    LEFT JOIN organization_members om
                      ON om.organization_id = cm.organization_id
                     AND om.user_id = cm.sender_user_id
                    WHERE cm.organization_id = %s
                      AND cm.sender_user_id <> %s
                      AND cm.message_type = 'text'
                      AND cm.deleted_at IS NULL
                      AND cm.id > %s
                    ORDER BY cm.id DESC
                    LIMIT %s
                    """,
                    (
                        current_user.user_id,
                        organization_id,
                        current_user.user_id,
                        after_message_id,
                        limit,
                    ),
                )
                rows = cur.fetchall()

        notifications = [
            {
                "id": f"message:{row[0]}",
                "message_id": row[0],
                "conversation_id": row[1],
                "organization_id": row[2],
                "sender_user_id": row[3],
                "body": row[4],
                "created_at": row[5],
                "conversation_type": row[6],
                "conversation_name": row[7],
                "sender_name": row[8],
                "sender_email": row[9],
                "target_url": f"/team/messages?conversationId={row[1]}&messageId={row[0]}",
            }
            for row in rows
        ]
        notifications.reverse()

        return {
            "success": True,
            "latest_message_id": latest_message_id,
            "notifications": notifications,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "message_notifications_load_failed",
                "message": "Could not load message notifications.",
            },
        ) from exc


@router.get("/organizations/{organization_id}/presence")
async def list_presence(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Derive presence from Redis connection leases and verified LiveKit state."""

    try:
        def _load_presence_inputs() -> list[dict[str, Any]]:
            with get_db() as conn:
                require_business_or_enterprise_organization(
                    conn, organization_id, current_user
                )
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            om.user_id,
                            om.role,
                            om.created_at,
                            mp.last_seen_at,
                            mp.updated_at,
                            EXISTS (
                                SELECT 1
                                FROM call_participants cp
                                JOIN call_sessions cs
                                  ON cs.id = cp.call_session_id
                                WHERE cp.organization_id = om.organization_id
                                  AND cp.user_id = om.user_id
                                  AND cp.provider_joined_at IS NOT NULL
                                  AND (
                                      cp.provider_left_at IS NULL
                                      OR cp.provider_left_at < cp.provider_joined_at
                                  )
                                  AND (
                                      cs.provider_finished_at IS NULL
                                      OR cs.provider_finished_at < cp.provider_joined_at
                                  )
                            ) AS in_verified_call
                        FROM organization_members om
                        LEFT JOIN member_presence mp
                          ON mp.organization_id = om.organization_id
                         AND mp.user_id = om.user_id
                        WHERE om.organization_id = %s
                          AND om.status = 'active'
                        ORDER BY om.created_at ASC, om.id ASC
                        """,
                        (organization_id,),
                    )
                    return [
                        {
                            "user_id": str(row[0]),
                            "organization_role": row[1],
                            "created_at": row[2],
                            "last_seen_at": row[3],
                            "updated_at": row[4],
                            "in_verified_call": bool(row[5]),
                        }
                        for row in cur.fetchall()
                    ]

        members = await anyio.to_thread.run_sync(_load_presence_inputs)
        user_ids = [member["user_id"] for member in members]
        counts = await active_organization_connection_counts(
            organization_id=organization_id,
            user_ids=user_ids,
        )

        presence = []
        for member in members:
            connection_count = int(counts.get(member["user_id"], 0))
            status = (
                "in_call"
                if member["in_verified_call"]
                else ("online" if connection_count > 0 else "offline")
            )
            presence.append(
                {
                    "organization_id": organization_id,
                    "user_id": member["user_id"],
                    "status": status,
                    "connection_count": connection_count,
                    "last_seen_at": member["last_seen_at"],
                    "updated_at": member["updated_at"],
                    "organization_role": member["organization_role"],
                    "source": "livekit" if status == "in_call" else "redis",
                }
            )

        created_at_by_user = {
            member["user_id"]: member["created_at"] for member in members
        }
        presence.sort(
            key=lambda item: (
                {"in_call": 1, "online": 2, "offline": 3}[item["status"]],
                created_at_by_user.get(
                    item["user_id"],
                    datetime.max.replace(tzinfo=timezone.utc),
                ),
            )
        )
        return {"success": True, "presence": presence}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "presence_load_failed",
                "message": "Could not load member presence.",
            },
        ) from exc


@router.post("/organizations/{organization_id}/presence")
async def update_presence(
    payload: UpdatePresenceRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Compatibility endpoint: records last-seen but never sets authoritative status."""

    if payload.status == "in_call":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "presence_provider_controlled",
                "message": "In-call presence is controlled exclusively by verified LiveKit events.",
            },
        )

    def _touch() -> None:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn, organization_id, current_user
            )
            touch_presence(conn, organization_id, current_user.user_id)

    await anyio.to_thread.run_sync(_touch)
    counts = await active_organization_connection_counts(
        organization_id=organization_id,
        user_ids=[current_user.user_id],
    )
    online = bool(counts and counts.get(current_user.user_id, 0) > 0)
    presence = await anyio.to_thread.run_sync(
        lambda: effective_presence_sync(
            organization_id=organization_id,
            user_id=current_user.user_id,
            online=online,
        )
    )
    await publish_organization_realtime_event(
        organization_id=organization_id,
        event={
            "type": "presence.updated",
            "organization_id": organization_id,
            "presence": presence,
            "user": user_public_payload(current_user),
        },
    )
    return {"success": True, "presence": presence}


# Import after communication helpers are defined to avoid the lifecycle module's
# deliberate back-reference to this module during startup.
from backend.team_call_lifecycle import router as team_call_lifecycle_router

router.include_router(team_call_lifecycle_router)

__all__ = [
    "router",
    "start_team_realtime_services",
    "stop_team_realtime_services",
    "account_realtime",
    "organization_realtime",
]
