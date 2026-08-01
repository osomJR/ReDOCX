from __future__ import annotations

"""
ReDOCX PDF Tools - Preview generation.

Provides two preview outputs:
1. page image previews for browser UI thumbnails/canvases;
2. a preview PDF artifact compatible with schema.PdfPreviewResult.

This module is also reusable by E-Signature after each signer completes a step.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol, Sequence
import mimetypes
import os
import re
import shutil

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
class PdfPagePreviewArtifact:
    page_number: int
    width: int
    height: int
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


@dataclass(frozen=True)
class PdfPreviewArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    page_count: Optional[int] = None
    preview_stage: Optional[str] = None
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


@dataclass(frozen=True)
class PdfPreviewBundle:
    preview_pdf: PdfPreviewArtifact
    page_images: list[PdfPagePreviewArtifact]


class PdfPreviewBackend(Protocol):
    def generate_preview_pdf(
        self,
        *,
        source_path: str | Path,
        output_filename: str = "document-preview.pdf",
        preview_stage: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> PdfPreviewArtifact:
        ...

    def render_page_images(
        self,
        *,
        source_path: str | Path,
        pages: Optional[Sequence[int]] = None,
        image_format: str = "png",
        zoom: float = 1.5,
    ) -> list[PdfPagePreviewArtifact]:
        ...


class PyMuPDFPreviewBackend:
    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/preview",
        image_artifacts_dir: str | Path = "artifacts/pdf_tools/preview/pages",
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)
        self.image_artifacts_dir = Path(image_artifacts_dir)

    def generate_preview_pdf(
        self,
        *,
        source_path: str | Path,
        output_filename: str = "document-preview.pdf",
        preview_stage: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> PdfPreviewArtifact:
        source = _require_pdf_path(source_path)
        output_name = _normalize_pdf_filename(output_filename, default="document-preview.pdf")

        with fitz.open(source) as src:
            _reject_encrypted_pdf(src, source)
            with TemporaryDirectory(prefix="redocx-preview-") as workdir:
                output_path = Path(workdir) / output_name
                preview = fitz.open()
                try:
                    if max_pages is None:
                        page_limit = int(src.page_count)
                    else:
                        requested_pages = int(max_pages)
                        if requested_pages < 1:
                            raise ValueError("max_pages must be at least 1 when provided.")
                        page_limit = min(int(src.page_count), requested_pages)
                    for index in range(page_limit):
                        preview.insert_pdf(src, from_page=index, to_page=index)
                    preview.save(output_path, garbage=4, deflate=True, clean=True)
                finally:
                    preview.close()

                persisted_path, storage_key, download_url = _persist_or_copy(
                    output_path,
                    output_name=output_name,
                    storage_backend=self.storage_backend,
                    artifacts_dir=self.artifacts_dir,
                )

            page_count = page_limit

        return PdfPreviewArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=_get_file_size_mb(persisted_path),
            file_path=str(persisted_path),
            page_count=page_count,
            preview_stage=preview_stage,
            storage_key=storage_key,
            download_url=download_url,
        )

    def render_page_images(
        self,
        *,
        source_path: str | Path,
        pages: Optional[Sequence[int]] = None,
        image_format: str = "png",
        zoom: float = 1.5,
        output_basename: str = "page-preview",
    ) -> list[PdfPagePreviewArtifact]:
        source = _require_pdf_path(source_path)
        fmt = _normalize_image_format(image_format)
        basename = _safe_stem(output_basename or source.stem)
        zoom_value = _normalize_zoom(zoom)

        artifacts: list[PdfPagePreviewArtifact] = []
        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            page_numbers = _normalize_pages(pages, page_count=int(pdf.page_count))
            matrix = fitz.Matrix(zoom_value, zoom_value)

            with TemporaryDirectory(prefix="redocx-page-preview-") as workdir:
                workdir_path = Path(workdir)
                for page_number in page_numbers:
                    page = pdf[page_number - 1]
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    output_name = f"{basename}-page-{page_number}.{fmt}"
                    output_path = workdir_path / output_name
                    pix.save(str(output_path))

                    persisted_path, storage_key, download_url = _persist_or_copy(
                        output_path,
                        output_name=output_name,
                        storage_backend=self.storage_backend,
                        artifacts_dir=self.image_artifacts_dir,
                    )
                    artifacts.append(
                        PdfPagePreviewArtifact(
                            page_number=page_number,
                            width=int(pix.width),
                            height=int(pix.height),
                            file_name=output_name,
                            file_extension=fmt,
                            file_size_mb=_get_file_size_mb(persisted_path),
                            file_path=str(persisted_path),
                            storage_key=storage_key,
                            download_url=download_url,
                        )
                    )
        return artifacts

    def generate_bundle(
        self,
        *,
        source_path: str | Path,
        pages: Optional[Sequence[int]] = None,
        image_format: str = "png",
        zoom: float = 1.5,
        output_filename: str = "document-preview.pdf",
        preview_stage: Optional[str] = None,
    ) -> PdfPreviewBundle:
        preview_pdf = self.generate_preview_pdf(
            source_path=source_path,
            output_filename=output_filename,
            preview_stage=preview_stage,
        )
        page_images = self.render_page_images(
            source_path=source_path,
            pages=pages,
            image_format=image_format,
            zoom=zoom,
            output_basename=Path(output_filename).stem,
        )
        return PdfPreviewBundle(preview_pdf=preview_pdf, page_images=page_images)


# Public convenience API -----------------------------------------------------


def generate_preview_pdf(
    source_path: str | Path,
    *,
    output_filename: str = "document-preview.pdf",
    preview_stage: Optional[str] = None,
    max_pages: Optional[int] = None,
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/preview",
) -> PdfPreviewArtifact:
    backend = PyMuPDFPreviewBackend(storage_backend=storage_backend, artifacts_dir=artifacts_dir)
    return backend.generate_preview_pdf(
        source_path=source_path,
        output_filename=output_filename,
        preview_stage=preview_stage,
        max_pages=max_pages,
    )


def render_pdf_page_previews(
    source_path: str | Path,
    *,
    pages: Optional[Sequence[int]] = None,
    image_format: str = "png",
    zoom: float = 1.5,
    output_basename: str = "page-preview",
    storage_backend: Optional[StorageBackend] = None,
    image_artifacts_dir: str | Path = "artifacts/pdf_tools/preview/pages",
) -> list[PdfPagePreviewArtifact]:
    backend = PyMuPDFPreviewBackend(storage_backend=storage_backend, image_artifacts_dir=image_artifacts_dir)
    return backend.render_page_images(
        source_path=source_path,
        pages=pages,
        image_format=image_format,
        zoom=zoom,
        output_basename=output_basename,
    )


def generate_pdf_preview_bundle(
    source_path: str | Path,
    *,
    pages: Optional[Sequence[int]] = None,
    image_format: str = "png",
    zoom: float = 1.5,
    output_filename: str = "document-preview.pdf",
    preview_stage: Optional[str] = None,
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/preview",
    image_artifacts_dir: str | Path = "artifacts/pdf_tools/preview/pages",
) -> PdfPreviewBundle:
    backend = PyMuPDFPreviewBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        image_artifacts_dir=image_artifacts_dir,
    )
    return backend.generate_bundle(
        source_path=source_path,
        pages=pages,
        image_format=image_format,
        zoom=zoom,
        output_filename=output_filename,
        preview_stage=preview_stage,
    )


# Internal helpers -----------------------------------------------------------


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


def _normalize_pages(pages: Optional[Sequence[int]], *, page_count: int) -> list[int]:
    if page_count < 1:
        raise ValueError("Source PDF has no pages.")
    if pages is None:
        return list(range(1, page_count + 1))
    normalized = [int(page) for page in pages]
    if not normalized:
        raise ValueError("pages cannot be empty when provided.")
    if len(set(normalized)) != len(normalized):
        raise ValueError("pages cannot contain duplicates.")
    if min(normalized) < 1 or max(normalized) > page_count:
        raise ValueError("pages must be within source PDF page range.")
    return normalized


def _normalize_image_format(value: str) -> str:
    normalized = (value or "png").strip().lower().lstrip(".")
    if normalized in {"jpg", "jpeg"}:
        return "jpg"
    if normalized == "png":
        return "png"
    raise ValueError("image_format must be 'png', 'jpg', or 'jpeg'.")


def _normalize_zoom(value: float) -> float:
    zoom = float(value)
    if zoom <= 0:
        raise ValueError("zoom must be greater than 0.")
    return max(0.5, min(zoom, 4.0))


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    stem = _safe_stem(Path(raw).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "document-preview"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated preview is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed:
        return guessed
    if str(path).lower().endswith(".png"):
        return "image/png"
    if str(path).lower().endswith(('.jpg', '.jpeg')):
        return "image/jpeg"
    return "application/pdf"


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
    "PdfPagePreviewArtifact",
    "PdfPreviewArtifact",
    "PdfPreviewBundle",
    "PyMuPDFPreviewBackend",
    "generate_pdf_preview_bundle",
    "generate_preview_pdf",
    "render_pdf_page_previews",
]