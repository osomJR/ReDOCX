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
from typing import Any, Literal, Mapping
from urllib.parse import urlencode

import requests

BillingPlanName = Literal["free", "personal", "business", "enterprise"]
ProviderName = Literal["static", "generic", "stripe", "paystack", "flutterwave"]
WebhookAction = Literal["activate", "cancel", "past_due", "ignore"]

PAID_PLANS: set[str] = {"personal", "business", "enterprise"}
ORGANIZATION_PLANS: set[str] = {"business", "enterprise"}

DEFAULT_TIMEOUT_SECONDS = float(os.getenv("BILLING_PROVIDER_TIMEOUT_SECONDS", "15"))
DEFAULT_CURRENCY = os.getenv("BILLING_DEFAULT_CURRENCY", "NGN").strip().upper() or "NGN"
DEFAULT_SUCCESS_URL = os.getenv("BILLING_SUCCESS_URL", "").strip()
DEFAULT_CANCEL_URL = os.getenv("BILLING_CANCEL_URL", "").strip()


class BillingProviderError(RuntimeError):
    """Raised when the billing provider cannot complete a requested action."""


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
    success_url: str | None = None
    cancel_url: str | None = None
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
    }
    past_due_events = {
        "invoice.payment_failed",
        "charge.failed",
        "payment.failed",
        "subscription.not_renew",
        "subscription.payment_failed",
    }

    if event in cancel_events or normalized_status in {"cancelled", "canceled", "disabled"}:
        return "cancel"
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
        action=action_from_event(resolved_event_type, resolved_status),
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

        metadata = {key: str(value) for key, value in plan_metadata(request).items()}
        form: list[tuple[str, str]] = [
            ("mode", "subscription"),
            ("success_url", success_url),
            ("cancel_url", cancel_url),
            ("client_reference_id", request.user_id),
            ("line_items[0][price]", price_id),
            ("line_items[0][quantity]", "1"),
            ("allow_promotion_codes", os.getenv("STRIPE_ALLOW_PROMOTION_CODES", "false").strip().lower() in {"1", "true", "yes", "on"} and "true" or "false"),
        ]
        if request.email:
            form.append(("customer_email", request.email))
        for key, value in metadata.items():
            # Checkout-session metadata lets checkout.session.completed activate quickly.
            form.append((f"metadata[{key}]", value))
            # Subscription metadata lets customer.subscription.* events update/cancel later.
            form.append((f"subscription_data[metadata][{key}]", value))

        response = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            auth=(secret_key, ""),
            data=form,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            raise BillingProviderError(payload.get("error", {}).get("message") or "Stripe checkout failed.")

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

        parent = obj.get("parent") if isinstance(obj.get("parent"), dict) else {}
        subscription_details = obj.get("subscription_details") if isinstance(obj.get("subscription_details"), dict) else {}
        metadata = merged_metadata_from(
            obj.get("metadata"),
            subscription_details.get("metadata"),
            parent.get("subscription_details", {}).get("metadata") if isinstance(parent.get("subscription_details"), dict) else None,
        )

        status = obj.get("status") or obj.get("payment_status")
        event_text = str(event_type or "")
        if event_text.startswith("customer.subscription"):
            subscription = obj.get("id")
        else:
            subscription = obj.get("subscription") or subscription_details.get("subscription")

        customer_details = obj.get("customer_details") if isinstance(obj.get("customer_details"), dict) else {}

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
            provider_customer_id=obj.get("customer"),
            provider_subscription_id=subscription,
            provider_reference=obj.get("id") or obj.get("payment_intent") or obj.get("invoice"),
            current_period_start=obj.get("current_period_start"),
            current_period_end=obj.get("current_period_end"),
            amount=obj.get("amount_total") or obj.get("amount_paid") or obj.get("amount_due"),
            currency=obj.get("currency"),
        )


