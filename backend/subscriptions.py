from __future__ import annotations

"""
Subscription and entitlement source of truth.

Responsibilities:
- store/read the user's current plan from PostgreSQL
- normalize paid/free entitlement for rate-limit routing
- enforce the account-count contract for Personal, Business, and Enterprise
- resolve Business/Enterprise access through organization membership
- provide upsert helpers that can later be called from billing webhooks

Non-responsibilities:
- payment checkout
- invoice management
- card collection
- provider-specific webhook signature verification
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal

from backend.database import get_db

PlanName = Literal["free", "personal", "business", "enterprise"]
UserSubscriptionPlanName = Literal["free", "personal"]
OrganizationSubscriptionPlanName = Literal["business", "enterprise"]
SubscriptionStatus = Literal["active", "inactive", "cancelled", "past_due"]
OrganizationRole = Literal["owner", "admin", "member"]
OrganizationMemberStatus = Literal["active", "invited", "removed"]
EntitlementSource = Literal["user", "organization"]

VALID_PLANS: set[str] = {"free", "personal", "business", "enterprise"}
VALID_USER_SUBSCRIPTION_PLANS: set[str] = {"free", "personal"}
VALID_ORGANIZATION_SUBSCRIPTION_PLANS: set[str] = {"business", "enterprise"}
VALID_STATUSES: set[str] = {"active", "inactive", "cancelled", "past_due"}
VALID_ORGANIZATION_ROLES: set[str] = {"owner", "admin", "member"}
VALID_ORGANIZATION_MEMBER_STATUSES: set[str] = {"active", "invited", "removed"}

ACTIVE_STATUS = "active"
ACTIVE_MEMBER_STATUS = "active"

ORGANIZATION_NAME_MIN_LENGTH = 2
ORGANIZATION_NAME_MAX_LENGTH = 100
ORGANIZATION_NAME_WHITESPACE_RE = re.compile(r"\s+")

PLAN_ACCOUNT_LIMITS: dict[str, tuple[int, int | None]] = {
    "free": (1, None),
    "personal": (1, 1),
    "business": (2, 19),
    "enterprise": (20, None),
}

DEFAULT_ORGANIZATION_PLAN_ACCOUNTS: dict[str, int] = {
    "business": 19,
    "enterprise": 20,
}


@dataclass(frozen=True)
class UserEntitlement:
    """
    Normalized entitlement used by the rate-limit dependency bridge.

    Personal subscriptions are user-level entitlements from user_subscriptions.
    Business and Enterprise subscriptions are organization-level entitlements
    resolved through active organization membership.
    """

    user_id: str
    plan: PlanName
    account_count: int
    status: SubscriptionStatus
    is_paid: bool
    source: EntitlementSource = "user"
    organization_id: int | None = None
    organization_name: str | None = None
    organization_role: OrganizationRole | None = None
    current_period_end: datetime | None = None
    grace_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    pending_plan: PlanName | None = None

    @property
    def is_personal(self) -> bool:
        return self.plan == "personal"

    @property
    def is_business(self) -> bool:
        return self.plan == "business"

    @property
    def is_enterprise(self) -> bool:
        return self.plan == "enterprise"

    @property
    def is_organization_plan(self) -> bool:
        return self.plan in VALID_ORGANIZATION_SUBSCRIPTION_PLANS


def normalize_user_id(user_id: str) -> str:
    normalized = (user_id or "").strip()
    if not normalized:
        raise ValueError("user_id is required.")
    return normalized


def normalize_organization_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("organization name is required.")

    normalized = ORGANIZATION_NAME_WHITESPACE_RE.sub(" ", name).strip()
    if len(normalized) < ORGANIZATION_NAME_MIN_LENGTH:
        raise ValueError(
            f"organization name must contain at least {ORGANIZATION_NAME_MIN_LENGTH} characters."
        )
    if len(normalized) > ORGANIZATION_NAME_MAX_LENGTH:
        raise ValueError(
            f"organization name cannot exceed {ORGANIZATION_NAME_MAX_LENGTH} characters."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError("organization name cannot contain control characters.")

    return normalized


def normalize_plan(plan: str) -> PlanName:
    normalized = (plan or "").strip().lower()
    if normalized not in VALID_PLANS:
        raise ValueError("plan must be one of: free, personal, business, enterprise.")
    return normalized  # type: ignore[return-value]


def normalize_user_subscription_plan(plan: str) -> UserSubscriptionPlanName:
    normalized = normalize_plan(plan)
    if normalized not in VALID_USER_SUBSCRIPTION_PLANS:
        raise ValueError("user subscriptions only support free or personal plans.")
    return normalized  # type: ignore[return-value]


def normalize_organization_subscription_plan(
    plan: str,
) -> OrganizationSubscriptionPlanName:
    normalized = normalize_plan(plan)
    if normalized not in VALID_ORGANIZATION_SUBSCRIPTION_PLANS:
        raise ValueError(
            "organization subscriptions only support business or enterprise plans."
        )
    return normalized  # type: ignore[return-value]


def normalize_status(status: str) -> SubscriptionStatus:
    normalized = (status or "").strip().lower()
    if normalized not in VALID_STATUSES:
        raise ValueError("status must be one of: active, inactive, cancelled, past_due.")
    return normalized  # type: ignore[return-value]


def normalize_organization_role(role: str) -> OrganizationRole:
    normalized = (role or "").strip().lower()
    if normalized not in VALID_ORGANIZATION_ROLES:
        raise ValueError("role must be one of: owner, admin, member.")
    return normalized  # type: ignore[return-value]


def normalize_organization_member_status(status: str) -> OrganizationMemberStatus:
    normalized = (status or "").strip().lower()
    if normalized not in VALID_ORGANIZATION_MEMBER_STATUSES:
        raise ValueError("member status must be one of: active, invited, removed.")
    return normalized  # type: ignore[return-value]


def validate_account_count(plan: str, account_count: int) -> int:
    normalized_plan = normalize_plan(plan)

    if not isinstance(account_count, int) or account_count < 1:
        raise ValueError("account_count must be an integer greater than or equal to 1.")

    min_accounts, max_accounts = PLAN_ACCOUNT_LIMITS[normalized_plan]
    if account_count < min_accounts:
        raise ValueError(
            f"{normalized_plan} requires at least {min_accounts} account(s)/user(s)."
        )
    if max_accounts is not None and account_count > max_accounts:
        raise ValueError(
            f"{normalized_plan} supports at most {max_accounts} account(s)/user(s)."
        )

    return account_count


def resolve_organization_max_accounts(
    plan: str,
    max_accounts: int | None = None,
) -> int:
    normalized_plan = normalize_organization_subscription_plan(plan)

    if normalized_plan == "business":
        # Product rule: Business includes the full Business allowance by default.
        return DEFAULT_ORGANIZATION_PLAN_ACCOUNTS["business"]

    if max_accounts is None:
        max_accounts = DEFAULT_ORGANIZATION_PLAN_ACCOUNTS["enterprise"]

    return validate_account_count(normalized_plan, max_accounts)


def _free_entitlement(
    user_id: str,
    *,
    status: SubscriptionStatus = "active",
) -> UserEntitlement:
    return UserEntitlement(
        user_id=normalize_user_id(user_id),
        plan="free",
        account_count=1,
        status=status,
        is_paid=False,
    )


def _user_subscription_row_to_entitlement(row) -> UserEntitlement:
    user_id = row[0]
    stored_plan = normalize_plan(row[1])
    stored_account_count = int(row[2])
    stored_status = normalize_status(row[3])
    current_period_end = row[4]
    grace_period_end = row[5]
    access_revoked_at = row[6]
    cancel_at_period_end = bool(row[7])
    pending_plan = normalize_plan(row[8]) if row[8] else None
    now = datetime.now(tz=timezone.utc)

    has_paid_access = access_revoked_at is None and (
        (
            stored_status == "active"
            and (current_period_end is None or current_period_end > now)
        )
        or (
            stored_status == "cancelled"
            and current_period_end is not None
            and current_period_end > now
        )
        or (
            stored_status == "past_due"
            and grace_period_end is not None
            and grace_period_end > now
        )
    )

    if not has_paid_access or stored_plan == "free":
        return _free_entitlement(user_id, status=stored_status)

    if stored_plan != "personal":
        # Business and Enterprise are resolved from organization_subscriptions.
        # Treat legacy user-level team plans as free instead of granting access
        # without verified team membership.
        return _free_entitlement(user_id, status=stored_status)

    validate_account_count(stored_plan, stored_account_count)
    return UserEntitlement(
        user_id=user_id,
        plan="personal",
        account_count=stored_account_count,
        status=stored_status,
        is_paid=True,
        source="user",
        current_period_end=current_period_end,
        grace_period_end=grace_period_end,
        cancel_at_period_end=cancel_at_period_end,
        pending_plan=pending_plan,
    )


def _organization_subscription_row_to_entitlement(row) -> UserEntitlement:
    user_id = row[0]
    organization_id = int(row[1])
    organization_name = row[2]
    organization_role = normalize_organization_role(row[3])
    plan = normalize_organization_subscription_plan(row[4])
    stored_max_accounts = int(row[5]) if row[5] is not None else None
    max_accounts = resolve_organization_max_accounts(plan, stored_max_accounts)
    subscription_status = normalize_status(row[6])
    current_period_end = row[7]
    grace_period_end = row[8]
    access_revoked_at = row[9]
    cancel_at_period_end = bool(row[10])
    pending_plan = normalize_plan(row[11]) if row[11] else None
    now = datetime.now(tz=timezone.utc)

    has_paid_access = access_revoked_at is None and (
        (
            subscription_status == "active"
            and (current_period_end is None or current_period_end > now)
        )
        or (
            subscription_status == "cancelled"
            and current_period_end is not None
            and current_period_end > now
        )
        or (
            subscription_status == "past_due"
            and grace_period_end is not None
            and grace_period_end > now
        )
    )

    if not has_paid_access:
        return _free_entitlement(user_id, status=subscription_status)

    validate_account_count(plan, max_accounts)
    return UserEntitlement(
        user_id=user_id,
        plan=plan,
        account_count=max_accounts,
        status=subscription_status,
        is_paid=True,
        source="organization",
        organization_id=organization_id,
        organization_name=organization_name,
        organization_role=organization_role,
        current_period_end=current_period_end,
        grace_period_end=grace_period_end,
        cancel_at_period_end=cancel_at_period_end,
        pending_plan=pending_plan,
    )


def ensure_user_subscription(conn, user_id: str) -> UserEntitlement:
    """
    Ensure a user-level subscription row exists and return its entitlement.

    New authenticated users default to the free plan. Personal paid rows can be
    created by your admin flow now, or by a billing-provider webhook later.

    Business and Enterprise entitlements are intentionally not returned from
    this helper; they are resolved through organization membership by
    get_user_entitlement(...).
    """

    normalized_user_id = normalize_user_id(user_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_subscriptions (user_id, plan, account_count, status)
            VALUES (%s, 'free', 1, 'active')
            ON CONFLICT (user_id) DO NOTHING
            """,
            (normalized_user_id,),
        )
        cur.execute(
            """
            SELECT user_id, plan, account_count, status,
                   current_period_end, grace_period_end, access_revoked_at,
                   cancel_at_period_end, pending_plan
            FROM user_subscriptions
            WHERE user_id = %s
            """,
            (normalized_user_id,),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to load user subscription.")

    return _user_subscription_row_to_entitlement(row)


def get_organization_entitlement(conn, user_id: str) -> UserEntitlement | None:
    """
    Return the best active organization entitlement for a user, if one exists.

    A user receives Business/Enterprise access only when:
    - organization_members.user_id matches the authenticated user
    - organization_members.status is active
    - organization_subscriptions.status is active
    - organization_subscriptions.plan is business or enterprise
    """

    normalized_user_id = normalize_user_id(user_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                om.user_id,
                o.id AS organization_id,
                o.name AS organization_name,
                om.role AS organization_role,
                os.plan,
                os.max_accounts,
                os.status
                , os.current_period_end
                , os.grace_period_end
                , os.access_revoked_at
                , os.cancel_at_period_end
                , os.pending_plan
            FROM organization_members om
            JOIN organizations o
              ON o.id = om.organization_id
            JOIN organization_subscriptions os
              ON os.organization_id = om.organization_id
            WHERE om.user_id = %s
              AND om.status = 'active'
              AND os.access_revoked_at IS NULL
              AND (
                    (os.status = 'active' AND (os.current_period_end IS NULL OR os.current_period_end > NOW()))
                 OR (os.status = 'cancelled' AND os.current_period_end > NOW())
                 OR (os.status = 'past_due' AND os.grace_period_end > NOW())
              )
              AND os.plan IN ('business', 'enterprise')
            ORDER BY
                CASE os.plan
                    WHEN 'enterprise' THEN 2
                    WHEN 'business' THEN 1
                    ELSE 0
                END DESC,
                os.max_accounts DESC,
                os.updated_at DESC,
                os.id DESC
            LIMIT 1
            """,
            (normalized_user_id,),
        )
        row = cur.fetchone()

    if row is None:
        return None

    return _organization_subscription_row_to_entitlement(row)


def get_user_entitlement(user_id: str) -> UserEntitlement:
    """
    Return the normalized entitlement for the authenticated user.

    Resolution order:
    1. Active Business/Enterprise subscription through organization membership.
    2. Active personal subscription from user_subscriptions.
    3. Authenticated-free fallback.

    Organization entitlements take priority so a Free or Personal user who accepts
    a Business/Enterprise invitation immediately receives the team plan.
    """

    normalized_user_id = normalize_user_id(user_id)

    with get_db() as conn:
        user_entitlement = ensure_user_subscription(conn, normalized_user_id)

        organization_entitlement = get_organization_entitlement(
            conn,
            normalized_user_id,
        )
        if organization_entitlement is not None and organization_entitlement.is_paid:
            return organization_entitlement

        if user_entitlement.is_personal and user_entitlement.is_paid:
            return user_entitlement

        return _free_entitlement(normalized_user_id)


def upsert_user_subscription(
    conn,
    *,
    user_id: str,
    plan: UserSubscriptionPlanName,
    account_count: int,
    status: SubscriptionStatus = "active",
    provider: str | None = None,
    provider_customer_id: str | None = None,
    provider_subscription_id: str | None = None,
    reset_plan_change_state: bool = True,
) -> UserEntitlement:
    """
    Create or update a user's free/personal subscription row.

    Use this for Personal subscriptions only. Business and Enterprise belong in
    organization_subscriptions and should use upsert_organization_subscription.
    """

    normalized_user_id = normalize_user_id(user_id)
    normalized_plan = normalize_user_subscription_plan(plan)
    normalized_status = normalize_status(status)
    normalized_account_count = validate_account_count(
        normalized_plan,
        account_count,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_subscriptions (
                user_id,
                plan,
                account_count,
                status,
                provider,
                provider_customer_id,
                provider_subscription_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                plan = EXCLUDED.plan,
                account_count = EXCLUDED.account_count,
                status = EXCLUDED.status,
                provider = EXCLUDED.provider,
                provider_customer_id = EXCLUDED.provider_customer_id,
                provider_subscription_id = EXCLUDED.provider_subscription_id,
                cancel_at_period_end = CASE
                    WHEN %s
                      OR user_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN FALSE
                    ELSE user_subscriptions.cancel_at_period_end
                END,
                pending_plan = CASE
                    WHEN %s
                      OR user_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE user_subscriptions.pending_plan
                END,
                plan_change_effective_at = CASE
                    WHEN %s
                      OR user_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE user_subscriptions.plan_change_effective_at
                END,
                grace_period_end = NULL,
                payment_failure_count = 0,
                access_revoked_at = CASE
                    WHEN user_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE user_subscriptions.access_revoked_at
                END,
                access_revocation_reason = CASE
                    WHEN user_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE user_subscriptions.access_revocation_reason
                END,
                updated_at = NOW()
            RETURNING user_id, plan, account_count, status,
                      current_period_end, grace_period_end, access_revoked_at,
                      cancel_at_period_end, pending_plan
            """,
            (
                normalized_user_id,
                normalized_plan,
                normalized_account_count,
                normalized_status,
                provider,
                provider_customer_id,
                provider_subscription_id,
                reset_plan_change_state,
                reset_plan_change_state,
                reset_plan_change_state,
            ),
        )
        row = cur.fetchone()

    if row is None:
        raise RuntimeError("Failed to upsert user subscription.")

    return _user_subscription_row_to_entitlement(row)


def create_organization(
    conn,
    *,
    name: str,
    owner_user_id: str,
) -> int:
    """
    Create an organization and add the owner as an active organization member.

    Returns the new organization_id.
    """

    normalized_name = normalize_organization_name(name)

    normalized_owner_user_id = normalize_user_id(owner_user_id)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organizations (name, owner_user_id)
            VALUES (%s, %s)
            RETURNING id
            """,
            (normalized_name, normalized_owner_user_id),
        )
        row = cur.fetchone()

        if row is None:
            raise RuntimeError("Failed to create organization.")

        organization_id = int(row[0])

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
            (organization_id, normalized_owner_user_id),
        )

    return organization_id


def upsert_organization_member(
    conn,
    *,
    organization_id: int,
    user_id: str,
    role: OrganizationRole = "member",
    status: OrganizationMemberStatus = "active",
    invited_by_user_id: str | None = None,
) -> None:
    """
    Add or update a user's organization membership.

    The database migration enforces that active members cannot exceed the
    organization's active subscription max_accounts.
    """

    if not isinstance(organization_id, int) or organization_id < 1:
        raise ValueError("organization_id must be an integer greater than or equal to 1.")

    normalized_user_id = normalize_user_id(user_id)
    normalized_role = normalize_organization_role(role)
    normalized_status = normalize_organization_member_status(status)
    normalized_invited_by_user_id = (
        normalize_user_id(invited_by_user_id)
        if invited_by_user_id is not None
        else None
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organization_members (
                organization_id,
                user_id,
                role,
                status,
                invited_by_user_id,
                invited_at,
                joined_at
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                CASE WHEN %s = 'invited' THEN NOW() ELSE NULL END,
                CASE WHEN %s = 'active' THEN NOW() ELSE NULL END
            )
            ON CONFLICT (organization_id, user_id) DO UPDATE SET
                role = EXCLUDED.role,
                status = EXCLUDED.status,
                invited_by_user_id = EXCLUDED.invited_by_user_id,
                invited_at = COALESCE(EXCLUDED.invited_at, organization_members.invited_at),
                joined_at = COALESCE(organization_members.joined_at, EXCLUDED.joined_at),
                updated_at = NOW()
            """,
            (
                organization_id,
                normalized_user_id,
                normalized_role,
                normalized_status,
                normalized_invited_by_user_id,
                normalized_status,
                normalized_status,
            ),
        )


