-- 023_flexible_organization_seat_billing.sql
-- Allow Business and Enterprise subscriptions to persist the exact number of
-- seats purchased at checkout.
--
-- Product rules after this migration:
-- - Business: 1 to 19 purchased seats.
-- - Enterprise: 1 or more purchased seats.
-- - The organization owner consumes one purchased seat.
-- - Existing subscriptions retain their current max_accounts value.

ALTER TABLE organization_subscriptions
    DROP CONSTRAINT IF EXISTS organization_subscriptions_max_accounts_check,
    DROP CONSTRAINT IF EXISTS organization_subscriptions_business_max_accounts_check,
    DROP CONSTRAINT IF EXISTS organization_subscriptions_enterprise_max_accounts_check;

ALTER TABLE organization_subscriptions
    ADD CONSTRAINT organization_subscriptions_max_accounts_check
        CHECK (max_accounts >= 1),
    ADD CONSTRAINT organization_subscriptions_business_max_accounts_check
        CHECK (plan <> 'business' OR max_accounts BETWEEN 1 AND 19),
    ADD CONSTRAINT organization_subscriptions_enterprise_max_accounts_check
        CHECK (plan <> 'enterprise' OR max_accounts >= 1);