class PaystackBillingProvider(BaseBillingProvider):
    name = "paystack"

    def create_checkout_session(self, request: BillingCheckoutRequest) -> BillingCheckoutSession:
        secret_key = os.getenv("PAYSTACK_SECRET_KEY", "").strip()
        callback_url = request.success_url or os.getenv("PAYSTACK_CALLBACK_URL", "").strip() or DEFAULT_SUCCESS_URL
        plan_code = env_for_plan("PAYSTACK", request.target_plan, "PLAN_CODE")
        amount = env_for_plan("PAYSTACK", request.target_plan, "AMOUNT_KOBO")

        if not secret_key or not callback_url:
            raise CheckoutNotConfiguredError("Paystack checkout requires PAYSTACK_SECRET_KEY and a callback URL.")
        if not plan_code and not amount:
            raise CheckoutNotConfiguredError(
                f"Set PAYSTACK_{request.target_plan.upper()}_PLAN_CODE or PAYSTACK_{request.target_plan.upper()}_AMOUNT_KOBO."
            )
        if not request.email:
            raise CheckoutNotConfiguredError("Paystack requires an email address to initialize checkout.")

        timestamp = int(datetime.now(tz=timezone.utc).timestamp())
        reference_seed = f"{request.user_id}:{request.target_plan}:{timestamp}"
        reference = f"redocx-{request.target_plan}-{hashlib.sha256(reference_seed.encode('utf-8')).hexdigest()[:20]}"

        body: dict[str, Any] = {
            "email": request.email,
            "callback_url": callback_url,
            "reference": reference,
            "metadata": plan_metadata(request),
        }
        if plan_code:
            body["plan"] = plan_code
        elif amount:
            body["amount"] = int(amount)

        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            json=body,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400 or not payload.get("status"):
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
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}
        authorization = data.get("authorization") if isinstance(data.get("authorization"), dict) else {}
        transaction = data.get("transaction") if isinstance(data.get("transaction"), dict) else {}
        plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
        metadata = merged_metadata_from(
            data.get("metadata"),
            data.get("meta"),
            transaction.get("metadata"),
            transaction.get("meta"),
            subscription.get("metadata"),
            subscription.get("meta"),
        )

        plan_from_code = None
        plan_code = first_non_empty(
            subscription.get("plan"),
            subscription.get("plan_code"),
            plan.get("plan_code"),
            data.get("plan"),
            transaction.get("plan"),
        )
        if plan_code:
            for candidate in PAID_PLANS:
                if plan_code == env_for_plan("PAYSTACK", candidate, "PLAN_CODE"):
                    plan_from_code = candidate
                    break

        return normalize_event_from_parts(
            provider=self.name,
            raw_body=raw_body,
            payload=payload,
            event_id=data.get("id") or data.get("reference") or subscription.get("subscription_code"),
            event_type=payload.get("event"),
            data=data,
            metadata=metadata,
            plan=plan_from_code,
            status=data.get("status") or subscription.get("status"),
            email=customer.get("email") or data.get("email"),
            provider_customer_id=customer.get("customer_code") or customer.get("id"),
            provider_subscription_id=subscription.get("subscription_code") or data.get("subscription_code"),
            provider_reference=data.get("reference") or subscription.get("email_token") or authorization.get("authorization_code"),
            amount=data.get("amount") or transaction.get("amount"),
            currency=data.get("currency") or transaction.get("currency"),
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


def verify_provider_webhook(provider_name: str, raw_body: bytes, headers: Mapping[str, Any]) -> BillingWebhookEvent:
    provider = get_billing_provider(provider_name)
    return provider.verify_webhook(raw_body, headers)


__all__ = [
    "BillingCheckoutRequest",
    "BillingCheckoutSession",
    "BillingProviderError",
    "BillingWebhookEvent",
    "CheckoutNotConfiguredError",
    "ProviderName",
    "WebhookVerificationError",
    "create_checkout_session",
    "get_billing_provider",
    "verify_provider_webhook",
]
