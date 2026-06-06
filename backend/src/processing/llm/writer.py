from __future__ import annotations

"""
V1 document writer layer for AI document actions.

Purpose:
- convert processed AI-action text into real downloadable document artifacts
- preserve paragraph order and content structure reasonably
- keep analyzer.py responsible for orchestration, routing, and response building
- keep processing modules responsible for text transformation only

Design notes:
- schema-agnostic: this module creates document artifacts only
- does not call any LLM / ASR / conversion provider
- focuses on valid .pdf and .docx output generation
- .txt inline output is intentionally out of scope for this layer
"""

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Protocol

from docx import Document
from fpdf import FPDF

from backend.src.storage.artifacts import (
    LocalArtifactStorage,
    StorageBackend,
    guess_content_type
)


SUPPORTED_OUTPUT_FORMATS = {"pdf", "docx"}

PDF_UNICODE_FONT_PATH_ENV = "PDF_UNICODE_FONT_PATH"
PDF_UNICODE_FONT_NAME = "UnicodeDocumentFont"


@dataclass(frozen=True)
class WrittenArtifact:
    """
    Schema-agnostic written artifact descriptor.

    file_path is the persisted artifact location, not the temporary writer path.
    storage_key and download_url are exposed for future orchestration layers.
    """

    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class DocumentWriterBackend(Protocol):
    """Provider interface for writing processed text into document artifacts."""

    def write(
        self,
        *,
        content: str,
        output_format: str,
        planned_output_name: str,
    ) -> WrittenArtifact:
        ...


class RealDocumentWriterBackend:
    """
    Real document writer backend.

    Supported outputs:
    - pdf
    - docx

    Goals:
    - preserve paragraph order
    - preserve empty-line paragraph separation reasonably
    - produce valid downloadable document files
    """

    def __init__(self, storage_backend: Optional[StorageBackend] = None) -> None:
        self.storage_backend = storage_backend or LocalArtifactStorage(
            base_dir="artifacts/ai_documents"
        )

    def write(
        self,
        *,
        content: str,
        output_format: str,
        planned_output_name: str,
    ) -> WrittenArtifact:
        normalized_content = _normalize_content(content)
        normalized_output_format = _normalize_output_format(output_format)
        normalized_name = _normalize_file_name(planned_output_name)

        with TemporaryDirectory(prefix="writer-work-") as workdir:
            output_path = Path(workdir) / normalized_name

            if normalized_output_format == "docx":
                self._write_docx(normalized_content, output_path)
            elif normalized_output_format == "pdf":
                self._write_pdf(normalized_content, output_path)
            else:
                raise ValueError(
                    f"Unsupported writer output format: {normalized_output_format}"
                )

            if not output_path.exists():
                raise RuntimeError(
                    "Document writing completed without producing an output file."
                )

            stored = self.storage_backend.persist(
                source_file_path=str(output_path),
                artifact_name=output_path.name,
                content_type=guess_content_type(str(output_path))
            )

            stored_path = Path(stored.stored_path)
            return WrittenArtifact(
                file_name=output_path.name,
                file_extension=normalized_output_format,
                file_size_mb=_get_file_size_mb(stored_path),
                file_path=str(stored_path),
                storage_key=stored.storage_key,
                download_url=stored.download_url,
            )

    def _write_docx(self, content: str, output_path: Path) -> None:
        document = Document()

        for paragraph_text in _split_paragraphs(content):
            document.add_paragraph(paragraph_text)

        document.save(output_path)

    def _write_pdf(self, content: str, output_path: Path) -> None:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        is_arabic_document = _contains_arabic(content)
        unicode_font_path = _find_unicode_font_path(content)

        if unicode_font_path is not None:
            try:
               pdf.add_font(PDF_UNICODE_FONT_NAME, "", str(unicode_font_path))
               pdf.set_font(PDF_UNICODE_FONT_NAME, size=11)
            except Exception as exc:
               if _contains_non_latin1(content):
                   raise RuntimeError(
                       "PDF output contains Unicode characters, but the configured "
                       f"font could not be loaded: {unicode_font_path}. "
                       f"Set {PDF_UNICODE_FONT_PATH_ENV} to a valid Unicode .ttf/.otf "
                       "font that covers the target language, or use DOCX output."
                   ) from exc

               pdf.set_font("Helvetica", size=11)
        else:
            if _contains_non_latin1(content):
                raise RuntimeError(
                    "PDF output contains Unicode characters that FPDF's built-in "
                    "Helvetica font cannot encode. Set "
                    f"{PDF_UNICODE_FONT_PATH_ENV} to a local Unicode .ttf/.otf font "
                    "that covers the target language, or use DOCX output."
                )
            pdf.set_font("Helvetica", size=11)

        if is_arabic_document:
            try:
                pdf.set_text_shaping(
                    use_shaping_engine=True,
                    direction="rtl",
                    script="arab",
                    language="ara",
                )
            except AttributeError as exc:
                raise RuntimeError(
                    "Arabic PDF output requires fpdf2 with text shaping support. "
                    "Run: pip uninstall -y fpdf && pip install --upgrade fpdf2 uharfbuzz"
                ) from exc

        paragraphs = _split_paragraphs(content)
        line_height = 6
        for index, paragraph_text in enumerate(paragraphs):
            text = paragraph_text if paragraph_text.strip() else " "

            if is_arabic_document:
                pdf.multi_cell(0, line_height, text, align="R")
            else:
                pdf.multi_cell(0, line_height, text)

            if index < len(paragraphs) - 1:
                pdf.ln(2)

        pdf.output(str(output_path))

