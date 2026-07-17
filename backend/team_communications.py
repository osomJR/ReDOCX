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

from datetime import datetime, timedelta
import asyncio
import hashlib
import mimetypes
import os
from pathlib import Path as FileSystemPath
from typing import Any, Literal
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator, model_validator
from psycopg import errors as psycopg_errors
from psycopg.types.json import Jsonb

from backend.auth0_dependencies import AuthenticatedUser, authenticate_access_token, get_current_user
from backend.database import get_db


router = APIRouter(tags=["team_communications"])

LIVEKIT_API_KEY_ENV = "LIVEKIT_API_KEY"
LIVEKIT_API_SECRET_ENV = "LIVEKIT_API_SECRET"
LIVEKIT_URL_ENV = "LIVEKIT_URL"
LIVEKIT_TOKEN_TTL_MINUTES_ENV = "LIVEKIT_TOKEN_TTL_MINUTES"
DEFAULT_LIVEKIT_TOKEN_TTL_MINUTES = 120

TEAM_ATTACHMENT_STORAGE_DIR_ENV = "TEAM_ATTACHMENT_STORAGE_DIR"
TEAM_ATTACHMENT_MAX_BYTES_ENV = "TEAM_ATTACHMENT_MAX_BYTES"
DEFAULT_TEAM_ATTACHMENT_STORAGE_DIR = "storage/team_attachments"
DEFAULT_TEAM_ATTACHMENT_MAX_BYTES = 50 * 1024 * 1024

BLOCKED_ATTACHMENT_EXTENSIONS = {
    ".ade", ".adp", ".apk", ".app", ".bat", ".bin", ".cmd",
    ".com", ".cpl", ".dll", ".dmg", ".exe", ".gadget", ".hta",
    ".ins", ".iso", ".jar", ".js", ".jse", ".lib", ".lnk",
    ".mde", ".msc", ".msi", ".msp", ".mst", ".nsh", ".pif",
    ".ps1", ".scr", ".sh", ".sys", ".vb", ".vbe", ".vbs",
    ".ws", ".wsc", ".wsf", ".wsh",
}

DOCUMENT_ATTACHMENT_EXTENSIONS = {
    ".csv", ".doc", ".docx", ".json", ".md", ".odt", ".pdf",
    ".ppt", ".pptx", ".rtf", ".txt", ".xls", ".xlsx", ".xml",
}

ConversationType = Literal["dm", "group"]
ConversationStatus = Literal["active", "archived"]
ConversationRole = Literal["owner", "admin", "member"]
PresenceStatus = Literal["online", "offline", "in_call"]


