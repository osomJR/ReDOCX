from __future__ import annotations

"""Authoritative LiveKit call lifecycle for ReDOCX team communications.

HTTP actions express user intent. Signed LiveKit webhooks are authoritative for
actual media connection and disconnection. Every user-visible state change is
written to PostgreSQL with its realtime outbox row in the same transaction.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any, Literal
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, field_validator
from psycopg import errors as psycopg_errors
from psycopg.types.json import Jsonb

from backend import team_communications as communications
from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db


router = APIRouter(tags=["team_calls"])
logger = logging.getLogger(__name__)

CallMediaType = Literal["audio", "video"]
TERMINAL_CALL_STATUSES = frozenset({"ended", "missed", "cancelled"})
TEAM_CALL_MAX_DURATION_SECONDS_ENV = "TEAM_CALL_MAX_DURATION_SECONDS"
DEFAULT_TEAM_CALL_MAX_DURATION_SECONDS = 12 * 60 * 60
JOINABLE_PARTICIPANT_STATUSES = frozenset(
    {"invited", "connecting", "joined", "declined", "left"}
)

CALL_COLUMNS = """
    id, organization_id, conversation_id, type, status,
    created_by_user_id, livekit_room_name, started_at, ended_at,
    created_at, updated_at, media_type, ended_by_user_id,
    end_reason, ringing_expires_at, provider_room_sid,
    provider_started_at, provider_finished_at,
    last_provider_event_at, lifecycle_version
"""

PARTICIPANT_COLUMNS = """
    id, call_session_id, organization_id, user_id, status,
    invited_at, joined_at, left_at, created_at, updated_at,
    accepted_at, token_issued_at, provider_participant_sid,
    provider_joined_at, provider_left_at, last_provider_event_at
"""


class StartCallRequest(BaseModel):
    media_type: CallMediaType = "video"

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        normalized = str(value or "video").strip().lower()
        if normalized not in {"audio", "video"}:
            raise ValueError("media_type must be one of: audio, video.")
        return normalized


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _ring_timeout_seconds() -> int:
    return _bounded_int_env(
        communications.TEAM_CALL_RING_TIMEOUT_SECONDS_ENV,
        communications.DEFAULT_TEAM_CALL_RING_TIMEOUT_SECONDS,
        30,
        300,
    )


def _max_call_duration_seconds() -> int:
    # This is a final safety boundary for the rare case where both the browser
    # disconnect signal and LiveKit's retried room-finished webhook are lost.
    return _bounded_int_env(
        TEAM_CALL_MAX_DURATION_SECONDS_ENV,
        DEFAULT_TEAM_CALL_MAX_DURATION_SECONDS,
        60 * 60,
        24 * 60 * 60,
    )


def _call_to_public(call: dict[str, Any]) -> dict[str, Any]:
    return {
        key: call.get(key)
        for key in (
            "id",
            "organization_id",
            "conversation_id",
            "type",
            "media_type",
            "status",
            "created_by_user_id",
            "started_at",
            "ended_at",
            "ended_by_user_id",
            "end_reason",
            "ringing_expires_at",
            "created_at",
            "updated_at",
            "lifecycle_version",
        )
    }


def _participant_to_public(participant: dict[str, Any]) -> dict[str, Any]:
    return {
        key: participant.get(key)
        for key in (
            "id",
            "call_session_id",
            "organization_id",
            "user_id",
            "status",
            "invited_at",
            "accepted_at",
            "joined_at",
            "left_at",
            "created_at",
            "updated_at",
        )
    }


def _select_call(
    conn,
    call_session_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any]:
    lock_clause = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {CALL_COLUMNS} FROM call_sessions WHERE id = %s{lock_clause}",
            (call_session_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "call_not_found", "message": "Call session was not found."},
        )
    return communications.row_to_call_session(row)


def _select_call_by_room(
    conn,
    room_name: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {CALL_COLUMNS}
            FROM call_sessions
            WHERE livekit_room_name = %s
            {lock_clause}
            """,
            (room_name,),
        )
        row = cur.fetchone()
    return communications.row_to_call_session(row) if row is not None else None


def _select_participant(
    conn,
    call_session_id: int,
    user_id: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {PARTICIPANT_COLUMNS}
            FROM call_participants
            WHERE call_session_id = %s AND user_id = %s
            {lock_clause}
            """,
            (call_session_id, user_id),
        )
        row = cur.fetchone()
    return communications.row_to_call_participant(row) if row is not None else None


def _fetch_participants(conn, call_session_id: int) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {PARTICIPANT_COLUMNS}
            FROM call_participants
            WHERE call_session_id = %s
            ORDER BY id ASC
            """,
            (call_session_id,),
        )
        rows = cur.fetchall()
    return [communications.row_to_call_participant(row) for row in rows]


def _conversation_member_ids(conn, conversation_id: int | None) -> list[str]:
    if conversation_id is None:
        return []
    return [
        str(member["user_id"])
        for member in communications.fetch_conversation_members(conn, conversation_id)
        if member.get("status") == "active"
    ]


