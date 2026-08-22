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

from dataclasses import asdict, replace
import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from psycopg.types.json import Jsonb

from backend.billing_provider import (
    BillingProviderError,
    BillingWebhookEvent,
    WebhookVerificationError,
    cancel_provider_subscription,
    expected_checkout_amount_kobo,
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
logger = logging.getLogger(__name__)

PAID_PLANS = {"personal", "business", "enterprise"}
ORGANIZATION_PLANS = {"business", "enterprise"}
BILLING_PAYMENT_GRACE_DAYS = max(
    0,
    int(os.getenv("BILLING_PAYMENT_GRACE_DAYS", "7")),
)


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


def first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return None


def _activation_resets_plan_change_state(event: BillingWebhookEvent) -> bool:
    """Identify verified events that explicitly resume or replace plan state.

    A generic payment-success event clears dunning counters, but must not erase
    a previously scheduled cancellation or an open dispute suspension. New
    provider subscription IDs are handled independently by the subscription
    upsert SQL.
    """
    event_type = str(event.event_type or "").strip().lower()
    return event_type in {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "subscription.create",
        "subscription.created",
        "subscription.active",
    }


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


def _hydrate_event_identity(conn, event: BillingWebhookEvent) -> BillingWebhookEvent:
    """Fill missing webhook metadata from the server-side checkout ledger."""
    references = [
        event.provider_reference,
        event.provider_subscription_id,
        event.provider_customer_id,
    ]
    if not any(references) and not event.email:
        return event

    row = None
    if any(references):
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, target_plan, organization_id, organization_name,
                       provider_customer_id, provider_subscription_id,
                       provider_reference
                FROM billing_checkout_sessions
                WHERE provider = %s
                  AND (
                        (%s IS NOT NULL AND provider_subscription_id = %s)
                     OR (%s IS NOT NULL AND (provider_reference = %s OR provider_session_id = %s))
                     OR (%s IS NULL AND %s IS NULL
                         AND %s IS NOT NULL AND provider_customer_id = %s)
                  )
                ORDER BY
                    CASE status WHEN 'completed' THEN 1 WHEN 'created' THEN 2 ELSE 3 END,
                    updated_at DESC,
                    id DESC
                LIMIT 1
                """,
                (
                    event.provider,
                    event.provider_subscription_id,
                    event.provider_subscription_id,
                    event.provider_reference,
                    event.provider_reference,
                    event.provider_reference,
                    event.provider_subscription_id,
                    event.provider_reference,
                    event.provider_customer_id,
                    event.provider_customer_id,
                ),
            )
            row = cur.fetchone()

    if row is None and event.email:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT user_id, target_plan, organization_id, organization_name,
                       provider_customer_id, provider_subscription_id,
                       provider_reference
                FROM billing_checkout_sessions
                WHERE provider = %s
                  AND LOWER(email) = LOWER(%s)
                  AND (%s IS NULL OR target_plan = %s)
                  AND (%s IS NULL OR user_id = %s)
                  AND status IN ('started', 'created', 'completed')
                  AND created_at >= NOW() - INTERVAL '24 hours'
                ORDER BY updated_at DESC, id DESC
                LIMIT 2
                """,
                (
                    event.provider,
                    event.email,
                    event.plan,
                    event.plan,
                    event.user_id,
                    event.user_id,
                ),
            )
            candidates = cur.fetchall()

        # Email (and plan/user when supplied) is deliberately only a last-resort
        # Paystack subscription.create correlation. Never guess if ambiguous.
        if len(candidates) == 1:
            row = candidates[0]

    if row is None:
        return event
    return replace(
        event,
        user_id=event.user_id or row[0],
        plan=event.plan or row[1],
        organization_id=event.organization_id or row[2],
        organization_name=event.organization_name or row[3],
        provider_customer_id=event.provider_customer_id or row[4],
        provider_subscription_id=event.provider_subscription_id or row[5],
        # The checkout reference is the stable correlation key shared with the
        # browser callback and charge.success event. Prefer it over weaker
        # Paystack fields such as an email token.
        provider_reference=(
            row[6] or event.provider_reference
            if event.provider == "paystack"
            else event.provider_reference or row[6]
        ),
    )


