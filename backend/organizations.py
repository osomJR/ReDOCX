from __future__ import annotations

"""
Organization and team-management API.

Responsibilities:
- create organizations
- view organizations the authenticated user belongs to
- view an organization and its subscription
- invite members by email
- accept email invitations
- change member roles
- soft-remove members by setting status = 'removed'

Notes:
- Personal subscriptions remain user-level in user_subscriptions.
- Business/Enterprise subscriptions are organization-level in organization_subscriptions.
- Email invitations are stored as organization_members.user_id = 'invite:{email}' while pending.
- A pending invite grants no entitlement until the invited user accepts it and the row is converted
  to the authenticated Auth0 subject from current_user.user_id.
"""

from datetime import datetime
from typing import Any, Literal
import os
import re
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, field_validator
from requests import RequestException
import requests

from backend.auth0_dependencies import AuthenticatedUser, get_current_user, require_scopes
from backend.database import get_db
from backend.subscriptions import normalize_organization_name
from backend.team_communications import (
    dispatch_account_realtime_event_by_email,
    dispatch_organization_member_revocation,
    dispatch_organization_realtime_event,
    revoke_organization_member_communications,
)
from backend.account_lifecycle import create_pending_account_deletion, resolve_restore_deadline


router = APIRouter(prefix="/organizations", tags=["organizations"])

OrganizationRole = Literal["owner", "admin", "member"]
OrganizationMemberStatus = Literal["active", "invited", "removed"]

VALID_ROLES = {"owner", "admin", "member"}
VALID_MEMBER_STATUSES = {"active", "invited", "removed"}

INVITE_USER_ID_PREFIX = "invite:"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

AUTH0_MANAGEMENT_TOKEN_ENV = "AUTH0_MANAGEMENT_API_TOKEN"
AUTH0_MANAGEMENT_TOKEN_FALLBACK_ENV = "AUTH0_MGMT_API_TOKEN"
AUTH0_PROFILE_TIMEOUT_SECONDS = float(os.getenv("AUTH0_PROFILE_TIMEOUT_SECONDS", "3"))
AUTH0_PROFILE_CACHE_TTL_SECONDS = int(os.getenv("AUTH0_PROFILE_CACHE_TTL_SECONDS", "300"))

BUSINESS_DEFAULT_MAX_ACCOUNTS = 19
ENTERPRISE_DEFAULT_MAX_ACCOUNTS = 20

# In-memory best-effort profile cache. This prevents repeatedly calling Auth0
# Management API for the same member list during normal page refreshes.
_AUTH0_PROFILE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class CreateOrganizationRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_organization_name(value)


class UpdateOrganizationRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return normalize_organization_name(value)


class InviteMemberRequest(BaseModel):
    email: str
    role: OrganizationRole = "member"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        return normalize_role(value)


