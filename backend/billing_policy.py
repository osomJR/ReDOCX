"""Pure pricing and collection rules. All amounts are integer NGN subunits."""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

# Initial collection plus four further attempts for recoverable failures.
# A native Paystack failed invoice has already used its initial attempt.
RETRY_DAYS = (1, 2, 4, 6)
MAX_COLLECTION_ATTEMPTS = 5


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("A verified billing timestamp is required.")
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def next_month(
    value: datetime, provider: str, anchor_day: int | None = None
) -> datetime:
    value = utc(value)
    year, month = (
        (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    )
    # Match Paystack's documented 29th–31st -> 28th monthly convention.
    day = min(
        anchor_day or value.day,
        28 if provider == "paystack" else calendar.monthrange(year, month)[1],
    )
    return value.replace(year=year, month=month, day=day)


def seat_count(plan: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Seats must be a positive integer, including the owner.")
    if plan == "personal" and value != 1:
        raise ValueError("Personal supports exactly one seat.")
    if plan == "business" and value > 19:
        raise ValueError("Business supports at most 19 seats.")
    if plan not in {"personal", "business", "enterprise"}:
        raise ValueError("Unsupported paid plan.")
    return value


def prorated_amount(
    unit: int, added: int, start: datetime, end: datetime, now: datetime
) -> int:
    start, end, now = utc(start), utc(end), utc(now)
    if isinstance(unit, bool) or not isinstance(unit, int) or unit < 1 or added < 1:
        raise ValueError("Invalid seat price or quantity.")
    if not start <= now < end:
        raise ValueError("Seat additions require a verified current paid period.")
    total_us = (end - start) // timedelta(microseconds=1)
    remaining_us = (end - now) // timedelta(microseconds=1)
    # Round up by at most one kobo; never use binary floating point for money.
    return (unit * added * remaining_us + total_us - 1) // total_us


def retry_at(
    due: datetime, failed_attempts: int, now: datetime, deadline: datetime
) -> datetime | None:
    if failed_attempts < 1 or failed_attempts > len(RETRY_DAYS):
        return None
    candidate = max(
        utc(due) + timedelta(days=RETRY_DAYS[failed_attempts - 1]),
        utc(now) + timedelta(hours=24),
    )
    return candidate if candidate < utc(deadline) else None


def retryable_decline(code: str, message: str = "") -> bool:
    text = f"{code} {message}".lower().replace("_", " ")
    hard = (
        "expired",
        "stolen",
        "lost card",
        "pickup",
        "pick up",
        "fraud",
        "restricted",
        "revoked",
        "invalid authorization",
        "invalid account",
        "closed account",
        "authentication required",
        "do not honor",
        "do not honour",
        "not permitted",
    )
    if any(token in text for token in hard):
        return False
    # Unknown issuer responses need customer intervention, not blind retries.
    return any(
        token in text
        for token in (
            "insufficient",
            "issuer unavailable",
            "issuer or switch",
            "processing error",
            "temporarily",
            "temporary",
            "network",
            "try again",
            "system error",
        )
    )