def _participant_is_still_authorized(
    conn,
    call: dict[str, Any],
    user_id: str,
) -> bool:
    if communications.get_active_organization_membership(
        conn,
        int(call["organization_id"]),
        user_id,
    ) is None:
        return False
    if call.get("conversation_id") is None:
        return True
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1
            FROM conversation_members
            WHERE conversation_id = %s
              AND user_id = %s
              AND status = 'active'
            """,
            (call["conversation_id"], user_id),
        )
        return cur.fetchone() is not None


def _presence_after_call_change(
    conn,
    organization_id: int,
    user_id: str,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM call_participants cp
                JOIN call_sessions cs ON cs.id = cp.call_session_id
                WHERE cp.organization_id = %s
                  AND cp.user_id = %s
                  AND cp.status = 'joined'
                  AND cs.status = 'active'
            )
            """,
            (organization_id, user_id),
        )
        still_in_call = bool(cur.fetchone()[0])
    return communications.upsert_presence(
        conn,
        organization_id,
        user_id,
        "in_call" if still_in_call else "online",
    )


def _enqueue_realtime_event(
    conn,
    *,
    call: dict[str, Any],
    event_type: str,
    recipient_user_ids: list[str],
    event: dict[str, Any],
    event_id: str | None = None,
) -> str:
    resolved_event_id = event_id or (
        f"{event_type}:{call['id']}:{call.get('lifecycle_version', 0)}"
    )
    payload = {
        **event,
        "event_id": resolved_event_id,
        "type": event_type,
        "organization_id": int(call["organization_id"]),
        "call": _call_to_public(call),
        "delivery": "committed",
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_realtime_outbox (
                organization_id, aggregate_type, aggregate_id, event_type,
                event_key, delivery_scope, recipient_user_ids, payload
            )
            VALUES (%s, 'call', %s, %s, %s, 'organization_users', %s, %s)
            ON CONFLICT (event_key) DO NOTHING
            """,
            (
                call["organization_id"],
                str(call["id"]),
                event_type,
                resolved_event_id,
                sorted(set(recipient_user_ids)),
                Jsonb(communications.normalize_realtime_payload(payload)),
            ),
        )
    return resolved_event_id


def _enqueue_media_job(
    conn,
    *,
    call: dict[str, Any],
    delivery_scope: Literal["call_media_revoke", "call_room_terminate"],
    participant_user_ids: list[str],
    event_key: str | None = None,
) -> str:
    resolved_event_key = event_key or (
        f"{delivery_scope}:{call['id']}:{uuid4().hex}"
    )
    revoked_at_epoch = int(datetime.now(timezone.utc).timestamp())
    normalized_participant_user_ids = sorted(
        {
            str(user_id).strip()
            for user_id in participant_user_ids
            if str(user_id).strip()
        }
    )
    aggregate_type = (
        "call_media_room"
        if delivery_scope == "call_room_terminate"
        else "call_media_participant"
    )
    aggregate_id = (
        str(call["id"])
        if delivery_scope == "call_room_terminate"
        else f"{call['id']}:{','.join(normalized_participant_user_ids)}"
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO team_realtime_outbox (
                organization_id, aggregate_type, aggregate_id, event_type,
                event_key, delivery_scope, recipient_user_ids,
                media_room_names, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_key) DO NOTHING
            """,
            (
                call["organization_id"],
                aggregate_type,
                aggregate_id,
                delivery_scope,
                resolved_event_key,
                delivery_scope,
                normalized_participant_user_ids,
                [str(call["livekit_room_name"])],
                Jsonb(
                    {
                        "event_id": resolved_event_key,
                        "type": delivery_scope,
                        "call_session_id": int(call["id"]),
                        "revoked_at_epoch": revoked_at_epoch,
                    }
                ),
            ),
        )
    return resolved_event_key


def _refresh_call(conn, call_session_id: int) -> dict[str, Any]:
    return _select_call(conn, call_session_id, for_update=False)


def _terminalize_call(
    conn,
    *,
    call: dict[str, Any],
    status: Literal["ended", "missed", "cancelled"],
    reason: str,
    ended_by_user_id: str | None,
    event_at: datetime | None = None,
) -> dict[str, Any]:
    resolved_event_at = event_at or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE call_sessions
            SET status = %s,
                ended_at = COALESCE(ended_at, %s),
                ended_by_user_id = COALESCE(ended_by_user_id, %s),
                end_reason = COALESCE(end_reason, %s),
                updated_at = NOW(),
                lifecycle_version = lifecycle_version + 1
            WHERE id = %s
              AND status NOT IN ('ended', 'missed', 'cancelled')
            RETURNING {CALL_COLUMNS}
            """,
            (status, resolved_event_at, ended_by_user_id, reason, call["id"]),
        )
        row = cur.fetchone()
    return communications.row_to_call_session(row) if row is not None else _refresh_call(conn, int(call["id"]))


def _mark_remaining_participants_terminal(
    conn,
    call_session_id: int,
    event_at: datetime,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE call_participants
            SET status = CASE
                    WHEN status IN ('joined', 'connecting') THEN 'left'
                    ELSE 'missed'
                END,
                left_at = CASE
                    WHEN status IN ('joined', 'connecting')
                    THEN COALESCE(left_at, %s)
                    ELSE left_at
                END,
                updated_at = NOW()
            WHERE call_session_id = %s
              AND status IN ('invited', 'connecting', 'joined')
            """,
            (event_at, call_session_id),
        )


