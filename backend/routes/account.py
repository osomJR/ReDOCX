from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, field_validator
import requests
from requests import RequestException

from backend.auth0_dependencies import (
    AuthenticatedUser,
    get_current_user,
)
from backend.billing_provider import (
    BillingProviderError,
    cancel_provider_subscription,
    is_redocx_paystack_transaction_reference,
    resolve_paystack_subscription_reference,
)
from backend.database import get_db
from backend.settings import ensure_user_settings, update_appearance
from backend.subscriptions import get_user_entitlement
from backend.account_lifecycle import (
    ACTIVE_STATUS,
    DEACTIVATION_REQUESTED_STATUS,
    DEACTIVATED_PENDING_DELETION_STATUS,
    PURGE_DUE_STATUS,
    PURGED_STATUS,
    account_access_is_restricted,
    account_subject_tombstone_id,
    create_account_deactivation_request,
    create_pending_account_deletion,
    ensure_account_lifecycle_active,
    get_account_lifecycle,
    normalize_optional_datetime,
    restore_account_if_allowed,
    resolve_restore_deadline,
    utc_now,
)


router = APIRouter(prefix="/account", tags=["account-v1"])

logger = logging.getLogger(__name__)

AUTH0_ACCOUNT_DELETE_TIMEOUT_SECONDS = float(
    os.getenv("AUTH0_ACCOUNT_DELETE_TIMEOUT_SECONDS", "8")
)
AUTH0_MANAGEMENT_TOKEN_SKEW_SECONDS = 60
_auth0_management_token = ""
_auth0_management_token_expires_at = 0.0


class AppearanceSettingsUpdate(BaseModel):
    appearance: str

    @field_validator("appearance")
    @classmethod
    def validate_appearance(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"light", "dark", "system"}:
            raise ValueError("appearance must be one of: light, dark, system.")
        return normalized


def first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_auth0_domain(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    return normalized.rstrip("/") or None


@router.websocket("/realtime")
async def account_realtime(websocket: WebSocket):
    """Delegate to the shared Redis-backed account realtime implementation."""

    from backend.team_communications import account_realtime as shared_account_realtime

    await shared_account_realtime(websocket)


def get_auth0_management_credentials() -> tuple[str, str, str]:
    domain = normalize_auth0_domain(
        first_non_empty_text(os.getenv("AUTH0_DOMAIN"), os.getenv("AUTH0_ISSUER"))
    )
    client_id = first_non_empty_text(
        os.getenv("AUTH0_MANAGEMENT_CLIENT_ID"),
        os.getenv("AUTH0_MGMT_CLIENT_ID"),
        os.getenv("AUTH0_M2M_CLIENT_ID"),
    )
    client_secret = first_non_empty_text(
        os.getenv("AUTH0_MANAGEMENT_CLIENT_SECRET"),
        os.getenv("AUTH0_MGMT_CLIENT_SECRET"),
        os.getenv("AUTH0_M2M_CLIENT_SECRET"),
    )

    if not domain or not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_not_configured",
                "message": (
                    "Account deletion requires Auth0 Management API credentials. "
                    "Set AUTH0_DOMAIN, AUTH0_MANAGEMENT_CLIENT_ID, and "
                    "AUTH0_MANAGEMENT_CLIENT_SECRET with delete:users permission."
                ),
            },
        )

    return domain, client_id, client_secret


def get_auth0_management_token() -> str:
    global _auth0_management_token, _auth0_management_token_expires_at

    import time

    now = time.time()
    if (
        _auth0_management_token
        and now < _auth0_management_token_expires_at - AUTH0_MANAGEMENT_TOKEN_SKEW_SECONDS
    ):
        return _auth0_management_token

    domain, client_id, client_secret = get_auth0_management_credentials()

    try:
        response = requests.post(
            f"https://{domain}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "audience": f"https://{domain}/api/v2/",
            },
            timeout=AUTH0_ACCOUNT_DELETE_TIMEOUT_SECONDS,
        )
    except RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_unavailable",
                "message": "Could not contact Auth0 Management API.",
            },
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_forbidden",
                "message": (
                    "Auth0 Management API credentials cannot delete users. "
                    "Ensure the machine-to-machine application has delete:users permission."
                ),
            },
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_unavailable",
                "message": "Auth0 Management API token request failed.",
            },
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_invalid_response",
                "message": "Auth0 Management API returned an invalid token response.",
            },
        ) from exc

    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_invalid_response",
                "message": "Auth0 Management API did not return an access token.",
            },
        )

    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600

    _auth0_management_token = token.strip()
    _auth0_management_token_expires_at = now + max(expires_in, 1)
    return _auth0_management_token


def delete_auth0_user(user_id: str) -> None:
    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "invalid_token",
                "message": "Token missing subject (sub).",
            },
        )

    domain, _, _ = get_auth0_management_credentials()
    token = get_auth0_management_token()
    encoded_user_id = quote(normalized_user_id, safe="")

    try:
        response = requests.delete(
            f"https://{domain}/api/v2/users/{encoded_user_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=AUTH0_ACCOUNT_DELETE_TIMEOUT_SECONDS,
        )
    except RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_unavailable",
                "message": "Could not delete the Auth0 user account.",
            },
        ) from exc

    # Auth0 returns 204 on success. Treat 404 as idempotent because the local
    # cleanup below should still run if the Auth0 user has already been removed.
    if response.status_code in {204, 404}:
        return

    if response.status_code in {401, 403}:
        global _auth0_management_token, _auth0_management_token_expires_at
        _auth0_management_token = ""
        _auth0_management_token_expires_at = 0
        raise HTTPException(
            status_code=503,
            detail={
                "error": "auth0_management_forbidden",
                "message": (
                    "Auth0 Management API credentials cannot delete users. "
                    "Ensure delete:users permission is granted."
                ),
            },
        )

    raise HTTPException(
        status_code=503,
        detail={
            "error": "auth0_delete_failed",
            "message": "Auth0 did not accept the account deletion request.",
        },
    )


