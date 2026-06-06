-- 004_set_default_plan_seat_limits.sql
-- Normalize organization subscription seat limits to match product defaults.
--
-- Product rules:
-- - Business includes up to 19 users automatically.
-- - Enterprise starts at 20 users and can be configured higher.
--
-- Do not edit already-run migrations in production. Run this after
-- 002_create_organizations_and_team_subscriptions.sql.

UPDATE organization_subscriptions
SET max_accounts = 19,
    updated_at = NOW()
WHERE plan = 'business'
  AND max_accounts <> 19;

UPDATE organization_subscriptions
SET max_accounts = 20,
    updated_at = NOW()
WHERE plan = 'enterprise'
  AND max_accounts < 20;

-- Verification query:
-- SELECT organization_id, plan, max_accounts, status
-- FROM organization_subscriptions
-- ORDER BY organization_id;
