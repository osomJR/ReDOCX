from __future__ import annotations

"""Consent-governed, organization-scoped LiveKit audio recording."""

import asyncio
import logging
import os
from typing import Any
from uuid import uuid4

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from psycopg import errors as psycopg_errors

from backend import team_communications as communications
from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.database import get_db
from backend.team_audit import insert_team_audit_event
from backend.team_call_lifecycle import (
    _call_to_public,
    _enqueue_realtime_event,
    _fetch_participants,
    _participant_to_public,
    _publish_committed_realtime_events_best_effort,
    _provider_event_datetime,
    _select_call,
    _select_call_by_room,
    _select_participant,
)


router = APIRouter(tags=["team_call_recordings"])
logger = logging.getLogger(__name__)

CONSENT_VERSION = "call-audio-recording-v1"
DEFAULT_RECORDING_RETENTION_DAYS = 30
RECORDING_RETENTION_SWEEP_SECONDS = 60 * 60
RECORDING_RECONCILE_SECONDS = 15
OPEN_RECORDING_STATUSES = ("requested", "starting", "recording", "stopping")

RECORDING_COLUMNS = """
    id, call_session_id, organization_id, requested_by_user_id, status,
    egress_id, storage_key, content_type, participant_count, requested_at,
    started_at, ended_at, retention_expires_at, file_size_bytes,
    checksum_sha256, failure_reason, deleted_at, created_at, updated_at
"""


class CallRecordingConsentRequest(BaseModel):
    consent: bool


def _env_bool(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default) or default).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _recording_to_public(row: tuple[Any, ...] | dict[str, Any]) -> dict[str, Any]:
    if isinstance(row, dict):
        data = dict(row)
    else:
        data = {
            "id": row[0],
            "call_session_id": row[1],
            "organization_id": row[2],
            "requested_by_user_id": row[3],
            "status": row[4],
            "egress_id": row[5],
            "storage_key": row[6],
            "content_type": row[7],
            "participant_count": row[8],
            "requested_at": row[9],
            "started_at": row[10],
            "ended_at": row[11],
            "retention_expires_at": row[12],
            "file_size_bytes": row[13],
            "checksum_sha256": row[14],
            "failure_reason": row[15],
            "deleted_at": row[16],
            "created_at": row[17],
            "updated_at": row[18],
        }
    storage_key = data.pop("storage_key", None)
    data.pop("egress_id", None)
    data["available_for_download"] = bool(
        data.get("status") == "completed"
        and storage_key
        and not data.get("deleted_at")
    )
    if data["available_for_download"]:
        data["download_url"] = (
            f"/api/calls/{int(data['call_session_id'])}/recordings/"
            f"{int(data['id'])}/download"
        )
    else:
        data["download_url"] = None
    return data


def _load_recording(
    conn,
    recording_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any]:
    lock = " FOR UPDATE" if for_update else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {RECORDING_COLUMNS} FROM call_recordings WHERE id = %s{lock}",
            (recording_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "recording_not_found", "message": "Recording was not found."},
        )
    raw = dict(zip(
        [
            "id", "call_session_id", "organization_id", "requested_by_user_id",
            "status", "egress_id", "storage_key", "content_type",
            "participant_count", "requested_at", "started_at", "ended_at",
            "retention_expires_at", "file_size_bytes", "checksum_sha256",
            "failure_reason", "deleted_at", "created_at", "updated_at",
        ],
        row,
    ))
    return raw


def _recording_policy(conn, organization_id: int) -> tuple[bool, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT call_recording_enabled, call_recording_retention_days
            FROM organization_communication_policies
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        row = cur.fetchone()
    if row is None:
        return True, DEFAULT_RECORDING_RETENTION_DAYS
    return bool(row[0]), max(1, min(int(row[1] or 30), 365))


def _joined_user_ids(conn, call_session_id: int) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM call_participants
            WHERE call_session_id = %s AND status = 'joined'
            ORDER BY user_id
            """,
            (call_session_id,),
        )
        return [str(row[0]) for row in cur.fetchall()]


def _all_joined_participants_consented(
    conn,
    *,
    recording_id: int,
    call_session_id: int,
) -> bool:
    joined = _joined_user_ids(conn, call_session_id)
    if len(joined) < 2:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT user_id
            FROM call_recording_consents
            WHERE recording_id = %s AND revoked_at IS NULL
            """,
            (recording_id,),
        )
        consented = {str(row[0]) for row in cur.fetchall()}
    return set(joined).issubset(consented)


def _require_joined_participant(
    conn,
    *,
    call_session_id: int,
    user_id: str,
) -> dict[str, Any]:
    participant = _select_participant(conn, call_session_id, user_id)
    if participant is None or participant.get("status") != "joined":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "joined_call_participant_required",
                "message": "You must be actively connected to this call.",
            },
        )
    return participant


