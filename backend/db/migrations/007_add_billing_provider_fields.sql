-- ReDOCX billing provider fields and webhook idempotency ledger.
-- Safe to run multiple times on PostgreSQL/Neon.

BEGIN;

-- ---------------------------------------------------------------------------
-- User-level subscriptions: Free/Personal
-- ---------------------------------------------------------------------------

ALTER TABLE user_subscriptions
  ADD COLUMN IF NOT EXISTS provider TEXT,
  ADD COLUMN IF NOT EXISTS provider_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS provider_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_provider_customer
  ON user_subscriptions (provider, provider_customer_id)
  WHERE provider IS NOT NULL AND provider_customer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_user_subscriptions_provider_subscription
  ON user_subscriptions (provider, provider_subscription_id)
  WHERE provider IS NOT NULL AND provider_subscription_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Organization-level subscriptions: Business/Enterprise
-- ---------------------------------------------------------------------------

ALTER TABLE organization_subscriptions
  ADD COLUMN IF NOT EXISTS provider TEXT,
  ADD COLUMN IF NOT EXISTS provider_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS provider_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS current_period_start TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_provider_customer
  ON organization_subscriptions (provider, provider_customer_id)
  WHERE provider IS NOT NULL AND provider_customer_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_organization_subscriptions_provider_subscription
  ON organization_subscriptions (provider, provider_subscription_id)
  WHERE provider IS NOT NULL AND provider_subscription_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Checkout session ledger.
-- This lets you persist outbound checkout sessions from any provider.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS billing_checkout_sessions (
  id BIGSERIAL PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_session_id TEXT,
  provider_reference TEXT,
  user_id TEXT NOT NULL,
  email TEXT,
  target_plan TEXT NOT NULL CHECK (target_plan IN ('personal', 'business', 'enterprise')),
  current_plan TEXT CHECK (current_plan IN ('free', 'personal', 'business', 'enterprise')),
  organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
  organization_name TEXT,
  checkout_url TEXT,
  status TEXT NOT NULL DEFAULT 'created' CHECK (
    status IN ('created', 'started', 'completed', 'expired', 'cancelled', 'failed')
  ),
  provider_customer_id TEXT,
  provider_subscription_id TEXT,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  raw_response JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_checkout_sessions_provider_session
  ON billing_checkout_sessions (provider, provider_session_id)
  WHERE provider_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_checkout_sessions_user_id
  ON billing_checkout_sessions (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_billing_checkout_sessions_provider_reference
  ON billing_checkout_sessions (provider, provider_reference)
  WHERE provider_reference IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Verified provider event ledger.
-- This is the critical idempotency table. Providers retry webhook delivery, so
-- entitlement writes must be protected by a unique provider_event_id.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS billing_provider_events (
  id BIGSERIAL PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('activate', 'cancel', 'past_due', 'ignore')),
  event_status TEXT,
  user_id TEXT,
  target_plan TEXT CHECK (target_plan IN ('free', 'personal', 'business', 'enterprise')),
  organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
  provider_customer_id TEXT,
  provider_subscription_id TEXT,
  provider_reference TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  processing_status TEXT NOT NULL DEFAULT 'received' CHECK (
    processing_status IN ('received', 'processing', 'processed', 'ignored', 'failed')
  ),
  processing_message TEXT,
  processed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (provider, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_billing_provider_events_provider_created
  ON billing_provider_events (provider, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_billing_provider_events_user_id
  ON billing_provider_events (user_id, created_at DESC)
  WHERE user_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_provider_events_subscription
  ON billing_provider_events (provider, provider_subscription_id)
  WHERE provider_subscription_id IS NOT NULL;

COMMIT;
