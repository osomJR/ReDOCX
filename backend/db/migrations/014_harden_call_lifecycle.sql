-- 014_harden_call_lifecycle.sql
-- Authoritative, replay-safe LiveKit call lifecycle state.
-- Apply after 013_secure_team_message_attachments.sql.

BEGIN;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS ringing_expires_at TIMESTAMPTZ;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS provider_room_sid TEXT;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS provider_started_at TIMESTAMPTZ;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS provider_finished_at TIMESTAMPTZ;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS last_provider_event_at TIMESTAMPTZ;

ALTER TABLE call_sessions
    ADD COLUMN IF NOT EXISTS lifecycle_version BIGINT NOT NULL DEFAULT 0;

UPDATE call_sessions
SET ringing_expires_at = COALESCE(
        ringing_expires_at,
        created_at + INTERVAL '90 seconds'
    )
WHERE ringing_expires_at IS NULL;

ALTER TABLE call_sessions
    ALTER COLUMN ringing_expires_at SET DEFAULT (NOW() + INTERVAL '90 seconds');

ALTER TABLE call_sessions
    ALTER COLUMN ringing_expires_at SET NOT NULL;

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
                'connection_failed'
            )
        );

ALTER TABLE call_sessions
    DROP CONSTRAINT IF EXISTS call_sessions_terminal_consistency_check;

UPDATE call_sessions
SET ended_at = COALESCE(ended_at, updated_at, created_at, NOW())
WHERE status IN ('ended', 'missed', 'cancelled')
  AND ended_at IS NULL;

ALTER TABLE call_sessions
    ADD CONSTRAINT call_sessions_terminal_consistency_check
        CHECK (
            status NOT IN ('ended', 'missed', 'cancelled')
            OR ended_at IS NOT NULL
        );

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS token_issued_at TIMESTAMPTZ;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS provider_participant_sid TEXT;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS provider_joined_at TIMESTAMPTZ;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS provider_left_at TIMESTAMPTZ;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS last_provider_event_at TIMESTAMPTZ;

ALTER TABLE call_participants
    DROP CONSTRAINT IF EXISTS call_participants_status_check;

ALTER TABLE call_participants
    ADD CONSTRAINT call_participants_status_check
        CHECK (
            status IN (
                'invited',
                'connecting',
                'joined',
                'declined',
                'left',
                'missed',
                'removed'
            )
        );

-- Never guess which live provider room should survive. Abort the migration if
-- legacy data has overlapping calls so an operator can close the corresponding
-- LiveKit rooms and reconcile those rows deliberately.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM call_sessions
        WHERE conversation_id IS NOT NULL
          AND status IN ('ringing', 'active')
        GROUP BY conversation_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Overlapping live calls exist. Reconcile their LiveKit rooms and call_sessions rows before migration 014.';
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_call_sessions_live_conversation
    ON call_sessions (conversation_id)
    WHERE conversation_id IS NOT NULL
      AND status IN ('ringing', 'active');

CREATE INDEX IF NOT EXISTS idx_call_sessions_ringing_expiry
    ON call_sessions (ringing_expires_at, id)
    WHERE status = 'ringing';

CREATE INDEX IF NOT EXISTS idx_call_sessions_active_duration
    ON call_sessions (started_at, id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_call_sessions_provider_room_sid
    ON call_sessions (provider_room_sid)
    WHERE provider_room_sid IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_call_participants_provider_state
    ON call_participants (call_session_id, status, last_provider_event_at DESC);

-- Migration 012 created this partial index before the provider-controlled
-- "connecting" state existed. Rebuild it so pre-join participants are covered
-- by membership-revocation lookups as well.
DROP INDEX IF EXISTS idx_call_participants_active_org_user;

CREATE INDEX idx_call_participants_active_org_user
    ON call_participants (organization_id, user_id, call_session_id)
    WHERE status IN ('invited', 'connecting', 'joined');

CREATE TABLE IF NOT EXISTS livekit_webhook_events (
    id BIGSERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    provider_created_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    call_session_id BIGINT REFERENCES call_sessions(id) ON DELETE SET NULL,
    room_name TEXT,
    participant_identity TEXT,
    processing_status TEXT NOT NULL DEFAULT 'received',
    processing_detail TEXT,

    CONSTRAINT livekit_webhook_events_status_check
        CHECK (processing_status IN ('received', 'processed', 'ignored'))
);

CREATE INDEX IF NOT EXISTS idx_livekit_webhook_events_call_created
    ON livekit_webhook_events (call_session_id, provider_created_at DESC);

CREATE INDEX IF NOT EXISTS idx_livekit_webhook_events_received
    ON livekit_webhook_events (received_at DESC);

ALTER TABLE team_realtime_outbox
    DROP CONSTRAINT IF EXISTS team_realtime_outbox_delivery_scope_check;

ALTER TABLE team_realtime_outbox
    ADD CONSTRAINT team_realtime_outbox_delivery_scope_check
        CHECK (
            delivery_scope IN (
                'organization_users',
                'organization',
                'account_email',
                'account_user',
                'organization_revoke',
                'call_media_revoke',
                'call_room_terminate'
            )
        );

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_call_media_pending
    ON team_realtime_outbox (available_at, id)
    WHERE status = 'pending'
      AND delivery_scope IN ('call_media_revoke', 'call_room_terminate');

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_pending_aggregate_order
    ON team_realtime_outbox (aggregate_type, aggregate_id, id)
    WHERE status = 'pending';

COMMIT;
