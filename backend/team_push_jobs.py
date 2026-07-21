from __future__ import annotations

"""Deliver durable team-message browser notifications to offline users.

Requires ``redis`` and ``pywebpush``. Schedule once per minute or run as a
long-lived worker. Delivery is suppressed while an authoritative organization
realtime lease exists for the recipient.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from typing import Any

from backend.database import get_db

logger = logging.getLogger(__name__)


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
                          item.payload, item.attempts
                """,
                (limit,),
            )
            return [
                {
                    "id": int(row[0]),
                    "organization_id": int(row[1]),
                    "recipient_user_id": str(row[2]),
                    "event_type": str(row[3]),
                    "payload": row[4] if isinstance(row[4], dict) else {},
                    "attempts": int(row[5]),
                }
                for row in cur.fetchall()
            ]


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


def _mark(item_id: int, *, status: str, error: str | None = None, retry_seconds: int = 0) -> None:
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


def _localized_payload(payload: dict[str, Any], locale: str) -> dict[str, Any]:
    body_preview = str(payload.get("body_preview") or "").strip()
    is_fr = locale == "fr"
    return {
        "title": "Nouveau message ReDOCX" if is_fr else "New ReDOCX message",
        "body": body_preview or ("Vous avez reçu un message d’équipe." if is_fr else "You received a team message."),
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


async def deliver_pending_notifications(*, limit: int = 100, max_attempts: int = 10) -> dict[str, int]:
    redis_url = os.getenv("TEAM_REALTIME_REDIS_URL", "").strip() or os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        raise RuntimeError("TEAM_REALTIME_REDIS_URL or REDIS_URL is required for offline push suppression.")
    vapid_private_key = os.getenv("WEB_PUSH_VAPID_PRIVATE_KEY", "").strip()
    vapid_subject = os.getenv("WEB_PUSH_VAPID_SUBJECT", "").strip()
    if not vapid_private_key or not vapid_subject:
        raise RuntimeError("WEB_PUSH_VAPID_PRIVATE_KEY and WEB_PUSH_VAPID_SUBJECT are required.")

    try:
        import redis.asyncio as redis_asyncio
        from pywebpush import WebPushException, webpush
    except ImportError as exc:
        raise RuntimeError("Install redis and pywebpush for the notification worker.") from exc

    claimed = _claim(limit)
    delivered = suppressed = deferred = dead = 0
    redis_client = redis_asyncio.from_url(redis_url, decode_responses=True)
    try:
        for item in claimed:
            try:
                if _already_read(item):
                    _mark(item["id"], status="suppressed", error="Message already read")
                    suppressed += 1
                    continue

                if await _is_online(redis_client, item):
                    _mark(item["id"], status="suppressed", error="Recipient has an active realtime lease")
                    suppressed += 1
                    continue

                subscriptions = _subscriptions(item["recipient_user_id"])
                if not subscriptions:
                    _mark(item["id"], status="dead", error="No active push subscription")
                    dead += 1
                    continue

                successes = 0
                errors: list[str] = []
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
                                _localized_payload(item["payload"], subscription["locale"]),
                                separators=(",", ":"),
                            ),
                            vapid_private_key=vapid_private_key,
                            vapid_claims={"sub": vapid_subject},
                            ttl=300,
                        )
                        successes += 1
                    except WebPushException as exc:
                        status_code = getattr(getattr(exc, "response", None), "status_code", None)
                        if status_code in {404, 410}:
                            _expire_subscription(subscription["endpoint"])
                        errors.append(str(exc))

                if successes:
                    _mark(item["id"], status="delivered", error="; ".join(errors) or None)
                    delivered += 1
                elif item["attempts"] >= max_attempts:
                    _mark(item["id"], status="dead", error="; ".join(errors) or "Delivery failed")
                    dead += 1
                else:
                    delay = min(3600, 30 * (2 ** min(item["attempts"], 7)))
                    _mark(item["id"], status="pending", error="; ".join(errors), retry_seconds=delay)
                    deferred += 1
            except Exception as exc:
                logger.exception("Push delivery failed for outbox item %s.", item["id"])
                if item["attempts"] >= max_attempts:
                    _mark(item["id"], status="dead", error=str(exc))
                    dead += 1
                else:
                    _mark(item["id"], status="pending", error=str(exc), retry_seconds=120)
                    deferred += 1
    finally:
        await redis_client.aclose()

    return {"claimed": len(claimed), "delivered": delivered, "suppressed": suppressed, "deferred": deferred, "dead": dead}


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver ReDOCX offline team notifications.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(asyncio.run(deliver_pending_notifications(limit=max(1, min(args.limit, 1000)))))


if __name__ == "__main__":
    main()
