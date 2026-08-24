from __future__ import annotations

"""Organization-only scheduled call links.

The URL is a locator, not an authorization credential. Every read and join is
authenticated and rechecks active organization membership and entitlement.
"""

from datetime import datetime, timedelta, timezone
import os
import secrets
from typing import Any, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db
from backend import team_communications as communications
from backend.team_audit import insert_team_audit_event
from backend.team_call_lifecycle import (
    CALL_COLUMNS,
    PARTICIPANT_COLUMNS,
    _call_to_public,
    _fetch_participants,
    _participant_to_public,
    _select_call,
)


router = APIRouter(tags=["team_call_links"])

EARLY_JOIN_MINUTES = 15
MAX_SCHEDULE_HORIZON_DAYS = 365
MIN_CALL_DURATION_MINUTES = 15
MAX_CALL_DURATION_MINUTES = 12 * 60
MAX_CALL_LINK_PARTICIPANTS = 500


class CreateOrganizationCallLinkRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    scheduled_start_at: datetime
    duration_minutes: int = Field(
        ge=MIN_CALL_DURATION_MINUTES,
        le=MAX_CALL_DURATION_MINUTES,
    )
    max_participants: int = Field(ge=2, le=MAX_CALL_LINK_PARTICIPANTS)
    media_type: Literal["audio", "video"] = "video"

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = " ".join(str(value or "").split())
        if not normalized:
            raise ValueError("title is required.")
        return normalized

    @field_validator("scheduled_start_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_start_at must include a timezone offset.")
        return value.astimezone(timezone.utc)

    @field_validator("media_type")
    @classmethod
    def normalize_media_type(cls, value: str) -> str:
        normalized = str(value or "video").strip().lower()
        if normalized not in {"audio", "video"}:
            raise ValueError("media_type must be one of: audio, video.")
        return normalized

    @model_validator(mode="after")
    def validate_schedule(self):
        now = datetime.now(timezone.utc)
        if self.scheduled_start_at < now - timedelta(minutes=5):
            raise ValueError("scheduled_start_at cannot be in the past.")
        if self.scheduled_start_at > now + timedelta(days=MAX_SCHEDULE_HORIZON_DAYS):
            raise ValueError("scheduled_start_at cannot be more than one year ahead.")
        return self


class JoinOrganizationCallLinkRequest(BaseModel):
    recording_consent: bool = False


def _frontend_base_url() -> str:
    for name in ("FRONTEND_URL", "APP_BASE_URL", "NEXT_PUBLIC_APP_URL"):
        value = str(os.getenv(name, "") or "").strip().rstrip("/")
        if value.startswith(("https://", "http://")):
            return value
    return ""


def _share_url(organization_id: int, public_id: str) -> str:
    query = urlencode(
        {
            "organizationId": str(organization_id),
            "callLink": public_id,
        }
    )
    return f"{_frontend_base_url()}/team?{query}"


def _call_link_to_public(row: tuple[Any, ...] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        data = row
    else:
        data = {
            "id": row[0],
            "public_id": row[1],
            "organization_id": row[2],
            "created_by_user_id": row[3],
            "title": row[4],
            "media_type": row[5],
            "scheduled_start_at": row[6],
            "duration_minutes": row[7],
            "max_participants": row[8],
            "status": row[9],
            "active_call_session_id": row[10],
            "cancelled_at": row[11],
            "created_at": row[12],
            "updated_at": row[13],
        }
    scheduled_start_at = data["scheduled_start_at"]
    scheduled_end_at = scheduled_start_at + timedelta(
        minutes=int(data["duration_minutes"])
    )
    return {
        **data,
        "scheduled_end_at": scheduled_end_at,
        "share_url": _share_url(int(data["organization_id"]), str(data["public_id"])),
        "join_window_opens_at": scheduled_start_at
        - timedelta(minutes=EARLY_JOIN_MINUTES),
    }


CALL_LINK_COLUMNS = """
    id, public_id, organization_id, created_by_user_id, title, media_type,
    scheduled_start_at, duration_minutes, max_participants, status,
    active_call_session_id, cancelled_at, created_at, updated_at
"""


def _load_call_link(
    conn,
    *,
    organization_id: int,
    public_id: str,
    for_update: bool = False,
) -> dict[str, Any]:
    normalized_public_id = str(public_id or "").strip()
    lock = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT {CALL_LINK_COLUMNS}
            FROM organization_call_links
            WHERE organization_id = %s AND public_id = %s
            {lock}
            """,
            (organization_id, normalized_public_id),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "call_link_not_found",
                "message": "This organization call link was not found.",
            },
        )
    return _call_link_to_public(row)


def _active_organization_member_count(conn, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM organization_members
            WHERE organization_id = %s AND status = 'active'
            """,
            (organization_id,),
        )
        return int(cur.fetchone()[0])


