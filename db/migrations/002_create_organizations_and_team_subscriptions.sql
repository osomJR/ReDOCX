-- Migration: create organization/team subscription model
-- Purpose:
-- - Keep personal subscriptions on user_subscriptions.
-- - Add organization-level subscriptions for business and enterprise plans.
-- - Track which Auth0 users belong to each paid team.
-- - Enforce business/enterprise seat ranges and prevent active members from exceeding max_accounts.

CREATE TABLE IF NOT EXISTS organizations (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organizations_name_not_blank_check
        CHECK (LENGTH(BTRIM(name)) > 0),
    CONSTRAINT organizations_owner_user_id_not_blank_check
        CHECK (LENGTH(BTRIM(owner_user_id)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_organizations_owner_user_id
    ON organizations (owner_user_id);


CREATE TABLE IF NOT EXISTS organization_members (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id)
        ON DELETE CASCADE,
    user_id TEXT NOT NULL,

    -- Safe profile snapshot fields for display in team UIs.
    -- user_id remains the internal auth identifier; do not use it as display text.
    member_name TEXT,
    member_email TEXT,
    member_picture TEXT,

    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',

    invited_by_user_id TEXT,
    invited_at TIMESTAMPTZ,
    joined_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_members_user_id_not_blank_check
        CHECK (LENGTH(BTRIM(user_id)) > 0),
    CONSTRAINT organization_members_member_name_not_blank_check
        CHECK (member_name IS NULL OR LENGTH(BTRIM(member_name)) > 0),
    CONSTRAINT organization_members_member_email_not_blank_check
        CHECK (member_email IS NULL OR LENGTH(BTRIM(member_email)) > 0),
    CONSTRAINT organization_members_member_picture_not_blank_check
        CHECK (member_picture IS NULL OR LENGTH(BTRIM(member_picture)) > 0),
    CONSTRAINT organization_members_role_check
        CHECK (role IN ('owner', 'admin', 'member')),
    CONSTRAINT organization_members_status_check
        CHECK (status IN ('active', 'invited', 'removed')),
    CONSTRAINT organization_members_invited_by_user_id_not_blank_check
        CHECK (invited_by_user_id IS NULL OR LENGTH(BTRIM(invited_by_user_id)) > 0),

    CONSTRAINT organization_members_unique_user_per_org
        UNIQUE (organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_organization_members_user_id
    ON organization_members (user_id);

CREATE INDEX IF NOT EXISTS idx_organization_members_member_email
    ON organization_members (LOWER(member_email))
    WHERE member_email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_organization_members_organization_id_status
    ON organization_members (organization_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_organization_members_one_owner_per_org
    ON organization_members (organization_id)
    WHERE role = 'owner' AND status = 'active';


CREATE TABLE IF NOT EXISTS organization_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL UNIQUE
        REFERENCES organizations(id)
        ON DELETE CASCADE,

    -- Organization subscriptions are only for multi-account plans.
    -- Personal subscriptions remain in user_subscriptions.
    plan TEXT NOT NULL,
    max_accounts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',

    -- Optional billing-provider fields for future checkout/webhook integration.
    provider TEXT,
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_subscriptions_plan_check
        CHECK (plan IN ('business', 'enterprise')),
    CONSTRAINT organization_subscriptions_status_check
        CHECK (status IN ('active', 'inactive', 'cancelled', 'past_due')),
    CONSTRAINT organization_subscriptions_max_accounts_check
        CHECK (max_accounts >= 2),
    CONSTRAINT organization_subscriptions_business_max_accounts_check
        CHECK (plan <> 'business' OR max_accounts BETWEEN 2 AND 19),
    CONSTRAINT organization_subscriptions_enterprise_max_accounts_check
        CHECK (plan <> 'enterprise' OR max_accounts >= 20)
);

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_organization_id
    ON organization_subscriptions (organization_id);

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_provider_subscription_id
    ON organization_subscriptions (provider_subscription_id)
    WHERE provider_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_status_plan
    ON organization_subscriptions (status, plan);


CREATE OR REPLACE FUNCTION set_organizations_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organizations_updated_at ON organizations;

CREATE TRIGGER trg_organizations_updated_at
BEFORE UPDATE ON organizations
FOR EACH ROW
EXECUTE FUNCTION set_organizations_updated_at();


CREATE OR REPLACE FUNCTION set_organization_members_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();

    IF NEW.status = 'active' AND NEW.joined_at IS NULL THEN
        NEW.joined_at = NOW();
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organization_members_updated_at ON organization_members;

CREATE TRIGGER trg_organization_members_updated_at
BEFORE UPDATE ON organization_members
FOR EACH ROW
EXECUTE FUNCTION set_organization_members_updated_at();


CREATE OR REPLACE FUNCTION set_organization_subscriptions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organization_subscriptions_updated_at ON organization_subscriptions;

CREATE TRIGGER trg_organization_subscriptions_updated_at
BEFORE UPDATE ON organization_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_organization_subscriptions_updated_at();


CREATE OR REPLACE FUNCTION enforce_organization_subscription_seat_limit()
RETURNS TRIGGER AS $$
DECLARE
    target_organization_id BIGINT;
    active_member_count INTEGER;
    subscribed_max_accounts INTEGER;
    subscription_status TEXT;
BEGIN
    target_organization_id := COALESCE(NEW.organization_id, OLD.organization_id);

    SELECT os.max_accounts, os.status
    INTO subscribed_max_accounts, subscription_status
    FROM organization_subscriptions os
    WHERE os.organization_id = target_organization_id;

    -- Let organizations exist before a subscription is attached.
    -- Once an active subscription exists, active members cannot exceed max_accounts.
    IF subscribed_max_accounts IS NULL OR subscription_status <> 'active' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    SELECT COUNT(*)
    INTO active_member_count
    FROM organization_members om
    WHERE om.organization_id = target_organization_id
      AND om.status = 'active';

    IF active_member_count > subscribed_max_accounts THEN
        RAISE EXCEPTION
            'Active organization members (%) exceed subscribed max_accounts (%) for organization_id %',
            active_member_count,
            subscribed_max_accounts,
            target_organization_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organization_members_enforce_seat_limit
    ON organization_members;

CREATE CONSTRAINT TRIGGER trg_organization_members_enforce_seat_limit
AFTER INSERT OR UPDATE OF organization_id, status OR DELETE
ON organization_members
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION enforce_organization_subscription_seat_limit();


CREATE OR REPLACE FUNCTION enforce_organization_subscription_update_seat_limit()
RETURNS TRIGGER AS $$
DECLARE
    active_member_count INTEGER;
BEGIN
    IF NEW.status <> 'active' THEN
        RETURN NEW;
    END IF;

    SELECT COUNT(*)
    INTO active_member_count
    FROM organization_members om
    WHERE om.organization_id = NEW.organization_id
      AND om.status = 'active';

    IF active_member_count > NEW.max_accounts THEN
        RAISE EXCEPTION
            'Active organization members (%) exceed subscribed max_accounts (%) for organization_id %',
            active_member_count,
            NEW.max_accounts,
            NEW.organization_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organization_subscriptions_enforce_seat_limit
    ON organization_subscriptions;

CREATE CONSTRAINT TRIGGER trg_organization_subscriptions_enforce_seat_limit
AFTER INSERT OR UPDATE OF organization_id, status, max_accounts
ON organization_subscriptions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION enforce_organization_subscription_update_seat_limit();