class UpdateMemberRequest(BaseModel):
    role: OrganizationRole | None = None
    status: OrganizationMemberStatus | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_role(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_member_status(value)


class TransferOwnershipRequest(BaseModel):
    new_owner_user_id: str
    reason: str | None = None

    @field_validator("new_owner_user_id")
    @classmethod
    def validate_new_owner_user_id(cls, value: str) -> str:
        return normalize_user_id(value)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class UpdateOrganizationSubscriptionRequest(BaseModel):
    plan: Literal["business", "enterprise"]
    # Business always receives the full Business allowance: 19 seats.
    # Enterprise defaults to 20 seats and can be explicitly set higher.
    max_accounts: int | None = None
    status: Literal["active", "inactive", "cancelled", "past_due"] = "active"
    provider: str | None = None
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"business", "enterprise"}:
            raise ValueError("plan must be one of: business, enterprise.")
        return normalized

    @field_validator("max_accounts")
    @classmethod
    def validate_max_accounts(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if not isinstance(value, int) or value < 2:
            raise ValueError("max_accounts must be an integer greater than or equal to 2.")
        return value

    @field_validator("status")
    @classmethod
    def validate_subscription_status(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"active", "inactive", "cancelled", "past_due"}:
            raise ValueError("status must be one of: active, inactive, cancelled, past_due.")
        return normalized

    @field_validator("provider", "provider_customer_id", "provider_subscription_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def resolved_max_accounts(self) -> int:
        return resolve_organization_subscription_max_accounts(
            self.plan,
            self.max_accounts,
        )

    def validate_plan_account_range(self) -> None:
        # Business is sold as "up to 19 users", so it always receives the full
        # Business allowance instead of an accidentally lower manual seat value.
        # Enterprise defaults to 20 and may be set higher.
        self.resolved_max_accounts()


def normalize_email(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized or not EMAIL_PATTERN.match(normalized):
        raise ValueError("A valid email address is required.")
    return normalized


def normalize_user_id(user_id: str) -> str:
    normalized = (user_id or "").strip()
    if not normalized:
        raise ValueError("user_id is required.")
    return normalized


def normalize_role(role: str) -> OrganizationRole:
    normalized = (role or "").strip().lower()
    if normalized not in VALID_ROLES:
        raise ValueError("role must be one of: owner, admin, member.")
    return normalized  # type: ignore[return-value]


def normalize_member_status(status: str) -> OrganizationMemberStatus:
    normalized = (status or "").strip().lower()
    if normalized not in VALID_MEMBER_STATUSES:
        raise ValueError("status must be one of: active, invited, removed.")
    return normalized  # type: ignore[return-value]


def resolve_organization_subscription_max_accounts(
    plan: str,
    requested_max_accounts: int | None = None,
) -> int:
    normalized_plan = (plan or "").strip().lower()

    if normalized_plan == "business":
        # Product rule: Business includes up to 19 users by default.
        # Ignore manually supplied lower values like 5 so Business customers do
        # not have to contact support to unlock the rest of the Business tier.
        return BUSINESS_DEFAULT_MAX_ACCOUNTS

    if normalized_plan == "enterprise":
        if requested_max_accounts is None:
            return ENTERPRISE_DEFAULT_MAX_ACCOUNTS
        if requested_max_accounts < ENTERPRISE_DEFAULT_MAX_ACCOUNTS:
            raise ValueError(
                "enterprise subscriptions require max_accounts greater than or equal to 20."
            )
        return requested_max_accounts

    raise ValueError("plan must be one of: business, enterprise.")


def invited_user_id_for_email(email: str) -> str:
    return f"{INVITE_USER_ID_PREFIX}{normalize_email(email)}"


def current_user_email(current_user: AuthenticatedUser) -> str | None:
    email = current_user.claims.get("email")
    if not isinstance(email, str) or not email.strip():
        return None
    return normalize_email(email)


def resolve_member_identifier(member_user_id: str) -> str:
    """
    Resolve a route path value to the stored organization_members.user_id.

    Supports:
    - Auth0 subject values, e.g. auth0|abc123 or google-oauth2|123456
    - raw invited email values, e.g. user@example.com
    - stored invitation IDs, e.g. invite:user@example.com
    """

    raw = (member_user_id or "").strip()
    if not raw:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_member",
                "message": "member_user_id is required.",
            },
        )

    if raw.startswith(INVITE_USER_ID_PREFIX):
        return invited_user_id_for_email(raw.removeprefix(INVITE_USER_ID_PREFIX))

    if "@" in raw:
        return invited_user_id_for_email(raw)

    return raw


def user_public_payload(current_user: AuthenticatedUser) -> dict[str, Any]:
    return {
        "id": current_user.user_id,
        "name": current_user.claims.get("name"),
        "email": current_user.claims.get("email"),
        "picture": current_user.claims.get("picture"),
    }


def first_non_empty_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def current_user_profile(current_user: AuthenticatedUser) -> dict[str, Any]:
    email = first_non_empty_text(current_user.claims.get("email"))

    return {
        "user_id": current_user.user_id,
        "name": first_non_empty_text(
            current_user.claims.get("name"),
            current_user.claims.get("nickname"),
            current_user.claims.get("preferred_username"),
        ),
        "email": normalize_email(email) if email else None,
        "picture": first_non_empty_text(current_user.claims.get("picture")),
    }


def invitation_profile(email: str) -> dict[str, Any]:
    normalized_email = normalize_email(email)

    return {
        "name": normalized_email.split("@", 1)[0],
        "email": normalized_email,
        "picture": None,
    }


def update_current_member_profile_snapshots(
    conn,
    current_user: AuthenticatedUser,
) -> None:
    """
    Persist the authenticated user's safe display profile on any active team rows.

    This makes member lists stable without exposing Auth0 subject IDs. It also
    backfills older accepted rows the next time that member loads team data.
    """

    profile = current_user_profile(current_user)
    name = first_non_empty_text(profile.get("name"))
    email = first_non_empty_text(profile.get("email"))
    picture = first_non_empty_text(profile.get("picture"))

    if not any([name, email, picture]):
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE organization_members
            SET member_name = COALESCE(%s, member_name),
                member_email = COALESCE(%s, member_email),
                member_picture = COALESCE(%s, member_picture),
                updated_at = NOW()
            WHERE user_id = %s
              AND status = 'active'
            """,
            (name, email, picture, current_user.user_id),
        )


def normalize_auth0_domain(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    return normalized.rstrip("/") or None


def get_auth0_management_token() -> str | None:
    token = first_non_empty_text(
        os.getenv(AUTH0_MANAGEMENT_TOKEN_ENV),
        os.getenv(AUTH0_MANAGEMENT_TOKEN_FALLBACK_ENV),
    )
    return token


def get_cached_auth0_profile(user_id: str) -> dict[str, Any] | None:
    cached = _AUTH0_PROFILE_CACHE.get(user_id)
    if cached is None:
        return None

    cached_at, profile = cached
    if time.time() - cached_at > AUTH0_PROFILE_CACHE_TTL_SECONDS:
        _AUTH0_PROFILE_CACHE.pop(user_id, None)
        return None

    return profile


def cache_auth0_profile(user_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    _AUTH0_PROFILE_CACHE[user_id] = (time.time(), profile)
    return profile


def fetch_auth0_user_profile(user_id: str) -> dict[str, Any] | None:
    """
    Best-effort Auth0 Management API profile lookup.

    Required backend env vars:
    - AUTH0_DOMAIN
    - AUTH0_MANAGEMENT_API_TOKEN or AUTH0_MGMT_API_TOKEN

    The token needs Auth0 Management API permission to read users. If this is
    not configured, the endpoint still works and returns membership fields with
    profile values set to null where unavailable.
    """

    normalized_user_id = normalize_user_id(user_id)
    cached = get_cached_auth0_profile(normalized_user_id)
    if cached is not None:
        return cached

    domain = normalize_auth0_domain(os.getenv("AUTH0_DOMAIN"))
    token = get_auth0_management_token()

    if not domain or not token:
        return None

    url = (
        f"https://{domain}/api/v2/users/{quote(normalized_user_id, safe='')}"
        "?fields=user_id,name,email,picture,nickname,preferred_username"
        "&include_fields=true"
    )

    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=AUTH0_PROFILE_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            return cache_auth0_profile(normalized_user_id, {})
        response.raise_for_status()
        payload = response.json()
    except (RequestException, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    return cache_auth0_profile(normalized_user_id, payload)


def build_member_profile_map(
    member_rows: list[Any],
    current_user: AuthenticatedUser,
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {
        current_user.user_id: current_user_profile(current_user)
    }

    for row in member_rows:
        user_id = row[2]
        status = row[4]

        if not isinstance(user_id, str) or not user_id.strip():
            continue
        if user_id in profiles:
            continue
        if user_id.startswith(INVITE_USER_ID_PREFIX):
            continue
        if status != "active":
            continue

        profile = fetch_auth0_user_profile(user_id)
        if profile is not None:
            profiles[user_id] = profile

    return profiles


def row_to_organization_summary(row) -> dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "owner_user_id": row[2],
        "member": {
            "role": row[3],
            "status": row[4],
            "joined_at": row[5],
        },
        "subscription": {
            "plan": row[6],
            "max_accounts": row[7],
            "status": row[8],
            "active_members": row[9],
        },
        "created_at": row[10],
        "updated_at": row[11],
    }


def row_to_member(
    row,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user_id = row[2]
    is_email_invitation = isinstance(user_id, str) and user_id.startswith(
        INVITE_USER_ID_PREFIX
    )
    invited_email = (
        user_id.removeprefix(INVITE_USER_ID_PREFIX)
        if is_email_invitation
        else None
    )

    snapshot_name = row[10] if len(row) > 10 and isinstance(row[10], str) else None
    snapshot_email = row[11] if len(row) > 11 and isinstance(row[11], str) else None
    snapshot_picture = row[12] if len(row) > 12 and isinstance(row[12], str) else None

    profile = profile or {}
    profile_email = profile.get("email") if isinstance(profile.get("email"), str) else None
    profile_name = first_non_empty_text(
        profile.get("name"),
        profile.get("nickname"),
        profile.get("preferred_username"),
    )
    profile_picture = (
        profile.get("picture") if isinstance(profile.get("picture"), str) else None
    )

    # Pending invitations show the invited email. Accepted members show the
    # persisted safe snapshot first, then best-effort Auth0 profile data.
    email = invited_email or first_non_empty_text(snapshot_email, profile_email)
    name = first_non_empty_text(snapshot_name, profile_name)

    if not name and invited_email:
        name = invited_email.split("@", 1)[0]

    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": user_id,
        "role": row[3],
        "status": row[4],
        "invited_by_user_id": row[5],
        "invited_at": row[6],
        "joined_at": row[7],
        "created_at": row[8],
        "updated_at": row[9],
        "is_email_invitation": is_email_invitation,
        "name": name,
        "email": email,
        "picture": first_non_empty_text(snapshot_picture, profile_picture),
    }


def row_to_subscription(row) -> dict[str, Any] | None:
    if row is None:
        return None

    return {
        "id": row[0],
        "organization_id": row[1],
        "plan": row[2],
        "max_accounts": row[3],
        "status": row[4],
        "provider": row[5],
        "provider_customer_id": row[6],
        "provider_subscription_id": row[7],
        "current_period_start": row[8],
        "current_period_end": row[9],
        "active_members": row[10],
        "created_at": row[11],
        "updated_at": row[12],
    }


def get_active_membership(conn, organization_id: int, user_id: str) -> dict[str, Any] | None:
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


def require_active_member(conn, organization_id: int, current_user: AuthenticatedUser) -> dict[str, Any]:
    membership = get_active_membership(conn, organization_id, current_user.user_id)
    if membership is None:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_access_denied",
                "message": "You are not an active member of this organization.",
            },
        )
    return membership


def require_admin_or_owner(conn, organization_id: int, current_user: AuthenticatedUser) -> dict[str, Any]:
    membership = require_active_member(conn, organization_id, current_user)
    if membership["role"] not in {"owner", "admin"}:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_admin_required",
                "message": "Only organization owners or admins can perform this action.",
            },
        )
    return membership


def require_owner(conn, organization_id: int, current_user: AuthenticatedUser) -> dict[str, Any]:
    membership = require_active_member(conn, organization_id, current_user)
    if membership["role"] != "owner":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_owner_required",
                "message": "Only the organization owner can perform this action.",
            },
        )
    return membership


def get_member_for_update(conn, organization_id: int, member_user_id: str) -> dict[str, Any]:
    resolved_member_user_id = resolve_member_identifier(member_user_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, organization_id, user_id, role, status, invited_by_user_id
            FROM organization_members
            WHERE organization_id = %s
              AND user_id = %s
            """,
            (organization_id, resolved_member_user_id),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "member_not_found",
                "message": "Organization member was not found.",
            },
        )

    return {
        "id": row[0],
        "organization_id": row[1],
        "user_id": row[2],
        "role": row[3],
        "status": row[4],
        "invited_by_user_id": row[5],
    }


