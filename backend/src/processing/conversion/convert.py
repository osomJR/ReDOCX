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

from bisect import bisect_right
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from itertools import count
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence
import json
import logging
import math
import os
import re
import signal
import unicodedata
import shutil
import subprocess
import zipfile
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

import fitz  # PyMuPDF
from PIL import Image, ImageChops, ImageCms, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.oxml.shape import CT_Inline
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph

from backend.src.storage.artifacts import (
    StorageBackend,
    LocalArtifactStorage,
)

try:
    from fontTools.ttLib import TTFont
    from pdf2docx import Converter as PDFToDOCXConverter
    from pdf2docx.converter import ConversionException as PDFToDOCXConversionException
    from pdf2docx.image.ImageBlock import ImageBlock as PDFToDOCXImageBlock
except ImportError:  # pragma: no cover
    PDFToDOCXConverter = None

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.drawing.image import Image as OpenPyXLImage
except ImportError:  # pragma: no cover
    Workbook = None
    load_workbook = None
    OpenPyXLImage = None

try:
    from pptx import Presentation as PptxPresentation
except ImportError:  # pragma: no cover
    PptxPresentation = None

try:
    from weasyprint import HTML as WeasyHTML, default_url_fetcher as weasy_default_url_fetcher
except ImportError:  # pragma: no cover
    WeasyHTML = None
    weasy_default_url_fetcher = None


logger = logging.getLogger(__name__)
# pdf2docx shares its coordinate transform across converter instances.
_PDF_TO_DOCX_LAYOUT_LOCK = Lock()


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
    "pdf": {"docx", "jpg", "pptx", "xlsx", "pdfa"},
    "docx": {"pdf"},
    "xlsx": {"pdf"},
    "html": {"pdf"},
    "htm": {"pdf"},
    "pptx": {"pdf"},
    "jpg": {"pdf", "docx"},
    "jpeg": {"pdf", "docx"},
    "png": {"jpg", "jpeg"},
}

OUTPUT_EXTENSION_ALIASES: dict[str, str] = {
    "jpg": "jpg",
    "jpeg": "jpeg",
    "pdf": "pdf",
    "pdfa": "pdfa",
    "docx": "docx",
    "png": "png",
    "xlsx": "xlsx",
    "html": "html",
    "htm": "htm",
    "pptx": "pptx",
    "zip": "zip",
}

PHYSICAL_OUTPUT_FORMAT_BY_TARGET: dict[str, str] = {
    "pdf": "pdf",
    "pdfa": "pdf",
    "docx": "docx",
    "jpg": "jpg",
    "jpeg": "jpeg",
    "pptx": "pptx",
    "xlsx": "xlsx",
}

PDF_TO_DOCX_MODES = {"auto", "editable", "fidelity", "native"}
DEFAULT_PDF_TO_DOCX_MODE = os.getenv("REDOCX_PDF_TO_DOCX_MODE", "editable").strip().lower()
DEFAULT_PDF_TO_DOCX_OCR_DPI = int(os.getenv("REDOCX_PDF_TO_DOCX_OCR_DPI", "200"))
DEFAULT_PDF_TO_DOCX_OCR_LANGUAGE = os.getenv("REDOCX_PDF_TO_DOCX_OCR_LANGUAGE", "eng").strip() or "eng"
DEFAULT_PDF_TO_DOCX_FIDELITY_DPI = int(os.getenv("REDOCX_PDF_TO_DOCX_FIDELITY_DPI", "180"))
DEFAULT_PDF_TO_DOCX_VISUAL_MIN_SCORE = float(
    os.getenv("REDOCX_PDF_TO_DOCX_VISUAL_MIN_SCORE", "0.60")
)
DEFAULT_DOCX_TO_PDF_TIMEOUT_SECONDS = int(os.getenv("REDOCX_DOCX_TO_PDF_TIMEOUT_SECONDS", "90"))
DEFAULT_OFFICE_TO_PDF_TIMEOUT_SECONDS = int(os.getenv("REDOCX_OFFICE_TO_PDF_TIMEOUT_SECONDS", str(DEFAULT_DOCX_TO_PDF_TIMEOUT_SECONDS)))
DEFAULT_PDFA_TIMEOUT_SECONDS = int(os.getenv("REDOCX_PDFA_TIMEOUT_SECONDS", "120"))
DEFAULT_PDFA_TEXT_TOKEN_COVERAGE = float(
    os.getenv("REDOCX_PDFA_TEXT_TOKEN_COVERAGE", "0.80")
)
DEFAULT_PDFA_VISUAL_MIN_SCORE = float(
    os.getenv("REDOCX_PDFA_VISUAL_MIN_SCORE", "0.98")
)
DEFAULT_PDF_RASTER_DPI = int(os.getenv("REDOCX_PDF_RASTER_DPI", "180"))
DEFAULT_IMAGE_PDF_DPI = float(os.getenv("REDOCX_IMAGE_PDF_DPI", "150"))
DEFAULT_IMAGE_JPEG_QUALITY = int(os.getenv("REDOCX_IMAGE_JPEG_QUALITY", "95"))
MAX_PDF_TO_DOCX_PAGES = int(os.getenv("REDOCX_PDF_TO_DOCX_MAX_PAGES", "250"))
MIN_EDITABLE_DOCX_TEXT_CHARS = int(os.getenv("REDOCX_MIN_EDITABLE_DOCX_TEXT_CHARS", "20"))
MIN_PDF_TEXT_RETENTION_RATIO = float(os.getenv("REDOCX_MIN_PDF_TEXT_RETENTION_RATIO", "0.70"))

# Conversion reliability / fidelity gates. These reject structurally valid but
# materially incomplete outputs instead of silently returning untrustworthy files.
DEFAULT_TEXT_TOKEN_COVERAGE = float(os.getenv("REDOCX_CONVERSION_TEXT_TOKEN_COVERAGE", "0.95"))
DEFAULT_XLSX_PDF_TOKEN_COVERAGE = float(os.getenv("REDOCX_XLSX_PDF_TOKEN_COVERAGE", "0.985"))
DEFAULT_XLSX_PDF_CELL_COVERAGE = float(os.getenv("REDOCX_XLSX_PDF_CELL_COVERAGE", "0.995"))
DEFAULT_XLSX_PDF_CHARACTER_COVERAGE = float(os.getenv("REDOCX_XLSX_PDF_CHARACTER_COVERAGE", "0.9975"))
DEFAULT_XLSX_LAYOUT_MODE = os.getenv("REDOCX_XLSX_PDF_LAYOUT_MODE", "readable").strip().lower()
DEFAULT_XLSX_MIN_COLUMN_WIDTH = float(os.getenv("REDOCX_XLSX_MIN_COLUMN_WIDTH", "10"))
DEFAULT_XLSX_MAX_COLUMN_WIDTH = float(os.getenv("REDOCX_XLSX_MAX_COLUMN_WIDTH", "42"))
DEFAULT_XLSX_TOTAL_COLUMN_WIDTH = float(os.getenv("REDOCX_XLSX_TOTAL_COLUMN_WIDTH", "108"))
DEFAULT_XLSX_LANDSCAPE_THRESHOLD = float(os.getenv("REDOCX_XLSX_LANDSCAPE_THRESHOLD", "72"))
DEFAULT_XLSX_PAPER_SIZE = os.getenv("REDOCX_XLSX_PAPER_SIZE", "A4").strip().upper() or "A4"
DEFAULT_PDF_TO_XLSX_RASTER_DPI = int(os.getenv("REDOCX_PDF_TO_XLSX_RASTER_DPI", "144"))
DEFAULT_PDF_TO_XLSX_MAX_IMAGE_WIDTH = int(os.getenv("REDOCX_PDF_TO_XLSX_MAX_IMAGE_WIDTH", "1600"))
DEFAULT_PDFA_VALIDATOR_MODE = os.getenv("REDOCX_PDFA_VALIDATOR_MODE", "required").strip().lower()
DEFAULT_LIBREOFFICE_UNO_PYTHON = os.getenv(
    "REDOCX_LIBREOFFICE_UNO_PYTHON", "/usr/bin/python3"
).strip() or "/usr/bin/python3"
LIBREOFFICE_CALC_PDF_WORKER = Path(__file__).with_name("libreoffice_calc_pdf_worker.py")

CONTENT_TYPES_BY_FORMAT: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}

WORDPROCESSINGML_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"
REDOCX_FIDELITY_DOCX_MARKER = "redocx-pdf-fidelity-v1"
REDOCX_POSITIONED_DOCX_MARKER = "redocx-pdf-positioned-v1"
REDOCX_MIXED_DOCX_MARKER = "redocx-pdf-mixed-v1"
_PDF_DOCX_VISUAL_PAGE_NAME = "PDF page image"
_PDF_DOCX_SHAPE_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_PDF_DOCX_MIN_INK_SCORE = 0.90


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


@dataclass(frozen=True)
class PdfHyperlink:
    """External link annotation recovered from a source PDF."""

    page_number: int
    label: str
    target: str


class PdfToDocxFidelityError(RuntimeError):
    """A PDF-to-Word candidate failed content or layout validation."""


@dataclass(frozen=True)
class PdfTextLine:
    """One visual PDF line and its native text spans."""

    bbox: tuple[float, float, float, float]
    spans: tuple[dict[str, Any], ...]

    @property
    def text(self) -> str:
        return "".join(str(span.get("text", "")) for span in self.spans)