def add_call_state_to_message(conn, message: dict[str, Any]) -> dict[str, Any]:
    if message.get("message_type") != "call_event":
        return message
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return message
    call_session_id = communications.parse_optional_int(
        metadata.get("call_session_id") or metadata.get("callSessionId")
    )
    if not call_session_id:
        return message
    try:
        call = _select_call(conn, call_session_id)
    except HTTPException:
        return message
    return {
        **message,
        "metadata": {**metadata, "call": _call_to_public(call)},
    }


@router.post("/conversations/{conversation_id}/calls")
def start_call(
    payload: StartCallRequest | None = None,
    conversation_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_payload = payload or StartCallRequest()
    try:
        with get_db() as conn:
            conversation = communications.get_conversation(conn, conversation_id)
            communications.require_business_or_enterprise_organization(
                conn, conversation["organization_id"], current_user
            )
            communications.require_active_conversation_member(
                conn, conversation_id, current_user.user_id
            )
            members = [
                member
                for member in communications.fetch_conversation_members(
                    conn, conversation_id
                )
                if member.get("status") == "active"
            ]
            if len(members) < 2:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "not_enough_call_participants",
                        "message": "A call requires at least two active conversation members.",
                    },
                )

            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (conversation_id,))
                cur.execute(
                    """
                    SELECT id
                    FROM call_sessions
                    WHERE conversation_id = %s
                      AND status IN ('ringing', 'active')
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (conversation_id,),
                )
                existing = cur.fetchone()
                if existing is not None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "call_already_active",
                            "message": "This conversation already has a live or ringing call.",
                            "call_session_id": int(existing[0]),
                        },
                    )

                room_name = (
                    f"org-{conversation['organization_id']}-conv-{conversation_id}-"
                    f"call-{uuid4().hex}"
                )
                call_type = "group" if conversation["type"] == "group" else "one_to_one"
                cur.execute(
                    f"""
                    INSERT INTO call_sessions (
                        organization_id, conversation_id, type, media_type,
                        status, created_by_user_id, livekit_room_name,
                        ringing_expires_at
                    )
                    VALUES (
                        %s, %s, %s, %s, 'ringing', %s, %s,
                        NOW() + (%s * INTERVAL '1 second')
                    )
                    RETURNING {CALL_COLUMNS}
                    """,
                    (
                        conversation["organization_id"],
                        conversation_id,
                        call_type,
                        request_payload.media_type,
                        current_user.user_id,
                        room_name,
                        _ring_timeout_seconds(),
                    ),
                )
                call = communications.row_to_call_session(cur.fetchone())

                for member in members:
                    is_caller = member["user_id"] == current_user.user_id
                    cur.execute(
                        """
                        INSERT INTO call_participants (
                            call_session_id, organization_id, user_id, status,
                            accepted_at, token_issued_at
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            CASE WHEN %s THEN NOW() ELSE NULL END,
                            CASE WHEN %s THEN NOW() ELSE NULL END
                        )
                        """,
                        (
                            call["id"],
                            conversation["organization_id"],
                            member["user_id"],
                            "connecting" if is_caller else "invited",
                            is_caller,
                            is_caller,
                        ),
                    )

                call_body = (
                    "Audio call started."
                    if request_payload.media_type == "audio"
                    else "Video call started."
                )
                cur.execute(
                    """
                    INSERT INTO conversation_messages (
                        conversation_id, organization_id, sender_user_id,
                        message_type, body, metadata
                    )
                    VALUES (%s, %s, %s, 'call_event', %s, %s)
                    RETURNING id, conversation_id, organization_id,
                              sender_user_id, message_type, body, metadata,
                              edited_at, deleted_at, created_at, updated_at
                    """,
                    (
                        conversation_id,
                        conversation["organization_id"],
                        current_user.user_id,
                        call_body,
                        Jsonb(
                            {
                                "call_session_id": int(call["id"]),
                                "media_type": request_payload.media_type,
                            }
                        ),
                    ),
                )
                call_message = communications.row_to_message(cur.fetchone())
                cur.execute(
                    """
                    UPDATE organization_conversations
                    SET last_message_at = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (call_message["created_at"], conversation_id),
                )

            participants = _fetch_participants(conn, int(call["id"]))
            conversation_payload = communications.add_members_to_conversation_payload(
                conn, conversation
            )
            livekit_payload = communications.generate_livekit_join_payload(
                current_user=current_user,
                room_name=call["livekit_room_name"],
                call_session_id=int(call["id"]),
                organization_id=int(call["organization_id"]),
                media_type=str(call["media_type"]),
            )
            member_ids = [str(member["user_id"]) for member in members]
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type="call.started",
                recipient_user_ids=member_ids,
                event={
                    "participants": [_participant_to_public(item) for item in participants],
                    "conversation": conversation_payload,
                    "message": call_message,
                    "sender": communications.user_public_payload(current_user),
                },
            )

        return {
            "success": True,
            "call": _call_to_public(call),
            "participants": [_participant_to_public(item) for item in participants],
            "message": call_message,
            "livekit": livekit_payload,
            "connection_state": "prepared",
        }
    except HTTPException:
        raise
    except psycopg_errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "call_already_active",
                "message": "This conversation already has a live or ringing call.",
            },
        ) from exc
    except Exception as exc:
        logger.exception("Could not start call for conversation %s.", conversation_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "call_start_failed", "message": "Could not start call."},
        ) from exc


