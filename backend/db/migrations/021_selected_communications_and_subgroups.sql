-- 021_selected_communications_and_subgroups.sql
-- Distinguish the single organization-wide group from member-created
-- subgroups and enforce plan-specific subgroup membership limits.

BEGIN;

ALTER TABLE organization_conversations
    ADD COLUMN IF NOT EXISTS group_scope TEXT;

-- Every group created before this migration is the legacy organization-wide
-- group because subset groups were not previously supported.
UPDATE organization_conversations
SET group_scope = 'organization'
WHERE type = 'group' AND group_scope IS NULL;

UPDATE organization_conversations
SET group_scope = NULL
WHERE type = 'dm' AND group_scope IS NOT NULL;

ALTER TABLE organization_conversations
    DROP CONSTRAINT IF EXISTS organization_conversations_group_scope_check;

ALTER TABLE organization_conversations
    ADD CONSTRAINT organization_conversations_group_scope_check
    CHECK (
        (type = 'dm' AND group_scope IS NULL)
        OR
        (
            type = 'group'
            AND group_scope IS NOT NULL
            AND group_scope IN ('organization', 'subgroup')
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS ux_organization_conversations_wide_group
    ON organization_conversations (organization_id)
    WHERE type = 'group'
      AND group_scope = 'organization'
      AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_organization_conversations_subgroups
    ON organization_conversations (organization_id, updated_at DESC, id DESC)
    WHERE type = 'group'
      AND group_scope = 'subgroup'
      AND status = 'active';

CREATE OR REPLACE FUNCTION enforce_team_subgroup_member_limit()
RETURNS TRIGGER AS $$
DECLARE
    target_conversation_id BIGINT;
    target_organization_id BIGINT;
    target_type TEXT;
    target_scope TEXT;
    subscription_plan TEXT;
    subscription_status TEXT;
    active_member_count INTEGER;
    allowed_member_count INTEGER;
BEGIN
    target_conversation_id := NEW.conversation_id;

    SELECT oc.organization_id, oc.type, oc.group_scope
    INTO target_organization_id, target_type, target_scope
    FROM organization_conversations oc
    WHERE oc.id = target_conversation_id;

    IF target_type <> 'group' OR target_scope <> 'subgroup' THEN
        RETURN NEW;
    END IF;

    SELECT os.plan, os.status
    INTO subscription_plan, subscription_status
    FROM organization_subscriptions os
    WHERE os.organization_id = target_organization_id
    ORDER BY os.updated_at DESC, os.id DESC
    LIMIT 1;

    IF subscription_status <> 'active'
       OR subscription_plan NOT IN ('business', 'enterprise') THEN
        RAISE EXCEPTION
            'An active Business or Enterprise subscription is required for subgroup membership.'
            USING ERRCODE = 'check_violation';
    END IF;

    allowed_member_count := CASE subscription_plan
        WHEN 'business' THEN 18
        WHEN 'enterprise' THEN 30
    END;

    SELECT COUNT(*)
    INTO active_member_count
    FROM conversation_members cm
    WHERE cm.conversation_id = target_conversation_id
      AND cm.status = 'active';

    IF active_member_count > allowed_member_count THEN
        RAISE EXCEPTION
            'Subgroup member count (%) exceeds the % plan limit (%) for conversation_id %',
            active_member_count,
            subscription_plan,
            allowed_member_count,
            target_conversation_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_conversation_members_subgroup_limit
    ON conversation_members;

CREATE CONSTRAINT TRIGGER trg_conversation_members_subgroup_limit
AFTER INSERT OR UPDATE OF conversation_id, status
ON conversation_members
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION enforce_team_subgroup_member_limit();

CREATE OR REPLACE FUNCTION enforce_team_subgroup_plan_change()
RETURNS TRIGGER AS $$
DECLARE
    allowed_member_count INTEGER;
    oversized_conversation_id BIGINT;
BEGIN
    IF NEW.status <> 'active' OR NEW.plan NOT IN ('business', 'enterprise') THEN
        RETURN NEW;
    END IF;

    allowed_member_count := CASE NEW.plan
        WHEN 'business' THEN 18
        WHEN 'enterprise' THEN 30
    END;

    SELECT oc.id
    INTO oversized_conversation_id
    FROM organization_conversations oc
    JOIN conversation_members cm
      ON cm.conversation_id = oc.id
     AND cm.status = 'active'
    WHERE oc.organization_id = NEW.organization_id
      AND oc.type = 'group'
      AND oc.group_scope = 'subgroup'
      AND oc.status = 'active'
    GROUP BY oc.id
    HAVING COUNT(*) > allowed_member_count
    ORDER BY oc.id
    LIMIT 1;

    IF oversized_conversation_id IS NOT NULL THEN
        RAISE EXCEPTION
            'Plan % permits at most % active members in subgroup conversation_id %',
            NEW.plan,
            allowed_member_count,
            oversized_conversation_id
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_organization_subscriptions_subgroup_plan_limit
    ON organization_subscriptions;

CREATE CONSTRAINT TRIGGER trg_organization_subscriptions_subgroup_plan_limit
AFTER INSERT OR UPDATE OF plan, status
ON organization_subscriptions
DEFERRABLE INITIALLY IMMEDIATE
FOR EACH ROW
EXECUTE FUNCTION enforce_team_subgroup_plan_change();

COMMIT;
