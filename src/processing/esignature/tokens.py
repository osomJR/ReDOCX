from __future__ import annotations

"""
Secure signing-token utilities for ReDOCX Sign.

Production rule:
- send raw token only in the email/link
- store only token_hash in the database
- compare hashes using hmac.compare_digest
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import os
import secrets
from typing import Optional
from uuid import uuid4


DEFAULT_SIGNING_TOKEN_TTL_DAYS = int(os.getenv("ESIGN_TOKEN_TTL_DAYS", "30"))
DEFAULT_TOKEN_BYTES = 48
TOKEN_PEPPER_ENV = "ESIGN_TOKEN_PEPPER"
SIGNING_BASE_URL_ENV = "ESIGN_SIGNING_BASE_URL"


@dataclass(frozen=True)
class SigningToken:
    token_id: str
    envelope_id: str
    signer_email: str
    raw_token: str
    token_hash: str
    expires_at_iso: str


@dataclass(frozen=True)
class StoredSigningToken:
    token_id: str
    envelope_id: str
    signer_email: str
    token_hash: str
    expires_at_iso: str
    used_at_iso: Optional[str] = None
    revoked_at_iso: Optional[str] = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def generate_raw_token(*, nbytes: int = DEFAULT_TOKEN_BYTES) -> str:
    if nbytes < 32:
        raise ValueError("Signing tokens must use at least 32 random bytes.")
    return secrets.token_urlsafe(nbytes)


def _pepper(secret: Optional[str] = None) -> str:
    value = secret if secret is not None else os.getenv(TOKEN_PEPPER_ENV, "")
    return value.strip()


def hash_token(raw_token: str, *, secret: Optional[str] = None) -> str:
    token = (raw_token or "").strip()
    if not token:
        raise ValueError("raw_token is required.")
    material = f"{_pepper(secret)}:{token}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def verify_token_hash(raw_token: str, expected_hash: str, *, secret: Optional[str] = None) -> bool:
    if not expected_hash:
        return False
    actual = hash_token(raw_token, secret=secret)
    return hmac.compare_digest(actual, expected_hash)


def iso_in_days(days: int) -> str:
    if days < 1:
        raise ValueError("days must be >= 1.")
    return (utcnow() + timedelta(days=days)).isoformat()


def is_expired(expires_at_iso: str, *, now: Optional[datetime] = None) -> bool:
    expires_at = datetime.fromisoformat(expires_at_iso)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return (now or utcnow()) >= expires_at


def create_signing_token(
    *,
    envelope_id: str,
    signer_email: str,
    expires_in_days: int = DEFAULT_SIGNING_TOKEN_TTL_DAYS,
    secret: Optional[str] = None,
) -> SigningToken:
    raw = generate_raw_token()
    return SigningToken(
        token_id=f"tok_{uuid4().hex}",
        envelope_id=envelope_id,
        signer_email=signer_email.strip().lower(),
        raw_token=raw,
        token_hash=hash_token(raw, secret=secret),
        expires_at_iso=iso_in_days(expires_in_days),
    )


def to_stored_token(token: SigningToken) -> StoredSigningToken:
    return StoredSigningToken(
        token_id=token.token_id,
        envelope_id=token.envelope_id,
        signer_email=token.signer_email,
        token_hash=token.token_hash,
        expires_at_iso=token.expires_at_iso,
    )


def validate_stored_token(
    *,
    raw_token: str,
    stored: StoredSigningToken,
    signer_email: Optional[str] = None,
    secret: Optional[str] = None,
) -> None:
    if stored.revoked_at_iso is not None:
        raise ValueError("Signing token has been revoked.")
    if stored.used_at_iso is not None:
        raise ValueError("Signing token has already been used.")
    if is_expired(stored.expires_at_iso):
        raise ValueError("Signing token has expired.")
    if signer_email is not None and stored.signer_email.lower() != signer_email.strip().lower():
        raise ValueError("Signing token does not belong to this signer.")
    if not verify_token_hash(raw_token, stored.token_hash, secret=secret):
        raise ValueError("Invalid signing token.")


def mark_token_used(stored: StoredSigningToken, *, used_at_iso: Optional[str] = None) -> StoredSigningToken:
    return StoredSigningToken(
        token_id=stored.token_id,
        envelope_id=stored.envelope_id,
        signer_email=stored.signer_email,
        token_hash=stored.token_hash,
        expires_at_iso=stored.expires_at_iso,
        used_at_iso=used_at_iso or utcnow_iso(),
        revoked_at_iso=stored.revoked_at_iso,
    )


def build_signing_url(raw_token: str, *, base_url: Optional[str] = None) -> str:
    resolved_base = (base_url or os.getenv(SIGNING_BASE_URL_ENV, "")).strip().rstrip("/")
    if not resolved_base:
        raise ValueError(
            f"Signing base URL is required. Pass base_url or set {SIGNING_BASE_URL_ENV}."
        )
    return f"{resolved_base}/sign/recipient/{raw_token.strip()}"


__all__ = [
    "SigningToken",
    "StoredSigningToken",
    "utcnow",
    "utcnow_iso",
    "generate_raw_token",
    "hash_token",
    "verify_token_hash",
    "iso_in_days",
    "is_expired",
    "create_signing_token",
    "to_stored_token",
    "validate_stored_token",
    "mark_token_used",
    "build_signing_url",
]
