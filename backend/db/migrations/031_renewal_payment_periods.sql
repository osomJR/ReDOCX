BEGIN;

-- Existing collection accounts keep their active-seat pricing.
ALTER TABLE billing_collection_accounts
    ADD COLUMN IF NOT EXISTS renewal_basis TEXT NOT NULL DEFAULT 'active_members',
    ALTER COLUMN organization_id DROP NOT NULL,
    DROP CONSTRAINT IF EXISTS billing_collection_accounts_plan_check;

ALTER TABLE billing_collection_accounts
    ADD CONSTRAINT billing_collection_accounts_plan_check CHECK (
        (plan = 'personal' AND organization_id IS NULL AND renewal_basis = 'purchased_capacity')
        OR (plan IN ('business', 'enterprise') AND organization_id IS NOT NULL
            AND renewal_basis IN ('active_members', 'purchased_capacity'))
    );

-- The automatic retry limit still applies in the worker. A customer can start
-- another verified checkout after a decline, including after grace expires.
ALTER TABLE billing_collection_jobs
    DROP CONSTRAINT IF EXISTS billing_collection_jobs_attempts_check,
    ADD CONSTRAINT billing_collection_jobs_attempts_check CHECK (attempts >= 0);

ALTER TABLE billing_collection_attempts
    DROP CONSTRAINT IF EXISTS billing_collection_attempts_ordinal_check,
    ADD CONSTRAINT billing_collection_attempts_ordinal_check CHECK (ordinal >= 1),
    ADD COLUMN IF NOT EXISTS provider_paid_at TIMESTAMPTZ;

COMMIT;