@router.post("/calls/{call_session_id}/join")
def join_call(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    expired = False
    try:
        with get_db() as conn:
            call = _select_call(conn, call_session_id, for_update=True)
            communications.require_business_or_enterprise_organization(
                conn, call["organization_id"], current_user
            )
            if call.get("conversation_id") is not None:
                communications.require_active_conversation_member(
                    conn, call["conversation_id"], current_user.user_id
                )

            if (
                call["status"] == "ringing"
                and call["ringing_expires_at"] <= datetime.now(timezone.utc)
            ):
                expired = True
                event_at = datetime.now(timezone.utc)
                call = _terminalize_call(
                    conn,
                    call=call,
                    status="missed",
                    reason="no_answer",
                    ended_by_user_id=None,
                    event_at=event_at,
                )
                _mark_remaining_participants_terminal(conn, call_session_id, event_at)
                participants = _fetch_participants(conn, call_session_id)
                member_ids = _conversation_member_ids(conn, call.get("conversation_id"))
                _enqueue_realtime_event(
                    conn,
                    call=call,
                    event_type="call.missed",
                    recipient_user_ids=member_ids,
                    event={"participants": [_participant_to_public(p) for p in participants]},
                )
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_room_terminate",
                    participant_user_ids=[p["user_id"] for p in participants],
                )
            elif call["status"] not in {"ringing", "active"}:
                raise HTTPException(
                    status_code=410,
                    detail={
                        "error": "call_not_joinable",
                        "message": "This call is no longer available.",
                        "call_status": call["status"],
                    },
                )
            else:
                participant = _select_participant(
                    conn, call_session_id, current_user.user_id, for_update=True
                )
                if participant is None or participant["status"] == "removed":
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": "call_invitation_required",
                            "message": "You are not an authorized participant in this call.",
                        },
                    )
                if participant["status"] not in JOINABLE_PARTICIPANT_STATUSES:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "call_participant_not_joinable",
                            "message": "Your participant state does not allow joining this call.",
                        },
                    )

                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE call_participants
                        SET status = CASE WHEN status = 'joined' THEN 'joined' ELSE 'connecting' END,
                            accepted_at = COALESCE(accepted_at, NOW()),
                            token_issued_at = NOW(),
                            left_at = CASE WHEN status = 'joined' THEN left_at ELSE NULL END,
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING {PARTICIPANT_COLUMNS}
                        """,
                        (participant["id"],),
                    )
                    participant = communications.row_to_call_participant(cur.fetchone())

                livekit_payload = communications.generate_livekit_join_payload(
                    current_user=current_user,
                    room_name=call["livekit_room_name"],
                    call_session_id=int(call["id"]),
                    organization_id=int(call["organization_id"]),
                    media_type=str(call["media_type"]),
                )

        if expired:
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "call_expired",
                    "message": "This call was not answered before it expired.",
                },
            )
        return {
            "success": True,
            "call": _call_to_public(call),
            "participant": _participant_to_public(participant),
            "livekit": livekit_payload,
            "connection_state": "prepared",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not prepare call %s for join.", call_session_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "call_join_failed", "message": "Could not join call."},
        ) from exc


@router.post("/calls/{call_session_id}/leave")
def leave_call(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = _select_call(conn, call_session_id, for_update=True)
            communications.require_business_or_enterprise_organization(
                conn, call["organization_id"], current_user
            )
            participant = _select_participant(
                conn, call_session_id, current_user.user_id, for_update=True
            )
            if participant is None:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "call_participant_not_found",
                        "message": "You are not a participant in this call.",
                    },
                )

            changed = participant["status"] in {"invited", "connecting", "joined"}
            if changed:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE call_participants
                        SET status = 'left', left_at = COALESCE(left_at, NOW()),
                            updated_at = NOW()
                        WHERE id = %s
                        RETURNING {PARTICIPANT_COLUMNS}
                        """,
                        (participant["id"],),
                    )
                    participant = communications.row_to_call_participant(cur.fetchone())

            terminalized = False
            if call["status"] == "ringing" and call["created_by_user_id"] == current_user.user_id:
                call = _terminalize_call(
                    conn,
                    call=call,
                    status="cancelled",
                    reason="caller_cancelled",
                    ended_by_user_id=current_user.user_id,
                )
                terminalized = True
            elif call["status"] == "active":
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM call_participants
                        WHERE call_session_id = %s AND status = 'joined'
                        """,
                        (call_session_id,),
                    )
                    joined_count = int(cur.fetchone()[0])
                if joined_count == 0:
                    call = _terminalize_call(
                        conn,
                        call=call,
                        status="ended",
                        reason="empty_room",
                        ended_by_user_id=None,
                    )
                    terminalized = True

            presence = _presence_after_call_change(
                conn, int(call["organization_id"]), current_user.user_id
            )
            participants = _fetch_participants(conn, call_session_id)
            member_ids = _conversation_member_ids(conn, call.get("conversation_id"))
            if changed:
                _enqueue_realtime_event(
                    conn,
                    call=call,
                    event_type="call.left",
                    recipient_user_ids=member_ids,
                    event={
                        "participant": _participant_to_public(participant),
                        "presence": presence,
                        "user": communications.user_public_payload(current_user),
                    },
                    event_id=f"call.left:{call_session_id}:{current_user.user_id}:{call['lifecycle_version']}",
                )

            if terminalized:
                _mark_remaining_participants_terminal(
                    conn, call_session_id, datetime.now(timezone.utc)
                )
                participants = _fetch_participants(conn, call_session_id)
                _enqueue_realtime_event(
                    conn,
                    call=call,
                    event_type=("call.cancelled" if call["status"] == "cancelled" else "call.ended"),
                    recipient_user_ids=member_ids,
                    event={"participants": [_participant_to_public(p) for p in participants]},
                )
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_room_terminate",
                    participant_user_ids=[p["user_id"] for p in participants],
                )
            else:
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_media_revoke",
                    participant_user_ids=[current_user.user_id],
                )

        return {
            "success": True,
            "call": _call_to_public(call),
            "participant": _participant_to_public(participant),
            "presence": presence,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not leave call %s.", call_session_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "call_leave_failed", "message": "Could not leave call."},
        ) from exc


@router.post("/calls/{call_session_id}/decline")
def decline_call(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = _select_call(conn, call_session_id, for_update=True)
            communications.require_business_or_enterprise_organization(
                conn, call["organization_id"], current_user
            )
            if call["status"] not in {"ringing", "active"}:
                raise HTTPException(
                    status_code=410,
                    detail={"error": "call_not_active", "message": "This call has ended."},
                )
            participant = _select_participant(
                conn, call_session_id, current_user.user_id, for_update=True
            )
            if participant is None or participant["status"] == "removed":
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "call_participant_not_found",
                        "message": "You are not invited to this call.",
                    },
                )
            if participant["status"] == "joined":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "call_already_joined",
                        "message": "Leave the call instead of declining it.",
                    },
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE call_participants
                    SET status = 'declined', updated_at = NOW()
                    WHERE id = %s
                    RETURNING {PARTICIPANT_COLUMNS}
                    """,
                    (participant["id"],),
                )
                participant = communications.row_to_call_participant(cur.fetchone())

            member_ids = _conversation_member_ids(conn, call.get("conversation_id"))
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type="call.declined",
                recipient_user_ids=member_ids,
                event={
                    "participant": _participant_to_public(participant),
                    "user": communications.user_public_payload(current_user),
                },
                event_id=f"call.declined:{call_session_id}:{current_user.user_id}",
            )
            _enqueue_media_job(
                conn,
                call=call,
                delivery_scope="call_media_revoke",
                participant_user_ids=[current_user.user_id],
            )
        return {
            "success": True,
            "call": _call_to_public(call),
            "participant": _participant_to_public(participant),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not decline call %s.", call_session_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "call_decline_failed", "message": "Could not decline call."},
        ) from exc