def deleted_user_marker(user_id: str) -> str:
    return account_subject_tombstone_id(user_id)


def auth_provider_from_subject(user_id: str) -> str:
    normalized = (user_id or "").strip().lower()
    if "|" not in normalized:
        return ""
    return normalized.split("|", 1)[0]


def supports_database_password_change(user_id: str) -> bool:
    # Auth0 database-connection users have subjects like auth0|abc123.
    # Social users are usually google-oauth2|..., github|..., windowslive|..., etc.
    return auth_provider_from_subject(user_id) == "auth0"


def relation_exists(conn, relation_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (relation_name,))
        row = cur.fetchone()
    return bool(row and row[0])


def execute_if_relation_exists(conn, relation_name: str, sql: str, params: tuple[Any, ...]) -> int:
    if not relation_exists(conn, relation_name):
        return 0

    with conn.cursor() as cur:
        cur.execute(sql, params)
        return int(cur.rowcount or 0)


def transfer_owned_organizations(conn, user_id: str, replacement_fallback_user_id: str) -> int:
    if not relation_exists(conn, "organizations"):
        return 0

    transferred = 0
    organization_members_exists = relation_exists(conn, "organization_members")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id
            FROM organizations
            WHERE owner_user_id = %s
            """,
            (user_id,),
        )
        organization_ids = [int(row[0]) for row in cur.fetchall()]

    for organization_id in organization_ids:
        replacement_user_id = None

        if organization_members_exists:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id
                    FROM organization_members
                    WHERE organization_id = %s
                      AND user_id <> %s
                      AND status = 'active'
                      AND user_id NOT LIKE 'invite:%%'
                    ORDER BY
                      CASE role
                        WHEN 'admin' THEN 1
                        WHEN 'owner' THEN 2
                        ELSE 3
                      END,
                      joined_at ASC NULLS LAST,
                      created_at ASC,
                      id ASC
                    LIMIT 1
                    """,
                    (organization_id, user_id),
                )
                row = cur.fetchone()
                replacement_user_id = row[0] if row else None

        if replacement_user_id:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organizations
                    SET owner_user_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (replacement_user_id, organization_id),
                )
                transferred += int(cur.rowcount or 0)

                if organization_members_exists:
                    cur.execute(
                        """
                        UPDATE organization_members
                        SET role = 'owner',
                            status = 'active',
                            joined_at = COALESCE(joined_at, NOW()),
                            updated_at = NOW()
                        WHERE organization_id = %s
                          AND user_id = %s
                        """,
                        (organization_id, replacement_user_id),
                    )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organizations
                    SET owner_user_id = %s,
                        name = CASE
                            WHEN owner_user_id = %s THEN 'Deleted account workspace ' || id::text
                            ELSE name
                        END,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (replacement_fallback_user_id, user_id, organization_id),
                )
                transferred += int(cur.rowcount or 0)

            if relation_exists(conn, "organization_subscriptions"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE organization_subscriptions
                        SET status = 'cancelled',
                            provider_customer_id = NULL,
                            provider_subscription_id = NULL,
                            reconciliation_error = NULL,
                            updated_at = NOW()
                        WHERE organization_id = %s
                        """,
                        (organization_id,),
                    )

    return transferred




def serialize_account_lifecycle(lifecycle: dict[str, Any] | None) -> dict[str, Any]:
    if not lifecycle:
        return {"status": ACTIVE_STATUS}

    return {
        "status": lifecycle.get("status") or ACTIVE_STATUS,
        "deletion_reason": lifecycle.get("deletion_reason"),
        "deactivated_at": lifecycle.get("deactivated_at"),
        "restore_deadline": lifecycle.get("restore_deadline"),
        "purge_after": lifecycle.get("purge_after"),
        "restored_at": lifecycle.get("restored_at"),
        "purged_at": lifecycle.get("purged_at"),
    }


def active_paid_organization_memberships(conn, user_id: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                o.id,
                o.name,
                o.owner_user_id,
                om.role,
                os.plan,
                os.status,
                os.current_period_end,
                os.provider,
                os.provider_customer_id,
                os.provider_subscription_id,
                (
                    SELECT COUNT(*)
                    FROM organization_members active_om
                    WHERE active_om.organization_id = o.id
                      AND active_om.status = 'active'
                ) AS active_members,
                (
                    SELECT COUNT(*)
                    FROM organization_members reserved_om
                    WHERE reserved_om.organization_id = o.id
                      AND reserved_om.status IN ('active', 'invited')
                ) AS reserved_members
            FROM organization_members om
            JOIN organizations o
              ON o.id = om.organization_id
            JOIN organization_subscriptions os
              ON os.organization_id = om.organization_id
            WHERE om.user_id = %s
              AND om.status = 'active'
              AND (
                os.status IN ('active', 'past_due')
                OR (
                    os.status = 'cancelled'
                    AND os.current_period_end > NOW()
                )
              )
              AND os.plan IN ('business', 'enterprise')
            ORDER BY os.plan DESC, o.id ASC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    return [
        {
            "organization_id": int(row[0]),
            "organization_name": row[1],
            "owner_user_id": row[2],
            "role": row[3],
            "plan": row[4],
            "subscription_status": row[5],
            "current_period_end": row[6],
            "provider": row[7],
            "provider_customer_id": row[8],
            "provider_subscription_id": row[9],
            "active_members": int(row[10] or 0),
            "reserved_members": int(row[11] or 0),
        }
        for row in rows
    ]


def active_personal_subscription(conn, user_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plan, status, current_period_end, provider,
                   provider_customer_id, provider_subscription_id
            FROM user_subscriptions
            WHERE user_id = %s
              AND plan = 'personal'
              AND (
                status IN ('active', 'past_due')
                OR (
                    status = 'cancelled'
                    AND current_period_end > NOW()
                )
              )
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "plan": row[0],
        "status": row[1],
        "current_period_end": row[2],
        "provider": row[3],
        "provider_customer_id": row[4],
        "provider_subscription_id": row[5],
    }


def build_delete_block_response(*, error: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": error,
            "message": message,
            **extra,
        },
    )


def cancel_external_subscription_for_account_deletion(
    subscription: dict[str, Any],
) -> dict[str, Any]:
    provider = str(subscription.get("provider") or "").strip().lower()
    provider_customer_id = str(
        subscription.get("provider_customer_id") or ""
    ).strip()
    provider_subscription_id = str(
        subscription.get("provider_subscription_id") or ""
    ).strip()
    stored_period_end = normalize_optional_datetime(subscription.get("current_period_end"))

    if not provider and not provider_subscription_id:
        return {
            "provider": None,
            "provider_subscription_id": None,
            "status": "not_required",
            "current_period_end": stored_period_end,
            "clear_provider_subscription_id": False,
        }

    if provider in {"manual", "static", "admin"} and not provider_subscription_id:
        return {
            "provider": provider,
            "provider_subscription_id": None,
            "status": "not_required",
            "current_period_end": stored_period_end,
            "clear_provider_subscription_id": False,
        }

    if provider == "paystack" and subscription.get(
        "provider_subscription_verified_absent"
    ):
        return {
            "provider": "paystack",
            "provider_subscription_id": None,
            "status": "not_required_verified_non_recurring",
            "current_period_end": stored_period_end,
            "clear_provider_subscription_id": True,
        }

    if provider == "paystack" and (
        not provider_subscription_id
        or is_redocx_paystack_transaction_reference(provider_subscription_id)
        or subscription.get("provider_subscription_requires_resolution")
    ):
        try:
            resolution = resolve_paystack_subscription_reference(
                provider_subscription_id=provider_subscription_id or None,
                provider_customer_id=provider_customer_id or None,
                provider_reference=(
                    subscription.get("provider_reference")
                    or (
                        provider_subscription_id
                        if is_redocx_paystack_transaction_reference(provider_subscription_id)
                        else None
                    )
                ),
                expected_plan=subscription.get("plan"),
            )
        except BillingProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "account_deactivation_pending_retry",
                    "message": (
                        "Your account deletion request was recorded, but ReDOCX "
                        "could not safely resolve the legacy Paystack billing state. "
                        "ReDOCX will retry automatically."
                    ),
                },
            ) from exc

        if resolution.outcome == "not_recurring":
            return {
                "provider": "paystack",
                "provider_subscription_id": None,
                "status": "not_required_verified_non_recurring",
                "current_period_end": stored_period_end,
                "clear_provider_subscription_id": True,
            }

        if resolution.state is None:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "account_deactivation_pending_retry",
                    "message": (
                        "Your account deletion request was recorded, but ReDOCX "
                        "could not resolve the Paystack subscription safely. "
                        "ReDOCX will retry automatically."
                    ),
                },
            )
        provider_subscription_id = resolution.state.provider_subscription_id
        stored_period_end = resolution.state.current_period_end or stored_period_end

    if not provider or not provider_subscription_id:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_subscription_reference_missing",
                "message": (
                    "Account deletion cannot continue because the active billing "
                    "subscription is missing its provider reference. Please contact support."
                ),
            },
        )

    try:
        result = cancel_provider_subscription(
            provider,
            provider_subscription_id,
        )
    except BillingProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "account_deactivation_pending_retry",
                "message": (
                    "Your account deletion request was recorded, but subscription "
                    "cancellation could not be finalized. ReDOCX will retry automatically."
                ),
            },
        ) from exc

    return {
        "provider": result.provider,
        "provider_subscription_id": result.provider_subscription_id,
        "status": result.status,
        "current_period_end": result.current_period_end or stored_period_end,
        "clear_provider_subscription_id": False,
    }


def cancellation_metadata(cancellation: dict[str, Any]) -> dict[str, Any]:
    period_end = cancellation.get("current_period_end")
    return {
        "provider": cancellation.get("provider"),
        "provider_subscription_id": cancellation.get(
            "provider_subscription_id"
        ),
        "status": cancellation.get("status"),
        "current_period_end": (
            period_end.isoformat() if hasattr(period_end, "isoformat") else None
        ),
    }


def _validate_deactivation_subscription_target(
    subscription: dict[str, Any],
) -> dict[str, Any]:
    """Validate local cancellation prerequisites without calling the provider.

    Provider reads/writes happen only after deactivation_requested is committed,
    preserving the durable saga boundary even for legacy Paystack references.
    """

    normalized_subscription = dict(subscription)
    provider = str(subscription.get("provider") or "").strip().lower()
    provider_customer_id = str(
        subscription.get("provider_customer_id") or ""
    ).strip()
    provider_subscription_id = str(
        subscription.get("provider_subscription_id") or ""
    ).strip()

    if not provider and not provider_subscription_id:
        return normalized_subscription
    if provider in {"manual", "static", "admin"} and not provider_subscription_id:
        return normalized_subscription
    if provider not in {"stripe", "paystack"}:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "external_subscription_cancellation_unsupported",
                "message": (
                    f"Account deletion cannot start because provider '{provider}' "
                    "does not support server-side subscription cancellation."
                ),
            },
        )

    if provider == "stripe":
        if not provider_subscription_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "external_subscription_reference_missing",
                    "message": (
                        "Account deletion cannot start because the Stripe subscription "
                        "is missing its provider reference. Please contact support."
                    ),
                },
            )
        if not os.getenv("STRIPE_SECRET_KEY", "").strip():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "billing_provider_not_configured",
                    "message": "Stripe cancellation credentials are not configured.",
                },
            )
        return normalized_subscription

    if not os.getenv("PAYSTACK_SECRET_KEY", "").strip():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "billing_provider_not_configured",
                "message": "Paystack cancellation credentials are not configured.",
            },
        )

    if is_redocx_paystack_transaction_reference(provider_subscription_id):
        normalized_subscription["provider_reference"] = provider_subscription_id
        normalized_subscription["provider_subscription_requires_resolution"] = True
        return normalized_subscription

    if not provider_subscription_id:
        if not provider_customer_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "external_subscription_reference_missing",
                    "message": (
                        "Account deletion cannot start because the Paystack subscription "
                        "has neither a subscription code nor a customer identity that can "
                        "be reconciled safely. Please contact support."
                    ),
                },
            )
        normalized_subscription["provider_subscription_requires_resolution"] = True

    return normalized_subscription


def _deactivation_subscription_snapshot(subscription: dict[str, Any]) -> dict[str, Any]:
    period_end = normalize_optional_datetime(subscription.get("current_period_end"))
    return {
        "organization_id": subscription.get("organization_id"),
        "organization_name": subscription.get("organization_name"),
        "plan": subscription.get("plan"),
        "status": subscription.get("status") or subscription.get("subscription_status"),
        "provider": subscription.get("provider"),
        "provider_customer_id": subscription.get("provider_customer_id"),
        "provider_subscription_id": subscription.get("provider_subscription_id"),
        "provider_reference": subscription.get("provider_reference"),
        "provider_subscription_verified_absent": bool(
            subscription.get("provider_subscription_verified_absent")
        ),
        "provider_subscription_requires_resolution": bool(
            subscription.get("provider_subscription_requires_resolution")
        ),
        "current_period_end": period_end.isoformat() if period_end is not None else None,
    }


def _deactivation_result(lifecycle: dict[str, Any]) -> dict[str, Any]:
    status = str(lifecycle.get("status") or "").strip().lower()
    return {
        "mode": "soft_deactivation",
        "deleted": False,
        "deactivated": status in {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS},
        "reason": lifecycle.get("deletion_reason"),
        "lifecycle": serialize_account_lifecycle(lifecycle),
    }


def _account_deactivation_reason(
    *,
    owner_memberships: list[dict[str, Any]],
    personal_subscription: dict[str, Any] | None,
) -> str:
    if owner_memberships:
        return "sole_owner_subscription_cancelled_for_account_deletion"
    if personal_subscription is not None:
        return "personal_subscription_cancelled_for_account_deletion"
    return "free_account_deletion_requested"


def _stage_account_deactivation(
    conn,
    *,
    user_id: str,
    email: str | None,
) -> dict[str, Any]:
    lifecycle = ensure_account_lifecycle_active(conn, user_id)
    status = str(lifecycle.get("status") or ACTIVE_STATUS).strip().lower()

    if status in {
        DEACTIVATION_REQUESTED_STATUS,
        DEACTIVATED_PENDING_DELETION_STATUS,
        PURGE_DUE_STATUS,
    }:
        return lifecycle
    if status == PURGED_STATUS:
        raise HTTPException(
            status_code=410,
            detail={
                "error": "account_deleted",
                "message": "This account has already been permanently deleted.",
            },
        )

    memberships = active_paid_organization_memberships(conn, user_id)
    non_owner_memberships = [
        membership
        for membership in memberships
        if membership["role"] != "owner" or membership["owner_user_id"] != user_id
    ]
    if non_owner_memberships:
        raise build_delete_block_response(
            error="organization_membership_leave_required",
            message=(
                "Leave every active Business or Enterprise organization before deleting your account."
            ),
            memberships=[
                {
                    "organization_id": membership["organization_id"],
                    "organization_name": membership["organization_name"],
                    "plan": membership["plan"],
                    "role": membership["role"],
                }
                for membership in non_owner_memberships
            ],
        )

    owner_memberships = [
        membership
        for membership in memberships
        if membership["role"] == "owner" and membership["owner_user_id"] == user_id
    ]
    owner_memberships_with_others = [
        membership
        for membership in owner_memberships
        if membership["active_members"] > 1 or membership["reserved_members"] > 1
    ]
    if owner_memberships_with_others:
        raise build_delete_block_response(
            error="ownership_transfer_or_member_removal_required",
            message=(
                "Transfer ownership to another active member, or remove every member and invitation before deleting this owner account."
            ),
            organizations=[
                {
                    "organization_id": membership["organization_id"],
                    "organization_name": membership["organization_name"],
                    "plan": membership["plan"],
                    "active_members": membership["active_members"],
                    "reserved_members": membership["reserved_members"],
                }
                for membership in owner_memberships_with_others
            ],
        )

    personal_subscription = active_personal_subscription(conn, user_id)
    if personal_subscription is not None:
        personal_subscription = _validate_deactivation_subscription_target(
            personal_subscription
        )
    owner_memberships = [
        _validate_deactivation_subscription_target(membership)
        for membership in owner_memberships
    ]

    reason = _account_deactivation_reason(
        owner_memberships=owner_memberships,
        personal_subscription=personal_subscription,
    )
    intent_metadata = {
        "deactivation_request_version": 1,
        "deactivation_requested_at": utc_now().isoformat(),
        "email": email,
        "personal_subscription_snapshot": (
            _deactivation_subscription_snapshot(personal_subscription)
            if personal_subscription is not None
            else None
        ),
        "organization_snapshots": [
            _deactivation_subscription_snapshot(membership)
            for membership in owner_memberships
        ],
        "free_account": not owner_memberships and personal_subscription is None,
    }
    return create_account_deactivation_request(
        conn,
        user_id=user_id,
        reason=reason,
        metadata=intent_metadata,
    )


def resume_pending_account_deactivation(
    conn,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Resume an idempotent deletion saga persisted as deactivation_requested."""

    lifecycle = get_account_lifecycle(conn, user_id)
    if lifecycle is None:
        raise RuntimeError("Account deletion lifecycle state is missing.")

    status = str(lifecycle.get("status") or "").strip().lower()
    if status in {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}:
        return _deactivation_result(lifecycle)
    if status == PURGED_STATUS:
        raise HTTPException(
            status_code=410,
            detail={
                "error": "account_deleted",
                "message": "This account has already been permanently deleted.",
            },
        )
    if status != DEACTIVATION_REQUESTED_STATUS:
        raise RuntimeError(
            f"Account deletion cannot resume from lifecycle status '{status or 'unknown'}'."
        )

    metadata = lifecycle.get("metadata") if isinstance(lifecycle.get("metadata"), dict) else {}
    if int(metadata.get("deactivation_request_version") or 0) != 1:
        raise RuntimeError("Account deletion intent metadata is missing or unsupported.")

    personal_snapshot = metadata.get("personal_subscription_snapshot")
    if not isinstance(personal_snapshot, dict):
        personal_snapshot = None
    organization_snapshots = metadata.get("organization_snapshots")
    if not isinstance(organization_snapshots, list):
        organization_snapshots = []
    organization_snapshots = [
        item for item in organization_snapshots if isinstance(item, dict)
    ]

    # Release the transaction opened by the lifecycle read before performing
    # outbound provider requests. The durable deactivation_requested row remains
    # committed and makes this crash/retry safe.
    conn.commit()

    personal_cancellation = (
        cancel_external_subscription_for_account_deletion(personal_snapshot)
        if personal_snapshot is not None
        else None
    )
    organization_cancellations = [
        cancel_external_subscription_for_account_deletion(snapshot)
        for snapshot in organization_snapshots
    ]

    cancellations = [
        *organization_cancellations,
        *([personal_cancellation] if personal_cancellation is not None else []),
    ]
    if cancellations:
        deadlines = [
            resolve_restore_deadline(
                normalize_optional_datetime(cancellation.get("current_period_end"))
            )
            for cancellation in cancellations
        ]
        restore_deadline = max(deadline for deadline, _ in deadlines)
        used_fallback_deadline = any(used_fallback for _, used_fallback in deadlines)
    else:
        restore_deadline, used_fallback_deadline = resolve_restore_deadline(None)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"account-deletion:{user_id}",),
        )

    current = get_account_lifecycle(conn, user_id)
    if current is None:
        raise RuntimeError("Account deletion lifecycle state disappeared during finalization.")
    current_status = str(current.get("status") or "").strip().lower()
    if current_status in {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}:
        return _deactivation_result(current)
    if current_status != DEACTIVATION_REQUESTED_STATUS:
        raise RuntimeError(
            f"Account deletion finalization found unexpected lifecycle status '{current_status}'."
        )

    if personal_snapshot is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = 'cancelled',
                    current_period_end = COALESCE(%s, current_period_end),
                    provider_subscription_id = CASE
                        WHEN %s THEN NULL
                        ELSE COALESCE(%s, provider_subscription_id)
                    END,
                    cancel_at_period_end = TRUE,
                    pending_plan = 'free',
                    plan_change_effective_at = COALESCE(%s, current_period_end),
                    reconciliation_error = NULL,
                    updated_at = NOW()
                WHERE user_id = %s
                  AND plan = 'personal'
                  AND status IN ('active', 'past_due', 'cancelled')
                """,
                (
                    personal_cancellation.get("current_period_end")
                    if personal_cancellation
                    else None,
                    bool(
                        personal_cancellation
                        and personal_cancellation.get("clear_provider_subscription_id")
                    ),
                    personal_cancellation.get("provider_subscription_id")
                    if personal_cancellation
                    else None,
                    personal_cancellation.get("current_period_end")
                    if personal_cancellation
                    else None,
                    user_id,
                ),
            )

    for snapshot, cancellation in zip(
        organization_snapshots,
        organization_cancellations,
        strict=True,
    ):
        organization_id = snapshot.get("organization_id")
        if not isinstance(organization_id, int):
            raise RuntimeError("Account deletion organization snapshot is invalid.")
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE organization_members
                SET status = 'removed',
                    updated_at = NOW()
                WHERE organization_id = %s
                  AND user_id = %s
                  AND status = 'active'
                """,
                (organization_id, user_id),
            )
            cur.execute(
                """
                UPDATE organization_subscriptions
                SET status = 'cancelled',
                    current_period_end = COALESCE(%s, current_period_end),
                    provider_subscription_id = CASE
                        WHEN %s THEN NULL
                        ELSE COALESCE(%s, provider_subscription_id)
                    END,
                    cancel_at_period_end = TRUE,
                    pending_plan = 'free',
                    plan_change_effective_at = COALESCE(%s, current_period_end),
                    reconciliation_error = NULL,
                    updated_at = NOW()
                WHERE organization_id = %s
                  AND status IN ('active', 'past_due', 'cancelled')
                """,
                (
                    cancellation.get("current_period_end"),
                    bool(cancellation.get("clear_provider_subscription_id")),
                    cancellation.get("provider_subscription_id"),
                    cancellation.get("current_period_end"),
                    organization_id,
                ),
            )

    reason = str(current.get("deletion_reason") or "free_account_deletion_requested")
    final_metadata: dict[str, Any] = {
        "email": metadata.get("email"),
        "used_fallback_restore_deadline": used_fallback_deadline,
        "external_cancellation_requested": any(
            not str(cancellation.get("status") or "").startswith("not_required")
            for cancellation in cancellations
        ),
        "external_cancellations": [
            cancellation_metadata(cancellation) for cancellation in cancellations
        ],
    }

    if personal_snapshot is not None:
        final_metadata.update(
            {
                "personal_subscription": True,
                "plan": "personal",
                "provider": personal_snapshot.get("provider"),
                "provider_subscription_id": personal_snapshot.get(
                    "provider_subscription_id"
                ),
            }
        )
    elif not organization_snapshots:
        final_metadata.update(
            {
                "free_account": True,
                "plan": "free",
                "personal_subscription": False,
            }
        )

    if organization_snapshots:
        final_metadata.update(
            {
                "organization_owner_exit": True,
                "personal_subscription": personal_snapshot is not None,
                "organizations": [
                    {
                        "organization_id": snapshot.get("organization_id"),
                        "organization_name": snapshot.get("organization_name"),
                        "plan": snapshot.get("plan"),
                        "provider": cancellation.get("provider"),
                        "provider_subscription_id": cancellation.get(
                            "provider_subscription_id"
                        ),
                        "current_period_end": (
                            cancellation.get("current_period_end").isoformat()
                            if hasattr(cancellation.get("current_period_end"), "isoformat")
                            else None
                        ),
                    }
                    for snapshot, cancellation in zip(
                        organization_snapshots,
                        organization_cancellations,
                        strict=True,
                    )
                ],
            }
        )

    finalized = create_pending_account_deletion(
        conn,
        user_id=user_id,
        reason=reason,
        restore_deadline=restore_deadline,
        metadata=final_metadata,
    )
    return _deactivation_result(finalized)


