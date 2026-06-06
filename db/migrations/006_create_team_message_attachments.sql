-- 006_create_team_message_attachments.sql
-- Adds attachment support for Business/Enterprise team conversations.
-- Attachments are stored on disk/object storage and referenced from messages.

ALTER TABLE conversation_messages
    DROP CONSTRAINT IF EXISTS conversation_messages_message_type_check;

ALTER TABLE conversation_messages
    ADD CONSTRAINT conversation_messages_message_type_check
    CHECK (message_type IN ('text', 'system', 'call_event', 'attachment'));

CREATE TABLE IF NOT EXISTS conversation_message_attachments (
    id BIGSERIAL PRIMARY KEY,
    message_id BIGINT NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
    conversation_id BIGINT NOT NULL REFERENCES organization_conversations(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    uploaded_by_user_id TEXT NOT NULL,

    -- image, audio, video, document, or generic file.
    kind TEXT NOT NULL,

    original_filename TEXT NOT NULL,
    stored_filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    file_size_bytes BIGINT NOT NULL,
    checksum_sha256 TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT conversation_message_attachments_kind_check
        CHECK (kind IN ('image', 'audio', 'video', 'document', 'file')),

    CONSTRAINT conversation_message_attachments_size_check
        CHECK (file_size_bytes > 0),

    CONSTRAINT conversation_message_attachments_storage_key_unique
        UNIQUE (storage_key)
);

CREATE INDEX IF NOT EXISTS idx_conversation_message_attachments_message_id
    ON conversation_message_attachments (message_id);

CREATE INDEX IF NOT EXISTS idx_conversation_message_attachments_conversation_id
    ON conversation_message_attachments (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_message_attachments_organization_id
    ON conversation_message_attachments (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_message_attachments_uploader
    ON conversation_message_attachments (uploaded_by_user_id, created_at DESC);
