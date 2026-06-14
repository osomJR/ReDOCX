-- 008_create_esignature_persistence.sql
-- Persist ReDOCX Sign envelope state, signer tokens, recipients, fields,
-- audit events, and generated file references.
--
-- Important security rule:
-- - raw signing tokens are never stored;
-- - only esignature_tokens.token_hash is persisted.
--
-- The token foreign key is DEFERRABLE because ESignatureService creates tokens
-- during the send flow before it calls envelope_repository.save(state) at the
-- end of process(...). Both writes happen in the same database transaction.

CREATE TABLE IF NOT EXISTS esignature_envelopes (
    envelope_id TEXT PRIMARY KEY,
    owner_email TEXT,
    workflow TEXT NOT NULL,
    status TEXT NOT NULL,
    source_document_sha256 TEXT,
    signed_pdf_storage_key TEXT,
    audit_certificate_storage_key TEXT,
    state_json JSONB NOT NULL,
    created_at_iso TEXT,
    updated_at_iso TEXT,
    expires_at_iso TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS esignature_recipients (
    id BIGSERIAL PRIMARY KEY,
    envelope_id TEXT NOT NULL REFERENCES esignature_envelopes(envelope_id) ON DELETE CASCADE,
    signer_email TEXT NOT NULL,
    signer_name TEXT NOT NULL,
    role TEXT NOT NULL,
    signing_order INTEGER NOT NULL,
    status TEXT NOT NULL,
    recipient_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (envelope_id, signer_email)
);

CREATE TABLE IF NOT EXISTS esignature_fields (
    id BIGSERIAL PRIMARY KEY,
    envelope_id TEXT NOT NULL REFERENCES esignature_envelopes(envelope_id) ON DELETE CASCADE,
    field_id TEXT,
    assigned_to_email TEXT NOT NULL,
    field_type TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    field_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS esignature_audit_events (
    event_id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL REFERENCES esignature_envelopes(envelope_id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    actor_email TEXT,
    ip_address TEXT,
    user_agent TEXT,
    document_sha256 TEXT,
    created_at_iso TEXT NOT NULL,
    event_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS esignature_tokens (
    token_id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    signer_email TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at_iso TEXT NOT NULL,
    used_at_iso TEXT,
    revoked_at_iso TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_esignature_tokens_envelope
        FOREIGN KEY (envelope_id)
        REFERENCES esignature_envelopes(envelope_id)
        ON DELETE CASCADE
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS esignature_files (
    id BIGSERIAL PRIMARY KEY,
    envelope_id TEXT NOT NULL REFERENCES esignature_envelopes(envelope_id) ON DELETE CASCADE,
    file_role TEXT NOT NULL,
    filename TEXT,
    storage_key TEXT,
    download_url TEXT,
    content_type TEXT DEFAULT 'application/pdf',
    file_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_esignature_envelopes_owner_email
ON esignature_envelopes(owner_email);

CREATE INDEX IF NOT EXISTS idx_esignature_envelopes_status
ON esignature_envelopes(status);

CREATE INDEX IF NOT EXISTS idx_esignature_recipients_email
ON esignature_recipients(signer_email);

CREATE INDEX IF NOT EXISTS idx_esignature_fields_envelope_assignee
ON esignature_fields(envelope_id, assigned_to_email);

CREATE INDEX IF NOT EXISTS idx_esignature_audit_events_envelope
ON esignature_audit_events(envelope_id, created_at_iso);

CREATE INDEX IF NOT EXISTS idx_esignature_tokens_envelope_id
ON esignature_tokens(envelope_id);

CREATE INDEX IF NOT EXISTS idx_esignature_tokens_signer_email
ON esignature_tokens(signer_email);

CREATE INDEX IF NOT EXISTS idx_esignature_tokens_token_hash
ON esignature_tokens(token_hash);

CREATE INDEX IF NOT EXISTS idx_esignature_files_envelope
ON esignature_files(envelope_id, file_role);
