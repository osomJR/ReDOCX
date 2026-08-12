-- 009_harden_esignature_workflow.sql
-- Concurrency and one-time-link hardening for the completed recipient workflow.

-- A token hash identifies exactly one one-time signing link.
CREATE UNIQUE INDEX IF NOT EXISTS uq_esignature_tokens_token_hash
ON esignature_tokens(token_hash);

-- Speeds active-link revocation/resend and recipient workflow lookups.
CREATE INDEX IF NOT EXISTS idx_esignature_tokens_active_recipient
ON esignature_tokens(envelope_id, signer_email)
WHERE used_at_iso IS NULL AND revoked_at_iso IS NULL;

-- Prevent invalid lifecycle rows from being inserted outside the application.
ALTER TABLE esignature_envelopes
    DROP CONSTRAINT IF EXISTS chk_esignature_envelope_status;

ALTER TABLE esignature_envelopes
    ADD CONSTRAINT chk_esignature_envelope_status
    CHECK (status IN (
        'draft',
        'sent',
        'viewed',
        'partially_signed',
        'completed',
        'voided',
        'expired'
    ));

ALTER TABLE esignature_recipients
    DROP CONSTRAINT IF EXISTS chk_esignature_recipient_status;

ALTER TABLE esignature_recipients
    ADD CONSTRAINT chk_esignature_recipient_status
    CHECK (status IN ('pending', 'sent', 'viewed', 'signed', 'declined'));