@dataclass(frozen=True)
class PdfTextGroup:
    """Consecutive visual lines that should become one editable Word paragraph."""

    bbox: tuple[float, float, float, float]
    lines: tuple[PdfTextLine, ...]

    @property
    def text(self) -> str:
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())


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
    - pdf -> docx      via editable reconstruction with OCR fallback and link preservation
    - docx/xlsx/pptx -> pdf via isolated LibreOffice headless conversion
    - html -> pdf      via network-isolated WeasyPrint rendering
    - pdf -> jpg       via high-resolution page rasterization (ZIP for multi-page PDFs)
    - pdf -> pptx      via audited editable text and lossless page graphics
    - pdf -> xlsx      via table/text extraction plus a full-page visual fidelity reference
    - pdf -> PDF/A-2b  via Ghostscript with ICC OutputIntent and conformance validation
    - jpg/jpeg -> pdf  via original JPEG embedding or lossless normalization
    - jpg/jpeg -> docx via python-docx image insertion
    - png -> jpg/jpeg  via Pillow image conversion

    PDF -> DOCX is fidelity-first. ReDOCX prefers the layout-aware pdf2docx engine
    when installed, but only accepts its output after structural, text, hyperlink,
    and round-trip visual-layout validation. If that engine is unavailable or fails
    the audit, the default production fallback creates a fixed-layout OOXML DOCX
    containing one lossless rendering per source page plus a hidden searchable text
    layer. This preserves page geometry and orientation instead of silently returning
    a scattered flowing document. Operators that require normal paragraph-level
    editability can select REDOCX_PDF_TO_DOCX_MODE=editable and fail closed.
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
        ensure_allowed_conversion_pair(
            input_format=normalized_input,
            output_format=normalized_output,
        )
        source_path = Path(_normalize_source_reference(source_reference)).resolve()

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")

        _validate_source_format(source_path, normalized_input)
        planned_name = _normalize_file_name(planned_output_name)

        with TemporaryDirectory(prefix="convert-work-") as workdir:
            output_path = (Path(workdir) / planned_name).resolve()
            physical_output_format = PHYSICAL_OUTPUT_FORMAT_BY_TARGET[normalized_output]

            if normalized_input == "pdf" and normalized_output == "docx":
                layout = self._convert_pdf_to_docx(source_path, output_path)
                if layout in {"mixed", "visual"}:
                    output_path = output_path.rename(output_path.with_suffix(f".{layout}.docx"))
            elif normalized_input in {"docx", "xlsx", "pptx"} and normalized_output == "pdf":
                self._convert_office_to_pdf(source_path, output_path, normalized_input)
            elif normalized_input in {"html", "htm"} and normalized_output == "pdf":
                self._convert_html_to_pdf(source_path, output_path)
            elif normalized_input == "pdf" and normalized_output == "jpg":
                physical_output_format = self._convert_pdf_to_jpg(source_path, output_path)
                if physical_output_format == "zip":
                    output_path = output_path.with_suffix(".zip")
            elif normalized_input == "pdf" and normalized_output == "pptx":
                self._convert_pdf_to_pptx(source_path, output_path)
            elif normalized_input == "pdf" and normalized_output == "xlsx":
                self._convert_pdf_to_xlsx(source_path, output_path)
            elif normalized_input == "pdf" and normalized_output == "pdfa":
                self._convert_pdf_to_pdfa(source_path, output_path)
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

            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("Conversion completed without producing a valid output file.")

            _validate_converted_output(
                output_path,
                physical_output_format,
                source_path=source_path,
                input_format=normalized_input,
            )
            _validate_conversion_fidelity(
                source_path=source_path,
                input_format=normalized_input,
                requested_output_format=normalized_output,
                output_path=output_path,
                physical_output_format=physical_output_format,
            )
            if normalized_output == "pdfa":
                _validate_pdfa_file(output_path)

            stored = self.storage_backend.persist(
                source_file_path=str(output_path),
                artifact_name=output_path.name,
                content_type=CONTENT_TYPES_BY_FORMAT[physical_output_format],
            )

            stored_path = Path(stored.stored_path)
            _validate_converted_output(
                stored_path,
                physical_output_format,
                source_path=source_path,
                input_format=normalized_input,
            )
            if normalized_output == "pdfa":
                _validate_pdfa_file(stored_path)

            return ConversionArtifact(
                file_name=output_path.name,
                file_extension=physical_output_format,
                file_size_mb=_get_file_size_mb(stored_path),
                file_path=stored.stored_path,
                storage_key=stored.storage_key,
                download_url=stored.download_url,
            )

    def _convert_pdf_to_docx(self, source_path: Path, output_path: Path) -> str:
        mode = _pdf_to_docx_mode()
        with fitz.open(source_path) as source:
            page_count = source.page_count
            if page_count > MAX_PDF_TO_DOCX_PAGES:
                raise ValueError(f"PDF-to-Word conversion is capped at {MAX_PDF_TO_DOCX_PAGES} pages.")
            # Scans and outlined text have no visible Word text to reconstruct.
            visual_pages = {
                page.number for page in source
                if mode == "fidelity" or (
                    not _pdf_docx_visible_lines(page) and any(
                        kind != "ignore-text" for kind, _ in page.get_bboxlog()
                    )
                )
            }

        source_links = _extract_pdf_hyperlinks(source_path)
        if mode == "native":
            self._convert_pdf_to_native_editable_docx(source_path, output_path)
            self._validate_pdf_docx_candidate(source_path, output_path, source_links, repair_links=True)
            self._assert_pdf_to_docx_visual_fidelity(source_path, output_path)
            return "editable"

        if mode in {"auto", "editable"} and PDFToDOCXConverter is not None and not visual_pages:
            try:
                self._convert_pdf_to_editable_docx(source_path, output_path, source_links)
                return "editable"
            except PdfToDocxFidelityError as exc:
                logger.info("Trying positioned Word text after flow layout rejection. Reason: %s", exc)

        allow_visual = mode in {"auto", "fidelity"}
        if visual_pages and not allow_visual:
            raise PdfToDocxFidelityError("One or more pages lack visible PDF text.")

        with TemporaryDirectory(prefix="positioned-docx-", dir=output_path.parent) as workdir:
            candidate = Path(workdir) / output_path.name
            # A retry uses page images for rejected pages while retaining editable
            # text on the pages that passed. The last candidate is entirely visual.
            for attempt in range(3):
                visual_pages = _write_positioned_pdf_docx(
                    source_path, candidate, visual_pages=visual_pages, allow_visual=allow_visual
                )
                try:
                    self._validate_pdf_docx_candidate(source_path, candidate, source_links)
                    with TemporaryDirectory(prefix="pdf-docx-layout-audit-") as audit_dir:
                        rendered = Path(audit_dir) / "rendered.pdf"
                        self._render_pdf_docx_for_audit(candidate, rendered)
                        rejected = _pdf_docx_layout_rejections(source_path, rendered, visual_pages)
                except PdfToDocxFidelityError:
                    if not allow_visual or len(visual_pages) == page_count or attempt == 2:
                        raise
                    visual_pages = set(range(page_count))
                    continue
                if not rejected:
                    candidate.replace(output_path)
                    if len(visual_pages) == page_count:
                        return "visual"
                    return "mixed" if visual_pages else "editable"
                if not allow_visual or len(visual_pages) == page_count or attempt == 2:
                    raise PdfToDocxFidelityError(next(iter(rejected.values())))
                for reason in rejected.values():
                    logger.info("Using a source page image after editable layout rejection. Reason: %s", reason)
                visual_pages = (
                    set(range(page_count)) if attempt or set(rejected) & visual_pages
                    else visual_pages | set(rejected)
                )
        raise PdfToDocxFidelityError("No Word candidate passed the layout audit.")

    @staticmethod
    def _validate_pdf_docx_candidate(
        source_path: Path,
        candidate: Path,
        source_links: Sequence[PdfHyperlink],
        *,
        repair_links: bool = False,
    ) -> None:
        try:
            _validate_docx_file(candidate)
            _validate_pdf_to_docx_editability(source_path, candidate)
            if repair_links:
                _apply_pdf_hyperlinks(candidate, source_links)
            _assert_pdf_hyperlinks_preserved(candidate, source_links)
            _validate_conversion_fidelity(
                source_path=source_path,
                input_format="pdf",
                requested_output_format="docx",
                output_path=candidate,
                physical_output_format="docx",
            )
        except (ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            raise PdfToDocxFidelityError(str(exc)) from exc

    def _convert_pdf_to_editable_docx(
        self,
        source_path: Path,
        output_path: Path,
        source_links: Sequence[PdfHyperlink],
    ) -> None:
        if PDFToDOCXConverter is None:
            raise RuntimeError("Editable PDF-to-Word conversion requires the pdf2docx runtime.")

        with TemporaryDirectory(prefix="editable-docx-", dir=output_path.parent) as workdir:
            candidate = Path(workdir) / output_path.name
            try:
                graphics = _write_editable_pdf_docx(source_path, candidate)
            except (PDFToDOCXConversionException, ValueError, IndexError, KeyError, ArithmeticError) as exc:
                raise PdfToDocxFidelityError("The PDF layout could not be reconstructed.") from exc

            self._validate_pdf_docx_candidate(source_path, candidate, source_links, repair_links=True)

            self._assert_pdf_to_docx_visual_fidelity(
                source_path, candidate, editable_graphics=graphics, source_links=source_links
            )
            candidate.replace(output_path)

    def _render_pdf_docx_for_audit(self, docx_path: Path, rendered_path: Path) -> None:
        try:
            self._convert_office_to_pdf(docx_path, rendered_path, "docx")
            _validate_pdf_file(rendered_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError("The PDF-to-Word audit renderer could not produce a readable PDF.") from exc

    def _assert_pdf_to_docx_visual_fidelity(
        self,
        source_path: Path,
        docx_path: Path,
        *,
        editable_graphics: Optional[Sequence[Sequence[fitz.Rect]]] = None,
        source_links: Sequence[PdfHyperlink] = (),
    ) -> None:
        """Audit a flowing Word candidate against the fixed PDF page positions."""
        with TemporaryDirectory(prefix="pdf-docx-visual-audit-") as audit_dir:
            roundtrip_pdf = Path(audit_dir) / "roundtrip.pdf"
            self._render_pdf_docx_for_audit(docx_path, roundtrip_pdf)
            rejected = _pdf_docx_layout_rejections(source_path, roundtrip_pdf, set())
            if rejected:
                raise PdfToDocxFidelityError(next(iter(rejected.values())))
            if editable_graphics is not None:
                _assert_editable_pdf_docx_render(source_path, roundtrip_pdf, editable_graphics, source_links)

    def _convert_pdf_to_native_editable_docx(self, source_path: Path, output_path: Path) -> None:
        """Reconstruct an editable DOCX without using a full-page image fallback."""

        document = Document()

        with fitz.open(source_path) as pdf:
            if pdf.is_encrypted or pdf.needs_pass:
                raise ValueError("Password-protected PDFs cannot be converted without an unlock workflow.")
            if pdf.page_count < 1:
                raise ValueError("PDF has no pages to convert.")
            if pdf.page_count > MAX_PDF_TO_DOCX_PAGES:
                raise ValueError(
                    f"PDF has {pdf.page_count} pages; editable DOCX conversion is capped at "
                    f"{MAX_PDF_TO_DOCX_PAGES} pages."
                )

            for page_index, page in enumerate(pdf, start=1):
                native_page_dict = page.get_text("dict", sort=True)
                page_dict = native_page_dict

                if _pdf_dict_text_char_count(page_dict) < 1:
                    try:
                        text_page = page.get_textpage_ocr(
                            language=DEFAULT_PDF_TO_DOCX_OCR_LANGUAGE,
                            dpi=max(96, DEFAULT_PDF_TO_DOCX_OCR_DPI),
                            full=True,
                        )
                        page_dict = page.get_text("dict", textpage=text_page, sort=True)
                    except Exception as exc:
                        raise RuntimeError(
                            f"PDF page {page_index} contains no native text and OCR is unavailable. "
                            "ReDOCX refused to return an image-only Word document. Install/configure "
                            "Tesseract OCR or provide a text-based PDF."
                        ) from exc

                if _pdf_dict_text_char_count(page_dict) < 1:
                    raise RuntimeError(
                        f"PDF page {page_index} contains no extractable or OCR-readable text. "
                        "ReDOCX refused to return an image-only Word document."
                    )

                if page_index == 1:
                    section = document.sections[0]
                else:
                    section = document.add_section(WD_SECTION.NEW_PAGE)

                page_elements, content_bbox = _build_pdf_page_elements(
                    page,
                    page_dict=page_dict,
                    native_page_dict=native_page_dict,
                )
                _configure_section_for_editable_pdf_page(section, page.rect, content_bbox)

                previous_bottom: Optional[float] = None
                for element_type, bbox, payload in page_elements:
                    if element_type == "text":
                        paragraph = document.add_paragraph()
                        _populate_pdf_text_paragraph(
                            paragraph,
                            payload,
                            page_rect=page.rect,
                            section=section,
                            previous_bottom=previous_bottom,
                        )
                    elif element_type == "table":
                        _add_pdf_table_to_docx(document, payload, previous_bottom=previous_bottom)
                    elif element_type == "image":
                        _add_pdf_image_to_docx(
                            document,
                            payload,
                            bbox=bbox,
                            section=section,
                            previous_bottom=previous_bottom,
                        )
                    previous_bottom = max(previous_bottom or bbox[3], bbox[3])

        document.save(output_path)

    def _convert_docx_to_pdf(self, source_path: Path, output_path: Path) -> None:
        self._convert_office_to_pdf(source_path, output_path, "docx")

    def _convert_office_to_pdf(
        self,
        source_path: Path,
        output_path: Path,
        input_format: str,
    ) -> None:
        """Convert Office documents through an isolated LibreOffice process.

        XLSX conversion is environment-hardened. ReDOCX first uses the balanced
        OOXML print-layout normalizer and verifies the resulting PDF with a
        cell-aware retention audit that is tolerant of PDF extractor line wraps.
        If the first export is materially incomplete, ReDOCX automatically retries
        from the untouched source with a conservative ``safe`` layout that wraps
        every populated visible cell and releases row heights for auto-sizing.

        This retry is intentionally scoped to XLSX: DOCX and PPTX already carry
        their own page/flow semantics and are validated by the generic fidelity
        gates after export.
        """
        soffice_bin = (
            os.getenv("SOFFICE_PATH")
            or shutil.which("soffice")
            or shutil.which("libreoffice")
            or shutil.which("soffice.exe")
        )
        if not soffice_bin:
            raise RuntimeError(
                "LibreOffice not found. Set SOFFICE_PATH or add soffice/libreoffice to PATH."
            )

        source_path = source_path.resolve()
        output_path = output_path.resolve()
        output_dir = output_path.parent.resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Source Office file not found: {source_path}")

        export_filters = {
            "docx": "writer_pdf_Export",
            "xlsx": "calc_pdf_Export",
            "pptx": "impress_pdf_Export",
        }
        export_filter = export_filters.get(input_format)
        if not export_filter:
            raise ValueError(f"Unsupported Office-to-PDF input format: {input_format}.")

        filter_options: dict[str, dict[str, str]] = {
            "UseLosslessCompression": {"type": "boolean", "value": "true"},
            "ReduceImageResolution": {"type": "boolean", "value": "false"},
            "UseTaggedPDF": {"type": "boolean", "value": "true"},
            "ExportBookmarks": {"type": "boolean", "value": "true"},
        }
        if input_format == "pptx":
            filter_options["ExportHiddenSlides"] = {"type": "boolean", "value": "false"}
        filter_spec = f"pdf:{export_filter}:{json.dumps(filter_options, separators=(',', ':'))}"

        layout_profiles: tuple[str, ...]
        if input_format != "xlsx" or DEFAULT_XLSX_LAYOUT_MODE == "preserve":
            layout_profiles = ("preserve",)
        else:
            # A second export is performed only when the first artifact does not
            # satisfy the XLSX-specific retention audit. This keeps normal jobs fast
            # while making production output resilient to font substitution and
            # LibreOffice build differences.
            layout_profiles = ("balanced", "safe")

        last_fidelity_error: Optional[RuntimeError] = None
        for attempt_index, layout_profile in enumerate(layout_profiles, start=1):
            output_path.unlink(missing_ok=True)
            default_output = output_dir / f"{source_path.stem}.pdf"
            if default_output != output_path:
                default_output.unlink(missing_ok=True)

            with TemporaryDirectory(prefix="office-pdf-") as office_dir:
                office_root = Path(office_dir).resolve()
                staged_source = office_root / source_path.name
                shutil.copy2(source_path, staged_source)

                if input_format == "xlsx" and layout_profile != "preserve":
                    _prepare_xlsx_for_pdf(staged_source, profile=layout_profile)

                use_calc_uno = input_format == "xlsx" and layout_profile != "preserve"
                if use_calc_uno:
                    # XLSX is rendered through UNO so Calc itself recalculates live
                    # row heights with the fonts installed in this exact container.
                    # This removes the environment-dependent clipping that cannot be
                    # solved reliably with OOXML wrap/height hints alone.
                    self._convert_xlsx_to_pdf_with_uno(
                        staged_source,
                        output_path,
                        soffice_bin=soffice_bin,
                        layout_profile=layout_profile,
                    )
                else:
                    profile_dir = office_root / "profile"
                    profile_dir.mkdir(parents=True, exist_ok=True)
                    profile_uri = profile_dir.as_uri()
                    cmd = [
                        soffice_bin,
                        "--headless",
                        "--nologo",
                        "--nodefault",
                        "--nolockcheck",
                        "--nofirststartwizard",
                        f"-env:UserInstallation={profile_uri}",
                        "--convert-to",
                        filter_spec,
                        "--outdir",
                        str(output_dir),
                        str(staged_source),
                    ]
                    env = {
                        **os.environ,
                        "HOME": str(profile_dir),
                        "TMPDIR": str(office_root),
                        "SAL_USE_VCLPLUGIN": os.getenv("SAL_USE_VCLPLUGIN", "svp"),
                        "LANG": os.getenv("LANG", "C.UTF-8"),
                        "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
                    }
                    try:
                        result = subprocess.run(
                            cmd,
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=DEFAULT_OFFICE_TO_PDF_TIMEOUT_SECONDS,
                            env=env,
                        )
                    except subprocess.TimeoutExpired as exc:
                        raise RuntimeError(
                            f"LibreOffice timed out while converting {input_format} to pdf."
                        ) from exc

            if not use_calc_uno:
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()[-2000:]
                    suffix = f" Details: {detail}" if detail else ""
                    raise RuntimeError(
                        f"LibreOffice failed while converting {input_format} to pdf. "
                        f"Return code: {result.returncode}.{suffix}"
                    )

                default_output = output_dir / f"{source_path.stem}.pdf"
                if not default_output.exists() or default_output.stat().st_size <= 0:
                    raise RuntimeError(
                        f"LibreOffice completed without producing a PDF for the {input_format} source."
                    )
                if default_output != output_path:
                    default_output.replace(output_path)
            elif not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError(
                    "LibreOffice Calc UNO completed without producing a PDF for the XLSX source."
                )

            if input_format != "xlsx":
                return
            if layout_profile == "preserve":
                # Explicit preserve mode retains the caller's spreadsheet print
                # semantics. It still passes the same fail-closed fidelity audit.
                _assert_xlsx_pdf_fidelity(source_path, output_path)
                return

            try:
                _assert_xlsx_pdf_fidelity(source_path, output_path)
                if attempt_index > 1:
                    logger.info(
                        "XLSX-to-PDF conversion passed fidelity validation after safe-layout retry."
                    )
                return
            except RuntimeError as exc:
                last_fidelity_error = exc
                if attempt_index < len(layout_profiles):
                    logger.warning(
                        "XLSX-to-PDF balanced layout did not satisfy fidelity validation; "
                        "retrying with safe wrapping and automatic row sizing. Audit: %s",
                        exc,
                    )
                    continue
                raise

        if last_fidelity_error is not None:  # pragma: no cover - defensive guard.
            raise last_fidelity_error

    def _convert_xlsx_to_pdf_with_uno(
        self,
        source_path: Path,
        output_path: Path,
        *,
        soffice_bin: str,
        layout_profile: str,
    ) -> None:
        """Render XLSX through Calc's live UNO layout engine.

        XML-only row-height hints are not authoritative in headless Calc. Different
        LibreOffice/font environments may retain default row heights even when
        wrapText is present, which clips long cell values. This worker loads the
        temporary workbook in the exact Calc process that exports the PDF, applies
        live row OptimalHeight/page-scaling properties, and exports without closing
        and reopening the document.

        The UNO bridge is intentionally executed with Debian's system Python because
        python3-uno is ABI-coupled to the LibreOffice packages. The application
        virtualenv must not attempt to pip-install or import pyuno directly.
        """
        uno_python = Path(DEFAULT_LIBREOFFICE_UNO_PYTHON).expanduser()
        worker = LIBREOFFICE_CALC_PDF_WORKER.resolve()
        if not uno_python.is_file():
            raise RuntimeError(
                "LibreOffice UNO Python runtime is unavailable. Install python3-uno "
                "and set REDOCX_LIBREOFFICE_UNO_PYTHON to the system Python executable."
            )
        if not worker.is_file():
            raise RuntimeError(
                "ReDOCX LibreOffice Calc PDF worker is missing from the deployment image."
            )

        command = [
            str(uno_python),
            str(worker),
            "--source",
            str(source_path),
            "--output",
            str(output_path),
            "--soffice",
            str(soffice_bin),
            "--profile",
            layout_profile if layout_profile in {"balanced", "safe"} else "balanced",
            "--timeout",
            str(DEFAULT_OFFICE_TO_PDF_TIMEOUT_SECONDS),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=(os.name != "nt"),
            env={
                **os.environ,
                "LANG": os.getenv("LANG", "C.UTF-8"),
                "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
                "SAL_USE_VCLPLUGIN": os.getenv("SAL_USE_VCLPLUGIN", "svp"),
            },
        )
        try:
            stdout, stderr = process.communicate(
                timeout=DEFAULT_OFFICE_TO_PDF_TIMEOUT_SECONDS + 30
            )
        except subprocess.TimeoutExpired as exc:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            else:  # pragma: no cover - production image is Linux.
                process.kill()
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                "LibreOffice Calc UNO layout/export timed out while converting XLSX to PDF."
            ) from exc

        if process.returncode != 0:
            output_path.unlink(missing_ok=True)
            detail = (stderr or stdout or "").strip()[-2000:]
            # Temporary customer paths are not useful diagnostics outside this
            # process. Redact them before the message can reach API/log surfaces.
            for sensitive in (str(source_path), str(output_path), str(worker)):
                if sensitive:
                    detail = detail.replace(sensitive, "<runtime-path>")
            raise RuntimeError(
                "LibreOffice Calc UNO layout/export failed for XLSX-to-PDF."
                + (f" Details: {detail}" if detail else "")
            )
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError(
                "LibreOffice Calc UNO completed without producing a non-empty PDF."
            )

    def _convert_html_to_pdf(self, source_path: Path, output_path: Path) -> None:
        if WeasyHTML is None or weasy_default_url_fetcher is None:
            raise RuntimeError(
                "WeasyPrint is required for HTML to PDF conversion. Install the weasyprint runtime dependency."
            )

        # WeasyPrint logs resource failures and continues rendering. Retain those
        # failures explicitly so a PDF missing its stylesheet, logo or font cannot
        # be returned as a successful conversion. Keep URLs out of diagnostics:
        # data URLs and remote query strings can contain customer content.
        failed_resources: list[str] = []

        def safe_fetcher(url: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            parsed = urlsplit(str(url or ""))
            if parsed.scheme.casefold() != "data":
                failed_resources.append("blocked")
                raise ValueError(
                    "External and local resource loading is disabled during HTML conversion. "
                    "Embed images/fonts with data: URLs."
                )
            try:
                return weasy_default_url_fetcher(url, *args, **kwargs)
            except Exception:
                failed_resources.append("invalid")
                raise

        try:
            html_text = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("HTML input must be valid UTF-8.") from exc
        if not html_text.strip():
            raise ValueError("HTML input is empty.")

        # Some losses never reach the fetcher: unresolved relative references and
        # corrupt image bytes are logged by WeasyPrint while rendering continues.
        # Capture only errors from this synchronous render's thread. Concurrent
        # batch jobs must not inherit another document's rendering failure.
        from threading import get_ident

        render_thread = get_ident()
        rendering_errors: list[bool] = []

        class RenderErrorHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.thread == render_thread and record.levelno >= logging.ERROR:
                    # Retain no URL, embedded data or customer document text.
                    rendering_errors.append(True)

        render_logger = logging.getLogger("weasyprint")
        error_handler = RenderErrorHandler(level=logging.ERROR)
        render_logger.addHandler(error_handler)
        # WeasyPrint does not execute JavaScript. The custom fetcher additionally
        # prevents network access and local-file reads from CSS/images/fonts.
        try:
            WeasyHTML(
                string=html_text,
                base_url=None,
                url_fetcher=safe_fetcher,
            ).write_pdf(str(output_path))
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            raise RuntimeError("HTML to PDF rendering failed.") from exc
        finally:
            render_logger.removeHandler(error_handler)
            error_handler.close()

        if failed_resources:
            output_path.unlink(missing_ok=True)
            if "blocked" in failed_resources:
                raise RuntimeError(
                    "HTML conversion requires a self-contained document. "
                    "Embed referenced stylesheets, images and fonts with data: URLs; "
                    "external and local resources cannot be loaded."
                )
            raise RuntimeError(
                "HTML conversion could not load an embedded resource. "
                "Correct the document's data: URLs before converting it."
            )
        if rendering_errors:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                "HTML conversion could not render all document resources. "
                "Correct missing or invalid images, stylesheets and fonts, and "
                "embed referenced resources with data: URLs before converting it."
            )

    def _convert_pdf_to_jpg(self, source_path: Path, output_path: Path) -> str:
        scale = max(1.0, float(DEFAULT_PDF_RASTER_DPI) / 72.0)
        matrix = fitz.Matrix(scale, scale)
        with fitz.open(source_path) as pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF has no pages to convert.")
            if pdf.page_count == 1:
                pix = pdf.load_page(0).get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                image.save(
                    output_path,
                    "JPEG",
                    quality=DEFAULT_IMAGE_JPEG_QUALITY,
                    subsampling=0,
                    optimize=True,
                    dpi=(DEFAULT_PDF_RASTER_DPI, DEFAULT_PDF_RASTER_DPI),
                )
                return "jpg"

            zip_path = output_path.with_suffix(".zip")
            with TemporaryDirectory(prefix="pdf-jpg-pages-") as pages_dir:
                page_paths: list[Path] = []
                digits = max(4, len(str(pdf.page_count)))
                for page_index in range(pdf.page_count):
                    pix = pdf.load_page(page_index).get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False)
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    page_path = Path(pages_dir) / f"page-{page_index + 1:0{digits}d}.jpg"
                    image.save(
                        page_path,
                        "JPEG",
                        quality=DEFAULT_IMAGE_JPEG_QUALITY,
                        subsampling=0,
                        optimize=True,
                        dpi=(DEFAULT_PDF_RASTER_DPI, DEFAULT_PDF_RASTER_DPI),
                    )
                    page_paths.append(page_path)

                with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for page_path in page_paths:
                        archive.write(page_path, arcname=page_path.name)
            return "zip"

    def _convert_pdf_to_pptx(self, source_path: Path, output_path: Path) -> None:
        if PptxPresentation is None:
            raise RuntimeError("python-pptx is required for PDF to PowerPoint conversion.")

        # Native text remains visible and editable, while graphics retain a
        # lossless visual layer. Accept this reconstruction only after an actual
        # Office round-trip audit; unsupported writing directions or unavailable
        # fonts must never replace the original page with degraded content.
        try:
            native_pages = self._write_pdf_to_pptx(source_path, output_path, editable=True)
            if native_pages:
                self._audit_editable_pdf_to_pptx(source_path, output_path, native_pages)
            return
        except Exception as exc:
            output_path.unlink(missing_ok=True)
            logger.warning(
                "Editable PDF-to-PowerPoint reconstruction did not satisfy fidelity "
                "requirements; retaining rendered page content instead. Reason: %s", exc,
            )
        self._write_pdf_to_pptx(source_path, output_path, editable=False)

    def _write_pdf_to_pptx(
        self, source_path: Path, output_path: Path, *, editable: bool
    ) -> list[int]:
        from pptx.enum.text import MSO_AUTO_SIZE
        from pptx.dml.color import RGBColor as PptxRGBColor
        from pptx.util import Pt as PptxPt

        native_pages: list[int] = []
        with fitz.open(source_path) as pdf, fitz.open(
            stream=source_path.read_bytes(), filetype="pdf"
        ) as graphics_pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF has no pages to convert.")
            first_rect = pdf[0].rect
            prs = PptxPresentation()
            # PowerPoint supports a single canvas per deck, between 1 and 56
            # inches on each axis. Letterbox each page within that valid canvas.
            canvas_width = max(72.0, min(4032.0, float(first_rect.width)))
            canvas_height = max(72.0, min(4032.0, float(first_rect.height)))
            prs.slide_width = int(canvas_width * 12700)
            prs.slide_height = int(canvas_height * 12700)
            blank_layout = prs.slide_layouts[6]
            scale = max(1.0, float(DEFAULT_PDF_RASTER_DPI) / 72.0)
            with TemporaryDirectory(prefix="pdf-pptx-pages-") as image_dir:
                for page_index, page in enumerate(pdf):
                    slide = prs.slides.add_slide(blank_layout)
                    fit_scale = min(canvas_width / page.rect.width, canvas_height / page.rect.height)
                    left = (canvas_width - page.rect.width * fit_scale) / 2.0
                    top = (canvas_height - page.rect.height * fit_scale) / 2.0
                    spans = _pdf_pptx_editable_spans(page) if editable else None
                    background = graphics_pdf[page_index]
                    if spans:
                        for span in spans:
                            background.add_redact_annot(fitz.Rect(span["bbox"]), fill=False)
                        # Remove only the native text; never erase overlapping
                        # vector lines, colored cell fills, or source images.
                        background.apply_redactions(images=0, graphics=0, text=0)
                        native_pages.append(page_index)
                    else:
                        background = page
                    pix = background.get_pixmap(
                        matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False
                    )
                    image_path = Path(image_dir) / f"page-{page_index + 1:04d}.png"
                    pix.save(str(image_path))
                    slide.shapes.add_picture(
                        str(image_path), int(left * 12700), int(top * 12700),
                        width=int(page.rect.width * fit_scale * 12700),
                        height=int(page.rect.height * fit_scale * 12700),
                    )
                    for span in spans or []:
                        bbox = fitz.Rect(span["bbox"])
                        font_size = float(span["size"])
                        box = slide.shapes.add_textbox(
                            int((left + bbox.x0 * fit_scale) * 12700),
                            int((top + (float(span["origin"][1]) - font_size) * fit_scale) * 12700),
                            int(max(1.0, (bbox.width + font_size * 2.0) * fit_scale) * 12700),
                            int(max(1.0, font_size * 2.0 * fit_scale) * 12700),
                        )
                        frame = box.text_frame
                        frame.clear()
                        frame.word_wrap = False
                        frame.auto_size = MSO_AUTO_SIZE.NONE
                        frame.margin_left = frame.margin_top = frame.margin_right = frame.margin_bottom = 0
                        paragraph = frame.paragraphs[0]
                        paragraph.space_before = paragraph.space_after = PptxPt(0)
                        run = paragraph.add_run()
                        run.text = str(span["text"])
                        run.font.name = _pdf_pptx_font_family(str(span.get("font", "")))
                        run.font.size = PptxPt(font_size * fit_scale)
                        flags = int(span.get("flags", 0))
                        run.font.bold = bool(flags & 16)
                        run.font.italic = bool(flags & 2)
                        color = int(span.get("color", 0))
                        run.font.color.rgb = PptxRGBColor(
                            (color >> 16) & 255, (color >> 8) & 255, color & 255
                        )
                    _add_pdf_pptx_hyperlinks(slide, page, left, top, fit_scale)
            prs.save(output_path)
        return native_pages

    def _audit_editable_pdf_to_pptx(
        self, source_path: Path, output_path: Path, native_pages: Sequence[int]
    ) -> None:
        with TemporaryDirectory(prefix="pdf-pptx-audit-") as audit_dir:
            root = Path(audit_dir)
            rendered = root / "roundtrip.pdf"
            expected = root / "expected.pdf"
            self._convert_office_to_pdf(output_path, rendered, "pptx")
            prs = PptxPresentation(str(output_path))
            width = float(prs.slide_width) / 12700.0
            height = float(prs.slide_height) / 12700.0
            with fitz.open(source_path) as source, fitz.open() as canvas, fitz.open(rendered) as actual:
                for page_index, page in enumerate(source):
                    target = canvas.new_page(width=width, height=height)
                    # Use the source's rendered orientation, including page
                    # rotation/crop metadata, when building the audit canvas.
                    pixmap = page.get_pixmap(
                        matrix=fitz.Matrix(max(1.0, float(DEFAULT_PDF_RASTER_DPI) / 72.0),
                                           max(1.0, float(DEFAULT_PDF_RASTER_DPI) / 72.0)),
                        colorspace=fitz.csRGB, alpha=False,
                    )
                    target.insert_image(target.rect, pixmap=pixmap, keep_proportion=True)
                canvas.save(expected)
                _assert_text_token_coverage(
                    source_text="\n".join(source[i].get_text("text") for i in native_pages),
                    output_text="\n".join(actual[i].get_text("text") for i in native_pages),
                    minimum=DEFAULT_TEXT_TOKEN_COVERAGE,
                    label="Editable PDF-to-PowerPoint conversion",
                )
            _assert_pdf_visual_layout_fidelity(
                expected, rendered, minimum_score=0.85,
                label="Editable PDF-to-PowerPoint conversion",
            )

    def _convert_pdf_to_xlsx(self, source_path: Path, output_path: Path) -> None:
        if Workbook is None or OpenPyXLImage is None:
            raise RuntimeError("openpyxl is required for PDF to Excel conversion.")

        workbook = Workbook()
        workbook.remove(workbook.active)

        def excel_column_name(index: int) -> str:
            value = max(1, int(index))
            letters = ""
            while value:
                value, remainder = divmod(value - 1, 26)
                letters = chr(65 + remainder) + letters
            return letters

        scale = max(1.0, float(DEFAULT_PDF_TO_XLSX_RASTER_DPI) / 72.0)
        with TemporaryDirectory(prefix="pdf-xlsx-images-") as image_dir, fitz.open(source_path) as pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF has no pages to convert.")

            for page_index in range(pdf.page_count):
                page = pdf.load_page(page_index)
                sheet = workbook.create_sheet(title=f"Page {page_index + 1}"[:31])
                next_row = 1
                # Keep narrative and tables in visual reading order. Previously,
                # detecting any table discarded every heading, footnote, and other
                # paragraph on that page from the workbook's editable cell layer.
                elements: list[tuple[tuple[float, float, float, float], list[list[Any]]]] = []
                table_rectangles: list[tuple[float, float, float, float]] = []
                for table in _safe_find_pdf_tables(page):
                    try:
                        rows = table.extract() or []
                    except Exception:
                        continue
                    if not any(str(value).strip() for row in rows for value in (row or []) if value is not None):
                        continue
                    bbox = _rect_tuple(table.bbox)
                    elements.append((bbox, rows))
                    table_rectangles.append(bbox)

                page_dict = page.get_text(
                    "dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True
                )
                for block in page_dict.get("blocks", []):
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        bbox = _rect_tuple(line.get("bbox") or block.get("bbox") or (0, 0, 0, 0))
                        if _mostly_inside_any(bbox, table_rectangles):
                            continue
                        text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
                        if text.strip():
                            elements.append((bbox, [[text]]))

                for _, rows in sorted(elements, key=lambda item: (item[0][1], item[0][0])):
                    for row in rows:
                        for col_index, value in enumerate(row or [], start=1):
                            if value is not None:
                                _write_pdf_text_cell(sheet, next_row, col_index, str(value))
                        next_row += 1
                    next_row += 1

                # Preserve the complete page alongside the editable cells. A
                # lossless PNG avoids introducing JPEG artifacts into fine table
                # rules, small labels, stamps, and signatures.
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                pil_image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                image_path = Path(image_dir) / f"page-{page_index + 1:04d}.png"
                maximum_width = max(1, DEFAULT_PDF_TO_XLSX_MAX_IMAGE_WIDTH)
                if pil_image.width > maximum_width:
                    ratio = maximum_width / float(pil_image.width)
                    pil_image = pil_image.resize(
                        (maximum_width, max(1, int(pil_image.height * ratio))),
                        Image.Resampling.LANCZOS,
                    )
                pil_image.save(image_path, "PNG", optimize=True)
                xl_image = OpenPyXLImage(str(image_path))
                anchor_column = sheet.max_column + 2 if elements else 1
                if anchor_column > 16384:
                    # Excel cannot anchor an image beyond column XFD. In this
                    # uncommon case put the visual reference below the cells.
                    anchor = f"A{min(1048576, next_row + 1)}"
                else:
                    anchor = f"{excel_column_name(anchor_column)}1"
                sheet.add_image(xl_image, anchor)

            workbook.save(output_path)

    def _convert_pdf_to_pdfa(self, source_path: Path, output_path: Path) -> None:
        gs_bin = (
            os.getenv("GHOSTSCRIPT_PATH")
            or shutil.which("gs")
            or shutil.which("gswin64c")
            or shutil.which("gswin32c")
        )
        if not gs_bin:
            raise RuntimeError(
                "Ghostscript is required for PDF/A conversion. Set GHOSTSCRIPT_PATH or add Ghostscript to PATH."
            )

        icc_profile = _resolve_pdfa_icc_profile()
        pdfa_def = _resolve_pdfa_definition_file(gs_bin)
        with TemporaryDirectory(prefix="pdfa-config-") as config_dir:
            local_def = Path(config_dir) / "PDFA_def.ps"
            definition = pdfa_def.read_text(encoding="latin-1")
            escaped_icc = str(icc_profile).replace("\\", "/").replace("(", "\\(").replace(")", "\\)")
            definition = re.sub(
                r"/ICCProfile\s*\([^\r\n]*\)",
                f"/ICCProfile ({escaped_icc})",
                definition,
                count=1,
            )
            local_def.write_text(definition, encoding="latin-1")
            render_source = _prepare_pdfa_uri_link_flags(
                source_path, Path(config_dir) / "source-with-archival-link-flags.pdf"
            )

            cmd = [
                gs_bin,
                "-dPDFA=2",
                "-dBATCH",
                "-dNOPAUSE",
                "-dSAFER",
                f"--permit-file-read={icc_profile}",
                f"--permit-file-read={local_def}",
                f"--permit-file-read={render_source}",
                "-sDEVICE=pdfwrite",
                "-dPreserveAnnots=true",
                "-sColorConversionStrategy=RGB",
                "-dAutoRotatePages=/None",
                "-dEmbedAllFonts=true",
                "-dSubsetFonts=true",
                "-dDownsampleColorImages=false",
                "-dDownsampleGrayImages=false",
                "-dDownsampleMonoImages=false",
                "-dPDFACompatibilityPolicy=1",
                f"-sOutputFile={output_path}",
                str(local_def),
                str(render_source),
            ]
            try:
                result = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=DEFAULT_PDFA_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("PDF/A conversion timed out.") from exc

        if result.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Ghostscript failed while creating PDF/A-2b. Return code: {result.returncode}."
            )
        # Archival conformance alone does not prove that navigation survived.
        with fitz.open(source_path) as source, fitz.open(output_path) as converted:
            for page_number, page in enumerate(source):
                expected = Counter(
                    str(link["uri"]) for link in page.get_links()
                    if link.get("kind") == fitz.LINK_URI and link.get("uri")
                )
                if not expected:
                    continue
                actual = Counter(
                    str(link["uri"]) for link in converted[page_number].get_links()
                    if link.get("kind") == fitz.LINK_URI and link.get("uri")
                ) if page_number < len(converted) else Counter()
                if expected - actual:
                    raise RuntimeError("PDF/A conversion did not preserve the source URI hyperlinks.")

    def _convert_image_to_pdf(self, source_path: Path, output_path: Path) -> None:
        try:
            with Image.open(source_path) as image:
                payload, width_px, height_px, dpi = _document_image_payload(image, source_path)
            # Embed the source JPEG stream directly when no normalization is needed.
            # Pillow's PDF writer recompresses JPEG pixels, causing an avoidable
            # generation of losses in small text and document scan edges.
            with fitz.open() as document:
                page = document.new_page(
                    width=width_px * 72.0 / dpi[0],
                    height=height_px * 72.0 / dpi[1],
                )
                page.insert_image(page.rect, stream=payload, keep_proportion=False)
                document.save(output_path, deflate=True)
        except UnidentifiedImageError as exc:
            raise ValueError(f"Uploaded file is not a readable image: {source_path}") from exc

    def _convert_image_to_docx(self, source_path: Path, output_path: Path) -> None:
        source_path = Path(source_path)
        output_path = Path(output_path)

        if not source_path.exists():
            raise FileNotFoundError(f"Source file not found: {source_path}")
        if source_path.stat().st_size == 0:
            raise ValueError(f"Source image is empty: {source_path}")

        try:
            with Image.open(source_path) as image:
                payload, width_px, height_px, _ = _document_image_payload(image, source_path)
            document = Document()
            section = document.sections[0]
            _configure_section_for_image(section, width_px, height_px)
            paragraph = document.paragraphs[0] if document.paragraphs else document.add_paragraph()
            _configure_full_page_image_paragraph(paragraph)
            picture_width, picture_height = _fit_image_to_section(
                section,
                width_px=width_px,
                height_px=height_px,
            )
            vertical_gap = max(
                0.0,
                (float(section.page_height.pt) - float(picture_height.pt)) / 2.0,
            )
            paragraph.paragraph_format.space_before = Pt(vertical_gap)
            paragraph.add_run().add_picture(
                BytesIO(payload),
                width=picture_width,
                height=picture_height,
            )
            document.save(output_path)
        except UnidentifiedImageError as e:
            with open(source_path, "rb") as f:
                header = f.read(32)
            raise ValueError(
                f"Uploaded file is not a readable image. path={source_path}, header={header!r}"
            ) from e

    def _convert_png_to_jpeg_family(self, source_path: Path, output_path: Path) -> None:
        try:
            with Image.open(source_path) as image:
                image = ImageOps.exif_transpose(image)
                rgb = _flatten_image_to_rgb(image)
                profile_options = (
                    {"icc_profile": rgb.info["icc_profile"]}
                    if rgb.info.get("icc_profile")
                    else {}
                )
                rgb.save(
                    output_path,
                    "JPEG",
                    quality=DEFAULT_IMAGE_JPEG_QUALITY,
                    subsampling=0,
                    optimize=True,
                    **profile_options,
                )
        except UnidentifiedImageError as exc:
            raise ValueError(f"Uploaded file is not a readable PNG image: {source_path}") from exc


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
    physical_extension = PHYSICAL_OUTPUT_FORMAT_BY_TARGET[normalized_output]
    return f"{base}.converted.{physical_extension}"


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
        raise ValueError(
            "format must be one of: pdf, pdfa, docx, jpg, jpeg, png, xlsx, html, htm, pptx, zip."
        )
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
    if (
        normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or ":" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("planned_output_name must be a plain filename without path components.")
    if len(normalized.encode("utf-8")) > 255:
        raise ValueError("planned_output_name exceeds the filesystem filename limit.")
    return normalized


def _safe_basename(value: str) -> str:
    raw = str(value).strip().lower()
    if not raw:
        return "document"
    filtered = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    compact = "-".join(part for part in filtered.split("-") if part)
    # Leave room for .converted.<extension> and archive page suffixes even when
    # the uploaded name uses multi-byte characters near filesystem limits.
    return compact.encode("utf-8")[:200].decode("utf-8", errors="ignore").rstrip("-") or "document"


def _get_file_size_mb(path: Path) -> float:
    return round(path.stat().st_size / (1024 * 1024), 4)


SPREADSHEETML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_CELL_REF_RE = re.compile(r"^([A-Z]+)([1-9][0-9]*)$")


def _xlsx_q(tag: str) -> str:
    return f"{{{SPREADSHEETML_NS}}}{tag}"


def _xlsx_column_index(cell_reference: str) -> Optional[int]:
    match = _XLSX_CELL_REF_RE.match(str(cell_reference or "").upper())
    if not match:
        return None
    value = 0
    for character in match.group(1):
        value = value * 26 + (ord(character) - 64)
    return value


def _xlsx_shared_strings(package: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in package.namelist():
        return []
    root = ElementTree.fromstring(package.read("xl/sharedStrings.xml"))
    return [
        "".join(node.text or "" for node in item.iter(_xlsx_q("t")))
        for item in root.findall(_xlsx_q("si"))
    ]


def _xlsx_cell_text(cell: Any, shared_strings: Sequence[str]) -> str:
    cell_type = str(cell.attrib.get("t") or "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(_xlsx_q("t")))

    value = cell.find(_xlsx_q("v"))
    if value is None:
        return ""
    raw = value.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def _xlsx_effective_column_attributes(columns: Sequence[Any], index: int) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for column in columns:
        try:
            minimum = int(column.attrib.get("min", "0"))
            maximum = int(column.attrib.get("max", "0"))
        except ValueError:
            continue
        if minimum <= index <= maximum:
            attributes = dict(column.attrib)
    return attributes


def _xlsx_add_wrapped_styles(
    styles_xml: bytes,
    style_ids: set[int],
) -> tuple[bytes, dict[int, int]]:
    if not style_ids:
        return styles_xml, {}

    ElementTree.register_namespace("", SPREADSHEETML_NS)
    root = ElementTree.fromstring(styles_xml)
    cell_xfs = root.find(_xlsx_q("cellXfs"))
    if cell_xfs is None:
        raise RuntimeError("XLSX styles.xml is missing cellXfs.")

    original_xfs = list(cell_xfs)
    if not original_xfs:
        raise RuntimeError("XLSX styles.xml contains no cell formats.")

    mapping: dict[int, int] = {}
    for requested_style_id in sorted(style_ids):
        style_id = requested_style_id if 0 <= requested_style_id < len(original_xfs) else 0
        copied = deepcopy(original_xfs[style_id])
        alignment = copied.find(_xlsx_q("alignment"))
        if alignment is None:
            alignment = ElementTree.SubElement(copied, _xlsx_q("alignment"))
        alignment.set("wrapText", "1")
        if "vertical" not in alignment.attrib:
            alignment.set("vertical", "top")
        copied.set("applyAlignment", "1")
        cell_xfs.append(copied)
        mapping[requested_style_id] = len(cell_xfs) - 1

    cell_xfs.set("count", str(len(cell_xfs)))
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), mapping


def _xlsx_insert_page_setup(root: Any) -> Any:
    existing = root.find(_xlsx_q("pageSetup"))
    if existing is not None:
        return existing

    page_setup = ElementTree.Element(_xlsx_q("pageSetup"))
    trailing_tags = {
        _xlsx_q("headerFooter"),
        _xlsx_q("rowBreaks"),
        _xlsx_q("colBreaks"),
        _xlsx_q("customProperties"),
        _xlsx_q("cellWatches"),
        _xlsx_q("ignoredErrors"),
        _xlsx_q("smartTags"),
        _xlsx_q("drawing"),
        _xlsx_q("legacyDrawing"),
        _xlsx_q("legacyDrawingHF"),
        _xlsx_q("picture"),
        _xlsx_q("oleObjects"),
        _xlsx_q("controls"),
        _xlsx_q("webPublishItems"),
        _xlsx_q("tableParts"),
        _xlsx_q("extLst"),
    }
    children = list(root)
    for position, child in enumerate(children):
        if child.tag in trailing_tags:
            root.insert(position, page_setup)
            return page_setup
    root.append(page_setup)
    return page_setup


def _xlsx_patch_sheet_for_readable_pdf(
    sheet_xml: bytes,
    shared_strings: Sequence[str],
    *,
    style_mapping: Optional[dict[int, int]] = None,
    scan_only: bool = False,
    profile: str = "balanced",
) -> tuple[bytes, set[int]]:
    normalized_profile = str(profile or "balanced").strip().lower()
    safe_profile = normalized_profile == "safe"

    ElementTree.register_namespace("", SPREADSHEETML_NS)
    root = ElementTree.fromstring(sheet_xml)
    sheet_data = root.find(_xlsx_q("sheetData"))
    if sheet_data is None:
        return sheet_xml, set()

    columns_element = root.find(_xlsx_q("cols"))
    original_columns = list(columns_element) if columns_element is not None else []

    cells: list[tuple[Any, Any, int, str, int]] = []
    lengths_by_column: dict[int, list[int]] = {}
    maximum_column = 0

    for row in sheet_data.findall(_xlsx_q("row")):
        if str(row.attrib.get("hidden", "0")).lower() in {"1", "true"}:
            continue
        for cell in row.findall(_xlsx_q("c")):
            column_index = _xlsx_column_index(cell.attrib.get("r", ""))
            if not column_index:
                continue
            maximum_column = max(maximum_column, column_index)
            text = _xlsx_cell_text(cell, shared_strings)
            if not text:
                continue
            longest_line = max((len(part) for part in text.splitlines()), default=0)
            lengths_by_column.setdefault(column_index, []).append(longest_line)
            cells.append((row, cell, column_index, text, longest_line))

    if maximum_column < 1:
        return sheet_xml, set()

    hidden: dict[int, bool] = {}
    current_widths: dict[int, float] = {}
    desired_widths: dict[int, float] = {}

    for index in range(1, maximum_column + 1):
        attributes = _xlsx_effective_column_attributes(original_columns, index)
        hidden[index] = str(attributes.get("hidden", "0")).lower() in {"1", "true"}
        try:
            current_widths[index] = float(attributes.get("width", "8.43"))
        except ValueError:
            current_widths[index] = 8.43

        values = lengths_by_column.get(index, [])
        if hidden[index]:
            desired_widths[index] = current_widths[index]
            continue
        if not values:
            desired_widths[index] = max(
                DEFAULT_XLSX_MIN_COLUMN_WIDTH,
                min(current_widths[index], DEFAULT_XLSX_MAX_COLUMN_WIDTH),
            )
            continue

        sorted_values = sorted(values)
        percentile = 1.0 if safe_profile else 0.95
        percentile_index = max(0, math.ceil(len(sorted_values) * percentile) - 1)
        representative_length = sorted_values[percentile_index]
        max_column_width = max(DEFAULT_XLSX_MAX_COLUMN_WIDTH, 48.0) if safe_profile else DEFAULT_XLSX_MAX_COLUMN_WIDTH
        width_multiplier = 1.18 if safe_profile else 1.10
        width_padding = 3.0 if safe_profile else 2.0
        target = min(
            max_column_width,
            max(DEFAULT_XLSX_MIN_COLUMN_WIDTH, representative_length * width_multiplier + width_padding),
        )
        desired_widths[index] = max(current_widths[index], target)

    visible_columns = [index for index in range(1, maximum_column + 1) if not hidden[index]]
    desired_total = sum(desired_widths[index] for index in visible_columns)
    total_column_width = (
        max(DEFAULT_XLSX_TOTAL_COLUMN_WIDTH, 128.0)
        if safe_profile
        else DEFAULT_XLSX_TOTAL_COLUMN_WIDTH
    )
    if desired_total > total_column_width and visible_columns:
        floor_total = DEFAULT_XLSX_MIN_COLUMN_WIDTH * len(visible_columns)
        distributable = max(0.0, total_column_width - floor_total)
        wants = {
            index: max(0.0, desired_widths[index] - DEFAULT_XLSX_MIN_COLUMN_WIDTH)
            for index in visible_columns
        }
        want_total = sum(wants.values())
        for index in visible_columns:
            desired_widths[index] = DEFAULT_XLSX_MIN_COLUMN_WIDTH + (
                distributable * wants[index] / want_total if want_total else 0.0
            )

    wrap_candidates: list[tuple[Any, Any, int]] = []
    requested_style_ids: set[int] = set()
    for row, cell, column_index, text, longest_line in cells:
        if hidden[column_index]:
            continue
        if safe_profile or "\n" in text or longest_line > desired_widths[column_index] * 0.92:
            try:
                style_id = int(cell.attrib.get("s", "0") or "0")
            except ValueError:
                style_id = 0
            requested_style_ids.add(style_id)
            wrap_candidates.append((row, cell, style_id))

    if scan_only:
        return sheet_xml, requested_style_ids

    style_mapping = style_mapping or {}
    for row, cell, style_id in wrap_candidates:
        cell.set("s", str(style_mapping.get(style_id, style_id)))
        # Explicit fixed row heights are a frequent second source of clipping once
        # wrapping is enabled. On the temporary conversion copy, let Calc auto-size.
        row.attrib.pop("ht", None)
        row.attrib.pop("customHeight", None)

    if safe_profile:
        # Font substitution can change glyph widths enough that a row which looked
        # safe under development fonts clips in a production container. Release the
        # height of every populated visible row in the fallback profile so Calc can
        # recompute it using the fonts that actually exist in that runtime.
        rows_with_visible_content = {id(row): row for row, _, column_index, _, _ in cells if not hidden[column_index]}
        for row in rows_with_visible_content.values():
            row.attrib.pop("ht", None)
            row.attrib.pop("customHeight", None)

    if columns_element is None:
        columns_element = ElementTree.Element(_xlsx_q("cols"))
        children = list(root)
        root.insert(children.index(sheet_data), columns_element)
    else:
        for child in list(columns_element):
            columns_element.remove(child)

    for index in range(1, maximum_column + 1):
        attributes = _xlsx_effective_column_attributes(original_columns, index)
        attributes["min"] = str(index)
        attributes["max"] = str(index)
        if not hidden[index]:
            attributes["width"] = f"{desired_widths[index]:.3f}".rstrip("0").rstrip(".")
            attributes["customWidth"] = "1"
            attributes.pop("bestFit", None)
        ElementTree.SubElement(columns_element, _xlsx_q("col"), attributes)

    # Preserve any explicit column definitions that start beyond the populated area.
    for column in original_columns:
        try:
            if int(column.attrib.get("min", "0")) > maximum_column:
                columns_element.append(deepcopy(column))
        except ValueError:
            continue

    sheet_properties = root.find(_xlsx_q("sheetPr"))
    if sheet_properties is None:
        sheet_properties = ElementTree.Element(_xlsx_q("sheetPr"))
        root.insert(0, sheet_properties)
    page_setup_properties = sheet_properties.find(_xlsx_q("pageSetUpPr"))
    if page_setup_properties is None:
        page_setup_properties = ElementTree.SubElement(sheet_properties, _xlsx_q("pageSetUpPr"))
    page_setup_properties.set("fitToPage", "1")

    page_setup = _xlsx_insert_page_setup(root)
    page_setup.set("fitToWidth", "1")
    page_setup.set("fitToHeight", "0")
    page_setup.attrib.pop("scale", None)
    if "orientation" not in page_setup.attrib:
        page_setup.set(
            "orientation",
            "landscape"
            if sum(desired_widths[index] for index in visible_columns) >= DEFAULT_XLSX_LANDSCAPE_THRESHOLD
            else "portrait",
        )
    if "paperSize" not in page_setup.attrib:
        paper_sizes = {"LETTER": "1", "LEGAL": "5", "A4": "9"}
        page_setup.set("paperSize", paper_sizes.get(DEFAULT_XLSX_PAPER_SIZE, "9"))

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True), requested_style_ids


def _prepare_xlsx_for_pdf(path: Path, *, profile: str = "balanced") -> None:
    """Normalize a temporary XLSX copy for readable, complete PDF output.

    The OOXML package is rewritten in-place without loading/saving it through an
    office library, so charts, drawings, formulas, relationships, macros/extensions
    and unknown package parts remain byte-for-byte untouched unless they are one of
    the worksheet/style parts required for print readability.
    """
    path = Path(path).resolve()
    if DEFAULT_XLSX_LAYOUT_MODE == "preserve":
        return
    if not zipfile.is_zipfile(path):
        raise ValueError(f"File content is not an OOXML XLSX package: {path}")

    temp_path = path.with_name(f".{path.name}.redocx-normalizing")
    temp_path.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(path, "r") as source_package:
            names = set(source_package.namelist())
            sheet_paths = sorted(
                name
                for name in names
                if name.startswith("xl/worksheets/") and name.endswith(".xml")
            )
            if not sheet_paths or "xl/styles.xml" not in names:
                raise ValueError("XLSX package is missing worksheet/style parts.")

            shared_strings = _xlsx_shared_strings(source_package)
            requested_style_ids: set[int] = set()
            for sheet_path in sheet_paths:
                _, style_ids = _xlsx_patch_sheet_for_readable_pdf(
                    source_package.read(sheet_path),
                    shared_strings,
                    scan_only=True,
                    profile=profile,
                )
                requested_style_ids.update(style_ids)

            styles_xml, style_mapping = _xlsx_add_wrapped_styles(
                source_package.read("xl/styles.xml"),
                requested_style_ids,
            )

            with zipfile.ZipFile(temp_path, "w") as target_package:
                for info in source_package.infolist():
                    payload = source_package.read(info.filename)
                    if info.filename == "xl/styles.xml":
                        payload = styles_xml
                    elif info.filename in sheet_paths:
                        payload, _ = _xlsx_patch_sheet_for_readable_pdf(
                            payload,
                            shared_strings,
                            style_mapping=style_mapping,
                            profile=profile,
                        )
                    target_package.writestr(info, payload)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _pdf_pptx_font_family(font_name: str) -> str:
    name = re.sub(r"^[A-Z]{6}\+", "", font_name)
    name = re.sub(r"(?:[-,]?(?:BoldItalic|BoldOblique|Bold|Italic|Oblique|Regular|Roman|MT))+$", "", name, flags=re.I)
    aliases = {
        "DejaVuSans": "DejaVu Sans", "DejaVuSerif": "DejaVu Serif",
        "DejaVuSansMono": "DejaVu Sans Mono", "LiberationSans": "Liberation Sans",
        "LiberationSerif": "Liberation Serif", "LiberationMono": "Liberation Mono",
        "Helvetica": "Arial", "Times": "Times New Roman", "Courier": "Courier New",
        "TimesNewRomanPS": "Times New Roman", "Arial": "Arial",
    }
    return aliases.get(name, name or "Arial")


def _pdf_pptx_editable_spans(page: fitz.Page) -> Optional[list[dict[str, Any]]]:
    # Vertical/rotated writing, clipping, transparency and bidirectional shaping
    # need a richer presentation model. Keep their source rendering intact.
    if page.rotation:
        return None
    spans: list[dict[str, Any]] = []
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES).get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            direction = line.get("dir", (1.0, 0.0))
            if line.get("wmode", 0) or abs(direction[0] - 1.0) > 0.001 or abs(direction[1]) > 0.001:
                return None
            for span in line.get("spans", []):
                if not str(span.get("text", "")).strip():
                    continue
                if (
                    float(span.get("size", 0)) <= 0
                    or int(span.get("alpha", 255)) != 255
                    or int(span.get("bidi", 0))
                    or ("char_flags" in span and not int(span["char_flags"]) & 16)
                    or not page.rect.contains(fitz.Rect(span["bbox"]))
                ):
                    return None
                spans.append(span)
    return spans or None


