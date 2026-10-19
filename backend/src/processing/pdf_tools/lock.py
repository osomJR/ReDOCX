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
6. Rejects digital signatures before rewriting and verifies document structure,
   decoded content, forms, annotations, and page appearance before persistence.
7. Normalizes annotation/form dependencies and keeps accessibility available.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol
import hashlib
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
_PERMISSION_ASSEMBLE = 1024
_PERMISSION_PRINT_HIGH_QUALITY = 2048
_RELEVANT_PERMISSION_BITS = (
    _PERMISSION_PRINT
    | _PERMISSION_MODIFY
    | _PERMISSION_COPY
    | _PERMISSION_ANNOTATE
    | _PERMISSION_FORM
    | _PERMISSION_ACCESSIBILITY
    | _PERMISSION_ASSEMBLE
    | _PERMISSION_PRINT_HIGH_QUALITY
)


class SignedPdfLockError(ValueError):
    """Encryption would rewrite bytes covered by an existing signature."""


class PdfLockPreservationError(RuntimeError):
    """The source cannot be verified or the encrypted document changed."""


_SIGNED_PDF_MESSAGE = (
    "Digitally signed PDFs cannot be locked without invalidating their signatures. "
    "Use an unsigned original and apply digital signatures after locking."
)
_PRESERVATION_MESSAGE = (
    "PDF content could not be preserved and verified safely. No locked PDF was created."
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

        with TemporaryDirectory(prefix="redocx-lock-") as workdir:
            # Keep inspection, fingerprinting and both encryption engines on the
            # same verified bytes. Only a proven xref numbering defect is repaired;
            # the customer's source file is never rewritten.
            source = _prepare_lock_source(source, Path(workdir))
            source_page_count = _inspect_unlocked_source(source)
            source_fingerprint = _document_fingerprint(source)
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

            def verify_candidate() -> None:
                _verify_locked_pdf(
                    output_path,
                    password=normalized_password,
                    expected_page_count=source_page_count,
                    expected_permission_flags=permission_flags,
                )
                if _document_fingerprint(output_path, password=normalized_password) != source_fingerprint:
                    raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
                _verify_page_appearance(source, output_path, password=normalized_password)

            try:
                verify_candidate()
            except PdfLockPreservationError:
                if engine != "pymupdf-aes256":
                    raise
                # MuPDF serializes some numbers at reduced precision, including
                # font metrics. Retry from the untouched source using the clone
                # writer; never relax comparison or persist the failed candidate.
                output_path.unlink(missing_ok=True)
                engine = self._lock_with_pypdf(
                    source,
                    output_path=output_path,
                    password=normalized_password,
                    permission_flags=permission_flags,
                )
                verify_candidate()

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
                # Encryption must not optimize, deduplicate, or clean objects.
                garbage=0,
                deflate=False,
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
            from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject
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
                # pypdf 5.9 also rounds FloatObject values in its default writer.
                # Preserve each parsed double value without changing global
                # library precision settings (which would affect other tools).
                class PreservedFloat(FloatObject):
                    def write_to_stream(self, stream: Any, encryption_key: Any = None) -> None:
                        from decimal import Decimal
                        stream.write(format(Decimal(repr(float(self))), "f").encode("ascii"))

                visited: set[int] = set()

                def preserve_numbers(value: Any) -> Any:
                    if isinstance(value, FloatObject):
                        preserved = PreservedFloat(float(value))
                        reference = getattr(value, "indirect_reference", None)
                        if reference is not None:
                            preserved.indirect_reference = reference
                        return preserved
                    if isinstance(value, (DictionaryObject, ArrayObject)):
                        if id(value) in visited:
                            return value
                        visited.add(id(value))
                        if isinstance(value, DictionaryObject):
                            for key in list(value):
                                value[key] = preserve_numbers(value.raw_get(key))
                        else:
                            for index, item in enumerate(value):
                                value[index] = preserve_numbers(item)
                    return value

                for index, value in enumerate(writer._objects):
                    writer._objects[index] = preserve_numbers(value)
                writer.encrypt(
                    user_password=password,
                    owner_password=owner_password,
                    permissions_flag=permission_flags | 0xFFFFF0C0,
                    algorithm="AES-256",
                )
                # pypdf 5.9 stores /P as unsigned. PDF permissions use a signed
                # 32-bit integer; preserve identical bits and /Perms integrity.
                entry = writer._encrypt_entry
                if entry is None:
                    raise RuntimeError("Lock PDF encryption dictionary is missing.")
                entry[NameObject("/P")] = NumberObject((permission_flags | 0xFFFFF0C0) - (1 << 32))
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


def _prepare_lock_source(source: Path, workdir: Path) -> Path:
    """Repair only a fully verified, off-by-one classic xref subsection.

    Some scanners write ``1 N`` above a table containing the reserved object-0
    entry followed by objects 1..N-1. Repair that one digit in a private copy
    only when every live entry already points to the correct object header.
    Streams, document objects and byte offsets remain unchanged. Incremental,
    hybrid and other ambiguous layouts are never rewritten by this helper.
    """
    try:
        with source.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 4096))
            end = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*\Z", stream.read())
            if end is None:
                return source
            offset = int(end.group(1))
            if not 0 <= offset < size:
                return source
            stream.seek(offset)
            header = re.match(
                rb"xref[ \t]*(?:\r\n|\r|\n)[ \t]*(?P<first>1)[ \t]+"
                rb"(?P<count>\d+)[ \t]*(?:\r\n|\r|\n)",
                stream.read(128),
            )
            if header is None:
                return source
            count = int(header.group("count"))
            if not 2 <= count <= 500_000:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            stream.seek(0)
            data = stream.read()

        entry_pattern = re.compile(
            rb"(\d{10})[ \t]+(\d{5})[ \t]+([nf])[ \t]*(?:\r\n|\r|\n)"
        )
        position = offset + header.end()
        for number in range(count):
            entry = entry_pattern.match(data, position)
            if entry is None:
                return source
            position = entry.end()
            object_offset, generation = int(entry.group(1)), int(entry.group(2))
            if number == 0:
                if (object_offset, generation, entry.group(3)) != (0, 65535, b"f"):
                    return source
                continue
            if entry.group(3) != b"n" or not 0 < object_offset < offset:
                return source
            object_header = re.match(rb"(\d+)\s+(\d+)\s+obj\b", data[object_offset:object_offset + 64])
            if (object_header is None
                    or int(object_header.group(1)) != number
                    or int(object_header.group(2)) != generation):
                return source

        if re.match(rb"[\x00\t\n\f\r ]*trailer\b", data[position:position + 32]) is None:
            return source

        from io import BytesIO
        from pypdf import PdfReader

        digit = offset + header.start("first")
        repaired = data[:digit] + b"0" + data[digit + 1:]
        reader = PdfReader(BytesIO(repaired), strict=True)
        if (reader.xref_index != 0 or reader.trailer.get("/Size") != count
                or "/Prev" in reader.trailer or "/XRefStm" in reader.trailer
                or reader.is_encrypted):
            return source

        prepared = workdir / "_lock_source" / "source.pdf"
        prepared.parent.mkdir(parents=True, exist_ok=True)
        prepared.write_bytes(repaired)
        return prepared
    except PdfLockPreservationError:
        raise
    except Exception as exc:
        raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc


