from __future__ import annotations

"""
Preview generation for ReDOCX E-Signature.

This module produces:
- schema.PdfPreviewResult for the current signed PDF version
- schema.ESignatureStepPreview after each signer completes
- optional PNG page previews for frontend thumbnails
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol
import shutil

import fitz  # PyMuPDF

try:
    from backend.src.schema import ESignatureStepPreview, PdfPreviewResult
    from backend.src.validation import build_pdf_preview_result
    from backend.src.storage.artifacts import LocalArtifactStorage, guess_content_type
except ImportError:
    from ...schema import ESignatureStepPreview, PdfPreviewResult
    from ...validation import build_pdf_preview_result
    try:
        from ...storage.artifacts import LocalArtifactStorage, guess_content_type
    except ImportError:  # pragma: no cover - for standalone test environments
        LocalArtifactStorage = None  # type: ignore
        def guess_content_type(_path: str) -> str:
            return "application/pdf"



class StorageBackend(Protocol):
    """Storage adapter protocol used by e-signature preview generation."""

    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class PagePreviewImage:
    page_number: int
    filename: str
    path: str
    file_size_mb: float
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_size_mb(path: str | Path) -> float:
    return round(Path(path).stat().st_size / (1024 * 1024), 4)


def _page_count(path: str | Path) -> int:
    with fitz.open(path) as pdf:
        return int(pdf.page_count)


def _default_storage(storage_backend: Optional[StorageBackend]):
    if storage_backend is not None:
        return storage_backend
    if LocalArtifactStorage is None:
        return None
    return LocalArtifactStorage(base_dir="artifacts/esignature/previews")


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


def generate_preview_pdf(
    *,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    preview_stage: str,
    storage_backend: Optional[StorageBackend] = None,
    algorithm_version: Optional[str] = None,
) -> PdfPreviewResult:
    source = Path(source_pdf_path)
    if not source.exists():
        raise FileNotFoundError(f"Source PDF not found: {source}")
    output = Path(output_pdf_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(source) as pdf:
        pdf.set_metadata({**(pdf.metadata or {}), "subject": preview_stage})
        pdf.save(output, garbage=4, deflate=True)

    storage_key, download_url = _persist_optional(output, storage_backend=storage_backend)
    return build_pdf_preview_result(
        filename=output.name,
        file_size_mb=_file_size_mb(output),
        page_count=_page_count(output),
        preview_stage=preview_stage,
        storage_key=storage_key,
        download_url=download_url,
        algorithm_version=algorithm_version,
    )


def generate_step_preview(
    *,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    signer_email: str,
    signer_name: str,
    signing_order: int,
    preview_stage: Optional[str] = None,
    storage_backend: Optional[StorageBackend] = None,
    algorithm_version: Optional[str] = None,
) -> ESignatureStepPreview:
    created_at = utcnow_iso()
    stage = preview_stage or f"signed_by_{signer_email.strip().lower()}"
    preview_pdf = generate_preview_pdf(
        source_pdf_path=source_pdf_path,
        output_pdf_path=output_pdf_path,
        preview_stage=stage,
        storage_backend=storage_backend,
        algorithm_version=algorithm_version,
    )
    return ESignatureStepPreview(
        signer_email=signer_email.strip().lower(),
        signer_name=signer_name,
        signing_order=signing_order,
        preview_pdf=preview_pdf,
        created_at_iso=created_at,
    )


def render_pdf_pages_to_png(
    *,
    source_pdf_path: str | Path,
    output_dir: str | Path,
    zoom: float = 1.4,
    max_pages: Optional[int] = None,
    storage_backend: Optional[StorageBackend] = None,
) -> list[PagePreviewImage]:
    source = Path(source_pdf_path)
    if not source.exists():
        raise FileNotFoundError(f"Source PDF not found: {source}")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    matrix = fitz.Matrix(zoom, zoom)
    rendered: list[PagePreviewImage] = []

    with fitz.open(source) as pdf:
        limit = min(pdf.page_count, max_pages or pdf.page_count)
        for index in range(limit):
            page = pdf[index]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = output / f"{source.stem}.page-{index + 1}.png"
            pix.save(str(image_path))
            storage_key, download_url = _persist_optional(image_path, storage_backend=storage_backend)
            rendered.append(
                PagePreviewImage(
                    page_number=index + 1,
                    filename=image_path.name,
                    path=str(image_path),
                    file_size_mb=_file_size_mb(image_path),
                    storage_key=storage_key,
                    download_url=download_url,
                )
            )
    return rendered


__all__ = [
    "PagePreviewImage",
    "utcnow_iso",
    "generate_preview_pdf",
    "generate_step_preview",
    "render_pdf_pages_to_png",
]