def active_owner_count(conn, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM organization_members
            WHERE organization_id = %s
              AND role = 'owner'
              AND status = 'active'
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    return int(row[0] if row else 0)




def invited_member_count(conn, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM organization_members
            WHERE organization_id = %s
              AND status = 'invited'
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    return int(row[0] if row else 0)


def get_active_organization_subscription(conn, organization_id: int) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT plan, status, current_period_end
            FROM organization_subscriptions
            WHERE organization_id = %s
              AND status = 'active'
              AND plan IN ('business', 'enterprise')
            LIMIT 1
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return {
        "plan": row[0],
        "status": row[1],
        "current_period_end": row[2],
    }


def assert_owner_can_exit_as_sole_member(conn, organization_id: int) -> None:
    active_members = active_member_count(conn, organization_id)
    invited_members = invited_member_count(conn, organization_id)

    if active_members > 1 or invited_members > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "ownership_transfer_or_member_removal_required",
                "message": (
                    "Transfer ownership to another active member, or remove every active member "
                    "and pending invitation before the owner can leave this subscription."
                ),
                "active_members": active_members,
                "invited_members": invited_members,
            },
        )


def insert_ownership_transfer_audit(
    conn,
    *,
    organization_id: int,
    previous_owner_user_id: str,
    new_owner_user_id: str,
    transferred_by_user_id: str,
    reason: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('organization_ownership_transfers')")
        row = cur.fetchone()
        if not row or not row[0]:
            return

        cur.execute(
            """
            INSERT INTO organization_ownership_transfers (
                organization_id,
                previous_owner_user_id,
                new_owner_user_id,
                transferred_by_user_id,
                reason
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                organization_id,
                previous_owner_user_id,
                new_owner_user_id,
                transferred_by_user_id,
                reason,
            ),
        )

def get_organization_owner_user_id(conn, organization_id: int) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id
            FROM organizations
            WHERE id = %s
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "organization_not_found",
                "message": "Organization was not found.",
            },
        )

    return normalize_user_id(row[0])


def ensure_organization_owner_membership(conn, organization_id: int) -> str:
    """
    Ensure the subscribing plan owner is always an active owner member.

    This protects both fresh subscriptions and existing organizations whose owner
    membership may have been accidentally changed before this policy existed.
    """

    owner_user_id = get_organization_owner_user_id(conn, organization_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organization_members (
                organization_id,
                user_id,
                role,
                status,
                joined_at
            )
            VALUES (%s, %s, 'owner', 'active', NOW())
            ON CONFLICT (organization_id, user_id) DO UPDATE SET
                role = 'owner',
                status = 'active',
                joined_at = COALESCE(organization_members.joined_at, NOW()),
                updated_at = NOW()
            """,
            (organization_id, owner_user_id),
        )

    return owner_user_id


def get_active_subscription_limit(conn, organization_id: int) -> int | None:
    """
    Return the organization's active subscription seat limit.

    A missing or inactive organization subscription does not create an active
    seat limit here. Paid entitlement is still enforced elsewhere by the
    subscription resolver.
    """

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max_accounts
            FROM organization_subscriptions
            WHERE organization_id = %s
              AND status = 'active'
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    if row is None or row[0] is None:
        return None

    return int(row[0])


def active_member_count(conn, organization_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM organization_members
            WHERE organization_id = %s
              AND status = 'active'
            """,
            (organization_id,),
        )
        row = cur.fetchone()

    return int(row[0] if row else 0)


def reserved_member_count(
    conn,
    organization_id: int,
    *,
    exclude_user_id: str | None = None,
) -> int:
    """
    Count seats reserved by active members plus pending invitations.

    Pending invitations reserve seats so an organization cannot invite past its
    subscribed account count and then exceed the limit when invitees accept.
    """

    with conn.cursor() as cur:
        if exclude_user_id:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM organization_members
                WHERE organization_id = %s
                  AND status IN ('active', 'invited')
                  AND user_id <> %s
                """,
                (organization_id, exclude_user_id),
            )
        else:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM organization_members
                WHERE organization_id = %s
                  AND status IN ('active', 'invited')
                """,
                (organization_id,),
            )

        row = cur.fetchone()

    return int(row[0] if row else 0)


def assert_invite_seat_available(
    conn,
    organization_id: int,
    *,
    invited_user_id: str,
) -> None:
    max_accounts = get_active_subscription_limit(conn, organization_id)

    if max_accounts is None:
        return

    reserved_accounts = reserved_member_count(
        conn,
        organization_id,
        exclude_user_id=invited_user_id,
    )

    if reserved_accounts >= max_accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "seat_limit_reached",
                "message": (
                    "This organization has reached its subscribed seat limit. "
                    "Remove a member/invitation or increase max_accounts before inviting another member."
                ),
                "max_accounts": max_accounts,
                "reserved_accounts": reserved_accounts,
            },
        )


def assert_acceptance_seat_available(
    conn,
    organization_id: int,
    *,
    accepting_user_id: str,
) -> None:
    max_accounts = get_active_subscription_limit(conn, organization_id)

    if max_accounts is None:
        return

    # Only active members consume accepted seats here. Invited placeholders do
    # not block acceptance because accepting converts one invited row into one
    # active row for the same person. Exclude the accepting user defensively so
    # a stale duplicate row cannot block an idempotent acceptance.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM organization_members
            WHERE organization_id = %s
              AND status = 'active'
              AND user_id <> %s
            """,
            (organization_id, accepting_user_id),
        )
        row = cur.fetchone()

    active_accounts = int(row[0] if row else 0)

    if active_accounts >= max_accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "seat_limit_reached",
                "message": (
                    "This organization has reached its subscribed seat limit. "
                    "Remove a member or increase max_accounts before accepting another invitation."
                ),
                "max_accounts": max_accounts,
                "active_accounts": active_accounts,
            },
        )