@router.post("/calls/{call_session_id}/end")
def end_call(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            call = _select_call(conn, call_session_id, for_update=True)
            access = communications.require_business_or_enterprise_organization(
                conn, call["organization_id"], current_user
            )
            role = access["membership"]["role"]
            if call["created_by_user_id"] != current_user.user_id and role not in {"owner", "admin"}:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "call_host_required",
                        "message": "Only the call host or an organization administrator can end the call for everyone.",
                    },
                )
            if call["status"] not in TERMINAL_CALL_STATUSES:
                terminal_status = "cancelled" if call["status"] == "ringing" else "ended"
                call = _terminalize_call(
                    conn,
                    call=call,
                    status=terminal_status,
                    reason="host_ended",
                    ended_by_user_id=current_user.user_id,
                )
                event_at = datetime.now(timezone.utc)
                _mark_remaining_participants_terminal(conn, call_session_id, event_at)
            participants = _fetch_participants(conn, call_session_id)
            for participant in participants:
                _presence_after_call_change(
                    conn, int(call["organization_id"]), str(participant["user_id"])
                )
            member_ids = _conversation_member_ids(conn, call.get("conversation_id"))
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type=("call.cancelled" if call["status"] == "cancelled" else "call.ended"),
                recipient_user_ids=member_ids,
                event={
                    "participants": [_participant_to_public(p) for p in participants],
                    "ended_by": communications.user_public_payload(current_user),
                },
            )
            _enqueue_media_job(
                conn,
                call=call,
                delivery_scope="call_room_terminate",
                participant_user_ids=[p["user_id"] for p in participants],
            )
        return {
            "success": True,
            "call": _call_to_public(call),
            "participants": [_participant_to_public(p) for p in participants],
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not end call %s.", call_session_id)
        raise HTTPException(
            status_code=500,
            detail={"error": "call_end_failed", "message": "Could not end call."},
        ) from exc


def _provider_event_datetime(created_at: Any) -> datetime:
    try:
        epoch = int(created_at)
    except (TypeError, ValueError):
        epoch = 0
    if epoch <= 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _is_stale_call_provider_event(
    call: dict[str, Any],
    event_at: datetime,
) -> bool:
    last_event_at = call.get("last_provider_event_at")
    return bool(last_event_at and event_at < last_event_at)


def _mark_webhook_result(
    conn,
    webhook_row_id: int,
    status: Literal["processed", "ignored"],
    detail: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE livekit_webhook_events
            SET processing_status = %s, processing_detail = %s
            WHERE id = %s
            """,
            (status, detail[:500], webhook_row_id),
        )


def _record_webhook(
    conn,
    *,
    event_id: str,
    event_type: str,
    event_at: datetime,
    call_session_id: int | None,
    room_name: str,
    participant_identity: str,
) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO livekit_webhook_events (
                event_id, event_type, provider_created_at, call_session_id,
                room_name, participant_identity
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
            """,
            (
                event_id,
                event_type,
                event_at,
                call_session_id,
                room_name or None,
                participant_identity or None,
            ),
        )
        row = cur.fetchone()
    return int(row[0]) if row is not None else None


def _process_livekit_webhook_sync(event: Any) -> dict[str, Any]:
    event_id = str(getattr(event, "id", "") or "").strip()
    event_type = str(getattr(event, "event", "") or "").strip()
    room = getattr(event, "room", None)
    participant_info = getattr(event, "participant", None)
    room_name = str(getattr(room, "name", "") or "").strip()
    room_sid = str(getattr(room, "sid", "") or "").strip()
    participant_identity = str(
        getattr(participant_info, "identity", "") or ""
    ).strip()
    participant_sid = str(getattr(participant_info, "sid", "") or "").strip()
    event_at = _provider_event_datetime(getattr(event, "created_at", 0))

    if not event_id or not event_type:
        raise ValueError("LiveKit webhook is missing its event id or type.")

    with get_db() as conn:
        call = _select_call_by_room(conn, room_name, for_update=True) if room_name else None
        webhook_row_id = _record_webhook(
            conn,
            event_id=event_id,
            event_type=event_type,
            event_at=event_at,
            call_session_id=int(call["id"]) if call else None,
            room_name=room_name,
            participant_identity=participant_identity,
        )
        if webhook_row_id is None:
            return {"duplicate": True, "event_id": event_id}
        if call is None:
            _mark_webhook_result(conn, webhook_row_id, "ignored", "unknown_room")
            return {"ignored": True, "reason": "unknown_room", "event_id": event_id}

        member_ids = _conversation_member_ids(conn, call.get("conversation_id"))

        if event_type == "room_started":
            if _is_stale_call_provider_event(call, event_at):
                _mark_webhook_result(conn, webhook_row_id, "ignored", "stale_room_event")
                return {"ignored": True, "reason": "stale_event", "event_id": event_id}
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_sessions
                    SET provider_room_sid = COALESCE(NULLIF(%s, ''), provider_room_sid),
                        provider_started_at = COALESCE(provider_started_at, %s),
                        last_provider_event_at = GREATEST(
                            COALESCE(last_provider_event_at, %s), %s
                        ),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (room_sid, event_at, event_at, event_at, call["id"]),
                )
            _mark_webhook_result(conn, webhook_row_id, "processed", "room_started")
            return {"processed": True, "event_id": event_id}

        if event_type == "participant_joined":
            participant = _select_participant(
                conn, int(call["id"]), participant_identity, for_update=True
            )
            is_stale = bool(
                participant
                and participant.get("last_provider_event_at")
                and event_at < participant["last_provider_event_at"]
            )
            authorized = bool(
                participant
                and participant.get("status") in {"connecting", "joined"}
                and call.get("status") in {"ringing", "active"}
                and _participant_is_still_authorized(conn, call, participant_identity)
            )
            if is_stale:
                _mark_webhook_result(conn, webhook_row_id, "ignored", "stale_participant_event")
                return {"ignored": True, "reason": "stale_event", "event_id": event_id}
            if not authorized:
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_media_revoke",
                    participant_user_ids=[participant_identity],
                    event_key=f"call.media_revoke:webhook:{event_id}",
                )
                _mark_webhook_result(conn, webhook_row_id, "ignored", "participant_not_authorized")
                return {"ignored": True, "reason": "not_authorized", "event_id": event_id}

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE call_participants
                    SET status = 'joined',
                        joined_at = COALESCE(joined_at, %s),
                        left_at = NULL,
                        provider_participant_sid = COALESCE(NULLIF(%s, ''), provider_participant_sid),
                        provider_joined_at = %s,
                        last_provider_event_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING {PARTICIPANT_COLUMNS}
                    """,
                    (event_at, participant_sid, event_at, event_at, participant["id"]),
                )
                participant = communications.row_to_call_participant(cur.fetchone())
                cur.execute(
                    f"""
                    UPDATE call_sessions
                    SET status = 'active',
                        started_at = COALESCE(started_at, %s),
                        provider_started_at = COALESCE(provider_started_at, %s),
                        provider_room_sid = COALESCE(NULLIF(%s, ''), provider_room_sid),
                        last_provider_event_at = GREATEST(
                            COALESCE(last_provider_event_at, %s), %s
                        ),
                        updated_at = NOW(),
                        lifecycle_version = lifecycle_version + 1
                    WHERE id = %s
                      AND status IN ('ringing', 'active')
                    RETURNING {CALL_COLUMNS}
                    """,
                    (event_at, event_at, room_sid, event_at, event_at, call["id"]),
                )
                updated_call = cur.fetchone()
            if updated_call is None:
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_media_revoke",
                    participant_user_ids=[participant_identity],
                    event_key=f"call.media_revoke:terminal:{event_id}",
                )
                _mark_webhook_result(conn, webhook_row_id, "ignored", "call_became_terminal")
                return {"ignored": True, "reason": "terminal_call", "event_id": event_id}

            call = communications.row_to_call_session(updated_call)
            presence = communications.upsert_presence(
                conn, int(call["organization_id"]), participant_identity, "in_call"
            )
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type="call.joined",
                recipient_user_ids=member_ids,
                event={
                    "participant": _participant_to_public(participant),
                    "presence": presence,
                    "user": {"id": participant_identity},
                },
                event_id=f"call.joined:{call['id']}:{participant_identity}:{call['lifecycle_version']}",
            )
            _mark_webhook_result(conn, webhook_row_id, "processed", "participant_joined")
            return {"processed": True, "event_id": event_id}

        if event_type in {"participant_left", "participant_connection_aborted"}:
            participant = _select_participant(
                conn, int(call["id"]), participant_identity, for_update=True
            )
            if participant is None:
                _mark_webhook_result(conn, webhook_row_id, "ignored", "unknown_participant")
                return {"ignored": True, "reason": "unknown_participant", "event_id": event_id}
            if participant.get("last_provider_event_at") and event_at < participant["last_provider_event_at"]:
                _mark_webhook_result(conn, webhook_row_id, "ignored", "stale_participant_event")
                return {"ignored": True, "reason": "stale_event", "event_id": event_id}

            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE call_participants
                    SET status = CASE WHEN status = 'removed' THEN 'removed' ELSE 'left' END,
                        left_at = COALESCE(left_at, %s),
                        provider_left_at = %s,
                        last_provider_event_at = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING {PARTICIPANT_COLUMNS}
                    """,
                    (event_at, event_at, event_at, participant["id"]),
                )
                participant = communications.row_to_call_participant(cur.fetchone())
                cur.execute(
                    """
                    SELECT COUNT(*) FROM call_participants
                    WHERE call_session_id = %s AND status = 'joined'
                    """,
                    (call["id"],),
                )
                joined_count = int(cur.fetchone()[0])

            terminalized = False
            if call["status"] == "active" and joined_count == 0:
                call = _terminalize_call(
                    conn,
                    call=call,
                    status="ended",
                    reason="empty_room",
                    ended_by_user_id=None,
                    event_at=event_at,
                )
                terminalized = True
            elif (
                call["status"] == "ringing"
                and participant_identity == call["created_by_user_id"]
            ):
                call = _terminalize_call(
                    conn,
                    call=call,
                    status="cancelled",
                    reason=(
                        "connection_failed"
                        if event_type == "participant_connection_aborted"
                        else "caller_cancelled"
                    ),
                    ended_by_user_id=None,
                    event_at=event_at,
                )
                terminalized = True
            presence = _presence_after_call_change(
                conn, int(call["organization_id"]), participant_identity
            )
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type="call.left",
                recipient_user_ids=member_ids,
                event={
                    "participant": _participant_to_public(participant),
                    "presence": presence,
                    "reason": event_type,
                    "user": {"id": participant_identity},
                },
                event_id=f"call.left:webhook:{event_id}",
            )
            if terminalized:
                _mark_remaining_participants_terminal(conn, int(call["id"]), event_at)
                participants = _fetch_participants(conn, int(call["id"]))
                _enqueue_realtime_event(
                    conn,
                    call=call,
                    event_type=("call.cancelled" if call["status"] == "cancelled" else "call.ended"),
                    recipient_user_ids=member_ids,
                    event={"participants": [_participant_to_public(p) for p in participants]},
                )
                _enqueue_media_job(
                    conn,
                    call=call,
                    delivery_scope="call_room_terminate",
                    participant_user_ids=[p["user_id"] for p in participants],
                    event_key=f"call.room_terminate:webhook:{event_id}",
                )
            _mark_webhook_result(conn, webhook_row_id, "processed", event_type)
            return {"processed": True, "event_id": event_id}

        if event_type == "room_finished":
            if _is_stale_call_provider_event(call, event_at):
                _mark_webhook_result(conn, webhook_row_id, "ignored", "stale_room_event")
                return {"ignored": True, "reason": "stale_event", "event_id": event_id}
            was_terminal = call["status"] in TERMINAL_CALL_STATUSES
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_sessions
                    SET provider_room_sid = COALESCE(NULLIF(%s, ''), provider_room_sid),
                        provider_finished_at = COALESCE(provider_finished_at, %s),
                        last_provider_event_at = GREATEST(
                            COALESCE(last_provider_event_at, %s), %s
                        ),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (room_sid, event_at, event_at, event_at, call["id"]),
                )
            if call["status"] not in TERMINAL_CALL_STATUSES:
                call = _terminalize_call(
                    conn,
                    call=call,
                    status=("missed" if call["status"] == "ringing" else "ended"),
                    reason=("no_answer" if call["status"] == "ringing" else "empty_room"),
                    ended_by_user_id=None,
                    event_at=event_at,
                )
                _mark_remaining_participants_terminal(conn, int(call["id"]), event_at)
            else:
                call = _refresh_call(conn, int(call["id"]))
            participants = _fetch_participants(conn, int(call["id"]))
            for item in participants:
                _presence_after_call_change(
                    conn, int(call["organization_id"]), str(item["user_id"])
                )
            if not was_terminal:
                _enqueue_realtime_event(
                    conn,
                    call=call,
                    event_type=(
                        "call.missed"
                        if call["status"] == "missed"
                        else "call.cancelled"
                        if call["status"] == "cancelled"
                        else "call.ended"
                    ),
                    recipient_user_ids=member_ids,
                    event={"participants": [_participant_to_public(p) for p in participants]},
                    event_id=f"call.room_finished:{event_id}",
                )
            _mark_webhook_result(conn, webhook_row_id, "processed", "room_finished")
            return {"processed": True, "event_id": event_id}

        _mark_webhook_result(conn, webhook_row_id, "ignored", "event_type_not_used")
        return {"ignored": True, "reason": "event_type_not_used", "event_id": event_id}