def _mark_checkout_completed(conn, event: BillingWebhookEvent) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE billing_checkout_sessions
            SET status = 'completed',
                provider_customer_id = COALESCE(%s, provider_customer_id),
                provider_subscription_id = COALESCE(%s, provider_subscription_id),
                provider_reference = COALESCE(%s, provider_reference),
                updated_at = NOW()
            WHERE provider = %s
              AND (
                    (%s IS NOT NULL AND provider_subscription_id = %s)
                 OR (%s IS NOT NULL AND (provider_reference = %s OR provider_session_id = %s))
                 OR (%s IS NULL AND %s IS NULL
                     AND %s IS NOT NULL AND provider_customer_id = %s)
              )
            """,
            (
                event.provider_customer_id,
                event.provider_subscription_id,
                event.provider_reference,
                event.provider,
                event.provider_subscription_id,
                event.provider_subscription_id,
                event.provider_reference,
                event.provider_reference,
                event.provider_reference,
                event.provider_subscription_id,
                event.provider_reference,
                event.provider_customer_id,
                event.provider_customer_id,
            ),
        )


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
        # A Paystack transaction reference identifies a payment, not a
        # subscription. Persist only a real provider subscription code so later
        # cancellation/reconciliation calls never target the wrong resource.
        provider_subscription_id=event.provider_subscription_id,
        reset_plan_change_state=_activation_resets_plan_change_state(event),
    )
    _update_period_fields(
        conn,
        table_name="user_subscriptions",
        owner_column="user_id",
        owner_value=user_id,
        event=event,
    )

    # A Business/Enterprise -> Personal transition must not leave a second paid
    # organization entitlement attached to the same provider subscription.
    with conn.cursor() as cur:
        if event.organization_id is not None:
            cur.execute(
                """
                UPDATE organization_subscriptions
                SET status = 'inactive',
                    provider = NULL,
                    provider_customer_id = NULL,
                    provider_subscription_id = NULL,
                    cancel_at_period_end = FALSE,
                    pending_plan = NULL,
                    plan_change_effective_at = NULL,
                    grace_period_end = NULL,
                    access_revoked_at = NULL,
                    access_revocation_reason = NULL,
                    updated_at = NOW()
                WHERE organization_id = %s
                """,
                (event.organization_id,),
            )

    return {
        "subscription_scope": "user",
        "user_id": user_id,
        "plan": "personal",
    }


def _resolve_or_create_organization(conn, event: BillingWebhookEvent, *, owner_user_id: str) -> int:
    """Resolve one stable organization for a provider subscription.

    Stripe can deliver several activation-shaped events for the same plan
    change. Personal -> Business/Enterprise checkout metadata has no
    organization_id yet, so blindly creating an organization on every event
    would duplicate the workspace. Serialize resolution by the strongest
    provider/customer identity, then reuse the subscription or checkout ledger
    association before creating anything.
    """
    lock_identity = first_non_empty_text(
        event.provider_reference,
        event.provider_subscription_id,
        event.provider_customer_id,
        event.user_id,
    ) or event.event_id
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"billing-organization:{event.provider}:{lock_identity}",),
        )

    if event.provider_subscription_id:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT organization_id
                FROM organization_subscriptions
                WHERE provider = %s
                  AND provider_subscription_id = %s
                LIMIT 1
                """,
                (event.provider, event.provider_subscription_id),
            )
            row = cur.fetchone()
        if row is not None:
            resolved_id = int(row[0])
            if event.organization_id is not None and resolved_id != int(event.organization_id):
                raise BillingWebhookProcessingError(
                    "Verified billing metadata conflicts with the organization "
                    "already attached to this provider subscription."
                )
            return resolved_id

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

    checkout_row_id: int | None = None
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id
            FROM billing_checkout_sessions
            WHERE provider = %s
              AND (
                    (%s IS NOT NULL AND provider_subscription_id = %s)
                 OR (%s IS NOT NULL AND (provider_reference = %s OR provider_session_id = %s))
                 OR (%s IS NULL AND %s IS NULL
                     AND %s IS NOT NULL AND provider_customer_id = %s)
              )
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (
                event.provider,
                event.provider_subscription_id,
                event.provider_subscription_id,
                event.provider_reference,
                event.provider_reference,
                event.provider_reference,
                event.provider_subscription_id,
                event.provider_reference,
                event.provider_customer_id,
                event.provider_customer_id,
            ),
        )
        checkout_row = cur.fetchone()
    if checkout_row is not None:
        checkout_row_id = int(checkout_row[0])
        if checkout_row[1] is not None:
            return int(checkout_row[1])

    organization_id = create_organization(
        conn,
        name=_organization_name_for_event(event, owner_user_id),
        owner_user_id=owner_user_id,
    )
    if checkout_row_id is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE billing_checkout_sessions
                SET organization_id = %s,
                    updated_at = NOW()
                WHERE id = %s
                  AND organization_id IS NULL
                """,
                (organization_id, checkout_row_id),
            )
    return organization_id


def _resolved_organization_seat_count(
    conn,
    *,
    organization_id: int,
    event: BillingWebhookEvent,
) -> int:
    """Preserve purchased seats when provider renewal events omit checkout metadata."""
    if event.max_accounts is not None:
        return int(event.max_accounts)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT max_accounts FROM organization_subscriptions WHERE organization_id = %s",
            (organization_id,),
        )
        row = cur.fetchone()
    if row is not None and row[0] is not None:
        return int(row[0])

    # New Business/Enterprise purchases default to one seat only when a legacy
    # provider event lacks seat metadata. Modern ReDOCX checkout always supplies it.
    return 1


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
    purchased_seats = _resolved_organization_seat_count(
        conn,
        organization_id=organization_id,
        event=event,
    )
    upsert_organization_subscription(
        conn,
        organization_id=organization_id,
        plan=plan,  # type: ignore[arg-type]
        max_accounts=purchased_seats,
        status="active",
        provider=event.provider,
        provider_customer_id=event.provider_customer_id,
        provider_subscription_id=event.provider_subscription_id,
        reset_plan_change_state=_activation_resets_plan_change_state(event),
    )
    _update_period_fields(
        conn,
        table_name="organization_subscriptions",
        owner_column="organization_id",
        owner_value=organization_id,
        event=event,
    )

    # Personal -> Business/Enterprise is a scope transition, not a second paid
    # entitlement. The organization row becomes authoritative.
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_subscriptions
            SET plan = 'free',
                account_count = 1,
                status = 'active',
                provider = NULL,
                provider_customer_id = NULL,
                provider_subscription_id = NULL,
                current_period_start = NULL,
                current_period_end = NULL,
                cancel_at_period_end = FALSE,
                pending_plan = NULL,
                plan_change_effective_at = NULL,
                grace_period_end = NULL,
                payment_failure_count = 0,
                access_revoked_at = NULL,
                access_revocation_reason = NULL,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (owner_user_id,),
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


def _update_subscription_status_rows(
    conn,
    event: BillingWebhookEvent,
    *,
    table_name: Literal["user_subscriptions", "organization_subscriptions"],
    owner_column: Literal["user_id", "organization_id"],
    owner_value: str | int | None,
    status: str,
    policy: Literal["cancel", "payment_grace", "suspend", "revoke", "restore"],
) -> int:
    grace_days = BILLING_PAYMENT_GRACE_DAYS
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {table_name}
            SET status = %s,
                current_period_start = COALESCE(%s, current_period_start),
                current_period_end = CASE
                    WHEN %s = 'revoke' THEN LEAST(COALESCE(current_period_end, NOW()), NOW())
                    ELSE COALESCE(%s, current_period_end)
                END,
                cancel_at_period_end = CASE
                    WHEN %s IN ('cancel', 'revoke') THEN TRUE
                    WHEN %s = 'restore' THEN FALSE
                    ELSE cancel_at_period_end
                END,
                grace_period_end = CASE
                    WHEN %s = 'payment_grace' THEN
                        COALESCE(
                            grace_period_end,
                            GREATEST(COALESCE(%s, current_period_end, NOW()), NOW())
                            + make_interval(days => %s)
                        )
                    WHEN %s IN ('restore', 'revoke', 'suspend') THEN NULL
                    ELSE grace_period_end
                END,
                payment_failure_count = CASE
                    WHEN %s = 'payment_grace' THEN payment_failure_count + 1
                    WHEN %s = 'restore' THEN 0
                    ELSE payment_failure_count
                END,
                access_revoked_at = CASE
                    WHEN %s IN ('suspend', 'revoke') THEN NOW()
                    WHEN %s = 'restore' THEN NULL
                    ELSE access_revoked_at
                END,
                access_revocation_reason = CASE
                    WHEN %s = 'suspend' THEN 'payment_dispute_opened'
                    WHEN %s = 'revoke' THEN 'refund_or_chargeback'
                    WHEN %s = 'restore' THEN NULL
                    ELSE access_revocation_reason
                END,
                last_provider_event_at = NOW(),
                updated_at = NOW()
            WHERE provider = %s
              AND (
                    (%s IS NOT NULL AND provider_subscription_id = %s)
                 OR (%s IS NULL AND %s IS NOT NULL AND provider_customer_id = %s)
                 OR (%s IS NULL AND %s IS NULL
                     AND %s IS NOT NULL AND {owner_column} = %s)
              )
            """,
            (
                status,
                event.current_period_start,
                policy,
                event.current_period_end,
                policy,
                policy,
                policy,
                event.current_period_end,
                grace_days,
                policy,
                policy,
                policy,
                policy,
                policy,
                policy,
                policy,
                policy,
                event.provider,
                event.provider_subscription_id,
                event.provider_subscription_id,
                event.provider_subscription_id,
                event.provider_customer_id,
                event.provider_customer_id,
                event.provider_subscription_id,
                event.provider_customer_id,
                owner_value,
                owner_value,
            ),
        )
        return int(cur.rowcount or 0)