def _has_participated_in_call(participant: dict[str, Any] | None) -> bool:
    return bool(
        participant
        and (
            participant.get("joined_at") is not None
            or participant.get("provider_joined_at") is not None
        )
    )


def _recording_event(
    conn,
    *,
    call: dict[str, Any],
    recording: dict[str, Any],
    event_type: str,
    recipient_user_ids: list[str],
    extra: dict[str, Any] | None = None,
    event_id_suffix: str | None = None,
) -> dict[str, Any]:
    return _enqueue_realtime_event(
        conn,
        call=call,
        event_type=event_type,
        recipient_user_ids=recipient_user_ids,
        event={
            "recording": _recording_to_public(recording),
            **(extra or {}),
        },
        event_id=(
            f"{event_type}:{recording['id']}:"
            f"{event_id_suffix or recording.get('updated_at')}"
        ),
    )


def _s3_config() -> dict[str, Any]:
    bucket = str(os.getenv("CALL_RECORDING_S3_BUCKET", "") or "").strip()
    region = str(os.getenv("CALL_RECORDING_S3_REGION", "") or "").strip()
    access_key = str(os.getenv("CALL_RECORDING_S3_ACCESS_KEY", "") or "").strip()
    secret = str(os.getenv("CALL_RECORDING_S3_SECRET_KEY", "") or "").strip()
    endpoint = str(os.getenv("CALL_RECORDING_S3_ENDPOINT", "") or "").strip()
    if not bucket or not access_key or not secret:
        raise RuntimeError(
            "CALL_RECORDING_S3_BUCKET, CALL_RECORDING_S3_ACCESS_KEY, and "
            "CALL_RECORDING_S3_SECRET_KEY are required."
        )
    return {
        "bucket": bucket,
        "region": region,
        "access_key": access_key,
        "secret": secret,
        "endpoint": endpoint,
        "force_path_style": _env_bool("CALL_RECORDING_S3_FORCE_PATH_STYLE"),
    }


def _boto3_client():
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise RuntimeError("Install boto3 to download and purge call recordings.") from exc
    config = _s3_config()
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint"] or None,
        region_name=config["region"] or None,
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if config["force_path_style"] else "auto"},
        ),
    )


