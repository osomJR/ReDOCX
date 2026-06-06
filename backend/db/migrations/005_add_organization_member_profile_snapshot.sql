-- Migration: add safe organization member profile snapshots
-- Purpose:
-- - Store display-safe team member name/email/picture separately from Auth0 user_id.
-- - Prevent Business/Enterprise team UIs from falling back to authentication IDs.
-- - Backfill pending invitation rows from invite:{email}; accepted users are backfilled
--   when they next load team data or accept an invitation.

ALTER TABLE organization_members
    ADD COLUMN IF NOT EXISTS member_name TEXT;

ALTER TABLE organization_members
    ADD COLUMN IF NOT EXISTS member_email TEXT;

ALTER TABLE organization_members
    ADD COLUMN IF NOT EXISTS member_picture TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'organization_members_member_name_not_blank_check'
    ) THEN
        ALTER TABLE organization_members
            ADD CONSTRAINT organization_members_member_name_not_blank_check
            CHECK (member_name IS NULL OR LENGTH(BTRIM(member_name)) > 0)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'organization_members_member_email_not_blank_check'
    ) THEN
        ALTER TABLE organization_members
            ADD CONSTRAINT organization_members_member_email_not_blank_check
            CHECK (member_email IS NULL OR LENGTH(BTRIM(member_email)) > 0)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'organization_members_member_picture_not_blank_check'
    ) THEN
        ALTER TABLE organization_members
            ADD CONSTRAINT organization_members_member_picture_not_blank_check
            CHECK (member_picture IS NULL OR LENGTH(BTRIM(member_picture)) > 0)
            NOT VALID;
    END IF;
END $$;

UPDATE organization_members
SET member_email = COALESCE(
        member_email,
        NULLIF(BTRIM(SUBSTRING(user_id FROM LENGTH('invite:') + 1)), '')
    ),
    member_name = COALESCE(
        member_name,
        NULLIF(
            SPLIT_PART(BTRIM(SUBSTRING(user_id FROM LENGTH('invite:') + 1)), '@', 1),
            ''
        )
    ),
    updated_at = NOW()
WHERE user_id LIKE 'invite:%'
  AND (
      member_email IS NULL
      OR member_name IS NULL
  );

CREATE INDEX IF NOT EXISTS idx_organization_members_member_email
    ON organization_members (LOWER(member_email))
    WHERE member_email IS NOT NULL;

ALTER TABLE organization_members
    VALIDATE CONSTRAINT organization_members_member_name_not_blank_check;

ALTER TABLE organization_members
    VALIDATE CONSTRAINT organization_members_member_email_not_blank_check;

ALTER TABLE organization_members
    VALIDATE CONSTRAINT organization_members_member_picture_not_blank_check;
