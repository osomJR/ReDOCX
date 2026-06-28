-- 009_create_account_lifecycle.sql
-- Account soft-deactivation, restore window, delayed purge, and explicit ownership-transfer audit.

BEGIN;

CREATE TABLE IF NOT EXISTS account_lifecycle (
    user_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active',
    deletion_reason TEXT,
    deactivated_at TIMESTAMPTZ,
    restore_deadline TIMESTAMPTZ,
    purge_after TIMESTAMPTZ,
    restored_at TIMESTAMPTZ,
    purged_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT account_lifecycle_user_id_not_blank_check
        CHECK (LENGTH(BTRIM(user_id)) > 0),
    CONSTRAINT account_lifecycle_status_check
        CHECK (status IN (
            'active',
            'deactivation_requested',
            'deactivated_pending_deletion',
            'purge_due',
            'purged'
        )),
    CONSTRAINT account_lifecycle_deadline_check
        CHECK (
            status NOT IN ('deactivated_pending_deletion', 'purge_due')
            OR (restore_deadline IS NOT NULL AND purge_after IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS idx_account_lifecycle_status_purge_after
    ON account_lifecycle (status, purge_after)
    WHERE status IN ('deactivated_pending_deletion', 'purge_due');

CREATE TABLE IF NOT EXISTS organization_ownership_transfers (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    previous_owner_user_id TEXT NOT NULL,
    new_owner_user_id TEXT NOT NULL,
    transferred_by_user_id TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_ownership_transfer_previous_owner_not_blank
        CHECK (LENGTH(BTRIM(previous_owner_user_id)) > 0),
    CONSTRAINT organization_ownership_transfer_new_owner_not_blank
        CHECK (LENGTH(BTRIM(new_owner_user_id)) > 0),
    CONSTRAINT organization_ownership_transfer_actor_not_blank
        CHECK (LENGTH(BTRIM(transferred_by_user_id)) > 0),
    CONSTRAINT organization_ownership_transfer_different_users
        CHECK (previous_owner_user_id <> new_owner_user_id)
);

CREATE INDEX IF NOT EXISTS idx_organization_ownership_transfers_org_created
    ON organization_ownership_transfers (organization_id, created_at DESC);

CREATE OR REPLACE FUNCTION set_account_lifecycle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_account_lifecycle_updated_at ON account_lifecycle;
CREATE TRIGGER trg_account_lifecycle_updated_at
BEFORE UPDATE ON account_lifecycle
FOR EACH ROW
EXECUTE FUNCTION set_account_lifecycle_updated_at();

COMMIT;