async def start_recording_egress(recording_id: int) -> None:
    queued: list[dict[str, Any]] = []
    storage_key = ""
    call: dict[str, Any] | None = None
    recording: dict[str, Any] | None = None
    try:
        def _claim() -> tuple[dict[str, Any], dict[str, Any], list[str], str] | None:
            with get_db() as conn:
                current = _load_recording(conn, recording_id, for_update=True)
                if current["status"] != "requested":
                    return None
                current_call = _select_call(
                    conn, int(current["call_session_id"]), for_update=True
                )
                if current_call["status"] != "active":
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE call_recordings
                            SET status = 'cancelled', ended_at = NOW(),
                                failure_reason = 'call_not_active'
                            WHERE id = %s
                            """,
                            (recording_id,),
                        )
                    return None
                joined = _joined_user_ids(conn, int(current_call["id"]))
                if len(joined) < 2 or not _all_joined_participants_consented(
                    conn,
                    recording_id=recording_id,
                    call_session_id=int(current_call["id"]),
                ):
                    return None
                key_prefix = str(
                    os.getenv("CALL_RECORDING_S3_PREFIX", "redocx/call-recordings")
                    or "redocx/call-recordings"
                ).strip("/")
                key = (
                    f"{key_prefix}/org-{int(current_call['organization_id'])}/"
                    f"call-{int(current_call['id'])}/{uuid4().hex}.ogg"
                )
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE call_recordings
                        SET status = 'starting', storage_key = %s,
                            participant_count = %s
                        WHERE id = %s AND status = 'requested'
                        RETURNING {RECORDING_COLUMNS}
                        """,
                        (key, len(joined), recording_id),
                    )
                    updated_row = cur.fetchone()
                if updated_row is None:
                    return None
                updated = _load_recording(conn, recording_id)
                return current_call, updated, joined, key

        claimed = await anyio.to_thread.run_sync(_claim)
        if claimed is None:
            return
        call, recording, joined_user_ids, storage_key = claimed

        try:
            from livekit import api as livekit_api
        except ImportError as exc:
            raise RuntimeError("Install livekit-api to record calls.") from exc

        config = _s3_config()
        request = livekit_api.RoomCompositeEgressRequest(
            room_name=str(call["livekit_room_name"]),
            audio_only=True,
            file_outputs=[
                livekit_api.EncodedFileOutput(
                    file_type=livekit_api.EncodedFileType.OGG,
                    filepath=storage_key,
                    s3=livekit_api.S3Upload(
                        bucket=config["bucket"],
                        region=config["region"],
                        access_key=config["access_key"],
                        secret=config["secret"],
                        endpoint=config["endpoint"],
                        force_path_style=config["force_path_style"],
                    ),
                )
            ],
        )
        async with livekit_api.LiveKitAPI() as livekit:
            response = await livekit.egress.start_room_composite_egress(request)
        egress_id = str(getattr(response, "egress_id", "") or "").strip()
        if not egress_id:
            raise RuntimeError("LiveKit did not return an egress identifier.")

        def _mark_recording() -> tuple[
            dict[str, Any], dict[str, Any], list[dict[str, Any]], bool
        ]:
            with get_db() as conn:
                current_call = _select_call(conn, int(call["id"]))
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = CASE
                                WHEN status = 'starting' THEN 'recording'
                                ELSE status
                            END,
                            egress_id = %s,
                            started_at = COALESCE(started_at, NOW())
                        WHERE id = %s AND status IN ('starting', 'stopping')
                        RETURNING status
                        """,
                        (egress_id, recording_id),
                    )
                    state_row = cur.fetchone()
                current = _load_recording(conn, recording_id)
                should_stop = state_row is None or str(state_row[0]) == "stopping"
                events: list[dict[str, Any]] = []
                if not should_stop:
                    recipients = [
                        str(item["user_id"])
                        for item in _fetch_participants(conn, int(current_call["id"]))
                    ]
                    events.append(
                        _recording_event(
                            conn,
                            call=current_call,
                            recording=current,
                            event_type="call.recording.started",
                            recipient_user_ids=recipients,
                        )
                    )
                return current_call, current, events, should_stop

        _, recording, queued, should_stop = await anyio.to_thread.run_sync(
            _mark_recording
        )
        _publish_committed_realtime_events_best_effort(queued)
        if should_stop:
            async with livekit_api.LiveKitAPI() as livekit:
                await livekit.egress.stop_egress(
                    livekit_api.StopEgressRequest(egress_id=egress_id)
                )
    except Exception as exc:
        logger.exception("Could not start call recording %s.", recording_id)

        def _mark_failed() -> list[dict[str, Any]]:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'failed', ended_at = NOW(),
                            failure_reason = %s
                        WHERE id = %s
                          AND status IN ('requested', 'starting', 'stopping')
                        """,
                        (type(exc).__name__, recording_id),
                    )
                current = _load_recording(conn, recording_id)
                current_call = _select_call(conn, int(current["call_session_id"]))
                recipients = _joined_user_ids(conn, int(current_call["id"]))
                return [
                    _recording_event(
                        conn,
                        call=current_call,
                        recording=current,
                        event_type="call.recording.failed",
                        recipient_user_ids=recipients,
                    )
                ]

        try:
            queued = await anyio.to_thread.run_sync(_mark_failed)
            _publish_committed_realtime_events_best_effort(queued)
        except Exception:
            logger.exception("Could not persist call recording failure state.")


