-- 019_billing_reliability_hardening.sql
-- ReDOCX billing operation idempotency, lifecycle enforcement, grace handling,
-- plan-change state, dispute/refund controls, reconciliation metadata, and
-- durable account-purge leases for scheduled deletion workers.
-- Safe to apply after 007_add_billing_provider_fields.sql and 009_create_account_lifecycle.sql.

BEGIN;

ALTER TABLE account_lifecycle
    ADD COLUMN IF NOT EXISTS purge_locked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS purge_locked_by TEXT,
    ADD COLUMN IF NOT EXISTS purge_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS purge_last_error TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'account_lifecycle_purge_attempts_check'
          AND conrelid = 'account_lifecycle'::regclass
    ) THEN
        ALTER TABLE account_lifecycle
            ADD CONSTRAINT account_lifecycle_purge_attempts_check
            CHECK (purge_attempts >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'account_lifecycle_purge_lock_pair_check'
          AND conrelid = 'account_lifecycle'::regclass
    ) THEN
        ALTER TABLE account_lifecycle
            ADD CONSTRAINT account_lifecycle_purge_lock_pair_check
            CHECK (
                (purge_locked_at IS NULL AND purge_locked_by IS NULL)
                OR
                (purge_locked_at IS NOT NULL AND purge_locked_by IS NOT NULL AND LENGTH(BTRIM(purge_locked_by)) > 0)
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_account_lifecycle_due_purge_claim
    ON account_lifecycle (purge_after ASC, purge_locked_at ASC NULLS FIRST)
    WHERE status IN ('deactivated_pending_deletion', 'purge_due');

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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_subscriptions_pending_plan_check'
          AND conrelid = 'user_subscriptions'::regclass
    ) THEN
        ALTER TABLE user_subscriptions
            ADD CONSTRAINT user_subscriptions_pending_plan_check
            CHECK (pending_plan IS NULL OR pending_plan IN ('free', 'personal', 'business', 'enterprise'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'user_subscriptions_payment_failure_count_check'
          AND conrelid = 'user_subscriptions'::regclass
    ) THEN
        ALTER TABLE user_subscriptions
            ADD CONSTRAINT user_subscriptions_payment_failure_count_check
            CHECK (payment_failure_count >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'organization_subscriptions_pending_plan_check'
          AND conrelid = 'organization_subscriptions'::regclass
    ) THEN
        ALTER TABLE organization_subscriptions
            ADD CONSTRAINT organization_subscriptions_pending_plan_check
            CHECK (pending_plan IS NULL OR pending_plan IN ('free', 'personal', 'business', 'enterprise'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'organization_subscriptions_payment_failure_count_check'
          AND conrelid = 'organization_subscriptions'::regclass
    ) THEN
        ALTER TABLE organization_subscriptions
            ADD CONSTRAINT organization_subscriptions_payment_failure_count_check
            CHECK (payment_failure_count >= 0);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_access_expiration
    ON user_subscriptions (status, current_period_end, grace_period_end)
    WHERE status IN ('active', 'cancelled', 'past_due');

CREATE INDEX IF NOT EXISTS idx_user_subscriptions_reconciliation
    ON user_subscriptions (last_reconciled_at, provider)
    WHERE provider IS NOT NULL AND provider_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_access_expiration
    ON organization_subscriptions (status, current_period_end, grace_period_end)
    WHERE status IN ('active', 'cancelled', 'past_due');

CREATE INDEX IF NOT EXISTS idx_organization_subscriptions_reconciliation
    ON organization_subscriptions (last_reconciled_at, provider)
    WHERE provider IS NOT NULL AND provider_subscription_id IS NOT NULL;

ALTER TABLE billing_checkout_sessions
    ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT 'checkout',
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS request_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS replaced_provider_subscription_id TEXT;

-- A cancellation action targets the Free plan, so extend the original check.
ALTER TABLE billing_checkout_sessions
    DROP CONSTRAINT IF EXISTS billing_checkout_sessions_target_plan_check;
ALTER TABLE billing_checkout_sessions
    ADD CONSTRAINT billing_checkout_sessions_target_plan_check
    CHECK (target_plan IN ('free', 'personal', 'business', 'enterprise'));

CREATE UNIQUE INDEX IF NOT EXISTS ux_billing_checkout_sessions_user_provider_idempotency
    ON billing_checkout_sessions (user_id, provider, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_checkout_sessions_operation_status
    ON billing_checkout_sessions (operation, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_billing_checkout_sessions_request_fingerprint
    ON billing_checkout_sessions (
        user_id,
        provider,
        request_fingerprint,
        created_at DESC
    )
    WHERE request_fingerprint IS NOT NULL
      AND status IN ('started', 'created', 'completed');

-- Refund/dispute/chargeback events need explicit entitlement actions.
ALTER TABLE billing_provider_events
    DROP CONSTRAINT IF EXISTS billing_provider_events_action_check;
ALTER TABLE billing_provider_events
    ADD CONSTRAINT billing_provider_events_action_check
    CHECK (action IN (
        'activate', 'cancel', 'past_due', 'suspend', 'revoke', 'restore', 'ignore'
    ));

COMMIT;
