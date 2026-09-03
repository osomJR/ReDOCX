BEGIN;

-- Stable document identity in normalized E-Signature rows. The canonical
-- envelope snapshot remains state_json, but these columns keep operational
-- queries document-aware and preserve the audit chain outside the snapshot.
ALTER TABLE esignature_fields
    ADD COLUMN IF NOT EXISTS document_id VARCHAR(96);

ALTER TABLE esignature_audit_events
    ADD COLUMN IF NOT EXISTS document_id VARCHAR(96),
    ADD COLUMN IF NOT EXISTS previous_event_hash CHAR(64),
    ADD COLUMN IF NOT EXISTS event_hash CHAR(64);

ALTER TABLE esignature_files
    ADD COLUMN IF NOT EXISTS document_id VARCHAR(96);

-- Existing envelopes were single-document by definition. Backfill their
-- field identity before enforcing the invariant used by all new writes.
UPDATE esignature_fields
SET document_id = 'document_1'
WHERE document_id IS NULL;

ALTER TABLE esignature_fields
    ALTER COLUMN document_id SET DEFAULT 'document_1',
    ALTER COLUMN document_id SET NOT NULL;

-- Multi-document roles contain the stable ID (for example,
-- signed_pdf:document_20 and preview_12:document_20).
ALTER TABLE esignature_files
    ALTER COLUMN file_role TYPE VARCHAR(160) USING file_role::VARCHAR(160);

CREATE INDEX IF NOT EXISTS idx_esignature_fields_envelope_document
    ON esignature_fields (envelope_id, document_id);

CREATE INDEX IF NOT EXISTS idx_esignature_audit_envelope_document
    ON esignature_audit_events (envelope_id, document_id, created_at_iso);

CREATE INDEX IF NOT EXISTS idx_esignature_files_envelope_document
    ON esignature_files (envelope_id, document_id, file_role);

CREATE UNIQUE INDEX IF NOT EXISTS idx_esignature_audit_event_hash
    ON esignature_audit_events (envelope_id, event_hash)
    WHERE event_hash IS NOT NULL;

COMMIT;
