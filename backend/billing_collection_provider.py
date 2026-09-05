"""Provider IO for collection jobs. No entitlement writes and no stored card tokens."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.parse import quote, urlencode

import requests

from backend.billing_provider import (
    BillingProviderError,
    get_billing_provider,
    parse_timestamp,
)

TIMEOUT = (5, 20)


@dataclass(frozen=True)
class PaymentResult:
    status: str
    provider_id: str | None = None
    amount: int | None = None
    currency: str | None = None
    customer: str | None = None
    reference: str | None = None
    paid_at: datetime | None = None
    code: str = ""
    message: str = ""
    checkout_url: str | None = None


def _call(provider: str, method: str, path: str, *, key: str | None = None, **kwargs):
    secret = os.getenv(f"{provider.upper()}_SECRET_KEY", "").strip()
    if not secret:
        raise BillingProviderError(f"{provider.upper()}_SECRET_KEY is required.")
    headers = {"Idempotency-Key": key} if key and provider == "stripe" else {}
    if provider == "paystack":
        headers["Authorization"] = f"Bearer {secret}"
        base = "https://api.paystack.co"
    else:
        kwargs["auth"] = (secret, "")
        base = "https://api.stripe.com/v1"
    try:
        response = requests.request(
            method, base + path, headers=headers, timeout=TIMEOUT, **kwargs
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        # An IO failure is an unknown outcome, never evidence that a debit failed.
        raise BillingProviderError(
            "Provider outcome is not yet known; verification is pending."
        ) from exc
    if not isinstance(payload, dict):
        raise BillingProviderError("Provider returned an invalid response.")
    if response.status_code >= 500 or response.status_code == 429:
        raise BillingProviderError(
            "Provider is temporarily unavailable; verification will retry."
        )
    return response.status_code, payload


def native_subscription(provider: str, subscription_id: str):
    # Deliberately bypass collection-aware public wrappers.
    return get_billing_provider(provider).retrieve_subscription(subscription_id)


def stop_native(provider: str, subscription_id: str):
    return get_billing_provider(provider).cancel_subscription(subscription_id)


def payer(provider: str, subscription_id: str) -> dict[str, Any]:
    state = native_subscription(provider, subscription_id)
    raw = state.raw
    customer_id = state.provider_customer_id
    if not customer_id:
        raise BillingProviderError("A verified provider customer is required.")
    if provider == "paystack":
        customer = raw.get("customer") or {}
        authorization = raw.get("authorization") or {}
        return {
            "customer": customer_id,
            "email": customer.get("email"),
            "authorization": (
                authorization.get("authorization_code")
                if authorization.get("reusable") is True
                else None
            ),
            "state": state,
        }
    status, customer = _call(
        provider, "GET", f"/customers/{quote(customer_id, safe='')}"
    )
    if status >= 400 or customer.get("deleted"):
        raise BillingProviderError("The billing customer could not be verified.")
    default_pm = (customer.get("invoice_settings") or {}).get("default_payment_method")
    pm = default_pm or raw.get("default_payment_method")
    if isinstance(pm, dict):
        pm = pm.get("id")
    return {
        "customer": customer_id,
        "email": customer.get("email"),
        "authorization": pm,
        "state": state,
    }


def payment_authorization(provider: str, reference: str, identity: dict) -> dict:
    """Use the card from a verified, customer-authorized recovery checkout.

    Persist only the receipt reference in ReDOCX. Retrieve the reusable token
    from the provider when needed and never include it in logs or API output.
    """
    if provider == "paystack":
        status, payload = _call(
            provider, "GET", f"/transaction/verify/{quote(reference, safe='')}"
        )
        data = payload.get("data") or {}
        if (
            status >= 400
            or payload.get("status") is not True
            or data.get("status") != "success"
            or (data.get("customer") or {}).get("customer_code") != identity["customer"]
        ):
            raise BillingProviderError(
                "The replacement payment authorization could not be verified."
            )
        authorization = data.get("authorization") or {}
        return {
            **identity,
            "authorization": (
                authorization.get("authorization_code")
                if authorization.get("reusable") is True
                else None
            ),
        }
    status, data = _call(
        provider, "GET", f"/payment_intents/{quote(reference, safe='')}"
    )
    if (
        status >= 400
        or data.get("status") != "succeeded"
        or data.get("customer") != identity["customer"]
    ):
        raise BillingProviderError(
            "The replacement payment authorization could not be verified."
        )
    method = data.get("payment_method")
    return {
        **identity,
        "authorization": method.get("id") if isinstance(method, dict) else method,
    }


def close_unconfirmed_payment(provider: str, attempt: dict) -> PaymentResult:
    """Close an uncompleted Stripe authentication before offering a new checkout."""
    result = lookup(provider, attempt)
    if (
        provider == "stripe"
        and result.status == "requires_action"
        and result.provider_id
    ):
        _call(
            provider,
            "POST",
            f"/payment_intents/{quote(result.provider_id, safe='')}/cancel",
            key=attempt["reference"] + "-cancel",
        )
        return lookup(provider, {**attempt, "provider_id": result.provider_id})
    return result


def _paystack_result(data: dict) -> PaymentResult:
    status = str(data.get("status") or "pending").lower()
    normalized = {
        "success": "success",
        "failed": "failed",
        "abandoned": "failed",
        "reversed": "review",
    }.get(status, "pending")
    if status in {"send_otp", "send_pin", "send_birthday", "open_url"}:
        normalized = "requires_action"
    customer = data.get("customer") or {}
    return PaymentResult(
        normalized,
        str(data.get("id") or "") or None,
        data.get("amount"),
        str(data.get("currency") or "").upper(),
        str(customer.get("customer_code") or "") or None,
        data.get("reference"),
        parse_timestamp(data.get("paid_at")),
        str(data.get("gateway_response") or ""),
        str(data.get("gateway_response") or data.get("message") or ""),
    )


def _stripe_result(data: dict) -> PaymentResult:
    status = data.get("status")
    error = data.get("last_payment_error") or {}
    normalized = {
        "succeeded": "success",
        "canceled": "failed",
        "requires_payment_method": "failed",
        "requires_action": "requires_action",
    }.get(status, "pending")
    customer = data.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    return PaymentResult(
        normalized,
        data.get("id"),
        data.get("amount_received") if normalized == "success" else data.get("amount"),
        str(data.get("currency") or "").upper(),
        customer,
        (data.get("metadata") or {}).get("redocx_collection_reference"),
        parse_timestamp(data.get("created")),
        str(error.get("decline_code") or error.get("code") or ""),
        str(error.get("message") or ""),
    )


def lookup(provider: str, attempt: dict) -> PaymentResult:
    reference = attempt["reference"]
    if provider == "paystack":
        status, payload = _call(
            provider, "GET", f"/transaction/verify/{quote(reference, safe='')}"
        )
        if (
            status in {400, 404}
            and "not found" in str(payload.get("message") or "").lower()
        ):
            return PaymentResult("not_found")
        if status >= 400 or payload.get("status") is not True:
            raise BillingProviderError(
                "Payment verification is temporarily unavailable."
            )
        return _paystack_result(payload.get("data") or {})
    provider_id = attempt.get("provider_id")
    if provider_id and provider_id.startswith("cs_"):
        status, session = _call(
            provider, "GET", f"/checkout/sessions/{quote(provider_id, safe='')}"
        )
        if status >= 400:
            raise BillingProviderError("Checkout verification is unavailable.")
        if session.get("status") == "expired":
            return PaymentResult("failed", provider_id, code="checkout_expired")
        provider_id = session.get("payment_intent")
        if not provider_id:
            return PaymentResult("pending", attempt["provider_id"])
    if not provider_id:
        # Recover an automatic payment whose response was lost. Search is only
        # recovery evidence, never proof of failure. The same idempotency key is
        # reused within its safe window; after that, unresolved work is quarantined.
        status, payload = _call(
            provider,
            "GET",
            "/payment_intents/search",
            params={
                "query": f"metadata['redocx_collection_reference']:'{reference}'",
                "limit": 10,
            },
        )
        if status >= 400:
            raise BillingProviderError("Payment verification is unavailable.")
        items = payload.get("data") or []
        if len(items) > 1:
            return PaymentResult("review", code="ambiguous_payment")
        if not items:
            return PaymentResult("not_found")
        return _stripe_result(items[0])
    status, payload = _call(
        provider, "GET", f"/payment_intents/{quote(provider_id, safe='')}"
    )
    if status >= 400:
        raise BillingProviderError("Payment verification is unavailable.")
    return _stripe_result(payload)


def automatic_charge(job: dict, attempt: dict, identity: dict) -> PaymentResult:
    if not identity.get("authorization") or not identity.get("email"):
        return PaymentResult("requires_action", code="payment_method_required")
    provider, reference = job["provider"], attempt["reference"]
    if provider == "paystack":
        status, payload = _call(
            provider,
            "POST",
            "/transaction/charge_authorization",
            json={
                "email": identity["email"],
                "authorization_code": identity["authorization"],
                "amount": job["amount"],
                "currency": job["currency"],
                "reference": reference,
                "metadata": json.dumps({"redocx_collection_reference": reference}),
                "queue": False,
            },
        )
        # HTTP errors and duplicate-reference replies require verification.
        # They do not allocate a new reference or consume another retry.
        if status >= 400 or payload.get("status") is not True:
            raise BillingProviderError("Charge outcome requires verification.")
        return _paystack_result(payload.get("data") or {})
    status, payload = _call(
        provider,
        "POST",
        "/payment_intents",
        key=reference,
        data={
            "customer": identity["customer"],
            "payment_method": identity["authorization"],
            "amount": job["amount"],
            "currency": job["currency"].lower(),
            "off_session": "true",
            "confirm": "true",
            "metadata[redocx_collection_reference]": reference,
            "description": f"ReDOCX {job['plan'].title()} renewal, {job['quantity']} active seats",
        },
    )
    if status >= 400:
        intent = (payload.get("error") or {}).get("payment_intent")
        if isinstance(intent, dict):
            return _stripe_result(intent)
        raise BillingProviderError("Charge outcome requires verification.")
    return _stripe_result(payload)


def seat_checkout(job: dict, attempt: dict, identity: dict) -> PaymentResult:
    base = os.getenv("BILLING_SUCCESS_URL", "").strip()
    if not base or not base.startswith("https://"):
        raise BillingProviderError(
            "BILLING_SUCCESS_URL must be an HTTPS billing-page URL."
        )
    from backend.billing_provider import with_query_parameters

    url = with_query_parameters(base, seat_job=str(job["id"]))
    reference = attempt["reference"]
    if job["provider"] == "paystack":
        status, payload = _call(
            "paystack",
            "POST",
            "/transaction/initialize",
            json={
                "email": identity["email"],
                "amount": job["amount"],
                "currency": "NGN",
                "reference": reference,
                "callback_url": url,
                "metadata": json.dumps({"redocx_collection_reference": reference}),
            },
        )
        data = payload.get("data") or {}
        if (
            status >= 400
            or payload.get("status") is not True
            or not data.get("authorization_url")
        ):
            raise BillingProviderError(
                "Checkout initialization requires reconciliation."
            )
        return PaymentResult(
            "pending", data.get("access_code"), checkout_url=data["authorization_url"]
        )
    status, payload = _call(
        "stripe",
        "POST",
        "/checkout/sessions",
        key=reference,
        data={
            "mode": "payment",
            "customer": identity["customer"],
            "success_url": url,
            "cancel_url": url,
            "expires_at": int(
                min(
                    job["deadline"].timestamp(),
                    datetime.now(timezone.utc).timestamp() + 2700,
                )
            ),
            "line_items[0][price_data][currency]": "ngn",
            "line_items[0][price_data][unit_amount]": job["amount"],
            "line_items[0][price_data][product_data][name]": (
                "ReDOCX additional seats (prorated)"
                if job["purpose"] == "seats"
                else f"ReDOCX {job['plan'].title()} renewal ({job['quantity']} active seats)"
            ),
            "line_items[0][quantity]": 1,
            "payment_intent_data[metadata][redocx_collection_reference]": reference,
            "payment_intent_data[setup_future_usage]": "off_session",
            "metadata[redocx_collection_reference]": reference,
        },
    )
    if status >= 400 or not payload.get("url"):
        raise BillingProviderError("Checkout initialization requires reconciliation.")
    return PaymentResult("pending", payload.get("id"), checkout_url=payload["url"])


def payment_method_url(provider: str, subscription_id: str) -> str:
    if provider == "paystack":
        status, payload = _call(
            provider,
            "GET",
            f"/subscription/{quote(subscription_id, safe='')}/manage/link",
        )
        url = (payload.get("data") or {}).get("link")
    else:
        identity = payer(provider, subscription_id)
        status, payload = _call(
            provider,
            "POST",
            "/billing_portal/sessions",
            data={
                "customer": identity["customer"],
                "return_url": os.getenv("BILLING_SUCCESS_URL", ""),
                "flow_data[type]": "payment_method_update",
            },
        )
        url = payload.get("url")
    if status >= 400 or not url or not str(url).startswith("https://"):
        raise BillingProviderError("The payment-method update page is unavailable.")
    return str(url)