def _add_pdf_pptx_hyperlinks(
    slide: Any, page: fitz.Page, left: float, top: float, fit_scale: float
) -> None:
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor as PptxRGBColor
    from pptx.oxml.xmlchemy import OxmlElement as PptxOxmlElement

    for link in page.get_links():
        target = str(link.get("uri") or "").strip()
        if not target or not link.get("from"):
            continue
        rectangle = fitz.Rect(link["from"])
        if page.rotation:
            rectangle = rectangle * page.rotation_matrix
        rectangle &= page.rect
        if rectangle.is_empty:
            continue
        shape = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            int((left + rectangle.x0 * fit_scale) * 12700),
            int((top + rectangle.y0 * fit_scale) * 12700),
            max(1, int(rectangle.width * fit_scale * 12700)),
            max(1, int(rectangle.height * fit_scale * 12700)),
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = PptxRGBColor(255, 255, 255)
        color = shape.fill.fore_color._xFill.find("{http://schemas.openxmlformats.org/drawingml/2006/main}srgbClr")
        alpha = PptxOxmlElement("a:alpha")
        alpha.set("val", "0")
        color.append(alpha)
        shape.line.fill.background()
        shape.click_action.hyperlink.address = target


def _write_pdf_text_cell(sheet: Any, row: int, column: int, text: str) -> None:
    """Write extracted PDF content literally, without Excel formula inference."""

    if not 1 <= row <= 1048576 or not 1 <= column <= 16384:
        raise RuntimeError("PDF-to-Excel content exceeds Excel worksheet dimensions.")
    if len(text) > 32767:
        raise RuntimeError(
            "PDF-to-Excel content exceeds Excel's 32767-character cell limit; "
            "ReDOCX refused to silently truncate the source text."
        )
    cell = sheet.cell(row=row, column=column, value=text)
    # '=SUM(...)' and '#N/A' are literal source text, not newly authored
    # formulas/errors. Setting the type after assignment retains the exact value.
    cell.data_type = "s"