def _set_subscription_status(
    conn,
    event: BillingWebhookEvent,
    *,
    status: str,
    policy: Literal["cancel", "payment_grace", "suspend", "revoke", "restore"],
) -> dict[str, Any]:
    user_count = _update_subscription_status_rows(
        conn,
        event,
        table_name="user_subscriptions",
        owner_column="user_id",
        owner_value=event.user_id,
        status=status,
        policy=policy,
    )
    organization_count = _update_subscription_status_rows(
        conn,
        event,
        table_name="organization_subscriptions",
        owner_column="organization_id",
        owner_value=event.organization_id,
        status=status,
        policy=policy,
    )

    return {
        "subscription_scope": "status_update",
        "status": status,
        "policy": policy,
        "grace_days": BILLING_PAYMENT_GRACE_DAYS if policy == "payment_grace" else 0,
        "user_rows_updated": user_count,
        "organization_rows_updated": organization_count,
    }


def _provider_subscription_ids_for_revocation(
    conn,
    event: BillingWebhookEvent,
) -> list[str]:
    direct = str(event.provider_subscription_id or "").strip()
    if direct:
        return [direct]

    conditions: list[str] = []
    params: list[Any] = [event.provider]
    if event.provider_customer_id:
        conditions.append("provider_customer_id = %s")
        params.append(event.provider_customer_id)
    elif event.organization_id is not None:
        conditions.append("organization_id = %s")
        params.append(event.organization_id)

    subscription_ids: set[str] = set()
    if conditions:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT provider_subscription_id
                FROM organization_subscriptions
                WHERE provider = %s
                  AND provider_subscription_id IS NOT NULL
                  AND ({' OR '.join(conditions)})
                """,
                tuple(params),
            )
            subscription_ids.update(
                str(row[0]).strip()
                for row in cur.fetchall()
                if row and str(row[0] or "").strip()
            )

    user_conditions: list[str] = []
    user_params: list[Any] = [event.provider]
    if event.provider_customer_id:
        user_conditions.append("us.provider_customer_id = %s")
        user_params.append(event.provider_customer_id)
    elif event.user_id:
        user_conditions.append("us.user_id = %s")
        user_params.append(event.user_id)

    if user_conditions:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT us.provider_subscription_id
                FROM user_subscriptions us
                WHERE us.provider = %s
                  AND us.provider_subscription_id IS NOT NULL
                  AND ({' OR '.join(user_conditions)})
                UNION
                SELECT os.provider_subscription_id
                FROM organization_subscriptions os
                JOIN organizations o ON o.id = os.organization_id
                WHERE os.provider = %s
                  AND os.provider_subscription_id IS NOT NULL
                  AND o.owner_user_id = %s
                """,
                (
                    *user_params,
                    event.provider,
                    event.user_id or "",
                ),
            )
            subscription_ids.update(
                str(row[0]).strip()
                for row in cur.fetchall()
                if row and str(row[0] or "").strip()
            )

    return sorted(subscription_ids)


