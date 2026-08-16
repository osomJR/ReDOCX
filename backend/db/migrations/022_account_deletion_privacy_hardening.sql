-- 022_account_deletion_privacy_hardening.sql
-- Production hardening for durable account-deletion sagas and irreversible
-- pseudonymization of retained audit history.
-- Apply after 016_team_enterprise_hardening.sql and
-- 019_billing_reliability_hardening.sql.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE INDEX IF NOT EXISTS idx_account_lifecycle_deactivation_requested
    ON account_lifecycle (updated_at ASC)
    WHERE status = 'deactivation_requested';

CREATE OR REPLACE FUNCTION account_subject_tombstone_id(raw_subject TEXT)
RETURNS TEXT
LANGUAGE SQL
IMMUTABLE
STRICT
AS $$
    SELECT 'deleted:' || SUBSTRING(
        ENCODE(DIGEST(CONVERT_TO(BTRIM(raw_subject), 'UTF8'), 'sha256'), 'hex')
        FROM 1 FOR 32
    );
$$;

DO $$
BEGIN
    IF TO_REGCLASS('team_audit_events') IS NULL THEN
        RAISE EXCEPTION
            '022_account_deletion_privacy_hardening.sql requires team_audit_events from migration 016';
    END IF;
END
$$;

-- Keep the audit ledger immutable for normal application writes. The only
-- permitted UPDATE is a deterministic privacy rewrite: actor/target subjects
-- may become their irreversible tombstone IDs, and previous_hash/event_hash
-- may change only to the mathematically correct resealed values.
CREATE OR REPLACE FUNCTION deny_team_audit_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    prior BYTEA;
    canonical TEXT;
    expected_hash BYTEA;
    actor_change_allowed BOOLEAN;
    target_change_allowed BOOLEAN;
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'team audit events are immutable'
            USING ERRCODE = '55000';
    END IF;

    actor_change_allowed :=
        NEW.actor_user_id IS NOT DISTINCT FROM OLD.actor_user_id
        OR (
            OLD.actor_user_id IS NOT NULL
            AND NEW.actor_user_id = account_subject_tombstone_id(OLD.actor_user_id)
        );
    target_change_allowed :=
        NEW.target_user_id IS NOT DISTINCT FROM OLD.target_user_id
        OR (
            OLD.target_user_id IS NOT NULL
            AND NEW.target_user_id = account_subject_tombstone_id(OLD.target_user_id)
        );

    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
       OR NEW.event_type IS DISTINCT FROM OLD.event_type
       OR NOT actor_change_allowed
       OR NOT target_change_allowed
       OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
       OR NEW.message_id IS DISTINCT FROM OLD.message_id
       OR NEW.attachment_id IS DISTINCT FROM OLD.attachment_id
       OR NEW.call_session_id IS DISTINCT FROM OLD.call_session_id
       OR NEW.request_id IS DISTINCT FROM OLD.request_id
       OR NEW.metadata IS DISTINCT FROM OLD.metadata
       OR NEW.occurred_at IS DISTINCT FROM OLD.occurred_at THEN
        RAISE EXCEPTION 'team audit events are immutable'
            USING ERRCODE = '55000';
    END IF;

    SELECT event_hash
    INTO prior
    FROM team_audit_events
    WHERE organization_id = NEW.organization_id
      AND id < NEW.id
    ORDER BY id DESC
    LIMIT 1;

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
    expected_hash := DIGEST(CONVERT_TO(canonical, 'UTF8'), 'sha256');

    IF NEW.previous_hash IS DISTINCT FROM prior
       OR NEW.event_hash IS DISTINCT FROM expected_hash THEN
        RAISE EXCEPTION 'team audit event privacy rewrite has an invalid hash chain'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION pseudonymize_team_audit_subject(p_user_id TEXT)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    resolved_user_id TEXT := BTRIM(COALESCE(p_user_id, ''));
    tombstone_id TEXT;
    organization_row RECORD;
    audit_row RECORD;
    prior BYTEA;
    new_actor_user_id TEXT;
    new_target_user_id TEXT;
    canonical TEXT;
    new_hash BYTEA;
    changed_count INTEGER := 0;
BEGIN
    IF resolved_user_id = '' THEN
        RAISE EXCEPTION 'p_user_id is required'
            USING ERRCODE = '22023';
    END IF;

    tombstone_id := account_subject_tombstone_id(resolved_user_id);

    FOR organization_row IN
        SELECT DISTINCT organization_id
        FROM team_audit_events
        WHERE actor_user_id = resolved_user_id
           OR target_user_id = resolved_user_id
        ORDER BY organization_id
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended('team_audit:' || organization_row.organization_id::TEXT, 0)
        );

        prior := NULL;
        FOR audit_row IN
            SELECT *
            FROM team_audit_events
            WHERE organization_id = organization_row.organization_id
            ORDER BY id ASC
            FOR UPDATE
        LOOP
            new_actor_user_id := CASE
                WHEN audit_row.actor_user_id = resolved_user_id THEN tombstone_id
                ELSE audit_row.actor_user_id
            END;
            new_target_user_id := CASE
                WHEN audit_row.target_user_id = resolved_user_id THEN tombstone_id
                ELSE audit_row.target_user_id
            END;

            IF new_actor_user_id IS DISTINCT FROM audit_row.actor_user_id
               OR new_target_user_id IS DISTINCT FROM audit_row.target_user_id THEN
                changed_count := changed_count + 1;
            END IF;

            canonical := CONCAT_WS(
                '|',
                audit_row.organization_id::TEXT,
                audit_row.event_type,
                COALESCE(new_actor_user_id, ''),
                COALESCE(new_target_user_id, ''),
                COALESCE(audit_row.conversation_id::TEXT, ''),
                COALESCE(audit_row.message_id::TEXT, ''),
                COALESCE(audit_row.attachment_id::TEXT, ''),
                COALESCE(audit_row.call_session_id::TEXT, ''),
                COALESCE(audit_row.request_id, ''),
                audit_row.occurred_at::TEXT,
                audit_row.metadata::TEXT,
                COALESCE(ENCODE(prior, 'hex'), '')
            );
            new_hash := DIGEST(CONVERT_TO(canonical, 'UTF8'), 'sha256');

            UPDATE team_audit_events
            SET actor_user_id = new_actor_user_id,
                target_user_id = new_target_user_id,
                previous_hash = prior,
                event_hash = new_hash
            WHERE id = audit_row.id;

            prior := new_hash;
        END LOOP;
    END LOOP;

    RETURN changed_count;
END;
$$;

COMMIT;