def _require_link_join_window(link: dict[str, Any]) -> None:
    if link["status"] == "cancelled":
        raise HTTPException(
            status_code=410,
            detail={"error": "call_link_cancelled", "message": "This call was cancelled."},
        )
    if link["status"] == "completed":
        raise HTTPException(
            status_code=410,
            detail={
                "error": "call_link_completed",
                "message": "This scheduled call has already ended.",
            },
        )
    now = datetime.now(timezone.utc)
    if now < link["join_window_opens_at"]:
        raise HTTPException(
            status_code=425,
            detail={
                "error": "call_link_too_early",
                "message": "This call opens 15 minutes before its scheduled time.",
                "join_window_opens_at": link["join_window_opens_at"].isoformat(),
            },
        )
    if now >= link["scheduled_end_at"]:
        raise HTTPException(
            status_code=410,
            detail={
                "error": "call_link_expired",
                "message": "The scheduled call window has ended.",
            },
        )


@router.post("/organizations/{organization_id}/call-links", status_code=201)
def create_organization_call_link(
    payload: CreateOrganizationCallLinkRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        active_member_count = _active_organization_member_count(conn, organization_id)
        if active_member_count < 2:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "not_enough_organization_members",
                    "message": "A call link requires at least two active organization members.",
                },
            )
        if payload.max_participants > active_member_count:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "participant_limit_exceeds_membership",
                    "message": (
                        "The participant limit cannot exceed the number of active "
                        "organization members."
                    ),
                    "active_member_count": active_member_count,
                },
            )

        public_id = secrets.token_urlsafe(32)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO organization_call_links (
                    public_id, organization_id, created_by_user_id, title,
                    media_type, scheduled_start_at, duration_minutes,
                    max_participants
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {CALL_LINK_COLUMNS}
                """,
                (
                    public_id,
                    organization_id,
                    current_user.user_id,
                    payload.title,
                    payload.media_type,
                    payload.scheduled_start_at,
                    payload.duration_minutes,
                    payload.max_participants,
                ),
            )
            link = _call_link_to_public(cur.fetchone())
        insert_team_audit_event(
            conn,
            organization_id=organization_id,
            event_type="call_link.created",
            actor_user_id=current_user.user_id,
            metadata={
                "call_link_id": link["id"],
                "public_id": link["public_id"],
                "scheduled_start_at": link["scheduled_start_at"],
                "duration_minutes": link["duration_minutes"],
                "max_participants": link["max_participants"],
                "media_type": link["media_type"],
            },
        )

    return {"success": True, "call_link": link}


@router.get("/organizations/{organization_id}/call-links")
def list_organization_call_links(
    organization_id: int = Path(..., ge=1),
    include_past: bool = Query(False),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        condition = (
            "TRUE"
            if include_past
            else "status <> 'cancelled' AND "
            "scheduled_start_at + duration_minutes * INTERVAL '1 minute' > NOW()"
        )
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {CALL_LINK_COLUMNS},
                       EXISTS (
                           SELECT 1
                           FROM call_participants participant
                           WHERE participant.call_session_id =
                                 organization_call_links.active_call_session_id
                             AND participant.user_id = %s
                             AND (
                                 participant.joined_at IS NOT NULL
                                 OR participant.provider_joined_at IS NOT NULL
                             )
                       ) AS current_user_participated
                FROM organization_call_links
                WHERE organization_id = %s AND {condition}
                ORDER BY
                    (
                        scheduled_start_at
                        + duration_minutes * INTERVAL '1 minute' > NOW()
                    ) DESC,
                    CASE
                        WHEN scheduled_start_at
                             + duration_minutes * INTERVAL '1 minute' > NOW()
                        THEN scheduled_start_at
                    END ASC,
                    CASE
                        WHEN scheduled_start_at
                             + duration_minutes * INTERVAL '1 minute' <= NOW()
                        THEN scheduled_start_at
                    END DESC,
                    id DESC
                LIMIT 200
                """,
                (current_user.user_id, organization_id),
            )
            links = []
            for row in cur.fetchall():
                link = _call_link_to_public(row[:14])
                link["current_user_participated"] = bool(row[14])
                links.append(link)
    return {"success": True, "call_links": links}


