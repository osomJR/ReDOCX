from __future__ import annotations

"""Deliver durable team-message and ringing-call browser notifications.

Requires ``redis`` and ``pywebpush``. Message notifications are suppressed once
read and while the recipient owns an active organization realtime lease. Call
notifications remain eligible while the recipient is still invited and the
call is within its authoritative ringing window, including when an application
tab is connected but backgrounded.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import time
from typing import Any, Iterable

from backend.database import get_db

logger = logging.getLogger(__name__)


def _rows_to_items(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [
        {
            "id": int(row[0]),
            "organization_id": int(row[1]),
            "recipient_user_id": str(row[2]),
            "event_type": str(row[3]),
            "event_key": str(row[4]),
            "payload": row[5] if isinstance(row[5], dict) else {},
            "attempts": int(row[6]),
        }
        for row in rows
    ]


def _claim(limit: int) -> list[dict[str, Any]]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id
                    FROM team_notification_outbox
                    WHERE status = 'pending'
                      AND available_at <= NOW()
                      AND (locked_at IS NULL OR locked_at < NOW() - INTERVAL '5 minutes')
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE team_notification_outbox item
                SET locked_at = NOW(), attempts = attempts + 1
                FROM claimed
                WHERE item.id = claimed.id
                RETURNING item.id, item.organization_id,
                          item.recipient_user_id, item.event_type,
                          item.event_key, item.payload, item.attempts
                """,
                (limit,),
            )
            return _rows_to_items(cur.fetchall())


def _claim_event_keys(event_keys: Iterable[str]) -> list[dict[str, Any]]:
    normalized = sorted({str(value).strip() for value in event_keys if str(value).strip()})
    if not normalized:
        return []

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id
                    FROM team_notification_outbox
                    WHERE event_key = ANY(%s)
                      AND status = 'pending'
                      AND available_at <= NOW()
                      AND (locked_at IS NULL OR locked_at < NOW() - INTERVAL '5 minutes')
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                )
                UPDATE team_notification_outbox item
                SET locked_at = NOW(), attempts = attempts + 1
                FROM claimed
                WHERE item.id = claimed.id
                RETURNING item.id, item.organization_id,
                          item.recipient_user_id, item.event_type,
                          item.event_key, item.payload, item.attempts
                """,
                (normalized,),
            )
            return _rows_to_items(cur.fetchall())


def _subscriptions(user_id: str) -> list[dict[str, str]]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT endpoint, p256dh, auth_secret, locale
                FROM user_push_subscriptions
                WHERE user_id = %s AND status = 'active'
                """,
                (user_id,),
            )
            return [
                {
                    "endpoint": str(row[0]),
                    "p256dh": str(row[1]),
                    "auth": str(row[2]),
                    "locale": str(row[3] or "en"),
                }
                for row in cur.fetchall()
            ]


def _mark(
    item_id: int,
    *,
    status: str,
    error: str | None = None,
    retry_seconds: int = 0,
) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            if status == "pending":
                cur.execute(
                    """
                    UPDATE team_notification_outbox
                    SET status = 'pending', locked_at = NULL,
                        available_at = NOW() + MAKE_INTERVAL(secs => %s),
                        last_error = %s
                    WHERE id = %s
                    """,
                    (retry_seconds, (error or "")[:1000] or None, item_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE team_notification_outbox
                    SET status = %s, locked_at = NULL,
                        delivered_at = CASE WHEN %s = 'delivered' THEN NOW() ELSE delivered_at END,
                        suppressed_at = CASE WHEN %s = 'suppressed' THEN NOW() ELSE suppressed_at END,
                        last_error = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        status,
                        status,
                        (error or "")[:1000] or None,
                        item_id,
                    ),
                )


def _expire_subscription(endpoint: str) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_push_subscriptions
                SET status = 'expired', updated_at = NOW()
                WHERE endpoint = %s
                """,
                (endpoint,),
            )


def _already_read(item: dict[str, Any]) -> bool:
    if item.get("event_type") != "message.created":
        return False

    message_id = int(item.get("payload", {}).get("message_id") or 0)
    conversation_id = int(item.get("payload", {}).get("conversation_id") or 0)
    if message_id < 1 or conversation_id < 1:
        return False
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(last_read_message_id, 0) >= %s
                FROM conversation_read_state
                WHERE conversation_id = %s AND user_id = %s
                """,
                (message_id, conversation_id, item["recipient_user_id"]),
            )
            row = cur.fetchone()
    return bool(row and row[0])


