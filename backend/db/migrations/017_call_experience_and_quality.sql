-- 017_call_experience_and_quality.sql
-- Call-specific push delivery and bounded client quality telemetry.
-- Apply after 016_team_enterprise_hardening.sql.

BEGIN;

-- Call lifecycle notifications are inserted explicitly by team_call_lifecycle.
-- Prevent the generic message trigger from also creating a delayed "new message"
-- push for the corresponding call_event conversation record.
CREATE OR REPLACE FUNCTION enqueue_team_message_push_notifications()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.message_type = 'call_event' THEN
        RETURN NEW;
    END IF;

    INSERT INTO team_notification_outbox (
        organization_id, recipient_user_id, event_type, event_key, payload
    )
    SELECT
        NEW.organization_id,
        cm.user_id,
        'message.created',
        'message.created:' || NEW.id::TEXT || ':' || cm.user_id,
        JSONB_BUILD_OBJECT(
            'event_version', 1,
            'type', 'message.created',
            'organization_id', NEW.organization_id,
            'conversation_id', NEW.conversation_id,
            'message_id', NEW.id,
            'message_type', NEW.message_type,
            'body_preview', CASE
                WHEN policy.push_notification_previews_enabled
                THEN LEFT(NEW.body, 180)
                ELSE NULL
            END,
            'sender_user_id', NEW.sender_user_id
        )
    FROM conversation_members cm
    JOIN organization_communication_policies policy
      ON policy.organization_id = NEW.organization_id
     AND policy.push_notifications_enabled = TRUE
    WHERE cm.conversation_id = NEW.conversation_id
      AND cm.status = 'active'
      AND cm.user_id <> NEW.sender_user_id
    ON CONFLICT (event_key) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS call_quality_events (
    id BIGSERIAL PRIMARY KEY,
    call_session_id BIGINT NOT NULL
        REFERENCES call_sessions(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    client_event_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT call_quality_events_event_type_check
        CHECK (event_type IN (
            'prejoin.opened',
            'connection.requested',
            'connection.connected',
            'connection.quality',
            'connection.reconnecting',
            'connection.reconnected',
            'connection.disconnected',
            'connection.recovery_failed',
            'media.stats'
        )),
    CONSTRAINT call_quality_events_metrics_check
        CHECK (JSONB_TYPEOF(metrics) = 'object'),
    CONSTRAINT call_quality_events_client_event_unique
        UNIQUE (call_session_id, user_id, client_event_id)
);

CREATE INDEX IF NOT EXISTS idx_call_quality_events_call_time
    ON call_quality_events (call_session_id, occurred_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_call_quality_events_organization_time
    ON call_quality_events (organization_id, occurred_at DESC, id DESC);

COMMIT;
