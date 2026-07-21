-- 016_team_enterprise_hardening.sql
-- Apply after 015_allow_best_effort_secure_attachments.sql.
--
-- Adds compact-event membership versions, read state, multilingual search,
-- tenant retention policy, tamper-evident immutable audit events, tenant key
-- metadata, and a durable browser-push outbox.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE organization_conversations
    ADD COLUMN IF NOT EXISTS membership_version BIGINT NOT NULL DEFAULT 1;

CREATE OR REPLACE FUNCTION bump_conversation_membership_version()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    resolved_conversation_id BIGINT;
BEGIN
    resolved_conversation_id := CASE
        WHEN TG_OP = 'DELETE' THEN OLD.conversation_id
        ELSE NEW.conversation_id
    END;
    UPDATE organization_conversations
    SET membership_version = membership_version + 1,
        updated_at = NOW()
    WHERE id = resolved_conversation_id;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_conversation_members_membership_version
    ON conversation_members;
DROP TRIGGER IF EXISTS trg_conversation_members_membership_version_insert_delete
    ON conversation_members;
DROP TRIGGER IF EXISTS trg_conversation_members_membership_version_update
    ON conversation_members;
CREATE TRIGGER trg_conversation_members_membership_version_insert_delete
AFTER INSERT OR DELETE ON conversation_members
FOR EACH ROW
EXECUTE FUNCTION bump_conversation_membership_version();
CREATE TRIGGER trg_conversation_members_membership_version_update
AFTER UPDATE OF role, status ON conversation_members
FOR EACH ROW
EXECUTE FUNCTION bump_conversation_membership_version();

CREATE TABLE IF NOT EXISTS organization_communication_policies (
    organization_id BIGINT PRIMARY KEY
        REFERENCES organizations(id) ON DELETE CASCADE,
    message_retention_days INTEGER NOT NULL DEFAULT 365,
    attachment_retention_days INTEGER NOT NULL DEFAULT 365,
    legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
    active_encryption_key_id TEXT,
    push_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    push_notification_previews_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT organization_communication_message_retention_check
        CHECK (message_retention_days BETWEEN 1 AND 3650),
    CONSTRAINT organization_communication_attachment_retention_check
        CHECK (attachment_retention_days BETWEEN 1 AND 3650),
    CONSTRAINT organization_communication_key_id_check
        CHECK (
            active_encryption_key_id IS NULL
            OR active_encryption_key_id ~ '^[A-Za-z0-9._-]{1,64}$'
        )
);

CREATE OR REPLACE FUNCTION set_organization_communication_policy_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_organization_communication_policy_updated_at
    ON organization_communication_policies;
CREATE TRIGGER trg_organization_communication_policy_updated_at
BEFORE UPDATE ON organization_communication_policies
FOR EACH ROW
EXECUTE FUNCTION set_organization_communication_policy_updated_at();

CREATE TABLE IF NOT EXISTS organization_encryption_key_versions (
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    key_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'environment_keyring',
    external_key_reference TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (organization_id, key_id),
    CONSTRAINT organization_encryption_key_status_check
        CHECK (status IN ('active', 'decrypt_only', 'retired')),
    CONSTRAINT organization_encryption_key_id_check
        CHECK (key_id ~ '^[A-Za-z0-9._-]{1,64}$')
);

