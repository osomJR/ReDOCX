-- 013_secure_team_message_attachments.sql
-- Adds fail-closed security state, malware-scan evidence, application-level
-- AES-256-GCM encryption, and an append-oriented audit trail exclusively for
-- Business/Enterprise team-message attachments.
--
-- Apply after 012_add_shared_realtime_and_access_revocation.sql.
-- Existing filesystem attachments are intentionally marked legacy_unverified
-- and remain download-locked until the supplied migration command secures them.

BEGIN;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS storage_backend VARCHAR(40)
        NOT NULL DEFAULT 'legacy_filesystem';

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS security_status VARCHAR(30)
        NOT NULL DEFAULT 'legacy_unverified';

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS malware_scan_status VARCHAR(30)
        NOT NULL DEFAULT 'unscanned';

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS malware_scanner VARCHAR(80);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS malware_scanner_version VARCHAR(160);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS scan_completed_at TIMESTAMPTZ;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS detected_content_type VARCHAR(255);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS validation_version VARCHAR(80);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS encryption_algorithm VARCHAR(40);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS encryption_key_id VARCHAR(64);

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS encryption_nonce BYTEA;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS encryption_aad_version SMALLINT;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS encrypted_content BYTEA;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS secured_at TIMESTAMPTZ;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS last_integrity_verified_at TIMESTAMPTZ;

ALTER TABLE conversation_message_attachments
    ADD COLUMN IF NOT EXISTS security_metadata JSONB
        NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_storage_backend_check;

ALTER TABLE conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_storage_backend_check
        CHECK (storage_backend IN ('legacy_filesystem', 'postgres_encrypted'));

ALTER TABLE conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_security_status_check;

ALTER TABLE conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_security_status_check
        CHECK (security_status IN ('legacy_unverified', 'secured', 'quarantined', 'rejected'));

ALTER TABLE conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_scan_status_check;

ALTER TABLE conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_scan_status_check
        CHECK (malware_scan_status IN ('unscanned', 'clean', 'infected', 'failed'));

ALTER TABLE conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_security_metadata_object_check;

ALTER TABLE conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_security_metadata_object_check
        CHECK (jsonb_typeof(security_metadata) = 'object');

ALTER TABLE conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_secured_state_check;

ALTER TABLE conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_secured_state_check
        CHECK (
            security_status <> 'secured'
            OR (
                storage_backend = 'postgres_encrypted'
                AND malware_scan_status = 'clean'
                AND NULLIF(BTRIM(malware_scanner), '') IS NOT NULL
                AND scan_completed_at IS NOT NULL
                AND NULLIF(BTRIM(detected_content_type), '') IS NOT NULL
                AND NULLIF(BTRIM(validation_version), '') IS NOT NULL
                AND encryption_algorithm = 'AES-256-GCM'
                AND NULLIF(BTRIM(encryption_key_id), '') IS NOT NULL
                AND OCTET_LENGTH(encryption_nonce) = 12
                AND encryption_aad_version = 1
                AND OCTET_LENGTH(encrypted_content) > 16
                AND secured_at IS NOT NULL
            )
        );

-- Reject plaintext writes from a stale application replica after cutover.
-- Existing rows are not touched; they remain legacy_unverified and locked by
-- the new download route until the explicit migration command secures them.
CREATE OR REPLACE FUNCTION enforce_secure_team_attachment_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.storage_backend <> 'postgres_encrypted'
       OR NEW.security_status <> 'secured'
       OR NEW.malware_scan_status <> 'clean'
       OR NEW.encrypted_content IS NULL THEN
        RAISE EXCEPTION 'plaintext team attachment inserts are disabled'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_secure_team_attachment_insert
    ON conversation_message_attachments;

CREATE TRIGGER trg_enforce_secure_team_attachment_insert
BEFORE INSERT ON conversation_message_attachments
FOR EACH ROW
EXECUTE FUNCTION enforce_secure_team_attachment_insert();

CREATE INDEX IF NOT EXISTS idx_team_attachments_security_status
    ON conversation_message_attachments (
        organization_id,
        security_status,
        created_at DESC,
        id DESC
    );

CREATE INDEX IF NOT EXISTS idx_team_attachments_key_rotation
    ON conversation_message_attachments (encryption_key_id, id)
    WHERE security_status = 'secured';

-- Older application versions copied storage_key/stored_filename into message
-- JSON. The API now rehydrates client-safe metadata from the attachment table,
-- so remove those stale internal fields during the migration.
UPDATE conversation_messages
SET metadata = JSONB_SET(
        metadata - 'attachments',
        '{attachments}',
        '[]'::JSONB,
        TRUE
    ),
    updated_at = NOW()
WHERE message_type = 'attachment'
  AND metadata ? 'attachments'
  AND (
      JSONB_TYPEOF(metadata -> 'attachments') <> 'array'
      OR EXISTS (
          SELECT 1
          FROM JSONB_ARRAY_ELEMENTS(
              CASE
                  WHEN JSONB_TYPEOF(metadata -> 'attachments') = 'array'
                      THEN metadata -> 'attachments'
                  ELSE '[]'::JSONB
              END
          ) AS attachment_elements(attachment_item)
          WHERE attachment_item ? 'storage_key'
             OR attachment_item ? 'stored_filename'
             OR attachment_item ? 'checksum_sha256'
      )
  );

CREATE TABLE IF NOT EXISTS team_attachment_security_events (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL,
    conversation_id BIGINT,
    message_id BIGINT,
    attachment_id BIGINT,
    actor_user_id TEXT NOT NULL,
    action VARCHAR(40) NOT NULL,
    outcome VARCHAR(30) NOT NULL,
    reason_code VARCHAR(100),
    request_id VARCHAR(160),
    details JSONB NOT NULL DEFAULT '{}'::JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT team_attachment_security_events_action_check
        CHECK (action IN ('upload', 'download', 'legacy_migration', 'key_rotation')),
    CONSTRAINT team_attachment_security_events_outcome_check
        CHECK (outcome IN ('succeeded', 'denied', 'rejected', 'failed')),
    CONSTRAINT team_attachment_security_events_details_object_check
        CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_team_attachment_security_events_org_time
    ON team_attachment_security_events (organization_id, occurred_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_team_attachment_security_events_attachment
    ON team_attachment_security_events (attachment_id, occurred_at DESC, id DESC)
    WHERE attachment_id IS NOT NULL;

COMMIT;