class RealtimeConnectionManager:
    """In-memory WebSocket registry for realtime events.

    Organization connections are used for team messages/calls/presence.
    Account connections are used for user-specific events that must work even
    before a user becomes an active organization member, such as team invites.

    This works for a single FastAPI process. For multi-process or multi-server
    production deployments, replace the in-memory fanout with Redis Pub/Sub,
    Postgres LISTEN/NOTIFY, or another shared broker.
    """

    def __init__(self) -> None:
        self._connections: dict[int, dict[str, set[WebSocket]]] = {}
        self._account_connections_by_user_id: dict[str, set[WebSocket]] = {}
        self._account_connections_by_email: dict[str, set[WebSocket]] = {}
        self._account_connection_index: dict[WebSocket, tuple[str, str | None]] = {}
        self._lock = anyio.Lock()

    async def connect(self, organization_id: int, user_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            organization_connections = self._connections.setdefault(organization_id, {})
            user_connections = organization_connections.setdefault(user_id, set())
            user_connections.add(websocket)

    async def disconnect(self, organization_id: int, user_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            organization_connections = self._connections.get(organization_id)
            if not organization_connections:
                return

            user_connections = organization_connections.get(user_id)
            if user_connections:
                user_connections.discard(websocket)
                if not user_connections:
                    organization_connections.pop(user_id, None)

            if not organization_connections:
                self._connections.pop(organization_id, None)

    async def connect_account(
        self,
        *,
        user_id: str,
        email: str | None,
        websocket: WebSocket,
    ) -> None:
        normalized_email = normalize_realtime_email(email)

        async with self._lock:
            self._account_connections_by_user_id.setdefault(user_id, set()).add(websocket)
            if normalized_email:
                self._account_connections_by_email.setdefault(normalized_email, set()).add(websocket)
            self._account_connection_index[websocket] = (user_id, normalized_email)

    async def disconnect_account(self, websocket: WebSocket) -> None:
        async with self._lock:
            indexed = self._account_connection_index.pop(websocket, None)
            if not indexed:
                return

            user_id, email = indexed

            user_connections = self._account_connections_by_user_id.get(user_id)
            if user_connections:
                user_connections.discard(websocket)
                if not user_connections:
                    self._account_connections_by_user_id.pop(user_id, None)

            if email:
                email_connections = self._account_connections_by_email.get(email)
                if email_connections:
                    email_connections.discard(websocket)
                    if not email_connections:
                        self._account_connections_by_email.pop(email, None)

    async def has_user_connections(self, organization_id: int, user_id: str) -> bool:
        async with self._lock:
            return bool(
                self._connections
                .get(organization_id, {})
                .get(user_id, set())
            )

    async def broadcast_to_users(
        self,
        organization_id: int,
        user_ids: list[str] | set[str],
        event: dict[str, Any],
        *,
        exclude_user_ids: set[str] | None = None,
    ) -> None:
        exclude_user_ids = exclude_user_ids or set()
        normalized_event = normalize_realtime_payload(event)

        async with self._lock:
            organization_connections = self._connections.get(organization_id, {})
            targets: list[tuple[str, WebSocket]] = []

            for user_id in user_ids:
                if user_id in exclude_user_ids:
                    continue
                for websocket in organization_connections.get(user_id, set()):
                    targets.append((user_id, websocket))

        stale: list[tuple[str, WebSocket]] = []
        for user_id, websocket in targets:
            try:
                await websocket.send_json(normalized_event)
            except Exception:
                stale.append((user_id, websocket))

        if stale:
            async with self._lock:
                organization_connections = self._connections.get(organization_id, {})
                for user_id, websocket in stale:
                    user_connections = organization_connections.get(user_id)
                    if user_connections:
                        user_connections.discard(websocket)
                        if not user_connections:
                            organization_connections.pop(user_id, None)

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
            organization_id,
            user_ids,
            event,
            exclude_user_ids=exclude_user_ids,
        )

    async def broadcast_account_email(
        self,
        email: str,
        event: dict[str, Any],
    ) -> None:
        normalized_email = normalize_realtime_email(email)
        if not normalized_email:
            return

        normalized_event = normalize_realtime_payload(event)

        async with self._lock:
            targets = list(self._account_connections_by_email.get(normalized_email, set()))

        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(normalized_event)
            except Exception:
                stale.append(websocket)

        if stale:
            for websocket in stale:
                await self.disconnect_account(websocket)

    async def broadcast_account_user(
        self,
        user_id: str,
        event: dict[str, Any],
    ) -> None:
        normalized_user_id = normalize_user_id(user_id)
        normalized_event = normalize_realtime_payload(event)

        async with self._lock:
            targets = list(self._account_connections_by_user_id.get(normalized_user_id, set()))

        stale: list[WebSocket] = []
        for websocket in targets:
            try:
                await websocket.send_json(normalized_event)
            except Exception:
                stale.append(websocket)

        if stale:
            for websocket in stale:
                await self.disconnect_account(websocket)


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


def dispatch_realtime_event(
    *,
    organization_id: int,
    user_ids: list[str] | set[str],
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> None:
    """Send a realtime event from sync route handlers without blocking them."""

    payload = {
        **event,
        "organization_id": organization_id,
    }

    async def _broadcast() -> None:
        await TEAM_REALTIME_MANAGER.broadcast_to_users(
            organization_id,
            list(user_ids),
            payload,
            exclude_user_ids=exclude_user_ids or set(),
        )

    try:
        anyio.from_thread.run(_broadcast)
    except RuntimeError:
        # Best-effort fallback for callers that are not running inside AnyIO's
        # worker-thread context. The HTTP response should not fail merely
        # because realtime fanout could not be scheduled.
        pass


def dispatch_organization_realtime_event(
    *,
    organization_id: int,
    event: dict[str, Any],
    exclude_user_ids: set[str] | None = None,
) -> None:
    payload = {
        **event,
        "organization_id": organization_id,
    }

    async def _broadcast() -> None:
        await TEAM_REALTIME_MANAGER.broadcast_organization(
            organization_id,
            payload,
            exclude_user_ids=exclude_user_ids or set(),
        )

    try:
        anyio.from_thread.run(_broadcast)
    except RuntimeError:
        pass


def dispatch_account_realtime_event_by_email(
    *,
    email: str,
    event: dict[str, Any],
) -> None:
    """Send a user-scoped realtime event to a signed-in user by email.

    Used for team invitations because pending invitees are not yet active
    organization members and therefore cannot connect to the org-scoped socket.
    """

    normalized_email = normalize_realtime_email(email)
    if not normalized_email:
        return

    payload = {
        **event,
        "recipient_email": normalized_email,
    }

    async def _broadcast() -> None:
        await TEAM_REALTIME_MANAGER.broadcast_account_email(
            normalized_email,
            payload,
        )

    try:
        anyio.from_thread.run(_broadcast)
    except RuntimeError:
        # Best-effort fallback. The invite is already persisted; realtime
        # delivery should not make the invite API fail.
        pass


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
        if self.type == "group":
            if not self.name:
                raise ValueError("Group conversations require a name.")
            if len(self.member_user_ids) < 1:
                raise ValueError("Group conversations require at least one member.")
        return self


class SendMessageRequest(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("Message body is required.")
        if len(normalized) > 5000:
            raise ValueError("Message body cannot exceed 5000 characters.")
        return normalized


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

    ttl_minutes = max(5, min(ttl_minutes, 12 * 60))

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

    token = (
        livekit_api.AccessToken(
            str(config["api_key"]),
            str(config["api_secret"]),
        )
        .with_identity(participant_identity)
        .with_name(participant_name)
        .with_ttl(ttl)
        .with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .to_jwt()
    )

    return {
        "server_url": config["server_url"],
        "room_name": room_name,
        "token": token,
        "token_status": "configured",
        "participant_identity": participant_identity,
        "participant_name": participant_name,
        "expires_in_seconds": int(ttl.total_seconds()),
    }


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
    return {
        "id": row[0],
        "conversation_id": row[1],
        "organization_id": row[2],
        "sender_user_id": row[3],
        "message_type": row[4],
        "body": row[5],
        "metadata": row[6],
        "edited_at": row[7],
        "deleted_at": row[8],
        "created_at": row[9],
        "updated_at": row[10],
    }


def row_to_attachment(row) -> dict[str, Any]:
    attachment = {
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
        "created_at": row[12],
    }
    attachment["download_url"] = build_attachment_download_url(
        conversation_id=attachment["conversation_id"],
        message_id=attachment["message_id"],
        attachment_id=attachment["id"],
    )
    return attachment


def get_team_attachment_storage_root() -> FileSystemPath:
    configured = os.getenv(
        TEAM_ATTACHMENT_STORAGE_DIR_ENV,
        DEFAULT_TEAM_ATTACHMENT_STORAGE_DIR,
    ).strip()
    root = FileSystemPath(configured or DEFAULT_TEAM_ATTACHMENT_STORAGE_DIR)

    try:
        root.mkdir(parents=True, exist_ok=True)
        resolved_root = root.resolve()
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_storage_unavailable",
                "message": (
                    "Attachment storage is not writable. Configure "
                    "TEAM_ATTACHMENT_STORAGE_DIR with a persistent writable path."
                ),
            },
        ) from exc

    return resolved_root


def get_team_attachment_max_bytes() -> int:
    raw = os.getenv(
        TEAM_ATTACHMENT_MAX_BYTES_ENV,
        str(DEFAULT_TEAM_ATTACHMENT_MAX_BYTES),
    ).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_TEAM_ATTACHMENT_MAX_BYTES
    return max(1 * 1024 * 1024, min(value, 250 * 1024 * 1024))


def normalize_attachment_filename(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not raw:
        raw = "attachment"

    cleaned = "".join(
        character if character.isalnum() or character in {" ", ".", "-", "_"} else "_"
        for character in raw
    ).strip(" .")

    return cleaned[:180] or "attachment"


def extension_for_filename(filename: str) -> str:
    extension = FileSystemPath(filename).suffix.lower()
    if len(extension) > 16:
        return ""
    return extension


def classify_attachment_kind(content_type: str | None, filename: str) -> str:
    normalized_type = (content_type or "").strip().lower()
    extension = extension_for_filename(filename)

    if normalized_type.startswith("image/"):
        return "image"
    if normalized_type.startswith("audio/"):
        return "audio"
    if normalized_type.startswith("video/"):
        return "video"
    if extension in DOCUMENT_ATTACHMENT_EXTENSIONS:
        return "document"
    return "file"


def validate_attachment_upload(filename: str, content_type: str | None) -> str:
    extension = extension_for_filename(filename)
    if extension in BLOCKED_ATTACHMENT_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "blocked_attachment_type",
                "message": "This file type is not allowed for team messaging.",
            },
        )

    return classify_attachment_kind(content_type, filename)


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