@router.get("/organizations/{organization_id}/call-links/{public_id}")
def get_organization_call_link(
    organization_id: int = Path(..., ge=1),
    public_id: str = Path(..., min_length=32, max_length=96),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        link = _load_call_link(
            conn, organization_id=organization_id, public_id=public_id
        )
    return {"success": True, "call_link": link}


@router.post("/organizations/{organization_id}/call-links/{public_id}/join")
def join_organization_call_link(
    payload: JoinOrganizationCallLinkRequest | None = None,
    organization_id: int = Path(..., ge=1),
    public_id: str = Path(..., min_length=32, max_length=96),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    request_payload = payload or JoinOrganizationCallLinkRequest()
    with get_db() as conn:
        communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        link = _load_call_link(
            conn,
            organization_id=organization_id,
            public_id=public_id,
            for_update=True,
        )
        _require_link_join_window(link)

        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (int(link["id"]),))

        call = None
        active_call_session_id = link.get("active_call_session_id")
        if active_call_session_id:
            call = _select_call(conn, int(active_call_session_id), for_update=True)
            if call["status"] not in {"ringing", "active"}:
                call = None

        if call is None:
            room_name = f"org-{organization_id}-scheduled-{secrets.token_hex(16)}"
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO call_sessions (
                        organization_id, conversation_id, type, media_type,
                        status, created_by_user_id, livekit_room_name,
                        ringing_expires_at, started_at, call_link_id,
                        participant_limit, scheduled_end_at
                    )
                    VALUES (
                        %s, NULL, 'group', %s, 'active', %s, %s,
                        %s, NOW(), %s, %s, %s
                    )
                    RETURNING {CALL_COLUMNS}
                    """,
                    (
                        organization_id,
                        link["media_type"],
                        link["created_by_user_id"],
                        room_name,
                        link["scheduled_end_at"],
                        link["id"],
                        link["max_participants"],
                        link["scheduled_end_at"],
                    ),
                )
                call = communications.row_to_call_session(cur.fetchone())
                cur.execute(
                    """
                    UPDATE organization_call_links
                    SET status = 'active', active_call_session_id = %s
                    WHERE id = %s
                    """,
                    (call["id"], link["id"]),
                )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status
                FROM call_recordings
                WHERE call_session_id = %s
                  AND status IN ('requested', 'starting', 'recording', 'stopping')
                ORDER BY id DESC
                LIMIT 1
                """,
                (call["id"],),
            )
            active_recording = cur.fetchone()
            existing_consent = False
            if active_recording is not None:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM call_recording_consents
                        WHERE recording_id = %s
                          AND user_id = %s
                          AND revoked_at IS NULL
                    )
                    """,
                    (active_recording[0], current_user.user_id),
                )
                existing_consent = bool(cur.fetchone()[0])
        if (
            active_recording is not None
            and not request_payload.recording_consent
            and not existing_consent
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "recording_consent_required",
                    "message": (
                        "This call is being recorded. Explicit recording consent "
                        "is required before joining."
                    ),
                },
            )

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status
                FROM call_participants
                WHERE call_session_id = %s AND user_id = %s
                FOR UPDATE
                """,
                (call["id"], current_user.user_id),
            )
            participant_row = cur.fetchone()
            participant_already_reserved = bool(
                participant_row
                and str(participant_row[1]) in {"connecting", "joined"}
            )
            if not participant_already_reserved:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM call_participants
                    WHERE call_session_id = %s
                      AND status IN ('connecting', 'joined')
                    """,
                    (call["id"],),
                )
                reserved_count = int(cur.fetchone()[0])
                if reserved_count >= int(link["max_participants"]):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "call_participant_limit_reached",
                            "message": "This call has reached its participant limit.",
                        },
                    )
            if participant_row is None:
                cur.execute(
                    f"""
                    INSERT INTO call_participants (
                        call_session_id, organization_id, user_id, status,
                        accepted_at, token_issued_at
                    )
                    VALUES (%s, %s, %s, 'connecting', NOW(), NOW())
                    RETURNING {PARTICIPANT_COLUMNS}
                    """,
                    (call["id"], organization_id, current_user.user_id),
                )
            else:
                cur.execute(
                    f"""
                    UPDATE call_participants
                    SET status = 'connecting',
                        accepted_at = COALESCE(accepted_at, NOW()),
                        token_issued_at = NOW(),
                        left_at = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                      AND status IN ('invited', 'connecting', 'joined', 'declined', 'left', 'missed')
                    RETURNING {PARTICIPANT_COLUMNS}
                    """,
                    (participant_row[0],),
                )
            participant_result = cur.fetchone()
        if participant_result is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "call_participant_not_joinable",
                    "message": "Your participant state does not permit joining this call.",
                },
            )
        participant = communications.row_to_call_participant(participant_result)

        if active_recording is not None and request_payload.recording_consent:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO call_recording_consents (
                        call_session_id, organization_id, user_id,
                        recording_id, consent_version
                    )
                    VALUES (%s, %s, %s, %s, 'call-audio-recording-v1')
                    ON CONFLICT (recording_id, user_id) DO UPDATE SET
                        consent_version = EXCLUDED.consent_version,
                        granted_at = NOW(), revoked_at = NULL, updated_at = NOW()
                    """,
                    (
                        call["id"],
                        organization_id,
                        current_user.user_id,
                        active_recording[0],
                    ),
                )

        participants = _fetch_participants(conn, int(call["id"]))
        livekit = communications.generate_livekit_join_payload(
            current_user=current_user,
            room_name=str(call["livekit_room_name"]),
            call_session_id=int(call["id"]),
            organization_id=organization_id,
            media_type=str(call["media_type"]),
        )

    return {
        "success": True,
        "call_link": link,
        "call": _call_to_public(call),
        "participant": _participant_to_public(participant),
        "participants": [_participant_to_public(item) for item in participants],
        "livekit": livekit,
        "connection_state": "prepared",
    }