def _pdf_to_docx_mode() -> str:
    mode = (DEFAULT_PDF_TO_DOCX_MODE or "auto").strip().lower()
    return mode if mode in PDF_TO_DOCX_MODES else "auto"



def _normalized_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    # Canonicalize common thousands separators before tokenization so a numeric
    # value stored as 125000 can be matched to rendered text such as 125,000.00.
    normalized = re.sub(r"(?<=\d)[,\u00a0\u202f ](?=\d{3}(?:\D|$))", "", normalized)
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            token = "".join(current)
            if len(token) >= 3:
                tokens.append(token)
            current = []
    if current:
        token = "".join(current)
        if len(token) >= 3:
            tokens.append(token)
    return tokens


def _token_coverage(source_text: str, output_text: str) -> tuple[float, int, int]:
    source = Counter(_normalized_tokens(source_text))
    if not source:
        return 1.0, 0, 0
    output = Counter(_normalized_tokens(output_text))
    matched = sum(min(count, output.get(token, 0)) for token, count in source.items())
    total = sum(source.values())
    return matched / total, matched, total


def _assert_text_token_coverage(
    *,
    source_text: str,
    output_text: str,
    minimum: float,
    label: str,
) -> None:
    ratio, matched, total = _token_coverage(source_text, output_text)
    if total < 5:
        # Tiny documents are better handled by structural validation; a single
        # punctuation/font extraction quirk should not turn a valid conversion into
        # a false negative.
        return
    if ratio + 1e-9 < max(0.0, min(1.0, minimum)):
        raise RuntimeError(
            f"{label} failed ReDOCX content-retention validation: "
            f"{matched}/{total} source tokens were recoverable from the output "
            f"({ratio:.1%}; required {minimum:.1%}). ReDOCX refused to return a "
            "structurally valid but materially incomplete conversion."
        )


def _docx_package_text(path: Path, *, join_runs: bool = False) -> str:
    text_tag = f"{{{WORDPROCESSINGML_NS}}}t"
    chunks: list[str] = []
    with zipfile.ZipFile(path) as package:
        # Only content that is part of the rendered document is included here.
        # Word comments are review metadata and are not expected to appear in a
        # normal PDF export, so treating them as required output text would create
        # false conversion failures.
        content_part_re = re.compile(
            r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes)\.xml$"
        )
        for name in package.namelist():
            if not content_part_re.fullmatch(name):
                continue
            try:
                root = ElementTree.fromstring(package.read(name))
            except ElementTree.ParseError:
                continue
            if join_runs:
                # Keep text in its own paragraph, including positioned textboxes.
                run_tag = qn("w:r")
                hyperlink_tag = qn("w:hyperlink")
                separators = {qn("w:tab"), qn("w:br"), qn("w:cr")}
                for paragraph in root.iter(qn("w:p")):
                    runs = (
                        run for child in paragraph
                        for run in (child.findall(run_tag) if child.tag == hyperlink_tag else (child,))
                        if run.tag == run_tag
                    )
                    chunks.append("".join(
                        (node.text or "") if node.tag == text_tag else " "
                        for run in runs for node in run
                        if node.tag == text_tag or node.tag in separators
                    ))
            else:
                chunks.extend(node.text or "" for node in root.iter(text_tag))
    return "\n".join(chunks)


def _pptx_visible_slide_text(path: Path) -> str:
    drawing_text_tag = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
    chunks: list[str] = []
    with zipfile.ZipFile(path) as package:
        slide_paths = sorted(
            name
            for name in package.namelist()
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
        )
        for slide_path in slide_paths:
            try:
                root = ElementTree.fromstring(package.read(slide_path))
            except ElementTree.ParseError:
                continue
            if str(root.attrib.get("show", "1")).lower() in {"0", "false"}:
                continue
            chunks.extend(node.text or "" for node in root.iter(drawing_text_tag))
    return "\n".join(chunks)


def _xlsx_nonliteral_number_format(format_code: str) -> str:
    value = re.sub(r'"(?:[^"\\]|\\.)*"', "", str(format_code or ""))
    value = re.sub(r"\\.", "", value)
    return value.casefold()


def _xlsx_numeric_styles_to_skip_for_text_gate(package: zipfile.ZipFile) -> set[int]:
    """Return style IDs whose raw stored numeric is not comparable to PDF text.

    Dates/times and scientific formats transform the stored scalar substantially at
    display time. They are intentionally excluded from raw-token comparison rather
    than producing false conversion failures. Ordinary number/currency/accounting
    formats remain eligible and are normalized for common thousands separators.
    """

    if "xl/styles.xml" not in package.namelist():
        return set()
    try:
        root = ElementTree.fromstring(package.read("xl/styles.xml"))
    except ElementTree.ParseError:
        return set()

    custom_formats: dict[int, str] = {}
    num_fmts = root.find(_xlsx_q("numFmts"))
    if num_fmts is not None:
        for item in num_fmts.findall(_xlsx_q("numFmt")):
            try:
                num_fmt_id = int(item.attrib.get("numFmtId", ""))
            except ValueError:
                continue
            custom_formats[num_fmt_id] = item.attrib.get("formatCode", "")

    # ECMA/Excel built-in date/time styles plus built-in scientific formats.
    built_in_date_ids = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
    built_in_scientific_ids = {11, 48}
    skipped: set[int] = set()
    cell_xfs = root.find(_xlsx_q("cellXfs"))
    if cell_xfs is None:
        return skipped

    for style_id, xf in enumerate(cell_xfs.findall(_xlsx_q("xf"))):
        try:
            num_fmt_id = int(xf.attrib.get("numFmtId", "0"))
        except ValueError:
            num_fmt_id = 0
        if num_fmt_id in built_in_date_ids or num_fmt_id in built_in_scientific_ids:
            skipped.add(style_id)
            continue
        custom = _xlsx_nonliteral_number_format(custom_formats.get(num_fmt_id, ""))
        if custom and (
            re.search(r"[ydhs]", custom)
            or ("m" in custom and any(marker in custom for marker in ("/", "-", ":")))
            or "e+" in custom
            or "e-" in custom
        ):
            skipped.add(style_id)
    return skipped


def _xlsx_visible_cell_values(path: Path) -> list[str]:
    """Return user-visible XLSX cell values suitable for fidelity auditing.

    Raw spreadsheet storage numbers whose display depends on number-format rules
    are intentionally excluded, matching the previous text gate. Textual values,
    booleans, string formula results, and numeric identifiers without display-only
    formatting remain auditable. Hidden rows and columns are excluded because they
    are not expected to render in a normal spreadsheet PDF export.
    """
    values: list[str] = []
    with zipfile.ZipFile(path) as package:
        shared_strings = _xlsx_shared_strings(package)
        numeric_styles_to_skip = _xlsx_numeric_styles_to_skip_for_text_gate(package)
        for sheet_path in sorted(
            name
            for name in package.namelist()
            if name.startswith("xl/worksheets/") and name.endswith(".xml")
        ):
            try:
                root = ElementTree.fromstring(package.read(sheet_path))
            except ElementTree.ParseError:
                continue
            columns_element = root.find(_xlsx_q("cols"))
            columns = list(columns_element) if columns_element is not None else []
            sheet_data = root.find(_xlsx_q("sheetData"))
            if sheet_data is None:
                continue
            for row in sheet_data.findall(_xlsx_q("row")):
                if str(row.attrib.get("hidden", "0")).lower() in {"1", "true"}:
                    continue
                for cell in row.findall(_xlsx_q("c")):
                    column_index = _xlsx_column_index(cell.attrib.get("r", ""))
                    if not column_index:
                        continue
                    attributes = _xlsx_effective_column_attributes(columns, column_index)
                    if str(attributes.get("hidden", "0")).lower() in {"1", "true"}:
                        continue
                    cell_type = str(cell.attrib.get("t") or "")
                    text = _xlsx_cell_text(cell, shared_strings)
                    if not text:
                        continue

                    if cell_type in {"s", "inlineStr", "str", "b"}:
                        values.append(text)
                    else:
                        try:
                            style_id = int(cell.attrib.get("s", "0") or "0")
                        except ValueError:
                            style_id = 0
                        if style_id not in numeric_styles_to_skip:
                            values.append(text)
    return values


def _xlsx_visible_cell_text(path: Path) -> str:
    return "\n".join(_xlsx_visible_cell_values(path))


def _xlsx_retention_key(value: str, *, preserve_punctuation: bool) -> str:
    """Canonicalize text for cell-aware PDF retention checks.

    PDF extractors may insert whitespace/newlines inside a visually complete cell
    when wrapping occurs. Removing whitespace makes the audit invariant to those
    extractor artifacts while still requiring the actual characters to survive.
    A punctuation-preserving form is used first; an alphanumeric form provides a
    fallback for benign punctuation-normalization differences.
    """
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = (
        normalized.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
    )
    if preserve_punctuation:
        allowed_punctuation = set("@._+-/:#%&()[]{}")
        return "".join(
            character
            for character in normalized
            if character.isalnum() or character in allowed_punctuation
        )
    return "".join(character for character in normalized if character.isalnum())


def _count_nonoverlapping_occurrences(haystack: str, needle: str) -> int:
    if not haystack or not needle:
        return 0
    return haystack.count(needle)


def _xlsx_pdf_cell_retention_metrics(
    source_path: Path,
    output_path: Path,
) -> dict[str, float | int]:
    """Measure XLSX-to-PDF fidelity without depending on word token boundaries.

    The previous gate compared whole tokens. That is useful diagnostically, but a
    PDF extractor is allowed to emit ``exam\nple`` for a wrapped visual string
    ``example``. In a different LibreOffice/font environment this made a complete
    conversion appear incomplete. The cell-aware audit removes extractor-inserted
    whitespace and verifies source cell values against the full PDF text stream.

    Multiplicity is retained: repeated source cell values must occur at least the
    same number of times in the PDF stream to receive full credit. Coverage is
    reported both by cell count and by source-character weight so one missing long
    identifier cannot be hidden by many short successfully rendered cells.
    """
    source_values = _xlsx_visible_cell_values(source_path)
    output_text = _pdf_native_text(output_path)

    strict_output = _xlsx_retention_key(output_text, preserve_punctuation=True)
    relaxed_output = _xlsx_retention_key(output_text, preserve_punctuation=False)

    strict_source = Counter(
        key
        for value in source_values
        if len(key := _xlsx_retention_key(value, preserve_punctuation=True)) >= 4
    )
    relaxed_source = Counter(
        key
        for value in source_values
        if len(key := _xlsx_retention_key(value, preserve_punctuation=False)) >= 4
    )

    def score(source_counter: Counter[str], output_compact: str) -> tuple[int, int, int, int]:
        matched_cells = 0
        total_cells = 0
        matched_chars = 0
        total_chars = 0
        for key, required_count in source_counter.items():
            available_count = _count_nonoverlapping_occurrences(output_compact, key)
            matched_count = min(required_count, available_count)
            matched_cells += matched_count
            total_cells += required_count
            matched_chars += matched_count * len(key)
            total_chars += required_count * len(key)
        return matched_cells, total_cells, matched_chars, total_chars

    strict = score(strict_source, strict_output)
    relaxed = score(relaxed_source, relaxed_output)

    # Use the stronger punctuation-preserving match when it works. The relaxed
    # match may only improve coverage where PDF Unicode normalization changes
    # punctuation, never where alphanumeric source content disappeared.
    matched_cells = max(strict[0], relaxed[0])
    total_cells = max(strict[1], relaxed[1])
    matched_chars = max(strict[2], relaxed[2])
    total_chars = max(strict[3], relaxed[3])

    cell_ratio = matched_cells / total_cells if total_cells else 1.0
    character_ratio = matched_chars / total_chars if total_chars else 1.0
    token_ratio, matched_tokens, total_tokens = _token_coverage(
        _xlsx_visible_cell_text(source_path),
        output_text,
    )

    return {
        "cell_ratio": cell_ratio,
        "character_ratio": character_ratio,
        "matched_cells": matched_cells,
        "total_cells": total_cells,
        "matched_characters": matched_chars,
        "total_characters": total_chars,
        "token_ratio": token_ratio,
        "matched_tokens": matched_tokens,
        "total_tokens": total_tokens,
    }


def _xlsx_pdf_fidelity_passes(metrics: Mapping[str, float | int]) -> bool:
    # Token coverage remains diagnostic only. PDF extractors may split a visually
    # complete wrapped value at arbitrary glyph positions, so token boundaries must
    # never be the acceptance authority for XLSX -> PDF. The cell-aware metrics are
    # stricter about actual source characters while being invariant to layout-only
    # whitespace inserted by the extractor.
    cell_ratio = float(metrics.get("cell_ratio", 0.0))
    character_ratio = float(metrics.get("character_ratio", 0.0))
    return (
        cell_ratio + 1e-9 >= max(0.0, min(1.0, DEFAULT_XLSX_PDF_CELL_COVERAGE))
        and character_ratio + 1e-9
        >= max(0.0, min(1.0, DEFAULT_XLSX_PDF_CHARACTER_COVERAGE))
    )


def _assert_xlsx_pdf_fidelity(source_path: Path, output_path: Path) -> None:
    metrics = _xlsx_pdf_cell_retention_metrics(source_path, output_path)
    if _xlsx_pdf_fidelity_passes(metrics):
        return

    raise RuntimeError(
        "Excel-to-PDF conversion failed ReDOCX content-retention validation after "
        "environment-tolerant cell auditing: "
        f"token coverage {int(metrics['matched_tokens'])}/{int(metrics['total_tokens'])} "
        f"({float(metrics['token_ratio']):.1%}; token target {DEFAULT_XLSX_PDF_TOKEN_COVERAGE:.1%}); "
        f"cell coverage {int(metrics['matched_cells'])}/{int(metrics['total_cells'])} "
        f"({float(metrics['cell_ratio']):.1%}; target {DEFAULT_XLSX_PDF_CELL_COVERAGE:.1%}); "
        f"character-weighted coverage {float(metrics['character_ratio']):.1%} "
        f"(target {DEFAULT_XLSX_PDF_CHARACTER_COVERAGE:.1%}). "
        "ReDOCX refused to return a structurally valid but materially incomplete conversion."
    )


