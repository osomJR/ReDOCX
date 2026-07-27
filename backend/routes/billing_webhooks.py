from __future__ import annotations

"""
Verified billing webhook routes for ReDOCX.

Security model:
- The frontend never grants paid plans.
- Checkout success pages never grant paid plans.
- Only a verified payment-provider webhook can activate, cancel, or mark a
  subscription past-due.
- Provider events are recorded idempotently before entitlement updates so
  repeated webhook delivery cannot duplicate work.

Route:
    POST /api/v1/billing/webhooks/{provider_name}

Supported provider names are resolved by backend.billing_provider:
    static, generic, stripe, paystack, flutterwave

The generic provider allows any additional gateway that can send a JSON webhook
with an HMAC signature and standard metadata fields such as user_id and
 target_plan.
"""

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb

from backend.billing_provider import (
    BillingProviderError,
    BillingWebhookEvent,
    WebhookVerificationError,
    verify_provider_webhook,
)
from backend.database import get_db
from backend.account_lifecycle import (
    PURGED_STATUS,
    RESTORABLE_STATUSES,
    get_account_lifecycle,
)
from backend.subscriptions import (
    create_organization,
    normalize_organization_name,
    upsert_organization_member,
    upsert_organization_subscription,
    upsert_user_subscription,
)

router = APIRouter(prefix="/billing/webhooks", tags=["billing-webhooks"])

PAID_PLANS = {"personal", "business", "enterprise"}
ORGANIZATION_PLANS = {"business", "enterprise"}


class BillingWebhookProcessingError(RuntimeError):
    pass


def _json_safe_event_payload(event: BillingWebhookEvent) -> dict[str, Any]:
    payload = asdict(event)
    for key in ("current_period_start", "current_period_end"):
        value = payload.get(key)
        if value is not None:
            payload[key] = value.isoformat()
    return payload


def _event_status_for_action(action: str) -> str:
    if action == "activate":
        return "active"
    if action == "cancel":
        return "cancelled"
    if action == "past_due":
        return "past_due"
    return "inactive"


