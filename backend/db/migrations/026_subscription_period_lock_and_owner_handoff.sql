-- 026_subscription_period_lock_and_owner_handoff.sql
-- Durable Business/Enterprise payer handoff state.
--
-- The active organization subscription continues to represent the already-paid
-- period.  A transfer stops the old provider subscription from renewing and
-- records the new owner's authorization separately.  The future provider
-- subscription is not allowed to replace entitlement state until the old paid
-- period has ended and a verified provider event activates the new period.

BEGIN;

CREATE TABLE IF NOT EXISTS organization_billing_handoffs (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    previous_owner_user_id TEXT NOT NULL,
    new_owner_user_id TEXT NOT NULL,
    new_owner_email TEXT NOT NULL,
    plan TEXT NOT NULL,
    max_accounts INTEGER NOT NULL,
    provider TEXT NOT NULL,
    old_provider_customer_id TEXT,
    old_provider_subscription_id TEXT NOT NULL,
    effective_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'authorization_required',
    authorization_reference TEXT,
    new_provider_customer_id TEXT,
    new_provider_subscription_id TEXT,
    authorized_at TIMESTAMPTZ,
    activated_at TIMESTAMPTZ,
    last_error TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_billing_handoffs_owner_change_check
        CHECK (previous_owner_user_id <> new_owner_user_id),
    CONSTRAINT organization_billing_handoffs_plan_check
        CHECK (plan IN ('business', 'enterprise')),
    CONSTRAINT organization_billing_handoffs_provider_check
        CHECK (provider IN ('stripe', 'paystack')),
    CONSTRAINT organization_billing_handoffs_max_accounts_check
        CHECK (max_accounts >= 1),
    CONSTRAINT organization_billing_handoffs_status_check
        CHECK (status IN (
            'authorization_required',
            'authorization_pending',
            'scheduled',
            'activated',
            'expired',
            'failed'
        )),
    CONSTRAINT organization_billing_handoffs_previous_owner_not_blank
        CHECK (LENGTH(BTRIM(previous_owner_user_id)) > 0),
    CONSTRAINT organization_billing_handoffs_new_owner_not_blank
        CHECK (LENGTH(BTRIM(new_owner_user_id)) > 0),
    CONSTRAINT organization_billing_handoffs_new_owner_email_not_blank
        CHECK (LENGTH(BTRIM(new_owner_email)) > 0),
    CONSTRAINT organization_billing_handoffs_old_subscription_not_blank
        CHECK (LENGTH(BTRIM(old_provider_subscription_id)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_organization_billing_handoffs_open_org
    ON organization_billing_handoffs (organization_id)
    WHERE status IN ('authorization_required', 'authorization_pending', 'scheduled');

CREATE UNIQUE INDEX IF NOT EXISTS ux_organization_billing_handoffs_new_subscription
    ON organization_billing_handoffs (provider, new_provider_subscription_id)
    WHERE new_provider_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_organization_billing_handoffs_effective
    ON organization_billing_handoffs (effective_at, status);

CREATE INDEX IF NOT EXISTS idx_organization_billing_handoffs_authorization_reference
    ON organization_billing_handoffs (provider, authorization_reference)
    WHERE authorization_reference IS NOT NULL;

COMMIT;