def _presence_key(channel: str, organization_id: int, user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    return f"{channel}:connections:organization:{organization_id}:{digest}"


async def _is_online(redis_client: Any, item: dict[str, Any]) -> bool:
    key = _presence_key(
        os.getenv("TEAM_REALTIME_REDIS_CHANNEL", "redocx:team-realtime:v1").strip()
        or "redocx:team-realtime:v1",
        item["organization_id"],
        item["recipient_user_id"],
    )
    now = time.time()
    async with redis_client.pipeline(transaction=True) as pipe:
        pipe.zremrangebyscore(key, "-inf", now)
        pipe.zcard(key)
        result = await pipe.execute()
    return int(result[1] or 0) > 0


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _remaining_call_seconds(item: dict[str, Any]) -> int | None:
    if item.get("event_type") != "call.started":
        return None
    expires_at = _parse_datetime(item.get("payload", {}).get("ringing_expires_at"))
    if expires_at is None:
        return 90
    return max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))


def _call_notification_is_current(item: dict[str, Any]) -> bool:
    """Return whether this recipient can still act on the ringing call."""

    if item.get("event_type") != "call.started":
        return True

    payload = item.get("payload") or {}
    try:
        call_session_id = int(payload.get("call_session_id") or 0)
    except (TypeError, ValueError):
        return False
    recipient_user_id = str(item.get("recipient_user_id") or "").strip()
    if call_session_id < 1 or not recipient_user_id:
        return False

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM call_sessions call
                    JOIN call_participants participant
                      ON participant.call_session_id = call.id
                     AND participant.user_id = %s
                    WHERE call.id = %s
                      AND call.status IN ('ringing', 'active')
                      AND call.ringing_expires_at > NOW()
                      AND participant.status = 'invited'
                )
                """,
                (recipient_user_id, call_session_id),
            )
            row = cur.fetchone()
    return bool(row and row[0])


def _localized_payload(item: dict[str, Any], locale: str) -> dict[str, Any]:
    payload = item.get("payload") or {}
    is_fr = locale == "fr"

    if item.get("event_type") == "call.started":
        media_type = "audio" if payload.get("media_type") == "audio" else "video"
        caller_name = str(payload.get("caller_name") or "").strip()
        fallback_caller = "Un membre de l’équipe" if is_fr else "A team member"
        caller = caller_name or fallback_caller
        media_label = (
            "audio" if is_fr and media_type == "audio" else
            "vidéo" if is_fr else
            media_type
        )
        call_session_id = payload.get("call_session_id")
        conversation_id = payload.get("conversation_id")
        join_url = (
            f"/team?conversationId={conversation_id}"
            f"&callSessionId={call_session_id}&callAction=join"
            f"&mediaType={media_type}"
        )
        return {
            "title": "Appel ReDOCX entrant" if is_fr else "Incoming ReDOCX call",
            "body": (
                f"{caller} démarre un appel {media_label}."
                if is_fr
                else f"{caller} is starting {'an' if media_type == 'audio' else 'a'} {media_label} call."
            ),
            "icon": "/icons/icon-192.png",
            "badge": "/icons/badge-72.png",
            "tag": f"redocx-call-{call_session_id}",
            "renotify": True,
            "requireInteraction": True,
            "actions": [
                {"action": "join", "title": "Rejoindre" if is_fr else "Join"},
                {"action": "decline", "title": "Refuser" if is_fr else "Decline"},
            ],
            "data": {
                "url": join_url,
                "join_url": join_url,
                "decline_url": f"/api/calls/{call_session_id}/decline",
                **payload,
            },
        }

    body_preview = str(payload.get("body_preview") or "").strip()
    return {
        "title": "Nouveau message ReDOCX" if is_fr else "New ReDOCX message",
        "body": body_preview or (
            "Vous avez reçu un message d’équipe."
            if is_fr
            else "You received a team message."
        ),
        "icon": "/icons/icon-192.png",
        "badge": "/icons/badge-72.png",
        "data": {
            "url": (
                f"/team?conversationId={payload.get('conversation_id')}"
                f"&messageId={payload.get('message_id')}"
            ),
            **payload,
        },
    }


def _retry_delay(item: dict[str, Any]) -> int | None:
    remaining = _remaining_call_seconds(item)
    if remaining is not None:
        if remaining <= 1:
            return None
        return min(5, max(1, remaining - 1))
    return min(3600, 30 * (2 ** min(item["attempts"], 7)))


async def _deliver_claimed(
    claimed: list[dict[str, Any]],
    *,
    max_attempts: int,
) -> dict[str, int]:
    redis_url = (
        os.getenv("TEAM_REALTIME_REDIS_URL", "").strip()
        or os.getenv("REDIS_URL", "").strip()
    )
    if not redis_url:
        raise RuntimeError(
            "TEAM_REALTIME_REDIS_URL or REDIS_URL is required for offline push suppression."
        )
    vapid_private_key = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "").strip()
    vapid_subject = os.getenv("WEB_PUSH_VAPID_SUBJECT", "").strip()
    if not vapid_private_key or not vapid_subject:
        raise RuntimeError(
            "WEB_PUSH_VAPID_PRIVATE_KEY and WEB_PUSH_VAPID_SUBJECT are required."
        )

    try:
        import redis.asyncio as redis_asyncio
        from pywebpush import WebPushException, webpush
    except ImportError as exc:
        raise RuntimeError(
            "Install redis and pywebpush for the notification worker."
        ) from exc

    delivered = suppressed = deferred = dead = 0
    redis_client = redis_asyncio.from_url(redis_url, decode_responses=True)
    try:
        for item in claimed:
            try:
                remaining_call_seconds = _remaining_call_seconds(item)
                if remaining_call_seconds is not None:
                    if remaining_call_seconds <= 0 or not _call_notification_is_current(item):
                        _mark(
                            item["id"],
                            status="suppressed",
                            error="Call is no longer actionable for this recipient",
                        )
                        suppressed += 1
                        continue

                if _already_read(item):
                    _mark(item["id"], status="suppressed", error="Message already read")
                    suppressed += 1
                    continue

                if remaining_call_seconds is None and await _is_online(redis_client, item):
                    _mark(
                        item["id"],
                        status="suppressed",
                        error="Recipient has an active realtime lease",
                    )
                    suppressed += 1
                    continue

                subscriptions = _subscriptions(item["recipient_user_id"])
                if not subscriptions:
                    _mark(item["id"], status="dead", error="No active push subscription")
                    dead += 1
                    continue

                successes = 0
                errors: list[str] = []
                ttl = min(300, max(5, remaining_call_seconds or 300))
                for subscription in subscriptions:
                    try:
                        await asyncio.to_thread(
                            webpush,
                            subscription_info={
                                "endpoint": subscription["endpoint"],
                                "keys": {
                                    "p256dh": subscription["p256dh"],
                                    "auth": subscription["auth"],
                                },
                            },
                            data=json.dumps(
                                _localized_payload(item, subscription["locale"]),
                                separators=(",", ":"),
                            ),
                            vapid_private_key=vapid_private_key,
                            vapid_claims={"sub": vapid_subject},
                            ttl=ttl,
                        )
                        successes += 1
                    except WebPushException as exc:
                        status_code = getattr(
                            getattr(exc, "response", None), "status_code", None
                        )
                        if status_code in {404, 410}:
                            _expire_subscription(subscription["endpoint"])
                        errors.append(str(exc))

                if successes:
                    _mark(
                        item["id"],
                        status="delivered",
                        error="; ".join(errors) or None,
                    )
                    delivered += 1
                    continue

                retry_seconds = _retry_delay(item)
                if item["attempts"] >= max_attempts or retry_seconds is None:
                    _mark(
                        item["id"],
                        status="dead",
                        error="; ".join(errors) or "Delivery failed",
                    )
                    dead += 1
                else:
                    _mark(
                        item["id"],
                        status="pending",
                        error="; ".join(errors),
                        retry_seconds=retry_seconds,
                    )
                    deferred += 1
            except Exception as exc:
                logger.exception("Push delivery failed for outbox item %s.", item["id"])
                retry_seconds = _retry_delay(item)
                if item["attempts"] >= max_attempts or retry_seconds is None:
                    _mark(item["id"], status="dead", error=str(exc))
                    dead += 1
                else:
                    _mark(
                        item["id"],
                        status="pending",
                        error=str(exc),
                        retry_seconds=retry_seconds,
                    )
                    deferred += 1
    finally:
        await redis_client.aclose()

    return {
        "claimed": len(claimed),
        "delivered": delivered,
        "suppressed": suppressed,
        "deferred": deferred,
        "dead": dead,
    }


async def deliver_pending_notifications(
    *,
    limit: int = 100,
    max_attempts: int = 10,
) -> dict[str, int]:
    return await _deliver_claimed(_claim(limit), max_attempts=max_attempts)


async def deliver_notification_event_keys(
    event_keys: Iterable[str],
    *,
    max_attempts: int = 10,
) -> dict[str, int]:
    return await _deliver_claimed(
        _claim_event_keys(event_keys),
        max_attempts=max_attempts,
    )


def deliver_notification_event_keys_sync(event_keys: Iterable[str]) -> dict[str, int]:
    return asyncio.run(deliver_notification_event_keys(event_keys))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deliver ReDOCX offline team notifications."
    )
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(
        asyncio.run(
            deliver_pending_notifications(limit=max(1, min(args.limit, 1000)))
        )
    )


if __name__ == "__main__":
    main()
