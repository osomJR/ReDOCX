from __future__ import annotations

"""
ReDOCX E-Signature audit helpers.

The audit trail should be append-only in production. This module provides
schema-aligned event creation and optional hash-chain records you can persist
in a database table for tamper-evidence.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional
from uuid import uuid4

try:
    from backend.src.schema import ESignatureAuditEvent, ESignatureAuditEventType
except ImportError:
    from ...schema import ESignatureAuditEvent, ESignatureAuditEventType


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"File not found: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_event_type(value: str | ESignatureAuditEventType) -> ESignatureAuditEventType:
    if isinstance(value, ESignatureAuditEventType):
        return value
    return ESignatureAuditEventType(str(value).strip())


def create_audit_event(
    *,
    event_type: str | ESignatureAuditEventType,
    actor_email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    document_id: Optional[str] = None,
    document_sha256: Optional[str] = None,
    created_at_iso: Optional[str] = None,
    event_id: Optional[str] = None,
) -> ESignatureAuditEvent:
    return ESignatureAuditEvent(
        event_id=event_id or f"evt_{uuid4().hex}",
        event_type=normalize_event_type(event_type),
        actor_email=actor_email.strip().lower() if isinstance(actor_email, str) and actor_email.strip() else None,
        ip_address=ip_address.strip() if isinstance(ip_address, str) and ip_address.strip() else None,
        user_agent=user_agent.strip() if isinstance(user_agent, str) and user_agent.strip() else None,
        document_id=document_id.strip() if isinstance(document_id, str) and document_id.strip() else None,
        document_sha256=document_sha256.lower() if isinstance(document_sha256, str) and document_sha256 else None,
        created_at_iso=created_at_iso or utcnow_iso(),
    )


@dataclass(frozen=True)
class AuditLedgerEntry:
    """
    Optional tamper-evident representation for database persistence.

    Store event_hash and previous_hash in addition to schema event fields.
    """

    event: ESignatureAuditEvent
    previous_hash: Optional[str]
    event_hash: str


def event_payload_for_hash(event: ESignatureAuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "actor_email": event.actor_email,
        "ip_address": event.ip_address,
        "user_agent": event.user_agent,
        "document_id": event.document_id,
        "document_sha256": event.document_sha256,
        "created_at_iso": event.created_at_iso,
    }


def hash_audit_event(
    event: ESignatureAuditEvent,
    *,
    previous_hash: Optional[str] = None,
) -> str:
    payload = {
        "previous_hash": previous_hash,
        "event": event_payload_for_hash(event),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_hash_chain(
    events: Iterable[ESignatureAuditEvent],
    *,
    previous_hash: Optional[str] = None,
) -> list[AuditLedgerEntry]:
    entries: list[AuditLedgerEntry] = []
    cursor = previous_hash
    for event in events:
        event_hash = hash_audit_event(event, previous_hash=cursor)
        entries.append(AuditLedgerEntry(event=event, previous_hash=cursor, event_hash=event_hash))
        cursor = event_hash
    return entries


@dataclass
class AuditLog:
    """Small in-memory append-only audit log useful for service tests."""

    events: list[ESignatureAuditEvent] = field(default_factory=list)

    def append(
        self,
        *,
        event_type: str | ESignatureAuditEventType,
        actor_email: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        document_id: Optional[str] = None,
        document_sha256: Optional[str] = None,
    ) -> ESignatureAuditEvent:
        event = create_audit_event(
            event_type=event_type,
            actor_email=actor_email,
            ip_address=ip_address,
            user_agent=user_agent,
            document_id=document_id,
            document_sha256=document_sha256,
        )
        self.events.append(event)
        return event

    def extend(self, events: Iterable[ESignatureAuditEvent]) -> None:
        self.events.extend(events)

    def to_hash_chain(self, *, previous_hash: Optional[str] = None) -> list[AuditLedgerEntry]:
        return build_hash_chain(self.events, previous_hash=previous_hash)


__all__ = [
    "AuditLedgerEntry",
    "AuditLog",
    "utcnow_iso",
    "sha256_file",
    "normalize_event_type",
    "create_audit_event",
    "event_payload_for_hash",
    "hash_audit_event",
    "build_hash_chain",
]