class _VisibleHtmlTextParser(HTMLParser):
    _IGNORED_TAGS = {"script", "style", "head", "template", "noscript"}
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        normalized_tag = tag.casefold()
        attributes = {str(key).casefold(): str(value or "") for key, value in attrs}
        style = attributes.get("style", "").replace(" ", "").casefold()
        hidden = (
            normalized_tag in self._IGNORED_TAGS
            or "hidden" in attributes
            or "display:none" in style
            or "visibility:hidden" in style
        )
        if normalized_tag in self._VOID_TAGS:
            return
        self._stack.append((normalized_tag, hidden))
        if hidden:
            self._hidden_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        # Self-closing tags cannot contribute visible text directly.
        return

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        # HTML can be malformed. Unwind to the matching open tag instead of
        # blindly popping one entry and corrupting hidden-depth accounting.
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] != normalized_tag:
                continue
            closing = self._stack[index:]
            del self._stack[index:]
            self._hidden_depth = max(
                0, self._hidden_depth - sum(1 for _, hidden in closing if hidden)
            )
            break

    def handle_data(self, data: str) -> None:
        if self._hidden_depth == 0 and data.strip():
            self.parts.append(data)


def _html_visible_text(path: Path) -> str:
    parser = _VisibleHtmlTextParser()
    parser.feed(path.read_text(encoding="utf-8"))
    parser.close()
    return "\n".join(parser.parts)


def _pdf_page_count(path: Path) -> int:
    with fitz.open(path) as document:
        return int(document.page_count)


def _validate_pdf_to_jpg_page_count(source_path: Path, output_path: Path, physical_format: str) -> None:
    expected = _pdf_page_count(source_path)
    if physical_format == "jpg":
        if expected != 1:
            raise RuntimeError("Multi-page PDF-to-JPG conversion must return a page-image archive.")
        return
    if physical_format != "zip":
        raise RuntimeError("PDF-to-JPG produced an unexpected physical output format.")
    with zipfile.ZipFile(output_path) as archive:
        actual = len([info for info in archive.infolist() if not info.is_dir()])
    if actual != expected:
        raise RuntimeError(
            f"PDF-to-JPG page-count mismatch: source has {expected} pages, output has {actual} images."
        )


def _validate_pdf_to_pptx_page_count(source_path: Path, output_path: Path) -> None:
    if PptxPresentation is None:
        return
    expected = _pdf_page_count(source_path)
    actual = len(PptxPresentation(str(output_path)).slides)
    if actual != expected:
        raise RuntimeError(
            f"PDF-to-PowerPoint page-count mismatch: source has {expected} pages, output has {actual} slides."
        )


def _validate_pdf_to_xlsx_page_count(source_path: Path, output_path: Path) -> None:
    if load_workbook is None:
        return
    expected = _pdf_page_count(source_path)
    workbook = load_workbook(output_path, read_only=False, data_only=False)
    try:
        actual = len(workbook.worksheets)
        visual_pages = sum(1 for sheet in workbook.worksheets if getattr(sheet, "_images", []))
    finally:
        workbook.close()
    if actual != expected:
        raise RuntimeError(
            f"PDF-to-Excel page-count mismatch: source has {expected} pages, output has {actual} worksheets."
        )
    if visual_pages != expected:
        raise RuntimeError(
            "PDF-to-Excel fidelity validation failed: every source page must retain a visual page reference."
        )


def _validate_image_dimensions_preserved(source_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source_image, Image.open(output_path) as output_image:
        source_size = ImageOps.exif_transpose(source_image).size
        output_size = output_image.size
    if tuple(source_size) != tuple(output_size):
        raise RuntimeError(
            f"Image conversion changed pixel dimensions from {source_size} to {output_size}."
        )


def _pdf_page_comparison_image(
    page: fitz.Page,
    *,
    width_px: int = 720,
    target_height_px: Optional[int] = None,
) -> Image.Image:
    """Render a PDF page to a deterministic low-resolution grayscale QA image."""

    width_px = max(240, min(1600, int(width_px)))
    page_width = max(1.0, float(page.rect.width))
    page_height = max(1.0, float(page.rect.height))
    scale = max(1.0, width_px / page_width)
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        colorspace=fitz.csGRAY,
        alpha=False,
    )
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    target_height = (
        max(1, int(target_height_px))
        if target_height_px is not None
        else max(1, round(width_px * page_height / page_width))
    )
    if image.size != (width_px, target_height):
        image = image.resize((width_px, target_height), Image.Resampling.LANCZOS)
    return image


def _binary_ink_mask(image: Image.Image, *, threshold: int = 238) -> Image.Image:
    return image.point(lambda value: 255 if value < threshold else 0, mode="L")


def _mask_pixel_count(mask: Image.Image) -> int:
    histogram = mask.histogram()
    return int(histogram[255]) if len(histogram) > 255 else 0


def _page_ink_layout_score(source: Image.Image, output: Image.Image) -> float:
    """Return a font-tolerant visual-layout score in [0, 1].

    A small dilation radius tolerates renderer/font antialiasing differences while
    still penalizing blocks that moved, merged, reflowed, or changed orientation.
    """

    source_mask = _binary_ink_mask(source)
    output_mask = _binary_ink_mask(output)
    source_count = _mask_pixel_count(source_mask)
    output_count = _mask_pixel_count(output_mask)
    if source_count == 0 and output_count == 0:
        return 1.0
    if source_count == 0 or output_count == 0:
        return 0.0

    source_dilated = source_mask.filter(ImageFilter.MaxFilter(5))
    output_dilated = output_mask.filter(ImageFilter.MaxFilter(5))
    matched_source = _mask_pixel_count(ImageChops.multiply(source_mask, output_dilated))
    matched_output = _mask_pixel_count(ImageChops.multiply(output_mask, source_dilated))
    recall = matched_source / source_count
    precision = matched_output / output_count
    if precision + recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _page_grayscale_similarity(source: Image.Image, output: Image.Image) -> float:
    if source.size != output.size:
        output = output.resize(source.size, Image.Resampling.LANCZOS)
    difference = ImageChops.difference(source, output)
    mean_difference = float(ImageStat.Stat(difference).mean[0])
    return max(0.0, min(1.0, 1.0 - mean_difference / 255.0))


def _assert_pdf_visual_layout_fidelity(
    source_path: Path,
    output_path: Path,
    *,
    minimum_score: float,
    label: str,
) -> None:
    """Fail closed when a PDF round-trip materially changes page layout."""

    minimum_score = max(0.0, min(1.0, float(minimum_score)))
    with fitz.open(source_path) as source_pdf, fitz.open(output_path) as output_pdf:
        if source_pdf.page_count != output_pdf.page_count:
            raise RuntimeError(
                f"{label} changed page count from {source_pdf.page_count} to {output_pdf.page_count}."
            )

        for page_index in range(source_pdf.page_count):
            source_page = source_pdf.load_page(page_index)
            output_page = output_pdf.load_page(page_index)
            source_width = max(1.0, float(source_page.rect.width))
            source_height = max(1.0, float(source_page.rect.height))
            output_width = max(1.0, float(output_page.rect.width))
            output_height = max(1.0, float(output_page.rect.height))

            source_landscape = source_width > source_height
            output_landscape = output_width > output_height
            if source_landscape != output_landscape:
                raise RuntimeError(
                    f"{label} changed page {page_index + 1} orientation."
                )
            if (
                abs(source_width - output_width) / source_width > 0.02
                or abs(source_height - output_height) / source_height > 0.02
            ):
                raise RuntimeError(
                    f"{label} changed page {page_index + 1} dimensions from "
                    f"{source_width:.1f}x{source_height:.1f} pt to "
                    f"{output_width:.1f}x{output_height:.1f} pt."
                )

            source_image = _pdf_page_comparison_image(source_page)
            output_image = _pdf_page_comparison_image(
                output_page,
                width_px=source_image.width,
                target_height_px=source_image.height,
            )
            source_text_chars = len(
                _normalized_alphanumeric_text(source_page.get_text("text"))
            )
            score = (
                _page_ink_layout_score(source_image, output_image)
                if source_text_chars >= 20
                else _page_grayscale_similarity(source_image, output_image)
            )
            if score + 1e-9 < minimum_score:
                raise RuntimeError(
                    f"{label} failed visual-layout validation on page {page_index + 1}: "
                    f"score {score:.3f}, required {minimum_score:.3f}. ReDOCX refused "
                    "to return a materially reflowed or rearranged artifact."
                )


def _validate_conversion_fidelity(
    *,
    source_path: Path,
    input_format: str,
    requested_output_format: str,
    output_path: Path,
    physical_output_format: str,
) -> None:
    pair = (input_format, requested_output_format)

    if pair == ("xlsx", "pdf"):
        _assert_xlsx_pdf_fidelity(source_path, output_path)
        return

    if pair == ("docx", "pdf"):
        _assert_text_token_coverage(
            source_text=_docx_package_text(source_path),
            output_text=_pdf_native_text(output_path),
            minimum=DEFAULT_TEXT_TOKEN_COVERAGE,
            label="Word-to-PDF conversion",
        )
        return

    if pair == ("pptx", "pdf"):
        _assert_text_token_coverage(
            source_text=_pptx_visible_slide_text(source_path),
            output_text=_pdf_native_text(output_path),
            minimum=DEFAULT_TEXT_TOKEN_COVERAGE,
            label="PowerPoint-to-PDF conversion",
        )
        return

    if pair in {("html", "pdf"), ("htm", "pdf")}:
        _assert_text_token_coverage(
            source_text=_html_visible_text(source_path),
            output_text=_pdf_native_text(output_path),
            minimum=DEFAULT_TEXT_TOKEN_COVERAGE,
            label="HTML-to-PDF conversion",
        )
        return

    if pair == ("pdf", "docx"):
        _assert_text_token_coverage(
            source_text=_pdf_native_text(source_path),
            output_text=_docx_package_text(output_path, join_runs=True),
            minimum=max(0.70, MIN_PDF_TEXT_RETENTION_RATIO),
            label="PDF-to-Word conversion",
        )
        return

    if pair == ("pdf", "jpg"):
        _validate_pdf_to_jpg_page_count(source_path, output_path, physical_output_format)
        return

    if pair == ("pdf", "pptx"):
        _validate_pdf_to_pptx_page_count(source_path, output_path)
        return

    if pair == ("pdf", "xlsx"):
        _validate_pdf_to_xlsx_page_count(source_path, output_path)
        return

    if pair == ("pdf", "pdfa"):
        if _pdf_page_count(source_path) != _pdf_page_count(output_path):
            raise RuntimeError("PDF/A conversion changed the document page count.")
        # Ghostscript may legitimately rewrite font encodings while preserving the
        # exact visible glyphs (especially when converting simple TrueType fonts to
        # embedded CID subsets). A near-100% extractor-token gate therefore creates
        # false failures on visually identical, conformant archival output. Keep a
        # conservative searchable-text floor for catastrophic-loss detection, but
        # make page geometry/render fidelity plus independent veraPDF conformance the
        # authoritative acceptance gates for PDF/A.
        _assert_text_token_coverage(
            source_text=_pdf_native_text(source_path),
            output_text=_pdf_native_text(output_path),
            minimum=max(0.0, min(1.0, DEFAULT_PDFA_TEXT_TOKEN_COVERAGE)),
            label="PDF-to-PDF/A conversion",
        )
        _assert_pdf_visual_layout_fidelity(
            source_path,
            output_path,
            minimum_score=max(0.0, min(1.0, DEFAULT_PDFA_VISUAL_MIN_SCORE)),
            label="PDF-to-PDF/A conversion",
        )
        return

    if pair in {("jpg", "pdf"), ("jpeg", "pdf")}:
        if _pdf_page_count(output_path) != 1:
            raise RuntimeError("Image-to-PDF conversion must produce exactly one PDF page.")
        return

    if pair in {("jpg", "docx"), ("jpeg", "docx")}:
        with zipfile.ZipFile(output_path) as package:
            media = [name for name in package.namelist() if name.startswith("word/media/")]
        if not media:
            raise RuntimeError("Image-to-Word conversion produced a DOCX without an embedded image.")
        return

    if input_format == "png" and requested_output_format in {"jpg", "jpeg"}:
        _validate_image_dimensions_preserved(source_path, output_path)
        return


def _validate_source_format(path: Path, expected_format: str) -> None:
    """Reject extension/MIME spoofing before any conversion engine is invoked."""

    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Source file is missing or empty: {path}")

    if expected_format == "pdf":
        _validate_pdf_file(path)
    elif expected_format == "docx":
        _validate_docx_file(path)
    elif expected_format == "xlsx":
        _validate_xlsx_file(path)
    elif expected_format == "pptx":
        _validate_pptx_file(path)
    elif expected_format in {"html", "htm"}:
        _validate_html_file(path)
    elif expected_format in {"jpg", "jpeg", "png"}:
        _validate_image_file(path, expected_format)
    else:  # pragma: no cover - guarded by _normalize_format
        raise ValueError(f"Unsupported source format validation: {expected_format}")


def _validate_converted_output(
    path: Path,
    expected_format: str,
    *,
    source_path: Optional[Path] = None,
    input_format: Optional[str] = None,
) -> None:
    """Verify output bytes, not merely the filename or declared MIME type."""

    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Conversion output is missing or empty: {path}")

    expected_suffixes = {"jpg": {".jpg"}, "jpeg": {".jpeg"}}.get(
        expected_format,
        {f".{expected_format}"},
    )
    if path.suffix.lower() not in expected_suffixes:
        raise RuntimeError(
            f"Conversion output extension {path.suffix or '(none)'} does not match {expected_format}."
        )

    if expected_format == "pdf":
        _validate_pdf_file(path)
    elif expected_format == "docx":
        _validate_docx_file(path)
        if input_format == "pdf" and source_path is not None:
            _validate_pdf_to_docx_editability(source_path, path)
            _assert_pdf_hyperlinks_preserved(path, _extract_pdf_hyperlinks(source_path))
    elif expected_format in {"jpg", "jpeg"}:
        _validate_image_file(path, expected_format)
    elif expected_format == "pptx":
        _validate_pptx_file(path)
    elif expected_format == "xlsx":
        _validate_xlsx_file(path)
    elif expected_format == "zip":
        _validate_jpg_archive(path)
    else:
        raise RuntimeError(f"Unsupported conversion output validation: {expected_format}")


def _validate_pdf_file(path: Path) -> None:
    try:
        with path.open("rb") as stream:
            header = stream.read(8)
        if not header.startswith(b"%PDF-"):
            raise ValueError(f"File content is not a PDF: {path}")
        with fitz.open(path) as pdf:
            if pdf.page_count < 1:
                raise ValueError(f"PDF contains no pages: {path}")
            if pdf.is_encrypted or pdf.needs_pass:
                raise ValueError(f"Password-protected PDF is not supported: {path}")
            # Force page-tree parsing so truncated/corrupt files fail now, before storage.
            for page_number in range(pdf.page_count):
                _ = pdf.load_page(page_number).rect
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"File is not a readable PDF: {path}") from exc



def _validate_html_file(path: Path) -> None:
    try:
        data = path.read_bytes()
        if b"\x00" in data:
            raise ValueError("HTML contains null bytes.")
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("HTML input must be valid UTF-8.") from exc
    if not text.strip():
        raise ValueError("HTML input is empty.")


def _validate_xlsx_file(path: Path) -> None:
    if load_workbook is None:
        raise RuntimeError("openpyxl is required for XLSX conversion.")
    try:
        workbook = load_workbook(path, read_only=True, data_only=False)
        try:
            if not workbook.sheetnames:
                raise ValueError("XLSX workbook contains no worksheets.")
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"File is not a readable XLSX workbook: {path}") from exc


