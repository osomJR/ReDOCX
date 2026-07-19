-- 012_add_shared_realtime_and_access_revocation.sql
-- Adds durable delivery routing and auditable membership/media revocation.
-- Apply after 011_add_message_idempotency_and_realtime_outbox.sql.

BEGIN;

ALTER TABLE team_realtime_outbox
    ADD COLUMN IF NOT EXISTS delivery_scope VARCHAR(40)
        NOT NULL DEFAULT 'organization_users';

ALTER TABLE team_realtime_outbox
    ADD COLUMN IF NOT EXISTS exclude_user_ids TEXT[]
        NOT NULL DEFAULT ARRAY[]::TEXT[];

ALTER TABLE team_realtime_outbox
    ADD COLUMN IF NOT EXISTS media_room_names TEXT[]
        NOT NULL DEFAULT ARRAY[]::TEXT[];

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
                'organization_revoke'
            )
        );

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS revoked_by_user_id TEXT;

ALTER TABLE call_participants
    ADD COLUMN IF NOT EXISTS revocation_reason TEXT;

ALTER TABLE call_participants
    DROP CONSTRAINT IF EXISTS call_participants_status_check;

ALTER TABLE call_participants
    ADD CONSTRAINT call_participants_status_check
        CHECK (
            status IN (
                'invited',
                'joined',
                'declined',
                'left',
                'missed',
                'removed'
            )
        );

ALTER TABLE call_participants
    DROP CONSTRAINT IF EXISTS call_participants_revocation_consistency_check;

ALTER TABLE call_participants
    ADD CONSTRAINT call_participants_revocation_consistency_check
        CHECK (
            status <> 'removed'
            OR (
                status = 'removed'
                AND revoked_at IS NOT NULL
                AND NULLIF(BTRIM(revoked_by_user_id), '') IS NOT NULL
                AND NULLIF(BTRIM(revocation_reason), '') IS NOT NULL
            )
        );

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_pending_lease
    ON team_realtime_outbox (available_at ASC, locked_at ASC, id ASC)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_team_realtime_outbox_revocation_pending
    ON team_realtime_outbox (organization_id, available_at ASC, id ASC)
    WHERE status = 'pending'
      AND delivery_scope = 'organization_revoke';

CREATE INDEX IF NOT EXISTS idx_call_participants_active_org_user
    ON call_participants (organization_id, user_id, call_session_id)
    WHERE status IN ('invited', 'joined');

CREATE INDEX IF NOT EXISTS idx_call_participants_revoked_at
    ON call_participants (revoked_at DESC, id DESC)
    WHERE status = 'removed';

COMMIT;