def _inspect_unlocked_source(source: Path) -> int:
    try:
        pdf = fitz.open(source)
    except Exception as exc:
        raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc

    with pdf:
        _reject_encrypted_pdf(pdf, source)
        try:
            page_count = int(pdf.page_count)
            if page_count < 1:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            _reject_signed_pdf(pdf)
            return page_count
        except (SignedPdfLockError, PdfLockPreservationError):
            raise
        except Exception as exc:
            # Reuse the existing Lock-specific error taxonomy. The routes catch
            # this before ValueError, retaining a useful public error code.
            raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(
        getattr(document, "is_encrypted", False)
    ):
        raise ValueError(f"lock_pdf requires an unlocked source PDF: {source_path.name}")


def _resolve_pdf_object(value: Any) -> Any:
    """Resolve references; only an explicitly free or null object is PDF null.

    An unresolved index cannot prove that an object is absent: a malformed xref
    can hide a live image, field or signature. Fail closed instead of hashing an
    incomplete graph. Explicit free entries remain supported.
    """
    from pypdf.generic import IndirectObject, NullObject

    references: set[tuple[int, int, int]] = set()
    while isinstance(value, IndirectObject):
        reader = value.pdf
        number, generation = value.idnum, value.generation
        identity = (id(reader), number, generation)
        if identity in references or len(references) >= 128:
            raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
        references.add(identity)
        if reader.xref_free_entry.get(generation, {}).get(number, False):
            return None
        if (number not in reader.xref.get(generation, {})
                and not (generation == 0 and number in reader.xref_objStm)):
            raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
        try:
            value = value.get_object()
        except Exception as exc:
            raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc
        if value is None:
            raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
    return None if isinstance(value, NullObject) else value