def _validate_pptx_file(path: Path) -> None:
    if PptxPresentation is None:
        raise RuntimeError("python-pptx is required for PPTX conversion.")
    try:
        presentation = PptxPresentation(str(path))
        if len(presentation.slides) < 1:
            raise ValueError("PPTX presentation contains no slides.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"File is not a readable PPTX presentation: {path}") from exc


def _validate_jpg_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if not infos:
                raise RuntimeError("PDF to JPG archive contains no page images.")
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts:
                    raise RuntimeError("PDF to JPG archive contains an unsafe entry path.")
                if not name.lower().endswith(".jpg"):
                    raise RuntimeError("PDF to JPG archive contains a non-JPG entry.")
                if info.file_size <= 0:
                    raise RuntimeError("PDF to JPG archive contains an empty page image.")
                payload = archive.read(info)
                try:
                    with Image.open(BytesIO(payload)) as image:
                        if image.format != "JPEG":
                            raise ValueError("Archive entry is not a JPEG image.")
                        _assert_jpeg_scan_is_complete(image)
                        image.load()
                except (UnidentifiedImageError, OSError, ValueError) as exc:
                    raise RuntimeError("PDF to JPG archive contains invalid JPEG data.") from exc
    except zipfile.BadZipFile as exc:
        raise RuntimeError("PDF to JPG output is not a valid ZIP archive.") from exc


def _validate_pdfa_file(path: Path) -> None:
    _validate_pdf_file(path)
    with fitz.open(path) as pdf:
        xmp_xref = pdf.xref_xml_metadata()
        if not xmp_xref:
            raise RuntimeError("PDF/A output is missing XMP conformance metadata.")
        try:
            metadata = ElementTree.fromstring(pdf.xref_stream(xmp_xref))
        except ElementTree.ParseError as exc:
            raise RuntimeError("PDF/A output has invalid XMP conformance metadata.") from exc
        # XMP properties may be RDF attributes or child elements, and XML prefixes
        # are arbitrary. Checking literal 'pdfaid:' strings rejects valid metadata
        # and can mistake unrelated text for an archival conformance declaration.
        identification_ns = "http://www.aiim.org/pdfa/ns/id/"
        declarations: dict[str, set[str]] = {"part": set(), "conformance": set()}
        for element in metadata.iter():
            for name in declarations:
                qualified = f"{{{identification_ns}}}{name}"
                if qualified in element.attrib:
                    declarations[name].add(element.attrib[qualified].strip().upper())
                if element.tag == qualified:
                    declarations[name].add((element.text or "").strip().upper())
        if declarations["part"] != {"2"} or declarations["conformance"] != {"B"}:
            raise RuntimeError("PDF/A output does not declare PDF/A-2b conformance.")
        catalog = pdf.xref_object(pdf.pdf_catalog(), compressed=False).casefold()
        if "/outputintents" not in catalog:
            raise RuntimeError("PDF/A output is missing an OutputIntent color profile.")

    _validate_pdfa_with_verapdf(path)


def _validate_pdfa_with_verapdf(path: Path) -> None:
    """Validate PDF/A-2b with the independent veraPDF validator.

    Modes:
    - required (production default): fail closed if veraPDF is missing, cannot run,
      or reports non-conformance.
    - auto: validate when veraPDF is installed; otherwise retain the built-in
      structural checks. Intended only for non-production environments.
    - off: disable the external validator (development/testing only).
    """

    mode = DEFAULT_PDFA_VALIDATOR_MODE
    if mode not in {"auto", "required", "off"}:
        raise RuntimeError(
            "REDOCX_PDFA_VALIDATOR_MODE must be one of: auto, required, off."
        )
    if mode == "off":
        return

    validator = (
        os.getenv("VERAPDF_PATH")
        or shutil.which("verapdf")
        or shutil.which("verapdf.bat")
    )
    if not validator:
        if mode == "required":
            raise RuntimeError(
                "veraPDF is required for production PDF/A validation but was not found. "
                "Install veraPDF or set VERAPDF_PATH."
            )
        return

    try:
        result = subprocess.run(
            [validator, "--loglevel", "1", "--format", "raw", "-f", "2b", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=DEFAULT_PDFA_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("veraPDF timed out while validating PDF/A-2b output.") from exc
    except OSError as exc:
        # Auto mode may tolerate an absent optional validator, but a configured
        # validator that fails to execute is not evidence of conformance.
        raise RuntimeError("veraPDF could not be executed.") from exc

    report = result.stdout or ""
    compliant = False
    if result.returncode == 0 and report.strip():
        try:
            root = ElementTree.fromstring(report)
            verdicts = [
                str(element.attrib.get("isCompliant", "")).strip().casefold()
                for element in root.iter()
                # veraPDF --format raw uses validationResult; XML reports use
                # validationReport. Both carry the authoritative verdict.
                if element.tag.rsplit("}", 1)[-1] in {"validationResult", "validationReport"}
            ]
            compliant = bool(verdicts) and all(verdict == "true" for verdict in verdicts)
        except ElementTree.ParseError:
            # A malformed or truncated report is not independent evidence of
            # conformance, even if a true verdict occurs somewhere in its text.
            compliant = False

    if not compliant:
        raise RuntimeError(
            "PDF/A conversion failed independent veraPDF PDF/A-2b validation. "
            "ReDOCX refused to return a non-conformant archival PDF."
        )


def _prepare_pdfa_uri_link_flags(source_path: Path, staged_path: Path) -> Path:
    """Normalize existing URI annotation flags on a temporary PDF copy.

    Ghostscript drops non-printing links while producing PDF/A. URI annotations
    may legitimately omit /F in an ordinary PDF, so preserve their existing
    rectangles/actions and normalize only the archival visibility flags. No new
    actions, links or attachments are copied into the archival output here.
    """
    changed = False
    with fitz.open(source_path) as source:
        for page in source:
            for link in page.get_links():
                if link.get("kind") != fitz.LINK_URI or not link.get("uri"):
                    continue
                xref = int(link.get("xref") or 0)
                if xref <= 0:
                    raise RuntimeError("PDF/A conversion cannot preserve a source URI annotation.")
                kind, value = source.xref_get_key(xref, "F")
                flags = int(value) if kind == "int" else 0
                # PDF annotation bits: Print=4, Invisible=1, Hidden=2, NoView=32.
                archival_flags = (flags | 4) & ~(1 | 2 | 32)
                if archival_flags != flags:
                    source.xref_set_key(xref, "F", str(archival_flags))
                    changed = True
        if changed:
            source.save(staged_path, garbage=4, deflate=True)
    return staged_path if changed else source_path


def _resolve_pdfa_icc_profile() -> Path:
    configured = os.getenv("REDOCX_PDFA_ICC_PROFILE", "").strip()
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path("/usr/share/color/icc/ghostscript/srgb.icc"),
        Path("/usr/share/color/icc/sRGB.icc"),
        Path("/usr/share/color/icc/colord/sRGB.icc"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()
    raise RuntimeError(
        "A valid sRGB ICC profile is required for PDF/A conversion. "
        "Set REDOCX_PDFA_ICC_PROFILE."
    )


def _resolve_pdfa_definition_file(gs_bin: str) -> Path:
    configured = os.getenv("REDOCX_PDFA_DEF_PS", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())
    try:
        version = subprocess.run(
            [gs_bin, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        version = ""
    if version:
        candidates.append(Path(f"/usr/share/ghostscript/{version}/lib/PDFA_def.ps"))
    candidates.extend(sorted(Path("/usr/share/ghostscript").glob("*/lib/PDFA_def.ps"), reverse=True))
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate.resolve()
    raise RuntimeError(
        "Ghostscript PDFA_def.ps was not found. Set REDOCX_PDFA_DEF_PS to its path."
    )

def _validate_docx_file(path: Path) -> None:
    required_parts = {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
    }
    expected_main_content_type = (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document.main+xml"
    )

    try:
        if not zipfile.is_zipfile(path):
            raise ValueError(f"File content is not an OOXML DOCX package: {path}")
        with zipfile.ZipFile(path) as package:
            names = set(package.namelist())
            missing = sorted(required_parts - names)
            if missing:
                raise ValueError(f"DOCX package is missing required parts: {', '.join(missing)}")

            content_types = ElementTree.fromstring(package.read("[Content_Types].xml"))
            main_types = {
                node.attrib.get("ContentType")
                for node in content_types
                if node.attrib.get("PartName") == "/word/document.xml"
            }
            if expected_main_content_type not in main_types:
                raise ValueError("OOXML package is not a standard .docx Word document.")

            ElementTree.fromstring(package.read("word/document.xml"))

        # python-docx exercises the OPC relationships and catches packages that
        # contain XML parts but cannot actually be opened as Word documents.
        Document(str(path))
    except (ValueError, zipfile.BadZipFile):
        raise
    except Exception as exc:
        raise ValueError(f"File is not a readable DOCX document: {path}") from exc


def _validate_image_file(path: Path, expected_format: str) -> None:
    expected_pillow_format = "JPEG" if expected_format in {"jpg", "jpeg"} else "PNG"
    try:
        with Image.open(path) as image:
            actual_format = (image.format or "").upper()
            width, height = image.size
            if actual_format == "JPEG":
                _assert_jpeg_scan_is_complete(image)
            image.verify()
        if actual_format != expected_pillow_format:
            raise ValueError(
                f"Image content is {actual_format or 'unknown'}, not {expected_pillow_format}."
            )
        if width < 1 or height < 1:
            raise ValueError("Image has invalid dimensions.")
        # JPEG verify() validates the container header but does not decode its
        # compressed scan data. Decode again to reject truncated uploads/outputs.
        with Image.open(path) as image:
            image.load()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"File is not a readable {expected_pillow_format} image: {path}") from exc


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        root = ElementTree.fromstring(package.read("word/document.xml"))
    text_tag = f"{{{WORDPROCESSINGML_NS}}}t"
    return "".join(node.text or "" for node in root.iter(text_tag))


def _normalized_alphanumeric_text(value: str) -> str:
    return "".join(character for character in value if character.isalnum())


def _pdf_native_text(path: Path) -> str:
    with fitz.open(path) as pdf:
        return "\n".join(page.get_text("text") for page in pdf)


def _pdf_docx_visible_lines(page: fitz.Page) -> list[dict[str, Any]]:
    lines = []
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        for line in block.get("lines", []):
            spans = [
                span for span in line["spans"]
                if span.get("text") and span.get("alpha", 255) and span.get("char_flags", 16) & 48
            ]
            if any(span["text"].strip() for span in spans):
                lines.append({**line, "spans": spans})
    return lines


def _pdf_docx_page_anchor(rect: fitz.Rect, shape_id: int, name: str, *, behind: bool) -> Any:
    # Word uses EMUs for the position and size of a DrawingML anchor.
    cx, cy = Pt(rect.width), Pt(rect.height)
    return parse_xml(f"""
        <wp:anchor {nsdecls('wp')} distT="0" distB="0" distL="0" distR="0"
                   simplePos="0" relativeHeight="{shape_id}" behindDoc="{int(behind)}"
                   locked="0" layoutInCell="1" allowOverlap="1">
          <wp:simplePos x="0" y="0"/>
          <wp:positionH relativeFrom="page"><wp:posOffset>{Pt(rect.x0)}</wp:posOffset></wp:positionH>
          <wp:positionV relativeFrom="page"><wp:posOffset>{Pt(rect.y0)}</wp:posOffset></wp:positionV>
          <wp:extent cx="{cx}" cy="{cy}"/>
          <wp:effectExtent l="0" t="0" r="0" b="0"/>
          <wp:wrapNone/>
          <wp:docPr id="{shape_id}" name="{name}"/>
          <wp:cNvGraphicFramePr/>
        </wp:anchor>
    """)


def _append_pdf_docx_anchor(paragraph: Any, anchor: Any, graphic: Any) -> None:
    anchor.append(graphic)
    paragraph.add_run()._r.add_drawing(anchor)


def _add_pdf_docx_page_picture(
    paragraph: Any,
    image: bytes,
    rect: fitz.Rect,
    shape_id: int,
    name: str,
    *,
    target: str | None = None,
) -> None:
    relationship, picture = paragraph.part.get_or_add_image(BytesIO(image))
    inline = CT_Inline.new_pic_inline(
        shape_id, relationship, picture.filename, Pt(rect.width), Pt(rect.height)
    )
    anchor = _pdf_docx_page_anchor(rect, shape_id, name, behind=target is None)
    if target:
        click = OxmlElement("a:hlinkClick")
        click.set(qn("r:id"), paragraph.part.relate_to(target, RT.HYPERLINK, is_external=True))
        anchor.find(qn("wp:docPr")).append(click)
    _append_pdf_docx_anchor(paragraph, anchor, inline.graphic)


def _add_pdf_docx_positioned_line(
    paragraph: Any,
    page_rect: fitz.Rect,
    line: Mapping[str, Any],
    font_families: Mapping[str, str],
    shape_id: int,
) -> None:
    spans = line["spans"]
    size = max(float(span["size"]) for span in spans)
    x, baseline = spans[0]["origin"]
    # Anchor the text to the PDF baseline and give Word room for font substitution.
    top = baseline - size * 0.90
    rect = fitz.Rect(x, top, max(x + 1, page_rect.x1), top + size * 1.7)
    anchor = _pdf_docx_page_anchor(rect, shape_id, "PDF editable text", behind=False)
    cx, cy = Pt(rect.width), Pt(rect.height)
    shape_ns = _PDF_DOCX_SHAPE_NS
    graphic = parse_xml(f"""
        <a:graphic {nsdecls('a', 'w')} xmlns:wps="{shape_ns}">
          <a:graphicData uri="{shape_ns}">
            <wps:wsp>
              <wps:cNvSpPr txBox="1"/>
              <wps:spPr>
                <a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                <a:noFill/><a:ln><a:noFill/></a:ln>
              </wps:spPr>
              <wps:txbx><w:txbxContent><w:p/></w:txbxContent></wps:txbx>
              <wps:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t">
                <a:noAutofit/>
              </wps:bodyPr>
            </wps:wsp>
          </a:graphicData>
        </a:graphic>
    """)
    text = Paragraph(next(graphic.iter(qn("w:p"))), paragraph._parent)
    text.paragraph_format.space_before = text.paragraph_format.space_after = Pt(0)
    text.paragraph_format.line_spacing = 1.0
    for span in spans:
        name = str(span["font"])
        family = font_families.get(name) or _pdf_pptx_font_family(name)
        _append_pdf_span(text, {**span, "font": family})
        run = text.runs[-1]
        run.font.size = Pt(round(float(span["size"]) * 2) / 2)
        properties = run._r.get_or_add_rPr()
        for attribute in ("w:eastAsia", "w:cs"):
            properties.rFonts.set(qn(attribute), family)
        if span["text"].isspace():
            # PDF extractors emit separate space runs for many justified lines.
            # Retain their measured width rather than collapsing to a normal space.
            nominal = {
                "Arial": 0.278, "Courier New": 0.60, "DejaVu Sans": 0.318,
            }.get(family, 0.25)
            width = fitz.Rect(span["bbox"]).width / len(span["text"])
            spacing = OxmlElement("w:spacing")
            spacing.set(qn("w:val"), str(round(20 * (width - nominal * float(span["size"])))))
            properties.insert_element_before(spacing, "w:sz", "w:szCs")
    _append_pdf_docx_anchor(paragraph, anchor, graphic)


def _pdf_docx_render_matrix(rect: fitz.Rect) -> fitz.Matrix:
    # Bound per-page memory for unusually large PDF canvases.
    dpi = max(72, DEFAULT_PDF_TO_DOCX_FIDELITY_DPI)
    scale = min(dpi / 72.0, 4600.0 / max(rect.width, rect.height),
                math.sqrt(12_000_000 / rect.get_area()))
    return fitz.Matrix(scale, scale)


def _pdf_docx_artwork(page: fitz.Page) -> bytes | None:
    """Render the artwork layer with the editable PDF text removed."""
    with fitz.open() as copy:
        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        art = copy[0]
        art.add_redact_annot(art.rect * art.derotation_matrix, fill=False, cross_out=False)
        art.apply_redactions(images=0, graphics=0, text=0)
        if _pdf_docx_visible_lines(art):
            raise PdfToDocxFidelityError(f"Could not separate the artwork and text on PDF page {page.number + 1}.")
        pixels = art.get_pixmap(matrix=_pdf_docx_render_matrix(page.rect), alpha=True)
        image = Image.frombytes("RGBA", (pixels.width, pixels.height), pixels.samples)
        if image.getbbox() is None:
            return None
        return pixels.tobytes("png")


def _write_positioned_pdf_docx(
    source_path: Path,
    output_path: Path,
    *,
    visual_pages: set[int],
    allow_visual: bool,
) -> set[int]:
    document = Document()
    visual = set(visual_pages)
    font_cache: dict[int, str] = {}
    shape_ids = count(1)
    hotspot = BytesIO()
    Image.new("RGBA", (2, 2), (255, 255, 255, 1)).save(hotspot, format="PNG")
    hotspot_bytes = hotspot.getvalue()
    with fitz.open(source_path) as source:
        for page in source:
            section = document.sections[0] if page.number == 0 else document.add_section(WD_SECTION.NEW_PAGE)
            _configure_section_for_fixed_pdf_page(section, page.rect)
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = Pt(1)
            if page.number not in visual:
                lines = _pdf_docx_visible_lines(page)
                unsupported = bool(lines and page.rotation) or any(
                    line.get("wmode") or abs(line["dir"][0] - 1.0) > 0.001 or abs(line["dir"][1]) > 0.001
                    or any(
                        span.get("bidi", 0) or span.get("alpha", 255) != 255
                        or not 0.5 <= float(span["size"]) <= 1638
                        or not page.rect.contains(fitz.Point(span["origin"]))
                        for span in line["spans"]
                    )
                    for line in lines
                )
                try:
                    if unsupported:
                        raise PdfToDocxFidelityError(f"PDF page {page.number + 1} uses unsupported text positioning.")
                    art = _pdf_docx_artwork(page) if lines else None
                except PdfToDocxFidelityError:
                    if not allow_visual:
                        raise
                    visual.add(page.number)
                else:
                    if art:
                        _add_pdf_docx_page_picture(paragraph, art, page.rect, next(shape_ids), "PDF artwork")
                    if lines:
                        families = _pdf_docx_font_families(page, font_cache)
                        for line in lines:
                            _add_pdf_docx_positioned_line(paragraph, page.rect, line, families, next(shape_ids))

            if page.number in visual:
                pixels = page.get_pixmap(matrix=_pdf_docx_render_matrix(page.rect), alpha=False)
                _add_pdf_docx_page_picture(
                    paragraph, pixels.tobytes("png"), page.rect, next(shape_ids), _PDF_DOCX_VISUAL_PAGE_NAME,
                )
                # Preserve search text, including best-effort OCR on scans.
                if page.get_bboxlog() and (text := _pdf_page_searchable_text(page)):
                    # A separate paragraph keeps Writer from hiding the image
                    # anchor when it suppresses a fully hidden paragraph.
                    search = document.add_paragraph()
                    search.paragraph_format.space_before = search.paragraph_format.space_after = Pt(0)
                    search.paragraph_format.line_spacing = Pt(1)
                    hidden = search.add_run(" ".join(text.split()))
                    hidden.font.hidden = True
                    hidden.font.size = Pt(1)

            # Preserve the source hit area as a native Word picture hyperlink.
            # Link export from Writer to PDF is not the authority for Word links.
            for rect, target in _pdf_docx_external_links(page):
                _add_pdf_docx_page_picture(
                    paragraph, hotspot_bytes, rect, next(shape_ids), "PDF hyperlink", target=target,
                )

        document.core_properties.keywords = (
            REDOCX_FIDELITY_DOCX_MARKER if len(visual) == source.page_count else
            REDOCX_MIXED_DOCX_MARKER if visual else REDOCX_POSITIONED_DOCX_MARKER
        )
    document.save(output_path)
    return visual


def _pdf_docx_layout_rejections(
    source_path: Path, rendered_path: Path, visual_pages: set[int]
) -> dict[int, str]:
    rejected: dict[int, str] = {}
    with fitz.open(source_path) as source, fitz.open(rendered_path) as rendered:
        if source.page_count != rendered.page_count:
            raise PdfToDocxFidelityError(
                f"PDF-to-Word changed the page count from {source.page_count} to {rendered.page_count}."
            )
        for number, (original, output) in enumerate(zip(source, rendered)):
            label = f"Rendered page {number + 1}"
            if (
                abs(original.rect.width - output.rect.width) > 0.5
                or abs(original.rect.height - output.rect.height) > 0.5
            ):
                rejected[number] = f"{label} changed the PDF page dimensions."
                continue
            original_image = _pdf_page_comparison_image(original)
            output_image = _pdf_page_comparison_image(
                output, width_px=original_image.width, target_height_px=original_image.height,
            )
            if number in visual_pages:
                if original.get_bboxlog() and not any(
                    (fitz.Rect(image["bbox"]) & output.rect).get_area() >= output.rect.get_area() * 0.995
                    for image in output.get_image_info()
                ):
                    rejected[number] = f"{label} lost the full-page PDF image."
                    continue
                # Compare mildly blurred page renders to tolerate a second rasterization.
                blur = ImageFilter.GaussianBlur(0.8)
                similarity = _page_grayscale_similarity(original_image.filter(blur), output_image.filter(blur))
                if similarity < 0.985:
                    rejected[number] = f"{label} changed the visual PDF page (similarity {similarity:.3f})."
                continue
            score = _page_ink_layout_score(original_image, output_image)
            if score < max(_PDF_DOCX_MIN_INK_SCORE, min(1.0, DEFAULT_PDF_TO_DOCX_VISUAL_MIN_SCORE)):
                rejected[number] = f"{label} changed the PDF layout (score {score:.3f})."
            elif not _pdf_docx_word_positions_match(original, output):
                rejected[number] = f"{label} moved or reflowed source words."
    return rejected


def _pdf_docx_word_positions_match(source: fitz.Page, rendered: fitz.Page) -> bool:
    expected = [word for word in source.get_text("words") if any(char.isalnum() for char in word[4])]
    if not expected:
        return _token_coverage(source.get_text("text"), rendered.get_text("text"))[0] == 1.0
    positions: dict[str, list[tuple[float, float]]] = {}
    for x, y, _, _, text, *_ in rendered.get_text("words"):
        positions.setdefault(unicodedata.normalize("NFKC", text), []).append((x, y))
    count_by_word = Counter(unicodedata.normalize("NFKC", word[4]) for word in expected)
    matched = 0
    for x, y, _, _, text, *_ in expected:
        word = unicodedata.normalize("NFKC", text)
        candidates = positions.get(word)
        if not candidates:
            continue
        nearest = min(range(len(candidates)), key=lambda i: abs(x - candidates[i][0]) + abs(y - candidates[i][1]))
        actual_x, actual_y = candidates.pop(nearest)
        # A single unique word wrapped to another line is significant on a letter.
        if count_by_word[word] == 1 and abs(y - actual_y) > 8:
            return False
        matched += abs(x - actual_x) <= 6 and abs(y - actual_y) <= 5
    return matched / len(expected) >= 0.99


def _write_editable_pdf_docx(source_path: Path, output_path: Path) -> list[list[fitz.Rect]]:
    document = Document()
    body = document.element.body
    graphics: list[list[fitz.Rect]] = []
    font_cache: dict[int, str] = {}

    with _PDF_TO_DOCX_LAYOUT_LOCK, fitz.open(source_path) as source:
        if source.page_count > MAX_PDF_TO_DOCX_PAGES:
            raise ValueError(f"Editable PDF-to-Word conversion is capped at {MAX_PDF_TO_DOCX_PAGES} pages.")
        converter = PDFToDOCXConverter(str(source_path))
        try:
            converter.parse(**{**converter.default_settings, "ignore_page_error": False})
            for page, layout in zip(source, converter.pages):
                if not layout.finalized:
                    raise PdfToDocxFidelityError(f"The layout of PDF page {page.number + 1} could not be reconstructed.")
                boxes = _pdf_docx_standalone_graphics(page)
                graphics.append(boxes)
                for box in boxes:
                    scale = min(2.0, 1600.0 / max(box.width, box.height))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=box, alpha=False, annots=False)
                    artwork = PDFToDOCXImageBlock({
                        "width": pix.width, "height": pix.height, "image": pix.tobytes("png"),
                    })
                    artwork.update_bbox(box)
                    artwork.set_float_image_block()
                    layout.float_images.append(artwork)

                previous = body[-2] if len(body) > 1 else None
                layout.make_docx(document)
                blocks = list(body.iterchildren() if previous is None else previous.itersiblings())
                # Give an empty source page a body paragraph so Writer retains it.
                if not any(
                    block.tag == qn("w:tbl") or (
                        block.tag == qn("w:p") and block.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None
                    )
                    for block in blocks
                ):
                    document.add_paragraph().paragraph_format.line_spacing = Pt(1)
                _repair_pdf_docx_page(page, blocks, font_cache)
        finally:
            converter.close()

    document.save(output_path)
    return graphics


def _pdf_docx_standalone_graphics(page: fitz.Page) -> list[fitz.Rect]:
    """Find standalone filled rectangles that pdf2docx can discard as unused shading."""
    words = [fitz.Rect(word[:4]) for word in page.get_text("words")]
    unrotated = page.rect * page.derotation_matrix
    graphics: list[fitz.Rect] = []
    for path in page.get_drawings():
        items = path["items"]
        fill = path.get("fill")
        rect = path["rect"]
        if (
            path["type"] not in {"f", "fs"}
            or len(items) != 1 or items[0][0] != "re"
            or not fill or min(fill) > 0.95 or not path.get("fill_opacity", 1)
            or min(rect.width, rect.height) < 4
            or rect.get_area() > unrotated.get_area() * 0.5 or not unrotated.contains(rect)
            or any((rect & word).get_area() > 0.25 for word in words)
        ):
            continue
        graphics.append(rect * page.rotation_matrix)
    return graphics


def _pdf_docx_compact_text(text: str) -> tuple[str, set[int]]:
    chars: list[str] = []
    breaks: set[int] = set()
    for char in text:
        if char.isspace():
            breaks.add(len(chars))
        else:
            chars.append(char)
    return "".join(chars), breaks


def _pdf_docx_font_families(page: fitz.Page, cache: dict[int, str]) -> dict[str, str]:
    families: dict[str, str] = {}
    for xref, extension, _kind, basename, *_ in page.get_fonts():
        name = re.sub(r"^[A-Z]{6}\+", "", basename)
        if xref not in cache:
            family = ""
            if extension in {"ttf", "otf"}:
                try:
                    _, _, _, data = page.parent.extract_font(xref)
                    with TTFont(BytesIO(data), lazy=True) as font:
                        family = font["name"].getBestFamilyName() or ""
                except Exception:
                    # Subset fonts can omit or damage the optional name table.
                    family = ""
            family = "".join(char for char in family if char.isprintable()).strip()
            fallback = name if " " in name else _pdf_pptx_font_family(name)
            cache[xref] = family or fallback.strip() or "Arial"
        families[name] = cache[xref]
    return families


def _repair_pdf_docx_page(
    page: fitz.Page, blocks: Iterable[Any], font_cache: dict[int, str]
) -> None:
    """Restore word boundaries and missing fonts from the corresponding PDF page."""
    source_parts: list[str] = []
    font_ends: list[int] = []
    source_fonts: list[str] = []
    char_count = 0
    flags = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES
    for block in page.get_text("dict", flags=flags)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                value = span["text"]
                source_parts.append(value)
                char_count += sum(not char.isspace() for char in value)
                font_ends.append(char_count)
                source_fonts.append(span["font"])
            source_parts.append("\n")
    reference, source_breaks = _pdf_docx_compact_text("".join(source_parts))
    families = _pdf_docx_font_families(page, font_cache)
    uniform_font = source_fonts[0] if len(set(source_fonts)) == 1 else ""

    paragraph_tag = qn("w:p")
    run_tag = qn("w:r")
    text_tag = qn("w:t")
    hyperlink_tag = qn("w:hyperlink")
    separator_tags = {qn("w:tab"), qn("w:br"), qn("w:cr")}
    for block in blocks:
        for table in block.iter(qn("w:tbl")):
            _restore_pdf_docx_table_grid(table)
        for paragraph in block.iter(paragraph_tag):
            _promote_pdf_docx_hyperlinks(paragraph)
            runs = [
                run
                for child in paragraph
                for run in (child.findall(run_tag) if child.tag == hyperlink_tag else (child,))
                if run.tag == run_tag
            ]
            nodes = [node for run in runs for node in run if node.tag == text_tag or node.tag in separator_tags]
            actual, actual_breaks = _pdf_docx_compact_text("".join(
                (node.text or "") if node.tag == text_tag else " " for node in nodes
            ))
            if not actual:
                continue
            # Only copy a boundary from a unique, otherwise identical source span.
            start = reference.find(actual)
            matched = start >= 0 and reference.find(actual, start + 1) < 0
            cursor = 0
            for run in runs:
                run_start = cursor
                for node in run.findall(text_tag):
                    old = node.text or ""
                    value: list[str] = []
                    for char in old:
                        if not char.isspace():
                            if matched and cursor and cursor not in actual_breaks and start + cursor in source_breaks:
                                value.append(" ")
                            cursor += 1
                        value.append(char)
                    if (new := "".join(value)) != old:
                        node.text = new
                        if new[:1].isspace() or new[-1:].isspace():
                            node.set(f"{{{XML_NS}}}space", "preserve")

                font = run.find(f"{qn('w:rPr')}/{qn('w:rFonts')}")
                if font is None or all(font.attrib.values()):
                    continue
                name = uniform_font
                if matched and cursor > run_start:
                    name = source_fonts[bisect_right(font_ends, start + run_start)]
                family = families.get(name) or (_pdf_pptx_font_family(name) if name else None)
                for attribute, value in list(font.attrib.items()):
                    if not value and family:
                        font.set(attribute, family)
                    elif not value:
                        del font.attrib[attribute]


def _restore_pdf_docx_table_grid(table: Any) -> None:
    # pdf2docx sets the cell widths but leaves an equal-width Word grid. Copy a
    # complete row's widths into the grid so chart labels stay under their bars.
    columns = table.tblGrid.gridCol_lst
    for row in table.tr_lst:
        cells = row.tc_lst
        if len(cells) == len(columns) and all(cell.grid_span == 1 and cell.width for cell in cells):
            for column, cell in zip(columns, cells):
                column.w = cell.width
            return


def _promote_pdf_docx_hyperlinks(paragraph: Any) -> None:
    """Move pdf2docx's run-wrapped hyperlinks to valid paragraph positions."""
    run_tag = qn("w:r")
    properties_tag = qn("w:rPr")
    hyperlink_tag = qn("w:hyperlink")
    for wrapper in paragraph.findall(run_tag):
        links = wrapper.findall(hyperlink_tag)
        if not links or any(child.tag not in {properties_tag, hyperlink_tag} for child in wrapper):
            continue
        properties = wrapper.find(properties_tag)
        for link in links:
            if properties is not None:
                for linked_run in link.findall(run_tag):
                    linked_properties = linked_run.get_or_add_rPr()
                    for prop in properties:
                        if linked_properties.find(prop.tag) is None:
                            linked_properties.append(deepcopy(prop))
            wrapper.addprevious(link)
        paragraph.remove(wrapper)


def _pdf_docx_rendered_link_key(target: str) -> str:
    # Writer may add the equivalent root path when exporting an HTTP(S) link.
    try:
        parts = urlsplit(target)
        if parts.scheme in {"http", "https"} and parts.netloc and not parts.path:
            target = parts._replace(path="/").geturl()
    except ValueError:
        pass
    return unquote(target)


def _assert_editable_pdf_docx_render(
    source_path: Path,
    rendered_path: Path,
    graphics: Sequence[Sequence[fitz.Rect]],
    source_links: Sequence[PdfHyperlink],
) -> None:
    minimum = max(0.70, min(1.0, MIN_PDF_TEXT_RETENTION_RATIO))
    links_by_page: dict[int, Counter[str]] = {}
    for link in source_links:
        links_by_page.setdefault(link.page_number, Counter())[_pdf_docx_rendered_link_key(link.target)] += 1

    with fitz.open(source_path) as source, fitz.open(rendered_path) as rendered:
        for number, (original, output, boxes) in enumerate(zip(source, rendered, graphics), start=1):
            expected_text = original.get_text("text")
            actual_text = output.get_text("text")
            ratio, matched, total = _token_coverage(expected_text, actual_text)
            expected_chars = len(_normalized_alphanumeric_text(expected_text))
            actual_chars = len(_normalized_alphanumeric_text(actual_text))
            if ratio + 1e-9 < minimum or (expected_chars and actual_chars < math.ceil(expected_chars * minimum)):
                raise PdfToDocxFidelityError(
                    f"Rendered page {number} failed text retention ({matched}/{total} source tokens)."
                )
            if expected_links := links_by_page.get(number):
                actual_links = Counter(
                    _pdf_docx_rendered_link_key(str(link["uri"])) for link in output.get_links() if link.get("uri")
                )
                if expected_links - actual_links:
                    raise PdfToDocxFidelityError(f"Rendered page {number} lost one or more external hyperlinks.")
            for box in boxes:
                inset = (box.width * 0.1, box.height * 0.1, -box.width * 0.1, -box.height * 0.1)
                source_box = box + inset
                output_box = source_box * original.rect.torect(output.rect)
                colors = []
                for page, clip in ((original, source_box), (output, output_box)):
                    scale = min(1.0, 64.0 / max(clip.width, clip.height))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, colorspace=fitz.csRGB, annots=False)
                    colors.append(ImageStat.Stat(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)).mean)
                expected_color, actual_color = colors
                contrast = sum(255 - value for value in expected_color) / 3
                difference = sum(abs(expected - actual) for expected, actual in zip(expected_color, actual_color)) / 3
                if difference > max(5.0, min(16.0, contrast * 0.30)):
                    raise PdfToDocxFidelityError(f"Rendered page {number} lost or displaced standalone graphics.")


def _pdf_page_searchable_text(page: fitz.Page) -> str:
    """Return native page text, with best-effort OCR for scanned pages.

    OCR is an enhancement for the fidelity DOCX text layer, not a prerequisite for
    visual conversion. A scanned PDF can still be converted faithfully when the OCR
    runtime is unavailable; in that case the page image remains authoritative.
    """

    native = page.get_text("text", sort=True).strip()
    if native:
        return native

    try:
        text_page = page.get_textpage_ocr(
            language=DEFAULT_PDF_TO_DOCX_OCR_LANGUAGE,
            dpi=max(96, DEFAULT_PDF_TO_DOCX_OCR_DPI),
            full=True,
        )
        return page.get_text("text", textpage=text_page, sort=True).strip()
    except Exception:
        return ""


def _pdf_docx_layout_marker(path: Path) -> str:
    with zipfile.ZipFile(path) as package:
        if "docProps/core.xml" not in package.namelist():
            return ""
        core = ElementTree.fromstring(package.read("docProps/core.xml"))
    keywords = core.findtext("{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}keywords")
    return keywords or ""


def _docx_is_redocx_fidelity_document(path: Path) -> bool:
    try:
        return _pdf_docx_layout_marker(path) == REDOCX_FIDELITY_DOCX_MARKER
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError):
        return False


def _validate_pdf_to_docx_editability(source_path: Path, docx_path: Path) -> None:
    source_chars = len(_normalized_alphanumeric_text(_pdf_native_text(source_path)))
    output_text = _docx_text(docx_path)
    output_chars = len(_normalized_alphanumeric_text(output_text))
    marker = _pdf_docx_layout_marker(docx_path)
    if marker in {REDOCX_FIDELITY_DOCX_MARKER, REDOCX_MIXED_DOCX_MARKER}:
        with fitz.open(source_path) as pdf:
            source_pages = pdf.page_count
        document = Document(str(docx_path))
        if len(document.sections) != source_pages:
            raise RuntimeError("PDF-to-Word changed the source page section count.")
        page_images = sum(
            child.get("name") == _PDF_DOCX_VISUAL_PAGE_NAME
            for child in document.element.body.iter(qn("wp:docPr"))
            if child.getparent().tag == qn("wp:anchor")
        )
        if marker == REDOCX_FIDELITY_DOCX_MARKER:
            # Accept earlier image-based DOCX packages during storage validation.
            if page_images != source_pages and len(document.inline_shapes) != source_pages:
                raise RuntimeError("PDF-to-Word is missing one or more full-page images.")
        elif not 0 < page_images < source_pages:
            raise RuntimeError("PDF-to-Word mixed layout has an invalid image page count.")
        if source_chars and output_chars < max(1, int(source_chars * 0.95)):
            raise RuntimeError("PDF-to-Word did not retain the searchable source text layer.")
        return

    if marker == REDOCX_POSITIONED_DOCX_MARKER and output_text.strip() and not source_chars:
        return

    if source_chars:
        minimum_retained = max(
            1,
            min(
                source_chars,
                max(
                    MIN_EDITABLE_DOCX_TEXT_CHARS,
                    int(source_chars * max(0.0, MIN_PDF_TEXT_RETENTION_RATIO)),
                ),
            ),
        )
    else:
        minimum_retained = max(1, MIN_EDITABLE_DOCX_TEXT_CHARS)

    if output_chars < minimum_retained:
        raise RuntimeError(
            "PDF-to-Word conversion did not retain enough editable text "
            f"(found {output_chars} editable characters; required at least {minimum_retained}). "
            "ReDOCX refused to return an image-only or mislabeled DOCX."
        )


def _pdf_docx_external_links(page: fitz.Page) -> Iterable[tuple[fitz.Rect, str]]:
    for link in page.get_links():
        target = str(link.get("uri") or "").strip()
        bounds = link.get("from")
        if target and bounds is not None:
            rect = fitz.Rect(bounds) & page.rect
            if not rect.is_empty:
                yield rect, target


def _extract_pdf_hyperlinks(path: Path) -> list[PdfHyperlink]:
    links: list[PdfHyperlink] = []
    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            for rect, target in _pdf_docx_external_links(page):
                words = [
                    str(word[4]).strip()
                    for word in page.get_text("words", clip=rect, sort=True)
                    if len(word) > 4 and str(word[4]).strip()
                ]
                label = " ".join(word for word in words if not _is_link_separator(word)).strip()
                if not label:
                    label = " ".join(page.get_textbox(rect).split()).strip()
                links.append(PdfHyperlink(
                    page_number=page_number,
                    label=label or _display_text_from_link_target(target),
                    target=target,
                ))
    return links


def _is_link_separator(value: str) -> bool:
    return not any(character.isalnum() for character in value) and "@" not in value


def _display_text_from_link_target(target: str) -> str:
    decoded = unquote(target).strip()
    if decoded.lower().startswith("mailto:"):
        decoded = decoded[7:].split("?", 1)[0]
        decoded = decoded.split("|", 1)[0].strip()
    return decoded or target


def _link_search_labels(link: PdfHyperlink) -> list[str]:
    candidates = [link.label, _display_text_from_link_target(link.target)]
    if link.target.lower().startswith("mailto:"):
        candidates.append(unquote(link.target[7:]).split("?", 1)[0].split("|", 1)[0].strip())

    unique: list[str] = []
    for candidate in candidates:
        normalized = " ".join(str(candidate or "").split()).strip(" :|\t\r\n")
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _docx_external_hyperlink_targets(path: Path) -> Counter[str]:
    with zipfile.ZipFile(path) as package:
        document_root = ElementTree.fromstring(package.read("word/document.xml"))
        relationships_path = "word/_rels/document.xml.rels"
        if relationships_path not in package.namelist():
            return Counter()
        relationships_root = ElementTree.fromstring(package.read(relationships_path))

    rel_id_attr = "Id"
    rel_type_attr = "Type"
    rel_target_attr = "Target"
    rel_mode_attr = "TargetMode"
    relationship_by_id: dict[str, str] = {}
    for relationship in relationships_root:
        if not relationship.attrib.get(rel_type_attr, "").endswith("/hyperlink"):
            continue
        if relationship.attrib.get(rel_mode_attr) != "External":
            continue
        relationship_by_id[relationship.attrib.get(rel_id_attr, "")] = relationship.attrib.get(
            rel_target_attr,
            "",
        )

    relationship_id_attr = qn("r:id")
    targets: Counter[str] = Counter()
    for paragraph in document_root.iter(qn("w:p")):
        for hyperlink in paragraph.findall(qn("w:hyperlink")):
            target = relationship_by_id.get(hyperlink.attrib.get(relationship_id_attr, ""))
            if target and any(node.text for node in hyperlink.iter(qn("w:t"))):
                targets[target] += 1
    for anchor in document_root.iter(qn("wp:anchor")):
        click = anchor.find(f"{qn('wp:docPr')}/{qn('a:hlinkClick')}")
        extent = anchor.find(qn("wp:extent"))
        picture = anchor.find(f".//{qn('pic:pic')}")
        if click is None or extent is None or picture is None:
            continue
        target = relationship_by_id.get(click.attrib.get(relationship_id_attr, ""))
        if target and int(extent.get("cx", 0)) > 0 and int(extent.get("cy", 0)) > 0:
            targets[target] += 1
    return targets


def _assert_pdf_hyperlinks_preserved(
    docx_path: Path,
    source_links: Sequence[PdfHyperlink],
) -> None:
    expected = Counter(link.target for link in source_links if link.target)
    if not expected:
        return
    actual = _docx_external_hyperlink_targets(docx_path)
    missing: list[str] = []
    for target, expected_count in expected.items():
        missing_count = expected_count - actual.get(target, 0)
        missing.extend([target] * max(0, missing_count))
    if missing:
        raise RuntimeError(
            "PDF-to-Word conversion could not preserve every external hyperlink: "
            + ", ".join(missing)
        )


def _apply_pdf_hyperlinks(docx_path: Path, source_links: Sequence[PdfHyperlink]) -> None:
    if not source_links:
        return

    document = Document(str(docx_path))
    existing = _docx_external_hyperlink_targets(docx_path)
    consumed_existing: Counter[str] = Counter()
    paragraph_tag = qn("w:p")
    paragraphs = list(document.element.body.iter(paragraph_tag))

    changed = False
    for link in source_links:
        if consumed_existing[link.target] < existing.get(link.target, 0):
            consumed_existing[link.target] += 1
            continue

        linked = False
        for label in _link_search_labels(link):
            for paragraph_element in paragraphs:
                if _wrap_text_range_with_hyperlink(
                    document,
                    paragraph_element,
                    label=label,
                    target=link.target,
                ):
                    linked = True
                    changed = True
                    break
            if linked:
                break

        if not linked:
            raise RuntimeError(
                f"Could not map PDF hyperlink label {link.label!r} to editable Word text."
            )

    if changed:
        document.save(docx_path)


def _run_visible_text(run_element: Any) -> str:
    text_tag = qn("w:t")
    tab_tag = qn("w:tab")
    break_tag = qn("w:br")
    chunks: list[str] = []
    for child in run_element:
        if child.tag == text_tag:
            chunks.append(child.text or "")
        elif child.tag == tab_tag:
            chunks.append("\t")
        elif child.tag == break_tag:
            chunks.append("\n")
    return "".join(chunks)


def _set_run_visible_text(run_element: Any, text: str) -> None:
    run_properties_tag = qn("w:rPr")
    for child in list(run_element):
        if child.tag != run_properties_tag:
            run_element.remove(child)

    text_element = OxmlElement("w:t")
    if text[:1].isspace() or text[-1:].isspace():
        text_element.set(f"{{{XML_NS}}}space", "preserve")
    text_element.text = text
    run_element.append(text_element)


def _clone_run_with_text(run_element: Any, text: str, *, hyperlink_style: bool = False) -> Any:
    cloned = deepcopy(run_element)
    _set_run_visible_text(cloned, text)
    if hyperlink_style:
        _style_hyperlink_run(cloned)
    return cloned


def _style_hyperlink_run(run_element: Any) -> None:
    run_properties = run_element.find(qn("w:rPr"))
    if run_properties is None:
        run_properties = OxmlElement("w:rPr")
        run_element.insert(0, run_properties)

    for tag_name in ("w:rStyle", "w:color", "w:u"):
        for existing in list(run_properties.findall(qn(tag_name))):
            run_properties.remove(existing)

    run_style = OxmlElement("w:rStyle")
    run_style.set(qn("w:val"), "Hyperlink")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    run_properties.extend((run_style, color, underline))


def _wrap_text_range_with_hyperlink(
    document: Any,
    paragraph_element: Any,
    *,
    label: str,
    target: str,
) -> bool:
    # Conversion engines used by ReDOCX emit ordinary body runs. Existing
    # hyperlink runs are excluded so repeated labels map to distinct occurrences.
    run_tag = qn("w:r")
    hyperlink_tag = qn("w:hyperlink")
    runs = [child for child in paragraph_element if child.tag == run_tag]
    if not runs:
        return False

    run_texts = [_run_visible_text(run) for run in runs]
    combined = "".join(run_texts)
    tokens = re.findall(r"\S+", label)
    if not tokens:
        return False
    pattern = re.compile(r"\s+".join(re.escape(token) for token in tokens), re.IGNORECASE)
    match = pattern.search(combined)
    if match is None:
        return False

    start_offset, end_offset = match.span()
    ranges: list[tuple[Any, str, int, int]] = []
    cursor = 0
    for run, run_text in zip(runs, run_texts):
        next_cursor = cursor + len(run_text)
        if next_cursor > start_offset and cursor < end_offset:
            ranges.append((run, run_text, cursor, next_cursor))
        cursor = next_cursor
    if not ranges:
        return False

    first_run, first_text, first_start, _ = ranges[0]
    last_run, last_text, last_start, _ = ranges[-1]
    insertion_index = paragraph_element.index(first_run)
    replacement_nodes: list[Any] = []

    before = first_text[: max(0, start_offset - first_start)]
    if before:
        replacement_nodes.append(_clone_run_with_text(first_run, before))

    relationship_id = document.part.relate_to(target, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    hyperlink.set(qn("w:history"), "1")

    for run, run_text, run_start, run_end in ranges:
        selected_start = max(start_offset, run_start) - run_start
        selected_end = min(end_offset, run_end) - run_start
        selected = run_text[selected_start:selected_end]
        if selected:
            hyperlink.append(_clone_run_with_text(run, selected, hyperlink_style=True))
    replacement_nodes.append(hyperlink)

    after = last_text[max(0, end_offset - last_start) :]
    if after:
        replacement_nodes.append(_clone_run_with_text(last_run, after))

    for run, _run_text, _run_start, _run_end in ranges:
        paragraph_element.remove(run)
    for offset, node in enumerate(replacement_nodes):
        paragraph_element.insert(insertion_index + offset, node)

    return any(child.tag == hyperlink_tag for child in replacement_nodes)


def _pdf_dict_text_char_count(page_dict: dict[str, Any]) -> int:
    return sum(
        len(_normalized_alphanumeric_text(str(span.get("text", ""))))
        for block in page_dict.get("blocks", [])
        if block.get("type") == 0
        for line in block.get("lines", [])
        for span in line.get("spans", [])
    )


def _rect_tuple(value: Sequence[float]) -> tuple[float, float, float, float]:
    return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))