def assert_subscription_can_cover_active_members(
    conn,
    organization_id: int,
    *,
    max_accounts: int,
) -> int:
    active_accounts = active_member_count(conn, organization_id)

    if max_accounts < active_accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "max_accounts_below_active_members",
                "message": (
                    "max_accounts cannot be lower than the current number of active organization members."
                ),
                "max_accounts": max_accounts,
                "active_members": active_accounts,
            },
        )

    return active_accounts


def assert_can_modify_member(
    *,
    actor_membership: dict[str, Any],
    target_member: dict[str, Any],
    organization_owner_user_id: str | None = None,
    requested_role: str | None = None,
    requested_status: str | None = None,
) -> None:
    actor_role = actor_membership["role"]
    target_user_id = target_member["user_id"]
    owner_user_id = normalize_user_id(organization_owner_user_id) if organization_owner_user_id else None
    target_is_plan_owner = bool(owner_user_id and target_user_id == owner_user_id)

    # The subscribing/plan owner is permanent. They cannot be demoted or removed,
    # even by themselves or another owner-level legacy row.
    if target_is_plan_owner:
        if requested_status == "removed" or (
            requested_role is not None and requested_role != "owner"
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "plan_owner_immutable",
                    "message": "The plan owner cannot be changed to admin/member or removed from the organization.",
                },
            )

    # Admins may invite members, but only the plan owner can change accepted
    # member roles or remove members from the organization.
    if actor_role != "owner" and (requested_role is not None or requested_status == "removed"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "organization_owner_required",
                "message": "Only the organization owner can change member roles or remove members.",
            },
        )

    # Do not allow creating additional owners through member role updates. The
    # subscriber/plan owner remains the single permanent owner.
    if requested_role == "owner" and not target_is_plan_owner:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "plan_owner_immutable",
                "message": "Only the subscribing plan owner can have the owner role.",
            },
        )

    # Invited email placeholders must be accepted by the invited user before becoming active.
    if (
        requested_status == "active"
        and isinstance(target_member["user_id"], str)
        and target_member["user_id"].startswith(INVITE_USER_ID_PREFIX)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "invitation_acceptance_required",
                "message": "Email invitations must be accepted by the invited user before becoming active.",
            },
        )


def assert_can_remove_or_cancel_member(
    *,
    actor_membership: dict[str, Any],
    target_member: dict[str, Any],
    organization_owner_user_id: str | None = None,
) -> None:
    """
    Enforce removal/cancellation policy.

    - Owners can remove active admins/members and cancel any pending invitation.
    - Admins can only cancel pending invitations that were created by admins,
      not invitations created by the plan owner.
    - Members cannot remove members or cancel invitations.
    - The subscribing plan owner can never be removed through this endpoint.
    """

    actor_role = actor_membership["role"]
    target_user_id = target_member["user_id"]
    target_status = target_member["status"]
    owner_user_id = (
        normalize_user_id(organization_owner_user_id)
        if organization_owner_user_id
        else None
    )

    if owner_user_id and target_user_id == owner_user_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "plan_owner_immutable",
                "message": "The plan owner cannot be removed from the organization.",
            },
        )

    if actor_role == "owner":
        return

    if actor_role == "admin" and target_status == "invited":
        invited_by_user_id = target_member.get("invited_by_user_id")

        if not invited_by_user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "invitation_cancel_denied",
                    "message": "Admins can only cancel invitations created by admins.",
                },
            )

        if owner_user_id and invited_by_user_id == owner_user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "owner_invitation_cancel_denied",
                    "message": "Admins cannot cancel invitations created by the organization owner.",
                },
            )

        return

    raise HTTPException(
        status_code=403,
        detail={
            "error": "organization_owner_required",
            "message": "Only the organization owner can remove active members. Admins can only cancel pending invitations created by admins.",
        },
    )


@router.post("")
def create_organization(
    payload: CreateOrganizationRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    owner_profile = current_user_profile(current_user)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO organizations (name, owner_user_id)
                    VALUES (%s, %s)
                    RETURNING id, name, owner_user_id, created_at, updated_at
                    """,
                    (payload.name, current_user.user_id),
                )
                organization = cur.fetchone()

                if organization is None:
                    raise RuntimeError("Failed to create organization.")

                organization_id = organization[0]

                cur.execute(
                    """
                    INSERT INTO organization_members (
                        organization_id,
                        user_id,
                        member_name,
                        member_email,
                        member_picture,
                        role,
                        status,
                        joined_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 'owner', 'active', NOW())
                    ON CONFLICT (organization_id, user_id) DO UPDATE SET
                        member_name = COALESCE(EXCLUDED.member_name, organization_members.member_name),
                        member_email = COALESCE(EXCLUDED.member_email, organization_members.member_email),
                        member_picture = COALESCE(EXCLUDED.member_picture, organization_members.member_picture),
                        role = 'owner',
                        status = 'active',
                        joined_at = COALESCE(organization_members.joined_at, NOW()),
                        updated_at = NOW()
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (
                        organization_id,
                        current_user.user_id,
                        owner_profile.get("name"),
                        owner_profile.get("email"),
                        owner_profile.get("picture"),
                    ),
                )
                member = cur.fetchone()

        return {
            "success": True,
            "organization": {
                "id": organization[0],
                "name": organization[1],
                "owner_user_id": organization[2],
                "created_at": organization[3],
                "updated_at": organization[4],
            },
            "member": row_to_member(member),
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_organization",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "organization_create_failed",
                "message": "Could not create organization.",
            },
        ) from exc


