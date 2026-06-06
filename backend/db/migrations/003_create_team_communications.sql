-- 003_create_team_communications.sql
-- Team communications schema for Business and Enterprise organizations.
--
-- Creates persistent state for:
-- - direct messages
-- - admin-created group chats
-- - 1-to-1 calls
-- - group calls
-- - member presence
--
-- Business/Enterprise-only access should be enforced in backend routes using
-- get_user_entitlement(), because that depends on the authenticated user and
-- current subscription state.

CREATE TABLE IF NOT EXISTS organization_conversations (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    -- dm = one-to-one direct message
    -- group = admin/owner-created group chat
    type TEXT NOT NULL,

    -- Required by backend for group conversations.
    -- Optional for dm conversations.
    name TEXT,

    created_by_user_id TEXT NOT NULL,

    -- active = usable
    -- archived = hidden/closed but retained for audit/history
    status TEXT NOT NULL DEFAULT 'active',

    last_message_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT organization_conversations_type_check
        CHECK (type IN ('dm', 'group')),

    CONSTRAINT organization_conversations_status_check
        CHECK (status IN ('active', 'archived')),

    CONSTRAINT organization_conversations_group_name_check
        CHECK (type <> 'group' OR NULLIF(BTRIM(name), '') IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_organization_conversations_organization_id
    ON organization_conversations (organization_id);

CREATE INDEX IF NOT EXISTS idx_organization_conversations_org_status_updated
    ON organization_conversations (organization_id, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_organization_conversations_org_type
    ON organization_conversations (organization_id, type);


CREATE TABLE IF NOT EXISTS conversation_members (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES organization_conversations(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    user_id TEXT NOT NULL,

    -- conversation-level role
    role TEXT NOT NULL DEFAULT 'member',

    -- active = can read/send/join calls
    -- removed = retained for audit/history but no access
    status TEXT NOT NULL DEFAULT 'active',

    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    removed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT conversation_members_role_check
        CHECK (role IN ('owner', 'admin', 'member')),

    CONSTRAINT conversation_members_status_check
        CHECK (status IN ('active', 'removed')),

    CONSTRAINT conversation_members_unique_user
        UNIQUE (conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_members_conversation_id
    ON conversation_members (conversation_id);

CREATE INDEX IF NOT EXISTS idx_conversation_members_organization_user
    ON conversation_members (organization_id, user_id);

CREATE INDEX IF NOT EXISTS idx_conversation_members_user_status
    ON conversation_members (user_id, status);


CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES organization_conversations(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    sender_user_id TEXT NOT NULL,

    -- text = normal user message
    -- system = system/admin generated event
    -- call_event = call started/ended/missed/etc.
    message_type TEXT NOT NULL DEFAULT 'text',

    body TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    edited_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT conversation_messages_message_type_check
        CHECK (message_type IN ('text', 'system', 'call_event')),

    CONSTRAINT conversation_messages_body_check
        CHECK (NULLIF(BTRIM(body), '') IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_conversation_created
    ON conversation_messages (conversation_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_organization_created
    ON conversation_messages (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_conversation_messages_sender
    ON conversation_messages (sender_user_id);


CREATE TABLE IF NOT EXISTS call_sessions (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    conversation_id BIGINT REFERENCES organization_conversations(id) ON DELETE SET NULL,

    -- one_to_one = direct call
    -- group = group/conversation call
    type TEXT NOT NULL,

    -- ringing = created and inviting participants
    -- active = call is live
    -- ended = finished normally
    -- missed = no participant accepted
    -- cancelled = caller/admin cancelled
    status TEXT NOT NULL DEFAULT 'ringing',

    created_by_user_id TEXT NOT NULL,

    -- LiveKit room name or other provider room identifier.
    livekit_room_name TEXT NOT NULL,

    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT call_sessions_type_check
        CHECK (type IN ('one_to_one', 'group')),

    CONSTRAINT call_sessions_status_check
        CHECK (status IN ('ringing', 'active', 'ended', 'missed', 'cancelled')),

    CONSTRAINT call_sessions_livekit_room_name_unique
        UNIQUE (livekit_room_name)
);

CREATE INDEX IF NOT EXISTS idx_call_sessions_organization_created
    ON call_sessions (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_call_sessions_conversation_status
    ON call_sessions (conversation_id, status);

CREATE INDEX IF NOT EXISTS idx_call_sessions_status
    ON call_sessions (status);


CREATE TABLE IF NOT EXISTS call_participants (
    id BIGSERIAL PRIMARY KEY,
    call_session_id BIGINT NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,

    user_id TEXT NOT NULL,

    -- invited = invited/ringing
    -- joined = currently joined or has joined
    -- declined = user declined
    -- left = user joined then left
    -- missed = call ended before user joined
    status TEXT NOT NULL DEFAULT 'invited',

    invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    joined_at TIMESTAMPTZ,
    left_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT call_participants_status_check
        CHECK (status IN ('invited', 'joined', 'declined', 'left', 'missed')),

    CONSTRAINT call_participants_unique_user
        UNIQUE (call_session_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_call_participants_call_session_id
    ON call_participants (call_session_id);

CREATE INDEX IF NOT EXISTS idx_call_participants_organization_user
    ON call_participants (organization_id, user_id);

CREATE INDEX IF NOT EXISTS idx_call_participants_user_status
    ON call_participants (user_id, status);


CREATE TABLE IF NOT EXISTS member_presence (
    organization_id BIGINT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,

    -- online = connected to app/team page
    -- offline = no recent heartbeat
    -- in_call = actively in a call
    status TEXT NOT NULL DEFAULT 'offline',

    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (organization_id, user_id),

    CONSTRAINT member_presence_status_check
        CHECK (status IN ('online', 'offline', 'in_call'))
);

CREATE INDEX IF NOT EXISTS idx_member_presence_organization_status
    ON member_presence (organization_id, status);

CREATE INDEX IF NOT EXISTS idx_member_presence_user
    ON member_presence (user_id);


CREATE OR REPLACE FUNCTION set_team_communications_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trg_organization_conversations_updated_at
    ON organization_conversations;

CREATE TRIGGER trg_organization_conversations_updated_at
BEFORE UPDATE ON organization_conversations
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


DROP TRIGGER IF EXISTS trg_conversation_members_updated_at
    ON conversation_members;

CREATE TRIGGER trg_conversation_members_updated_at
BEFORE UPDATE ON conversation_members
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


DROP TRIGGER IF EXISTS trg_conversation_messages_updated_at
    ON conversation_messages;

CREATE TRIGGER trg_conversation_messages_updated_at
BEFORE UPDATE ON conversation_messages
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


DROP TRIGGER IF EXISTS trg_call_sessions_updated_at
    ON call_sessions;

CREATE TRIGGER trg_call_sessions_updated_at
BEFORE UPDATE ON call_sessions
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


DROP TRIGGER IF EXISTS trg_call_participants_updated_at
    ON call_participants;

CREATE TRIGGER trg_call_participants_updated_at
BEFORE UPDATE ON call_participants
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


DROP TRIGGER IF EXISTS trg_member_presence_updated_at
    ON member_presence;

CREATE TRIGGER trg_member_presence_updated_at
BEFORE UPDATE ON member_presence
FOR EACH ROW
EXECUTE FUNCTION set_team_communications_updated_at();


CREATE OR REPLACE FUNCTION set_conversation_last_message_at()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE organization_conversations
    SET last_message_at = NEW.created_at,
        updated_at = NOW()
    WHERE id = NEW.conversation_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trg_conversation_messages_last_message_at
    ON conversation_messages;

CREATE TRIGGER trg_conversation_messages_last_message_at
AFTER INSERT ON conversation_messages
FOR EACH ROW
EXECUTE FUNCTION set_conversation_last_message_at();