def attachment_file_path(storage_key: str) -> FileSystemPath:
    root = get_team_attachment_storage_root()
    candidate = (root / storage_key).resolve()

    if root not in candidate.parents and candidate != root:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_attachment_path",
                "message": "Attachment storage path is invalid.",
            },
        )

    return candidate


def save_team_attachment_file(
    *,
    upload: UploadFile,
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
) -> dict[str, Any]:
    original_filename = normalize_attachment_filename(upload.filename)
    guessed_content_type = mimetypes.guess_type(original_filename)[0]
    content_type = (upload.content_type or guessed_content_type or "application/octet-stream").strip()
    kind = validate_attachment_upload(original_filename, content_type)
    extension = extension_for_filename(original_filename)
    stored_filename = f"{uuid4().hex}{extension}"
    storage_key = f"org-{organization_id}/conversation-{conversation_id}/{stored_filename}"
    destination = attachment_file_path(storage_key)

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_storage_unavailable",
                "message": "Attachment storage is temporarily unavailable.",
            },
        ) from exc

    max_bytes = get_team_attachment_max_bytes()
    total_bytes = 0
    digest = hashlib.sha256()

    try:
        with destination.open("wb") as output_file:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail={
                            "error": "attachment_too_large",
                            "message": (
                                "Attachment is too large. The maximum allowed size is "
                                f"{max_bytes // (1024 * 1024)} MB."
                            ),
                        },
                    )

                digest.update(chunk)
                output_file.write(chunk)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_storage_unavailable",
                "message": "Could not write the attachment to persistent storage.",
            },
        ) from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total_bytes <= 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "empty_attachment",
                "message": "Attachment file is empty.",
            },
        )

    return {
        "kind": kind,
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "storage_key": storage_key,
        "content_type": content_type,
        "file_size_bytes": total_bytes,
        "checksum_sha256": digest.hexdigest(),
        "uploaded_by_user_id": uploaded_by_user_id,
    }