@router.get("/me")
def list_my_organizations(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    email = current_user_email(current_user)
    invited_user_id = invited_user_id_for_email(email) if email else None

    try:
        with get_db() as conn:
            update_current_member_profile_snapshots(conn, current_user)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        o.id,
                        o.name,
                        o.owner_user_id,
                        om.role,
                        om.status,
                        om.joined_at,
                        os.plan,
                        os.max_accounts,
                        os.status AS subscription_status,
                        (
                            SELECT COUNT(*)
                            FROM organization_members active_om
                            WHERE active_om.organization_id = o.id
                              AND active_om.status = 'active'
                        ) AS active_members,
                        o.created_at,
                        o.updated_at
                    FROM organization_members om
                    JOIN organizations o
                      ON o.id = om.organization_id
                    LEFT JOIN organization_subscriptions os
                      ON os.organization_id = o.id
                    WHERE om.user_id = %s
                      AND om.status = 'active'
                    ORDER BY o.updated_at DESC, o.id DESC
                    """,
                    (current_user.user_id,),
                )
                active_rows = cur.fetchall()

                invite_rows = []
                if invited_user_id is not None:
                    cur.execute(
                        """
                        SELECT
                            o.id,
                            o.name,
                            o.owner_user_id,
                            om.role,
                            om.status,
                            om.joined_at,
                            os.plan,
                            os.max_accounts,
                            os.status AS subscription_status,
                            (
                                SELECT COUNT(*)
                                FROM organization_members active_om
                                WHERE active_om.organization_id = o.id
                                  AND active_om.status = 'active'
                            ) AS active_members,
                            o.created_at,
                            o.updated_at
                        FROM organization_members om
                        JOIN organizations o
                          ON o.id = om.organization_id
                        LEFT JOIN organization_subscriptions os
                          ON os.organization_id = o.id
                        WHERE om.user_id = %s
                          AND om.status = 'invited'
                        ORDER BY om.invited_at DESC NULLS LAST, o.id DESC
                        """,
                        (invited_user_id,),
                    )
                    invite_rows = cur.fetchall()

        return {
            "success": True,
            "user": user_public_payload(current_user),
            "organizations": [row_to_organization_summary(row) for row in active_rows],
            "invitations": [row_to_organization_summary(row) for row in invite_rows],
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "organizations_load_failed",
                "message": "Could not load organizations.",
            },
        ) from exc


@router.get("/{organization_id}")
def get_organization(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            update_current_member_profile_snapshots(conn, current_user)
            require_active_member(conn, organization_id, current_user)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, owner_user_id, created_at, updated_at
                    FROM organizations
                    WHERE id = %s
                    """,
                    (organization_id,),
                )
                organization = cur.fetchone()

                if organization is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "organization_not_found",
                            "message": "Organization was not found.",
                        },
                    )

                cur.execute(
                    """
                    SELECT id, organization_id, user_id, role, status,
                           invited_by_user_id, invited_at, joined_at,
                           created_at, updated_at,
                           member_name, member_email, member_picture
                    FROM organization_members
                    WHERE organization_id = %s
                      AND status <> 'removed'
                    ORDER BY
                        CASE role
                            WHEN 'owner' THEN 1
                            WHEN 'admin' THEN 2
                            ELSE 3
                        END,
                        created_at ASC,
                        id ASC
                    """,
                    (organization_id,),
                )
                members = cur.fetchall()
                member_profiles = build_member_profile_map(members, current_user)

        return {
            "success": True,
            "organization": {
                "id": organization[0],
                "name": organization[1],
                "owner_user_id": organization[2],
                "created_at": organization[3],
                "updated_at": organization[4],
            },
            "members": [
                row_to_member(row, member_profiles.get(row[2])) for row in members
            ],
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "organization_load_failed",
                "message": "Could not load organization.",
            },
        ) from exc


@router.patch("/{organization_id}")
def update_organization(
    payload: UpdateOrganizationRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Rename an organization. Only its active owner may do this."""
    try:
        with get_db() as conn:
            require_owner(conn, organization_id, current_user)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organizations
                    SET name = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, owner_user_id, created_at, updated_at
                    """,
                    (payload.name, organization_id),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "organization_not_found",
                            "message": "Organization was not found.",
                        },
                    )

        organization = {
            "id": row[0],
            "name": row[1],
            "owner_user_id": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }
        dispatch_organization_realtime_event(
            organization_id=organization_id,
            event={
                "type": "organization.updated",
                "organization": organization,
                "actor": user_public_payload(current_user),
            },
        )
        return {"success": True, "organization": organization}

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_organization_name", "message": str(exc)},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "organization_update_failed",
                "message": "Could not update organization.",
            },
        ) from exc


@router.post("/{organization_id}/members")
def invite_member_by_email(
    payload: InviteMemberRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    invited_user_id = invited_user_id_for_email(payload.email)
    invited_profile = invitation_profile(payload.email)

    try:
        with get_db() as conn:
            actor_membership = require_admin_or_owner(conn, organization_id, current_user)

            if payload.role == "owner":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "plan_owner_immutable",
                        "message": "Only the subscribing plan owner can have the owner role.",
                    },
                )

            if actor_membership["role"] != "owner" and payload.role == "admin":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "organization_owner_required",
                        "message": "Only the organization owner can invite admins.",
                    },
                )

            if payload.email == current_user_email(current_user):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "self_invite_not_allowed",
                        "message": "You are already represented by your authenticated account.",
                    },
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM organizations
                    WHERE id = %s
                    """,
                    (organization_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "organization_not_found",
                            "message": "Organization was not found.",
                        },
                    )

                assert_invite_seat_available(
                    conn,
                    organization_id,
                    invited_user_id=invited_user_id,
                )

                cur.execute(
                    """
                    INSERT INTO organization_members (
                        organization_id,
                        user_id,
                        member_name,
                        member_email,
                        member_picture,
                        role,
                        status,
                        invited_by_user_id,
                        invited_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, 'invited', %s, NOW())
                    ON CONFLICT (organization_id, user_id) DO UPDATE SET
                        member_name = COALESCE(EXCLUDED.member_name, organization_members.member_name),
                        member_email = COALESCE(EXCLUDED.member_email, organization_members.member_email),
                        member_picture = COALESCE(EXCLUDED.member_picture, organization_members.member_picture),
                        role = EXCLUDED.role,
                        status = 'invited',
                        invited_by_user_id = EXCLUDED.invited_by_user_id,
                        invited_at = NOW(),
                        updated_at = NOW()
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (
                        organization_id,
                        invited_user_id,
                        invited_profile.get("name"),
                        invited_profile.get("email"),
                        invited_profile.get("picture"),
                        payload.role,
                        current_user.user_id,
                    ),
                )
                member = cur.fetchone()

                cur.execute(
                    """
                    SELECT
                        o.id,
                        o.name,
                        o.owner_user_id,
                        om.role,
                        om.status,
                        om.joined_at,
                        os.plan,
                        os.max_accounts,
                        os.status AS subscription_status,
                        (
                            SELECT COUNT(*)
                            FROM organization_members active_om
                            WHERE active_om.organization_id = o.id
                              AND active_om.status = 'active'
                        ) AS active_members,
                        o.created_at,
                        o.updated_at
                    FROM organization_members om
                    JOIN organizations o
                      ON o.id = om.organization_id
                    LEFT JOIN organization_subscriptions os
                      ON os.organization_id = o.id
                    WHERE om.organization_id = %s
                      AND om.user_id = %s
                    LIMIT 1
                    """,
                    (organization_id, invited_user_id),
                )
                invitation_summary_row = cur.fetchone()

        invitation = row_to_member(member)
        invitation_summary = (
            row_to_organization_summary(invitation_summary_row)
            if invitation_summary_row is not None
            else None
        )

        if invitation_summary is not None:
            dispatch_account_realtime_event_by_email(
                email=payload.email,
                event={
                    "type": "organization.invitation.created",
                    "invitation": invitation_summary,
                    "organization": {
                        "id": invitation_summary["id"],
                        "name": invitation_summary["name"],
                    },
                    "member": invitation_summary["member"],
                    "subscription": invitation_summary["subscription"],
                    "actor": user_public_payload(current_user),
                },
            )

        return {
            "success": True,
            "invitation": invitation,
            "invitation_summary": invitation_summary,
            "message": "Invitation recorded. The invitee will be notified in realtime if they are online.",
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_invitation",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "member_invite_failed",
                "message": "Could not invite organization member.",
            },
        ) from exc