def prepare_account_deletion(
    conn,
    *,
    user_id: str,
    email: str | None = None,
) -> dict[str, Any]:
    lifecycle = _stage_account_deactivation(
        conn,
        user_id=user_id,
        email=email,
    )
    status = str(lifecycle.get("status") or "").strip().lower()
    if status in {DEACTIVATED_PENDING_DELETION_STATUS, PURGE_DUE_STATUS}:
        return _deactivation_result(lifecycle)

    # Commit the durable intent before any Stripe/Paystack mutation. If the
    # process dies after a provider accepts cancellation, maintenance can safely
    # resume the idempotent saga from deactivation_requested.
    conn.commit()
    try:
        return resume_pending_account_deactivation(conn, user_id=user_id)
    except HTTPException as exc:
        if exc.status_code not in {502, 503}:
            raise
        conn.rollback()
        pending_lifecycle = get_account_lifecycle(conn, user_id) or lifecycle
        logger.warning(
            "Account deactivation persisted for automatic retry user_id=%s error=%s",
            user_id,
            exc.detail,
        )
        result = _deactivation_result(pending_lifecycle)
        result.update(
            {
                "pending": True,
                "message": (
                    "Your account deletion request was recorded and ReDOCX will "
                    "retry finalization automatically."
                ),
            }
        )
        return result
    except Exception as exc:
        conn.rollback()
        pending_lifecycle = get_account_lifecycle(conn, user_id) or lifecycle
        logger.exception(
            "Account deactivation finalization failed after durable intent user_id=%s",
            user_id,
        )
        result = _deactivation_result(pending_lifecycle)
        result.update(
            {
                "pending": True,
                "message": (
                    "Your account deletion request was recorded and ReDOCX will "
                    "retry finalization automatically."
                ),
            }
        )
        return result