def upsert_organization_subscription(
    conn,
    *,
    organization_id: int,
    plan: OrganizationSubscriptionPlanName,
    max_accounts: int | None = None,
    status: SubscriptionStatus = "active",
    provider: str | None = None,
    provider_customer_id: str | None = None,
    provider_subscription_id: str | None = None,
    reset_plan_change_state: bool = True,
) -> None:
    """
    Create or update a Business/Enterprise organization subscription.
    """

    if not isinstance(organization_id, int) or organization_id < 1:
        raise ValueError("organization_id must be an integer greater than or equal to 1.")

    normalized_plan = normalize_organization_subscription_plan(plan)
    normalized_status = normalize_status(status)
    normalized_max_accounts = resolve_organization_max_accounts(
        normalized_plan,
        max_accounts,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO organization_subscriptions (
                organization_id,
                plan,
                max_accounts,
                status,
                provider,
                provider_customer_id,
                provider_subscription_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id) DO UPDATE SET
                plan = EXCLUDED.plan,
                max_accounts = EXCLUDED.max_accounts,
                status = EXCLUDED.status,
                provider = EXCLUDED.provider,
                provider_customer_id = EXCLUDED.provider_customer_id,
                provider_subscription_id = EXCLUDED.provider_subscription_id,
                cancel_at_period_end = CASE
                    WHEN %s
                      OR organization_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN FALSE
                    ELSE organization_subscriptions.cancel_at_period_end
                END,
                pending_plan = CASE
                    WHEN %s
                      OR organization_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE organization_subscriptions.pending_plan
                END,
                plan_change_effective_at = CASE
                    WHEN %s
                      OR organization_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE organization_subscriptions.plan_change_effective_at
                END,
                grace_period_end = NULL,
                payment_failure_count = 0,
                access_revoked_at = CASE
                    WHEN organization_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE organization_subscriptions.access_revoked_at
                END,
                access_revocation_reason = CASE
                    WHEN organization_subscriptions.provider_subscription_id
                         IS DISTINCT FROM EXCLUDED.provider_subscription_id
                    THEN NULL
                    ELSE organization_subscriptions.access_revocation_reason
                END,
                updated_at = NOW()
            """,
            (
                organization_id,
                normalized_plan,
                normalized_max_accounts,
                normalized_status,
                provider,
                provider_customer_id,
                provider_subscription_id,
                reset_plan_change_state,
                reset_plan_change_state,
                reset_plan_change_state,
            ),
        )


__all__ = [
    "ACTIVE_MEMBER_STATUS",
    "ACTIVE_STATUS",
    "EntitlementSource",
    "OrganizationMemberStatus",
    "OrganizationRole",
    "OrganizationSubscriptionPlanName",
    "PLAN_ACCOUNT_LIMITS",
    "PlanName",
    "SubscriptionStatus",
    "UserEntitlement",
    "UserSubscriptionPlanName",
    "create_organization",
    "ensure_user_subscription",
    "get_organization_entitlement",
    "get_user_entitlement",
    "normalize_organization_member_status",
    "normalize_organization_name",
    "normalize_organization_role",
    "normalize_organization_subscription_plan",
    "normalize_plan",
    "normalize_status",
    "normalize_user_id",
    "normalize_user_subscription_plan",
    "resolve_organization_max_accounts",
    "upsert_organization_member",
    "upsert_organization_subscription",
    "upsert_user_subscription",
    "validate_account_count",
]
