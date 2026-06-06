from __future__ import annotations

"""
V1 document-conversion processing.

Purpose:
- hold conversion-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless at the processor layer
- schema-agnostic: this module returns conversion artifact metadata
- provider-backed by default via concrete conversion backends
- performs real file conversion work for the contract-allowed pairs
- persists produced artifacts through a separate storage layer
- analyzer.py remains responsible for mapping ConversionArtifact into FileResult
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, NamedTemporaryFile
from typing import Optional, Protocol
import mimetypes
import os
import shutil
import subprocess

from PIL import Image, UnidentifiedImageError
from docx import Document
from docx.shared import Inches

from backend.src.storage.artifacts import (
    StorageBackend,
    LocalArtifactStorage,
)

try:
    from pdf2docx import Converter as PDFToDOCXConverter
except ImportError:  # pragma: no cover
    PDFToDOCXConverter = None


CONVERSION_RULES = """
TASK: DOCUMENT CONVERSION
RULES:
- Convert only between contract-allowed format pairs
- Preserve source content fidelity as much as the target format permits
- Do not add, remove, summarize, translate, or reinterpret content
- Output must be a downloadable file artifact, never inline text
- Conversion is format transformation only, not semantic editing
""".strip()

ALLOWED_CONVERSION_PAIRS: dict[str, set[str]] = {
    "pdf": {"docx"},
    "docx": {"pdf"},
    "jpg": {"pdf", "docx"},
    "jpeg": {"pdf", "docx"},
    "png": {"jpg", "jpeg"},
}

OUTPUT_EXTENSION_ALIASES: dict[str, str] = {
    "jpg": "jpg",
    "jpeg": "jpeg",
    "pdf": "pdf",
    "docx": "docx",
    "png": "png",
}


@dataclass(frozen=True)
class ConversionArtifact:
    """
    Schema-agnostic conversion artifact descriptor.

    file_path is the persisted artifact location, not the temporary conversion path.
    storage_key and download_url are exposed for future orchestration layers.
    """

    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class ConversionBackend(Protocol):
    """Provider interface for document-conversion runtime."""

    def convert(
        self,
        *,
        source_reference: str,
        input_format: str,
        output_format: str,
        planned_output_name: str,
    ) -> ConversionArtifact:
        ...


class RealConversionBackend:
    """
    Real conversion backend for the contract-allowed pairs.

    Supported conversions:
    - pdf -> docx      via pdf2docx
    - docx -> pdf      via LibreOffice headless conversion with isolated user profile
    - jpg/jpeg -> pdf  via Pillow PDF export
    - jpg/jpeg -> docx via python-docx image insertion
    - png -> jpg/jpeg  via Pillow image conversion
    """

    def __init__(self, storage_backend: Optional[StorageBackend] = None) -> None:
        self.storage_backend = storage_backend or LocalArtifactStorage()

    def convert(
        self,
        *,
        source_reference: str,
        input_format: str,
        output_format: str,
        planned_output_name: str,
    ) -> ConversionArtifact:
        normalized_input = _normalize_format(input_format)
        normalized_output = _normalize_format(output_format)
        source_path = Path(_normalize_source_reference(source_reference)).resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        planned_name = _normalize_file_name(planned_output_name)

        with TemporaryDirectory(prefix="convert-work-") as workdir:
            output_path = (Path(workdir) / planned_name).resolve()

            if normalized_input == "pdf" and normalized_output == "docx":
                self._convert_pdf_to_docx(source_path, output_path)
            elif normalized_input == "docx" and normalized_output == "pdf":
                self._convert_docx_to_pdf(source_path, output_path)
            elif normalized_input in {"jpg", "jpeg"} and normalized_output == "pdf":
                self._convert_image_to_pdf(source_path, output_path)
            elif normalized_input in {"jpg", "jpeg"} and normalized_output == "docx":
                self._convert_image_to_docx(source_path, output_path)
            elif normalized_input == "png" and normalized_output in {"jpg", "jpeg"}:
                self._convert_png_to_jpeg_family(source_path, output_path)
            else:
                raise ValueError(
                    f"Unsupported conversion pair: {normalized_input} -> {normalized_output}."
                )

            if not output_path.exists():
                raise RuntimeError("Conversion completed without producing an output file.")

            stored = self.storage_backend.persist(
                source_file_path=str(output_path),
                artifact_name=output_path.name,
                content_type=_guess_content_type(output_path),
            )

            file_size_mb = _get_file_size_mb(Path(stored.stored_path))

            return ConversionArtifact(
                file_name=output_path.name,
                file_extension=normalized_output,
                file_size_mb=file_size_mb,
                file_path=stored.stored_path,
                storage_key=stored.storage_key,
                download_url=stored.download_url,
            )

    def _convert_pdf_to_docx(self, source_path: Path, output_path: Path) -> None:
        if PDFToDOCXConverter is None:
            raise RuntimeError(
                "pdf2docx is required for pdf -> docx conversion but is not installed."
            )

        converter = PDFToDOCXConverter(str(source_path))
        try:
            converter.convert(str(output_path))
        finally:
            converter.close()

    def _convert_docx_to_pdf(self, source_path: Path, output_path: Path) -> None:
        soffice_bin = (
            os.getenv("SOFFICE_PATH")
            or shutil.which("soffice")
            or shutil.which("soffice.exe")
        )
        if not soffice_bin:
            raise RuntimeError(
                "LibreOffice not found. Set SOFFICE_PATH or add soffice/soffice.exe to PATH."
            )

        source_path = source_path.resolve()
        output_path = output_path.resolve()
        output_dir = output_path.parent.resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"Source DOCX not found: {source_path}")

        with TemporaryDirectory(prefix="libreoffice-profile-") as profile_dir:
            profile_uri = Path(profile_dir).resolve().as_uri()

            cmd = [
                soffice_bin,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile_uri}",
                "--convert-to",
                "pdf:writer_pdf_Export",
                "--outdir",
                str(output_dir),
                str(source_path),
            ]

            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )

        if result.returncode != 0:
            raise RuntimeError(
                "LibreOffice failed while converting docx to pdf.\n"
                f"command: {' '.join(cmd)}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        default_output = output_dir / f"{source_path.stem}.pdf"
        if not default_output.exists():
            raise RuntimeError(
                "LibreOffice completed without producing a PDF output file.\n"
                f"Expected output path: {default_output}"
            )

        if default_output != output_path:
            default_output.replace(output_path)

    def _convert_image_to_pdf(self, source_path: Path, output_path: Path) -> None:
        with Image.open(source_path) as image:
            rgb = image.convert("RGB")
            rgb.save(output_path, "PDF", resolution=100.0)

    def _convert_image_to_docx(self, source_path: Path, output_path: Path) -> None:
        source_path = Path(source_path)
        output_path = Path(output_path)

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        if source_path.stat().st_size == 0:
            raise ValueError(f"Source image is empty: {source_path}")

        try:
            with Image.open(source_path) as image:
                image.load()
                rgb = image.convert("RGB")
                with NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    normalized_path = Path(tmp.name)
                try:
                    rgb.save(normalized_path, format="PNG")
                    document = Document()
                    document.add_picture(str(normalized_path), width=Inches(6))
                    document.save(output_path)
                finally:
                    if normalized_path.exists():
                        normalized_path.unlink(missing_ok=True)
        except UnidentifiedImageError as e:
            with open(source_path, "rb") as f:
                header = f.read(32)
            raise ValueError(
                f"Uploaded file is not a readable image. path={source_path}, header={header!r}"
            ) from e

    def _convert_png_to_jpeg_family(self, source_path: Path, output_path: Path) -> None:
        with Image.open(source_path) as image:
            rgb = image.convert("RGB")
            rgb.save(output_path, "JPEG")


@dataclass(frozen=True)
class ConvertConfig:
    """Optional knobs for future provider-backed conversion."""

    algorithm_version: Optional[str] = None


class ConvertProcessor:
    """
    Stateless document-conversion processor.

    Responsibilities:
    - validate local conversion preconditions
    - enforce contract-allowed conversion pairs
    - plan deterministic output naming
    - delegate actual conversion to a backend
    - return schema-agnostic artifact metadata only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - analyzer response language-field orchestration
    - document text extraction
    """

    def __init__(
        self,
        backend: Optional[ConversionBackend] = None,
        config: Optional[ConvertConfig] = None,
    ) -> None:
        self.backend = backend or RealConversionBackend()
        self.config = config or ConvertConfig()

    def convert(
        self,
        *,
        input_format: str,
        output_format: str,
        source_reference: str,
        source_name_hint: Optional[str] = None,
    ) -> ConversionArtifact:
        normalized_input = _normalize_format(input_format)
        normalized_output = _normalize_format(output_format)
        normalized_reference = _normalize_source_reference(source_reference)

        ensure_allowed_conversion_pair(
            input_format=normalized_input,
            output_format=normalized_output,
        )

        planned_name = plan_output_file_name(
            input_format=normalized_input,
            output_format=normalized_output,
            source_name_hint=source_name_hint,
        )

        return self.backend.convert(
            source_reference=normalized_reference,
            input_format=normalized_input,
            output_format=normalized_output,
            planned_output_name=planned_name,
        )


def ensure_allowed_conversion_pair(*, input_format: str, output_format: str) -> None:
    normalized_input = _normalize_format(input_format)
    normalized_output = _normalize_format(output_format)

    allowed_targets = ALLOWED_CONVERSION_PAIRS.get(normalized_input, set())
    if normalized_output not in allowed_targets:
        allowed = ", ".join(sorted(allowed_targets)) if allowed_targets else "(none)"
        raise ValueError(
            f"Unsupported conversion pair: {normalized_input} -> {normalized_output}. "
            f"Allowed outputs for {normalized_input}: {allowed}."
        )


def plan_output_file_name(
    *,
    input_format: str,
    output_format: str,
    source_name_hint: Optional[str] = None,
) -> str:
    normalized_input = _normalize_format(input_format)
    normalized_output = _normalize_format(output_format)
    ensure_allowed_conversion_pair(
        input_format=normalized_input,
        output_format=normalized_output,
    )

    base_source = source_name_hint or normalized_input
    base = _safe_basename(Path(str(base_source)).stem)
    return f"{base}.converted.{normalized_output}"


def build_conversion_instructions(*, input_format: str, output_format: str) -> str:
    normalized_input = _normalize_format(input_format)
    normalized_output = _normalize_format(output_format)
    ensure_allowed_conversion_pair(
        input_format=normalized_input,
        output_format=normalized_output,
    )

    return (
        f"{CONVERSION_RULES}\n\n"
        f"SOURCE FORMAT:\n{normalized_input}\n\n"
        f"TARGET FORMAT:\n{normalized_output}"
    )


def convert_document(
    *,
    input_format: str,
    output_format: str,
    source_reference: str,
    source_name_hint: Optional[str] = None,
    backend: Optional[ConversionBackend] = None,
    config: Optional[ConvertConfig] = None,
) -> ConversionArtifact:
    """Functional convenience wrapper for analyzer integration."""
    processor = ConvertProcessor(backend=backend, config=config)
    return processor.convert(
        input_format=input_format,
        output_format=output_format,
        source_reference=source_reference,
        source_name_hint=source_name_hint,
    )


def _normalize_format(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("format value must be a string.")
    normalized = value.strip().lower()
    if normalized not in OUTPUT_EXTENSION_ALIASES:
        raise ValueError("format must be one of: pdf, docx, jpg, jpeg, png.")
    return OUTPUT_EXTENSION_ALIASES[normalized]


def _normalize_source_reference(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("source_reference must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("source_reference cannot be empty.")
    return normalized


def _normalize_file_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("planned_output_name must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("planned_output_name cannot be empty.")
    return normalized


def _safe_basename(value: str) -> str:
    raw = str(value).strip().lower()
    if not raw:
        return "document"
    filtered = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    compact = "-".join(part for part in filtered.split("-") if part)
    return compact or "document"


def _get_file_size_mb(path: Path) -> float:
    return round(path.stat().st_size / (1024 * 1024), 4)


def _guess_content_type(path: Path) -> Optional[str]:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed


__all__ = [
    "CONVERSION_RULES",
    "ALLOWED_CONVERSION_PAIRS",
    "ConversionArtifact",
    "ConversionBackend",
    "RealConversionBackend",
    "ConvertConfig",
    "ConvertProcessor",
    "ensure_allowed_conversion_pair",
    "plan_output_file_name",
    "build_conversion_instructions",
    "convert_document",
]