@router.delete("/organizations/{organization_id}/call-links/{public_id}")
def cancel_organization_call_link(
    organization_id: int = Path(..., ge=1),
    public_id: str = Path(..., min_length=32, max_length=96),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        access = communications.require_business_or_enterprise_organization(
            conn, organization_id, current_user
        )
        link = _load_call_link(
            conn,
            organization_id=organization_id,
            public_id=public_id,
            for_update=True,
        )
        role = str(access["membership"].get("role") or "")
        if link["created_by_user_id"] != current_user.user_id and role not in {
            "owner",
            "admin",
        }:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "call_link_owner_required",
                    "message": "Only the creator or an organization administrator can cancel this link.",
                },
            )
        if link.get("active_call_session_id"):
            call = _select_call(conn, int(link["active_call_session_id"]))
            if call["status"] in {"ringing", "active"}:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "call_link_has_active_call",
                        "message": "End the active call before cancelling its link.",
                    },
                )
        if link["status"] == "completed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "call_link_completed",
                    "message": "A completed call link cannot be cancelled.",
                },
            )
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE organization_call_links
                SET status = 'cancelled', cancelled_at = NOW(),
                    cancelled_by_user_id = %s
                WHERE id = %s
                RETURNING {CALL_LINK_COLUMNS}
                """,
                (current_user.user_id, link["id"]),
            )
            cancelled = _call_link_to_public(cur.fetchone())
        insert_team_audit_event(
            conn,
            organization_id=organization_id,
            event_type="call_link.cancelled",
            actor_user_id=current_user.user_id,
            metadata={
                "call_link_id": cancelled["id"],
                "public_id": cancelled["public_id"],
            },
        )
    return {"success": True, "call_link": cancelled}


__all__ = ["router"]