@router.post("/{organization_id}/invitations/accept")
def accept_email_invitation(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    email = current_user_email(current_user)
    if email is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "email_required",
                "message": "Your authenticated profile does not include an email address.",
            },
        )

    invited_user_id = invited_user_id_for_email(email)
    accepter_profile = current_user_profile(current_user)

    try:
        with get_db() as conn:
            update_current_member_profile_snapshots(conn, current_user)

            with conn.cursor() as cur:
                # Idempotency guard:
                # If the invite was already accepted, the pending invitation row may no
                # longer exist. In that case, return the active membership instead of
                # failing with invitation_not_found. This protects the UI from double
                # clicks, retries, and React/browser duplicate submissions.
                cur.execute(
                    """
                    SELECT id, organization_id, user_id, role, status,
                           invited_by_user_id, invited_at, joined_at,
                           created_at, updated_at,
                           member_name, member_email, member_picture
                    FROM organization_members
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'active'
                    """,
                    (organization_id, current_user.user_id),
                )
                already_active_member = cur.fetchone()

                if already_active_member is not None:
                    return {
                        "success": True,
                        "member": row_to_member(already_active_member),
                        "already_accepted": True,
                    }

                cur.execute(
                    """
                    SELECT id, role
                    FROM organization_members
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'invited'
                    """,
                    (organization_id, invited_user_id),
                )
                invite = cur.fetchone()

                if invite is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "invitation_not_found",
                            "message": "No pending invitation was found for your email address.",
                        },
                    )

                invite_role = invite[1]

                assert_acceptance_seat_available(
                    conn,
                    organization_id,
                    accepting_user_id=current_user.user_id,
                )

                cur.execute(
                    """
                    SELECT id
                    FROM organization_members
                    WHERE organization_id = %s
                      AND user_id = %s
                    """,
                    (organization_id, current_user.user_id),
                )
                existing_membership = cur.fetchone()

                if existing_membership is not None:
                    cur.execute(
                        """
                        UPDATE organization_members
                        SET role = %s,
                            member_name = COALESCE(%s, member_name),
                            member_email = COALESCE(%s, member_email),
                            member_picture = COALESCE(%s, member_picture),
                            status = 'active',
                            joined_at = COALESCE(joined_at, NOW()),
                            updated_at = NOW()
                        WHERE organization_id = %s
                          AND user_id = %s
                        RETURNING id, organization_id, user_id, role, status,
                                  invited_by_user_id, invited_at, joined_at,
                                  created_at, updated_at,
                                  member_name, member_email, member_picture
                        """,
                        (
                            invite_role,
                            accepter_profile.get("name"),
                            accepter_profile.get("email"),
                            accepter_profile.get("picture"),
                            organization_id,
                            current_user.user_id,
                        ),
                    )
                    accepted_member = cur.fetchone()

                    cur.execute(
                        """
                        UPDATE organization_members
                        SET status = 'removed',
                            updated_at = NOW()
                        WHERE organization_id = %s
                          AND user_id = %s
                        """,
                        (organization_id, invited_user_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE organization_members
                        SET user_id = %s,
                            member_name = COALESCE(%s, member_name),
                            member_email = COALESCE(%s, member_email),
                            member_picture = COALESCE(%s, member_picture),
                            status = 'active',
                            joined_at = NOW(),
                            updated_at = NOW()
                        WHERE organization_id = %s
                          AND user_id = %s
                          AND status = 'invited'
                        RETURNING id, organization_id, user_id, role, status,
                                  invited_by_user_id, invited_at, joined_at,
                                  created_at, updated_at,
                                  member_name, member_email, member_picture
                        """,
                        (
                            current_user.user_id,
                            accepter_profile.get("name"),
                            accepter_profile.get("email"),
                            accepter_profile.get("picture"),
                            organization_id,
                            invited_user_id,
                        ),
                    )
                    accepted_member = cur.fetchone()

                if accepted_member is None:
                    raise RuntimeError("Failed to accept invitation.")

        return {
            "success": True,
            "member": row_to_member(accepted_member),
            "already_accepted": False,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invitation_accept_failed",
                "message": "Could not accept organization invitation.",
            },
        ) from exc


@router.post("/{organization_id}/invitations/deny")
def deny_email_invitation(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    email = current_user_email(current_user)
    if email is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "email_required",
                "message": "Your authenticated profile does not include an email address.",
            },
        )

    invited_user_id = invited_user_id_for_email(email)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, organization_id, user_id, role, status,
                           invited_by_user_id, invited_at, joined_at,
                           created_at, updated_at,
                           member_name, member_email, member_picture
                    FROM organization_members
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'invited'
                    """,
                    (organization_id, invited_user_id),
                )
                invite = cur.fetchone()

                if invite is None:
                    cur.execute(
                        """
                        SELECT id, organization_id, user_id, role, status,
                               invited_by_user_id, invited_at, joined_at,
                               created_at, updated_at,
                               member_name, member_email, member_picture
                        FROM organization_members
                        WHERE organization_id = %s
                          AND user_id = %s
                          AND status = 'removed'
                        """,
                        (organization_id, invited_user_id),
                    )
                    already_denied = cur.fetchone()

                    if already_denied is not None:
                        return {
                            "success": True,
                            "member": row_to_member(already_denied),
                            "already_denied": True,
                        }

                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "invitation_not_found",
                            "message": "No pending invitation was found for your email address.",
                        },
                    )

                cur.execute(
                    """
                    UPDATE organization_members
                    SET status = 'removed',
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (invite[0],),
                )
                denied_member = cur.fetchone()

                if denied_member is None:
                    raise RuntimeError("Failed to deny invitation.")

        return {
            "success": True,
            "member": row_to_member(denied_member),
            "already_denied": False,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "invitation_deny_failed",
                "message": "Could not deny organization invitation.",
            },
        ) from exc


@router.patch("/{organization_id}/members/{member_user_id}")
def update_member(
    payload: UpdateMemberRequest,
    organization_id: int = Path(..., ge=1),
    member_user_id: str = Path(..., min_length=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    if payload.role is None and payload.status is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_member_update",
                "message": "Provide at least one field to update: role or status.",
            },
        )

    try:
        revocation_payload: dict[str, Any] | None = None
        event_payload: dict[str, Any] | None = None

        with get_db() as conn:
            actor_membership = require_admin_or_owner(conn, organization_id, current_user)
            target_member = get_member_for_update(conn, organization_id, member_user_id)
            owner_user_id = ensure_organization_owner_membership(conn, organization_id)

            assert_can_modify_member(
                actor_membership=actor_membership,
                target_member=target_member,
                organization_owner_user_id=owner_user_id,
                requested_role=payload.role,
                requested_status=payload.status,
            )

            if (
                target_member["role"] == "owner"
                and payload.status == "removed"
                and active_owner_count(conn, organization_id) <= 1
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "last_owner_required",
                        "message": "An organization must keep at least one active owner.",
                    },
                )

            if (
                target_member["role"] == "owner"
                and payload.role is not None
                and payload.role != "owner"
                and active_owner_count(conn, organization_id) <= 1
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "last_owner_required",
                        "message": "Assign another owner before changing the last owner's role.",
                    },
                )

            update_role = payload.role if payload.role is not None else target_member["role"]
            update_status = (
                payload.status if payload.status is not None else target_member["status"]
            )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organization_members
                    SET role = %s,
                        status = %s,
                        joined_at = CASE
                            WHEN %s = 'active' AND joined_at IS NULL THEN NOW()
                            ELSE joined_at
                        END,
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND user_id = %s
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (
                        update_role,
                        update_status,
                        update_status,
                        organization_id,
                        target_member["user_id"],
                    ),
                )
                member = cur.fetchone()

                if member is None:
                    raise RuntimeError("Failed to update member.")

            member_payload = row_to_member(member)
            was_active = target_member["status"] == "active"
            is_removed = update_status == "removed"

            if was_active and is_removed:
                revocation_payload = revoke_organization_member_communications(
                    conn,
                    organization_id=organization_id,
                    user_id=target_member["user_id"],
                    actor_user_id=current_user.user_id,
                    reason="membership_removed",
                )

            event_payload = {
                "type": (
                    "organization.member.removed"
                    if was_active and is_removed
                    else "organization.member.updated"
                ),
                "member": member_payload,
                "actor": user_public_payload(current_user),
            }

        revocation_delivered = (
            dispatch_organization_member_revocation(revocation_payload)
            if revocation_payload is not None
            else None
        )
        if event_payload is not None:
            dispatch_organization_realtime_event(
                organization_id=organization_id,
                event=event_payload,
                exclude_user_ids=(
                    {target_member["user_id"]}
                    if revocation_payload is not None
                    else set()
                ),
            )

        return {
            "success": True,
            "member": member_payload,
            "revocation": (
                {
                    "status": "delivered" if revocation_delivered else "queued",
                    "conversation_accesses_removed": revocation_payload[
                        "revoked_conversation_count"
                    ],
                    "call_accesses_removed": revocation_payload[
                        "revoked_call_count"
                    ],
                }
                if revocation_payload is not None
                else None
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "member_update_failed",
                "message": "Could not update organization member.",
            },
        ) from exc


@router.post("/{organization_id}/transfer-ownership")
def transfer_organization_ownership(
    payload: TransferOwnershipRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        event_payload: dict[str, Any] | None = None

        with get_db() as conn:
            actor_membership = require_owner(conn, organization_id, current_user)
            current_owner_user_id = ensure_organization_owner_membership(conn, organization_id)

            if current_user.user_id != current_owner_user_id or actor_membership["role"] != "owner":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "organization_owner_required",
                        "message": "Only the current subscribing plan owner can transfer ownership.",
                    },
                )

            if payload.new_owner_user_id == current_owner_user_id:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "invalid_ownership_transfer",
                        "message": "Choose a different active member as the new owner.",
                    },
                )

            target_member = get_member_for_update(conn, organization_id, payload.new_owner_user_id)
            if target_member["status"] != "active" or target_member["user_id"].startswith(INVITE_USER_ID_PREFIX):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "new_owner_must_be_active_member",
                        "message": "The new owner must be an active accepted member of the organization.",
                    },
                )

            with conn.cursor() as cur:
                # Drop the old owner role first to satisfy the one-active-owner partial unique index.
                cur.execute(
                    """
                    UPDATE organization_members
                    SET role = 'member',
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'active'
                    """,
                    (organization_id, current_owner_user_id),
                )
                cur.execute(
                    """
                    UPDATE organization_members
                    SET role = 'owner',
                        status = 'active',
                        joined_at = COALESCE(joined_at, NOW()),
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'active'
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (organization_id, payload.new_owner_user_id),
                )
                new_owner_row = cur.fetchone()

                if new_owner_row is None:
                    raise RuntimeError("Failed to assign new owner.")

                cur.execute(
                    """
                    UPDATE organizations
                    SET owner_user_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, name, owner_user_id, created_at, updated_at
                    """,
                    (payload.new_owner_user_id, organization_id),
                )
                organization_row = cur.fetchone()

                if organization_row is None:
                    raise RuntimeError("Failed to update organization owner.")

            insert_ownership_transfer_audit(
                conn,
                organization_id=organization_id,
                previous_owner_user_id=current_owner_user_id,
                new_owner_user_id=payload.new_owner_user_id,
                transferred_by_user_id=current_user.user_id,
                reason=payload.reason,
            )

            new_owner = row_to_member(new_owner_row)
            event_payload = {
                "type": "organization.ownership.transferred",
                "organization": {
                    "id": organization_row[0],
                    "name": organization_row[1],
                    "owner_user_id": organization_row[2],
                },
                "previous_owner_user_id": current_owner_user_id,
                "new_owner_user_id": payload.new_owner_user_id,
                "new_owner": new_owner,
                "actor": user_public_payload(current_user),
            }

        if event_payload is not None:
            dispatch_organization_realtime_event(
                organization_id=organization_id,
                event=event_payload,
            )

        return {
            "success": True,
            "transfer": event_payload,
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "ownership_transfer_failed",
                "message": "Could not transfer organization ownership.",
            },
        ) from exc


