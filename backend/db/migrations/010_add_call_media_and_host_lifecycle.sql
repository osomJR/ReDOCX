-- ReDOCX team calling: flexible audio/video calls and auditable host lifecycle.
-- Apply after 009_create_account_lifecycle.sql.

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS media_type TEXT NOT NULL DEFAULT 'video';

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS ended_by_user_id TEXT;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS end_reason TEXT;

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_media_type_check;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_media_type_check
        CHECK (media_type IN ('audio', 'video'));

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_end_reason_check;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_end_reason_check
        CHECK (
            end_reason IS NULL
            OR end_reason IN ('host_ended', 'empty_room', 'system')
        );

CREATE INDEX IF NOT EXISTS idx_call_sessions_media_status
    ON call_sessions (media_type, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_call_sessions_host_status
    ON call_sessions (created_by_user_id, status, created_at DESC);
