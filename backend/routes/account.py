from __future__ import annotations

import hashlib
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
from backend.billing_provider import BillingProviderError, cancel_provider_subscription
from backend.database import get_db
from backend.settings import ensure_user_settings, update_appearance
from backend.subscriptions import get_user_entitlement
from backend.account_lifecycle import (
    ACTIVE_STATUS,
    DEACTIVATED_PENDING_DELETION_STATUS,
    create_pending_account_deletion,
    ensure_account_lifecycle_active,
    get_account_lifecycle,
    restore_account_if_allowed,
    resolve_restore_deadline,
)


router = APIRouter(prefix="/account", tags=["account-v1"])

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
    digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:32]
    return f"deleted:{digest}"


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
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (replacement_fallback_user_id, organization_id),
                )
                transferred += int(cur.rowcount or 0)

            if relation_exists(conn, "organization_subscriptions"):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE organization_subscriptions
                        SET status = 'cancelled',
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
                os.status = 'active'
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
            "provider_subscription_id": row[8],
            "active_members": int(row[9] or 0),
            "reserved_members": int(row[10] or 0),
        }
        for row in rows
    ]


def active_personal_subscription(conn, user_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plan, status, current_period_end, provider, provider_subscription_id
            FROM user_subscriptions
            WHERE user_id = %s
              AND plan = 'personal'
              AND (
                status = 'active'
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
        "provider_subscription_id": row[4],
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
    provider_subscription_id = str(
        subscription.get("provider_subscription_id") or ""
    ).strip()
    stored_period_end = subscription.get("current_period_end")

    if not provider and not provider_subscription_id:
        return {
            "provider": None,
            "provider_subscription_id": None,
            "status": "not_required",
            "current_period_end": stored_period_end,
        }

    if provider in {"manual", "static", "admin"} and not provider_subscription_id:
        return {
            "provider": provider,
            "provider_subscription_id": None,
            "status": "not_required",
            "current_period_end": stored_period_end,
        }

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
                "error": "external_subscription_cancellation_failed",
                "message": (
                    "Account deletion was not started because ReDOCX could not "
                    "stop the subscription from renewing. Please retry."
                ),
            },
        ) from exc

    return {
        "provider": result.provider,
        "provider_subscription_id": result.provider_subscription_id,
        "status": result.status,
        "current_period_end": result.current_period_end or stored_period_end,
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


def deactivate_personal_account_for_period(conn, *, user_id: str, subscription: dict[str, Any]) -> dict[str, Any]:
    cancellation = cancel_external_subscription_for_account_deletion(subscription)
    period_end = cancellation.get("current_period_end")
    restore_deadline, used_fallback_deadline = resolve_restore_deadline(period_end)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE user_subscriptions
            SET status = 'cancelled',
                current_period_end = COALESCE(%s, current_period_end),
                updated_at = NOW()
            WHERE user_id = %s
              AND plan = 'personal'
              AND (
                status = 'active'
                OR (
                    status = 'cancelled'
                    AND current_period_end > NOW()
                )
              )
            """,
            (period_end, user_id),
        )

    return create_pending_account_deletion(
        conn,
        user_id=user_id,
        reason="personal_subscription_cancelled_for_account_deletion",
        restore_deadline=restore_deadline,
        metadata={
            "personal_subscription": True,
            "plan": "personal",
            "provider": subscription.get("provider"),
            "provider_subscription_id": subscription.get("provider_subscription_id"),
            "used_fallback_restore_deadline": used_fallback_deadline,
            "external_cancellation_requested": cancellation.get("status")
            != "not_required",
            "external_cancellations": [
                cancellation_metadata(cancellation),
            ],
        },
    )


def deactivate_sole_owner_accounts_for_period(
    conn,
    *,
    user_id: str,
    organizations: list[dict[str, Any]],
    personal_subscription: dict[str, Any] | None = None,
) -> dict[str, Any]:
    organization_cancellations: list[dict[str, Any]] = []
    for organization in organizations:
        cancellation = cancel_external_subscription_for_account_deletion(
            organization
        )
        organization_cancellations.append(cancellation)

    personal_cancellation = (
        cancel_external_subscription_for_account_deletion(personal_subscription)
        if personal_subscription is not None
        else None
    )

    period_ends = [
        cancellation.get("current_period_end")
        for cancellation in organization_cancellations
    ]
    if personal_cancellation is not None:
        period_ends.append(personal_cancellation.get("current_period_end"))

    deadlines = [
        resolve_restore_deadline(period_end)
        for period_end in period_ends
    ]
    restore_deadline = max(
        (deadline for deadline, _ in deadlines),
        default=resolve_restore_deadline(None)[0],
    )
    used_fallback_deadline = any(
        used_fallback for _, used_fallback in deadlines
    )

    for organization, cancellation in zip(
        organizations,
        organization_cancellations,
        strict=True,
    ):
        organization_id = organization["organization_id"]
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
                    updated_at = NOW()
                WHERE organization_id = %s
                  AND status = 'active'
                """,
                (
                    cancellation.get("current_period_end"),
                    organization_id,
                ),
            )

    if personal_subscription is not None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE user_subscriptions
                SET status = 'cancelled',
                    current_period_end = COALESCE(%s, current_period_end),
                    updated_at = NOW()
                WHERE user_id = %s
                  AND plan = 'personal'
                  AND status = 'active'
                """,
                (
                    personal_cancellation.get("current_period_end")
                    if personal_cancellation
                    else None,
                    user_id,
                ),
            )

    all_cancellations = [
        *organization_cancellations,
        *([personal_cancellation] if personal_cancellation is not None else []),
    ]

    return create_pending_account_deletion(
        conn,
        user_id=user_id,
        reason="sole_owner_subscription_cancelled_for_account_deletion",
        restore_deadline=restore_deadline,
        metadata={
            "organization_owner_exit": True,
            "personal_subscription": personal_subscription is not None,
            "organizations": [
                {
                    "organization_id": org["organization_id"],
                    "organization_name": org.get("organization_name"),
                    "plan": org.get("plan"),
                    "provider": cancellation.get("provider"),
                    "provider_subscription_id": cancellation.get(
                        "provider_subscription_id"
                    ),
                    "current_period_end": cancellation.get(
                        "current_period_end"
                    ).isoformat()
                    if hasattr(
                        cancellation.get("current_period_end"),
                        "isoformat",
                    )
                    else None,
                }
                for org, cancellation in zip(
                    organizations,
                    organization_cancellations,
                    strict=True,
                )
            ],
            "used_fallback_restore_deadline": used_fallback_deadline,
            "external_cancellation_requested": any(
                cancellation.get("status") != "not_required"
                for cancellation in all_cancellations
            ),
            "external_cancellations": [
                cancellation_metadata(cancellation)
                for cancellation in all_cancellations
            ],
        },
    )


def prepare_account_deletion(conn, *, user_id: str, email: str | None = None) -> dict[str, Any]:
    ensure_account_lifecycle_active(conn, user_id)
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

    if owner_memberships:
        lifecycle = deactivate_sole_owner_accounts_for_period(
            conn,
            user_id=user_id,
            organizations=owner_memberships,
            personal_subscription=personal_subscription,
        )
        return {
            "mode": "soft_deactivation",
            "deleted": False,
            "deactivated": True,
            "reason": "sole_owner_subscription_cancelled_for_account_deletion",
            "lifecycle": serialize_account_lifecycle(lifecycle),
        }

    if personal_subscription is not None:
        lifecycle = deactivate_personal_account_for_period(
            conn,
            user_id=user_id,
            subscription=personal_subscription,
        )
        return {
            "mode": "soft_deactivation",
            "deleted": False,
            "deactivated": True,
            "reason": "personal_subscription_cancelled_for_account_deletion",
            "lifecycle": serialize_account_lifecycle(lifecycle),
        }

    restore_deadline, used_fallback_deadline = resolve_restore_deadline(None)
    lifecycle = create_pending_account_deletion(
        conn,
        user_id=user_id,
        reason="free_account_deletion_requested",
        restore_deadline=restore_deadline,
        metadata={
            "free_account": True,
            "plan": "free",
            "used_fallback_restore_deadline": used_fallback_deadline,
            "external_cancellation_requested": False,
            "external_cancellations": [],
        },
    )
    return {
        "mode": "soft_deactivation",
        "deleted": False,
        "deactivated": True,
        "reason": "free_account_deletion_requested",
        "lifecycle": serialize_account_lifecycle(lifecycle),
    }
def delete_local_account_data(
    conn,
    *,
    user_id: str,
    email: str | None = None,
) -> dict[str, int]:
    normalized_user_id = (user_id or "").strip()
    if not normalized_user_id:
        raise ValueError("user_id is required.")

    deleted_marker = deleted_user_marker(normalized_user_id)
    counts: dict[str, int] = {}

    counts["owned_organizations_transferred"] = transfer_owned_organizations(
        conn,
        normalized_user_id,
        deleted_marker,
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

    counts["call_participants"] = execute_if_relation_exists(
        conn,
        "call_participants",
        """
        UPDATE call_participants
        SET user_id = %s,
            status = 'left',
            left_at = COALESCE(left_at, NOW()),
            updated_at = NOW()
        WHERE user_id = %s
        """,
        (deleted_marker, normalized_user_id),
    )

    counts["call_sessions"] = execute_if_relation_exists(
        conn,
        "call_sessions",
        """
        UPDATE call_sessions
        SET created_by_user_id = %s,
            updated_at = NOW()
        WHERE created_by_user_id = %s
        """,
        (deleted_marker, normalized_user_id),
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

    normalized_email = email.strip().lower() if isinstance(email, str) and email.strip() else None
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
            (f"deleted-invite:{hashlib.sha256(normalized_email.encode('utf-8')).hexdigest()[:32]}", invited_user_id),
        )

    counts["member_presence"] = execute_if_relation_exists(
        conn,
        "member_presence",
        """
        DELETE FROM member_presence
        WHERE user_id = %s
        """,
        (normalized_user_id,),
    )

    counts["user_settings"] = execute_if_relation_exists(
        conn,
        "user_settings",
        """
        DELETE FROM user_settings
        WHERE user_id = %s
        """,
        (normalized_user_id,),
    )

    counts["user_subscriptions"] = execute_if_relation_exists(
        conn,
        "user_subscriptions",
        """
        DELETE FROM user_subscriptions
        WHERE user_id = %s
        """,
        (normalized_user_id,),
    )

    return counts


@router.get("/me")
def get_account_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            lifecycle = restore_account_if_allowed(conn, current_user.user_id)
            settings = ensure_user_settings(conn, current_user.user_id)

        entitlement = get_user_entitlement(current_user.user_id)

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