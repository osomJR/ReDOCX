-- 015_allow_best_effort_secure_attachments.sql
--
-- Apply after 014_harden_call_lifecycle.sql.
--
-- Migration 013 is intentionally fail-closed and only permits secured rows
-- whose malware_scan_status is 'clean'. When TEAM_ATTACHMENT_SCAN_POLICY is
-- explicitly set to best_effort, the application records scanner
-- unavailability as malware_scan_status = 'failed'. This migration allows that
-- narrowly-scoped state only when structural validation, encryption, and
-- explicit best-effort audit metadata are all present.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.conversation_message_attachments') IS NULL THEN
        RAISE EXCEPTION
            'conversation_message_attachments is missing; apply migration 006 and migration 013 first';
    END IF;
END
$$;

ALTER TABLE public.conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_secured_state_check;

ALTER TABLE public.conversation_message_attachments
    DROP CONSTRAINT IF EXISTS conversation_message_attachments_scan_status_check;

-- Normalize rows created by an earlier provisional best-effort implementation.
UPDATE public.conversation_message_attachments
SET malware_scan_status = 'failed',
    security_metadata = JSONB_SET(
        COALESCE(security_metadata, '{}'::JSONB),
        '{malware_scan_status}',
        '"failed"'::JSONB,
        TRUE
    )
WHERE malware_scan_status = 'error';

ALTER TABLE public.conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_scan_status_check
        CHECK (
            malware_scan_status IN (
                'unscanned',
                'clean',
                'infected',
                'failed'
            )
        );

ALTER TABLE public.conversation_message_attachments
    ADD CONSTRAINT conversation_message_attachments_secured_state_check
        CHECK (
            security_status <> 'secured'
            OR (
                storage_backend = 'postgres_encrypted'
                AND malware_scan_status IN ('clean', 'failed')
                AND NULLIF(BTRIM(malware_scanner), '') IS NOT NULL
                AND scan_completed_at IS NOT NULL
                AND NULLIF(BTRIM(detected_content_type), '') IS NOT NULL
                AND NULLIF(BTRIM(validation_version), '') IS NOT NULL
                AND encryption_algorithm = 'AES-256-GCM'
                AND NULLIF(BTRIM(encryption_key_id), '') IS NOT NULL
                AND OCTET_LENGTH(encryption_nonce) = 12
                AND encryption_aad_version = 1
                AND OCTET_LENGTH(encrypted_content) > 16
                AND secured_at IS NOT NULL
                AND (
                    malware_scan_status = 'clean'
                    OR (
                        malware_scan_status = 'failed'
                        AND malware_scanner = 'unavailable'
                        AND security_metadata ->> 'scan_policy' = 'best_effort'
                        AND NULLIF(
                            BTRIM(security_metadata ->> 'scan_fallback_reason'),
                            ''
                        ) IS NOT NULL
                    )
                )
            )
        );

CREATE OR REPLACE FUNCTION public.enforce_secure_team_attachment_insert()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.storage_backend <> 'postgres_encrypted'
       OR NEW.security_status <> 'secured'
       OR NEW.encrypted_content IS NULL
       OR NOT (
            NEW.malware_scan_status = 'clean'
            OR (
                NEW.malware_scan_status = 'failed'
                AND NEW.malware_scanner = 'unavailable'
                AND NEW.security_metadata ->> 'scan_policy' = 'best_effort'
                AND NULLIF(
                    BTRIM(NEW.security_metadata ->> 'scan_fallback_reason'),
                    ''
                ) IS NOT NULL
            )
       ) THEN
        RAISE EXCEPTION 'insecure team attachment inserts are disabled'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_secure_team_attachment_insert
    ON public.conversation_message_attachments;

CREATE TRIGGER trg_enforce_secure_team_attachment_insert
BEFORE INSERT ON public.conversation_message_attachments
FOR EACH ROW
EXECUTE FUNCTION public.enforce_secure_team_attachment_insert();

COMMIT;

-- Verification:
-- SELECT conname, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conrelid = 'public.conversation_message_attachments'::regclass
-- ORDER BY conname;