def _stop_provider_renewal_for_revocation(
    conn,
    event: BillingWebhookEvent,
) -> list[dict[str, Any]]:
    """Prevent a refunded or lost-dispute subscription from renewing."""
    if event.action != "revoke" or event.provider not in {"stripe", "paystack"}:
        return []

    changes: list[dict[str, Any]] = []
    for subscription_id in _provider_subscription_ids_for_revocation(conn, event):
        try:
            change = cancel_provider_subscription(event.provider, subscription_id)
        except BillingProviderError as exc:
            raise BillingWebhookProcessingError(
                "Entitlement revocation was not completed because provider renewal "
                "could not be stopped. The provider should retry this webhook."
            ) from exc
        changes.append(
            {
                "provider": change.provider,
                "provider_subscription_id": change.provider_subscription_id,
                "status": change.status,
                "current_period_end": (
                    change.current_period_end.isoformat()
                    if change.current_period_end is not None
                    else None
                ),
            }
        )
    return changes


def _validate_paystack_seat_purchase_amount(event: BillingWebhookEvent) -> None:
    """Validate server-priced Paystack seat purchases before granting entitlement."""
    if (
        event.provider != "paystack"
        or event.action != "activate"
        or event.plan not in ORGANIZATION_PLANS
        or event.max_accounts is None
        or event.amount is None
    ):
        return

    try:
        expected_amount = expected_checkout_amount_kobo(event.plan, event.max_accounts)
        verified_amount = int(event.amount)
    except (TypeError, ValueError) as exc:
        raise BillingWebhookProcessingError(
            "Paystack seat purchase is missing a valid amount or seat count."
        ) from exc

    currency = str(event.currency or "").strip().upper()
    if verified_amount != expected_amount or currency != "NGN":
        raise BillingWebhookProcessingError(
            "Paystack seat purchase amount does not match the verified plan and seat count."
        )


