from __future__ import annotations

"""
ReDOCX PDF Tools - Lock PDF processing.

Security and compatibility guarantees:
1. Applies AES-256 encryption only.
2. Rejects already encrypted/password-protected source PDFs.
3. Honors the ReDOCX PdfPermissionPolicy permission flags.
4. Uses a separate, non-exported owner password so user permissions cannot be
   bypassed by authenticating with the same password as the owner.
5. Supports the schema password contract up to 128 characters. PyMuPDF is used
   for passwords it supports directly; pypdf is used for longer passwords.
6. Verifies that the produced PDF is encrypted, opens with the supplied user
   password, preserves the source page count, and exposes the requested user
   permissions before persistence.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol
import mimetypes
import os
import re
import secrets
import shutil

import fitz  # PyMuPDF


MAX_PDF_PASSWORD_LENGTH = 128
_PYMUPDF_MAX_PASSWORD_LENGTH = 40
_SUPPORTED_ENCRYPTION = "aes_256"

# PDF permission bits used by both PyMuPDF and pypdf.
_PERMISSION_PRINT = 4
_PERMISSION_MODIFY = 8
_PERMISSION_COPY = 16
_PERMISSION_ANNOTATE = 32
_PERMISSION_FORM = 256
_PERMISSION_ACCESSIBILITY = 512
_PERMISSION_PRINT_HIGH_QUALITY = 2048
_RELEVANT_PERMISSION_BITS = (
    _PERMISSION_PRINT
    | _PERMISSION_MODIFY
    | _PERMISSION_COPY
    | _PERMISSION_ANNOTATE
    | _PERMISSION_FORM
    | _PERMISSION_ACCESSIBILITY
    | _PERMISSION_PRINT_HIGH_QUALITY
)


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class LockedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    page_count: int
    encryption: str = _SUPPORTED_ENCRYPTION
    password_protected: bool = True
    engine: str = "unknown"
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class PdfLockBackend(Protocol):
    def lock(
        self,
        *,
        source_path: str | Path,
        password: str,
        encryption: str | Any = _SUPPORTED_ENCRYPTION,
        permissions: Any = None,
        output_filename: str = "locked-document.pdf",
    ) -> LockedPdfArtifact:
        ...


class LocalPdfLockBackend:
    """Production-oriented local AES-256 PDF locking backend."""

    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/lock",
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)

    def lock(
        self,
        *,
        source_path: str | Path,
        password: str,
        encryption: str | Any = _SUPPORTED_ENCRYPTION,
        permissions: Any = None,
        output_filename: str = "locked-document.pdf",
    ) -> LockedPdfArtifact:
        source = _require_pdf_path(source_path)
        normalized_password = _validate_password(password)
        _require_aes_256(encryption)
        output_name = _normalize_pdf_filename(
            output_filename,
            default="locked-document.pdf",
        )
        permission_flags = _permission_flags(permissions)

        source_page_count = _inspect_unlocked_source(source)

        with TemporaryDirectory(prefix="redocx-lock-") as workdir:
            output_path = Path(workdir) / output_name

            if len(normalized_password) <= _PYMUPDF_MAX_PASSWORD_LENGTH:
                engine = self._lock_with_pymupdf(
                    source,
                    output_path=output_path,
                    password=normalized_password,
                    permission_flags=permission_flags,
                )
            else:
                engine = self._lock_with_pypdf(
                    source,
                    output_path=output_path,
                    password=normalized_password,
                    permission_flags=permission_flags,
                )

            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("PDF locking completed without producing a valid output file.")

            _verify_locked_pdf(
                output_path,
                password=normalized_password,
                expected_page_count=source_page_count,
                expected_permission_flags=permission_flags,
            )

            persisted_path, storage_key, download_url = _persist_or_copy(
                output_path,
                output_name=output_name,
                storage_backend=self.storage_backend,
                artifacts_dir=self.artifacts_dir,
            )

        return LockedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=_get_file_size_mb(persisted_path),
            file_path=str(persisted_path),
            page_count=source_page_count,
            encryption=_SUPPORTED_ENCRYPTION,
            password_protected=True,
            engine=engine,
            storage_key=storage_key,
            download_url=download_url,
        )

    @staticmethod
    def _lock_with_pymupdf(
        source: Path,
        *,
        output_path: Path,
        password: str,
        permission_flags: int,
    ) -> str:
        owner_password = secrets.token_urlsafe(24)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            pdf.save(
                output_path,
                garbage=4,
                deflate=True,
                clean=False,
                encryption=fitz.PDF_ENCRYPT_AES_256,
                owner_pw=owner_password,
                user_pw=password,
                permissions=permission_flags,
            )

        return "pymupdf-aes256"

    @staticmethod
    def _lock_with_pypdf(
        source: Path,
        *,
        output_path: Path,
        password: str,
        permission_flags: int,
    ) -> str:
        """
        Encrypt passwords longer than PyMuPDF's 40-character API limit.

        Import is intentionally lazy so existing Combine/Split/Edit/Compress
        functionality remains importable even in deployments that have not yet
        installed pypdf. The Lock route reports a precise dependency error only
        when a >40-character password requires this path.
        """
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Lock PDF passwords longer than 40 characters require the 'pypdf' "
                "package so the configured 128-character schema contract can be honored."
            ) from exc

        owner_password = secrets.token_urlsafe(24)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with source.open("rb") as source_stream:
            reader = PdfReader(source_stream, strict=False)
            if reader.is_encrypted:
                raise ValueError(
                    f"lock_pdf requires an unlocked source PDF: {source.name}"
                )

            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            try:
                writer.encrypt(
                    user_password=password,
                    owner_password=owner_password,
                    permissions_flag=permission_flags,
                    algorithm="AES-256",
                )
                with output_path.open("wb") as output_stream:
                    writer.write(output_stream)
            finally:
                close = getattr(writer, "close", None)
                if callable(close):
                    close()

        return "pypdf-aes256"


# Public convenience API -----------------------------------------------------


def lock_pdf(
    source_path: str | Path,
    *,
    password: str,
    encryption: str | Any = _SUPPORTED_ENCRYPTION,
    permissions: Any = None,
    output_filename: str = "locked-document.pdf",
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/lock",
) -> LockedPdfArtifact:
    backend = LocalPdfLockBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
    )
    return backend.lock(
        source_path=source_path,
        password=password,
        encryption=encryption,
        permissions=permissions,
        output_filename=output_filename,
    )


# Validation / security helpers ---------------------------------------------


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")
    return path


def _inspect_unlocked_source(source: Path) -> int:
    try:
        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            page_count = int(pdf.page_count)
            if page_count < 1:
                raise ValueError("The source PDF has no pages.")
            return page_count
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Invalid or unreadable PDF file: {source.name}") from exc


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(
        getattr(document, "is_encrypted", False)
    ):
        raise ValueError(f"lock_pdf requires an unlocked source PDF: {source_path.name}")


def _validate_password(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("lock_pdf password must be a string.")
    if not 8 <= len(value) <= MAX_PDF_PASSWORD_LENGTH:
        raise ValueError(
            f"lock_pdf password must contain 8 to {MAX_PDF_PASSWORD_LENGTH} characters."
        )
    return value


def _require_aes_256(value: str | Any) -> None:
    normalized = str(getattr(value, "value", value)).strip().lower()
    if normalized != _SUPPORTED_ENCRYPTION:
        raise ValueError("lock_pdf requires AES-256 encryption.")


def _permission_flags(policy: Any) -> int:
    if policy is None:
        # Mirrors schema.PdfPermissionPolicy defaults.
        allow_printing = False
        allow_copying = False
        allow_modifying = False
        allow_annotations = False
        allow_form_filling = False
        allow_accessibility = True
    else:
        allow_printing = bool(getattr(policy, "allow_printing", False))
        allow_copying = bool(getattr(policy, "allow_copying", False))
        allow_modifying = bool(getattr(policy, "allow_modifying", False))
        allow_annotations = bool(getattr(policy, "allow_annotations", False))
        allow_form_filling = bool(getattr(policy, "allow_form_filling", False))
        allow_accessibility = bool(getattr(policy, "allow_accessibility", True))

    permissions = 0
    if allow_printing:
        permissions |= _PERMISSION_PRINT | _PERMISSION_PRINT_HIGH_QUALITY
    if allow_copying:
        permissions |= _PERMISSION_COPY
    if allow_modifying:
        permissions |= _PERMISSION_MODIFY
    if allow_annotations:
        permissions |= _PERMISSION_ANNOTATE
    if allow_form_filling:
        permissions |= _PERMISSION_FORM
    if allow_accessibility:
        permissions |= _PERMISSION_ACCESSIBILITY
    return permissions


def _verify_locked_pdf(
    output_path: Path,
    *,
    password: str,
    expected_page_count: int,
    expected_permission_flags: int,
) -> None:
    try:
        with fitz.open(output_path) as pdf:
            if not bool(getattr(pdf, "is_encrypted", False)) or not bool(
                getattr(pdf, "needs_pass", False)
            ):
                raise RuntimeError("Lock PDF output is not password-protected.")

            authentication = int(pdf.authenticate(password))
            if authentication <= 0:
                raise RuntimeError("Lock PDF output cannot be opened with the supplied password.")

            if int(pdf.page_count) != expected_page_count:
                raise RuntimeError("Lock PDF output does not preserve the source page count.")

            actual_permissions = int(getattr(pdf, "permissions", 0))
            if (
                actual_permissions & _RELEVANT_PERMISSION_BITS
            ) != (expected_permission_flags & _RELEVANT_PERMISSION_BITS):
                raise RuntimeError("Lock PDF output permissions do not match the request.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Lock PDF output failed post-encryption verification.") from exc


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    if not raw.lower().endswith(".pdf"):
        raise ValueError("lock_pdf output_filename must end with .pdf.")
    if raw in {".", ".."} or "/" in raw or "\\" in raw or "\x00" in raw:
        raise ValueError("lock_pdf output_filename must be a safe PDF basename.")
    return raw


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "locked-document"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated PDF is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/pdf"


def _persist_or_copy(
    source_path: Path,
    *,
    output_name: str,
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
) -> tuple[Path, Optional[str], Optional[str]]:
    if storage_backend is None:
        storage_backend = _try_default_storage(base_dir=str(artifacts_dir))

    if storage_backend is not None:
        stored = storage_backend.persist(
            source_file_path=str(source_path),
            artifact_name=output_name,
            content_type=_guess_content_type(source_path),
        )
        stored_path = Path(getattr(stored, "stored_path", source_path)).resolve()
        return (
            stored_path,
            getattr(stored, "storage_key", None),
            getattr(stored, "download_url", None),
        )

    artifacts = Path(artifacts_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / output_name
    if destination.exists():
        destination = artifacts / _dedupe_name(output_name)
    shutil.copy2(source_path, destination)
    return destination, None, None


def _try_default_storage(*, base_dir: str) -> Optional[StorageBackend]:
    try:
        from backend.src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


def _dedupe_name(file_name: str) -> str:
    path = Path(file_name)
    return f"{_safe_stem(path.stem)}-{os.urandom(4).hex()}{path.suffix or '.pdf'}"


__all__ = [
    "LockedPdfArtifact",
    "LocalPdfLockBackend",
    "PdfLockBackend",
    "lock_pdf",
]