def delete_local_account_data(
    conn,
    *,
    user_id: str,
    email: str | None = None,
) -> dict[str, int]:
    """Delete or irreversibly pseudonymize account-linked application data."""

    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required.")

    deleted_marker = deleted_user_marker(normalized_user_id)
    normalized_email = (
        email.strip().lower()
        if isinstance(email, str) and email.strip()
        else None
    )
    counts: dict[str, int] = {}

    counts["billing_collection_jobs"] = execute_if_relation_exists(
        conn, "billing_collection_jobs",
        "DELETE FROM billing_collection_jobs WHERE payer_user_id = %s",
        (normalized_user_id,),
    )
    counts["billing_collection_accounts"] = execute_if_relation_exists(
        conn, "billing_collection_accounts",
        "DELETE FROM billing_collection_accounts WHERE payer_user_id = %s",
        (normalized_user_id,),
    )

    # Immutable enterprise audit chains require migration 022's deterministic
    # pseudonymization/reseal function. Fail the purge rather than silently leave
    # a raw Auth0 subject behind.
    if relation_exists(conn, "team_audit_events"):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regprocedure('pseudonymize_team_audit_subject(text)')"
            )
            row = cur.fetchone()
        if not row or row[0] is None:
            raise RuntimeError(
                "Migration 022_account_deletion_privacy_hardening.sql must be applied before purging accounts."
            )

    counts["owned_organizations_transferred"] = transfer_owned_organizations(
        conn,
        normalized_user_id,
        deleted_marker,
    )

    counts["organization_ownership_transfers"] = execute_if_relation_exists(
        conn,
        "organization_ownership_transfers",
        """
        UPDATE organization_ownership_transfers
        SET previous_owner_user_id = CASE
                WHEN previous_owner_user_id = %s THEN %s
                ELSE previous_owner_user_id
            END,
            new_owner_user_id = CASE
                WHEN new_owner_user_id = %s THEN %s
                ELSE new_owner_user_id
            END,
            transferred_by_user_id = CASE
                WHEN transferred_by_user_id = %s THEN %s
                ELSE transferred_by_user_id
            END
        WHERE previous_owner_user_id = %s
           OR new_owner_user_id = %s
           OR transferred_by_user_id = %s
        """,
        (
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
        ),
    )

    counts["conversation_read_state"] = execute_if_relation_exists(
        conn,
        "conversation_read_state",
        "DELETE FROM conversation_read_state WHERE user_id = %s",
        (normalized_user_id,),
    )

    counts["user_push_subscriptions"] = execute_if_relation_exists(
        conn,
        "user_push_subscriptions",
        "DELETE FROM user_push_subscriptions WHERE user_id = %s",
        (normalized_user_id,),
    )

    counts["team_notification_outbox"] = execute_if_relation_exists(
        conn,
        "team_notification_outbox",
        """
        DELETE FROM team_notification_outbox
        WHERE recipient_user_id = %s
           OR POSITION(%s IN event_key) > 0
           OR POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           OR (
                %s IS NOT NULL
                AND POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           )
        """,
        (
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
            normalized_email,
            normalized_email,
        ),
    )

    counts["team_realtime_outbox"] = execute_if_relation_exists(
        conn,
        "team_realtime_outbox",
        """
        DELETE FROM team_realtime_outbox
        WHERE %s = ANY(recipient_user_ids)
           OR %s = ANY(exclude_user_ids)
           OR POSITION(%s IN event_key) > 0
           OR POSITION(%s IN aggregate_id) > 0
           OR POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           OR (
                %s IS NOT NULL
                AND POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           )
        """,
        (
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
            normalized_email,
            normalized_email,
        ),
    )

    counts["billing_checkout_sessions"] = execute_if_relation_exists(
        conn,
        "billing_checkout_sessions",
        """
        UPDATE billing_checkout_sessions
        SET user_id = %s,
            email = NULL,
            organization_name = NULL,
            checkout_url = NULL,
            provider_session_id = NULL,
            provider_reference = NULL,
            provider_customer_id = NULL,
            provider_subscription_id = NULL,
            replaced_provider_subscription_id = NULL,
            idempotency_key = NULL,
            request_fingerprint = NULL,
            metadata = '{}'::jsonb,
            raw_response = '{}'::jsonb,
            updated_at = NOW()
        WHERE user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["billing_provider_events"] = execute_if_relation_exists(
        conn,
        "billing_provider_events",
        """
        UPDATE billing_provider_events
        SET user_id = %s,
            provider_customer_id = NULL,
            provider_subscription_id = NULL,
            provider_reference = NULL,
            payload = '{}'::jsonb,
            processing_message = NULL,
            updated_at = NOW()
        WHERE user_id = %s
           OR POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           OR (
                %s IS NOT NULL
                AND POSITION(TO_JSONB(%s::text)::text IN payload::text) > 0
           )
        """,
        (
            deleted_marker,
            normalized_user_id,
            normalized_user_id,
            normalized_email,
            normalized_email,
        ),
    )

    counts["conversation_messages"] = execute_if_relation_exists(
        conn,
        "conversation_messages",
        """
        UPDATE conversation_messages
        SET sender_user_id = %s,
            body = '[deleted account]',
            metadata = '{}'::jsonb,
            deleted_at = COALESCE(deleted_at, NOW()),
            updated_at = NOW()
        WHERE sender_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["conversation_message_attachments"] = execute_if_relation_exists(
        conn,
        "conversation_message_attachments",
        """
        UPDATE conversation_message_attachments
        SET uploaded_by_user_id = %s
        WHERE uploaded_by_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["team_attachment_security_events"] = execute_if_relation_exists(
        conn,
        "team_attachment_security_events",
        """
        UPDATE team_attachment_security_events
        SET actor_user_id = %s
        WHERE actor_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["call_participants"] = execute_if_relation_exists(
        conn,
        "call_participants",
        """
        UPDATE call_participants
        SET user_id = CASE
                WHEN user_id = %s THEN %s
                ELSE user_id
            END,
            status = CASE
                WHEN user_id = %s AND status IN ('invited', 'connecting', 'joined')
                    THEN 'left'
                ELSE status
            END,
            left_at = CASE
                WHEN user_id = %s AND status IN ('invited', 'connecting', 'joined')
                    THEN COALESCE(left_at, NOW())
                ELSE left_at
            END,
            revoked_by_user_id = CASE
                WHEN revoked_by_user_id = %s THEN %s
                ELSE revoked_by_user_id
            END,
            updated_at = NOW()
        WHERE user_id = %s
           OR revoked_by_user_id = %s
        """,
        (
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            normalized_user_id,
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            normalized_user_id,
        ),
    )

    counts["call_sessions"] = execute_if_relation_exists(
        conn,
        "call_sessions",
        """
        UPDATE call_sessions
        SET created_by_user_id = CASE
                WHEN created_by_user_id = %s THEN %s
                ELSE created_by_user_id
            END,
            ended_by_user_id = CASE
                WHEN ended_by_user_id = %s THEN %s
                ELSE ended_by_user_id
            END,
            updated_at = NOW()
        WHERE created_by_user_id = %s
           OR ended_by_user_id = %s
        """,
        (
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            deleted_marker,
            normalized_user_id,
            normalized_user_id,
        ),
    )

    counts["organization_conversations"] = execute_if_relation_exists(
        conn,
        "organization_conversations",
        """
        UPDATE organization_conversations
        SET created_by_user_id = %s,
            updated_at = NOW()
        WHERE created_by_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["conversation_members"] = execute_if_relation_exists(
        conn,
        "conversation_members",
        """
        UPDATE conversation_members
        SET user_id = %s,
            status = 'removed',
            removed_at = COALESCE(removed_at, NOW()),
            updated_at = NOW()
        WHERE user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["organization_members"] = execute_if_relation_exists(
        conn,
        "organization_members",
        """
        UPDATE organization_members
        SET user_id = %s,
            status = 'removed',
            member_name = NULL,
            member_email = NULL,
            member_picture = NULL,
            updated_at = NOW()
        WHERE user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["organization_member_inviter_refs"] = execute_if_relation_exists(
        conn,
        "organization_members",
        """
        UPDATE organization_members
        SET invited_by_user_id = %s,
            updated_at = NOW()
        WHERE invited_by_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    if normalized_email:
        invited_user_id = f"invite:{normalized_email}"
        counts["pending_invitations"] = execute_if_relation_exists(
            conn,
            "organization_members",
            """
            UPDATE organization_members
            SET user_id = %s,
                status = 'removed',
                member_name = NULL,
                member_email = NULL,
                member_picture = NULL,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (
                f"deleted-invite:{hashlib.sha256(normalized_email.encode('utf-8')).hexdigest()[:32]}",
                invited_user_id,
            ),
        )

    counts["member_presence"] = execute_if_relation_exists(
        conn,
        "member_presence",
        "DELETE FROM member_presence WHERE user_id = %s",
        (normalized_user_id,),
    )

    counts["user_settings"] = execute_if_relation_exists(
        conn,
        "user_settings",
        "DELETE FROM user_settings WHERE user_id = %s",
        (normalized_user_id,),
    )

    counts["user_subscriptions"] = execute_if_relation_exists(
        conn,
        "user_subscriptions",
        "DELETE FROM user_subscriptions WHERE user_id = %s",
        (normalized_user_id,),
    )

    if relation_exists(conn, "team_audit_events"):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pseudonymize_team_audit_subject(%s)",
                (normalized_user_id,),
            )
            row = cur.fetchone()
        counts["team_audit_events"] = int(row[0] or 0) if row else 0

    return counts


def _read_deactivated_account_view(conn, lifecycle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the account/settings view without creating or restoring any data."""

    appearance = "system"
    if relation_exists(conn, "user_settings"):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT appearance FROM user_settings WHERE user_id = %s",
                (lifecycle.get("user_id"),),
            )
            row = cur.fetchone()
        if row and str(row[0] or "").strip().lower() in {"light", "dark", "system"}:
            appearance = str(row[0]).strip().lower()

    metadata = lifecycle.get("metadata") if isinstance(lifecycle.get("metadata"), dict) else {}
    organizations = metadata.get("organizations")
    if not isinstance(organizations, list):
        organizations = metadata.get("organization_snapshots")
    if isinstance(organizations, list) and organizations:
        organization = next(
            (item for item in organizations if isinstance(item, dict)),
            {},
        )
        plan = str(organization.get("plan") or "business").strip().lower()
        if plan not in {"business", "enterprise"}:
            plan = "business"
        entitlement = {
            "plan": plan,
            "account_count": 1,
            "status": "cancelled",
            "is_paid": True,
            "source": "organization",
            "organization_id": organization.get("organization_id"),
            "organization_name": organization.get("organization_name"),
            "organization_role": "owner",
        }
    elif metadata.get("personal_subscription") or isinstance(
        metadata.get("personal_subscription_snapshot"), dict
    ):
        entitlement = {
            "plan": "personal",
            "account_count": 1,
            "status": "cancelled",
            "is_paid": True,
            "source": "user",
            "organization_id": None,
            "organization_name": None,
            "organization_role": None,
        }
    else:
        entitlement = {
            "plan": "free",
            "account_count": 1,
            "status": "inactive",
            "is_paid": False,
            "source": "authenticated_fallback",
            "organization_id": None,
            "organization_name": None,
            "organization_role": None,
        }

    return {"appearance": appearance}, entitlement


@router.get("/me")
def get_account_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            lifecycle = get_account_lifecycle(conn, current_user.user_id)
            if account_access_is_restricted(lifecycle):
                settings, entitlement_payload = _read_deactivated_account_view(
                    conn, lifecycle
                )
            else:
                settings = ensure_user_settings(conn, current_user.user_id)
                entitlement_payload = None

        entitlement = (
            get_user_entitlement(current_user.user_id)
            if entitlement_payload is None
            else None
        )

        return {
            "user": {
                "id": current_user.user_id,
                "name": current_user.claims.get("name"),
                "email": current_user.claims.get("email"),
                "email_verified": current_user.claims.get("email_verified"),
                "picture": current_user.claims.get("picture"),
                "auth_provider": auth_provider_from_subject(current_user.user_id),
                "password_change_supported": supports_database_password_change(
                    current_user.user_id
                ),
            },
            "settings": {
                "appearance": settings["appearance"],
            },
            "entitlement": (
                entitlement_payload
                if entitlement_payload is not None
                else {
                    "plan": entitlement.plan,
                    "account_count": entitlement.account_count,
                    "status": entitlement.status,
                    "is_paid": entitlement.is_paid,
                    "source": entitlement.source,
                    "organization_id": entitlement.organization_id,
                    "organization_name": entitlement.organization_name,
                    "organization_role": entitlement.organization_role,
                }
            ),
            "account_lifecycle": serialize_account_lifecycle(lifecycle),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_request",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_load_failed",
                "message": "Could not load account details.",
            },
        ) from exc


@router.delete("/me")
def delete_account_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            result = prepare_account_deletion(
                conn,
                user_id=current_user.user_id,
                email=current_user.claims.get("email"),
            )

        return {
            "success": True,
            **result,
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_account_delete_request",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_delete_failed",
                "message": "Could not process account deletion.",
            },
        ) from exc


@router.post("/restore")
def restore_account_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            lifecycle = restore_account_if_allowed(conn, current_user.user_id)

        return {
            "success": True,
            "restored": True,
            "account_lifecycle": serialize_account_lifecycle(lifecycle),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_restore_failed",
                "message": "Could not restore account.",
            },
        ) from exc


@router.patch("/settings")
def patch_account_settings(
    payload: AppearanceSettingsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            ensure_user_settings(conn, current_user.user_id)
            settings = update_appearance(
                conn,
                current_user.user_id,
                payload.appearance,
            )

        entitlement = get_user_entitlement(current_user.user_id)

        return {
            "success": True,
            "settings": {
                "appearance": settings["appearance"],
            },
            "entitlement": {
                "plan": entitlement.plan,
                "account_count": entitlement.account_count,
                "status": entitlement.status,
                "is_paid": entitlement.is_paid,
                "source": entitlement.source,
                "organization_id": entitlement.organization_id,
                "organization_name": entitlement.organization_name,
                "organization_role": entitlement.organization_role,
            },
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_setting",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "account_update_failed",
                "message": "Could not update account settings.",
            },
        ) from exc
