-- Additive migration. Apply after 027. Does not rewrite historical purchases.
BEGIN;

CREATE TABLE IF NOT EXISTS billing_collection_accounts (
    id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('paystack', 'stripe')),
    provider_subscription_id TEXT NOT NULL,
    provider_customer_id TEXT NOT NULL,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    payer_user_id TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (plan IN ('business', 'enterprise')),
    state TEXT NOT NULL DEFAULT 'preparing' CHECK (state IN ('preparing', 'ready', 'blocked')),
    renewal_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL CHECK (period_end > period_start),
    billing_day INTEGER NOT NULL CHECK (billing_day BETWEEN 1 AND 31),
    native_stopped_at TIMESTAMPTZ,
    authorization_payment_reference TEXT,
    handoff_id BIGINT REFERENCES organization_billing_handoffs(id) ON DELETE SET NULL,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_subscription_id)
);

CREATE TABLE IF NOT EXISTS billing_collection_jobs (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT REFERENCES billing_collection_accounts(id) ON DELETE CASCADE,
    organization_id BIGINT REFERENCES organizations(id) ON DELETE CASCADE,
    payer_user_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('paystack', 'stripe')),
    provider_subscription_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('renewal', 'seats', 'recovery')),
    plan TEXT NOT NULL CHECK (plan IN ('personal', 'business', 'enterprise')),
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL CHECK (period_end > period_start),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    previous_quantity INTEGER,
    amount BIGINT NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'NGN' CHECK (currency = 'NGN'),
    invoice_code TEXT,
    initial_authorization_fingerprint TEXT,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
        'open', 'pending', 'paid', 'failed', 'requires_action', 'cancelled', 'review')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    due_at TIMESTAMPTZ NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL,
    deadline TIMESTAMPTZ NOT NULL,
    paid_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (payer_user_id, idempotency_key),
    CHECK ((plan <> 'business' OR quantity <= 19) AND (plan <> 'personal' OR quantity = 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_collection_renewal_period
    ON billing_collection_jobs(provider, provider_subscription_id, purpose, period_start)
    WHERE purpose IN ('renewal', 'recovery');
CREATE UNIQUE INDEX IF NOT EXISTS ux_collection_open_seats
    ON billing_collection_jobs(organization_id)
    WHERE purpose = 'seats' AND status IN ('open', 'pending', 'review');
CREATE INDEX IF NOT EXISTS ix_collection_due ON billing_collection_jobs(next_attempt_at)
    WHERE status IN ('open', 'pending');

CREATE TABLE IF NOT EXISTS billing_collection_attempts (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL REFERENCES billing_collection_jobs(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 5),
    reference TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('automatic', 'checkout')),
    provider_id TEXT,
    checkout_url TEXT,
    status TEXT NOT NULL DEFAULT 'prepared' CHECK (status IN (
        'prepared', 'pending', 'failed', 'success', 'requires_action', 'review')),
    response_code TEXT,
    authorization_fingerprint TEXT,
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, ordinal)
);

CREATE OR REPLACE FUNCTION billing_collection_admission_blocked(target_id BIGINT)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM billing_collection_jobs j
        JOIN organization_subscriptions s ON s.organization_id=j.organization_id
        WHERE j.organization_id=target_id AND j.purpose='renewal'
        AND j.period_end > NOW()
        AND (j.provider_subscription_id=s.provider_subscription_id OR EXISTS (
            SELECT 1 FROM billing_collection_accounts a
            JOIN organization_billing_handoffs h ON h.id=a.handoff_id
            WHERE a.id=j.account_id AND h.status='scheduled'))
        AND j.status IN ('open','pending','failed','requires_action','review'));
$$;

CREATE OR REPLACE FUNCTION serialize_collection_membership()
RETURNS TRIGGER AS $$
DECLARE target_id BIGINT;
BEGIN
    -- Profile edits and idempotent owner repairs do not change active seats.
    -- Avoid holding a membership lock across unrelated provider operations.
    IF TG_OP = 'UPDATE' AND OLD.organization_id=NEW.organization_id AND OLD.status=NEW.status THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' AND NEW.status='active' AND EXISTS (
        SELECT 1 FROM organization_members WHERE organization_id=NEW.organization_id
        AND user_id=NEW.user_id AND status='active'
    ) THEN RETURN NEW; END IF;
    IF TG_OP = 'DELETE' THEN target_id := OLD.organization_id;
    ELSE target_id := NEW.organization_id; END IF;
    IF TG_OP = 'UPDATE' AND OLD.organization_id <> NEW.organization_id THEN
        PERFORM pg_advisory_xact_lock(hashtextextended('billing-org:' || LEAST(OLD.organization_id,NEW.organization_id)::text, 0));
        PERFORM pg_advisory_xact_lock(hashtextextended('billing-org:' || GREATEST(OLD.organization_id,NEW.organization_id)::text, 0));
    ELSE
        PERFORM pg_advisory_xact_lock(hashtextextended('billing-org:' || target_id::text, 0));
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF NEW.status = 'active' AND (TG_OP = 'INSERT' OR OLD.status <> 'active' OR OLD.organization_id <> NEW.organization_id) THEN
            IF billing_collection_admission_blocked(target_id) THEN
                RAISE EXCEPTION 'Renewal payment is pending; new active members must wait for verified payment'
                    USING ERRCODE='check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_collection_membership ON organization_members;
CREATE TRIGGER trg_collection_membership BEFORE INSERT OR UPDATE OR DELETE
ON organization_members FOR EACH ROW EXECUTE FUNCTION serialize_collection_membership();
COMMIT;