async def stop_recording_egress(recording_id: int, reason: str = "user_stopped") -> None:
    def _claim_stop() -> str | None:
        with get_db() as conn:
            recording = _load_recording(conn, recording_id, for_update=True)
            if recording["status"] == "requested":
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'cancelled', ended_at = NOW(),
                            failure_reason = %s
                        WHERE id = %s AND status = 'requested'
                        """,
                        (reason[:500], recording_id),
                    )
                return None
            if recording["status"] not in {"starting", "recording", "stopping"}:
                return None
            if recording["status"] != "stopping":
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'stopping', failure_reason = NULL
                        WHERE id = %s
                        """,
                        (recording_id,),
                    )
            return str(recording.get("egress_id") or "").strip() or None

    egress_id = await anyio.to_thread.run_sync(_claim_stop)
    if not egress_id:
        return

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            from livekit import api as livekit_api

            async with livekit_api.LiveKitAPI() as livekit:
                await livekit.egress.stop_egress(
                    livekit_api.StopEgressRequest(egress_id=egress_id)
                )
            return
        except Exception as exc:
            last_error = exc
            logger.warning(
                "Could not stop call recording %s on attempt %s.",
                recording_id,
                attempt + 1,
                exc_info=True,
            )
            if attempt < 2:
                await asyncio.sleep(0.5 * (2**attempt))

    # Keep the recording in the stopping state. A later provider webhook or
    # room termination can still close it, and the open-state alert remains
    # visible instead of falsely claiming that media capture has ended.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE call_recordings
                SET failure_reason = %s
                WHERE id = %s AND status = 'stopping'
                """,
                (
                    f"stop_retry_exhausted:{type(last_error).__name__}:{reason}"[:500],
                    recording_id,
                ),
            )


@router.post("/calls/{call_session_id}/recordings", status_code=202)
def request_call_recording(
    background_tasks: BackgroundTasks,
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    queued: list[dict[str, Any]] = []
    try:
        with get_db() as conn:
            call = _select_call(conn, call_session_id, for_update=True)
            communications.require_business_or_enterprise_organization(
                conn, int(call["organization_id"]), current_user
            )
            _require_joined_participant(
                conn,
                call_session_id=call_session_id,
                user_id=current_user.user_id,
            )
            if call["status"] != "active":
                raise HTTPException(
                    status_code=409,
                    detail={"error": "call_not_active", "message": "The call is not active."},
                )
            joined = _joined_user_ids(conn, call_session_id)
            if len(joined) < 2:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "recording_requires_two_participants",
                        "message": "At least two organization members must be connected.",
                    },
                )
            enabled, retention_days = _recording_policy(
                conn, int(call["organization_id"])
            )
            if not enabled:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "call_recording_disabled",
                        "message": "Call recording is disabled by organization policy.",
                    },
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO call_recordings (
                        call_session_id, organization_id, requested_by_user_id,
                        participant_count, retention_expires_at
                    )
                    VALUES (%s, %s, %s, %s, NOW() + (%s * INTERVAL '1 day'))
                    RETURNING {RECORDING_COLUMNS}
                    """,
                    (
                        call_session_id,
                        call["organization_id"],
                        current_user.user_id,
                        len(joined),
                        retention_days,
                    ),
                )
                row = cur.fetchone()
                recording_id = int(row[0])
                cur.execute(
                    """
                    INSERT INTO call_recording_consents (
                        call_session_id, organization_id, user_id,
                        recording_id, consent_version
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        call_session_id,
                        call["organization_id"],
                        current_user.user_id,
                        recording_id,
                        CONSENT_VERSION,
                    ),
                )
            recording = _load_recording(conn, recording_id)
            queued.append(
                _recording_event(
                    conn,
                    call=call,
                    recording=recording,
                    event_type="call.recording.consent_required",
                    recipient_user_ids=joined,
                    extra={
                        "requested_by": communications.user_public_payload(current_user),
                        "consent_version": CONSENT_VERSION,
                    },
                )
            )
            insert_team_audit_event(
                conn,
                organization_id=int(call["organization_id"]),
                event_type="call_recording.requested",
                actor_user_id=current_user.user_id,
                call_session_id=call_session_id,
                metadata={
                    "recording_id": recording_id,
                    "consent_version": CONSENT_VERSION,
                    "participant_count": len(joined),
                    "retention_days": retention_days,
                },
            )
            ready = _all_joined_participants_consented(
                conn,
                recording_id=recording_id,
                call_session_id=call_session_id,
            )
        _publish_committed_realtime_events_best_effort(queued)
        if ready:
            background_tasks.add_task(start_recording_egress, recording_id)
        return {
            "success": True,
            "recording": _recording_to_public(recording),
            "consent_required_from": [
                user_id for user_id in joined if user_id != current_user.user_id
            ],
        }
    except HTTPException:
        raise
    except psycopg_errors.UniqueViolation as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "recording_already_open",
                "message": "This call already has an open recording request.",
            },
        ) from exc


@router.post("/calls/{call_session_id}/recordings/{recording_id}/consent")
def set_call_recording_consent(
    payload: CallRecordingConsentRequest,
    background_tasks: BackgroundTasks,
    call_session_id: int = Path(..., ge=1),
    recording_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    queued: list[dict[str, Any]] = []
    with get_db() as conn:
        call = _select_call(conn, call_session_id, for_update=True)
        communications.require_business_or_enterprise_organization(
            conn, int(call["organization_id"]), current_user
        )
        _require_joined_participant(
            conn,
            call_session_id=call_session_id,
            user_id=current_user.user_id,
        )
        recording = _load_recording(conn, recording_id, for_update=True)
        if int(recording["call_session_id"]) != call_session_id:
            raise HTTPException(status_code=404, detail="Recording was not found.")
        if recording["status"] not in OPEN_RECORDING_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "recording_not_open",
                    "message": "This recording request is no longer open.",
                },
            )
        with conn.cursor() as cur:
            if payload.consent:
                cur.execute(
                    """
                    INSERT INTO call_recording_consents (
                        call_session_id, organization_id, user_id,
                        recording_id, consent_version
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (recording_id, user_id) DO UPDATE SET
                        consent_version = EXCLUDED.consent_version,
                        granted_at = NOW(), revoked_at = NULL, updated_at = NOW()
                    """,
                    (
                        call_session_id,
                        call["organization_id"],
                        current_user.user_id,
                        recording_id,
                        CONSENT_VERSION,
                    ),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO call_recording_consents (
                        call_session_id, organization_id, user_id,
                        recording_id, consent_version, granted_at, revoked_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (recording_id, user_id) DO UPDATE SET
                        consent_version = EXCLUDED.consent_version,
                        revoked_at = NOW(), updated_at = NOW()
                    """,
                    (
                        call_session_id,
                        call["organization_id"],
                        current_user.user_id,
                        recording_id,
                        CONSENT_VERSION,
                    ),
                )
                if recording["status"] == "requested":
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'cancelled', ended_at = NOW(),
                            failure_reason = 'participant_declined_consent'
                        WHERE id = %s AND status = 'requested'
                        """,
                        (recording_id,),
                    )
        if not payload.consent and recording["status"] == "requested":
            recording = _load_recording(conn, recording_id)
        recipients = [
            str(item["user_id"])
            for item in _fetch_participants(conn, call_session_id)
        ]
        queued.append(
            _recording_event(
                conn,
                call=call,
                recording=recording,
                event_type=(
                    "call.recording.cancelled"
                    if recording["status"] == "cancelled"
                    else "call.recording.consent_updated"
                ),
                recipient_user_ids=recipients,
                extra={
                    "user_id": current_user.user_id,
                    "consent": payload.consent,
                    "consent_version": CONSENT_VERSION,
                },
                event_id_suffix=(
                    f"{current_user.user_id}:{payload.consent}:{uuid4().hex}"
                ),
            )
        )
        insert_team_audit_event(
            conn,
            organization_id=int(call["organization_id"]),
            event_type=(
                "call_recording.consent_granted"
                if payload.consent
                else "call_recording.consent_declined"
            ),
            actor_user_id=current_user.user_id,
            call_session_id=call_session_id,
            metadata={
                "recording_id": recording_id,
                "consent_version": CONSENT_VERSION,
            },
        )
        ready = payload.consent and _all_joined_participants_consented(
            conn,
            recording_id=recording_id,
            call_session_id=call_session_id,
        )
        should_stop = not payload.consent and recording["status"] in {
            "starting", "recording", "stopping"
        }
    _publish_committed_realtime_events_best_effort(queued)
    if ready:
        background_tasks.add_task(start_recording_egress, recording_id)
    if should_stop:
        background_tasks.add_task(
            stop_recording_egress,
            recording_id,
            "participant_revoked_consent",
        )
    return {"success": True, "consent": payload.consent, "recording_id": recording_id}


@router.delete("/calls/{call_session_id}/recordings/{recording_id}", status_code=202)
def stop_call_recording(
    background_tasks: BackgroundTasks,
    call_session_id: int = Path(..., ge=1),
    recording_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    queued: list[dict[str, Any]] = []
    with get_db() as conn:
        call = _select_call(conn, call_session_id)
        communications.require_business_or_enterprise_organization(
            conn, int(call["organization_id"]), current_user
        )
        _require_joined_participant(
            conn,
            call_session_id=call_session_id,
            user_id=current_user.user_id,
        )
        recording = _load_recording(conn, recording_id, for_update=True)
        if int(recording["call_session_id"]) != call_session_id:
            raise HTTPException(status_code=404, detail="Recording was not found.")
        if recording["status"] not in OPEN_RECORDING_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "recording_not_open",
                    "message": "This recording is no longer active.",
                },
            )
        if recording["status"] == "requested":
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_recordings
                    SET status = 'cancelled', ended_at = NOW(),
                        failure_reason = 'user_cancelled_before_start'
                    WHERE id = %s AND status = 'requested'
                    """,
                    (recording_id,),
                )
            recording = _load_recording(conn, recording_id)
            queued.append(
                _recording_event(
                    conn,
                    call=call,
                    recording=recording,
                    event_type="call.recording.cancelled",
                    recipient_user_ids=[
                        str(item["user_id"])
                        for item in _fetch_participants(conn, call_session_id)
                    ],
                    event_id_suffix="user_cancelled_before_start",
                )
            )
        insert_team_audit_event(
            conn,
            organization_id=int(call["organization_id"]),
            event_type="call_recording.stop_requested",
            actor_user_id=current_user.user_id,
            call_session_id=call_session_id,
            metadata={"recording_id": recording_id},
        )
    _publish_committed_realtime_events_best_effort(queued)
    if recording["status"] != "cancelled":
        background_tasks.add_task(stop_recording_egress, recording_id, "user_stopped")
    return {"success": True, "recording": _recording_to_public(recording)}


