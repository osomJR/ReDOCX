from __future__ import annotations

"""
Certificate of completion generation for ReDOCX Sign.

The certificate is a human-readable PDF summary of:
- envelope id/status/workflow
- document hash
- signer statuses
- audit events

It is not a qualified digital certificate. It is an audit-supporting completion
certificate for simple electronic signatures.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any, Iterable, Optional, Protocol, Sequence

import fitz  # PyMuPDF

try:
    from backend.src.schema import (
        DocumentFileResult,
        ESignatureAuditEvent,
        ESignatureEnvelopeStatus,
        ESignatureRecipientResult,
        ESignatureWorkflow,
    )
    from backend.src.validation import build_pdf_document_file_result
    from backend.src.storage.artifacts import LocalArtifactStorage, guess_content_type
except ImportError:
    from ...schema import (
        DocumentFileResult,
        ESignatureAuditEvent,
        ESignatureEnvelopeStatus,
        ESignatureRecipientResult,
        ESignatureWorkflow,
    )
    from ...validation import build_pdf_document_file_result
    try:
        from ...storage.artifacts import LocalArtifactStorage, guess_content_type
    except ImportError:  # pragma: no cover
        LocalArtifactStorage = None  # type: ignore
        def guess_content_type(_path: str) -> str:
            return "application/pdf"

from .audit import sha256_file



class StorageBackend(Protocol):
    """Storage adapter protocol used by certificate generation."""

    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 48
LINE_HEIGHT = 14


@dataclass(frozen=True)
class CertificateArtifact:
    filename: str
    path: str
    file_size_mb: float
    sha256: str
    result: DocumentFileResult


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_size_mb(path: str | Path) -> float:
    return round(Path(path).stat().st_size / (1024 * 1024), 4)


def _default_storage(storage_backend: Optional[StorageBackend]):
    if storage_backend is not None:
        return storage_backend
    if LocalArtifactStorage is None:
        return None
    return LocalArtifactStorage(base_dir="artifacts/esignature/certificates")


def _persist_optional(path: Path, *, storage_backend: Optional[StorageBackend]) -> tuple[Optional[str], Optional[str]]:
    storage = _default_storage(storage_backend)
    if storage is None:
        return None, None
    stored = storage.persist(
        source_file_path=str(path),
        artifact_name=path.name,
        content_type=guess_content_type(str(path)),
    )
    return stored.storage_key, stored.download_url


class CertificateWriter:
    def __init__(self) -> None:
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.y = MARGIN

    def ensure_space(self, height: float = LINE_HEIGHT) -> None:
        if self.y + height > PAGE_HEIGHT - MARGIN:
            self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            self.y = MARGIN

    def text(self, value: str, *, size: int = 10, bold: bool = False, gap: int = 4) -> None:
        font = "helv"  # Built-in. Keep certificate ASCII-safe for maximum portability.
        max_chars = 92 if size <= 10 else 64
        for line in wrap(str(value), width=max_chars) or [""]:
            self.ensure_space(LINE_HEIGHT)
            self.page.insert_text((MARGIN, self.y), line, fontsize=size, fontname=font)
            self.y += LINE_HEIGHT
        self.y += gap

    def heading(self, value: str) -> None:
        self.text(value, size=16, gap=10)

    def section(self, value: str) -> None:
        self.y += 6
        self.text(value.upper(), size=11, gap=3)
        self.page.draw_line((MARGIN, self.y - 3), (PAGE_WIDTH - MARGIN, self.y - 3), color=(0.75, 0.75, 0.75), width=0.5)

    def save(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(output_path, garbage=4, deflate=True)
        self.doc.close()


def generate_completion_certificate(
    *,
    output_pdf_path: str | Path,
    envelope_id: str,
    document_name: str,
    workflow: ESignatureWorkflow,
    status: ESignatureEnvelopeStatus,
    recipients: Sequence[ESignatureRecipientResult],
    audit_events: Sequence[ESignatureAuditEvent],
    signed_pdf_sha256: Optional[str] = None,
    sender_email: Optional[str] = None,
    completed_at_iso: Optional[str] = None,
    storage_backend: Optional[StorageBackend] = None,
    algorithm_version: Optional[str] = None,
) -> CertificateArtifact:
    output = Path(output_pdf_path)
    if output.suffix.lower() != ".pdf":
        raise ValueError("Certificate output path must end with .pdf.")

    writer = CertificateWriter()
    writer.heading("ReDOCX Sign - Certificate of Completion")

    writer.section("Envelope")
    writer.text(f"Envelope ID: {envelope_id}")
    writer.text(f"Document: {document_name}")
    writer.text(f"Workflow: {workflow.value}")
    writer.text(f"Status: {status.value}")
    writer.text(f"Sender: {sender_email or 'not recorded'}")
    writer.text(f"Completed At: {completed_at_iso or utcnow_iso()}")
    writer.text(f"Signed PDF SHA-256: {signed_pdf_sha256 or 'not recorded'}")

    writer.section("Recipients")
    for recipient in sorted(recipients, key=lambda item: (item.signing_order, item.email.lower())):
        writer.text(
            f"Order {recipient.signing_order} | {recipient.name} <{recipient.email}> | "
            f"Role: {recipient.role.value} | Status: {recipient.status.value}"
        )

    writer.section("Audit Trail")
    for event in audit_events:
        writer.text(
            f"{event.created_at_iso} | {event.event_type.value} | "
            f"Actor: {event.actor_email or 'system'} | IP: {event.ip_address or '-'} | "
            f"Doc SHA-256: {event.document_sha256 or '-'}"
        )

    writer.section("Important Notice")
    writer.text(
        "This certificate records the ReDOCX e-signature workflow events available to the system. "
        "It is not legal advice and is not a qualified digital signature certificate."
    )

    writer.save(output)

    storage_key, download_url = _persist_optional(output, storage_backend=storage_backend)
    result = build_pdf_document_file_result(
        filename=output.name,
        file_size_mb=_file_size_mb(output),
        storage_key=storage_key,
        download_url=download_url,
        algorithm_version=algorithm_version,
    )
    return CertificateArtifact(
        filename=output.name,
        path=str(output),
        file_size_mb=_file_size_mb(output),
        sha256=sha256_file(output),
        result=result,
    )


__all__ = [
    "CertificateArtifact",
    "utcnow_iso",
    "CertificateWriter",
    "generate_completion_certificate",
]