def fetch_message_attachments(conn, message_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, message_id, conversation_id, organization_id,
                   uploaded_by_user_id, kind, original_filename, stored_filename,
                   storage_key, content_type, file_size_bytes, checksum_sha256,
                   created_at
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


def get_call_session(conn, call_session_id: int) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, conversation_id, type, status,
                   created_by_user_id, livekit_room_name, started_at, ended_at,
                   created_at, updated_at
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
    """Receive a realtime token without exposing it in the WebSocket URL.

    The query parameter remains as a temporary compatibility path for older
    clients and can be removed after they have all been upgraded.
    """

    query_token = str(websocket.query_params.get("token") or "").strip()
    if query_token:
        return query_token

    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except TimeoutError:
        return ""
    except WebSocketDisconnect:
        raise
    except Exception:
        return ""

    if not isinstance(message, dict):
        return ""

    message_type = str(message.get("type") or "").strip().lower()
    if message_type not in {"auth", "authenticate"}:
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

    # Keep this bounded because the client controls it and it is echoed in events.
    return raw[:160]


def build_pending_realtime_message(
    *,
    organization_id: int,
    conversation_id: int,
    sender_user_id: str,
    body: str,
    client_message_id: str,
) -> dict[str, Any]:
    now = datetime.utcnow()

    return {
        "id": client_message_id,
        "conversation_id": conversation_id,
        "organization_id": organization_id,
        "sender_user_id": sender_user_id,
        "message_type": "text",
        "body": body,
        "metadata": {
            "client_message_id": client_message_id,
            "transport": "websocket",
            "pending": True,
        },
        "edited_at": None,
        "deleted_at": None,
        "created_at": now,
        "updated_at": now,
        "pending": True,
        "client_message_id": client_message_id,
    }


def prepare_realtime_message_send_sync(
    *,
    organization_id: int,
    current_user: AuthenticatedUser,
    event: dict[str, Any],
) -> dict[str, Any]:
    conversation_id = parse_realtime_positive_int(
        event.get("conversation_id") or event.get("conversationId"),
        "conversation_id",
    )
    client_message_id = normalize_client_message_id(
        event.get("client_message_id") or event.get("clientMessageId")
    )
    payload = SendMessageRequest(body=event.get("body", ""))

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

        conversation_payload = add_members_to_conversation_payload(conn, conversation)
        conversation_member_rows = fetch_conversation_members(conn, conversation_id)

    member_ids = [
        member["user_id"]
        for member in conversation_member_rows
        if member.get("status") == "active"
    ]

    pending_message = build_pending_realtime_message(
        organization_id=organization_id,
        conversation_id=conversation_id,
        sender_user_id=current_user.user_id,
        body=payload.body,
        client_message_id=client_message_id,
    )

    return {
        "conversation_id": conversation_id,
        "client_message_id": client_message_id,
        "body": payload.body,
        "conversation": conversation_payload,
        "member_ids": member_ids,
        "pending_message": pending_message,
    }


def persist_realtime_message_sync(
    *,
    organization_id: int,
    conversation_id: int,
    current_user: AuthenticatedUser,
    body: str,
) -> dict[str, Any]:
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

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_messages (
                    conversation_id,
                    organization_id,
                    sender_user_id,
                    message_type,
                    body
                )
                VALUES (%s, %s, %s, 'text', %s)
                RETURNING id, conversation_id, organization_id,
                          sender_user_id, message_type, body, metadata,
                          edited_at, deleted_at, created_at, updated_at
                """,
                (
                    conversation_id,
                    organization_id,
                    current_user.user_id,
                    body,
                ),
            )
            row = cur.fetchone()

            cur.execute(
                """
                UPDATE organization_conversations
                SET last_message_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                """,
                (conversation_id,),
            )

        message = row_to_message(row)
        conversation_payload = add_members_to_conversation_payload(conn, conversation)
        conversation_member_rows = fetch_conversation_members(conn, conversation_id)

    member_ids = [
        member["user_id"]
        for member in conversation_member_rows
        if member.get("status") == "active"
    ]

    return {
        "message": message,
        "conversation": conversation_payload,
        "member_ids": member_ids,
    }


async def persist_realtime_message_and_ack(
    *,
    organization_id: int,
    conversation_id: int,
    current_user: AuthenticatedUser,
    client_message_id: str,
    body: str,
) -> None:
    try:
        saved = await anyio.to_thread.run_sync(
            lambda: persist_realtime_message_sync(
                organization_id=organization_id,
                conversation_id=conversation_id,
                current_user=current_user,
                body=body,
            )
        )

        sender_payload = user_public_payload(current_user)
        saved_event = {
            "type": "message.persisted",
            "organization_id": organization_id,
            "client_message_id": client_message_id,
            "message": {
                **saved["message"],
                "client_message_id": client_message_id,
            },
            "conversation": saved["conversation"],
            "sender": sender_payload,
        }

        # Broadcast persistence reconciliation to all active conversation members
        # so receivers can replace the temporary client ID with the durable DB ID.
        await TEAM_REALTIME_MANAGER.broadcast_to_users(
            organization_id,
            saved["member_ids"],
            saved_event,
        )

        await TEAM_REALTIME_MANAGER.broadcast_to_users(
            organization_id,
            [current_user.user_id],
            {
                "type": "message.ack",
                "organization_id": organization_id,
                "client_message_id": client_message_id,
                "message": {
                    **saved["message"],
                    "client_message_id": client_message_id,
                },
                "conversation": saved["conversation"],
            },
        )

    except Exception as exc:
        await TEAM_REALTIME_MANAGER.broadcast_to_users(
            organization_id,
            [current_user.user_id],
            {
                "type": "message.failed",
                "organization_id": organization_id,
                "client_message_id": client_message_id,
                "conversation_id": conversation_id,
                "error": realtime_error_code(exc),
                "message": realtime_error_message(exc),
            },
        )



@router.websocket("/account/realtime")
async def account_realtime(websocket: WebSocket):
    """User-scoped realtime channel for dashboard/account events.

    This intentionally does not require Business/Enterprise entitlement because
    invitees are often Free/Personal users until they accept a team invitation.
    """

    current_user: AuthenticatedUser | None = None
    connected = False

    await websocket.accept()

    try:
        token = await receive_websocket_auth_token(websocket)
        try:
            current_user = authenticate_websocket_user(token)
        except HTTPException as exc:
            await websocket.send_json(websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
            return

        email = current_user.claims.get("email")

        await TEAM_REALTIME_MANAGER.connect_account(
            user_id=current_user.user_id,
            email=email if isinstance(email, str) else None,
            websocket=websocket,
        )
        connected = True

        await websocket.send_json(
            normalize_realtime_payload(
                {
                    "type": "account.realtime.connected",
                    "user": user_public_payload(current_user),
                }
            )
        )

        while True:
            event = await websocket.receive_json()
            event_type = event.get("type") if isinstance(event, dict) else None

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            await websocket.send_json(
                {
                    "type": "error",
                    "error": "unsupported_account_realtime_event",
                    "message": "Unsupported account realtime event type.",
                }
            )

    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await websocket.send_json(websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
        except RuntimeError:
            pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="Account realtime connection failed.")
        except RuntimeError:
            pass
    finally:
        if current_user is not None and connected:
            await TEAM_REALTIME_MANAGER.disconnect_account(websocket)


@router.websocket("/organizations/{organization_id}/realtime")
async def organization_realtime(
    websocket: WebSocket,
    organization_id: int,
):
    current_user: AuthenticatedUser | None = None
    connected = False

    await websocket.accept()

    try:
        token = await receive_websocket_auth_token(websocket)
        try:
            current_user = authenticate_websocket_user(token)
        except HTTPException as exc:
            await websocket.send_json(websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
            return

        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            presence = upsert_presence(
                conn,
                organization_id,
                current_user.user_id,
                "online",
            )

        await TEAM_REALTIME_MANAGER.connect(
            organization_id,
            current_user.user_id,
            websocket,
        )
        connected = True

        await websocket.send_json(
            normalize_realtime_payload(
                {
                    "type": "realtime.connected",
                    "organization_id": organization_id,
                    "user": user_public_payload(current_user),
                }
            )
        )

        await TEAM_REALTIME_MANAGER.broadcast_organization(
            organization_id,
            {
                "type": "presence.updated",
                "organization_id": organization_id,
                "presence": presence,
                "user": user_public_payload(current_user),
            },
        )

        while True:
            event = await websocket.receive_json()
            event_type = event.get("type") if isinstance(event, dict) else None

            if event_type == "ping":
                await websocket.send_json(
                    {
                        "type": "pong",
                        "organization_id": organization_id,
                    }
                )
                continue

            if event_type == "presence.update":
                requested_status = event.get("status", "online")
                payload = UpdatePresenceRequest(status=requested_status)

                with get_db() as conn:
                    require_business_or_enterprise_organization(
                        conn,
                        organization_id,
                        current_user,
                    )
                    presence = upsert_presence(
                        conn,
                        organization_id,
                        current_user.user_id,
                        payload.status,
                    )

                await TEAM_REALTIME_MANAGER.broadcast_organization(
                    organization_id,
                    {
                        "type": "presence.updated",
                        "organization_id": organization_id,
                        "presence": presence,
                        "user": user_public_payload(current_user),
                    },
                )
                continue

            if event_type == "message.send":
                client_message_id = normalize_client_message_id(
                    event.get("client_message_id") or event.get("clientMessageId")
                    if isinstance(event, dict)
                    else None
                )

                try:
                    prepared = await anyio.to_thread.run_sync(
                        lambda: prepare_realtime_message_send_sync(
                            organization_id=organization_id,
                            current_user=current_user,
                            event=event,
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

                sender_payload = user_public_payload(current_user)

                await TEAM_REALTIME_MANAGER.broadcast_to_users(
                    organization_id,
                    prepared["member_ids"],
                    {
                        "type": "message.created",
                        "organization_id": organization_id,
                        "client_message_id": prepared["client_message_id"],
                        "message": prepared["pending_message"],
                        "conversation": prepared["conversation"],
                        "sender": sender_payload,
                        "delivery": "optimistic",
                    },
                )

                asyncio.create_task(
                    persist_realtime_message_and_ack(
                        organization_id=organization_id,
                        conversation_id=prepared["conversation_id"],
                        current_user=current_user,
                        client_message_id=prepared["client_message_id"],
                        body=prepared["body"],
                    )
                )
                continue

            await websocket.send_json(
                {
                    "type": "error",
                    "organization_id": organization_id,
                    "error": "unsupported_realtime_event",
                    "message": "Unsupported realtime event type.",
                }
            )

    except WebSocketDisconnect:
        pass
    except HTTPException as exc:
        try:
            await websocket.send_json(websocket_auth_failure_payload(exc))
            await websocket.close(code=1008)
        except RuntimeError:
            pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="Realtime connection failed.")
        except RuntimeError:
            pass
    finally:
        if current_user is not None and connected:
            await TEAM_REALTIME_MANAGER.disconnect(
                organization_id,
                current_user.user_id,
                websocket,
            )

            if await TEAM_REALTIME_MANAGER.has_user_connections(
                organization_id,
                current_user.user_id,
            ):
                return

            try:
                with get_db() as conn:
                    presence = upsert_presence(
                        conn,
                        organization_id,
                        current_user.user_id,
                        "offline",
                    )

                await TEAM_REALTIME_MANAGER.broadcast_organization(
                    organization_id,
                    {
                        "type": "presence.updated",
                        "organization_id": organization_id,
                        "presence": presence,
                        "user": user_public_payload(current_user),
                    },
                )
            except Exception:
                pass


@router.get("/organizations/{organization_id}/conversations")
def list_conversations(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )

            # The team group chat is organization-wide. If it was created before
            # a member joined, this keeps that member attached to the shared
            # group the next time conversations are loaded.
            sync_organization_group_conversations(conn, organization_id)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT oc.id, oc.organization_id, oc.type, oc.name,
                           oc.created_by_user_id, oc.status, oc.last_message_at,
                           oc.created_at, oc.updated_at
                    FROM organization_conversations oc
                    JOIN conversation_members cm
                      ON cm.conversation_id = oc.id
                     AND cm.user_id = %s
                     AND cm.status = 'active'
                    WHERE oc.organization_id = %s
                      AND oc.status = 'active'
                    ORDER BY COALESCE(oc.last_message_at, oc.updated_at) DESC,
                             oc.id DESC
                    """,
                    (current_user.user_id, organization_id),
                )
                rows = cur.fetchall()

            conversations = [
                add_members_to_conversation_payload(
                    conn,
                    row_to_conversation(row),
                )
                for row in rows
            ]

        return {
            "success": True,
            "conversations": conversations,
        }

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
                "conversation": conversation_payload,
                "members": conversation_member_rows,
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