class DocumentWriter:
    """
    Stateless writer façade for analyzer integration.

    Responsibilities:
    - validate local writing preconditions
    - delegate actual document creation to a backend
    - return schema-agnostic artifact metadata only
    """

    def __init__(self, backend: Optional[DocumentWriterBackend] = None) -> None:
        self.backend = backend or RealDocumentWriterBackend()

    def write(
        self,
        *,
        content: str,
        output_format: str,
        output_name: str,
    ) -> WrittenArtifact:
        normalized_content = _normalize_content(content)
        normalized_output_format = _normalize_output_format(output_format)
        normalized_name = _normalize_file_name(output_name)

        return self.backend.write(
            content=normalized_content,
            output_format=normalized_output_format,
            planned_output_name=normalized_name,
        )


def write_document(
    *,
    content: str,
    output_format: str,
    output_name: str,
    backend: Optional[DocumentWriterBackend] = None,
) -> WrittenArtifact:
    """Functional convenience wrapper for analyzer integration."""
    writer = DocumentWriter(backend=backend)
    return writer.write(
        content=content,
        output_format=output_format,
        output_name=output_name,
    )


def _split_paragraphs(content: str) -> list[str]:
    """
    Preserve paragraph order reasonably.

    Strategy:
    - normalize line endings
    - split on blank-line boundaries
    """
    normalized = content.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    raw_parts = normalized.split("\\n\\n")

    paragraphs: list[str] = []
    for part in raw_parts:
        cleaned_lines = [line.rstrip() for line in part.split("\\n")]
        paragraph = "\\n".join(cleaned_lines).strip("\\n")
        paragraphs.append(paragraph)

    if not paragraphs:
        return [""]

    return paragraphs


def _normalize_content(content: str) -> str:
    if not isinstance(content, str):
        raise TypeError("content must be a string.")
    normalized = content.strip()
    if not normalized:
        raise ValueError("Empty content cannot be written.")
    return normalized


def _normalize_output_format(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("output_format must be a string.")
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError("output_format must be one of: pdf, docx.")
    return normalized


def _normalize_file_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("output_name must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("output_name cannot be empty.")
    return normalized


def _get_file_size_mb(path: Path) -> float:
    return round(path.stat().st_size / (1024 * 1024), 4)


def _contains_non_latin1(value: str) -> bool:
    try:
        value.encode("latin-1")
        return False
    except UnicodeEncodeError:
        return True

def _contains_arabic(value: str) -> bool:
    return any(
        "\u0600" <= char <= "\u06FF"
        or "\u0750" <= char <= "\u077F"
        or "\u08A0" <= char <= "\u08FF"
        for char in value
    )

def _find_unicode_font_path(content: str = "") -> Path | None:
    configured = os.getenv(PDF_UNICODE_FONT_PATH_ENV, "").strip()
    candidates: list[Path] = []

    if configured:
        candidates.append(Path(configured))

    if _contains_arabic(content):
        candidates.extend(
            [
                Path("assets/fonts/NotoNaskhArabic-Regular.ttf"),
                Path("assets/fonts/NotoNaskhArabic-Medium.ttf"),
                Path("assets/fonts/NotoSansArabic-Regular.ttf"),
                Path("fonts/NotoNaskhArabic-Regular.ttf"),
                Path("fonts/NotoNaskhArabic-Medium.ttf"),
                Path("fonts/NotoSansArabic-Regular.ttf"),
                Path("C:/Users/Akan/jupitAIx/assets/fonts/NotoNaskhArabic-Regular.ttf"),
                Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
                Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
            ]
        )
    else:
        candidates.extend(
            [
                Path("assets/fonts/NotoSans-Regular.ttf"),
                Path("assets/fonts/NotoSansJP-Regular.ttf"),
                Path("assets/fonts/NotoSansCJK-Regular.ttf"),
                Path("fonts/NotoSans-Regular.ttf"),
                Path("fonts/NotoSansJP-Regular.ttf"),
                Path("fonts/NotoSansCJK-Regular.ttf"),
                Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("C:/Windows/Fonts/arial.ttf"),
            ]
        )

    seen: set[str] = set()

    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)

        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue

        if resolved.is_file():
            return resolved

    return None


__all__ = [
    "SUPPORTED_OUTPUT_FORMATS",
    "WrittenArtifact",
    "DocumentWriterBackend",
    "RealDocumentWriterBackend",
    "DocumentWriter",
    "write_document",
]