@router.get("/calls/{call_session_id}/recordings")
def list_call_recordings(
    call_session_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        call = _select_call(conn, call_session_id)
        access = communications.require_business_or_enterprise_organization(
            conn, int(call["organization_id"]), current_user
        )
        participant = _select_participant(conn, call_session_id, current_user.user_id)
        role = str(access["membership"].get("role") or "")
        if not _has_participated_in_call(participant) and role not in {
            "owner",
            "admin",
        }:
            raise HTTPException(status_code=403, detail="Call participation is required.")
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {RECORDING_COLUMNS}
                FROM call_recordings
                WHERE call_session_id = %s AND status <> 'deleted'
                ORDER BY id DESC
                """,
                (call_session_id,),
            )
            recordings = [_recording_to_public(row) for row in cur.fetchall()]
    return {"success": True, "recordings": recordings}


@router.get("/calls/{call_session_id}/recordings/{recording_id}/download")
def download_call_recording(
    call_session_id: int = Path(..., ge=1),
    recording_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    with get_db() as conn:
        call = _select_call(conn, call_session_id)
        access = communications.require_business_or_enterprise_organization(
            conn, int(call["organization_id"]), current_user
        )
        participant = _select_participant(conn, call_session_id, current_user.user_id)
        role = str(access["membership"].get("role") or "")
        if not _has_participated_in_call(participant) and role not in {
            "owner",
            "admin",
        }:
            raise HTTPException(status_code=403, detail="Call participation is required.")
        recording = _load_recording(conn, recording_id)
        if (
            int(recording["call_session_id"]) != call_session_id
            or recording["status"] != "completed"
            or not recording.get("storage_key")
            or recording.get("deleted_at")
        ):
            raise HTTPException(status_code=404, detail="Recording is not available.")
        insert_team_audit_event(
            conn,
            organization_id=int(call["organization_id"]),
            event_type="call_recording.downloaded",
            actor_user_id=current_user.user_id,
            call_session_id=call_session_id,
            metadata={"recording_id": recording_id},
        )

    client = _boto3_client()
    config = _s3_config()
    try:
        response = client.get_object(
            Bucket=config["bucket"], Key=str(recording["storage_key"])
        )
    except Exception as exc:
        logger.exception("Could not download call recording %s.", recording_id)
        raise HTTPException(status_code=503, detail="Recording storage is unavailable.") from exc

    body = response["Body"]

    def stream():
        try:
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    filename = f"redocx-call-{call_session_id}-recording-{recording_id}.ogg"
    return StreamingResponse(
        stream(),
        media_type="audio/ogg",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


async def reconcile_recording_for_room(room_name: str) -> None:
    """Start a fully-consented request or stop when fewer than two remain."""

    normalized_room_name = str(room_name or "").strip()
    if not normalized_room_name:
        return

    def _plan() -> tuple[str, int] | None:
        with get_db() as conn:
            call = _select_call_by_room(conn, normalized_room_name)
            if call is None:
                return None
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
                row = cur.fetchone()
            if row is None:
                return None
            recording_id, status = int(row[0]), str(row[1])
            joined_count = len(_joined_user_ids(conn, int(call["id"])))
            if status == "requested" and call["status"] != "active":
                return "stop", recording_id
            if status == "requested" and _all_joined_participants_consented(
                conn,
                recording_id=recording_id,
                call_session_id=int(call["id"]),
            ):
                return "start", recording_id
            if status == "stopping":
                return "stop", recording_id
            if status in {"starting", "recording"} and (
                call["status"] != "active" or joined_count < 2
            ):
                return "stop", recording_id
            return None

    action = await anyio.to_thread.run_sync(_plan)
    if action is None:
        return
    action_name, recording_id = action
    if action_name == "start":
        await start_recording_egress(recording_id)
    else:
        await stop_recording_egress(
            recording_id,
            "fewer_than_two_participants",
        )


async def handle_livekit_egress_event(event: Any) -> dict[str, Any] | None:
    event_type = str(getattr(event, "event", "") or "").strip().lower()
    if event_type not in {"egress_started", "egress_updated", "egress_ended"}:
        return None
    info = getattr(event, "egress_info", None)
    egress_id = str(getattr(info, "egress_id", "") or "").strip()
    if not egress_id:
        return {"ignored": True, "reason": "missing_egress_id"}
    event_id = str(getattr(event, "id", "") or "").strip()
    if not event_id:
        return {"ignored": True, "reason": "missing_event_id"}
    event_at = _provider_event_datetime(getattr(event, "created_at", 0))

    status_text = str(getattr(info, "status", "") or "").lower()
    error_text = str(getattr(info, "error", "") or "").strip()
    file_results = list(getattr(info, "file_results", None) or [])
    file_size = None
    if file_results:
        try:
            file_size = int(getattr(file_results[0], "size", 0) or 0)
        except (TypeError, ValueError):
            file_size = None

    def _persist() -> (
        tuple[
            dict[str, Any],
            dict[str, Any],
            list[str],
            dict[str, Any] | None,
        ]
        | dict[str, Any]
        | None
    ):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM call_recordings WHERE egress_id = %s FOR UPDATE",
                    (egress_id,),
                )
                row = cur.fetchone()
            if row is None:
                return None
            recording_id = int(row[0])
            recording_before = _load_recording(conn, recording_id)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO livekit_webhook_events (
                        event_id, event_type, provider_created_at,
                        call_session_id, processing_status
                    )
                    VALUES (%s, %s, %s, %s, 'received')
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        event_id,
                        event_type,
                        event_at,
                        recording_before["call_session_id"],
                    ),
                )
                webhook_row = cur.fetchone()
            if webhook_row is None:
                return {"duplicate": True, "event_id": event_id}
            if recording_before["status"] in {
                "completed",
                "cancelled",
                "purging",
                "deleted",
            } or (
                recording_before["status"] == "failed"
                and event_type != "egress_ended"
            ):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE livekit_webhook_events
                        SET processing_status = 'ignored',
                            processing_detail = 'terminal_recording_state'
                        WHERE id = %s
                        """,
                        (webhook_row[0],),
                    )
                return {
                    "ignored": True,
                    "reason": "terminal_recording_state",
                    "event_id": event_id,
                }
            completed = event_type == "egress_ended" and not error_text and "failed" not in status_text
            next_status = (
                "completed"
                if completed
                else "failed"
                if event_type == "egress_ended" and (error_text or "failed" in status_text)
                else "recording"
            )
            if (
                event_type != "egress_ended"
                and recording_before["status"] == "stopping"
            ):
                next_status = "stopping"
            _, retention_days = _recording_policy(
                conn, int(recording_before["organization_id"])
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE call_recordings
                    SET status = %s,
                        started_at = CASE
                            WHEN %s = 'recording' THEN COALESCE(started_at, NOW())
                            ELSE started_at
                        END,
                        ended_at = CASE
                            WHEN %s IN ('completed', 'failed') THEN COALESCE(ended_at, NOW())
                            ELSE ended_at
                        END,
                        file_size_bytes = COALESCE(%s, file_size_bytes),
                        failure_reason = CASE
                            WHEN %s = 'failed' THEN NULLIF(%s, '')
                            ELSE failure_reason
                        END,
                        retention_expires_at = CASE
                            WHEN %s = 'completed'
                            THEN NOW() + (%s * INTERVAL '1 day')
                            ELSE retention_expires_at
                        END
                    WHERE id = %s
                    """,
                    (
                        next_status,
                        next_status,
                        next_status,
                        file_size,
                        next_status,
                        (error_text or status_text or "egress_failed")[:500],
                        next_status,
                        retention_days,
                        recording_id,
                    ),
                )
                cur.execute(
                    """
                    UPDATE livekit_webhook_events
                    SET processing_status = 'processed',
                        processing_detail = %s
                    WHERE id = %s
                    """,
                    (next_status, webhook_row[0]),
                )
            recording = _load_recording(conn, recording_id)
            call = _select_call(conn, int(recording["call_session_id"]))
            recipients = [
                str(item["user_id"])
                for item in _fetch_participants(conn, int(call["id"]))
            ]
            outbound_type = (
                "call.recording.completed"
                if next_status == "completed"
                else "call.recording.failed"
                if next_status == "failed"
                else None
                if next_status == "stopping"
                else "call.recording.started"
                if recording_before["status"] == "starting"
                else None
            )
            queued = (
                _recording_event(
                    conn,
                    call=call,
                    recording=recording,
                    event_type=outbound_type,
                    recipient_user_ids=recipients,
                )
                if outbound_type
                else None
            )
            return call, recording, recipients, queued

    persisted = await anyio.to_thread.run_sync(_persist)
    if persisted is None:
        return {"ignored": True, "reason": "unknown_egress"}
    if isinstance(persisted, dict):
        return persisted
    _, recording, _, queued = persisted
    if queued is not None:
        _publish_committed_realtime_events_best_effort([queued])
    return {"processed": True, "recording_id": int(recording["id"])}


def purge_expired_call_recordings_sync(limit: int = 25) -> int:
    # Recover claims abandoned by a crashed worker before claiming a fresh
    # batch. S3 deletion remains idempotent, and every object is reauthorized
    # under a policy-row lock immediately before deletion.
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE call_recordings
                SET status = 'completed',
                    failure_reason = 'recovered_abandoned_purge_claim'
                WHERE status = 'purging'
                  AND updated_at < NOW() - INTERVAL '1 hour'
                """,
            )
            cur.execute(
                """
                WITH candidates AS (
                    SELECT recording.id
                    FROM call_recordings recording
                    LEFT JOIN organization_communication_policies policy
                      ON policy.organization_id = recording.organization_id
                    WHERE recording.status = 'completed'
                      AND recording.deleted_at IS NULL
                      AND recording.retention_expires_at <= NOW()
                      AND COALESCE(policy.legal_hold, FALSE) = FALSE
                    ORDER BY recording.retention_expires_at ASC, recording.id ASC
                    FOR UPDATE OF recording SKIP LOCKED
                    LIMIT %s
                )
                UPDATE call_recordings recording
                SET status = 'purging', failure_reason = NULL
                FROM candidates
                WHERE recording.id = candidates.id
                RETURNING recording.id, recording.organization_id,
                          recording.storage_key
                """,
                (max(1, min(limit, 100)),),
            )
            rows = [
                (int(row[0]), int(row[1]), str(row[2] or ""))
                for row in cur.fetchall()
            ]
    if not rows:
        return 0
    client = _boto3_client()
    config = _s3_config()
    deleted = 0
    for recording_id, organization_id, storage_key in rows:
        if not storage_key:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'completed',
                            failure_reason = 'missing_storage_key'
                        WHERE id = %s AND status = 'purging'
                        """,
                        (recording_id,),
                    )
            continue
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO organization_communication_policies (
                            organization_id
                        )
                        VALUES (%s)
                        ON CONFLICT (organization_id) DO NOTHING
                        """,
                        (organization_id,),
                    )
                    cur.execute(
                        """
                        SELECT recording.status, policy.legal_hold,
                               recording.retention_expires_at <= NOW()
                        FROM call_recordings recording
                        JOIN organization_communication_policies policy
                          ON policy.organization_id = recording.organization_id
                        WHERE recording.id = %s
                        FOR UPDATE OF recording, policy
                        """,
                        (recording_id,),
                    )
                    state = cur.fetchone()
                    if state is None or state[0] != "purging":
                        continue
                    if bool(state[1]) or not bool(state[2]):
                        cur.execute(
                            """
                            UPDATE call_recordings
                            SET status = 'completed',
                                failure_reason = 'purge_blocked_by_policy'
                            WHERE id = %s AND status = 'purging'
                            """,
                            (recording_id,),
                        )
                        continue
                    # The policy row remains locked until the object deletion
                    # and database transition commit, so legal hold cannot race
                    # this final authorization check.
                    client.delete_object(Bucket=config["bucket"], Key=storage_key)
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'deleted', deleted_at = NOW()
                        WHERE id = %s AND status = 'purging'
                        """,
                        (recording_id,),
                    )
            deleted += 1
        except Exception as exc:
            logger.exception("Could not purge expired call recording %s.", recording_id)
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE call_recordings
                        SET status = 'completed', failure_reason = %s
                        WHERE id = %s AND status = 'purging'
                        """,
                        (f"purge_failed:{type(exc).__name__}"[:500], recording_id),
                    )
    return deleted


