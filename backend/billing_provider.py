from __future__ import annotations

"""
Provider-agnostic billing integration layer for ReDOCX.

This module deliberately separates checkout/webhook mechanics from entitlement
activation. It can start checkout sessions and verify provider webhook payloads,
but it never grants paid access by itself. Entitlement activation belongs in the
webhook route after a verified event has been recorded idempotently.

Supported provider adapters:
- static: static checkout URLs from environment variables
- generic: generic checkout URL + HMAC-signed webhook payloads
- stripe: Stripe Checkout + Stripe-Signature webhook verification
- paystack: Paystack transaction initialization + x-paystack-signature verification
- flutterwave: Flutterwave payment link initialization + verif-hash verification

Adding another gateway usually means adding one small adapter that implements
create_checkout_session(...) and verify_webhook(...). The rest of the app keeps
using the same normalized CheckoutSession and BillingWebhookEvent objects.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
import logging

logger = logging.getLogger(__name__)

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
ProviderName = Literal["static", "generic", "stripe", "paystack", "flutterwave"]
WebhookAction = Literal[
    "activate",
    "cancel",
    "past_due",
    "suspend",
    "revoke",
    "restore",
    "ignore",
]

PAID_PLANS: set[str] = {"personal", "business", "enterprise"}
ORGANIZATION_PLANS: set[str] = {"business", "enterprise"}

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("BILLING_PROVIDER_TIMEOUT_SECONDS", "15"))
DEFAULT_CURRENCY = os.getenv("BILLING_DEFAULT_CURRENCY", "NGN").strip().upper() or "NGN"
DEFAULT_SUCCESS_URL = os.getenv("BILLING_SUCCESS_URL", "").strip()
DEFAULT_CANCEL_URL = os.getenv("BILLING_CANCEL_URL", "").strip()

# Canonical recurring unit prices. Provider configuration is validated against
# these values so browser input can never choose or alter the amount charged.
PLAN_UNIT_AMOUNT_KOBO: dict[str, int] = {
    "personal": 10_000,
    "business": 10_000,
    "enterprise": 10_000,
}


def plan_unit_amount_kobo(plan: str) -> int:
    normalized = str(plan or "").strip().lower()
    amount = PLAN_UNIT_AMOUNT_KOBO.get(normalized)
    if amount is None:
        raise ValueError(f"No paid unit price is configured for plan: {plan}.")
    return amount


def validate_checkout_seat_count(plan: str, seat_count: int | None) -> int:
    normalized = str(plan or "").strip().lower()
    resolved = 1 if seat_count is None else seat_count
    if not isinstance(resolved, int) or isinstance(resolved, bool) or resolved < 1:
        raise ValueError("seat_count must be an integer greater than or equal to 1.")
    if normalized == "business" and resolved > 19:
        raise ValueError("Business supports at most 19 seats.")
    if normalized not in PAID_PLANS:
        raise ValueError(f"Seat pricing is not supported for plan: {plan}.")
    if normalized == "personal" and resolved != 1:
        raise ValueError("Personal supports exactly one seat.")
    return resolved


def expected_checkout_amount_kobo(plan: str, seat_count: int | None = 1) -> int:
    quantity = validate_checkout_seat_count(plan, seat_count)
    return plan_unit_amount_kobo(plan) * quantity


class BillingProviderError(RuntimeError):
    """Raised when the billing provider cannot complete a requested action."""


class PaystackTransactionNotFoundError(BillingProviderError):
    """Paystack cannot find a historical transaction on the current integration."""


class PaystackCustomerNotFoundError(BillingProviderError):
    """Paystack cannot find a stored customer on the current integration."""


class PaystackLegacyBindingUnresolvableError(BillingProviderError):
    """A legacy ReDOCX Paystack binding cannot be verified on this integration.

    This is a data/integration anomaly, not proof that no recurring subscription
    exists. Callers must preserve active entitlements and fail closed for account
    deletion until the binding is resolved or an already-purged terminal record
    is explicitly retired.
    """


class CheckoutNotConfiguredError(BillingProviderError):
    """Raised when a checkout session cannot be created because config is missing."""


class WebhookVerificationError(BillingProviderError):
    """Raised when a webhook cannot be verified as authentic."""


@dataclass(frozen=True)
class BillingCheckoutRequest:
    user_id: str
    email: str | None
    target_plan: BillingPlanName
    current_plan: BillingPlanName | None = None
    organization_id: int | None = None
    organization_name: str | None = None
    seat_count: int = 1
    success_url: str | None = None
    cancel_url: str | None = None
    idempotency_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingCheckoutSession:
    provider: str
    target_plan: BillingPlanName
    checkout_url: str
    provider_session_id: str | None = None
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    reference: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingWebhookEvent:
    provider: str
    event_id: str
    event_type: str
    action: WebhookAction
    plan: BillingPlanName | None = None
    status: str | None = None
    user_id: str | None = None
    email: str | None = None
    organization_id: int | None = None
    organization_name: str | None = None
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    provider_reference: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    max_accounts: int | None = None
    amount: int | float | None = None
    currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingSubscriptionChange:
    provider: str
    provider_subscription_id: str
    status: str
    current_period_end: datetime | None = None
    effective_at: datetime | None = None
    target_plan: BillingPlanName | None = None
    cancel_at_period_end: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingSubscriptionState:
    provider: str
    provider_subscription_id: str
    status: str
    provider_customer_id: str | None = None
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    plan: BillingPlanName | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingHandoffAuthorization:
    provider: str
    status: Literal["authorization_pending", "scheduled"]
    authorization_url: str | None = None
    provider_reference: str | None = None
    provider_customer_id: str | None = None
    provider_subscription_id: str | None = None
    start_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BillingFutureSubscription:
    provider: str
    provider_subscription_id: str
    provider_customer_id: str | None
    status: str
    start_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaystackSubscriptionResolution:
    outcome: Literal["resolved", "not_recurring"]
    state: BillingSubscriptionState | None = None
    provider_customer_id: str | None = None
    provider_reference: str | None = None
    source: str = ""


# ---------------------------------------------------------------------------
# Generic normalization helpers
# ---------------------------------------------------------------------------


def normalize_provider_name(value: str | None) -> str:
    provider = (value or os.getenv("BILLING_PROVIDER") or "static").strip().lower()
    provider = provider.replace("-", "_")
    aliases = {
        "pay_stack": "paystack",
        "flutter_wave": "flutterwave",
        "flutterwave_v3": "flutterwave",
        "stripe_checkout": "stripe",
        "hmac": "generic",
        "generic_hmac": "generic",
    }
    return aliases.get(provider, provider)


def normalize_plan(value: Any) -> BillingPlanName | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in PAID_PLANS or normalized == "free":
        return normalized  # type: ignore[return-value]
    return None


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def provider_response_json(response: requests.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise BillingProviderError(
            "The billing provider returned an invalid JSON response."
        ) from exc
    return payload if isinstance(payload, dict) else {}


def require_provider_subscription_id(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BillingProviderError("provider_subscription_id is required.")
    return normalized


_REDOCX_PAYSTACK_REFERENCE_RE = re.compile(
    r"^redocx-(personal|business|enterprise)-[0-9a-f]{20}$",
    re.IGNORECASE,
)


def is_redocx_paystack_transaction_reference(value: Any) -> bool:
    return bool(_REDOCX_PAYSTACK_REFERENCE_RE.fullmatch(str(value or "").strip()))


def stripe_subscription_period_end(payload: Mapping[str, Any]) -> datetime | None:
    direct = parse_timestamp(payload.get("current_period_end"))
    if direct is not None:
        return direct

    items = payload.get("items")
    item_rows = items.get("data") if isinstance(items, dict) else None
    if not isinstance(item_rows, list):
        return None

    candidates = [
        parsed
        for parsed in (
            parse_timestamp(item.get("current_period_end"))
            for item in item_rows
            if isinstance(item, dict)
        )
        if parsed is not None
    ]
    return max(candidates) if candidates else None


def stripe_subscription_period_start(payload: Mapping[str, Any]) -> datetime | None:
    direct = parse_timestamp(payload.get("current_period_start"))
    if direct is not None:
        return direct

    items = payload.get("items")
    item_rows = items.get("data") if isinstance(items, dict) else None
    if not isinstance(item_rows, list):
        return None

    candidates = [
        parsed
        for parsed in (
            parse_timestamp(item.get("current_period_start"))
            for item in item_rows
            if isinstance(item, dict)
        )
        if parsed is not None
    ]
    return min(candidates) if candidates else None


def normalize_email(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email or None


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return parse_timestamp(int(text))
        try:
            normalized = text.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            text = str(value).strip()
            if text:
                return text
    return None


def metadata_from(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
            return dict(loaded) if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def merged_metadata_from(*values: Any) -> dict[str, Any]:
    """Merge provider metadata shapes, ignoring empty and malformed values."""
    merged: dict[str, Any] = {}
    for value in values:
        merged.update(metadata_from(value))
    return merged


def signed_sha(raw_body: bytes, secret: str, algorithm: str) -> str:
    digestmod = getattr(hashlib, algorithm.lower().replace("-", ""), None)
    if digestmod is None:
        raise WebhookVerificationError(f"Unsupported webhook signature algorithm: {algorithm}.")
    return hmac.new(secret.encode("utf-8"), raw_body, digestmod).hexdigest()


def constant_time_equals(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(str(left).strip(), str(right).strip())


def lower_headers(headers: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def header_value(headers: Mapping[str, Any], name: str) -> str | None:
    return lower_headers(headers).get(name.lower())


def load_json_body(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise WebhookVerificationError("Webhook payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise WebhookVerificationError("Webhook payload must be a JSON object.")
    return payload


def fallback_event_id(provider: str, raw_body: bytes) -> str:
    return f"{provider}:{hashlib.sha256(raw_body).hexdigest()}"


def env_for_plan(prefix: str, plan: str, suffix: str) -> str | None:
    value = os.getenv(f"{prefix}_{plan.upper()}_{suffix}", "").strip()
    return value or None


def with_query_parameters(url: str, **parameters: str) -> str:
    """Add provider callback state without discarding configured query values."""
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(
        {
            key: str(value)
            for key, value in parameters.items()
            if str(value).strip()
        }
    )
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def plan_metadata(request: BillingCheckoutRequest) -> dict[str, Any]:
    metadata = {
        **(request.metadata or {}),
        "user_id": request.user_id,
        "target_plan": request.target_plan,
    }
    if request.current_plan:
        metadata["current_plan"] = request.current_plan
    if request.email:
        metadata["email"] = request.email
    if request.organization_id is not None:
        metadata["organization_id"] = str(request.organization_id)
    if request.organization_name:
        metadata["organization_name"] = request.organization_name
    if request.target_plan in ORGANIZATION_PLANS:
        quantity = validate_checkout_seat_count(request.target_plan, request.seat_count)
        metadata["seat_count"] = str(quantity)
        metadata["max_accounts"] = str(quantity)
    return {key: value for key, value in metadata.items() if value is not None}


def action_from_event(event_type: str | None, status: str | None) -> WebhookAction:
    event = (event_type or "").strip().lower()
    normalized_status = (status or "").strip().lower()

    activate_events = {
        "checkout.session.completed",
        "invoice.paid",
        "customer.subscription.created",
        "customer.subscription.updated",
        "charge.success",
        "charge.successful",
        "charge.completed",
        "payment.success",
        "payment.successful",
        "payment.completed",
        "transaction.success",
        "subscription.create",
        "subscription.created",
        "subscription.active",
    }
    cancel_events = {
        "customer.subscription.deleted",
        "subscription.disable",
        "subscription.disabled",
        "subscription.cancelled",
        "subscription.canceled",
        "payment.cancelled",
        "payment.canceled",
        "subscription.not_renew",
    }
    past_due_events = {
        "invoice.payment_failed",
        "charge.failed",
        "payment.failed",
        "subscription.payment_failed",
    }
    suspend_events = {
        "charge.dispute.created",
        "charge.dispute.updated",
        "charge.dispute.funds_withdrawn",
        "charge.dispute.create",
        "charge.dispute.remind",
    }
    revoke_events = {
        "refund.processed",
        "refund.succeeded",
    }

    if event in cancel_events or normalized_status in {"cancelled", "canceled", "disabled"}:
        return "cancel"
    if event in revoke_events:
        return "revoke"
    if event in suspend_events:
        return "suspend"
    if event in past_due_events or normalized_status in {"failed", "past_due", "unpaid"}:
        return "past_due"
    if event in activate_events or normalized_status in {
        "success",
        "successful",
        "paid",
        "completed",
        "active",
        "succeeded",
    }:
        return "activate"
    return "ignore"


def normalize_event_from_parts(
    *,
    provider: str,
    raw_body: bytes,
    payload: dict[str, Any],
    event_id: Any = None,
    event_type: Any = None,
    data: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    status: Any = None,
    plan: Any = None,
    user_id: Any = None,
    email: Any = None,
    organization_id: Any = None,
    organization_name: Any = None,
    provider_customer_id: Any = None,
    provider_subscription_id: Any = None,
    provider_reference: Any = None,
    current_period_start: Any = None,
    current_period_end: Any = None,
    max_accounts: Any = None,
    amount: Any = None,
    currency: Any = None,
    action: WebhookAction | None = None,
) -> BillingWebhookEvent:
    data = data or {}
    metadata = metadata or {}

    resolved_event_type = first_non_empty(event_type, payload.get("event"), payload.get("type"), "unknown") or "unknown"
    resolved_status = normalize_status(first_non_empty(status, data.get("status"), payload.get("status")))
    resolved_plan = normalize_plan(
        first_non_empty(
            plan,
            metadata.get("target_plan"),
            metadata.get("plan"),
            data.get("target_plan"),
            data.get("plan"),
            payload.get("target_plan"),
            payload.get("plan"),
        )
    )

    resolved_user_id = first_non_empty(
        user_id,
        metadata.get("user_id"),
        metadata.get("auth0_user_id"),
        data.get("user_id"),
        payload.get("user_id"),
    )
    resolved_email = normalize_email(
        first_non_empty(
            email,
            metadata.get("email"),
            data.get("email"),
            payload.get("email"),
        )
    )
    resolved_organization_id = parse_int(
        first_non_empty(
            organization_id,
            metadata.get("organization_id"),
            data.get("organization_id"),
            payload.get("organization_id"),
        )
    )

    return BillingWebhookEvent(
        provider=provider,
        event_id=first_non_empty(event_id) or fallback_event_id(provider, raw_body),
        event_type=resolved_event_type,
        action=action or action_from_event(resolved_event_type, resolved_status),
        plan=resolved_plan,
        status=resolved_status,
        user_id=resolved_user_id,
        email=resolved_email,
        organization_id=resolved_organization_id,
        organization_name=first_non_empty(
            organization_name,
            metadata.get("organization_name"),
            data.get("organization_name"),
            payload.get("organization_name"),
        ),
        provider_customer_id=first_non_empty(provider_customer_id),
        provider_subscription_id=first_non_empty(provider_subscription_id),
        provider_reference=first_non_empty(provider_reference),
        current_period_start=parse_timestamp(
            first_non_empty(current_period_start, metadata.get("current_period_start"), data.get("current_period_start"))
        ),
        current_period_end=parse_timestamp(
            first_non_empty(current_period_end, metadata.get("current_period_end"), data.get("current_period_end"))
        ),
        max_accounts=parse_int(first_non_empty(max_accounts, metadata.get("max_accounts"), data.get("max_accounts"))),
        amount=amount,
        currency=first_non_empty(currency, data.get("currency"), payload.get("currency")),
        raw=payload,
    )


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------


class BaseBillingProvider:
    name = "base"

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        raise NotImplementedError

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        raise NotImplementedError

    def verify_transaction(self, reference: str) -> BillingWebhookEvent:
        raise BillingProviderError(
            f"Provider '{self.name}' does not support server-side transaction verification."
        )

    def cancel_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        raise BillingProviderError(
            f"Provider '{self.name}' does not support server-side subscription cancellation."
        )

    def resume_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        raise BillingProviderError(
            f"Provider '{self.name}' does not support server-side subscription resumption."
        )

    def retrieve_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionState:
        raise BillingProviderError(
            f"Provider '{self.name}' does not support subscription reconciliation."
        )

    def change_subscription_plan(
        self,
        provider_subscription_id: str,
        *,
        target_plan: BillingPlanName,
        metadata: Mapping[str, Any] | None = None,
        quantity: int | None = None,
        idempotency_key: str | None = None,
        effective_at_period_end: bool = False,
    ) -> BillingSubscriptionChange:
        raise BillingProviderError(
            f"Provider '{self.name}' does not support changing an existing subscription plan."
        )


class StaticCheckoutProvider(BaseBillingProvider):
    name = "static"

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        url = env_for_plan("BILLING", request.target_plan, "CHECKOUT_URL")
        if not url:
            raise CheckoutNotConfiguredError(
                f"Set BILLING_{request.target_plan.upper()}_CHECKOUT_URL or use a live provider."
            )
        return BillingCheckoutSession(
            provider=self.name,
            target_plan=request.target_plan,
            checkout_url=url,
            raw={"static_url": url},
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        raise WebhookVerificationError("The static provider does not accept webhooks.")


class GenericHmacProvider(BaseBillingProvider):
    name = "generic"

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        url = env_for_plan("BILLING_GENERIC", request.target_plan, "CHECKOUT_URL") or env_for_plan(
            "BILLING", request.target_plan, "CHECKOUT_URL"
        )
        if not url:
            raise CheckoutNotConfiguredError(
                f"Set BILLING_GENERIC_{request.target_plan.upper()}_CHECKOUT_URL."
            )
        metadata = plan_metadata(request)
        separator = "&" if "?" in url else "?"
        checkout_url = f"{url}{separator}{urlencode({key: str(value) for key, value in metadata.items()})}"
        return BillingCheckoutSession(
            provider=self.name,
            target_plan=request.target_plan,
            checkout_url=checkout_url,
            raw={"metadata": metadata},
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        secret = os.getenv("BILLING_GENERIC_WEBHOOK_SECRET", "").strip()
        signature_header = os.getenv("BILLING_GENERIC_SIGNATURE_HEADER", "x-webhook-signature").strip()
        algorithm = os.getenv("BILLING_GENERIC_SIGNATURE_ALGORITHM", "sha256").strip().lower()
        prefix = os.getenv("BILLING_GENERIC_SIGNATURE_PREFIX", "").strip()

        if not secret:
            raise WebhookVerificationError("BILLING_GENERIC_WEBHOOK_SECRET is required.")

        incoming = header_value(headers, signature_header)
        expected = signed_sha(raw_body, secret, algorithm)
        if prefix:
            expected = f"{prefix}{expected}"

        if not constant_time_equals(incoming, expected):
            raise WebhookVerificationError("Invalid generic billing webhook signature.")

        payload = load_json_body(raw_body)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        metadata = metadata_from(data.get("metadata") or data.get("meta") or payload.get("metadata"))

        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=payload.get("id") or data.get("id") or data.get("reference"),
            event_type=payload.get("event") or payload.get("type"),
            data=data,
            metadata=metadata,
            provider_customer_id=data.get("customer_id") or data.get("customer") or data.get("customer_code"),
            provider_subscription_id=data.get("subscription_id") or data.get("subscription") or data.get("subscription_code"),
            provider_reference=data.get("reference") or data.get("tx_ref") or payload.get("reference"),
            amount=data.get("amount"),
            currency=data.get("currency"),
        )


class StripeBillingProvider(BaseBillingProvider):
    name = "stripe"

    def _validate_unit_price(
        self,
        secret_key: str,
        price_id: str,
        plan: BillingPlanName,
    ) -> None:
        try:
            response = requests.get(
                "https://api.stripe.com/v1/prices/" f"{quote(price_id, safe='')}",
                auth=(secret_key, ""),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to validate the configured recurring price."
            ) from exc

        payload = provider_response_json(response)
        if response.status_code >= 400:
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else None
            )
            raise CheckoutNotConfiguredError(
                message or f"Stripe price {price_id} could not be validated."
            )

        expected_unit_amount = plan_unit_amount_kobo(plan)
        actual_unit_amount = parse_int(payload.get("unit_amount"))
        currency = str(payload.get("currency") or "").strip().lower()
        recurring = payload.get("recurring")
        interval = str(recurring.get("interval") or "").strip().lower() if isinstance(recurring, dict) else ""
        interval_count = parse_int(recurring.get("interval_count")) if isinstance(recurring, dict) else None
        if (
            payload.get("active") is False
            or payload.get("type") != "recurring"
            or not isinstance(recurring, dict)
            or interval != "month"
            or (interval_count or 1) != 1
            or currency != "ngn"
            or actual_unit_amount != expected_unit_amount
        ):
            raise CheckoutNotConfiguredError(
                f"STRIPE_{plan.upper()}_PRICE_ID must be an active monthly recurring NGN "
                f"price with interval_count=1 and unit_amount={expected_unit_amount}."
            )

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        price_id = env_for_plan("STRIPE", request.target_plan, "PRICE_ID")
        success_url = request.success_url or DEFAULT_SUCCESS_URL
        cancel_url = request.cancel_url or DEFAULT_CANCEL_URL

        if not secret_key or not price_id or not success_url or not cancel_url:
            raise CheckoutNotConfiguredError(
                "Stripe checkout requires STRIPE_SECRET_KEY, STRIPE_<PLAN>_PRICE_ID, "
                "BILLING_SUCCESS_URL, and BILLING_CANCEL_URL."
            )

        quantity = validate_checkout_seat_count(request.target_plan, request.seat_count)
        self._validate_unit_price(secret_key, price_id, request.target_plan)
        metadata = {key: str(value) for key, value in plan_metadata(request).items()}
        form: list[tuple[str, str]] = [
            ("mode", "subscription"),
            ("success_url", success_url),
            ("cancel_url", cancel_url),
            ("client_reference_id", request.user_id),
            ("line_items[0][price]", price_id),
            ("line_items[0][quantity]", str(quantity)),
            ("allow_promotion_codes", os.getenv("STRIPE_ALLOW_PROMOTION_CODES", "false").strip().lower() in {"1", "true", "yes", "on"} and "true" or "false"),
        ]
        if request.email:
            form.append(("customer_email", request.email))
        for key, value in metadata.items():
            # Checkout-session metadata lets checkout.session.completed activate quickly.
            form.append((f"metadata[{key}]", value))
            # Subscription metadata lets customer.subscription.* events update/cancel later.
            form.append((f"subscription_data[metadata][{key}]", value))

        request_headers = (
            {"Idempotency-Key": request.idempotency_key}
            if request.idempotency_key
            else None
        )
        try:
            response = requests.post(
                "https://api.stripe.com/v1/checkout/sessions",
                auth=(secret_key, ""),
                data=form,
                headers=request_headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to create checkout."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400:
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else None
            )
            raise BillingProviderError(message or "Stripe checkout failed.")

        checkout_url = payload.get("url")
        if not checkout_url:
            raise BillingProviderError("Stripe did not return a checkout URL.")

        return BillingCheckoutSession(
            provider=self.name,
            target_plan=request.target_plan,
            checkout_url=checkout_url,
            provider_session_id=payload.get("id"),
            provider_customer_id=payload.get("customer"),
            provider_subscription_id=payload.get("subscription"),
            reference=payload.get("payment_intent") or payload.get("id"),
            raw=payload,
        )

    def initialize_handoff_authorization(
        self,
        *,
        email: str,
        target_plan: BillingPlanName,
        seat_count: int,
        organization_id: int,
        new_owner_user_id: str,
        start_at: datetime,
        return_url: str,
        idempotency_key: str,
    ) -> BillingHandoffAuthorization:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        price_id = env_for_plan("STRIPE", target_plan, "PRICE_ID")
        if not secret_key or not price_id or not return_url:
            raise CheckoutNotConfiguredError(
                "Stripe ownership handoff requires STRIPE_SECRET_KEY, the plan price ID, and a return URL."
            )
        quantity = validate_checkout_seat_count(target_plan, seat_count)
        self._validate_unit_price(secret_key, price_id, target_plan)
        normalized_start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        metadata = {
            "billing_operation": "ownership_handoff",
            "organization_id": str(organization_id),
            "user_id": str(new_owner_user_id),
            "target_plan": str(target_plan),
            "seat_count": str(quantity),
            "max_accounts": str(quantity),
            "handoff_effective_at": normalized_start.astimezone(timezone.utc).isoformat(),
        }
        success_url = with_query_parameters(
            return_url, billing_handoff="success", organization_id=organization_id
        )
        cancel_url = with_query_parameters(
            return_url, billing_handoff="cancelled", organization_id=organization_id
        )
        form: list[tuple[str, str]] = [
            ("mode", "setup"),
            ("success_url", success_url),
            ("cancel_url", cancel_url),
            ("customer_email", email),
            ("client_reference_id", str(new_owner_user_id)),
        ]
        for key, value in metadata.items():
            form.append((f"metadata[{key}]", value))
            form.append((f"setup_intent_data[metadata][{key}]", value))
        try:
            response = requests.post(
                "https://api.stripe.com/v1/checkout/sessions",
                auth=(secret_key, ""),
                data=form,
                headers={"Idempotency-Key": idempotency_key[:255]},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to authorize the new organization payer."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400:
            message = payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else None
            raise BillingProviderError(message or "Stripe payer authorization could not be created.")
        checkout_url = first_non_empty(payload.get("url"))
        session_id = first_non_empty(payload.get("id"))
        if not checkout_url or not session_id:
            raise BillingProviderError("Stripe did not return a payer authorization session.")
        return BillingHandoffAuthorization(
            provider=self.name,
            status="authorization_pending",
            authorization_url=checkout_url,
            provider_reference=session_id,
            provider_customer_id=first_non_empty(payload.get("customer")),
            start_at=normalized_start,
            raw=payload,
        )

    def finalize_handoff_authorization(
        self,
        *,
        provider_reference: str,
        email: str,
        target_plan: BillingPlanName,
        seat_count: int,
        organization_id: int,
        new_owner_user_id: str,
        start_at: datetime,
        idempotency_key: str,
    ) -> BillingFutureSubscription:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        price_id = env_for_plan("STRIPE", target_plan, "PRICE_ID")
        if not secret_key or not price_id:
            raise CheckoutNotConfiguredError("Stripe ownership handoff is not configured.")
        quantity = validate_checkout_seat_count(target_plan, seat_count)
        self._validate_unit_price(secret_key, price_id, target_plan)
        session_id = str(provider_reference or "").strip()
        if not session_id:
            raise BillingProviderError("Stripe payer authorization session is missing.")
        try:
            response = requests.get(
                "https://api.stripe.com/v1/checkout/sessions/" + quote(session_id, safe=""),
                auth=(secret_key, ""),
                params={"expand[]": "setup_intent"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError("Stripe could not verify the payer authorization session.") from exc
        session = provider_response_json(response)
        if response.status_code >= 400:
            message = session.get("error", {}).get("message") if isinstance(session.get("error"), dict) else None
            raise BillingProviderError(message or "Stripe payer authorization verification failed.")
        if str(session.get("mode") or "").lower() != "setup" or str(session.get("status") or "").lower() != "complete":
            raise BillingProviderError("Stripe payer authorization is not complete yet.")
        setup_intent = session.get("setup_intent")
        if isinstance(setup_intent, str):
            try:
                setup_response = requests.get(
                    "https://api.stripe.com/v1/setup_intents/" + quote(setup_intent, safe=""),
                    auth=(secret_key, ""),
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise BillingProviderError("Stripe could not load the completed SetupIntent.") from exc
            setup_intent = provider_response_json(setup_response)
            if setup_response.status_code >= 400:
                raise BillingProviderError("Stripe could not verify the completed SetupIntent.")
        if not isinstance(setup_intent, dict) or str(setup_intent.get("status") or "").lower() != "succeeded":
            raise BillingProviderError("Stripe payer authorization has not succeeded yet.")
        payment_method = first_non_empty(setup_intent.get("payment_method"))
        customer_id = first_non_empty(session.get("customer"), setup_intent.get("customer"))
        if not payment_method or not customer_id:
            raise BillingProviderError("Stripe payer authorization is missing its customer or payment method.")
        normalized_start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        start_epoch = int(normalized_start.astimezone(timezone.utc).timestamp())
        if start_epoch <= int(datetime.now(tz=timezone.utc).timestamp()):
            raise BillingProviderError("The ownership handoff paid period has already ended.")
        metadata = {
            "billing_operation": "ownership_handoff",
            "organization_id": str(organization_id),
            "user_id": str(new_owner_user_id),
            "target_plan": str(target_plan),
            "seat_count": str(quantity),
            "max_accounts": str(quantity),
            "handoff_effective_at": normalized_start.astimezone(timezone.utc).isoformat(),
        }
        form: list[tuple[str, str]] = [
            ("customer", customer_id),
            ("items[0][price]", price_id),
            ("items[0][quantity]", str(quantity)),
            ("default_payment_method", payment_method),
            ("collection_method", "charge_automatically"),
            ("trial_end", str(start_epoch)),
            ("proration_behavior", "none"),
        ]
        for key, value in metadata.items():
            form.append((f"metadata[{key}]", value))
        try:
            subscription_response = requests.post(
                "https://api.stripe.com/v1/subscriptions",
                auth=(secret_key, ""),
                data=form,
                headers={"Idempotency-Key": f"{idempotency_key}:subscription"[:255]},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError("Stripe could not schedule the new owner's subscription.") from exc
        payload = provider_response_json(subscription_response)
        if subscription_response.status_code >= 400:
            message = payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else None
            raise BillingProviderError(message or "Stripe could not schedule the new owner's subscription.")
        subscription_id = first_non_empty(payload.get("id"))
        if not subscription_id:
            raise BillingProviderError("Stripe did not return the new subscription identifier.")
        return BillingFutureSubscription(
            provider=self.name,
            provider_subscription_id=subscription_id,
            provider_customer_id=customer_id,
            status=str(payload.get("status") or "trialing"),
            start_at=normalized_start,
            raw=payload,
        )

    def _fetch_subscription(self, provider_subscription_id: str) -> dict[str, Any]:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "STRIPE_SECRET_KEY is required to load a Stripe subscription."
            )
        subscription_id = require_provider_subscription_id(provider_subscription_id)
        try:
            response = requests.get(
                "https://api.stripe.com/v1/subscriptions/"
                f"{quote(subscription_id, safe='')}",
                auth=(secret_key, ""),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to load the subscription."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400:
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else None
            )
            raise BillingProviderError(message or "Stripe could not load the subscription.")
        return payload

    def _fetch_related_object(
        self,
        resource: Literal["charges", "invoices"],
        object_id: str | None,
    ) -> dict[str, Any]:
        normalized_id = str(object_id or "").strip()
        if not normalized_id:
            return {}
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not secret_key:
            return {}
        try:
            response = requests.get(
                f"https://api.stripe.com/v1/{resource}/{quote(normalized_id, safe='')}",
                auth=(secret_key, ""),
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            return {}
        if response.status_code >= 400:
            return {}
        return provider_response_json(response)

    @staticmethod
    def _subscription_item(payload: Mapping[str, Any]) -> dict[str, Any]:
        items = payload.get("items")
        rows = items.get("data") if isinstance(items, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise BillingProviderError(
                "Stripe subscription does not contain a billable subscription item."
            )
        return rows[0]

    def retrieve_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionState:
        payload = self._fetch_subscription(provider_subscription_id)
        item = self._subscription_item(payload)
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        price_id = first_non_empty(
            price.get("id"),
            item.get("plan", {}).get("id")
            if isinstance(item.get("plan"), dict)
            else None,
        )
        resolved_plan: BillingPlanName | None = None
        for candidate in PAID_PLANS:
            if price_id and price_id == env_for_plan("STRIPE", candidate, "PRICE_ID"):
                resolved_plan = candidate  # type: ignore[assignment]
                break
        return BillingSubscriptionState(
            provider=self.name,
            provider_subscription_id=str(payload.get("id") or provider_subscription_id),
            status=str(payload.get("status") or "unknown").strip().lower(),
            provider_customer_id=first_non_empty(payload.get("customer")),
            current_period_start=stripe_subscription_period_start(payload),
            current_period_end=stripe_subscription_period_end(payload),
            cancel_at_period_end=bool(payload.get("cancel_at_period_end")),
            plan=resolved_plan,
            raw=payload,
        )

    def change_subscription_plan(
        self,
        provider_subscription_id: str,
        *,
        target_plan: BillingPlanName,
        metadata: Mapping[str, Any] | None = None,
        quantity: int | None = None,
        idempotency_key: str | None = None,
        effective_at_period_end: bool = False,
    ) -> BillingSubscriptionChange:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        price_id = env_for_plan("STRIPE", target_plan, "PRICE_ID")
        if not secret_key or not price_id:
            raise CheckoutNotConfiguredError(
                f"Stripe plan changes require STRIPE_SECRET_KEY and STRIPE_{target_plan.upper()}_PRICE_ID."
            )

        self._validate_unit_price(secret_key, price_id, target_plan)
        target_quantity = (
            validate_checkout_seat_count(target_plan, quantity)
            if quantity is not None
            else None
        )
        subscription = self._fetch_subscription(provider_subscription_id)
        subscription_id = str(subscription.get("id") or provider_subscription_id)
        item = self._subscription_item(subscription)
        item_id = first_non_empty(item.get("id"))
        if not item_id:
            raise BillingProviderError("Stripe subscription item is missing its identifier.")

        normalized_metadata = {
            str(key): str(value)
            for key, value in dict(metadata or {}).items()
            if value is not None
        }
        request_headers = (
            {"Idempotency-Key": idempotency_key[:255]}
            if idempotency_key
            else None
        )

        if effective_at_period_end:
            current_end = stripe_subscription_period_end(subscription)
            current_start = stripe_subscription_period_start(subscription)
            current_price = item.get("price") if isinstance(item.get("price"), dict) else {}
            current_price_id = first_non_empty(
                current_price.get("id"),
                item.get("plan", {}).get("id") if isinstance(item.get("plan"), dict) else None,
            )
            if current_end is None or current_start is None or not current_price_id:
                raise BillingProviderError(
                    "Stripe did not return the current billing period required to schedule this downgrade."
                )

            schedule_id = first_non_empty(subscription.get("schedule"))
            if not schedule_id:
                schedule_headers = (
                    {"Idempotency-Key": f"{idempotency_key}:schedule"[:255]}
                    if idempotency_key
                    else None
                )
                try:
                    response = requests.post(
                        "https://api.stripe.com/v1/subscription_schedules",
                        auth=(secret_key, ""),
                        data={"from_subscription": subscription_id},
                        headers=schedule_headers,
                        timeout=DEFAULT_TIMEOUT_SECONDS,
                    )
                except requests.RequestException as exc:
                    raise BillingProviderError(
                        "Stripe could not be reached to create a subscription schedule."
                    ) from exc
                schedule = provider_response_json(response)
                if response.status_code >= 400:
                    message = (
                        schedule.get("error", {}).get("message")
                        if isinstance(schedule.get("error"), dict)
                        else None
                    )
                    raise BillingProviderError(
                        message or "Stripe could not create a subscription schedule."
                    )
                schedule_id = first_non_empty(schedule.get("id"))
            if not schedule_id:
                raise BillingProviderError("Stripe did not return a subscription schedule ID.")

            current_quantity = parse_int(item.get("quantity")) or 1
            next_quantity = target_quantity or current_quantity
            form: list[tuple[str, str]] = [
                ("end_behavior", "release"),
                ("phases[0][start_date]", str(int(current_start.timestamp()))),
                ("phases[0][end_date]", str(int(current_end.timestamp()))),
                ("phases[0][items][0][price]", current_price_id),
                ("phases[0][items][0][quantity]", str(current_quantity)),
                ("phases[0][proration_behavior]", "none"),
                ("phases[1][start_date]", str(int(current_end.timestamp()))),
                ("phases[1][items][0][price]", price_id),
                ("phases[1][items][0][quantity]", str(next_quantity)),
                ("phases[1][proration_behavior]", "none"),
            ]
            for key, value in normalized_metadata.items():
                form.append((f"phases[1][metadata][{key}]", value))

            try:
                response = requests.post(
                    "https://api.stripe.com/v1/subscription_schedules/"
                    f"{quote(schedule_id, safe='')}",
                    auth=(secret_key, ""),
                    data=form,
                    headers=request_headers,
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise BillingProviderError(
                    "Stripe could not be reached to schedule the downgrade."
                ) from exc
            payload = provider_response_json(response)
            if response.status_code >= 400:
                message = (
                    payload.get("error", {}).get("message")
                    if isinstance(payload.get("error"), dict)
                    else None
                )
                raise BillingProviderError(
                    message or "Stripe could not schedule the subscription downgrade."
                )
            return BillingSubscriptionChange(
                provider=self.name,
                provider_subscription_id=subscription_id,
                status="change_scheduled",
                current_period_end=current_end,
                effective_at=current_end,
                target_plan=target_plan,
                raw=payload,
            )

        form = [
            ("items[0][id]", item_id),
            ("items[0][price]", price_id),
            ("proration_behavior", os.getenv("STRIPE_PLAN_CHANGE_PRORATION_BEHAVIOR", "create_prorations")),
            ("cancel_at_period_end", "false"),
        ]
        if target_quantity is not None:
            form.append(("items[0][quantity]", str(target_quantity)))
        for key, value in normalized_metadata.items():
            form.append((f"metadata[{key}]", value))

        try:
            response = requests.post(
                "https://api.stripe.com/v1/subscriptions/"
                f"{quote(subscription_id, safe='')}",
                auth=(secret_key, ""),
                data=form,
                headers=request_headers,
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to update the subscription plan."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400:
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else None
            )
            raise BillingProviderError(message or "Stripe could not update the subscription plan.")
        return BillingSubscriptionChange(
            provider=self.name,
            provider_subscription_id=subscription_id,
            status=str(payload.get("status") or "active"),
            current_period_end=stripe_subscription_period_end(payload),
            target_plan=target_plan,
            raw=payload,
        )

    def _set_cancel_at_period_end(
        self,
        provider_subscription_id: str,
        *,
        enabled: bool,
    ) -> BillingSubscriptionChange:
        secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "STRIPE_SECRET_KEY is required to change a Stripe subscription."
            )

        subscription_id = require_provider_subscription_id(
            provider_subscription_id
        )
        try:
            response = requests.post(
                "https://api.stripe.com/v1/subscriptions/"
                f"{quote(subscription_id, safe='')}",
                auth=(secret_key, ""),
                data={
                    "cancel_at_period_end": "true" if enabled else "false",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Stripe could not be reached to change the subscription."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code < 400 and not payload:
            raise BillingProviderError(
                "Stripe returned an invalid subscription response."
            )
        if response.status_code >= 400:
            message = (
                payload.get("error", {}).get("message")
                if isinstance(payload.get("error"), dict)
                else None
            )
            raise BillingProviderError(
                message
                or (
                    "Stripe could not schedule subscription cancellation."
                    if enabled
                    else "Stripe could not resume the subscription."
                )
            )

        return BillingSubscriptionChange(
            provider=self.name,
            provider_subscription_id=subscription_id,
            status=(
                "cancellation_scheduled"
                if bool(payload.get("cancel_at_period_end"))
                else str(payload.get("status") or "active")
            ),
            current_period_end=stripe_subscription_period_end(payload),
            raw=payload,
        )

    def cancel_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        return self._set_cancel_at_period_end(
            provider_subscription_id,
            enabled=True,
        )

    def resume_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        return self._set_cancel_at_period_end(
            provider_subscription_id,
            enabled=False,
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
        signature_header = header_value(headers, "stripe-signature")
        if not secret:
            raise WebhookVerificationError("STRIPE_WEBHOOK_SECRET is required.")
        if not signature_header:
            raise WebhookVerificationError("Missing Stripe-Signature header.")

        pieces: dict[str, list[str]] = {}
        for item in signature_header.split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            pieces.setdefault(key.strip(), []).append(value.strip())

        timestamp = pieces.get("t", [None])[0]
        signatures = pieces.get("v1", [])
        if not timestamp or not signatures:
            raise WebhookVerificationError("Malformed Stripe-Signature header.")

        try:
            timestamp_value = int(timestamp)
            tolerance_seconds = int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))
            if tolerance_seconds > 0:
                age_seconds = abs(int(datetime.now(tz=timezone.utc).timestamp()) - timestamp_value)
                if age_seconds > tolerance_seconds:
                    raise WebhookVerificationError("Stripe webhook timestamp is outside tolerance.")
        except ValueError as exc:
            raise WebhookVerificationError("Malformed Stripe webhook timestamp.") from exc

        signed_payload = f"{timestamp}.".encode("utf-8") + raw_body
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        if not any(constant_time_equals(signature, expected) for signature in signatures):
            raise WebhookVerificationError("Invalid Stripe webhook signature.")

        payload = load_json_body(raw_body)
        event_type = payload.get("type")
        obj = payload.get("data", {}).get("object", {}) if isinstance(payload.get("data"), dict) else {}
        if not isinstance(obj, dict):
            obj = {}

        normalized_event_type = str(event_type or "").strip().lower()
        related_charge: dict[str, Any] = {}
        if normalized_event_type == "charge.refunded":
            related_charge = obj
        elif normalized_event_type.startswith("charge.dispute"):
            related_charge = self._fetch_related_object("charges", obj.get("charge"))

        invoice_id = first_non_empty(
            obj.get("invoice"),
            related_charge.get("invoice"),
        )
        related_invoice = self._fetch_related_object("invoices", invoice_id)

        parent = obj.get("parent") if isinstance(obj.get("parent"), dict) else {}
        subscription_details = obj.get("subscription_details") if isinstance(obj.get("subscription_details"), dict) else {}
        invoice_parent = (
            related_invoice.get("parent")
            if isinstance(related_invoice.get("parent"), dict)
            else {}
        )
        invoice_subscription_details = (
            invoice_parent.get("subscription_details")
            if isinstance(invoice_parent.get("subscription_details"), dict)
            else {}
        )
        metadata = merged_metadata_from(
            related_invoice.get("metadata"),
            invoice_subscription_details.get("metadata"),
            related_charge.get("metadata"),
            obj.get("metadata"),
            subscription_details.get("metadata"),
            parent.get("subscription_details", {}).get("metadata") if isinstance(parent.get("subscription_details"), dict) else None,
        )

        status = obj.get("status") or obj.get("payment_status")
        event_text = str(event_type or "")
        if event_text.startswith("customer.subscription"):
            subscription = obj.get("id")
        else:
            subscription = first_non_empty(
                obj.get("subscription"),
                subscription_details.get("subscription"),
                related_charge.get("subscription"),
                related_invoice.get("subscription"),
                invoice_subscription_details.get("subscription"),
            )

        customer_details = obj.get("customer_details") if isinstance(obj.get("customer_details"), dict) else {}

        action_override: WebhookAction | None = None
        normalized_event_status = str(status or "").strip().lower()
        if (
            normalized_event_type == "checkout.session.completed"
            and str(obj.get("mode") or "").strip().lower() == "setup"
        ):
            # Setup mode only records a payment method. It must never grant or
            # replace paid entitlement; ownership-handoff activation waits for
            # the successor subscription's paid period to begin.
            action_override = "ignore"
        elif (
            normalized_event_type == "customer.subscription.updated"
            and bool(obj.get("cancel_at_period_end"))
        ):
            action_override = "cancel"
        elif normalized_event_type == "charge.refunded":
            amount = parse_int(obj.get("amount"))
            amount_refunded = parse_int(obj.get("amount_refunded"))
            if bool(obj.get("refunded")) or (
                amount is not None
                and amount_refunded is not None
                and amount_refunded >= amount
            ):
                action_override = "revoke"
            else:
                action_override = "ignore"
        elif normalized_event_type in {
            "charge.dispute.created",
            "charge.dispute.updated",
            "charge.dispute.funds_withdrawn",
        }:
            action_override = "suspend"
        elif normalized_event_type in {
            "charge.dispute.closed",
            "charge.dispute.funds_reinstated",
        }:
            if normalized_event_status in {"won", "warning_closed"}:
                action_override = "restore"
            elif normalized_event_status == "lost":
                action_override = "revoke"

        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=payload.get("id"),
            event_type=event_type,
            data=obj,
            metadata=metadata,
            status=status,
            user_id=obj.get("client_reference_id"),
            email=obj.get("customer_email") or customer_details.get("email"),
            provider_customer_id=first_non_empty(
                obj.get("customer"),
                related_charge.get("customer"),
                related_invoice.get("customer"),
            ),
            provider_subscription_id=subscription,
            provider_reference=first_non_empty(
                obj.get("id"),
                obj.get("payment_intent"),
                obj.get("invoice"),
                related_charge.get("payment_intent"),
                invoice_id,
            ),
            current_period_start=obj.get("current_period_start"),
            current_period_end=obj.get("current_period_end"),
            amount=obj.get("amount_total") or obj.get("amount_paid") or obj.get("amount_due"),
            currency=obj.get("currency"),
            action=action_override,
        )


class PaystackBillingProvider(BaseBillingProvider):
    name = "paystack"

    def _fetch_plan(self, secret_key: str, plan_code: str) -> dict[str, Any]:
        try:
            response = requests.get(
                "https://api.paystack.co/plan/" f"{quote(plan_code, safe='')}",
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to validate the configured recurring plan."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise CheckoutNotConfiguredError(
                str(payload.get("message") or "Paystack recurring plan could not be validated.")
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise CheckoutNotConfiguredError("Paystack returned an invalid plan response.")
        return data

    def _resolve_checkout_plan_code(
        self,
        *,
        secret_key: str,
        request: BillingCheckoutRequest,
        quantity: int,
    ) -> str:
        base_plan_code = env_for_plan("PAYSTACK", request.target_plan, "PLAN_CODE")
        if not base_plan_code:
            raise CheckoutNotConfiguredError(
                f"Recurring Paystack checkout requires PAYSTACK_{request.target_plan.upper()}_PLAN_CODE."
            )

        base_plan = self._fetch_plan(secret_key, base_plan_code)
        expected_unit_amount = plan_unit_amount_kobo(request.target_plan)
        base_amount = parse_int(base_plan.get("amount"))
        interval = str(base_plan.get("interval") or "").strip().lower()
        currency = str(base_plan.get("currency") or "").strip().upper()
        invoice_limit = parse_int(base_plan.get("invoice_limit")) or 0
        if (
            base_amount != expected_unit_amount
            or currency != "NGN"
            or interval != "monthly"
            or invoice_limit > 0
        ):
            raise CheckoutNotConfiguredError(
                f"PAYSTACK_{request.target_plan.upper()}_PLAN_CODE must be an NGN monthly recurring "
                f"plan priced at {expected_unit_amount} kobo per seat with no finite invoice_limit."
            )

        if quantity == 1:
            return str(base_plan.get("plan_code") or base_plan_code)

        total_amount = expected_checkout_amount_kobo(request.target_plan, quantity)
        plan_name = (
            f"ReDOCX {request.target_plan.title()} - {quantity} "
            f"seat{'s' if quantity != 1 else ''}"
        )

        try:
            response = requests.get(
                "https://api.paystack.co/plan",
                params={"perPage": 100, "amount": total_amount, "interval": interval},
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to resolve the recurring seat plan."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(
                str(payload.get("message") or "Paystack could not list recurring plans.")
            )
        for candidate in payload.get("data") or []:
            if not isinstance(candidate, dict):
                continue
            if (
                str(candidate.get("name") or "") == plan_name
                and parse_int(candidate.get("amount")) == total_amount
                and str(candidate.get("interval") or "").strip().lower() == interval
                and str(candidate.get("currency") or "").strip().upper() == currency
                and (parse_int(candidate.get("invoice_limit")) or 0) == 0
                and candidate.get("plan_code")
            ):
                return str(candidate["plan_code"])

        create_body: dict[str, Any] = {
            "name": plan_name,
            "amount": total_amount,
            "interval": interval,
            "currency": currency,
            "description": (
                f"ReDOCX {request.target_plan.title()} recurring subscription for "
                f"{quantity} seat{'s' if quantity != 1 else ''}."
            ),
            "send_invoices": bool(base_plan.get("send_invoices", True)),
            "send_sms": bool(base_plan.get("send_sms", False)),
        }
        try:
            response = requests.post(
                "https://api.paystack.co/plan",
                json=create_body,
                headers={
                    "Authorization": f"Bearer {secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to create the recurring seat plan."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(
                str(payload.get("message") or "Paystack could not create the recurring seat plan.")
            )
        data = payload.get("data") or {}
        plan_code = data.get("plan_code") if isinstance(data, dict) else None
        if not plan_code:
            raise BillingProviderError("Paystack did not return a recurring plan code.")
        if (
            str(data.get("interval") or interval).strip().lower() != "monthly"
            or (parse_int(data.get("invoice_limit")) or 0) > 0
        ):
            raise BillingProviderError(
                "Paystack created a seat plan that is not unlimited monthly recurring; billing was stopped before checkout."
            )
        return str(plan_code)

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        callback_url = request.success_url or os.getenv("PAYSTACK_CALLBACK_URL", "").strip() or DEFAULT_SUCCESS_URL

        if not secret_key or not callback_url:
            raise CheckoutNotConfiguredError("Paystack checkout requires PAYSTACK_SECRET_KEY and a callback URL.")
        if not request.email:
            raise CheckoutNotConfiguredError("Paystack requires an email address to initialize checkout.")

        quantity = validate_checkout_seat_count(request.target_plan, request.seat_count)
        amount = expected_checkout_amount_kobo(request.target_plan, quantity)
        plan_code = self._resolve_checkout_plan_code(
            secret_key=secret_key,
            request=request,
            quantity=quantity,
        )

        callback_url = with_query_parameters(
            callback_url,
            checkout="success",
            provider=self.name,
        )

        timestamp = int(datetime.now(tz=timezone.utc).timestamp())
        reference_seed = (
            request.idempotency_key
            or f"{request.user_id}:{request.target_plan}:{timestamp}"
        )
        reference = f"redocx-{request.target_plan}-{hashlib.sha256(reference_seed.encode('utf-8')).hexdigest()[:20]}"

        body: dict[str, Any] = {
            "email": request.email,
            "callback_url": callback_url,
            "reference": reference,
            # Paystack's Initialize Transaction contract defines metadata as a
            # stringified JSON object. Keeping this canonical also makes the
            # same identity fields available in charge.success payloads.
            "metadata": json.dumps(
                plan_metadata(request),
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        body["plan"] = plan_code
        # Paystack requires amount when initializing a transaction, but when a
        # plan is supplied the plan amount is authoritative. The resolved plan
        # above is therefore created/validated at this exact seat total.
        body["amount"] = amount

        try:
            response = requests.post(
                "https://api.paystack.co/transaction/initialize",
                json=body,
                headers={
                    "Authorization": f"Bearer {secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to initialize checkout."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            logger.warning(
                "Paystack checkout failed status=%s plan=%s reference=%s message=%s",
                response.status_code,
                request.target_plan,
                reference,
                payload.get("message"),
            )
            raise BillingProviderError(payload.get("message") or "Paystack checkout failed.")

        data = payload.get("data") or {}
        checkout_url = data.get("authorization_url")
        if not checkout_url:
            raise BillingProviderError("Paystack did not return an authorization URL.")

        return BillingCheckoutSession(
            provider=self.name,
            target_plan=request.target_plan,
            checkout_url=checkout_url,
            provider_session_id=data.get("access_code"),
            reference=data.get("reference") or reference,
            raw=payload,
        )

    def _reusable_customer_authorization(self, customer: Mapping[str, Any]) -> str | None:
        authorizations = customer.get("authorizations")
        if not isinstance(authorizations, list):
            return None
        for item in reversed(authorizations):
            if not isinstance(item, dict):
                continue
            code = first_non_empty(item.get("authorization_code"))
            if code and item.get("reusable") is True and item.get("active") is not False:
                return code
        return None

    def _create_future_handoff_subscription(
        self,
        *,
        customer_identity: str,
        authorization_code: str,
        target_plan: BillingPlanName,
        seat_count: int,
        start_at: datetime,
    ) -> BillingFutureSubscription:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise CheckoutNotConfiguredError("PAYSTACK_SECRET_KEY is required for ownership handoff.")
        quantity = validate_checkout_seat_count(target_plan, seat_count)
        request = BillingCheckoutRequest(
            user_id="ownership-handoff",
            email=None,
            target_plan=target_plan,
            seat_count=quantity,
        )
        plan_code = self._resolve_checkout_plan_code(
            secret_key=secret_key, request=request, quantity=quantity
        )
        normalized_start = start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)
        if normalized_start <= datetime.now(tz=timezone.utc):
            raise BillingProviderError("The ownership handoff paid period has already ended.")
        body = {
            "customer": customer_identity,
            "plan": plan_code,
            "authorization": authorization_code,
            "start_date": normalized_start.astimezone(timezone.utc).isoformat(),
        }
        try:
            response = requests.post(
                "https://api.paystack.co/subscription",
                json=body,
                headers={
                    "Authorization": f"Bearer {secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError("Paystack could not schedule the new owner's subscription.") from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(str(payload.get("message") or "Paystack could not schedule the new owner's subscription."))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        subscription_id = first_non_empty(data.get("subscription_code"))
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        customer_code = first_non_empty(customer.get("customer_code"), customer.get("code"), customer_identity)
        if not subscription_id:
            raise BillingProviderError("Paystack did not return the new subscription identifier.")
        if (parse_int(data.get("invoice_limit")) or 0) > 0:
            try:
                self.cancel_subscription(subscription_id)
            except Exception:
                pass
            raise BillingProviderError("Paystack created a finite subscription unexpectedly; it was disabled for safety.")
        return BillingFutureSubscription(
            provider=self.name,
            provider_subscription_id=subscription_id,
            provider_customer_id=customer_code,
            status=str(data.get("status") or "active"),
            start_at=normalized_start,
            raw=payload,
        )

    def initialize_handoff_authorization(
        self,
        *,
        email: str,
        target_plan: BillingPlanName,
        seat_count: int,
        organization_id: int,
        new_owner_user_id: str,
        start_at: datetime,
        return_url: str,
        idempotency_key: str,
    ) -> BillingHandoffAuthorization:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key or not return_url:
            raise CheckoutNotConfiguredError("Paystack ownership handoff requires PAYSTACK_SECRET_KEY and a return URL.")
        quantity = validate_checkout_seat_count(target_plan, seat_count)
        normalized_email = str(email or "").strip().lower()
        if not normalized_email:
            raise CheckoutNotConfiguredError("Paystack ownership handoff requires the new owner's email address.")
        customer: dict[str, Any] | None = None
        try:
            customer = self._fetch_customer(normalized_email)
        except PaystackCustomerNotFoundError:
            customer = None
        if customer is not None:
            authorization_code = self._reusable_customer_authorization(customer)
            if authorization_code:
                customer_identity = str(first_non_empty(customer.get("customer_code"), customer.get("id"), normalized_email) or normalized_email)
                future = self._create_future_handoff_subscription(
                    customer_identity=customer_identity,
                    authorization_code=authorization_code,
                    target_plan=target_plan,
                    seat_count=quantity,
                    start_at=start_at,
                )
                return BillingHandoffAuthorization(
                    provider=self.name,
                    status="scheduled",
                    provider_customer_id=future.provider_customer_id,
                    provider_subscription_id=future.provider_subscription_id,
                    start_at=future.start_at,
                    raw=future.raw,
                )
        callback_url = with_query_parameters(
            return_url, billing_handoff="success", organization_id=organization_id
        )
        body = {
            "email": normalized_email,
            "channel": "direct_debit",
            "callback_url": callback_url,
        }
        try:
            response = requests.post(
                "https://api.paystack.co/customer/authorization/initialize",
                json=body,
                headers={
                    "Authorization": f"Bearer {secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError("Paystack could not initialize payer authorization.") from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(str(payload.get("message") or "Paystack payer authorization could not be initialized."))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        redirect_url = first_non_empty(data.get("redirect_url"), data.get("authorization_url"))
        reference = first_non_empty(data.get("reference"), data.get("access_code"))
        if not redirect_url or not reference:
            raise BillingProviderError("Paystack did not return a payer authorization URL.")
        return BillingHandoffAuthorization(
            provider=self.name,
            status="authorization_pending",
            authorization_url=redirect_url,
            provider_reference=reference,
            provider_customer_id=(first_non_empty(customer.get("customer_code"), customer.get("id")) if customer else None),
            start_at=(start_at if start_at.tzinfo else start_at.replace(tzinfo=timezone.utc)),
            raw=payload,
        )

    def finalize_handoff_authorization(
        self,
        *,
        provider_reference: str,
        email: str,
        target_plan: BillingPlanName,
        seat_count: int,
        organization_id: int,
        new_owner_user_id: str,
        start_at: datetime,
        idempotency_key: str,
    ) -> BillingFutureSubscription:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        reference = str(provider_reference or "").strip()
        if not secret_key or not reference:
            raise CheckoutNotConfiguredError("Paystack ownership handoff authorization is not configured.")
        try:
            response = requests.get(
                "https://api.paystack.co/customer/authorization/verify/" + quote(reference, safe=""),
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError("Paystack could not verify payer authorization.") from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(str(payload.get("message") or "Paystack payer authorization verification failed."))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        authorization_code = first_non_empty(data.get("authorization_code"))
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        customer_email = str(first_non_empty(customer.get("email"), email) or "").strip().lower()
        if customer_email != str(email or "").strip().lower():
            raise BillingProviderError("Paystack payer authorization belongs to a different customer email.")
        if data.get("active") is not True or not authorization_code:
            raise BillingProviderError("Paystack payer authorization is not active yet.")
        customer_identity = str(first_non_empty(customer.get("code"), customer.get("customer_code"), customer_email) or customer_email)
        return self._create_future_handoff_subscription(
            customer_identity=customer_identity,
            authorization_code=authorization_code,
            target_plan=target_plan,
            seat_count=seat_count,
            start_at=start_at,
        )

    def _fetch_subscription(self, provider_subscription_id: str) -> dict[str, Any]:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "PAYSTACK_SECRET_KEY is required to change a Paystack subscription."
            )

        subscription_id = require_provider_subscription_id(
            provider_subscription_id
        )
        try:
            response = requests.get(
                "https://api.paystack.co/subscription/"
                f"{quote(subscription_id, safe='')}",
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to load the subscription."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(
                str(payload.get("message") or "Paystack could not load the subscription.")
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BillingProviderError(
                "Paystack returned an invalid subscription response."
            )
        return data

    def _fetch_customer(self, customer_code_or_email: str) -> dict[str, Any]:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "PAYSTACK_SECRET_KEY is required to resolve a Paystack customer."
            )
        normalized = str(customer_code_or_email or "").strip()
        if not normalized:
            raise BillingProviderError("Paystack customer identity is required.")
        try:
            response = requests.get(
                "https://api.paystack.co/customer/" + quote(normalized, safe=""),
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to resolve the subscription customer."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            message = str(
                payload.get("message")
                or "Paystack could not load the subscription customer."
            )
            normalized_message = message.strip().lower()
            if response.status_code == 404 or normalized_message in {
                "customer not found",
                "a customer with the specified email or code was not found",
            }:
                raise PaystackCustomerNotFoundError(message)
            raise BillingProviderError(message)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BillingProviderError("Paystack returned an invalid customer response.")
        return data

    def _list_customer_subscriptions(self, customer_id: int) -> list[dict[str, Any]]:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "PAYSTACK_SECRET_KEY is required to resolve Paystack subscriptions."
            )
        subscriptions: list[dict[str, Any]] = []
        page = 1
        while page <= 10:
            try:
                response = requests.get(
                    "https://api.paystack.co/subscription",
                    params={"customer": int(customer_id), "perPage": 100, "page": page},
                    headers={"Authorization": f"Bearer {secret_key}"},
                    timeout=DEFAULT_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise BillingProviderError(
                    "Paystack could not be reached to list customer subscriptions."
                ) from exc
            payload = provider_response_json(response)
            if response.status_code >= 400 or not payload.get("status"):
                raise BillingProviderError(
                    str(payload.get("message") or "Paystack could not list customer subscriptions.")
                )
            data = payload.get("data")
            if not isinstance(data, list):
                raise BillingProviderError(
                    "Paystack returned an invalid subscription-list response."
                )
            subscriptions.extend(item for item in data if isinstance(item, dict))
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            page_count = parse_int(meta.get("pageCount"))
            if page_count is None or page >= page_count or not data:
                break
            page += 1
        return subscriptions

    def _state_from_subscription_payload(
        self,
        payload: dict[str, Any],
        *,
        fallback_subscription_id: str | None = None,
    ) -> BillingSubscriptionState:
        status = str(payload.get("status") or "unknown").strip().lower()
        customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
        plan_payload = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        plan_code = first_non_empty(
            plan_payload.get("plan_code"),
            payload.get("plan_code"),
            payload.get("plan") if not isinstance(payload.get("plan"), dict) else None,
        )
        resolved_plan: BillingPlanName | None = None
        for candidate in PAID_PLANS:
            if plan_code and plan_code == env_for_plan("PAYSTACK", candidate, "PLAN_CODE"):
                resolved_plan = candidate  # type: ignore[assignment]
                break
        subscription_id = first_non_empty(
            payload.get("subscription_code"),
            payload.get("id"),
            fallback_subscription_id,
        )
        if not subscription_id:
            raise BillingProviderError(
                "Paystack subscription data is missing its subscription identifier."
            )
        return BillingSubscriptionState(
            provider=self.name,
            provider_subscription_id=str(subscription_id),
            status=status,
            provider_customer_id=first_non_empty(
                customer.get("customer_code"),
                customer.get("id"),
            ),
            current_period_start=(
                parse_timestamp((payload.get("most_recent_invoice") or {}).get("period_start"))
                if (payload.get("most_recent_invoice") or {}).get("paid") in (True, 1)
                else parse_timestamp(payload.get("start"))
            ),
            current_period_end=parse_timestamp(payload.get("next_payment_date")),
            cancel_at_period_end=status in {
                "non-renewing",
                "cancelled",
                "canceled",
                "completed",
            },
            plan=resolved_plan,
            raw=payload,
        )

    def resolve_subscription_reference(
        self,
        *,
        provider_subscription_id: str | None,
        provider_customer_id: str | None = None,
        provider_reference: str | None = None,
        expected_plan: BillingPlanName | None = None,
    ) -> PaystackSubscriptionResolution:
        stored_id = str(provider_subscription_id or "").strip()
        customer_identity = str(provider_customer_id or "").strip()
        transaction_reference = str(provider_reference or "").strip()

        if stored_id and not is_redocx_paystack_transaction_reference(stored_id):
            state = self.retrieve_subscription(stored_id)
            return PaystackSubscriptionResolution(
                outcome="resolved",
                state=state,
                provider_customer_id=state.provider_customer_id or customer_identity or None,
                provider_reference=transaction_reference or None,
                source="stored_subscription_identifier",
            )

        if not transaction_reference and is_redocx_paystack_transaction_reference(stored_id):
            transaction_reference = stored_id

        verified_event: BillingWebhookEvent | None = None
        transaction_not_found = False
        if transaction_reference:
            try:
                verified_event = self.verify_transaction(transaction_reference)
            except PaystackTransactionNotFoundError:
                transaction_not_found = True
                # Historical ReDOCX data can outlive Paystack transaction lookup
                # availability, and a missing reference can also indicate that a
                # different integration key is configured. Never infer anything
                # from the missing transaction alone. We may continue only when a
                # stored customer identity exists; the customer lookup below must
                # independently succeed on the same Paystack integration before a
                # subscription can be resolved or classified as non-recurring.
                if not customer_identity:
                    raise
                logger.warning(
                    "Paystack historical transaction reference was not found; "
                    "falling back to provider-verified customer subscription inventory."
                )
                verified_event = None

            if verified_event is not None:
                customer_identity = str(
                    verified_event.provider_customer_id or customer_identity or ""
                ).strip()
                recovered_id = str(verified_event.provider_subscription_id or "").strip()
                if recovered_id and not is_redocx_paystack_transaction_reference(recovered_id):
                    state = self.retrieve_subscription(recovered_id)
                    if expected_plan and state.plan and state.plan != expected_plan:
                        raise BillingProviderError(
                            "Recovered Paystack subscription belongs to a different ReDOCX plan."
                        )
                    return PaystackSubscriptionResolution(
                        outcome="resolved",
                        state=state,
                        provider_customer_id=state.provider_customer_id or customer_identity or None,
                        provider_reference=transaction_reference,
                        source="verified_transaction",
                    )

        customer_data: dict[str, Any] | None = None
        customer_numeric_id: int | None = None
        if customer_identity:
            if customer_identity.isdigit():
                customer_numeric_id = int(customer_identity)
            else:
                requested_customer_identity = customer_identity
                try:
                    customer_data = self._fetch_customer(customer_identity)
                except PaystackCustomerNotFoundError as exc:
                    if transaction_not_found or is_redocx_paystack_transaction_reference(stored_id):
                        raise PaystackLegacyBindingUnresolvableError(
                            "Paystack cannot find either the historical ReDOCX transaction "
                            "or its stored customer on the current integration. Verify that "
                            "the production PAYSTACK_SECRET_KEY belongs to the same live/test "
                            "Paystack integration that created this billing record."
                        ) from exc
                    raise
                returned_customer_code = str(
                    first_non_empty(customer_data.get("customer_code")) or ""
                ).strip()
                if (
                    requested_customer_identity.upper().startswith("CUS_")
                    and (
                        not returned_customer_code
                        or not constant_time_equals(
                            requested_customer_identity, returned_customer_code
                        )
                    )
                ):
                    raise BillingProviderError(
                        "Paystack returned a different customer while resolving a legacy subscription."
                    )
                customer_numeric_id = parse_int(customer_data.get("id"))
                customer_identity = str(
                    first_non_empty(returned_customer_code, requested_customer_identity) or ""
                ).strip()

        if customer_numeric_id is None and verified_event is not None:
            raw_data = (
                verified_event.raw.get("data")
                if isinstance(verified_event.raw.get("data"), dict)
                else {}
            )
            raw_customer = (
                raw_data.get("customer")
                if isinstance(raw_data.get("customer"), dict)
                else {}
            )
            customer_numeric_id = parse_int(raw_customer.get("id"))
            if not customer_identity:
                customer_identity = str(
                    first_non_empty(raw_customer.get("customer_code"), raw_customer.get("id")) or ""
                ).strip()

        if customer_numeric_id is None:
            raise BillingProviderError(
                "Paystack subscription reconciliation could not resolve the customer safely."
            )

        subscriptions = self._list_customer_subscriptions(customer_numeric_id)

        historical_plan_code: str | None = None
        if verified_event is not None:
            raw_data = (
                verified_event.raw.get("data")
                if isinstance(verified_event.raw.get("data"), dict)
                else {}
            )
            raw_plan = (
                raw_data.get("plan_object")
                if isinstance(raw_data.get("plan_object"), dict)
                else {}
            )
            # The transaction's own provider-verified plan code is authoritative
            # for legacy recovery. Current environment plan codes may have changed
            # since an older customer originally subscribed.
            historical_plan_code = first_non_empty(
                raw_plan.get("plan_code"),
                raw_data.get("plan") if isinstance(raw_data.get("plan"), str) else None,
            )
        configured_plan_code = (
            env_for_plan("PAYSTACK", expected_plan, "PLAN_CODE")
            if expected_plan in PAID_PLANS
            else None
        )

        all_candidates: list[dict[str, Any]] = []
        for item in subscriptions:
            if str(item.get("subscription_code") or "").strip():
                all_candidates.append(item)

        def candidate_plan_code(item: dict[str, Any]) -> str | None:
            plan_payload = item.get("plan") if isinstance(item.get("plan"), dict) else {}
            return first_non_empty(plan_payload.get("plan_code"), item.get("plan_code"))

        if historical_plan_code:
            candidates = [
                item
                for item in all_candidates
                if candidate_plan_code(item) == historical_plan_code
            ]
        elif configured_plan_code:
            configured_matches = [
                item
                for item in all_candidates
                if candidate_plan_code(item) == configured_plan_code
            ]
            candidates = configured_matches if configured_matches else []
        else:
            candidates = list(all_candidates)

        ongoing_statuses = {"active", "non-renewing", "attention", "past-due", "past_due"}
        terminal_statuses = {"cancelled", "canceled", "completed"}

        # A current environment plan code is only a hint for old customers. If it
        # does not match but Paystack still shows a live subscription on the same
        # customer, never conclude "non-recurring"; the historical plan may have
        # been replaced in configuration. Require manual resolution instead.
        if configured_plan_code and not historical_plan_code and not candidates:
            other_ongoing = [
                item
                for item in all_candidates
                if str(item.get("status") or "").strip().lower() in ongoing_statuses
            ]
            if other_ongoing:
                raise BillingProviderError(
                    "Paystack shows a live customer subscription, but it cannot be matched safely to the historical ReDOCX plan."
                )

        ongoing = [
            item
            for item in candidates
            if str(item.get("status") or "").strip().lower() in ongoing_statuses
        ]
        if len(ongoing) > 1:
            raise BillingProviderError(
                "Multiple matching Paystack subscriptions exist for this customer; automatic reconciliation is unsafe."
            )
        if len(ongoing) == 1:
            state = self._state_from_subscription_payload(ongoing[0])
            return PaystackSubscriptionResolution(
                outcome="resolved",
                state=state,
                provider_customer_id=state.provider_customer_id or customer_identity or None,
                provider_reference=transaction_reference or None,
                source="customer_subscription_inventory",
            )

        unknown = [
            item
            for item in candidates
            if str(item.get("status") or "").strip().lower() not in terminal_statuses
        ]
        if unknown:
            raise BillingProviderError(
                "Paystack returned a matching subscription in an unrecognized state; automatic reconciliation is unsafe."
            )

        if len(candidates) == 1:
            state = self._state_from_subscription_payload(candidates[0])
            return PaystackSubscriptionResolution(
                outcome="resolved",
                state=state,
                provider_customer_id=state.provider_customer_id or customer_identity or None,
                provider_reference=transaction_reference or None,
                source="terminal_customer_subscription",
            )

        return PaystackSubscriptionResolution(
            outcome="not_recurring",
            state=None,
            provider_customer_id=customer_identity or None,
            provider_reference=transaction_reference or None,
            source=(
                "no_matching_customer_subscription"
                if subscriptions
                else "customer_has_no_subscriptions"
            ),
        )

    def retrieve_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionState:
        payload = self._fetch_subscription(provider_subscription_id)
        return self._state_from_subscription_payload(
            payload,
            fallback_subscription_id=provider_subscription_id,
        )

    def _set_enabled(
        self,
        provider_subscription_id: str,
        *,
        enabled: bool,
    ) -> BillingSubscriptionChange:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise BillingProviderError(
                "PAYSTACK_SECRET_KEY is required to change a Paystack subscription."
            )

        subscription = self._fetch_subscription(provider_subscription_id)
        subscription_id = str(
            subscription.get("subscription_code") or provider_subscription_id
        ).strip()
        current_status = str(subscription.get("status") or "").strip().lower()
        terminal_statuses = {"cancelled", "canceled", "completed"}

        if enabled and current_status == "active":
            return BillingSubscriptionChange(
                provider=self.name,
                provider_subscription_id=subscription_id,
                status="active",
                current_period_end=parse_timestamp(
                    subscription.get("next_payment_date")
                ),
            )
        if not enabled and current_status in {
            "non-renewing",
            *terminal_statuses,
        }:
            return BillingSubscriptionChange(
                provider=self.name,
                provider_subscription_id=subscription_id,
                status=(
                    current_status
                    if current_status in terminal_statuses
                    else "cancellation_scheduled"
                ),
                current_period_end=parse_timestamp(
                    subscription.get("next_payment_date")
                ),
            )

        email_token = str(subscription.get("email_token") or "").strip()
        if not email_token:
            raise BillingProviderError(
                "Paystack did not return the email token required to change this subscription."
            )

        try:
            response = requests.post(
                "https://api.paystack.co/subscription/"
                f"{'enable' if enabled else 'disable'}",
                json={"code": subscription_id, "token": email_token},
                headers={
                    "Authorization": f"Bearer {secret_key}",
                    "Content-Type": "application/json",
                },
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to change the subscription."
            ) from exc
        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            raise BillingProviderError(
                str(
                    payload.get("message")
                    or (
                        "Paystack could not resume the subscription."
                        if enabled
                        else "Paystack could not stop subscription renewal."
                    )
                )
            )

        return BillingSubscriptionChange(
            provider=self.name,
            provider_subscription_id=subscription_id,
            status="active" if enabled else "cancellation_scheduled",
            current_period_end=parse_timestamp(
                subscription.get("next_payment_date")
            ),
        )

    def cancel_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        return self._set_enabled(provider_subscription_id, enabled=False)

    def resume_subscription(
        self,
        provider_subscription_id: str,
    ) -> BillingSubscriptionChange:
        return self._set_enabled(provider_subscription_id, enabled=True)

    @staticmethod
    def _plan_from_provider_values(*values: Any) -> BillingPlanName | None:
        plan_codes: list[str] = []
        for value in values:
            if isinstance(value, dict):
                candidate = first_non_empty(
                    value.get("plan_code"),
                    value.get("code"),
                )
            else:
                candidate = first_non_empty(value)
            if candidate and candidate.startswith("PLN_"):
                plan_codes.append(candidate)

        for candidate in ("personal", "business", "enterprise"):
            configured_code = env_for_plan("PAYSTACK", candidate, "PLAN_CODE")
            if configured_code and configured_code in plan_codes:
                return candidate  # type: ignore[return-value]
        return None

    def verify_transaction(self, reference: str) -> BillingWebhookEvent:
        """Verify a Paystack callback reference directly with Paystack.

        This is the authenticated callback fallback for a delayed webhook. It
        returns the same normalized event contract used by webhook processing;
        it does not grant an entitlement on its own.
        """
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        normalized_reference = str(reference or "").strip()
        if not secret_key:
            raise BillingProviderError(
                "PAYSTACK_SECRET_KEY is required to verify a transaction."
            )
        if not normalized_reference:
            raise BillingProviderError("Paystack transaction reference is required.")

        try:
            response = requests.get(
                "https://api.paystack.co/transaction/verify/"
                f"{quote(normalized_reference, safe='')}",
                headers={"Authorization": f"Bearer {secret_key}"},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise BillingProviderError(
                "Paystack could not be reached to verify the transaction."
            ) from exc

        payload = provider_response_json(response)
        if response.status_code >= 400 or not payload.get("status"):
            message = str(
                payload.get("message") or "Paystack transaction verification failed."
            )
            normalized_message = message.strip().lower().rstrip(".")
            if normalized_message in {
                "transaction reference not found",
                "transaction not found",
            }:
                raise PaystackTransactionNotFoundError(message)
            raise BillingProviderError(message)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise BillingProviderError(
                "Paystack returned an invalid transaction verification response."
            )

        returned_reference = first_non_empty(data.get("reference"))
        if not constant_time_equals(normalized_reference, returned_reference):
            raise BillingProviderError(
                "Paystack returned a different transaction reference."
            )

        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        subscription = (
            data.get("subscription")
            if isinstance(data.get("subscription"), dict)
            else {}
        )
        plan_object = (
            data.get("plan_object")
            if isinstance(data.get("plan_object"), dict)
            else {}
        )
        plan_value = data.get("plan")
        metadata = merged_metadata_from(
            data.get("metadata"),
            customer.get("metadata"),
            subscription.get("metadata"),
        )
        provider_subscription_id = first_non_empty(
            subscription.get("subscription_code"),
            data.get("subscription_code"),
            data.get("subscription")
            if not isinstance(data.get("subscription"), dict)
            else None,
        )

        raw_body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        transaction_identity = first_non_empty(data.get("id"), returned_reference)
        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=(
                f"charge.success:{transaction_identity}"
                if transaction_identity
                else None
            ),
            event_type="charge.success",
            data=data,
            metadata=metadata,
            plan=self._plan_from_provider_values(
                plan_value,
                plan_object,
                subscription.get("plan"),
            ),
            status=data.get("status"),
            email=customer.get("email") or data.get("email"),
            provider_customer_id=customer.get("customer_code") or customer.get("id"),
            provider_subscription_id=provider_subscription_id,
            provider_reference=returned_reference,
            current_period_start=data.get("paid_at") or data.get("paidAt"),
            current_period_end=subscription.get("next_payment_date"),
            amount=data.get("amount"),
            currency=data.get("currency"),
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        if not secret_key:
            raise WebhookVerificationError("PAYSTACK_SECRET_KEY is required.")

        incoming = header_value(headers, "x-paystack-signature")
        expected = signed_sha(raw_body, secret_key, "sha512")
        if not constant_time_equals(incoming, expected):
            raise WebhookVerificationError("Invalid Paystack webhook signature.")

        payload = load_json_body(raw_body)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        event_type = str(payload.get("event") or "").strip().lower()
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        nested_subscription = (
            data.get("subscription")
            if isinstance(data.get("subscription"), dict)
            else {}
        )
        subscription = (
            nested_subscription
            if nested_subscription
            else data
            if event_type.startswith("subscription.")
            else {}
        )
        authorization = data.get("authorization") if isinstance(data.get("authorization"), dict) else {}
        nested_transaction = (
            data.get("transaction")
            if isinstance(data.get("transaction"), dict)
            else {}
        )
        transaction = (
            nested_transaction
            if nested_transaction
            else data
            if event_type == "charge.success"
            else {}
        )
        plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
        plan_object = (
            data.get("plan_object")
            if isinstance(data.get("plan_object"), dict)
            else {}
        )
        metadata = merged_metadata_from(
            data.get("metadata"),
            data.get("meta"),
            transaction.get("metadata"),
            transaction.get("meta"),
            subscription.get("metadata"),
            subscription.get("meta"),
        )

        plan_from_code = self._plan_from_provider_values(
            subscription.get("plan"),
            subscription.get("plan_code"),
            plan,
            plan_object,
            data.get("plan"),
            transaction.get("plan"),
            transaction.get("plan_object"),
        )

        dispute_resolution = str(
            first_non_empty(
                data.get("resolution"),
                data.get("status"),
                data.get("result"),
            )
            or ""
        ).strip().lower().replace(" ", "_")
        action_override: WebhookAction | None = None
        if event_type in {"invoice.create", "subscription.expiring_cards"}:
            # An invoice announced before the due date is not proof of payment.
            action_override = "ignore"
        elif event_type == "invoice.update":
            action_override = (
                "activate" if data.get("paid") in (True, 1)
                and str(transaction.get("status") or "").lower() == "success"
                else "ignore"
            )
        elif event_type == "refund.processed":
            refund_amount = parse_int(data.get("amount"))
            transaction_amount = parse_int(
                transaction.get("amount")
                or data.get("transaction_amount")
                or data.get("original_amount")
            )
            action_override = (
                "ignore"
                if refund_amount is not None
                and transaction_amount is not None
                and refund_amount < transaction_amount
                else "revoke"
            )
        elif event_type in {"charge.dispute.create", "charge.dispute.remind"}:
            action_override = "suspend"
        elif event_type == "charge.dispute.resolve":
            if dispute_resolution in {
                "won",
                "merchant_won",
                "resolved_in_merchant_favour",
                "resolved_in_merchant_favor",
            }:
                action_override = "restore"
            elif dispute_resolution in {
                "lost",
                "customer_won",
                "chargeback",
                "accepted",
            }:
                action_override = "revoke"
            else:
                action_override = "ignore"

        event_identity = first_non_empty(
            data.get("id"),
            data.get("reference"),
            transaction.get("reference"),
            subscription.get("subscription_code"),
        )
        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=(f"{event_type or 'unknown'}:{event_identity}" if event_identity else None),
            event_type=payload.get("event"),
            data=data,
            metadata=metadata,
            plan=plan_from_code,
            status=data.get("status") or subscription.get("status"),
            email=customer.get("email") or data.get("email"),
            provider_customer_id=customer.get("customer_code") or customer.get("id"),
            provider_subscription_id=subscription.get("subscription_code")
            or data.get("subscription_code")
            or (
                data.get("subscription")
                if isinstance(data.get("subscription"), str)
                else None
            ),
            provider_reference=data.get("reference")
            or transaction.get("reference")
            or subscription.get("email_token")
            or authorization.get("authorization_code"),
            current_period_start=data.get("period_start")
            or transaction.get("period_start"),
            current_period_end=data.get("period_end")
            or subscription.get("next_payment_date")
            or data.get("next_payment_date"),
            amount=data.get("amount") or transaction.get("amount"),
            currency=data.get("currency") or transaction.get("currency"),
            action=action_override,
        )


class FlutterwaveBillingProvider(BaseBillingProvider):
    name = "flutterwave"

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        secret_key = os.getenv("FLUTTERWAVE_SECRET_KEY", "").strip()
        redirect_url = request.success_url or os.getenv("FLUTTERWAVE_REDIRECT_URL", "").strip() or DEFAULT_SUCCESS_URL
        amount = env_for_plan("FLUTTERWAVE", request.target_plan, "AMOUNT")
        currency = env_for_plan("FLUTTERWAVE", request.target_plan, "CURRENCY") or DEFAULT_CURRENCY

        if not secret_key or not redirect_url or not amount:
            raise CheckoutNotConfiguredError(
                f"Flutterwave checkout requires FLUTTERWAVE_SECRET_KEY, redirect URL, and FLUTTERWAVE_{request.target_plan.upper()}_AMOUNT."
            )
        if not request.email:
            raise CheckoutNotConfiguredError("Flutterwave requires an email address to initialize checkout.")

        tx_ref = f"redocx-{request.target_plan}-{request.user_id}-{int(datetime.now(tz=timezone.utc).timestamp())}"
        body = {
            "tx_ref": tx_ref,
            "amount": str(amount),
            "currency": currency,
            "redirect_url": redirect_url,
            "customer": {
                "email": request.email,
            },
            "meta": plan_metadata(request),
        }

        payment_plan = env_for_plan("FLUTTERWAVE", request.target_plan, "PAYMENT_PLAN")
        if payment_plan:
            body["payment_plan"] = payment_plan

        response = requests.post(
            "https://api.flutterwave.com/v3/payments",
            json=body,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or payload.get("status") not in {"success", "successful"}:
            raise BillingProviderError(payload.get("message") or "Flutterwave checkout failed.")

        data = payload.get("data") or {}
        checkout_url = data.get("link")
        if not checkout_url:
            raise BillingProviderError("Flutterwave did not return a payment link.")

        return BillingCheckoutSession(
            provider=self.name,
            target_plan=request.target_plan,
            checkout_url=checkout_url,
            provider_session_id=data.get("id"),
            reference=tx_ref,
            raw=payload,
        )

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
        secret_hash = os.getenv("FLUTTERWAVE_SECRET_HASH", "").strip()
        if not secret_hash:
            raise WebhookVerificationError("FLUTTERWAVE_SECRET_HASH is required.")

        incoming = header_value(headers, "verif-hash")
        if not constant_time_equals(incoming, secret_hash):
            raise WebhookVerificationError("Invalid Flutterwave webhook secret hash.")

        payload = load_json_body(raw_body)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        metadata = metadata_from(data.get("meta") or data.get("metadata") or payload.get("meta"))
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}

        payment_plan_code = first_non_empty(data.get("payment_plan"), data.get("plan"))
        plan_from_code = None
        if payment_plan_code:
            for candidate in PAID_PLANS:
                if payment_plan_code == env_for_plan("FLUTTERWAVE", candidate, "PAYMENT_PLAN"):
                    plan_from_code = candidate
                    break

        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=data.get("id") or data.get("tx_ref") or data.get("flw_ref"),
            event_type=payload.get("event") or payload.get("type"),
            data=data,
            metadata=metadata,
            plan=plan_from_code,
            status=data.get("status"),
            email=customer.get("email"),
            provider_customer_id=customer.get("id") or customer.get("customer_code"),
            provider_subscription_id=data.get("subscription_id") or data.get("subscription") or data.get("payment_plan"),
            provider_reference=data.get("tx_ref") or data.get("flw_ref"),
            amount=data.get("amount"),
            currency=data.get("currency"),
        )


PROVIDER_CLASSES: dict[str, type[BaseBillingProvider]] = {
    "static": StaticCheckoutProvider,
    "generic": GenericHmacProvider,
    "stripe": StripeBillingProvider,
    "paystack": PaystackBillingProvider,
    "flutterwave": FlutterwaveBillingProvider,
}


def get_billing_provider(provider_name: str | None = None) -> BaseBillingProvider:
    normalized = normalize_provider_name(provider_name)
    provider_cls = PROVIDER_CLASSES.get(normalized)
    if provider_cls is None:
        raise BillingProviderError(
            f"Unsupported billing provider '{normalized}'. Supported providers: {', '.join(sorted(PROVIDER_CLASSES))}."
        )
    return provider_cls()


def create_checkout_session(request: BillingCheckoutRequest, provider_name: str | None = None) -> BillingCheckoutSession:
    provider = get_billing_provider(provider_name)
    return provider.create_checkout_session(request)


def cancel_provider_subscription(
    provider_name: str,
    provider_subscription_id: str,
) -> BillingSubscriptionChange:
    from backend.billing_collection import managed_change, preserve_paid_end
    managed = managed_change(provider_name, provider_subscription_id, resume=False)
    if managed is not None:
        return managed
    provider = get_billing_provider(provider_name)
    return preserve_paid_end(provider_name, provider_subscription_id, provider.cancel_subscription(provider_subscription_id))


def resume_provider_subscription(
    provider_name: str,
    provider_subscription_id: str,
) -> BillingSubscriptionChange:
    from backend.billing_collection import managed_change
    managed = managed_change(provider_name, provider_subscription_id, resume=True)
    if managed is not None:
        return managed
    provider = get_billing_provider(provider_name)
    return provider.resume_subscription(provider_subscription_id)


def retrieve_provider_subscription(
    provider_name: str,
    provider_subscription_id: str,
) -> BillingSubscriptionState:
    from backend.billing_collection import managed_state, recovered_state
    managed = managed_state(provider_name, provider_subscription_id)
    if managed is not None:
        return managed
    provider = get_billing_provider(provider_name)
    state = provider.retrieve_subscription(provider_subscription_id)
    return recovered_state(provider_name, provider_subscription_id, state)


def resolve_paystack_subscription_reference(
    *,
    provider_subscription_id: str | None,
    provider_customer_id: str | None = None,
    provider_reference: str | None = None,
    expected_plan: BillingPlanName | None = None,
) -> PaystackSubscriptionResolution:
    provider = get_billing_provider("paystack")
    if not isinstance(provider, PaystackBillingProvider):
        raise BillingProviderError("Paystack provider adapter is unavailable.")
    return provider.resolve_subscription_reference(
        provider_subscription_id=provider_subscription_id,
        provider_customer_id=provider_customer_id,
        provider_reference=provider_reference,
        expected_plan=expected_plan,
    )


def change_provider_subscription_plan(
    provider_name: str,
    provider_subscription_id: str,
    *,
    target_plan: BillingPlanName,
    metadata: Mapping[str, Any] | None = None,
    quantity: int | None = None,
    idempotency_key: str | None = None,
    effective_at_period_end: bool = False,
) -> BillingSubscriptionChange:
    provider = get_billing_provider(provider_name)
    return provider.change_subscription_plan(
        provider_subscription_id,
        target_plan=target_plan,
        metadata=metadata,
        quantity=quantity,
        idempotency_key=idempotency_key,
        effective_at_period_end=effective_at_period_end,
    )


def initialize_provider_handoff_authorization(
    provider_name: str,
    *,
    email: str,
    target_plan: BillingPlanName,
    seat_count: int,
    organization_id: int,
    new_owner_user_id: str,
    start_at: datetime,
    return_url: str,
    idempotency_key: str,
) -> BillingHandoffAuthorization:
    provider = get_billing_provider(provider_name)
    method = getattr(provider, "initialize_handoff_authorization", None)
    if method is None:
        raise BillingProviderError(f"Ownership billing handoff is not supported by provider '{provider_name}'.")
    return method(
        email=email,
        target_plan=target_plan,
        seat_count=seat_count,
        organization_id=organization_id,
        new_owner_user_id=new_owner_user_id,
        start_at=start_at,
        return_url=return_url,
        idempotency_key=idempotency_key,
    )


def finalize_provider_handoff_authorization(
    provider_name: str,
    *,
    provider_reference: str,
    email: str,
    target_plan: BillingPlanName,
    seat_count: int,
    organization_id: int,
    new_owner_user_id: str,
    start_at: datetime,
    idempotency_key: str,
) -> BillingFutureSubscription:
    provider = get_billing_provider(provider_name)
    method = getattr(provider, "finalize_handoff_authorization", None)
    if method is None:
        raise BillingProviderError(f"Ownership billing handoff is not supported by provider '{provider_name}'.")
    return method(
        provider_reference=provider_reference,
        email=email,
        target_plan=target_plan,
        seat_count=seat_count,
        organization_id=organization_id,
        new_owner_user_id=new_owner_user_id,
        start_at=start_at,
        idempotency_key=idempotency_key,
    )


def verify_provider_webhook(provider_name: str, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
    provider = get_billing_provider(provider_name)
    return provider.verify_webhook(raw_body, headers)


def verify_provider_transaction(
    provider_name: str,
    reference: str,
) -> BillingWebhookEvent:
    provider = get_billing_provider(provider_name)
    return provider.verify_transaction(reference)


__all__ = [
    "BillingCheckoutRequest",
    "BillingCheckoutSession",
    "BillingProviderError",
    "PLAN_UNIT_AMOUNT_KOBO",
    "plan_unit_amount_kobo",
    "expected_checkout_amount_kobo",
    "validate_checkout_seat_count",
    "BillingSubscriptionChange",
    "BillingSubscriptionState",
    "BillingHandoffAuthorization",
    "BillingFutureSubscription",
    "BillingWebhookEvent",
    "PaystackSubscriptionResolution",
    "PaystackTransactionNotFoundError",
    "PaystackCustomerNotFoundError",
    "PaystackLegacyBindingUnresolvableError",
    "CheckoutNotConfiguredError",
    "ProviderName",
    "WebhookVerificationError",
    "cancel_provider_subscription",
    "change_provider_subscription_plan",
    "create_checkout_session",
    "get_billing_provider",
    "initialize_provider_handoff_authorization",
    "finalize_provider_handoff_authorization",
    "is_redocx_paystack_transaction_reference",
    "resolve_paystack_subscription_reference",
    "resume_provider_subscription",
    "retrieve_provider_subscription",
    "verify_provider_transaction",
    "verify_provider_webhook",
]