@router.post("/{organization_id}/leave")
def leave_organization(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        event_payload: dict[str, Any] | None = None
        lifecycle_payload: dict[str, Any] | None = None
        revocation_payload: dict[str, Any] | None = None

        with get_db() as conn:
            membership = require_active_member(conn, organization_id, current_user)
            owner_user_id = ensure_organization_owner_membership(conn, organization_id)
            is_owner = current_user.user_id == owner_user_id or membership["role"] == "owner"

            if is_owner:
                assert_owner_can_exit_as_sole_member(conn, organization_id)
                subscription = get_active_organization_subscription(conn, organization_id)
                if subscription is None:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "active_subscription_required",
                            "message": "An active Business or Enterprise subscription is required for owner exit.",
                        },
                    )
                restore_deadline, used_fallback_deadline = resolve_restore_deadline(
                    subscription.get("current_period_end")
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organization_members
                    SET status = 'removed',
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND user_id = %s
                      AND status = 'active'
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (organization_id, current_user.user_id),
                )
                member = cur.fetchone()

                if member is None:
                    raise RuntimeError("Failed to leave organization.")

                if is_owner:
                    cur.execute(
                        """
                        UPDATE organization_subscriptions
                        SET status = 'cancelled',
                            updated_at = NOW()
                        WHERE organization_id = %s
                          AND status = 'active'
                        """,
                        (organization_id,),
                    )

            member_payload = row_to_member(member)
            revocation_payload = revoke_organization_member_communications(
                conn,
                organization_id=organization_id,
                user_id=current_user.user_id,
                actor_user_id=current_user.user_id,
                reason="membership_left",
            )
            event_payload = {
                "type": "organization.member.left",
                "member": member_payload,
                "actor": user_public_payload(current_user),
                "owner_exit": is_owner,
            }

            if is_owner:
                lifecycle = create_pending_account_deletion(
                    conn,
                    user_id=current_user.user_id,
                    reason="sole_owner_subscription_cancelled_for_account_deletion",
                    restore_deadline=restore_deadline,
                    metadata={
                        "organization_owner_exit": True,
                        "organizations": [
                            {
                                "organization_id": organization_id,
                                "organization_name": None,
                                "plan": subscription.get("plan"),
                                "current_period_end": subscription.get("current_period_end").isoformat()
                                if hasattr(subscription.get("current_period_end"), "isoformat")
                                else None,
                            }
                        ],
                        "used_fallback_restore_deadline": used_fallback_deadline,
                    },
                )
                lifecycle_payload = {
                    "status": lifecycle.get("status"),
                    "restore_deadline": lifecycle.get("restore_deadline"),
                    "purge_after": lifecycle.get("purge_after"),
                }

        revocation_delivered = (
            dispatch_organization_member_revocation(revocation_payload)
            if revocation_payload is not None
            else False
        )

        if event_payload is not None:
            dispatch_organization_realtime_event(
                organization_id=organization_id,
                event=event_payload,
                exclude_user_ids={current_user.user_id},
            )

        return {
            "success": True,
            "member": member_payload,
            "owner_exit": bool(lifecycle_payload),
            "account_lifecycle": lifecycle_payload,
            "revocation": {
                "status": "delivered" if revocation_delivered else "queued",
                "conversation_accesses_removed": revocation_payload[
                    "revoked_conversation_count"
                ],
                "call_accesses_removed": revocation_payload[
                    "revoked_call_count"
                ],
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "organization_leave_failed",
                "message": "Could not leave organization.",
            },
        ) from exc


