from __future__ import annotations

"""Runtime contract checks for the billing database schema.

The payment provider must never be asked to charge a customer when the deployed
application and PostgreSQL billing schema disagree. This module validates the
minimum schema required by checkout creation and verified entitlement activation.
"""

from dataclasses import dataclass
import logging
import threading
import time
from typing import Iterable

from backend.database import get_db

logger = logging.getLogger(__name__)

_SCHEMA_OK_TTL_SECONDS = 60.0
_schema_lock = threading.Lock()
_schema_ok_until = 0.0


class BillingSchemaError(RuntimeError):
    """Raised when the database cannot safely process billing mutations."""


@dataclass(frozen=True)
class BillingSchemaCheckResult:
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "billing_checkout_sessions": {
        "id",
        "provider",
        "provider_session_id",
        "provider_reference",
        "user_id",
        "email",
        "target_plan",
        "current_plan",
        "organization_id",
        "organization_name",
        "checkout_url",
        "status",
        "provider_customer_id",
        "provider_subscription_id",
        "metadata",
        "raw_response",
        "operation",
        "idempotency_key",
        "request_fingerprint",
        "replaced_provider_subscription_id",
        "created_at",
        "updated_at",
    },
    "billing_provider_events": {
        "id",
        "provider",
        "provider_event_id",
        "event_type",
        "action",
        "event_status",
        "user_id",
        "target_plan",
        "organization_id",
        "provider_customer_id",
        "provider_subscription_id",
        "provider_reference",
        "payload",
        "processing_status",
        "processing_message",
        "processed_at",
        "created_at",
        "updated_at",
    },
    "organizations": {
        "id",
        "name",
        "owner_user_id",
        "created_at",
        "updated_at",
    },
    "organization_members": {
        "organization_id",
        "user_id",
        "role",
        "status",
        "joined_at",
        "created_at",
        "updated_at",
    },
    "organization_subscriptions": {
        "organization_id",
        "plan",
        "max_accounts",
        "status",
        "provider",
        "provider_customer_id",
        "provider_subscription_id",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "pending_plan",
        "plan_change_effective_at",
        "grace_period_end",
        "payment_failure_count",
        "access_revoked_at",
        "access_revocation_reason",
        "updated_at",
    },
    "user_subscriptions": {
        "user_id",
        "plan",
        "account_count",
        "status",
        "provider",
        "provider_customer_id",
        "provider_subscription_id",
        "current_period_start",
        "current_period_end",
        "cancel_at_period_end",
        "pending_plan",
        "plan_change_effective_at",
        "grace_period_end",
        "payment_failure_count",
        "access_revoked_at",
        "access_revocation_reason",
        "updated_at",
    },
}


def _normalized_sql(value: str | None) -> str:
    return " ".join(str(value or "").lower().split())


def _constraint_definitions(conn, table_name: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.conname, pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = current_schema()
              AND t.relname = %s
            """,
            (table_name,),
        )
        return {str(row[0]): str(row[1]) for row in cur.fetchall()}


def _required_tokens_present(definition: str, tokens: Iterable[str]) -> bool:
    normalized = _normalized_sql(definition)
    return all(token.lower() in normalized for token in tokens)


def inspect_billing_schema() -> BillingSchemaCheckResult:
    errors: list[str] = []

    with get_db() as conn:
        existing_tables: set[str] = set()
        for table_name in _REQUIRED_COLUMNS:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT to_regclass(current_schema() || '.' || %s)",
                    (table_name,),
                )
                row = cur.fetchone()
            if row and row[0] is not None:
                existing_tables.add(table_name)
            else:
                errors.append(f"missing table: {table_name}")

        for table_name in sorted(existing_tables):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = %s
                    """,
                    (table_name,),
                )
                columns = {str(row[0]) for row in cur.fetchall()}
            missing = sorted(_REQUIRED_COLUMNS[table_name] - columns)
            if missing:
                errors.append(
                    f"{table_name} missing column(s): {', '.join(missing)}"
                )

        if "organization_subscriptions" in existing_tables:
            constraints = _constraint_definitions(conn, "organization_subscriptions")
            enterprise_def = constraints.get(
                "organization_subscriptions_enterprise_max_accounts_check", ""
            )
            business_def = constraints.get(
                "organization_subscriptions_business_max_accounts_check", ""
            )
            general_def = constraints.get(
                "organization_subscriptions_max_accounts_check", ""
            )

            if not _required_tokens_present(
                enterprise_def, ("enterprise", "max_accounts >= 1")
            ):
                errors.append(
                    "organization_subscriptions Enterprise seat constraint is stale; "
                    "the deployed product contract requires Enterprise >= 1 seat"
                )
            normalized_business_def = _normalized_sql(business_def)
            business_range_ok = (
                "business" in normalized_business_def
                and (
                    "between 1 and 19" in normalized_business_def
                    or (
                        "max_accounts >= 1" in normalized_business_def
                        and "max_accounts <= 19" in normalized_business_def
                    )
                )
            )
            if not business_range_ok:
                errors.append(
                    "organization_subscriptions Business seat constraint is stale; "
                    "the deployed product contract requires Business 1..19 seats"
                )
            if not _required_tokens_present(general_def, ("max_accounts >= 1",)):
                errors.append(
                    "organization_subscriptions max_accounts constraint must allow 1 seat"
                )

        if "billing_provider_events" in existing_tables:
            constraints = _constraint_definitions(conn, "billing_provider_events")
            action_def = constraints.get("billing_provider_events_action_check", "")
            for action in ("activate", "cancel", "past_due", "suspend", "revoke", "restore", "ignore"):
                if action not in _normalized_sql(action_def):
                    errors.append(
                        "billing_provider_events action constraint is stale; "
                        f"missing action '{action}'"
                    )
                    break

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT indexdef
                    FROM pg_indexes
                    WHERE schemaname = current_schema()
                      AND tablename = 'billing_provider_events'
                    """
                )
                index_defs = [_normalized_sql(row[0]) for row in cur.fetchall()]
            has_unique_event_key = any(
                "unique" in index_def
                and "(provider, provider_event_id)" in index_def
                for index_def in index_defs
            )
            if not has_unique_event_key:
                errors.append(
                    "billing_provider_events requires a unique (provider, provider_event_id) key"
                )

    return BillingSchemaCheckResult(errors=tuple(errors))


def assert_billing_schema_ready(*, force: bool = False) -> None:
    """Fail closed when billing mutations are unsafe.

    A successful result is cached briefly to avoid adding a schema-introspection
    round trip to every checkout request. Failures are never cached so a newly
    applied migration can recover immediately without restarting the process.
    """

    global _schema_ok_until

    now = time.monotonic()
    if not force and now < _schema_ok_until:
        return

    with _schema_lock:
        now = time.monotonic()
        if not force and now < _schema_ok_until:
            return

        result = inspect_billing_schema()
        if not result.ok:
            _schema_ok_until = 0.0
            raise BillingSchemaError(
                "Billing database schema is not ready: " + "; ".join(result.errors)
            )

        _schema_ok_until = time.monotonic() + _SCHEMA_OK_TTL_SECONDS
        logger.info("Billing database schema contract verified.")


__all__ = [
    "BillingSchemaCheckResult",
    "BillingSchemaError",
    "assert_billing_schema_ready",
    "inspect_billing_schema",
]
