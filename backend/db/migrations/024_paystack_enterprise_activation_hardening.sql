-- 024_paystack_enterprise_activation_hardening.sql
--
-- Forward-only production hardening for verified Paystack activation.
-- This migration intentionally does not edit historical migrations.
--
-- Critical contract aligned by this migration:
--   * Business supports 1..19 purchased seats.
--   * Enterprise supports 1+ purchased seats.
--   * Provider event processing has the columns/actions used by the current backend.
--   * Checkout mutations have the idempotency fields used by the current backend.
--
-- Apply before deploying the matching backend code.

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('redocx:migration:024_paystack_enterprise_activation_hardening'));

DO $$
DECLARE
    required_table TEXT;
BEGIN
    FOREACH required_table IN ARRAY ARRAY[
        'user_subscriptions',
        'organizations',
        'organization_members',
        'organization_subscriptions',
        'billing_checkout_sessions',
        'billing_provider_events'
    ]
    LOOP
        IF to_regclass(current_schema() || '.' || required_table) IS NULL THEN
            RAISE EXCEPTION
                'Migration 024 requires base billing table %, apply migrations 001, 002 and 007 first',
                required_table;
        END IF;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- Exact purchased-seat contract. This is the production-critical correction.
-- ---------------------------------------------------------------------------
ALTER TABLE organization_subscriptions
    DROP CONSTRAINT IF EXISTS organization_subscriptions_max_accounts_check,
    DROP CONSTRAINT IF EXISTS organization_subscriptions_business_max_accounts_check,
    DROP CONSTRAINT IF EXISTS organization_subscriptions_enterprise_max_accounts_check;

ALTER TABLE organization_subscriptions
    ADD CONSTRAINT organization_subscriptions_max_accounts_check
        CHECK (max_accounts >= 1),
    ADD CONSTRAINT organization_subscriptions_business_max_accounts_check
        CHECK (plan <> 'business' OR max_accounts BETWEEN 1 AND 19),
    ADD CONSTRAINT organization_subscriptions_enterprise_max_accounts_check
        CHECK (plan <> 'enterprise' OR max_accounts >= 1);

-- ---------------------------------------------------------------------------
-- Fields used by current entitlement mutation/reconciliation code.
-- These are IF NOT EXISTS so 024 is safe after 019 and repairs partial deploys.
-- ---------------------------------------------------------------------------
ALTER TABLE user_subscriptions
    ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pending_plan TEXT,
    ADD COLUMN IF NOT EXISTS plan_change_effective_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS grace_period_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payment_failure_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS access_revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS access_revocation_reason TEXT,
    ADD COLUMN IF NOT EXISTS last_provider_event_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reconciliation_error TEXT;

ALTER TABLE organization_subscriptions
    ADD COLUMN IF NOT EXISTS cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pending_plan TEXT,
    ADD COLUMN IF NOT EXISTS plan_change_effective_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS grace_period_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payment_failure_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS access_revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS access_revocation_reason TEXT,
    ADD COLUMN IF NOT EXISTS last_provider_event_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_reconciled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reconciliation_error TEXT;

ALTER TABLE billing_checkout_sessions
    ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT 'checkout',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS request_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS replaced_provider_subscription_id TEXT;

ALTER TABLE billing_provider_events
    ADD COLUMN IF NOT EXISTS provider_reference TEXT,
    ADD COLUMN IF NOT EXISTS processing_status TEXT NOT NULL DEFAULT 'received',
    ADD COLUMN IF NOT EXISTS processing_message TEXT,
    ADD COLUMN IF NOT EXISTS processed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Current backend supports dispute/refund lifecycle actions.
ALTER TABLE billing_provider_events
    DROP CONSTRAINT IF EXISTS billing_provider_events_action_check;
ALTER TABLE billing_provider_events
    ADD CONSTRAINT billing_provider_events_action_check
    CHECK (action IN (
        'activate', 'cancel', 'past_due', 'suspend', 'revoke', 'restore', 'ignore'
    ));

-- Cancellation operations use Free as a target plan.
ALTER TABLE billing_checkout_sessions
    DROP CONSTRAINT IF EXISTS billing_checkout_sessions_target_plan_check;
ALTER TABLE billing_checkout_sessions
    ADD CONSTRAINT billing_checkout_sessions_target_plan_check
    CHECK (target_plan IN ('free', 'personal', 'business', 'enterprise'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_checkout_sessions_user_provider_idempotency
    ON billing_checkout_sessions (user_id, provider, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_checkout_sessions_provider_reference
    ON billing_checkout_sessions (provider, provider_reference)
    WHERE provider_reference IS NOT NULL;

-- Ensure provider events have an idempotency key even on databases where the
-- original table was created by a partial/manual migration.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class t ON t.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'billing_provider_events'
          AND i.indisunique
          AND (
              SELECT array_agg(a.attname ORDER BY k.ordinality)
              FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
              JOIN pg_attribute a
                ON a.attrelid = t.oid
               AND a.attnum = k.attnum
          ) = ARRAY['provider', 'provider_event_id']::name[]
    ) THEN
        CREATE UNIQUE INDEX ux_billing_provider_events_provider_event_id_v024
            ON billing_provider_events (provider, provider_event_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_billing_provider_events_provider_created
    ON billing_provider_events (provider, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_billing_provider_events_subscription
    ON billing_provider_events (provider, provider_subscription_id)
    WHERE provider_subscription_id IS NOT NULL;

-- Final migration-level assertions. If these fail, COMMIT never happens.
DO $$
DECLARE
    enterprise_constraint TEXT;
    business_constraint TEXT;
BEGIN
    SELECT pg_get_constraintdef(c.oid)
    INTO enterprise_constraint
    FROM pg_constraint c
    WHERE c.conrelid = 'organization_subscriptions'::regclass
      AND c.conname = 'organization_subscriptions_enterprise_max_accounts_check';

    SELECT pg_get_constraintdef(c.oid)
    INTO business_constraint
    FROM pg_constraint c
    WHERE c.conrelid = 'organization_subscriptions'::regclass
      AND c.conname = 'organization_subscriptions_business_max_accounts_check';

    IF enterprise_constraint IS NULL
       OR POSITION('max_accounts >= 1' IN LOWER(enterprise_constraint)) = 0 THEN
        RAISE EXCEPTION 'Enterprise seat constraint is not compatible with 1+ seat checkout';
    END IF;

    IF business_constraint IS NULL OR NOT (
           POSITION('between 1 and 19' IN LOWER(business_constraint)) > 0
           OR (
               POSITION('max_accounts >= 1' IN LOWER(business_constraint)) > 0
               AND POSITION('max_accounts <= 19' IN LOWER(business_constraint)) > 0
           )
       ) THEN
        RAISE EXCEPTION 'Business seat constraint is not compatible with 1..19 seat checkout';
    END IF;
END
$$;

COMMIT;