CREATE TABLE IF NOT EXISTS conversation_read_state (
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    conversation_id BIGINT NOT NULL
        REFERENCES organization_conversations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    last_read_message_id BIGINT
        REFERENCES conversation_messages(id) ON DELETE SET NULL,
    last_read_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_read_state_user_org
    ON conversation_read_state (user_id, organization_id, updated_at DESC);

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS search_document TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('simple', COALESCE(body, ''))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_search_document
    ON conversation_messages USING GIN (search_document);

CREATE TABLE IF NOT EXISTS team_audit_events (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL,
    actor_user_id TEXT,
    target_user_id TEXT,
    conversation_id BIGINT,
    message_id BIGINT,
    attachment_id BIGINT,
    call_session_id BIGINT,
    request_id TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    previous_hash BYTEA,
    event_hash BYTEA NOT NULL,
    CONSTRAINT team_audit_event_type_check
        CHECK (NULLIF(BTRIM(event_type), '') IS NOT NULL),
    CONSTRAINT team_audit_metadata_object_check
        CHECK (JSONB_TYPEOF(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_team_audit_events_org_time
    ON team_audit_events (organization_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_team_audit_events_conversation
    ON team_audit_events (conversation_id, occurred_at DESC)
    WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_team_audit_events_attachment
    ON team_audit_events (attachment_id, occurred_at DESC)
    WHERE attachment_id IS NOT NULL;

CREATE OR REPLACE FUNCTION seal_team_audit_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    prior BYTEA;
    canonical TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('team_audit:' || NEW.organization_id::TEXT, 0));
    SELECT event_hash
    INTO prior
    FROM team_audit_events
    WHERE organization_id = NEW.organization_id
    ORDER BY id DESC
    LIMIT 1;

    NEW.previous_hash := prior;
    canonical := CONCAT_WS(
        '|',
        NEW.organization_id::TEXT,
        NEW.event_type,
        COALESCE(NEW.actor_user_id, ''),
        COALESCE(NEW.target_user_id, ''),
        COALESCE(NEW.conversation_id::TEXT, ''),
        COALESCE(NEW.message_id::TEXT, ''),
        COALESCE(NEW.attachment_id::TEXT, ''),
        COALESCE(NEW.call_session_id::TEXT, ''),
        COALESCE(NEW.request_id, ''),
        NEW.occurred_at::TEXT,
        NEW.metadata::TEXT,
        COALESCE(ENCODE(prior, 'hex'), '')
    );
    NEW.event_hash := DIGEST(CONVERT_TO(canonical, 'UTF8'), 'sha256');
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_seal_team_audit_event ON team_audit_events;
CREATE TRIGGER trg_seal_team_audit_event
BEFORE INSERT ON team_audit_events
FOR EACH ROW
EXECUTE FUNCTION seal_team_audit_event();

CREATE OR REPLACE FUNCTION deny_team_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'team audit events are immutable'
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_deny_team_audit_update ON team_audit_events;
CREATE TRIGGER trg_deny_team_audit_update
BEFORE UPDATE ON team_audit_events
FOR EACH ROW
EXECUTE FUNCTION deny_team_audit_mutation();

DROP TRIGGER IF EXISTS trg_deny_team_audit_delete ON team_audit_events;
CREATE TRIGGER trg_deny_team_audit_delete
BEFORE DELETE ON team_audit_events
FOR EACH ROW
EXECUTE FUNCTION deny_team_audit_mutation();

CREATE OR REPLACE FUNCTION audit_team_message_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    source_row conversation_messages%ROWTYPE;
    resolved_type TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        source_row := OLD;
    ELSE
        source_row := NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        resolved_type := 'message.created';
    ELSIF TG_OP = 'DELETE' THEN
        resolved_type := 'message.retention.deleted';
    ELSIF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at AND NEW.deleted_at IS NOT NULL THEN
        resolved_type := 'message.deleted';
    ELSIF NEW.edited_at IS DISTINCT FROM OLD.edited_at AND NEW.edited_at IS NOT NULL THEN
        resolved_type := 'message.edited';
    ELSE
        RETURN NULL;
    END IF;

    INSERT INTO team_audit_events (
        organization_id, event_type, actor_user_id,
        conversation_id, message_id, metadata
    ) VALUES (
        source_row.organization_id,
        resolved_type,
        source_row.sender_user_id,
        source_row.conversation_id,
        source_row.id,
        JSONB_BUILD_OBJECT('message_type', source_row.message_type)
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_team_message_change ON conversation_messages;
DROP TRIGGER IF EXISTS trg_audit_team_message_insert_delete ON conversation_messages;
DROP TRIGGER IF EXISTS trg_audit_team_message_update ON conversation_messages;
CREATE TRIGGER trg_audit_team_message_insert_delete
AFTER INSERT OR DELETE ON conversation_messages
FOR EACH ROW
EXECUTE FUNCTION audit_team_message_change();
CREATE TRIGGER trg_audit_team_message_update
AFTER UPDATE OF edited_at, deleted_at ON conversation_messages
FOR EACH ROW
EXECUTE FUNCTION audit_team_message_change();

CREATE OR REPLACE FUNCTION audit_organization_member_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    source_organization_id BIGINT;
    source_user_id TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        source_organization_id := OLD.organization_id;
        source_user_id := OLD.user_id;
    ELSE
        source_organization_id := NEW.organization_id;
        source_user_id := NEW.user_id;
    END IF;

    INSERT INTO team_audit_events (
        organization_id, event_type, actor_user_id,
        target_user_id, metadata
    ) VALUES (
        source_organization_id,
        CASE
            WHEN TG_OP = 'INSERT' THEN 'organization.member.added'
            WHEN TG_OP = 'DELETE' THEN 'organization.member.deleted'
            ELSE 'organization.member.changed'
        END,
        CASE WHEN TG_OP = 'INSERT' THEN NEW.invited_by_user_id ELSE NULL END,
        source_user_id,
        JSONB_BUILD_OBJECT(
            'operation', TG_OP,
            'old_status', CASE WHEN TG_OP <> 'INSERT' THEN OLD.status ELSE NULL END,
            'new_status', CASE WHEN TG_OP <> 'DELETE' THEN NEW.status ELSE NULL END,
            'old_role', CASE WHEN TG_OP <> 'INSERT' THEN OLD.role ELSE NULL END,
            'new_role', CASE WHEN TG_OP <> 'DELETE' THEN NEW.role ELSE NULL END
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_organization_member_change ON organization_members;
DROP TRIGGER IF EXISTS trg_audit_organization_member_insert_delete ON organization_members;
DROP TRIGGER IF EXISTS trg_audit_organization_member_update ON organization_members;
CREATE TRIGGER trg_audit_organization_member_insert_delete
AFTER INSERT OR DELETE ON organization_members
FOR EACH ROW
EXECUTE FUNCTION audit_organization_member_change();
CREATE TRIGGER trg_audit_organization_member_update
AFTER UPDATE OF role, status ON organization_members
FOR EACH ROW
EXECUTE FUNCTION audit_organization_member_change();

CREATE OR REPLACE FUNCTION audit_conversation_member_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    resolved_organization_id BIGINT;
    resolved_conversation_id BIGINT;
    resolved_user_id TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        resolved_organization_id := OLD.organization_id;
        resolved_conversation_id := OLD.conversation_id;
        resolved_user_id := OLD.user_id;
    ELSE
        resolved_organization_id := NEW.organization_id;
        resolved_conversation_id := NEW.conversation_id;
        resolved_user_id := NEW.user_id;
    END IF;

    INSERT INTO team_audit_events (
        organization_id, event_type, target_user_id,
        conversation_id, metadata
    ) VALUES (
        resolved_organization_id,
        CASE
            WHEN TG_OP = 'INSERT' THEN 'conversation.member.added'
            WHEN TG_OP = 'DELETE' THEN 'conversation.member.deleted'
            ELSE 'conversation.member.changed'
        END,
        resolved_user_id,
        resolved_conversation_id,
        JSONB_BUILD_OBJECT(
            'operation', TG_OP,
            'old_status', CASE WHEN TG_OP <> 'INSERT' THEN OLD.status ELSE NULL END,
            'new_status', CASE WHEN TG_OP <> 'DELETE' THEN NEW.status ELSE NULL END,
            'old_role', CASE WHEN TG_OP <> 'INSERT' THEN OLD.role ELSE NULL END,
            'new_role', CASE WHEN TG_OP <> 'DELETE' THEN NEW.role ELSE NULL END
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_conversation_member_change ON conversation_members;
DROP TRIGGER IF EXISTS trg_audit_conversation_member_insert_delete ON conversation_members;
DROP TRIGGER IF EXISTS trg_audit_conversation_member_update ON conversation_members;
CREATE TRIGGER trg_audit_conversation_member_insert_delete
AFTER INSERT OR DELETE ON conversation_members
FOR EACH ROW
EXECUTE FUNCTION audit_conversation_member_change();
CREATE TRIGGER trg_audit_conversation_member_update
AFTER UPDATE OF role, status ON conversation_members
FOR EACH ROW
EXECUTE FUNCTION audit_conversation_member_change();

CREATE OR REPLACE FUNCTION audit_call_session_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    resolved_event_type TEXT;
    resolved_old_status TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        resolved_event_type := 'call.created';
        resolved_old_status := NULL;
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        resolved_event_type := 'call.status.changed';
        resolved_old_status := OLD.status;
    ELSE
        RETURN NULL;
    END IF;

    INSERT INTO team_audit_events (
        organization_id, event_type, actor_user_id,
        conversation_id, call_session_id, metadata
    ) VALUES (
        NEW.organization_id,
        resolved_event_type,
        NEW.created_by_user_id,
        NEW.conversation_id,
        NEW.id,
        JSONB_BUILD_OBJECT(
            'old_status', resolved_old_status,
            'new_status', NEW.status,
            'media_type', NEW.media_type
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_call_session_change ON call_sessions;
DROP TRIGGER IF EXISTS trg_audit_call_session_insert ON call_sessions;
DROP TRIGGER IF EXISTS trg_audit_call_session_update ON call_sessions;
CREATE TRIGGER trg_audit_call_session_insert
AFTER INSERT ON call_sessions
FOR EACH ROW
EXECUTE FUNCTION audit_call_session_change();
CREATE TRIGGER trg_audit_call_session_update
AFTER UPDATE OF status ON call_sessions
FOR EACH ROW
EXECUTE FUNCTION audit_call_session_change();

CREATE OR REPLACE FUNCTION audit_attachment_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    source_row conversation_message_attachments%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        source_row := OLD;
    ELSE
        source_row := NEW;
    END IF;
    INSERT INTO team_audit_events (
        organization_id, event_type, actor_user_id,
        conversation_id, message_id, attachment_id, metadata
    ) VALUES (
        source_row.organization_id,
        CASE WHEN TG_OP = 'DELETE'
             THEN 'attachment.retention.deleted'
             ELSE 'attachment.created'
        END,
        source_row.uploaded_by_user_id,
        source_row.conversation_id,
        source_row.message_id,
        source_row.id,
        JSONB_BUILD_OBJECT(
            'security_status', source_row.security_status,
            'malware_scan_status', source_row.malware_scan_status,
            'file_size_bytes', source_row.file_size_bytes,
            'encryption_key_id', source_row.encryption_key_id
        )
    );
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_audit_attachment_insert
    ON conversation_message_attachments;
CREATE TRIGGER trg_audit_attachment_insert
AFTER INSERT OR DELETE ON conversation_message_attachments
FOR EACH ROW
EXECUTE FUNCTION audit_attachment_change();

CREATE TABLE IF NOT EXISTS user_push_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth_secret TEXT NOT NULL,
    user_agent TEXT,
    locale TEXT NOT NULL DEFAULT 'en',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT user_push_subscription_status_check
        CHECK (status IN ('active', 'revoked', 'expired')),
    CONSTRAINT user_push_subscription_locale_check
        CHECK (locale IN ('en', 'fr'))
);
CREATE INDEX IF NOT EXISTS idx_user_push_subscriptions_user_status
    ON user_push_subscriptions (user_id, status);

CREATE TABLE IF NOT EXISTS team_notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,
    recipient_user_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    suppressed_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT team_notification_outbox_status_check
        CHECK (status IN ('pending', 'delivered', 'suppressed', 'dead')),
    CONSTRAINT team_notification_outbox_payload_check
        CHECK (JSONB_TYPEOF(payload) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_team_notification_outbox_pending
    ON team_notification_outbox (available_at, id)
    WHERE status = 'pending';

CREATE OR REPLACE FUNCTION enqueue_team_message_push_notifications()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
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

DROP TRIGGER IF EXISTS trg_enqueue_team_message_push_notifications
    ON conversation_messages;
CREATE TRIGGER trg_enqueue_team_message_push_notifications
AFTER INSERT ON conversation_messages
FOR EACH ROW
EXECUTE FUNCTION enqueue_team_message_push_notifications();

CREATE TABLE IF NOT EXISTS team_retention_runs (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT REFERENCES organizations(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    messages_deleted INTEGER NOT NULL DEFAULT 0,
    attachments_deleted INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running',
    error TEXT,
    CONSTRAINT team_retention_runs_status_check
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_team_retention_runs_one_running_org
    ON team_retention_runs (organization_id)
    WHERE status = 'running' AND organization_id IS NOT NULL;

CREATE OR REPLACE FUNCTION ensure_organization_communication_policy()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO organization_communication_policies (organization_id)
    VALUES (NEW.id)
    ON CONFLICT (organization_id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_ensure_organization_communication_policy
    ON organizations;
CREATE TRIGGER trg_ensure_organization_communication_policy
AFTER INSERT ON organizations
FOR EACH ROW
EXECUTE FUNCTION ensure_organization_communication_policy();

-- Seed a policy for existing organizations.
INSERT INTO organization_communication_policies (organization_id)
SELECT id FROM organizations
ON CONFLICT (organization_id) DO NOTHING;

COMMIT;
