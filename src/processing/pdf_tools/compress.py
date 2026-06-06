from __future__ import annotations

"""
ReDOCX PDF Tools - Compress PDF processing.

Compression strategy:
1. Prefer Ghostscript when available because it performs real PDF/image
   downsampling compression.
2. Fall back to PyMuPDF save optimization when Ghostscript is unavailable.
3. Never silently return a larger output unless allow_larger_output=True.

The service layer can wrap CompressedPdfArtifact into validation.build_compress_pdf_result(...).
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol
import mimetypes
import os
import re
import shutil
import subprocess

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
class CompressedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    compression_level: str
    original_file_size_mb: float
    compressed_file_size_mb: float
    estimated_output_file_size_mb: Optional[float] = None
    compression_ratio: Optional[float] = None
    engine: str = "unknown"
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class PdfCompressionBackend(Protocol):
    def compress(
        self,
        *,
        source_path: str | Path,
        compression_level: str | Any = "balanced",
        output_filename: str = "compressed-document.pdf",
    ) -> CompressedPdfArtifact:
        ...


class LocalPdfCompressionBackend:
    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/compress",
        allow_larger_output: bool = False,
        ghostscript_binary: Optional[str] = None,
        qpdf_binary: Optional[str] = None,
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)
        self.allow_larger_output = allow_larger_output
        self.ghostscript_binary = ghostscript_binary or _find_binary("gs") or _find_binary("gswin64c") or _find_binary("gswin32c")
        self.qpdf_binary = qpdf_binary or _find_binary("qpdf")

    def compress(
        self,
        *,
        source_path: str | Path,
        compression_level: str | Any = "balanced",
        output_filename: str = "compressed-document.pdf",
    ) -> CompressedPdfArtifact:
        source = _require_pdf_path(source_path)
        level = _compression_level_value(compression_level)
        output_name = _normalize_pdf_filename(output_filename, default="compressed-document.pdf")
        original_size_mb = _get_file_size_mb(source)

        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)

        with TemporaryDirectory(prefix="redocx-compress-") as workdir:
            workdir_path = Path(workdir)
            output_path = workdir_path / output_name
            engine = self._compress_to_path(source, output_path=output_path, level=level)

            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("PDF compression completed without producing a valid output file.")

            # If compression produces a larger file, use an optimized copy of the
            # original unless explicitly configured otherwise.
            if not self.allow_larger_output and output_path.stat().st_size > source.stat().st_size:
                fallback_path = workdir_path / f"{Path(output_name).stem}-optimized.pdf"
                engine = self._pymupdf_optimize(source, output_path=fallback_path)
                if fallback_path.exists() and fallback_path.stat().st_size <= source.stat().st_size:
                    output_path = fallback_path
                else:
                    shutil.copy2(source, output_path)
                    engine = "original-preserved-no-smaller-output"

            persisted_path, storage_key, download_url = _persist_or_copy(
                output_path,
                output_name=output_name,
                storage_backend=self.storage_backend,
                artifacts_dir=self.artifacts_dir,
            )

        compressed_size_mb = _get_file_size_mb(persisted_path)
        ratio = round(compressed_size_mb / original_size_mb, 4) if original_size_mb > 0 else None

        return CompressedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=compressed_size_mb,
            file_path=str(persisted_path),
            compression_level=level,
            original_file_size_mb=original_size_mb,
            compressed_file_size_mb=compressed_size_mb,
            estimated_output_file_size_mb=None,
            compression_ratio=ratio,
            engine=engine,
            storage_key=storage_key,
            download_url=download_url,
        )

    def _compress_to_path(self, source_path: Path, *, output_path: Path, level: str) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self.ghostscript_binary:
            try:
                self._ghostscript_compress(source_path, output_path=output_path, level=level)
                return "ghostscript"
            except Exception:
                # Fall back to local structural optimization below.
                output_path.unlink(missing_ok=True)

        if self.qpdf_binary:
            try:
                self._qpdf_linearize(source_path, output_path=output_path)
                return "qpdf-linearize"
            except Exception:
                output_path.unlink(missing_ok=True)

        return self._pymupdf_optimize(source_path, output_path=output_path)

    def _ghostscript_compress(self, source_path: Path, *, output_path: Path, level: str) -> None:
        if not self.ghostscript_binary:
            raise RuntimeError("Ghostscript binary not configured.")

        settings = {
            "small_file": "/screen",
            "balanced": "/ebook",
            "high_quality": "/printer",
        }[level]

        # The image downsampling switches make the levels meaningful while keeping
        # the command deterministic and safe for server execution.
        if level == "small_file":
            image_dpi = "96"
        elif level == "balanced":
            image_dpi = "150"
        else:
            image_dpi = "300"

        cmd = [
            self.ghostscript_binary,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.6",
            f"-dPDFSETTINGS={settings}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dColorImageResolution={image_dpi}",
            f"-dGrayImageResolution={image_dpi}",
            f"-dMonoImageResolution={image_dpi}",
            f"-sOutputFile={output_path}",
            str(source_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def _qpdf_linearize(self, source_path: Path, *, output_path: Path) -> None:
        if not self.qpdf_binary:
            raise RuntimeError("qpdf binary not configured.")
        cmd = [
            self.qpdf_binary,
            "--linearize",
            "--object-streams=generate",
            str(source_path),
            str(output_path),
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @staticmethod
    def _pymupdf_optimize(source_path: Path, *, output_path: Path) -> str:
        with fitz.open(source_path) as pdf:
            _reject_encrypted_pdf(pdf, source_path)
            pdf.save(
                output_path,
                garbage=4,
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                clean=True,
            )
        return "pymupdf-deflate"


# Public convenience API -----------------------------------------------------


def compress_pdf(
    source_path: str | Path,
    *,
    compression_level: str | Any = "balanced",
    output_filename: str = "compressed-document.pdf",
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/compress",
    allow_larger_output: bool = False,
    ghostscript_binary: Optional[str] = None,
    qpdf_binary: Optional[str] = None,
) -> CompressedPdfArtifact:
    backend = LocalPdfCompressionBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        allow_larger_output=allow_larger_output,
        ghostscript_binary=ghostscript_binary,
        qpdf_binary=qpdf_binary,
    )
    return backend.compress(
        source_path=source_path,
        compression_level=compression_level,
        output_filename=output_filename,
    )


# Internal helpers -----------------------------------------------------------


def _compression_level_value(value: str | Any) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw).strip().lower()
    allowed = {"small_file", "balanced", "high_quality"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported compression level: {raw}. Expected one of: {', '.join(sorted(allowed))}.")
    return normalized


def estimate_compressed_size_mb(original_size_mb: float, compression_level: str | Any) -> float:
    """Heuristic used for UI estimates before actual worker completion."""
    level = _compression_level_value(compression_level)
    factor = {"small_file": 0.35, "balanced": 0.55, "high_quality": 0.75}[level]
    return round(max(0.01, original_size_mb * factor), 4)


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


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    stem = _safe_stem(Path(raw).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "compressed-document"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated PDF is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/pdf"


def _find_binary(name: str) -> Optional[str]:
    return shutil.which(name)


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
        from src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


__all__ = [
    "CompressedPdfArtifact",
    "LocalPdfCompressionBackend",
    "compress_pdf",
    "estimate_compressed_size_mb",
]
