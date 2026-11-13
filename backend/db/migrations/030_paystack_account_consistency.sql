BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY,
    appearance TEXT NOT NULL DEFAULT 'system' CHECK (appearance IN ('light', 'dark', 'system')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE billing_checkout_sessions
    ADD COLUMN IF NOT EXISTS paystack_submitted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS paystack_plan_code TEXT,
    ADD COLUMN IF NOT EXISTS paystack_amount_kobo BIGINT,
    ADD COLUMN IF NOT EXISTS paystack_timeout_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS paystack_settled_at TIMESTAMPTZ;

ALTER TABLE billing_collection_attempts
    ADD COLUMN IF NOT EXISTS paystack_timeout_seconds INTEGER,
    ADD COLUMN IF NOT EXISTS paystack_resolved_at TIMESTAMPTZ;

-- Older Paystack requests could reach initialization before the reference was
-- written. Recover the original deterministic reference for unresolved rows.
-- Keep the historical timeout unknown until the provider confirms closure.
UPDATE billing_checkout_sessions
SET provider_reference = COALESCE(
        provider_reference,
        'redocx-' || target_plan || '-' || LEFT(
            ENCODE(DIGEST(CONVERT_TO(idempotency_key, 'UTF8'), 'sha256'), 'hex'), 20
        )
    ),
    paystack_submitted_at = COALESCE(paystack_submitted_at, created_at)
WHERE provider = 'paystack' AND operation = 'checkout'
  AND status IN ('started', 'created', 'failed', 'completed')
  AND (idempotency_key IS NOT NULL OR provider_reference IS NOT NULL)
  AND paystack_submitted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_paystack_unresolved_checkout
    ON billing_checkout_sessions (user_id, id)
    WHERE provider = 'paystack' AND operation = 'checkout'
      AND status IN ('started', 'created', 'failed');

CREATE TABLE IF NOT EXISTS billing_purged_paystack_keys (
    kind TEXT NOT NULL CHECK (kind IN ('reference', 'subscription', 'customer')),
    digest TEXT NOT NULL,
    PRIMARY KEY (kind, digest)
);

COMMIT;