_RETENTION_TASK: asyncio.Task[None] | None = None
_RECONCILIATION_TASK: asyncio.Task[None] | None = None
_RETENTION_STOP = asyncio.Event()
_RECONCILIATION_STOP = asyncio.Event()


async def _retention_loop() -> None:
    while not _RETENTION_STOP.is_set():
        try:
            await anyio.to_thread.run_sync(purge_expired_call_recordings_sync)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call-recording retention sweep failed.")
        try:
            await asyncio.wait_for(
                _RETENTION_STOP.wait(), timeout=RECORDING_RETENTION_SWEEP_SECONDS
            )
        except asyncio.TimeoutError:
            pass


def _open_recording_room_names(limit: int = 250) -> list[str]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT call.livekit_room_name
                FROM call_recordings recording
                JOIN call_sessions call ON call.id = recording.call_session_id
                WHERE recording.status IN ('requested', 'starting', 'recording', 'stopping')
                  AND NULLIF(BTRIM(call.livekit_room_name), '') IS NOT NULL
                ORDER BY call.livekit_room_name
                LIMIT %s
                """,
                (max(1, min(limit, 1000)),),
            )
            return [str(row[0]) for row in cur.fetchall()]


async def _reconciliation_loop() -> None:
    interval = _bounded_env_int(
        "CALL_RECORDING_RECONCILE_SECONDS",
        RECORDING_RECONCILE_SECONDS,
        5,
        60,
    )
    while not _RECONCILIATION_STOP.is_set():
        try:
            room_names = await anyio.to_thread.run_sync(_open_recording_room_names)
            for room_name in room_names:
                await reconcile_recording_for_room(room_name)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Call-recording lifecycle reconciliation failed.")
        try:
            await asyncio.wait_for(_RECONCILIATION_STOP.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def start_call_recording_services() -> None:
    global _RETENTION_TASK, _RECONCILIATION_TASK
    if _RETENTION_TASK is not None or _RECONCILIATION_TASK is not None:
        return
    _RETENTION_STOP.clear()
    _RECONCILIATION_STOP.clear()
    _RETENTION_TASK = asyncio.create_task(
        _retention_loop(), name="team-call-recording-retention"
    )
    _RECONCILIATION_TASK = asyncio.create_task(
        _reconciliation_loop(), name="team-call-recording-reconciliation"
    )


async def stop_call_recording_services() -> None:
    global _RETENTION_TASK, _RECONCILIATION_TASK
    _RETENTION_STOP.set()
    _RECONCILIATION_STOP.set()
    tasks = [
        task
        for task in (_RETENTION_TASK, _RECONCILIATION_TASK)
        if task is not None
    ]
    _RETENTION_TASK = None
    _RECONCILIATION_TASK = None
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = [
    "router",
    "handle_livekit_egress_event",
    "reconcile_recording_for_room",
    "start_call_recording_services",
    "stop_call_recording_services",
]
