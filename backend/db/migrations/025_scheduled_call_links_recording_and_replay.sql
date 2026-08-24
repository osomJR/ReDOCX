-- 025_scheduled_call_links_recording_and_replay.sql
-- Organization-only scheduled call links, consent-governed audio recording,
-- and durable state required for missed-call replay.
-- Apply after 024_paystack_enterprise_activation_hardening.sql.

BEGIN;

CREATE TABLE IF NOT EXISTS organization_call_links (
    id BIGSERIAL PRIMARY KEY,
    public_id TEXT NOT NULL UNIQUE,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    created_by_user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'video',
    scheduled_start_at TIMESTAMPTZ NOT NULL,
    duration_minutes INTEGER NOT NULL,
    max_participants INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    active_call_session_id BIGINT,
    cancelled_at TIMESTAMPTZ,
    cancelled_by_user_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_call_links_public_id_check
        CHECK (public_id ~ '^[A-Za-z0-9_-]{32,96}$'),
    CONSTRAINT organization_call_links_title_check
        CHECK (CHAR_LENGTH(BTRIM(title)) BETWEEN 1 AND 120),
    CONSTRAINT organization_call_links_media_type_check
        CHECK (media_type IN ('audio', 'video')),
    CONSTRAINT organization_call_links_duration_check
        CHECK (duration_minutes BETWEEN 15 AND 720),
    CONSTRAINT organization_call_links_participant_limit_check
        CHECK (max_participants BETWEEN 2 AND 500),
    CONSTRAINT organization_call_links_status_check
        CHECK (status IN ('scheduled', 'active', 'completed', 'cancelled')),
    CONSTRAINT organization_call_links_cancelled_state_check
        CHECK (
            (status = 'cancelled' AND cancelled_at IS NOT NULL)
            OR (status <> 'cancelled')
        )
);

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS call_link_id BIGINT
        REFERENCES organization_call_links(id) ON DELETE SET NULL;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS participant_limit INTEGER;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS scheduled_end_at TIMESTAMPTZ;

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_participant_limit_check;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_participant_limit_check
        CHECK (participant_limit IS NULL OR participant_limit BETWEEN 2 AND 500);

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_scheduled_window_check;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_scheduled_window_check
        CHECK (
            scheduled_end_at IS NULL
            OR scheduled_end_at > COALESCE(started_at, created_at)
        );