@router.get("/conversations/{conversation_id}/messages")
def list_messages(
    conversation_id: int = Path(..., ge=1),
    limit: int = Query(50, ge=1, le=100),
    before_message_id: int | None = Query(None, ge=1),
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

            with conn.cursor() as cur:
                if before_message_id is not None:
                    cur.execute(
                        """
                        SELECT id, conversation_id, organization_id,
                               sender_user_id, message_type, body, metadata,
                               edited_at, deleted_at, created_at, updated_at
                        FROM conversation_messages
                        WHERE conversation_id = %s
                          AND deleted_at IS NULL
                          AND id < %s
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        (conversation_id, before_message_id, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, conversation_id, organization_id,
                               sender_user_id, message_type, body, metadata,
                               edited_at, deleted_at, created_at, updated_at
                        FROM conversation_messages
                        WHERE conversation_id = %s
                          AND deleted_at IS NULL
                        ORDER BY id DESC
                        LIMIT %s
                        """,
                        (conversation_id, limit),
                    )

                rows = cur.fetchall()

            messages = [
                add_attachments_to_message(conn, row_to_message(row))
                for row in rows
            ]

        messages.reverse()

        return {
            "success": True,
            "conversation": conversation,
            "messages": messages,
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


@router.post("/conversations/{conversation_id}/attachments")
def send_attachment_message(
    conversation_id: int = Path(..., ge=1),
    file: UploadFile = File(...),
    caption: str = Form(""),
    client_message_id: str | None = Form(None),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    saved_file: dict[str, Any] | None = None

    try:
        normalized_caption = (caption or "").strip()
        client_id = normalize_client_message_id(client_message_id)

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

            saved_file = save_team_attachment_file(
                upload=file,
                organization_id=conversation["organization_id"],
                conversation_id=conversation_id,
                uploaded_by_user_id=current_user.user_id,
            )

            message_body = normalized_caption or saved_file["original_filename"]
            preliminary_metadata = {
                "client_message_id": client_id,
                "transport": "http_upload",
                "attachment_count": 1,
                "attachments": [
                    {
                        key: value
                        for key, value in saved_file.items()
                        if key != "storage_key"
                    }
                ],
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
                        metadata
                    )
                    VALUES (%s, %s, %s, 'attachment', %s, %s)
                    RETURNING id, conversation_id, organization_id,
                              sender_user_id, message_type, body, metadata,
                              edited_at, deleted_at, created_at, updated_at
                    """,
                    (
                        conversation_id,
                        conversation["organization_id"],
                        current_user.user_id,
                        message_body,
                        Jsonb(preliminary_metadata),
                    ),
                )
                message_row = cur.fetchone()
                message = row_to_message(message_row)

                cur.execute(
                    """
                    INSERT INTO conversation_message_attachments (
                        message_id,
                        conversation_id,
                        organization_id,
                        uploaded_by_user_id,
                        kind,
                        original_filename,
                        stored_filename,
                        storage_key,
                        content_type,
                        file_size_bytes,
                        checksum_sha256
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, message_id, conversation_id, organization_id,
                              uploaded_by_user_id, kind, original_filename,
                              stored_filename, storage_key, content_type,
                              file_size_bytes, checksum_sha256, created_at
                    """,
                    (
                        message["id"],
                        conversation_id,
                        conversation["organization_id"],
                        current_user.user_id,
                        saved_file["kind"],
                        saved_file["original_filename"],
                        saved_file["stored_filename"],
                        saved_file["storage_key"],
                        saved_file["content_type"],
                        saved_file["file_size_bytes"],
                        saved_file["checksum_sha256"],
                    ),
                )
                attachment = row_to_attachment(cur.fetchone())

                final_metadata = {
                    "client_message_id": client_id,
                    "transport": "http_upload",
                    "attachment_count": 1,
                    "attachments": [attachment],
                }
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
                    (Jsonb(final_metadata), message["id"]),
                )
                message = row_to_message(cur.fetchone())

                cur.execute(
                    """
                    UPDATE organization_conversations
                    SET last_message_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (conversation_id,),
                )

            conversation_payload = add_members_to_conversation_payload(conn, conversation)
            conversation_member_rows = fetch_conversation_members(conn, conversation_id)

        member_ids = [
            member["user_id"]
            for member in conversation_member_rows
            if member.get("status") == "active"
        ]
        dispatch_realtime_event(
            organization_id=conversation["organization_id"],
            user_ids=member_ids,
            event={
                "type": "message.created",
                "client_message_id": client_id,
                "message": message,
                "conversation": conversation_payload,
                "sender": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "message": message,
            "conversation": conversation_payload,
        }

    except HTTPException:
        if saved_file:
            attachment_file_path(saved_file["storage_key"]).unlink(missing_ok=True)
        raise
    except ValueError as exc:
        if saved_file:
            attachment_file_path(saved_file["storage_key"]).unlink(missing_ok=True)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_attachment",
                "message": str(exc),
            },
        ) from exc
    except (
        psycopg_errors.UndefinedTable,
        psycopg_errors.UndefinedColumn,
        psycopg_errors.CheckViolation,
    ) as exc:
        if saved_file:
            attachment_file_path(saved_file["storage_key"]).unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail={
                "error": "attachment_schema_not_ready",
                "message": (
                    "Attachment persistence is not ready. Apply migration "
                    "006_create_team_message_attachments.sql and try again."
                ),
            },
        ) from exc
    except Exception as exc:
        if saved_file:
            attachment_file_path(saved_file["storage_key"]).unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "attachment_send_failed",
                "message": "Could not send attachment.",
            },
        ) from exc