@router.post("/livekit/webhook", include_in_schema=False)
async def livekit_webhook(request: Request):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > communications.MAX_LIVEKIT_WEBHOOK_BYTES:
                raise HTTPException(status_code=413, detail="Webhook payload is too large.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header.")

    body_chunks: list[bytes] = []
    received_bytes = 0
    async for chunk in request.stream():
        received_bytes += len(chunk)
        if received_bytes > communications.MAX_LIVEKIT_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Webhook payload is too large.")
        body_chunks.append(chunk)
    raw_body = b"".join(body_chunks)
    try:
        body = raw_body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be UTF-8 JSON.") from exc

    authorization = str(request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        authorization = authorization[7:].strip()
    if not authorization:
        raise HTTPException(status_code=401, detail="LiveKit webhook signature is required.")

    try:
        from livekit import api as livekit_api

        config = communications.get_livekit_config()
        verifier = livekit_api.TokenVerifier(
            str(config["api_key"]), str(config["api_secret"])
        )
        receiver = livekit_api.WebhookReceiver(verifier)
        event = receiver.receive(body, authorization)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rejected an invalid LiveKit webhook: %s", type(exc).__name__)
        raise HTTPException(status_code=401, detail="Invalid LiveKit webhook signature.") from exc

    try:
        result = await anyio.to_thread.run_sync(
            lambda: _process_livekit_webhook_sync(event)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Could not process a verified LiveKit webhook.")
        # LiveKit retries non-successful webhook deliveries.
        raise HTTPException(status_code=503, detail="Webhook processing is temporarily unavailable.") from exc
    return {"success": True, **result}


def expire_stale_calls_sync() -> int:
    now = datetime.now(timezone.utc)
    maximum_call_duration_seconds = _max_call_duration_seconds()
    active_call_cutoff = now - timedelta(seconds=maximum_call_duration_seconds)
    expired_count = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status
                FROM call_sessions
                WHERE (
                        status = 'ringing'
                        AND ringing_expires_at <= NOW()
                    )
                   OR (
                        status = 'active'
                        AND COALESCE(started_at, created_at) <=
                            NOW() - (%s * INTERVAL '1 second')
                    )
                ORDER BY
                    CASE
                        WHEN status = 'ringing' THEN ringing_expires_at
                        ELSE COALESCE(started_at, created_at)
                    END ASC,
                    id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 50
                """,
                (maximum_call_duration_seconds,),
            )
            candidates = [(int(row[0]), str(row[1])) for row in cur.fetchall()]

        for call_id, selected_status in candidates:
            call = _select_call(conn, call_id, for_update=True)
            is_expired_ringing = bool(
                selected_status == "ringing"
                and call["status"] == "ringing"
                and call["ringing_expires_at"] <= now
            )
            active_started_at = call.get("started_at") or call.get("created_at")
            is_expired_active = bool(
                selected_status == "active"
                and call["status"] == "active"
                and active_started_at
                and active_started_at <= active_call_cutoff
            )
            if not is_expired_ringing and not is_expired_active:
                continue
            call = _terminalize_call(
                conn,
                call=call,
                status="missed" if is_expired_ringing else "ended",
                reason="no_answer" if is_expired_ringing else "system",
                ended_by_user_id=None,
                event_at=now,
            )
            _mark_remaining_participants_terminal(conn, call_id, now)
            participants = _fetch_participants(conn, call_id)
            for participant in participants:
                _presence_after_call_change(
                    conn,
                    int(call["organization_id"]),
                    str(participant["user_id"]),
                )
            member_ids = _conversation_member_ids(conn, call.get("conversation_id"))
            _enqueue_realtime_event(
                conn,
                call=call,
                event_type="call.missed" if is_expired_ringing else "call.ended",
                recipient_user_ids=member_ids,
                event={
                    "participants": [_participant_to_public(p) for p in participants],
                    "reason": call["end_reason"],
                },
            )
            _enqueue_media_job(
                conn,
                call=call,
                delivery_scope="call_room_terminate",
                participant_user_ids=[p["user_id"] for p in participants],
            )
            expired_count += 1
    return expired_count


_CALL_REAPER_TASK: asyncio.Task[None] | None = None
_CALL_REAPER_STOP = asyncio.Event()


async def _call_reaper() -> None:
    interval = _bounded_int_env(
        communications.TEAM_CALL_REAPER_INTERVAL_SECONDS_ENV,
        communications.DEFAULT_TEAM_CALL_REAPER_INTERVAL_SECONDS,
        5,
        60,
    )
    while not _CALL_REAPER_STOP.is_set():
        try:
            await anyio.to_thread.run_sync(expire_stale_calls_sync)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call expiry reconciliation failed.")
        try:
            await asyncio.wait_for(_CALL_REAPER_STOP.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def start_call_lifecycle_services() -> None:
    global _CALL_REAPER_TASK
    if _CALL_REAPER_TASK is not None:
        return
    _CALL_REAPER_STOP.clear()
    _CALL_REAPER_TASK = asyncio.create_task(
        _call_reaper(), name="team-call-lifecycle-reaper"
    )


async def stop_call_lifecycle_services() -> None:
    global _CALL_REAPER_TASK
    _CALL_REAPER_STOP.set()
    task = _CALL_REAPER_TASK
    _CALL_REAPER_TASK = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
