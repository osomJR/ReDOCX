from __future__ import annotations

"""
ReDOCX PDF Tools - Split PDF processing.

Supported split modes:
- every_page
- extract_selected_pages
- page_ranges

The service layer should wrap SplitPdfArtifact into your schema SplitPdfResult
with validation.build_split_pdf_result(...).
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol, Sequence
import mimetypes
import os
import re
import shutil
import zipfile

import fitz  # PyMuPDF


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
class PdfPageRangeSpec:
    start_page: int
    end_page: int


@dataclass(frozen=True)
class PdfOutputArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    page_count: Optional[int] = None
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


@dataclass(frozen=True)
class SplitPdfArtifact:
    mode: str
    output_files: list[PdfOutputArtifact]
    archive_file: Optional[PdfOutputArtifact] = None


class PdfSplitBackend(Protocol):
    def split(
        self,
        *,
        source_path: str | Path,
        mode: str | Any,
        selected_pages: Optional[Sequence[int]] = None,
        page_ranges: Optional[Sequence[PdfPageRangeSpec | tuple[int, int] | Any]] = None,
        output_basename: str = "split-document",
    ) -> SplitPdfArtifact:
        ...


class PyMuPDFSplitBackend:
    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/split",
        archive_multiple_outputs: bool = True,
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)
        self.archive_multiple_outputs = archive_multiple_outputs

    def split(
        self,
        *,
        source_path: str | Path,
        mode: str | Any,
        selected_pages: Optional[Sequence[int]] = None,
        page_ranges: Optional[Sequence[PdfPageRangeSpec | tuple[int, int] | Any]] = None,
        output_basename: str = "split-document",
    ) -> SplitPdfArtifact:
        source = _require_pdf_path(source_path)
        split_mode = _mode_value(mode)
        basename = _safe_stem(output_basename or source.stem)
        selected = list(selected_pages or [])
        ranges = _normalize_ranges(page_ranges or [])

        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            page_count = int(pdf.page_count)
            plan = _build_split_plan(
                mode=split_mode,
                page_count=page_count,
                selected_pages=selected,
                page_ranges=ranges,
            )

            with TemporaryDirectory(prefix="redocx-split-") as workdir:
                workdir_path = Path(workdir)
                temp_outputs: list[tuple[Path, int]] = []

                for index, pages in enumerate(plan, start=1):
                    output_name = _output_name_for_split(
                        basename=basename,
                        mode=split_mode,
                        index=index,
                        pages=pages,
                    )
                    output_path = workdir_path / output_name
                    _write_pages(pdf, pages=pages, output_path=output_path)
                    temp_outputs.append((output_path, len(pages)))

                persisted_outputs = [
                    _artifact_from_persisted(
                        path,
                        page_count=pages,
                        storage_backend=self.storage_backend,
                        artifacts_dir=self.artifacts_dir,
                    )
                    for path, pages in temp_outputs
                ]

                archive_artifact: Optional[PdfOutputArtifact] = None
                if self.archive_multiple_outputs and len(temp_outputs) > 1:
                    archive_path = workdir_path / f"{basename}.zip"
                    _zip_outputs(archive_path, [path for path, _ in temp_outputs])
                    archive_artifact = _artifact_from_persisted(
                        archive_path,
                        page_count=None,
                        storage_backend=self.storage_backend,
                        artifacts_dir=self.artifacts_dir,
                    )

        return SplitPdfArtifact(
            mode=split_mode,
            output_files=persisted_outputs,
            archive_file=archive_artifact,
        )


# Public convenience API -----------------------------------------------------


def split_pdf(
    source_path: str | Path,
    *,
    mode: str | Any,
    selected_pages: Optional[Sequence[int]] = None,
    page_ranges: Optional[Sequence[PdfPageRangeSpec | tuple[int, int] | Any]] = None,
    output_basename: str = "split-document",
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/split",
    archive_multiple_outputs: bool = True,
) -> SplitPdfArtifact:
    backend = PyMuPDFSplitBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        archive_multiple_outputs=archive_multiple_outputs,
    )
    return backend.split(
        source_path=source_path,
        mode=mode,
        selected_pages=selected_pages,
        page_ranges=page_ranges,
        output_basename=output_basename,
    )


# Internal helpers -----------------------------------------------------------


def _mode_value(value: str | Any) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw).strip().lower()
    allowed = {"every_page", "extract_selected_pages", "page_ranges"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported split mode: {raw}. Expected one of: {', '.join(sorted(allowed))}.")
    return normalized


def _normalize_ranges(values: Sequence[PdfPageRangeSpec | tuple[int, int] | Any]) -> list[PdfPageRangeSpec]:
    ranges: list[PdfPageRangeSpec] = []
    for raw in values:
        if isinstance(raw, PdfPageRangeSpec):
            item = raw
        elif isinstance(raw, tuple) and len(raw) == 2:
            item = PdfPageRangeSpec(start_page=int(raw[0]), end_page=int(raw[1]))
        else:
            item = PdfPageRangeSpec(
                start_page=int(getattr(raw, "start_page")),
                end_page=int(getattr(raw, "end_page")),
            )
        if item.start_page < 1 or item.end_page < 1:
            raise ValueError("page_ranges must contain pages >= 1.")
        if item.end_page < item.start_page:
            raise ValueError("page range end_page must be >= start_page.")
        ranges.append(item)
    return ranges


def _build_split_plan(
    *,
    mode: str,
    page_count: int,
    selected_pages: Sequence[int],
    page_ranges: Sequence[PdfPageRangeSpec],
) -> list[list[int]]:
    if page_count < 1:
        raise ValueError("Source PDF has no pages.")

    if mode == "every_page":
        if selected_pages or page_ranges:
            raise ValueError("every_page mode must not include selected_pages or page_ranges.")
        return [[page] for page in range(1, page_count + 1)]

    if mode == "extract_selected_pages":
        if not selected_pages:
            raise ValueError("extract_selected_pages mode requires selected_pages.")
        if page_ranges:
            raise ValueError("extract_selected_pages mode must not include page_ranges.")
        pages = [int(page) for page in selected_pages]
        if any(page < 1 for page in pages):
            raise ValueError("selected_pages must be >= 1.")
        if len(set(pages)) != len(pages):
            raise ValueError("selected_pages cannot contain duplicates.")
        if max(pages) > page_count:
            raise ValueError("selected_pages cannot exceed source PDF page_count.")
        return [pages]

    if mode == "page_ranges":
        if selected_pages:
            raise ValueError("page_ranges mode must not include selected_pages.")
        if not page_ranges:
            raise ValueError("page_ranges mode requires page_ranges.")
        plan: list[list[int]] = []
        for item in page_ranges:
            if item.end_page > page_count:
                raise ValueError("page_ranges cannot exceed source PDF page_count.")
            plan.append(list(range(item.start_page, item.end_page + 1)))
        return plan

    raise ValueError(f"Unsupported split mode: {mode}")


def _write_pages(source_pdf: fitz.Document, *, pages: Sequence[int], output_path: Path) -> None:
    if not pages:
        raise ValueError("Cannot create split output with no pages.")
    output = fitz.open()
    try:
        for page_number in pages:
            zero_based = int(page_number) - 1
            output.insert_pdf(source_pdf, from_page=zero_based, to_page=zero_based)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output.save(output_path, garbage=4, deflate=True, clean=True)
    finally:
        output.close()


def _output_name_for_split(*, basename: str, mode: str, index: int, pages: Sequence[int]) -> str:
    if mode == "every_page":
        return f"{basename}-page-{pages[0]}.pdf"
    if mode == "extract_selected_pages":
        return f"{basename}-selected-pages.pdf"
    first = pages[0]
    last = pages[-1]
    if first == last:
        return f"{basename}-page-{first}.pdf"
    return f"{basename}-range-{first}-{last}.pdf"


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")
    return path


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
        raise ValueError(f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}")


def _artifact_from_persisted(
    path: Path,
    *,
    page_count: Optional[int],
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
) -> PdfOutputArtifact:
    persisted_path, storage_key, download_url = _persist_or_copy(
        path,
        output_name=path.name,
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
    )
    return PdfOutputArtifact(
        file_name=path.name,
        file_extension=path.suffix.lower().lstrip("."),
        file_size_mb=_get_file_size_mb(persisted_path),
        file_path=str(persisted_path),
        page_count=page_count,
        storage_key=storage_key,
        download_url=download_url,
    )


def _zip_outputs(archive_path: Path, output_paths: Sequence[Path]) -> None:
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in output_paths:
            archive.write(item, arcname=item.name)


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "split-document"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated output is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or ("application/zip" if str(path).lower().endswith(".zip") else "application/pdf")


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
        return stored_path, getattr(stored, "storage_key", None), getattr(stored, "download_url", None)

    artifacts = Path(artifacts_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / output_name
    if destination.exists():
        destination = artifacts / f"{Path(output_name).stem}-{os.urandom(4).hex()}{Path(output_name).suffix}"
    shutil.copy2(source_path, destination)
    return destination, None, None


def _try_default_storage(*, base_dir: str) -> Optional[StorageBackend]:
    try:
        from backend.src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


__all__ = [
    "PdfOutputArtifact",
    "PdfPageRangeSpec",
    "PyMuPDFSplitBackend",
    "SplitPdfArtifact",
    "split_pdf",
]
