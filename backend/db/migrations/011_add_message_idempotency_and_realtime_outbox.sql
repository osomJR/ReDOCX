-- 011_add_message_idempotency_and_realtime_outbox.sql
-- Makes message acceptance retry-safe and records committed realtime events in
-- the same PostgreSQL transaction as their source message.

BEGIN;

ALTER TABLE conversation_messages
    ADD COLUMN IF NOT EXISTS client_message_id VARCHAR(160);

-- Preserve one historical client key where older application versions stored
-- it only in JSON metadata. If duplicates already exist, retain every message
-- but bind the idempotency key to the earliest one.
WITH candidate_messages AS (
    SELECT
        id,
        conversation_id,
        sender_user_id,
        NULLIF(BTRIM(metadata ->> 'client_message_id'), '') AS candidate_key
    FROM conversation_messages
    WHERE client_message_id IS NULL
      AND NULLIF(BTRIM(metadata ->> 'client_message_id'), '') IS NOT NULL
      AND LENGTH(NULLIF(BTRIM(metadata ->> 'client_message_id'), '')) <= 160
),
ranked_candidates AS (
    SELECT
        id,
        conversation_id,
        sender_user_id,
        candidate_key,
        ROW_NUMBER() OVER (
            PARTITION BY conversation_id, sender_user_id, candidate_key
            ORDER BY id ASC
        ) AS key_rank
    FROM candidate_messages
)
UPDATE conversation_messages AS message
SET client_message_id = candidate.candidate_key
FROM ranked_candidates AS candidate
WHERE message.id = candidate.id
  AND candidate.key_rank = 1
  AND NOT EXISTS (
      SELECT 1
      FROM conversation_messages AS existing
      WHERE existing.conversation_id = candidate.conversation_id
        AND existing.sender_user_id = candidate.sender_user_id
        AND existing.client_message_id = candidate.candidate_key
  );

CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_messages_client_message
    ON conversation_messages (
        conversation_id,
        sender_user_id,
        client_message_id
    )
    WHERE client_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_client_message_lookup
    ON conversation_messages (conversation_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS team_realtime_outbox (
    id BIGSERIAL PRIMARY KEY,
    organization_id BIGINT NOT NULL
        REFERENCES organizations(id) ON DELETE CASCADE,

    aggregate_type VARCHAR(50) NOT NULL,
    aggregate_id TEXT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    event_key TEXT NOT NULL,

    recipient_user_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    payload JSONB NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    published_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT team_realtime_outbox_event_key_unique UNIQUE (event_key),
    CONSTRAINT team_realtime_outbox_status_check
        CHECK (status IN ('pending', 'published', 'dead_letter')),
    CONSTRAINT team_realtime_outbox_attempts_check CHECK (attempts >= 0),
    CONSTRAINT team_realtime_outbox_payload_object_check
        CHECK (jsonb_typeof(payload) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_pending
    ON team_realtime_outbox (available_at ASC, id ASC)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_organization_created
    ON team_realtime_outbox (organization_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_published_retention
    ON team_realtime_outbox (published_at ASC, id ASC)
    WHERE status = 'published';

COMMIT;