def _normalize_user_id(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise BillingWebhookProcessingError(
            "Verified billing event is missing user_id. Ensure checkout metadata includes user_id."
        )
    return normalized


def _normalize_plan(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in PAID_PLANS:
        raise BillingWebhookProcessingError(
            "Verified billing event is missing a paid target_plan. Ensure checkout metadata includes target_plan."
        )
    return normalized


def _organization_name_for_event(event: BillingWebhookEvent, owner_user_id: str) -> str:
    if event.organization_name and event.organization_name.strip():
        return normalize_organization_name(event.organization_name)
    if event.email and event.email.strip():
        return normalize_organization_name(
            f"{event.email.split('@', 1)[0]}'s ReDOCX Team"
        )
    return normalize_organization_name(f"{owner_user_id}'s ReDOCX Team")


def _insert_provider_event(conn, event: BillingWebhookEvent) -> int | None:
    """
    Insert a verified provider event.

    Returns the new ledger row id. Returns None when this provider/event_id was
    already processed or is already being processed.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO billing_provider_events (
                provider,
                provider_event_id,
                event_type,
                action,
                event_status,
                user_id,
                target_plan,
                organization_id,
                provider_customer_id,
                provider_subscription_id,
                provider_reference,
                payload,
                processing_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'processing')
            ON CONFLICT (provider, provider_event_id) DO NOTHING
            RETURNING id
            """,
            (
                event.provider,
                event.event_id,
                event.event_type,
                event.action,
                event.status,
                event.user_id,
                event.plan,
                event.organization_id,
                event.provider_customer_id,
                event.provider_subscription_id,
                event.provider_reference,
                Jsonb(_json_safe_event_payload(event)),
            ),
        )
        row = cur.fetchone()

    return int(row[0]) if row else None


def _mark_provider_event(
    conn,
    event_row_id: int,
    *,
    processing_status: Literal["processed", "ignored", "failed"],
    message: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE billing_provider_events
            SET processing_status = %s,
                processing_message = %s,
                processed_at = CASE
                    WHEN %s IN ('processed', 'ignored') THEN NOW()
                    ELSE processed_at
                END,
                updated_at = NOW()
            WHERE id = %s
            """,
            (processing_status, message, processing_status, event_row_id),
        )


def _update_period_fields(
    conn,
    *,
    table_name: Literal["user_subscriptions", "organization_subscriptions"],
    owner_column: Literal["user_id", "organization_id"],
    owner_value: str | int,
    event: BillingWebhookEvent,
) -> None:
    if event.current_period_start is None and event.current_period_end is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {table_name}
            SET current_period_start = COALESCE(%s, current_period_start),
                current_period_end = COALESCE(%s, current_period_end),
                updated_at = NOW()
            WHERE {owner_column} = %s
            """,
            (event.current_period_start, event.current_period_end, owner_value),
        )


def _activate_personal_subscription(conn, event: BillingWebhookEvent) -> dict[str, Any]:
    user_id = _normalize_user_id(event.user_id)

    upsert_user_subscription(
        conn,
        user_id=user_id,
        plan="personal",
        account_count=1,
        status="active",
        provider=event.provider,
        provider_customer_id=event.provider_customer_id,
        provider_subscription_id=event.provider_subscription_id or event.provider_reference,
    )
    _update_period_fields(
        conn,
        table_name="user_subscriptions",
        owner_column="user_id",
        owner_value=user_id,
        event=event,
    )

    return {
        "subscription_scope": "user",
        "user_id": user_id,
        "plan": "personal",
    }


def _resolve_or_create_organization(conn, event: BillingWebhookEvent, *, owner_user_id: str) -> int:
    if event.organization_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                FROM organizations
                WHERE id = %s
                """,
                (event.organization_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise BillingWebhookProcessingError(
                f"Billing event references organization_id={event.organization_id}, but it does not exist."
            )
        return int(event.organization_id)

    return create_organization(
        conn,
        name=_organization_name_for_event(event, owner_user_id),
        owner_user_id=owner_user_id,
    )


def _activate_organization_subscription(conn, event: BillingWebhookEvent, *, plan: str) -> dict[str, Any]:
    owner_user_id = _normalize_user_id(event.user_id)
    organization_id = _resolve_or_create_organization(
        conn,
        event,
        owner_user_id=owner_user_id,
    )

    upsert_organization_member(
        conn,
        organization_id=organization_id,
        user_id=owner_user_id,
        role="owner",
        status="active",
    )
    upsert_organization_subscription(
        conn,
        organization_id=organization_id,
        plan=plan,  # type: ignore[arg-type]
        max_accounts=event.max_accounts,
        status="active",
        provider=event.provider,
        provider_customer_id=event.provider_customer_id,
        provider_subscription_id=event.provider_subscription_id or event.provider_reference,
    )
    _update_period_fields(
        conn,
        table_name="organization_subscriptions",
        owner_column="organization_id",
        owner_value=organization_id,
        event=event,
    )

    return {
        "subscription_scope": "organization",
        "organization_id": organization_id,
        "owner_user_id": owner_user_id,
        "plan": plan,
    }


def _activation_owner_user_ids(
    conn,
    event: BillingWebhookEvent,
) -> set[str]:
    user_ids: set[str] = set()
    if event.user_id and str(event.user_id).strip():
        user_ids.add(str(event.user_id).strip())

    if event.provider_subscription_id:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id
                FROM user_subscriptions
                WHERE provider = %s
                  AND provider_subscription_id = %s

                UNION

                SELECT organizations.owner_user_id
                FROM organization_subscriptions
                JOIN organizations
                  ON organizations.id =
                     organization_subscriptions.organization_id
                WHERE organization_subscriptions.provider = %s
                  AND organization_subscriptions.provider_subscription_id = %s
                """,
                (
                    event.provider,
                    event.provider_subscription_id,
                    event.provider,
                    event.provider_subscription_id,
                ),
            )
            user_ids.update(
                str(row[0]).strip()
                for row in cur.fetchall()
                if row and str(row[0] or "").strip()
            )

    if event.organization_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT owner_user_id
                FROM organizations
                WHERE id = %s
                """,
                (event.organization_id,),
            )
            row = cur.fetchone()
        if row and str(row[0] or "").strip():
            user_ids.add(str(row[0]).strip())

    return user_ids


def _deletion_lifecycle_blocking_activation(
    conn,
    event: BillingWebhookEvent,
) -> dict[str, Any] | None:
    for user_id in sorted(_activation_owner_user_ids(conn, event)):
        lifecycle = get_account_lifecycle(conn, user_id)
        lifecycle_status = (
            str(lifecycle.get("status") or "").strip().lower()
            if lifecycle
            else ""
        )
        if lifecycle_status in {*RESTORABLE_STATUSES, PURGED_STATUS}:
            return lifecycle
    return None


def _activate_subscription(conn, event: BillingWebhookEvent) -> dict[str, Any]:
    if _deletion_lifecycle_blocking_activation(conn, event) is not None:
        return {
            "subscription_scope": "none",
            "ignored": True,
            "reason": (
                "Subscription activation was ignored because the owning "
                "account is pending deletion or has already been purged."
            ),
        }

    plan = _normalize_plan(event.plan)

    if plan == "personal":
        return _activate_personal_subscription(conn, event)

    if plan in ORGANIZATION_PLANS:
        return _activate_organization_subscription(conn, event, plan=plan)

    raise BillingWebhookProcessingError(f"Unsupported paid plan: {plan}.")


def _set_user_subscription_status_by_event(conn, event: BillingWebhookEvent, *, status: str) -> int:
    updated = 0
    with conn.cursor() as cur:
        if event.provider_subscription_id:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = %s,
                    updated_at = NOW()
                WHERE provider = %s
                  AND provider_subscription_id = %s
                """,
                (status, event.provider, event.provider_subscription_id),
            )
            updated += cur.rowcount

        if updated == 0 and event.user_id:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = %s,
                    updated_at = NOW()
                WHERE user_id = %s
                  AND provider = %s
                """,
                (status, event.user_id, event.provider),
            )
            updated += cur.rowcount

    return updated


def _set_organization_subscription_status_by_event(conn, event: BillingWebhookEvent, *, status: str) -> int:
    updated = 0
    with conn.cursor() as cur:
        if event.provider_subscription_id:
            cur.execute(
                """
                UPDATE organization_subscriptions
                SET status = %s,
                    updated_at = NOW()
                WHERE provider = %s
                  AND provider_subscription_id = %s
                """,
                (status, event.provider, event.provider_subscription_id),
            )
            updated += cur.rowcount

        if updated == 0 and event.organization_id is not None:
            cur.execute(
                """
                UPDATE organization_subscriptions
                SET status = %s,
                    updated_at = NOW()
                WHERE organization_id = %s
                  AND provider = %s
                """,
                (status, event.organization_id, event.provider),
            )
            updated += cur.rowcount

    return updated


def _set_subscription_status(conn, event: BillingWebhookEvent, *, status: str) -> dict[str, Any]:
    user_count = _set_user_subscription_status_by_event(conn, event, status=status)
    organization_count = _set_organization_subscription_status_by_event(conn, event, status=status)

    return {
        "subscription_scope": "status_update",
        "status": status,
        "user_rows_updated": user_count,
        "organization_rows_updated": organization_count,
    }


def apply_verified_billing_event(conn, event: BillingWebhookEvent) -> dict[str, Any]:
    if event.action == "ignore":
        return {
            "subscription_scope": "none",
            "ignored": True,
            "reason": f"No entitlement action mapped for event_type={event.event_type}.",
        }

    if event.action == "activate":
        return _activate_subscription(conn, event)

    if event.action == "cancel":
        return _set_subscription_status(conn, event, status="cancelled")

    if event.action == "past_due":
        return _set_subscription_status(conn, event, status="past_due")

    raise BillingWebhookProcessingError(f"Unsupported billing webhook action: {event.action}.")


@router.post("/{provider_name}")
async def handle_billing_webhook(provider_name: str, request: Request) -> dict[str, Any]:
    raw_body = await request.body()

    try:
        event = verify_provider_webhook(provider_name, raw_body, request.headers)
    except WebhookVerificationError as exc:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_webhook_signature",
                "message": str(exc),
            },
        ) from exc
    except BillingProviderError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_billing_webhook",
                "message": str(exc),
            },
        ) from exc

    try:
        with get_db() as conn:
            event_row_id = _insert_provider_event(conn, event)
            if event_row_id is None:
                return {
                    "success": True,
                    "duplicate": True,
                    "provider": event.provider,
                    "provider_event_id": event.event_id,
                    "message": "Billing event was already received.",
                }

            try:
                result = apply_verified_billing_event(conn, event)
            except Exception as exc:
                _mark_provider_event(
                    conn,
                    event_row_id,
                    processing_status="failed",
                    message=str(exc),
                )
                raise

            _mark_provider_event(
                conn,
                event_row_id,
                processing_status="ignored" if result.get("ignored") else "processed",
                message=result.get("reason"),
            )

        return {
            "success": True,
            "duplicate": False,
            "provider": event.provider,
            "provider_event_id": event.event_id,
            "event_type": event.event_type,
            "action": event.action,
            "result": result,
        }
    except BillingWebhookProcessingError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "billing_webhook_processing_failed",
                "message": str(exc),
            },
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "billing_webhook_failed",
                "message": "Could not process billing webhook.",
            },
        ) from exc


__all__ = ["router", "apply_verified_billing_event"]