def _reject_signed_pdf(document: fitz.Document) -> None:
    """Inspect live objects, including invisible signatures and timestamps.

    SigFlags is optional and can also be set on unsigned fields. Walk both
    reachable dictionaries and unreferenced live objects. Free xref slots are
    valid PDF structure, particularly after incremental signing, and are skipped.
    This detects signature presence, not signature validity or trust.
    """
    from pypdf import PdfReader
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NullObject

    with Path(document.name).open("rb") as stream:
        reader = PdfReader(stream, strict=True)
        pending = []
        # xref/xref_objStm are the parsed live-object indexes in pinned pypdf 5.9.
        for generation, entries in reader.xref.items():
            for number in entries:
                if number and not reader.xref_free_entry.get(generation, {}).get(number, False):
                    pending.append(IndirectObject(number, generation, reader))
        pending.extend(IndirectObject(number, 0, reader) for number in reader.xref_objStm)
        seen: set[int] = set()
        while pending:
            value = pending.pop()
            value = _resolve_pdf_object(value)
            if not isinstance(value, (DictionaryObject, ArrayObject)):
                continue
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            if len(seen) > 500_000:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            if isinstance(value, DictionaryObject):
                if value.get("/Type") in {"/Sig", "/DocTimeStamp"}:
                    raise SignedPdfLockError(_SIGNED_PDF_MESSAGE)
                if "/ByteRange" in value and "/Contents" in value:
                    raise SignedPdfLockError(_SIGNED_PDF_MESSAGE)
                signature_value = _resolve_pdf_object(value.get("/V"))
                if (value.get("/FT") == "/Sig" and signature_value is not None
                        and not isinstance(signature_value, NullObject)):
                    raise SignedPdfLockError(_SIGNED_PDF_MESSAGE)
                if "/DocMDP" in value:
                    raise SignedPdfLockError(_SIGNED_PDF_MESSAGE)
                pending.extend(value.values())
            else:
                pending.extend(value)


def _document_fingerprint(path: Path, *, password: str | None = None) -> bytes:
    """Hash the complete reachable catalog and document information graph.

    Object numbers, dictionary ordering, stream compression and the encryption
    envelope are serialization details. Everything else is compared, including
    page resources/content, field trees/values/actions/appearances, annotations,
    links, outlines, attachments, metadata and accessibility structure. Shared
    objects and cycles are encoded once with traversal-local node identifiers,
    rather than expanding every path or using file-specific object numbers.
    Null dictionary entries are omitted according to the PDF object rules.
    Unverifiable/malformed or excessively complex graphs fail closed.
    """
    try:
        from decimal import Decimal
        from pypdf import PdfReader
        from pypdf.generic import (
            ArrayObject, BooleanObject, ByteStringObject, DictionaryObject,
            FloatObject, IndirectObject, NameObject, NullObject, NumberObject,
            StreamObject, TextStringObject,
        )

        digest = hashlib.sha256()
        seen: dict[int, int] = {}
        visits = 0

        def emit(tag: bytes, value: bytes = b"") -> None:
            digest.update(tag + str(len(value)).encode("ascii") + b":" + value)

        def visit(value: Any, depth: int = 0) -> None:
            nonlocal visits
            visits += 1
            if visits > 500_000 or depth > 128:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            value = _resolve_pdf_object(value)
            if isinstance(value, (DictionaryObject, ArrayObject)):
                identity = id(value)
                if identity in seen:
                    emit(b"ref", str(seen[identity]).encode("ascii"))
                    return
                seen[identity] = len(seen)
                if isinstance(value, DictionaryObject):
                    is_stream = isinstance(value, StreamObject)
                    # Decoded bytes make lossless compression irrelevant.
                    ignored = {"/Length", "/Filter", "/DecodeParms"} if is_stream else set()
                    entries = []
                    for key in sorted(value):
                        if key in ignored:
                            continue
                        item = _resolve_pdf_object(value.raw_get(key))
                        # A null dictionary value is equivalent to an absent key.
                        if item is not None:
                            entries.append((key, item))
                    emit(b"stream" if is_stream else b"dict", str(len(entries)).encode("ascii"))
                    for key, item in entries:
                        emit(b"key", str(key).encode("utf-8"))
                        visit(item, depth + 1)
                    if is_stream:
                        data = value.get_data()
                        emit(b"data", hashlib.sha256(data).digest())
                        emit(b"size", str(len(data)).encode("ascii"))
                else:
                    emit(b"array", str(len(value)).encode("ascii"))
                    for item in value:
                        visit(item, depth + 1)
                emit(b"end")
            elif isinstance(value, NullObject) or value is None:
                emit(b"null")
            elif isinstance(value, BooleanObject):
                emit(b"bool", b"1" if value.value else b"0")
            elif isinstance(value, NameObject):
                emit(b"name", str(value).encode("utf-8"))
            elif isinstance(value, TextStringObject):
                emit(b"text", str(value).encode("utf-8", "surrogatepass"))
            elif isinstance(value, ByteStringObject):
                emit(b"bytes", bytes(value))
            elif isinstance(value, (NumberObject, FloatObject)):
                number = repr(float(value)) if isinstance(value, FloatObject) else str(int(value))
                emit(b"number", str(Decimal(number).normalize()).encode("ascii"))
            else:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)

        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                encryption = reader.trailer["/Encrypt"]
                if (encryption.get("/Filter") != "/Standard"
                        or encryption.get("/V") != 5 or encryption.get("/R") != 6
                        or encryption.get("/Length") != 256
                        or encryption.get("/StmF") != "/StdCF"
                        or encryption.get("/StrF") != "/StdCF"
                        or encryption["/CF"]["/StdCF"].get("/CFM") != "/AESV3"
                        or int(encryption["/P"]) >= 0
                        or int(encryption["/P"]) & 0xFFFFF0C0 != 0xFFFFF0C0
                        or int(encryption["/P"]) & 3):
                    raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
                if password is None or int(reader.decrypt(password)) != 1:
                    raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            root = _resolve_pdf_object(reader.trailer.raw_get("/Root"))
            if not isinstance(root, DictionaryObject) or root.get("/Type") != "/Catalog":
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            for key in ("/Root", "/Info"):
                emit(b"trailer-key", key.encode("ascii"))
                visit(reader.trailer.raw_get(key) if key in reader.trailer else None)
        return digest.digest()
    except PdfLockPreservationError:
        raise
    except Exception as exc:
        raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc


def _verify_page_appearance(source: Path, output: Path, *, password: str) -> None:
    """Also compare text, page geometry and rendered appearance, including widgets.

    Render one 512-point tile at a time to bound pixmap memory. This complements
    the structural check; a render alone cannot verify interactive semantics.
    """
    try:
        with fitz.open(source) as before, fitz.open(output) as after:
            if after.authenticate(password) != 2 or before.page_count != after.page_count:
                raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
            for index in range(before.page_count):
                left, right = before[index], after[index]
                if (left.rect != right.rect or left.mediabox != right.mediabox
                        or left.cropbox != right.cropbox or left.rotation != right.rotation
                        or left.get_text("text") != right.get_text("text")):
                    raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
                rect = left.rect
                if not rect.is_valid or rect.width * rect.height > 16_000_000:
                    raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
                for y in range(0, int(rect.height) + 1, 512):
                    for x in range(0, int(rect.width) + 1, 512):
                        clip = fitz.Rect(x, y, min(x + 512, rect.width), min(y + 512, rect.height))
                        if clip.is_empty:
                            continue
                        a = left.get_pixmap(clip=clip, colorspace=fitz.csRGB, alpha=False, annots=True)
                        b = right.get_pixmap(clip=clip, colorspace=fitz.csRGB, alpha=False, annots=True)
                        if (a.width, a.height, a.samples) != (b.width, b.height, b.samples):
                            raise PdfLockPreservationError(_PRESERVATION_MESSAGE)
    except PdfLockPreservationError:
        raise
    except Exception as exc:
        raise PdfLockPreservationError(_PRESERVATION_MESSAGE) from exc


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
    else:
        allow_printing = bool(getattr(policy, "allow_printing", False))
        allow_copying = bool(getattr(policy, "allow_copying", False))
        allow_modifying = bool(getattr(policy, "allow_modifying", False))
        allow_annotations = bool(getattr(policy, "allow_annotations", False))
        allow_form_filling = bool(getattr(policy, "allow_form_filling", False))

    permissions = 0
    if allow_printing:
        permissions |= _PERMISSION_PRINT | _PERMISSION_PRINT_HIGH_QUALITY
    if allow_copying:
        permissions |= _PERMISSION_COPY
    if allow_modifying:
        permissions |= _PERMISSION_MODIFY | _PERMISSION_ASSEMBLE
    if allow_annotations:
        permissions |= _PERMISSION_ANNOTATE
    if allow_form_filling or allow_annotations:
        permissions |= _PERMISSION_FORM
    # Accessibility is always available; retain the legacy request field only
    # for compatibility. Annotation permission also authorizes form filling.
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
            if authentication != 2:
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
    "SignedPdfLockError",
    "PdfLockPreservationError",
    "LocalPdfLockBackend",
    "PdfLockBackend",
    "lock_pdf",
]