def _rect_union(rectangles: Iterable[Sequence[float]]) -> tuple[float, float, float, float]:
    values = [_rect_tuple(rectangle) for rectangle in rectangles]
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(rectangle[0] for rectangle in values),
        min(rectangle[1] for rectangle in values),
        max(rectangle[2] for rectangle in values),
        max(rectangle[3] for rectangle in values),
    )


def _rect_intersection_area(first: Sequence[float], second: Sequence[float]) -> float:
    x0 = max(float(first[0]), float(second[0]))
    y0 = max(float(first[1]), float(second[1]))
    x1 = min(float(first[2]), float(second[2]))
    y1 = min(float(first[3]), float(second[3]))
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _rect_area(rectangle: Sequence[float]) -> float:
    return max(0.0, float(rectangle[2]) - float(rectangle[0])) * max(
        0.0,
        float(rectangle[3]) - float(rectangle[1]),
    )


def _mostly_inside_any(rectangle: Sequence[float], containers: Sequence[Sequence[float]]) -> bool:
    area = _rect_area(rectangle)
    if area <= 0:
        return False
    return any(_rect_intersection_area(rectangle, container) / area >= 0.55 for container in containers)


def _safe_find_pdf_tables(page: fitz.Page) -> list[Any]:
    try:
        finder = page.find_tables()
        return list(getattr(finder, "tables", []) or [])
    except Exception:
        # Table detection is an enhancement. Native text remains available if a
        # particular PDF drawing pattern is unsupported by PyMuPDF's detector.
        return []