def apply_verified_billing_event(conn, event: BillingWebhookEvent) -> dict[str, Any]:
    if event.action == "ignore":
        return {
            "subscription_scope": "none",
            "ignored": True,
            "reason": f"No entitlement action mapped for event_type={event.event_type}.",
        }

    if event.action == "activate":
        _validate_paystack_seat_purchase_amount(event)
        result = _activate_subscription(conn, event)
        _mark_checkout_completed(conn, event)
        return result

    if event.action == "cancel":
        return _set_subscription_status(
            conn,
            event,
            status="cancelled",
            policy="cancel",
        )

    if event.action == "past_due":
        return _set_subscription_status(
            conn,
            event,
            status="past_due",
            policy="payment_grace",
        )

    if event.action == "suspend":
        return _set_subscription_status(
            conn,
            event,
            status="past_due",
            policy="suspend",
        )

    if event.action == "revoke":
        return _set_subscription_status(
            conn,
            event,
            status="cancelled",
            policy="revoke",
        )

    if event.action == "restore":
        return _set_subscription_status(
            conn,
            event,
            status="active",
            policy="restore",
        )

    raise BillingWebhookProcessingError(f"Unsupported billing webhook action: {event.action}.")


def process_verified_billing_event(event: BillingWebhookEvent) -> dict[str, Any]:
    """Persist and apply one provider-verified event idempotently.

    Signed webhooks and authenticated server-to-server callback verification use
    this single entitlement mutation path. Any failure rolls the entire database
    transaction back so provider retry remains safe. Never issue compensating SQL
    inside an already-aborted PostgreSQL transaction.
    """
    try:
        with get_db() as conn:
            event = _hydrate_event_identity(conn, event)
            if event.action == "activate":
                activation_identity = first_non_empty_text(
                    event.provider_reference,
                    event.provider_subscription_id,
                    event.user_id,
                    event.email,
                    event.event_id,
                )
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"billing-activation:{event.provider}:{activation_identity}",),
                    )
                # A concurrent subscription.create/charge.success pair may have
                # populated the checkout with the real subscription code while we
                # waited for the lock. Re-hydrate before writing entitlement state.
                event = _hydrate_event_identity(conn, event)

            event_row_id = _insert_provider_event(conn, event)
            if event_row_id is None:
                return {
                    "success": True,
                    "duplicate": True,
                    "provider": event.provider,
                    "provider_event_id": event.event_id,
                    "message": "Billing event was already received.",
                }

            provider_cancellations = _stop_provider_renewal_for_revocation(
                conn, event
            )
            result = apply_verified_billing_event(conn, event)
            if provider_cancellations:
                result = {
                    **result,
                    "provider_cancellations": provider_cancellations,
                }

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
    except Exception:
        logger.exception(
            "Verified billing event processing failed provider=%s event_id=%s "
            "event_type=%s action=%s reference_present=%s",
            event.provider,
            event.event_id,
            event.event_type,
            event.action,
            bool(event.provider_reference),
        )
        raise


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
        return process_verified_billing_event(event)
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
        logger.exception(
            "Billing webhook processing failed provider=%s event_id=%s event_type=%s",
            provider_name,
            getattr(event, "event_id", ""),
            getattr(event, "event_type", ""),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": "billing_webhook_failed",
                "message": "Could not process billing webhook.",
            },
        ) from exc


__all__ = [
    "BillingWebhookProcessingError",
    "apply_verified_billing_event",
    "process_verified_billing_event",
    "router",
]