@router.delete("/{organization_id}/members/{member_user_id}")
def remove_member(
    organization_id: int = Path(..., ge=1),
    member_user_id: str = Path(..., min_length=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        revocation_payload: dict[str, Any] | None = None

        with get_db() as conn:
            actor_membership = require_admin_or_owner(conn, organization_id, current_user)
            target_member = get_member_for_update(conn, organization_id, member_user_id)
            owner_user_id = ensure_organization_owner_membership(conn, organization_id)

            assert_can_remove_or_cancel_member(
                actor_membership=actor_membership,
                target_member=target_member,
                organization_owner_user_id=owner_user_id,
            )

            if (
                target_member["role"] == "owner"
                and active_owner_count(conn, organization_id) <= 1
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "last_owner_required",
                        "message": "An organization must keep at least one active owner.",
                    },
                )

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE organization_members
                    SET status = 'removed',
                        updated_at = NOW()
                    WHERE organization_id = %s
                      AND user_id = %s
                    RETURNING id, organization_id, user_id, role, status,
                              invited_by_user_id, invited_at, joined_at,
                              created_at, updated_at,
                              member_name, member_email, member_picture
                    """,
                    (organization_id, target_member["user_id"]),
                )
                member = cur.fetchone()

                if member is None:
                    raise RuntimeError("Failed to remove member.")

            member_payload = row_to_member(member)
            if target_member["status"] == "active":
                revocation_payload = revoke_organization_member_communications(
                    conn,
                    organization_id=organization_id,
                    user_id=target_member["user_id"],
                    actor_user_id=current_user.user_id,
                    reason="membership_removed",
                )

        revocation_delivered = (
            dispatch_organization_member_revocation(revocation_payload)
            if revocation_payload is not None
            else None
        )

        dispatch_organization_realtime_event(
            organization_id=organization_id,
            event={
                "type": "organization.member.removed",
                "member": member_payload,
                "actor": user_public_payload(current_user),
                "invitation_cancelled": target_member["status"] == "invited",
            },
            exclude_user_ids={target_member["user_id"]},
        )

        return {
            "success": True,
            "member": member_payload,
            "revocation": (
                {
                    "status": "delivered" if revocation_delivered else "queued",
                    "conversation_accesses_removed": revocation_payload[
                        "revoked_conversation_count"
                    ],
                    "call_accesses_removed": revocation_payload[
                        "revoked_call_count"
                    ],
                }
                if revocation_payload is not None
                else None
            ),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "member_remove_failed",
                "message": "Could not remove organization member.",
            },
        ) from exc


@router.put("/{organization_id}/subscription")
@router.patch("/{organization_id}/subscription")
def upsert_organization_subscription(
    payload: UpdateOrganizationSubscriptionRequest,
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(
        require_scopes("write:organization_subscriptions")
    ),
):
    """
    Create or update an organization subscription.

    This is a platform-admin endpoint, not an organization-owner endpoint.
    The caller must have the Auth0 API permission/scope:
    write:organization_subscriptions
    """

    try:
        resolved_max_accounts = payload.resolved_max_accounts()

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM organizations
                    WHERE id = %s
                    """,
                    (organization_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail={
                            "error": "organization_not_found",
                            "message": "Organization was not found.",
                        },
                    )

                ensure_organization_owner_membership(conn, organization_id)

                assert_subscription_can_cover_active_members(
                    conn,
                    organization_id,
                    max_accounts=resolved_max_accounts,
                )

                cur.execute(
                    """
                    INSERT INTO organization_subscriptions (
                        organization_id,
                        plan,
                        max_accounts,
                        status,
                        provider,
                        provider_customer_id,
                        provider_subscription_id,
                        current_period_start,
                        current_period_end
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id) DO UPDATE SET
                        plan = EXCLUDED.plan,
                        max_accounts = EXCLUDED.max_accounts,
                        status = EXCLUDED.status,
                        provider = EXCLUDED.provider,
                        provider_customer_id = EXCLUDED.provider_customer_id,
                        provider_subscription_id = EXCLUDED.provider_subscription_id,
                        current_period_start = EXCLUDED.current_period_start,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at = NOW()
                    RETURNING
                        id,
                        organization_id,
                        plan,
                        max_accounts,
                        status,
                        provider,
                        provider_customer_id,
                        provider_subscription_id,
                        current_period_start,
                        current_period_end,
                        (
                            SELECT COUNT(*)
                            FROM organization_members om
                            WHERE om.organization_id = organization_subscriptions.organization_id
                              AND om.status = 'active'
                        ) AS active_members,
                        created_at,
                        updated_at
                    """,
                    (
                        organization_id,
                        payload.plan,
                        resolved_max_accounts,
                        payload.status,
                        payload.provider,
                        payload.provider_customer_id,
                        payload.provider_subscription_id,
                        payload.current_period_start,
                        payload.current_period_end,
                    ),
                )
                subscription = cur.fetchone()

                if subscription is None:
                    raise RuntimeError("Failed to upsert organization subscription.")

        return {
            "success": True,
            "subscription": row_to_subscription(subscription),
        }

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_subscription",
                "message": str(exc),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "subscription_update_failed",
                "message": "Could not update organization subscription.",
            },
        ) from exc


@router.get("/{organization_id}/subscription")
def get_organization_subscription(
    organization_id: int = Path(..., ge=1),
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    try:
        with get_db() as conn:
            require_active_member(conn, organization_id, current_user)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        os.id,
                        os.organization_id,
                        os.plan,
                        os.max_accounts,
                        os.status,
                        os.provider,
                        os.provider_customer_id,
                        os.provider_subscription_id,
                        os.current_period_start,
                        os.current_period_end,
                        (
                            SELECT COUNT(*)
                            FROM organization_members om
                            WHERE om.organization_id = os.organization_id
                              AND om.status = 'active'
                        ) AS active_members,
                        os.created_at,
                        os.updated_at
                    FROM organization_subscriptions os
                    WHERE os.organization_id = %s
                    """,
                    (organization_id,),
                )
                subscription = cur.fetchone()

        return {
            "success": True,
            "subscription": row_to_subscription(subscription),
        }

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "subscription_load_failed",
                "message": "Could not load organization subscription.",
            },
        ) from exc
