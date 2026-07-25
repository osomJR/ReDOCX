-- 018_attachment_only_retention.sql
-- Team messages remain for the lifetime of the organization.
-- Team attachment ciphertext and metadata are deleted after exactly 365 days,
-- unless the organization is under legal hold.

BEGIN;

UPDATE organization_communication_policies
SET attachment_retention_days = 365,
    updated_at = NOW()
WHERE attachment_retention_days <> 365;

ALTER TABLE organization_communication_policies
    ALTER COLUMN attachment_retention_days SET DEFAULT 365;

ALTER TABLE organization_communication_policies
    DROP CONSTRAINT IF EXISTS organization_communication_attachment_retention_check;

ALTER TABLE organization_communication_policies
    ADD CONSTRAINT organization_communication_attachment_retention_check
    CHECK (attachment_retention_days = 365);

COMMENT ON COLUMN organization_communication_policies.message_retention_days IS
    'Deprecated compatibility field. ReDOCX does not time-delete team messages; messages remain until organization deletion.';

COMMENT ON COLUMN organization_communication_policies.attachment_retention_days IS
    'Fixed at 365 days. Enforced by the ReDOCX attachment-only retention worker; legal hold suspends deletion.';

CREATE INDEX IF NOT EXISTS idx_team_attachments_retention_due
    ON conversation_message_attachments (created_at, id)
    INCLUDE (organization_id);

COMMIT;