def _build_pdf_page_elements(
    page: fitz.Page,
    *,
    page_dict: dict[str, Any],
    native_page_dict: dict[str, Any],
) -> tuple[list[tuple[str, tuple[float, float, float, float], Any]], tuple[float, float, float, float]]:
    tables = _safe_find_pdf_tables(page)
    table_rectangles = [_rect_tuple(table.bbox) for table in tables]

    raw_lines: list[PdfTextLine] = []
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            bbox = _rect_tuple(line.get("bbox") or block.get("bbox") or (0, 0, 0, 0))
            if _mostly_inside_any(bbox, table_rectangles):
                continue
            spans = tuple(
                dict(span)
                for span in line.get("spans", [])
                if str(span.get("text", ""))
            )
            combined_text = "".join(str(span.get("text", "")) for span in spans)
            has_text_or_marker = bool(
                _normalized_alphanumeric_text(combined_text)
                or any(marker in combined_text for marker in ("\uf0b7", "\uf0a7", "•", "●", "▪"))
            )
            if spans and has_text_or_marker:
                raw_lines.append(PdfTextLine(bbox=bbox, spans=spans))

    visual_lines = _merge_parallel_pdf_lines(raw_lines)
    text_groups = _group_pdf_lines_into_paragraphs(visual_lines)

    elements: list[tuple[str, tuple[float, float, float, float], Any]] = [
        ("text", group.bbox, group) for group in text_groups
    ]
    elements.extend(("table", _rect_tuple(table.bbox), table) for table in tables)

    # Keep native embedded images (logos, signatures, photos) as individual Word
    # images. This is materially different from the removed full-page screenshot
    # fallback: all source text remains real editable Word text.
    for block in native_page_dict.get("blocks", []):
        if block.get("type") != 1 or not block.get("image"):
            continue
        bbox = _rect_tuple(block.get("bbox") or (0, 0, 0, 0))
        if _mostly_inside_any(bbox, table_rectangles):
            continue
        elements.append(("image", bbox, block))

    elements.sort(key=lambda item: (round(item[1][1], 1), item[1][0], item[0] != "image"))
    content_rectangles = [bbox for _kind, bbox, _payload in elements if _rect_area(bbox) > 0]
    content_bbox = _rect_union(content_rectangles) if content_rectangles else _rect_tuple(page.rect)
    return elements, content_bbox


def _merge_parallel_pdf_lines(lines: Sequence[PdfTextLine]) -> list[PdfTextLine]:
    merged: list[PdfTextLine] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        if not merged:
            merged.append(line)
            continue

        previous = merged[-1]
        previous_center = (previous.bbox[1] + previous.bbox[3]) / 2.0
        current_center = (line.bbox[1] + line.bbox[3]) / 2.0
        height = max(1.0, min(previous.bbox[3] - previous.bbox[1], line.bbox[3] - line.bbox[1]))
        same_visual_row = abs(previous_center - current_center) <= max(2.5, height * 0.35)

        if same_visual_row:
            spans = tuple(
                sorted(
                    (*previous.spans, *line.spans),
                    key=lambda span: float((span.get("bbox") or (0, 0, 0, 0))[0]),
                )
            )
            merged[-1] = PdfTextLine(
                bbox=_rect_union((previous.bbox, line.bbox)),
                spans=spans,
            )
        else:
            merged.append(line)
    return merged


def _line_dominant_size(line: PdfTextLine) -> float:
    sizes = [float(span.get("size") or 10.0) for span in line.spans]
    return max(sizes) if sizes else 10.0


def _line_is_bold(line: PdfTextLine) -> bool:
    weighted_total = 0
    weighted_bold = 0
    for span in line.spans:
        weight = max(1, len(str(span.get("text", ""))))
        weighted_total += weight
        if int(span.get("flags") or 0) & 16 or "bold" in str(span.get("font", "")).lower():
            weighted_bold += weight
    return bool(weighted_total and weighted_bold / weighted_total >= 0.5)


def _looks_like_heading(value: str) -> bool:
    letters = [character for character in value if character.isalpha()]
    return bool(letters and len(value.strip()) <= 90 and all(character.isupper() for character in letters))


def _starts_with_bullet(value: str) -> bool:
    return value.lstrip().startswith(("\uf0b7", "•", "●", "▪", "- "))


def _starts_new_pdf_paragraph(group: Sequence[PdfTextLine], current: PdfTextLine) -> bool:
    previous = group[-1]
    gap = current.bbox[1] - previous.bbox[3]
    previous_size = _line_dominant_size(previous)
    current_size = _line_dominant_size(current)
    current_text = current.text.strip()
    previous_text = previous.text.strip()

    if gap > max(4.0, min(previous_size, current_size) * 0.45):
        return True
    if abs(current_size - previous_size) > 0.9:
        return True
    if _line_is_bold(current) != _line_is_bold(previous) and (
        _looks_like_heading(current_text) or _looks_like_heading(previous_text)
    ):
        return True
    if _looks_like_heading(current_text) or _looks_like_heading(previous_text):
        return True

    group_left = group[0].bbox[0]
    left_delta = current.bbox[0] - previous.bbox[0]
    if left_delta < -8.0:
        return True
    if left_delta > 14.0 and not _starts_with_bullet(group[0].text):
        return True
    if _starts_with_bullet(current_text) and not _starts_with_bullet(previous_text):
        return True
    if abs(current.bbox[0] - group_left) > 24.0 and not _starts_with_bullet(group[0].text):
        return True
    return False


def _group_pdf_lines_into_paragraphs(lines: Sequence[PdfTextLine]) -> list[PdfTextGroup]:
    groups: list[list[PdfTextLine]] = []
    for line in lines:
        if not groups or _starts_new_pdf_paragraph(groups[-1], line):
            groups.append([line])
        else:
            groups[-1].append(line)
    return [
        PdfTextGroup(
            bbox=_rect_union(line.bbox for line in group),
            lines=tuple(group),
        )
        for group in groups
    ]


def _configure_section_for_editable_pdf_page(
    section: Any,
    page_rect: fitz.Rect,
    content_bbox: Sequence[float],
) -> None:
    page_width = float(page_rect.width)
    page_height = float(page_rect.height)
    section.page_width = Pt(page_width)
    section.page_height = Pt(page_height)

    left = min(90.0, max(18.0, float(content_bbox[0])))
    top = min(90.0, max(18.0, float(content_bbox[1])))
    right = min(90.0, max(18.0, page_width - float(content_bbox[2])))
    # The last glyph's y-position is not a semantic bottom margin. Keeping a
    # compact editing margin prevents small Word font-metric differences from
    # spilling a source page just before the explicit section break.
    bottom = 18.0
    section.left_margin = Pt(left)
    section.right_margin = Pt(right)
    section.top_margin = Pt(top)
    section.bottom_margin = Pt(bottom)
    section.header_distance = Pt(0)
    section.footer_distance = Pt(0)


def _section_margin_points(section: Any, name: str) -> float:
    value = getattr(section, name, None)
    return float(getattr(value, "pt", 0.0) or 0.0)


def _should_keep_pdf_line_break(group: PdfTextGroup, line_index: int) -> bool:
    if line_index <= 0:
        return False
    previous_text = group.lines[line_index - 1].text.strip()
    current_text = group.lines[line_index].text.strip()
    if not previous_text or not current_text:
        return True
    # Addresses/signatures are intentionally line-oriented; ordinary wrapped prose
    # should reflow naturally when edited in Word.
    compact_line_block = (
        len(group.lines) >= 2
        and max(len(line.text.strip()) for line in group.lines) <= 60
    )
    return (
        compact_line_block
        or (
            len(previous_text) <= 45
            and len(current_text) <= 45
            and previous_text.endswith((",", ".", ":"))
        )
    )


def _normalized_pdf_span_text(value: str) -> str:
    return (
        str(value)
        .replace("\uf0b7", "•")
        .replace("\uf0a7", "▪")
        .replace("\u00a0", " ")
    )


def _append_pdf_span(paragraph: Any, span: dict[str, Any], *, prefix: str = "") -> None:
    source_text = str(span.get("text", ""))
    text = prefix + _normalized_pdf_span_text(source_text)
    if not text:
        return
    run = paragraph.add_run(text)
    font_name = str(span.get("font") or "").split("+")[-1].strip()
    if any(marker in source_text for marker in ("\uf0b7", "\uf0a7", "•", "●", "▪")):
        # Private-use bullet codepoints commonly come from Symbol/Wingdings PDF
        # fonts. After mapping them to Unicode, use a Unicode-capable Word font.
        font_name = "Arial"
    if font_name:
        run.font.name = font_name
    size = min(72.0, max(5.0, float(span.get("size") or 10.0)))
    run.font.size = Pt(size)
    flags = int(span.get("flags") or 0)
    font_name_lower = font_name.lower()
    run.bold = bool(flags & 16 or "bold" in font_name_lower)
    run.italic = bool(flags & 2 or "italic" in font_name_lower or "oblique" in font_name_lower)

    color_value = int(span.get("color") or 0)
    run.font.color.rgb = RGBColor(
        (color_value >> 16) & 0xFF,
        (color_value >> 8) & 0xFF,
        color_value & 0xFF,
    )


def _populate_pdf_text_paragraph(
    paragraph: Any,
    group: PdfTextGroup,
    *,
    page_rect: fitz.Rect,
    section: Any,
    previous_bottom: Optional[float],
) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    left_margin = _section_margin_points(section, "left_margin")
    fmt.left_indent = Pt(max(0.0, group.bbox[0] - left_margin))
    # PDF x1 is the end of the rendered glyphs, not a semantic paragraph edge.
    # Turning it into a Word right indent makes short address lines and headings
    # wrap unnecessarily and can add pages. Let normal paragraphs reflow across
    # the available text width.
    fmt.right_indent = Pt(0)

    if previous_bottom is None:
        fmt.space_before = Pt(max(0.0, group.bbox[1] - _section_margin_points(section, "top_margin")))
    else:
        fmt.space_before = Pt(min(18.0, max(0.0, group.bbox[1] - previous_bottom)))

    group_width = group.bbox[2] - group.bbox[0]
    group_center = (group.bbox[0] + group.bbox[2]) / 2.0
    group_text = group.text.strip()
    if (
        group_width < page_rect.width * 0.55
        and len(group_text) <= 90
        and abs(group_center - page_rect.width / 2.0) < 18.0
    ):
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    previous_span_right: Optional[float] = None
    for line_index, line in enumerate(group.lines):
        if line_index:
            separator = "\n" if _should_keep_pdf_line_break(group, line_index) else " "
            paragraph.add_run(separator)
        for span in line.spans:
            span_bbox = span.get("bbox") or (0, 0, 0, 0)
            prefix = ""
            if previous_span_right is not None and float(span_bbox[0]) - previous_span_right > 18.0:
                prefix = "\t"
            _append_pdf_span(paragraph, span, prefix=prefix)
            previous_span_right = float(span_bbox[2])
        previous_span_right = None


def _add_pdf_table_to_docx(document: Any, pdf_table: Any, *, previous_bottom: Optional[float]) -> None:
    data = list(pdf_table.extract() or [])
    if not data:
        return
    column_count = max(len(row or []) for row in data)
    if column_count < 1:
        return

    if previous_bottom is not None:
        spacer = document.add_paragraph()
        spacer.paragraph_format.space_before = Pt(
            min(12.0, max(0.0, float(pdf_table.bbox[1]) - previous_bottom))
        )
        spacer.paragraph_format.space_after = Pt(0)

    table = document.add_table(rows=len(data), cols=column_count)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = True

    for row_index, source_row in enumerate(data):
        source_row = list(source_row or [])
        for column_index in range(column_count):
            value = source_row[column_index] if column_index < len(source_row) else ""
            cell = table.cell(row_index, column_index)
            cell.text = str(value or "").strip()
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = Pt(9.0)
                    if row_index == 0:
                        run.bold = True


def _add_pdf_image_to_docx(
    document: Any,
    image_block: dict[str, Any],
    *,
    bbox: Sequence[float],
    section: Any,
    previous_bottom: Optional[float],
) -> None:
    image_bytes = image_block.get("image")
    if not image_bytes:
        return
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(0)
    if previous_bottom is not None:
        paragraph.paragraph_format.space_before = Pt(
            min(12.0, max(0.0, float(bbox[1]) - previous_bottom))
        )

    available_width = (
        float(section.page_width.pt)
        - _section_margin_points(section, "left_margin")
        - _section_margin_points(section, "right_margin")
    )
    width = min(max(1.0, float(bbox[2]) - float(bbox[0])), max(1.0, available_width))
    aspect = max(0.01, (float(bbox[3]) - float(bbox[1])) / max(1.0, float(bbox[2]) - float(bbox[0])))
    paragraph.add_run().add_picture(BytesIO(image_bytes), width=Pt(width), height=Pt(width * aspect))


def _configure_section_for_fixed_pdf_page(section, page_rect: fitz.Rect) -> None:
    """Mirror one PDF page's physical size and orientation in a Word section."""

    width = max(1.0, float(page_rect.width))
    height = max(1.0, float(page_rect.height))
    section.orientation = WD_ORIENT.LANDSCAPE if width > height else WD_ORIENT.PORTRAIT
    section.page_width = Pt(width)
    section.page_height = Pt(height)
    section.top_margin = Pt(0)
    section.bottom_margin = Pt(0)
    section.left_margin = Pt(0)
    section.right_margin = Pt(0)
    section.header_distance = Pt(0)
    section.footer_distance = Pt(0)
    try:
        section.gutter = Pt(0)
    except (AttributeError, ValueError):
        pass


def _fit_image_to_section(
    section: Any,
    *,
    width_px: int,
    height_px: int,
) -> tuple[Pt, Pt]:
    """Fit an image inside a Word page without altering its aspect ratio."""

    width_px = max(1, int(width_px))
    height_px = max(1, int(height_px))
    available_width = max(1.0, float(section.page_width.pt))
    available_height = max(1.0, float(section.page_height.pt))
    for attr in ("left_margin", "right_margin"):
        value = getattr(section, attr, None)
        if value is not None:
            available_width -= max(0.0, float(value.pt))
    for attr in ("top_margin", "bottom_margin"):
        value = getattr(section, attr, None)
        if value is not None:
            available_height -= max(0.0, float(value.pt))
    available_width = max(1.0, available_width)
    available_height = max(1.0, available_height)

    aspect = width_px / height_px
    fitted_width = available_width
    fitted_height = fitted_width / aspect
    if fitted_height > available_height:
        fitted_height = available_height
        fitted_width = fitted_height * aspect
    return Pt(max(1.0, fitted_width)), Pt(max(1.0, fitted_height))


def _configure_section_for_image(section, width_px: int, height_px: int) -> None:
    # Fit common image documents onto an A4-equivalent portrait/landscape page while
    # preserving image aspect ratio and avoiding the old fixed 6-inch insertion.
    if width_px >= height_px:
        section.page_width = Inches(11.69)
        section.page_height = Inches(8.27)
    else:
        section.page_width = Inches(8.27)
        section.page_height = Inches(11.69)
    section.top_margin = Pt(0)
    section.bottom_margin = Pt(0)
    section.left_margin = Pt(0)
    section.right_margin = Pt(0)
    section.header_distance = Pt(0)
    section.footer_distance = Pt(0)


def _configure_full_page_image_paragraph(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.left_indent = Pt(0)
    fmt.right_indent = Pt(0)
    fmt.first_line_indent = Pt(0)
    fmt.line_spacing = 1


def _assert_jpeg_scan_is_complete(image: Image.Image) -> None:
    """Reject truncated scans even if another library enables Pillow tolerance."""

    # WeasyPrint enables LOAD_TRUNCATED_IMAGES globally during import. Avoid
    # changing that process-wide flag in concurrent requests. JPEG's parser leaves
    # fp at the start of the compressed scan, after metadata/embedded thumbnails;
    # an end marker from a thumbnail therefore cannot satisfy this check.
    stream = image.fp
    position = stream.tell()
    previous = b""
    try:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                raise ValueError("JPEG compressed scan is missing its end-of-image marker.")
            if b"\xff\xd9" in previous + chunk:
                return
            previous = chunk[-1:]
    finally:
        stream.seek(position)


def _document_image_payload(
    image: Image.Image, source_path: Path
) -> tuple[bytes, int, int, tuple[float, float]]:
    """Keep JPEG samples intact, normalizing EXIF orientation losslessly if needed."""

    if image.format == "JPEG":
        _assert_jpeg_scan_is_complete(image)
    image.load()
    orientation = image.getexif().get(274, 1)
    raw_dpi = image.info.get("dpi", (DEFAULT_IMAGE_PDF_DPI, DEFAULT_IMAGE_PDF_DPI))
    try:
        dpi = (float(raw_dpi[0]), float(raw_dpi[1]))
    except (TypeError, ValueError, IndexError):
        dpi = (DEFAULT_IMAGE_PDF_DPI, DEFAULT_IMAGE_PDF_DPI)
    if not all(math.isfinite(value) and 1.0 <= value <= 10000.0 for value in dpi):
        dpi = (DEFAULT_IMAGE_PDF_DPI, DEFAULT_IMAGE_PDF_DPI)
    if orientation in {5, 6, 7, 8}:
        dpi = (dpi[1], dpi[0])
    if (
        image.format == "JPEG"
        and image.mode in {"RGB", "L"}
        and orientation == 1
        and not image.info.get("icc_profile")
    ):
        return source_path.read_bytes(), image.width, image.height, dpi

    normalized = _flatten_image_to_rgb(ImageOps.exif_transpose(image))
    stream = BytesIO()
    normalized.save(stream, "PNG")
    return stream.getvalue(), normalized.width, normalized.height, dpi


def _flatten_image_to_rgb(image: Image.Image) -> Image.Image:
    profile_bytes = image.info.get("icc_profile")
    output_profile = None
    if profile_bytes:
        # Converting CMYK/LAB pixels with Image.convert() ignores the input ICC
        # profile. Normalize profiled colors to sRGB before embedding them, and
        # never attach a CMYK/gray profile to newly created RGB samples.
        if image.mode in {"RGB", "L", "CMYK", "LAB"}:
            color_image = image
        elif image.mode in {"1", "LA", "I", "I;16", "I;16B", "I;16L"}:
            color_image = image.convert("L")
        else:
            color_image = image.convert("RGB")
        try:
            output_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
            rgb = ImageCms.profileToProfile(
                color_image,
                ImageCms.ImageCmsProfile(BytesIO(profile_bytes)),
                output_profile,
                outputMode="RGB",
            )
        except (ImageCms.PyCMSError, OSError, TypeError, ValueError) as exc:
            raise ValueError("Image ICC color profile cannot be applied safely.") from exc
    else:
        rgb = image.convert("RGB")

    # PNG tRNS transparency is also valid for RGB and grayscale images; treating
    # only palette/alpha modes as transparent reveals pixels meant to be hidden.
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = rgb.convert("RGBA")
        rgba.putalpha(image.convert("RGBA").getchannel("A"))
        background = Image.new("RGBA", rgba.size, "WHITE")
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
    if output_profile is not None:
        rgb.info["icc_profile"] = output_profile.tobytes()
    else:
        rgb.info.pop("icc_profile", None)
    return rgb


__all__ = [
    "CONVERSION_RULES",
    "ALLOWED_CONVERSION_PAIRS",
    "ConversionArtifact",
    "ConversionBackend",
    "PdfToDocxFidelityError",
    "RealConversionBackend",
    "ConvertConfig",
    "ConvertProcessor",
    "ensure_allowed_conversion_pair",
    "plan_output_file_name",
    "build_conversion_instructions",
    "convert_document",
]
