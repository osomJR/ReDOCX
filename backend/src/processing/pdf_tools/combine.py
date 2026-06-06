from __future__ import annotations

"""
ReDOCX PDF Tools - Combine PDF processing.

This module is intentionally a low-level processing layer:
- it performs real PDF combination work;
- it preserves frontend ordering exactly;
- it can persist the generated artifact through the existing storage layer;
- it does not build AnalyzerResponse objects directly.

Expected service-layer usage:
    artifact = combine_pdfs([...], output_filename="combined-document.pdf")
    result = build_combine_pdf_result(...artifact fields...)
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Optional, Protocol, Sequence
import mimetypes
import os
import re
import shutil

import fitz  # PyMuPDF


MAX_COMBINE_PDF_FILES = 10
SUPPORTED_PDF_MIME_TYPES = {"application/pdf", "application/x-pdf"}


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
class PdfSource:
    """
    Real filesystem source used by the PDF processing engine.

    display_name is what you want shown back to the user; path is the actual
    readable local file path.
    """

    path: str
    display_name: Optional[str] = None


@dataclass(frozen=True)
class CombinedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    source_file_count: int
    source_filenames: list[str]
    combined_page_count: int
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


SourcePathResolver = Callable[[Any], str]


class PdfCombineBackend(Protocol):
    def combine(
        self,
        *,
        sources: Sequence[str | Path | PdfSource | Any],
        output_filename: str = "combined-document.pdf",
        preserve_bookmarks: bool = True,
        preserve_metadata: bool = False,
        source_path_resolver: Optional[SourcePathResolver] = None,
    ) -> CombinedPdfArtifact:
        ...


class PyMuPDFCombineBackend:
    """
    Production-ready local combine backend built on PyMuPDF.

    It rejects encrypted/password-protected PDFs because your validation contract
    currently rejects them too. Add an explicit unlock workflow before allowing
    encrypted sources here.
    """

    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/combine",
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)

    def combine(
        self,
        *,
        sources: Sequence[str | Path | PdfSource | Any],
        output_filename: str = "combined-document.pdf",
        preserve_bookmarks: bool = True,
        preserve_metadata: bool = False,
        source_path_resolver: Optional[SourcePathResolver] = None,
    ) -> CombinedPdfArtifact:
        normalized_sources = _normalize_sources(sources, resolver=source_path_resolver)
        _validate_source_count(normalized_sources)
        output_name = _normalize_pdf_filename(output_filename, default="combined-document.pdf")

        with TemporaryDirectory(prefix="redocx-combine-") as workdir:
            output_path = Path(workdir) / output_name
            combined_page_count = self._combine_to_path(
                normalized_sources,
                output_path=output_path,
                preserve_bookmarks=preserve_bookmarks,
                preserve_metadata=preserve_metadata,
            )

            persisted_path, storage_key, download_url = _persist_or_copy(
                output_path,
                output_name=output_name,
                storage_backend=self.storage_backend,
                artifacts_dir=self.artifacts_dir,
            )

        return CombinedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=_get_file_size_mb(persisted_path),
            file_path=str(persisted_path),
            source_file_count=len(normalized_sources),
            source_filenames=[item.display_name or Path(item.path).name for item in normalized_sources],
            combined_page_count=combined_page_count,
            storage_key=storage_key,
            download_url=download_url,
        )

    @staticmethod
    def _combine_to_path(
        sources: Sequence[PdfSource],
        *,
        output_path: Path,
        preserve_bookmarks: bool,
        preserve_metadata: bool,
    ) -> int:
        output_pdf = fitz.open()
        toc: list[list[Any]] = []
        first_metadata: dict[str, Any] | None = None
        page_offset = 0

        try:
            for source in sources:
                source_path = Path(source.path)
                with fitz.open(source_path) as src:
                    _reject_encrypted_pdf(src, source_path)

                    if first_metadata is None:
                        first_metadata = dict(src.metadata or {})

                    page_count = int(src.page_count)
                    output_pdf.insert_pdf(src)

                    if preserve_bookmarks:
                        toc.extend(_offset_toc(src.get_toc(simple=False), page_offset))

                    page_offset += page_count

            if preserve_metadata and first_metadata:
                clean_metadata = {k: v for k, v in first_metadata.items() if isinstance(v, str)}
                output_pdf.set_metadata(clean_metadata)

            if preserve_bookmarks and toc:
                try:
                    output_pdf.set_toc(toc)
                except Exception:
                    # Broken source outlines should not make combine fail.
                    pass

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_pdf.save(
                output_path,
                garbage=4,
                deflate=True,
                clean=True,
            )
            return int(output_pdf.page_count)
        finally:
            output_pdf.close()


# Public convenience API -----------------------------------------------------


def combine_pdfs(
    sources: Sequence[str | Path | PdfSource | Any],
    *,
    output_filename: str = "combined-document.pdf",
    preserve_bookmarks: bool = True,
    preserve_metadata: bool = False,
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/combine",
    source_path_resolver: Optional[SourcePathResolver] = None,
) -> CombinedPdfArtifact:
    backend = PyMuPDFCombineBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
    )
    return backend.combine(
        sources=sources,
        output_filename=output_filename,
        preserve_bookmarks=preserve_bookmarks,
        preserve_metadata=preserve_metadata,
        source_path_resolver=source_path_resolver,
    )


# Internal helpers -----------------------------------------------------------


def _normalize_sources(
    sources: Sequence[str | Path | PdfSource | Any],
    *,
    resolver: Optional[SourcePathResolver] = None,
) -> list[PdfSource]:
    if not sources:
        raise ValueError("Combine PDF requires at least 2 source PDFs.")

    normalized: list[PdfSource] = []
    seen_paths: set[str] = set()

    for raw in sources:
        if isinstance(raw, PdfSource):
            source = raw
        elif isinstance(raw, (str, Path)):
            source = PdfSource(path=str(raw), display_name=Path(raw).name)
        else:
            resolved_path = resolver(raw) if resolver else _source_path_from_payload_like(raw)
            display_name = getattr(raw, "filename", None) or Path(resolved_path).name
            source = PdfSource(path=resolved_path, display_name=str(display_name))

        path = _require_pdf_path(source.path)
        key = str(path.resolve())
        if key in seen_paths:
            raise ValueError("Combine PDF input documents must not contain duplicate source paths.")
        seen_paths.add(key)
        normalized.append(PdfSource(path=str(path), display_name=source.display_name or path.name))

    return normalized


def _source_path_from_payload_like(value: Any) -> str:
    """
    Best-effort resolver for PdfFilePayload-like objects.

    Your extraction.py may set filename to only a display name; in production,
    pdf_tools_service.py should pass a resolver that maps storage_key/upload_id
    to a real local file path. This fallback accepts only fields that already
    resolve to a readable path.
    """
    for field_name in ("local_path", "file_path", "path", "storage_key", "filename"):
        candidate = getattr(value, field_name, None)
        if isinstance(candidate, str) and candidate.strip() and Path(candidate).exists():
            return candidate.strip()
    raise ValueError(
        "Could not resolve PDF source path. Pass source paths directly or provide "
        "source_path_resolver to map PdfFilePayload storage_key/upload_id to a local file."
    )


def _validate_source_count(sources: Sequence[PdfSource]) -> None:
    if len(sources) < 2:
        raise ValueError("Combine PDF requires at least 2 PDF files.")
    if len(sources) > MAX_COMBINE_PDF_FILES:
        raise ValueError(f"Combine PDF supports at most {MAX_COMBINE_PDF_FILES} PDF files.")


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")

    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and guessed.lower() not in SUPPORTED_PDF_MIME_TYPES:
        raise ValueError(f"PDF source has unsupported MIME type {guessed}: {path.name}")
    return path


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
        raise ValueError(
            f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}"
        )


def _offset_toc(toc: list[list[Any]], page_offset: int) -> list[list[Any]]:
    adjusted: list[list[Any]] = []
    for item in toc:
        if len(item) < 3:
            continue
        cloned = list(item)
        try:
            cloned[2] = int(cloned[2]) + page_offset
        except (TypeError, ValueError):
            continue
        adjusted.append(cloned)
    return adjusted


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    name = Path(raw).name
    stem = _safe_stem(Path(name).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "document"


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
        default_storage = _try_default_storage(base_dir=str(artifacts_dir))
        if default_storage is not None:
            storage_backend = default_storage

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
    "CombinedPdfArtifact",
    "PdfSource",
    "PyMuPDFCombineBackend",
    "combine_pdfs",
]