@router.get("/conversations/{conversation_id}/messages/{message_id}/attachments/{attachment_id}/download")
def download_conversation_attachment(
    conversation_id: int = Path(..., ge=1),
    message_id: int = Path(..., ge=1),
    attachment_id: int = Path(..., ge=1),
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

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, message_id, conversation_id, organization_id,
                           uploaded_by_user_id, kind, original_filename, stored_filename,
                           storage_key, content_type, file_size_bytes, checksum_sha256,
                           created_at
                    FROM conversation_message_attachments
                    WHERE id = %s
                      AND message_id = %s
                      AND conversation_id = %s
                      AND organization_id = %s
                    """,
                    (
                        attachment_id,
                        message_id,
                        conversation_id,
                        conversation["organization_id"],
                    ),
                )
                row = cur.fetchone()

        if row is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "attachment_not_found",
                    "message": "Attachment was not found.",
                },
            )

        attachment = row_to_attachment(row)
        path = attachment_file_path(attachment["storage_key"])

        if not path.exists() or not path.is_file():
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "attachment_file_missing",
                    "message": "Attachment file is no longer available.",
                },
            )

        return FileResponse(
            path,
            media_type=attachment.get("content_type") or "application/octet-stream",
            filename=attachment.get("original_filename") or "attachment",
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "attachment_download_failed",
                "message": "Could not download attachment.",
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

            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO conversation_messages (
                        conversation_id,
                        organization_id,
                        sender_user_id,
                        message_type,
                        body
                    )
                    VALUES (%s, %s, %s, 'text', %s)
                    RETURNING id, conversation_id, organization_id,
                              sender_user_id, message_type, body, metadata,
                              edited_at, deleted_at, created_at, updated_at
                    """,
                    (
                        conversation_id,
                        conversation["organization_id"],
                        current_user.user_id,
                        payload.body,
                    ),
                )
                row = cur.fetchone()

                cur.execute(
                    """
                    UPDATE organization_conversations
                    SET last_message_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (conversation_id,),
                )

            message = row_to_message(row)
            conversation_payload = add_members_to_conversation_payload(conn, conversation)
            conversation_member_rows = fetch_conversation_members(conn, conversation_id)

        member_ids = [
            member["user_id"]
            for member in conversation_member_rows
            if member.get("status") == "active"
        ]
        dispatch_realtime_event(
            organization_id=conversation["organization_id"],
            user_ids=member_ids,
            event={
                "type": "message.created",
                "message": message,
                "conversation": conversation_payload,
                "sender": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "message": message,
            "conversation": conversation_payload,
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


@router.post("/conversations/{conversation_id}/calls")
def start_call(
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
            "livekit": generate_livekit_join_payload(
                current_user=current_user,
                room_name=call["livekit_room_name"],
            ),
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


@router.post("/calls/{call_session_id}/join")
def join_call(
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
            "livekit": generate_livekit_join_payload(
                current_user=current_user,
                room_name=call["livekit_room_name"],
            ),
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


@router.post("/calls/{call_session_id}/leave")
def leave_call(
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


@router.post("/calls/{call_session_id}/decline")
def decline_call(
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
def list_presence(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
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
                    SELECT
                        om.organization_id,
                        om.user_id,
                        COALESCE(mp.status, 'offline') AS status,
                        COALESCE(mp.last_seen_at, om.updated_at) AS last_seen_at,
                        COALESCE(mp.updated_at, om.updated_at) AS updated_at,
                        om.role AS organization_role
                    FROM organization_members om
                    LEFT JOIN member_presence mp
                      ON mp.organization_id = om.organization_id
                     AND mp.user_id = om.user_id
                    WHERE om.organization_id = %s
                      AND om.status = 'active'
                    ORDER BY
                        CASE COALESCE(mp.status, 'offline')
                            WHEN 'in_call' THEN 1
                            WHEN 'online' THEN 2
                            ELSE 3
                        END,
                        om.created_at ASC
                    """,
                    (organization_id,),
                )
                rows = cur.fetchall()

        return {
            "success": True,
            "presence": [
                {
                    "organization_id": row[0],
                    "user_id": row[1],
                    "status": row[2],
                    "last_seen_at": row[3],
                    "updated_at": row[4],
                    "organization_role": row[5],
                }
                for row in rows
            ],
        }

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
def update_presence(
    payload: UpdatePresenceRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            require_business_or_enterprise_organization(
                conn,
                organization_id,
                current_user,
            )
            presence = upsert_presence(
                conn,
                organization_id,
                current_user.user_id,
                payload.status,
            )

        dispatch_organization_realtime_event(
            organization_id=organization_id,
            event={
                "type": "presence.updated",
                "presence": presence,
                "user": user_public_payload(current_user),
            },
        )

        return {
            "success": True,
            "presence": presence,
            "user": user_public_payload(current_user),
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_presence",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "presence_update_failed",
                "message": "Could not update member presence.",
            },
        ) from exc