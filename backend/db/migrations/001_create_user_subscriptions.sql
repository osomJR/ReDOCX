-- User subscription source of truth for ReDOCX.
--
-- This table is intentionally internal. A billing provider such as Stripe,
-- Paddle, or Lemon Squeezy can be added later and synchronized into these
-- rows through webhooks.

CREATE TABLE IF NOT EXISTS user_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,

    -- free users keep authenticated-free limits.
    -- paid plans bypass usage limits but still validate account/user count.
    plan TEXT NOT NULL DEFAULT 'free',
    account_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',

    -- Optional billing-provider fields for a future external checkout/webhook integration.
    provider TEXT,
    provider_customer_id TEXT,
    provider_subscription_id TEXT,
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT user_subscriptions_plan_check
        CHECK (plan IN ('free', 'personal', 'business', 'enterprise')),
    CONSTRAINT user_subscriptions_status_check
        CHECK (status IN ('active', 'inactive', 'cancelled', 'past_due')),
    CONSTRAINT user_subscriptions_account_count_check
        CHECK (account_count >= 1),
    CONSTRAINT user_subscriptions_personal_account_count_check
        CHECK (plan <> 'personal' OR account_count = 1),
    CONSTRAINT user_subscriptions_business_account_count_check
        CHECK (plan <> 'business' OR account_count BETWEEN 2 AND 19),
    CONSTRAINT user_subscriptions_enterprise_account_count_check
        CHECK (plan <> 'enterprise' OR account_count >= 20)
);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id
    ON user_subscriptions (user_id);

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_provider_subscription_id
    ON user_subscriptions (provider_subscription_id)
    WHERE provider_subscription_id IS NOT NULL;

CREATE OR REPLACE FUNCTION set_user_subscriptions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_user_subscriptions_updated_at ON user_subscriptions;

CREATE TRIGGER trg_user_subscriptions_updated_at
BEFORE UPDATE ON user_subscriptions
FOR EACH ROW
EXECUTE FUNCTION set_user_subscriptions_updated_at();