ALTER TABLE organization_call_links
    DROP CONSTRAINT IF EXISTS organization_call_links_active_call_fk;

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_sessions_id_organization
    ON call_sessions (id, organization_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_links_id_organization
    ON organization_call_links (id, organization_id);

ALTER TABLE organization_call_links
    ADD CONSTRAINT organization_call_links_active_call_fk
        FOREIGN KEY (active_call_session_id)
        REFERENCES call_sessions(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION enforce_organization_call_link_scope()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_TABLE_NAME = 'call_sessions' AND NEW.call_link_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM organization_call_links link
            WHERE link.id = NEW.call_link_id
              AND link.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'call link and call session must belong to the same organization';
        END IF;
    ELSIF TG_TABLE_NAME = 'organization_call_links'
          AND NEW.active_call_session_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM call_sessions call
            WHERE call.id = NEW.active_call_session_id
              AND call.organization_id = NEW.organization_id
        ) THEN
            RAISE EXCEPTION 'active call and call link must belong to the same organization';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_call_session_call_link_scope ON call_sessions;
CREATE TRIGGER trg_call_session_call_link_scope
BEFORE INSERT OR UPDATE OF call_link_id, organization_id ON call_sessions
FOR EACH ROW
EXECUTE FUNCTION enforce_organization_call_link_scope();

DROP TRIGGER IF EXISTS trg_organization_call_link_active_scope
    ON organization_call_links;
CREATE TRIGGER trg_organization_call_link_active_scope
BEFORE INSERT OR UPDATE OF active_call_session_id, organization_id
ON organization_call_links
FOR EACH ROW
EXECUTE FUNCTION enforce_organization_call_link_scope();

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_end_reason_check;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_end_reason_check
        CHECK (
            end_reason IS NULL
            OR end_reason IN (
                'host_ended',
                'empty_room',
                'system',
                'caller_cancelled',
                'no_answer',
                'superseded',
                'connection_failed',
                'scheduled_end'
            )
        );

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_sessions_live_call_link
    ON call_sessions (call_link_id)
    WHERE call_link_id IS NOT NULL
      AND status IN ('ringing', 'active');

CREATE INDEX IF NOT EXISTS idx_organization_call_links_schedule
    ON organization_call_links (organization_id, scheduled_start_at, id)
    WHERE status IN ('scheduled', 'active');

CREATE INDEX IF NOT EXISTS idx_call_sessions_scheduled_end
    ON call_sessions (scheduled_end_at, id)
    WHERE status = 'active' AND scheduled_end_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS call_recordings (
    id BIGSERIAL PRIMARY KEY,
    call_session_id BIGINT NOT NULL
        REFERENCES call_sessions(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    requested_by_user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'requested',
    egress_id TEXT,
    storage_key TEXT,
    content_type TEXT NOT NULL DEFAULT 'audio/ogg',
    participant_count INTEGER NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    retention_expires_at TIMESTAMPTZ NOT NULL,
    file_size_bytes BIGINT,
    checksum_sha256 TEXT,
    failure_reason TEXT,
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT call_recordings_status_check
        CHECK (status IN (
            'requested', 'starting', 'recording', 'stopping',
            'completed', 'failed', 'cancelled', 'purging', 'deleted'
        )),
    CONSTRAINT call_recordings_participant_count_check
        CHECK (participant_count >= 2),
    CONSTRAINT call_recordings_file_size_check
        CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    CONSTRAINT call_recordings_storage_key_check
        CHECK (storage_key IS NULL OR storage_key !~ '(^|/)(\.\.?)(/|$)')
);

ALTER TABLE call_recordings
    DROP CONSTRAINT IF EXISTS call_recordings_call_organization_fk;

ALTER TABLE call_recordings
    ADD CONSTRAINT call_recordings_call_organization_fk
        FOREIGN KEY (call_session_id, organization_id)
        REFERENCES call_sessions(id, organization_id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_recordings_one_open_per_call
    ON call_recordings (call_session_id)
    WHERE status IN ('requested', 'starting', 'recording', 'stopping');

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_recordings_egress_id
    ON call_recordings (egress_id)
    WHERE egress_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_recordings_storage_key
    ON call_recordings (storage_key)
    WHERE storage_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_recordings_id_call_organization
    ON call_recordings (id, call_session_id, organization_id);

CREATE INDEX IF NOT EXISTS idx_call_recordings_retention_due
    ON call_recordings (retention_expires_at, id)
    WHERE status = 'completed' AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS call_recording_consents (
    call_session_id BIGINT NOT NULL
        REFERENCES call_sessions(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    recording_id BIGINT NOT NULL
        REFERENCES call_recordings(id) ON DELETE CASCADE,
    consent_version TEXT NOT NULL DEFAULT 'call-audio-recording-v1',
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (recording_id, user_id),

    CONSTRAINT call_recording_consents_version_check
        CHECK (CHAR_LENGTH(BTRIM(consent_version)) BETWEEN 1 AND 80),
    CONSTRAINT call_recording_consents_time_check
        CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

ALTER TABLE call_recording_consents
    DROP CONSTRAINT IF EXISTS call_recording_consents_participant_fk;

ALTER TABLE call_recording_consents
    ADD CONSTRAINT call_recording_consents_participant_fk
        FOREIGN KEY (call_session_id, user_id)
        REFERENCES call_participants(call_session_id, user_id) ON DELETE CASCADE;

ALTER TABLE call_recording_consents
    DROP CONSTRAINT IF EXISTS call_recording_consents_recording_scope_fk;

ALTER TABLE call_recording_consents
    ADD CONSTRAINT call_recording_consents_recording_scope_fk
        FOREIGN KEY (recording_id, call_session_id, organization_id)
        REFERENCES call_recordings(id, call_session_id, organization_id)
        ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_call_recording_consents_call_user
    ON call_recording_consents (call_session_id, user_id, recording_id);

ALTER TABLE organization_communication_policies
    ADD COLUMN IF NOT EXISTS call_recording_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE organization_communication_policies
    ADD COLUMN IF NOT EXISTS call_recording_retention_days INTEGER NOT NULL DEFAULT 30;

ALTER TABLE organization_communication_policies
    DROP CONSTRAINT IF EXISTS organization_communication_recording_retention_check;

ALTER TABLE organization_communication_policies
    ADD CONSTRAINT organization_communication_recording_retention_check
        CHECK (call_recording_retention_days BETWEEN 1 AND 365);

CREATE OR REPLACE FUNCTION set_organization_call_link_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_organization_call_link_updated_at
    ON organization_call_links;
CREATE TRIGGER trg_organization_call_link_updated_at
BEFORE UPDATE ON organization_call_links
FOR EACH ROW
EXECUTE FUNCTION set_organization_call_link_updated_at();

CREATE OR REPLACE FUNCTION set_call_recording_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_call_recording_updated_at ON call_recordings;
CREATE TRIGGER trg_call_recording_updated_at
BEFORE UPDATE ON call_recordings
FOR EACH ROW
EXECUTE FUNCTION set_call_recording_updated_at();

COMMIT;
