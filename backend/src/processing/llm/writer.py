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

import hashlib
import os
import re
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from typing import Optional, Protocol

import fitz
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from fpdf import FPDF

from backend.src.storage.artifacts import (
    LocalArtifactStorage,
    StorageBackend,
    guess_content_type
)


SUPPORTED_OUTPUT_FORMATS = {"pdf", "docx"}

PDF_UNICODE_FONT_PATH_ENV = "PDF_UNICODE_FONT_PATH"
PDF_UNICODE_FONT_NAME = "UnicodeDocumentFont"
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_BUNDLED_FONT_DIR = _BACKEND_ROOT / "assets" / "fonts"
_SYSTEM_FONT_DIR = Path("/usr/local/share/fonts/redocx")


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
        language_tag: Optional[str] = None,
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
        language_tag: Optional[str] = None,
    ) -> WrittenArtifact:
        normalized_content = _normalize_content(content)
        normalized_output_format = _normalize_output_format(output_format)
        normalized_name = _normalize_file_name(planned_output_name)
        normalized_language_tag = _normalize_language_tag(language_tag)

        with TemporaryDirectory(prefix="writer-work-") as workdir:
            output_path = Path(workdir) / normalized_name

            if normalized_output_format == "docx":
                self._write_docx(
                    normalized_content,
                    output_path,
                    language_tag=normalized_language_tag,
                )
            elif normalized_output_format == "pdf":
                self._write_pdf(
                    normalized_content,
                    output_path,
                    language_tag=normalized_language_tag,
                )
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

    def _write_docx(
        self,
        content: str,
        output_path: Path,
        *,
        language_tag: Optional[str],
    ) -> None:
        document = Document()

        for paragraph_text in _split_paragraphs(content):
            paragraph = document.add_paragraph()
            run = paragraph.add_run(paragraph_text)
            font_family = _docx_font_family(
                paragraph_text,
                language_tag=language_tag,
            )
            _set_docx_run_font(run, font_family)

            if _is_rtl_text(paragraph_text):
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                _set_docx_paragraph_rtl(paragraph)
                _set_docx_run_rtl(run)

        document.save(output_path)

    def _write_pdf(
        self,
        content: str,
        output_path: Path,
        *,
        language_tag: Optional[str],
    ) -> None:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        font_paths = _select_document_font_paths(
            content,
            language_tag=language_tag,
        )
        font_names: list[str] = []
        try:
            for index, font_path in enumerate(font_paths):
                font_name = (
                    PDF_UNICODE_FONT_NAME
                    if index == 0
                    else f"{PDF_UNICODE_FONT_NAME}Fallback{index}"
                )
                pdf.add_font(font_name, "", str(font_path))
                font_names.append(font_name)
        except Exception as exc:
            raise RuntimeError(
                "A verified Unicode PDF font could not be loaded. "
                f"Check {PDF_UNICODE_FONT_PATH_ENV} and backend/assets/fonts."
            ) from exc

        pdf.set_font(font_names[0], size=11)
        if len(font_names) > 1:
            pdf.set_fallback_fonts(font_names[1:], exact_match=False)

        if _contains_arabic(content) or _contains_devanagari(content):
            try:
                # Let HarfBuzz infer direction, script, and language per fragment.
                # This keeps Arabic, Hindi, Latin identifiers, and equations correct
                # when they occur in the same paragraph.
                pdf.set_text_shaping(use_shaping_engine=True)
            except Exception as exc:
                raise RuntimeError(
                    "Arabic and Hindi PDF output requires the declared uharfbuzz "
                    "dependency for correct text shaping."
                ) from exc

        paragraphs = _split_paragraphs(content)
        line_height = 6
        for index, paragraph_text in enumerate(paragraphs):
            text = paragraph_text if paragraph_text.strip() else " "
            alignment = "R" if _is_rtl_text(text) else "L"
            pdf.multi_cell(
                0,
                line_height,
                text,
                align=alignment,
                new_x="LMARGIN",
                new_y="NEXT",
            )

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
        language_tag: Optional[str] = None,
    ) -> WrittenArtifact:
        normalized_content = _normalize_content(content)
        normalized_output_format = _normalize_output_format(output_format)
        normalized_name = _normalize_file_name(output_name)
        normalized_language_tag = _normalize_language_tag(language_tag)

        write_kwargs = {
            "content": normalized_content,
            "output_format": normalized_output_format,
            "planned_output_name": normalized_name,
        }
        if isinstance(self.backend, RealDocumentWriterBackend):
            write_kwargs["language_tag"] = normalized_language_tag

        return self.backend.write(
            **write_kwargs,
        )


def write_document(
    *,
    content: str,
    output_format: str,
    output_name: str,
    language_tag: Optional[str] = None,
    backend: Optional[DocumentWriterBackend] = None,
) -> WrittenArtifact:
    """Functional convenience wrapper for analyzer integration."""
    writer = DocumentWriter(backend=backend)
    return writer.write(
        content=content,
        output_format=output_format,
        output_name=output_name,
        language_tag=language_tag,
    )


def write_document_preserving_source_layout(
    *,
    content: str,
    source_file_path: str,
    output_format: str,
    output_name: str,
    storage_backend: Optional[StorageBackend] = None,
) -> WrittenArtifact:
    """Write a corrected PDF/DOCX by editing the source document in place.

    This path is intentionally reserved for conservative text transforms such as
    Grammar Correct. It preserves page geometry, paragraph placement, styles,
    images, annotations, and other source-document objects instead of rebuilding
    a generic document from extracted text.
    """
    normalized_content = _normalize_content(content)
    normalized_output_format = _normalize_output_format(output_format)
    normalized_name = _normalize_file_name(output_name)
    source_path = Path(source_file_path).expanduser()

    if not source_path.is_file():
        raise ValueError("The source document required for layout-preserving output is unavailable.")

    expected_suffix = f".{normalized_output_format}"
    if source_path.suffix.lower() != expected_suffix:
        raise ValueError(
            "Layout-preserving output requires the source and output formats to match."
        )

    storage = storage_backend or LocalArtifactStorage(base_dir="artifacts/ai_documents")

    with TemporaryDirectory(prefix="writer-layout-work-") as workdir:
        output_path = Path(workdir) / normalized_name
        if normalized_output_format == "pdf":
            _write_pdf_preserving_source_layout(
                source_path=source_path,
                corrected_content=normalized_content,
                output_path=output_path,
            )
        elif normalized_output_format == "docx":
            _write_docx_preserving_source_layout(
                source_path=source_path,
                corrected_content=normalized_content,
                output_path=output_path,
            )
        else:  # pragma: no cover - guarded by _normalize_output_format.
            raise ValueError(
                f"Unsupported layout-preserving output format: {normalized_output_format}"
            )

        if not output_path.is_file():
            raise RuntimeError(
                "Layout-preserving document writing completed without producing an output file."
            )

        stored = storage.persist(
            source_file_path=str(output_path),
            artifact_name=output_path.name,
            content_type=guess_content_type(str(output_path)),
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



def write_summary_preserving_source_layout(
    *,
    content: str,
    source_file_path: str,
    output_format: str,
    output_name: str,
    storage_backend: Optional[StorageBackend] = None,
) -> WrittenArtifact:
    """Write a summarized PDF/DOCX while retaining source layout and typography.

    Unlike Grammar Correct, summarization is intentionally compressive, so the
    summary cannot be mapped safely with the conservative near-identity text
    alignment used by ``write_document_preserving_source_layout``. This dedicated
    path keeps the same source document container, page geometry, paragraph
    placement, styles, images, annotations, and non-text objects, while allowing
    source text units to become shorter or empty.
    """
    normalized_content = _normalize_content(content)
    normalized_output_format = _normalize_output_format(output_format)
    normalized_name = _normalize_file_name(output_name)
    source_path = Path(source_file_path).expanduser()

    if not source_path.is_file():
        raise ValueError(
            "The source document required for layout-preserving summarization is unavailable."
        )

    expected_suffix = f".{normalized_output_format}"
    if source_path.suffix.lower() != expected_suffix:
        raise ValueError(
            "Layout-preserving summarization requires the source and output formats to match."
        )

    storage = storage_backend or LocalArtifactStorage(base_dir="artifacts/ai_documents")

    with TemporaryDirectory(prefix="writer-summary-layout-work-") as workdir:
        output_path = Path(workdir) / normalized_name
        if normalized_output_format == "pdf":
            _write_pdf_summary_preserving_source_layout(
                source_path=source_path,
                summarized_content=normalized_content,
                output_path=output_path,
            )
        elif normalized_output_format == "docx":
            _write_docx_summary_preserving_source_layout(
                source_path=source_path,
                summarized_content=normalized_content,
                output_path=output_path,
            )
        else:  # pragma: no cover - guarded by _normalize_output_format.
            raise ValueError(
                f"Unsupported layout-preserving summary format: {normalized_output_format}"
            )

        if not output_path.is_file():
            raise RuntimeError(
                "Layout-preserving summarization completed without producing an output file."
            )

        stored = storage.persist(
            source_file_path=str(output_path),
            artifact_name=output_path.name,
            content_type=guess_content_type(str(output_path)),
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

def _normalize_layout_candidate(content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    # PDF extraction inserts an additional newline between pages. Those blank
    # separators are page geometry, not editable text, so normalize them away
    # for source-to-candidate alignment.
    normalized = re.sub(r"\n[ \t]*\n+", "\n", normalized)
    return normalized


def _alignment_boundary_index(
    opcodes: list[tuple[str, int, int, int, int]],
    source_position: int,
    *,
    candidate_length: int,
) -> int:
    if source_position <= 0:
        return 0

    for tag, i1, i2, j1, j2 in opcodes:
        if i1 <= source_position <= i2:
            if tag == "equal":
                return min(candidate_length, j1 + (source_position - i1))
            if i2 == i1:
                return min(candidate_length, j2)
            fraction = (source_position - i1) / (i2 - i1)
            return min(candidate_length, round(j1 + fraction * (j2 - j1)))

    return candidate_length


def _map_candidate_to_source_units(
    source_units: list[str],
    corrected_content: str,
    *,
    collapse_unit_whitespace: bool,
    prefer_line_mapping: bool,
) -> list[str]:
    if not source_units:
        raise RuntimeError("The source document contains no editable text units.")

    candidate = _normalize_layout_candidate(corrected_content)
    source = "\n".join(source_units)

    source_similarity_text = re.sub(r"\s+", " ", source).strip()
    candidate_similarity_text = re.sub(r"\s+", " ", candidate).strip()
    similarity = SequenceMatcher(
        a=source_similarity_text,
        b=candidate_similarity_text,
        autojunk=False,
    ).ratio()
    if similarity < 0.70:
        raise RuntimeError(
            "Corrected text diverged too far from the source to preserve layout safely."
        )

    if prefer_line_mapping:
        candidate_lines = [line for line in candidate.split("\n") if line.strip()]
        if len(candidate_lines) == len(source_units):
            mapped = candidate_lines
            if collapse_unit_whitespace:
                mapped = [re.sub(r"\s+", " ", item).strip() for item in mapped]
            return mapped

    matcher = SequenceMatcher(a=source, b=candidate, autojunk=False)
    opcodes = list(matcher.get_opcodes())
    mapped_units: list[str] = []
    cursor = 0
    previous_candidate_end = 0

    for unit in source_units:
        source_start = cursor
        source_end = source_start + len(unit)
        candidate_start = _alignment_boundary_index(
            opcodes,
            source_start,
            candidate_length=len(candidate),
        )
        candidate_end = _alignment_boundary_index(
            opcodes,
            source_end,
            candidate_length=len(candidate),
        )
        candidate_start = max(previous_candidate_end, candidate_start)
        candidate_end = max(candidate_start, candidate_end)
        value = candidate[candidate_start:candidate_end].strip("\n")
        if collapse_unit_whitespace:
            value = re.sub(r"\s+", " ", value).strip()
        mapped_units.append(value)
        previous_candidate_end = candidate_end
        cursor = source_end + 1

    if any(source.strip() and not mapped.strip() for source, mapped in zip(source_units, mapped_units)):
        raise RuntimeError(
            "Corrected text could not be aligned safely with every source text unit."
        )

    return mapped_units


def _map_candidate_to_contiguous_units(
    source_units: list[str],
    corrected_content: str,
) -> list[str]:
    if not source_units:
        raise RuntimeError("The source document contains no editable text units.")

    source = "".join(source_units)
    candidate = corrected_content.replace("\r\n", "\n").replace("\r", "\n")
    if not source:
        return ["" for _ in source_units]

    similarity = SequenceMatcher(a=source, b=candidate, autojunk=False).ratio()
    if similarity < 0.60:
        raise RuntimeError(
            "Corrected text diverged too far from the source styled runs to preserve formatting safely."
        )

    opcodes = list(SequenceMatcher(a=source, b=candidate, autojunk=False).get_opcodes())
    mapped_units: list[str] = []
    cursor = 0
    previous_candidate_end = 0
    for unit in source_units:
        source_start = cursor
        source_end = source_start + len(unit)
        candidate_start = _alignment_boundary_index(
            opcodes, source_start, candidate_length=len(candidate)
        )
        candidate_end = _alignment_boundary_index(
            opcodes, source_end, candidate_length=len(candidate)
        )
        candidate_start = max(previous_candidate_end, candidate_start)
        candidate_end = max(candidate_start, candidate_end)
        mapped_units.append(candidate[candidate_start:candidate_end])
        previous_candidate_end = candidate_end
        cursor = source_end

    return mapped_units


def _is_standalone_list_marker(text: str) -> bool:
    return bool(re.fullmatch(r"(?:•|[-*]|\d+[.)])", text.strip()))


def _pdf_source_font_path(
    span_font_name: str,
    *,
    required_text: str = "",
) -> Optional[Path]:
    raw_name = str(span_font_name or "").strip().split("+", 1)[-1]
    compact_name = raw_name.replace(" ", "")
    normalized = compact_name.casefold()
    is_bold = any(token in normalized for token in ("bold", "black", "semibold", "demi"))
    is_italic = "italic" in normalized or "oblique" in normalized
    if is_bold and is_italic:
        variant = "BoldItalic"
    elif is_bold:
        variant = "Bold"
    elif is_italic:
        variant = "Italic"
    else:
        variant = "Regular"

    names = [raw_name, compact_name]
    serif_tokens = (
        "times",
        "serif",
        "cambria",
        "caladea",
        "georgia",
        "garamond",
        "baskerville",
        "bookman",
        "palatino",
    )
    mono_tokens = ("courier", "mono", "consolas")

    if "cambria" in normalized or "caladea" in normalized:
        names.extend(
            (
                f"Caladea-{variant}",
                f"LiberationSerif-{variant}",
                f"NimbusRoman-{variant}",
                f"DejaVuSerif-{variant}",
            )
        )
    elif any(token in normalized for token in serif_tokens):
        names.extend(
            (
                f"LiberationSerif-{variant}",
                f"NimbusRoman-{variant}",
                f"DejaVuSerif-{variant}",
                f"Caladea-{variant}",
            )
        )
    elif any(token in normalized for token in mono_tokens):
        names.extend(
            (
                f"LiberationMono-{variant}",
                f"DejaVuSansMono-{variant}",
            )
        )
    elif "calibri" in normalized or "carlito" in normalized:
        names.extend(
            (
                f"Carlito-{variant}",
                f"NotoSans-{variant}",
                f"LiberationSans-{variant}",
                f"DejaVuSans-{variant}",
            )
        )
    else:
        names.extend(
            (
                f"NotoSans-{variant}",
                f"LiberationSans-{variant}",
                f"DejaVuSans-{variant}",
                f"Carlito-{variant}",
            )
        )

    roots = (
        _BUNDLED_FONT_DIR,
        _SYSTEM_FONT_DIR,
        Path("assets/fonts"),
        Path("fonts"),
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/truetype/liberation2"),
        Path("/usr/share/fonts/truetype/crosextra"),
        Path("/usr/share/fonts/opentype/urw-base35"),
        Path("/usr/share/fonts/truetype/dejavu"),
    )
    for name in dict.fromkeys(name for name in names if name):
        for root in roots:
            for suffix in (".ttf", ".otf", ".ttc"):
                candidate = root / f"{name}{suffix}"
                if candidate.is_file() and (
                    not required_text
                    or _font_covers_content(candidate, required_text)
                ):
                    return candidate

    # The production image always contains the bundled Unicode font set. Use it
    # as the final safe fallback for an unembedded or unavailable source font,
    # but only when one file covers every character that will be inserted.
    if required_text:
        for candidate in _ordered_font_candidates(
            required_text,
            language_tag=None,
        ):
            if _font_covers_content(candidate, required_text):
                return candidate

    return None


def _pdf_font_resource(
    document: fitz.Document,
    page: fitz.Page,
    span_font_name: str,
    *,
    required_text: str = "",
) -> tuple[str, fitz.Font | None]:
    """Return a Unicode-safe resource matching the source PDF typeface.

    Existing PDF subset resources can use custom encodings that are unsafe for
    newly inserted Unicode. Prefer the corresponding full installed/bundled font
    file, while retaining the original point size, color, baseline, and style.
    """
    target = str(span_font_name or "").strip().casefold()
    for font in page.get_fonts(full=True):
        base_font = str(font[3] or "")
        resource_name = str(font[4] or "")
        base_without_subset = base_font.split("+", 1)[-1]
        if target not in {
            base_font.casefold(),
            base_without_subset.casefold(),
            resource_name.casefold(),
        }:
            continue

        xref = int(font[0] or 0)
        font_path = _pdf_source_font_path(
            base_without_subset or span_font_name,
            required_text=required_text,
        )
        if font_path is not None:
            path_digest = hashlib.sha256(
                str(font_path.resolve()).encode("utf-8")
            ).hexdigest()[:12]
            alias = f"RDXF{xref or 0}{path_digest}"
            existing_resources = {str(item[4] or "") for item in page.get_fonts(full=True)}
            if alias not in existing_resources:
                page.insert_font(fontname=alias, fontfile=str(font_path))
            return alias, fitz.Font(fontfile=str(font_path))

        source_extension = str(font[1] or "").strip().casefold()
        if source_extension not in {"", "n/a"}:
            # Embedded resources remain available to PyMuPDF. Reuse them only
            # when no full installed font can safely cover the replacement.
            return resource_name, None

        # PyMuPDF cannot calculate glyph widths for an unembedded resource: its
        # font buffer is null and insert_text raises inside get_char_widths().
        # Fail explicitly instead of exposing that low-level AttributeError or
        # emitting a document with missing glyphs.
        raise RuntimeError(
            "The source PDF uses an unembedded font and no safe replacement "
            f"font is available for '{base_without_subset or span_font_name}'."
        )

    raise RuntimeError(
        f"Could not resolve the source PDF font resource for '{span_font_name}'."
    )


def _pdf_color(value: int) -> tuple[float, float, float]:
    red, green, blue = fitz.sRGB_to_rgb(int(value or 0))
    return red / 255.0, green / 255.0, blue / 255.0


def _pdf_line_rotation(line: dict) -> int:
    direction = tuple(line.get("dir") or (1.0, 0.0))
    x, y = float(direction[0]), float(direction[1])
    candidates = {
        0: (1.0, 0.0),
        90: (0.0, -1.0),
        180: (-1.0, 0.0),
        270: (0.0, 1.0),
    }
    for rotation, expected in candidates.items():
        if abs(x - expected[0]) <= 0.01 and abs(y - expected[1]) <= 0.01:
            return rotation
    raise RuntimeError(
        "A corrected PDF line uses an unsupported non-orthogonal text rotation."
    )


def _pdf_layout_lines(document: fitz.Document) -> list[dict]:
    lines: list[dict] = []
    for page_index, page in enumerate(document):
        page_dict = page.get_text("dict", sort=True)
        for block in page_dict.get("blocks", ()):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", ()):
                spans = [span for span in line.get("spans", ()) if str(span.get("text", ""))]
                text = "".join(str(span.get("text", "")) for span in spans)
                if not text.strip():
                    continue
                lines.append(
                    {
                        "page_index": page_index,
                        "bbox": tuple(line.get("bbox") or block.get("bbox")),
                        "dir": tuple(line.get("dir") or (1.0, 0.0)),
                        "spans": spans,
                        "text": text,
                    }
                )
    return lines


def _map_candidate_to_pdf_spans(source_spans: list[dict], corrected_line: str) -> list[str]:
    source_values = [str(span.get("text", "")) for span in source_spans]
    if len(source_values) == 1:
        return [corrected_line]
    return _map_candidate_to_contiguous_units(source_values, corrected_line)


def _insert_corrected_pdf_line(
    document: fitz.Document,
    page: fitz.Page,
    line: dict,
    corrected_line: str,
) -> None:
    spans = list(line["spans"])
    if not spans:
        raise RuntimeError("A corrected PDF line has no source font spans.")

    corrected_spans = _map_candidate_to_pdf_spans(spans, corrected_line)
    rotation = _pdf_line_rotation(line)

    # Preserve every source run's embedded font resource, size, color, baseline,
    # and text-rendering orientation. For multi-run lines, shift later runs only
    # by the width delta introduced by earlier corrected runs so emphasis and
    # other typography remain attached to the same logical text regions.
    cumulative_shift = 0.0

    for source_span, replacement in zip(spans, corrected_spans):
        if not replacement:
            continue
        font_resource, font_object = _pdf_font_resource(
            document,
            page,
            str(source_span.get("font", "")),
            required_text=replacement,
        )
        font_size = float(source_span.get("size") or 11.0)
        origin = source_span.get("origin") or (
            float(source_span["bbox"][0]),
            float(source_span["bbox"][3]),
        )
        insert_point = fitz.Point(float(origin[0]) + cumulative_shift, float(origin[1]))
        color = _pdf_color(int(source_span.get("color") or 0))
        alpha = max(0.0, min(1.0, float(source_span.get("alpha", 255)) / 255.0))

        page.insert_text(
            insert_point,
            replacement,
            fontname=font_resource,
            fontsize=font_size,
            color=color,
            rotate=rotation,
            overlay=True,
            stroke_opacity=alpha,
            fill_opacity=alpha,
        )

        if rotation == 0 and font_object is not None:
            source_width = float(source_span["bbox"][2]) - float(source_span["bbox"][0])
            replacement_width = font_object.text_length(replacement, fontsize=font_size)
            cumulative_shift += replacement_width - source_width

    if rotation == 0:
        line_start = float(line["bbox"][0])
        line_end = float(line["bbox"][2]) + cumulative_shift
        page_right_limit = float(page.rect.x1) - 4.0
        if line_start < page_right_limit and line_end > page_right_limit + 0.5:
            raise RuntimeError(
                "A corrected PDF line does not fit within the source page geometry without changing typography."
            )


def _write_pdf_preserving_source_layout(
    *,
    source_path: Path,
    corrected_content: str,
    output_path: Path,
) -> None:
    document = fitz.open(source_path)
    try:
        if bool(getattr(document, "needs_pass", False)):
            raise ValueError(
                "Layout-preserving grammar correction does not accept password-protected PDFs."
            )

        lines = _pdf_layout_lines(document)
        if not lines:
            raise RuntimeError(
                "The source PDF has no native text layer that can be corrected while preserving typography."
            )

        source_lines = [str(line["text"]) for line in lines]
        corrected_lines = _map_candidate_to_source_units(
            source_lines,
            corrected_content,
            collapse_unit_whitespace=True,
            prefer_line_mapping=True,
        )

        replacements_by_page: dict[int, list[tuple[dict, str]]] = {}
        for line, corrected_line in zip(lines, corrected_lines):
            source_line = str(line["text"])
            if _is_standalone_list_marker(source_line) and corrected_line != source_line:
                raise RuntimeError("Grammar correction attempted to change a source list marker.")
            replacements_by_page.setdefault(int(line["page_index"]), []).append(
                (line, corrected_line)
            )

        # Reinsert the complete native text layer in geometric reading order.
        # Rewriting only changed lines would append those lines to later PDF
        # content streams, which can make default text extraction read them out
        # of order even though the page looks correct. Rewriting every text line
        # keeps both visual layout and subsequent extraction/search order stable.
        for page_index, replacements in replacements_by_page.items():
            page = document[page_index]
            for line, _ in replacements:
                page.add_redact_annot(
                    fitz.Rect(line["bbox"]),
                    fill=None,
                    cross_out=False,
                )

            # Remove text glyphs only. Images and vector graphics remain untouched,
            # which preserves page backgrounds, decorations, and embedded media.
            page.apply_redactions(images=0, graphics=0, text=0)

            for line, corrected_line in replacements:
                _insert_corrected_pdf_line(
                    document,
                    page,
                    line,
                    corrected_line,
                )

        document.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
        )
    finally:
        document.close()


def _iter_layout_docx_paragraphs(document: Document):
    seen: set[object] = set()

    def emit(paragraphs):
        for paragraph in paragraphs:
            element = paragraph._p
            if element in seen:
                continue
            seen.add(element)
            yield paragraph
            for nested_element in paragraph._p.xpath(".//w:txbxContent//w:p"):
                if nested_element in seen:
                    continue
                seen.add(nested_element)
                yield Paragraph(nested_element, paragraph._parent)

    def table_paragraphs(table):
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs
                for nested_table in cell.tables:
                    yield from table_paragraphs(nested_table)

    yield from emit(document.paragraphs)
    for table in document.tables:
        yield from emit(table_paragraphs(table))
    for section in document.sections:
        for story in (section.header, section.footer):
            yield from emit(story.paragraphs)
            for table in story.tables:
                yield from emit(table_paragraphs(table))


def _docx_paragraph_text_with_math(paragraph) -> str:
    """Return visible paragraph text including Office Math text nodes."""
    text_tags = {qn("w:t"), qn("m:t")}
    tab_tag = qn("w:tab")
    break_tags = {qn("w:br"), qn("w:cr")}
    paragraph_tag = qn("w:p")
    parts: list[str] = []

    for element in paragraph._p.iter():
        parent = element.getparent()
        belongs_to_paragraph = True
        while parent is not None and parent is not paragraph._p:
            if parent.tag == paragraph_tag:
                belongs_to_paragraph = False
                break
            parent = parent.getparent()
        if not belongs_to_paragraph:
            continue

        if element.tag in text_tags and element.text:
            parts.append(element.text)
        elif element.tag == tab_tag:
            parts.append("\t")
        elif element.tag in break_tags:
            parts.append("\n")

    return "".join(parts)


def _docx_math_fragments(paragraph) -> list[str]:
    fragments: list[str] = []
    math_text_tag = qn("m:t")
    for math_element in paragraph._p.xpath(".//m:oMath"):
        value = "".join(
            str(element.text or "")
            for element in math_element.iter()
            if element.tag == math_text_tag
        ).strip()
        if value and value not in fragments:
            fragments.append(value)
    return fragments


def _docx_math_spacing_requirements(paragraph) -> list[tuple[bool, bool]]:
    """Capture whether source prose has spaces immediately around each OMML node."""
    elements = list(paragraph._p.iter())
    word_text_tag = qn("w:t")
    math_tag = qn("m:oMath")
    requirements: list[tuple[bool, bool]] = []

    for index, element in enumerate(elements):
        if element.tag != math_tag:
            continue
        previous_text = next(
            (
                str(candidate.text or "")
                for candidate in reversed(elements[:index])
                if candidate.tag == word_text_tag
            ),
            "",
        )
        following_text = next(
            (
                str(candidate.text or "")
                for candidate in elements[index + 1:]
                if candidate.tag == word_text_tag
            ),
            "",
        )
        requirements.append(
            (
                bool(previous_text and previous_text[-1].isspace()),
                bool(following_text and following_text[0].isspace()),
            )
        )

    return requirements


def _restore_docx_math_spacing(
    paragraph,
    requirements: list[tuple[bool, bool]],
) -> None:
    elements = list(paragraph._p.iter())
    word_text_tag = qn("w:t")
    math_tag = qn("m:oMath")
    math_indexes = [
        index for index, element in enumerate(elements) if element.tag == math_tag
    ]

    for math_index, (space_before, space_after) in zip(math_indexes, requirements):
        previous_node = next(
            (
                candidate
                for candidate in reversed(elements[:math_index])
                if candidate.tag == word_text_tag and candidate.text
            ),
            None,
        )
        following_node = next(
            (
                candidate
                for candidate in elements[math_index + 1:]
                if candidate.tag == word_text_tag and candidate.text
            ),
            None,
        )
        if space_before and previous_node is not None and not previous_node.text[-1].isspace():
            previous_node.text += " "
        if space_after and following_node is not None and not following_node.text[0].isspace():
            following_node.text = " " + following_node.text


def _remove_preserved_docx_math(value: str, fragments: list[str]) -> str:
    """Remove summarized plain-text copies of equations kept as native OMML."""
    result = value
    for fragment in fragments:
        pieces = [piece for piece in re.split(r"\s+", fragment.strip()) if piece]
        if not pieces:
            continue
        flexible_pattern = r"\s+".join(re.escape(piece) for piece in pieces)
        result = re.sub(flexible_pattern, "", result, count=1)
    return re.sub(r"[ \t]{2,}", " ", result).strip()


def _replace_docx_paragraph_text_preserving_runs(paragraph, corrected_text: str) -> None:
    runs = list(paragraph.runs)
    if not runs:
        paragraph.add_run(corrected_text)
        return

    source_run_texts = [run.text for run in runs]
    run_source = "".join(source_run_texts)
    if run_source != paragraph.text:
        # Hyperlinks / field-code constructions are deliberately not flattened;
        # doing so would destroy formatting or document semantics.
        raise RuntimeError(
            "A corrected DOCX paragraph contains unsupported complex inline content."
        )

    corrected_run_texts = _map_candidate_to_contiguous_units(
        source_run_texts,
        corrected_text,
    )
    for run, replacement in zip(runs, corrected_run_texts):
        run.text = replacement


def _write_docx_preserving_source_layout(
    *,
    source_path: Path,
    corrected_content: str,
    output_path: Path,
) -> None:
    document = Document(source_path)
    paragraphs = [
        paragraph
        for paragraph in _iter_layout_docx_paragraphs(document)
        if paragraph.text and paragraph.text.strip()
    ]
    if not paragraphs:
        raise RuntimeError("The source DOCX contains no editable text paragraphs.")

    source_paragraphs = [paragraph.text for paragraph in paragraphs]
    corrected_paragraphs = _map_candidate_to_source_units(
        source_paragraphs,
        corrected_content,
        collapse_unit_whitespace=False,
        prefer_line_mapping=False,
    )

    for paragraph, corrected_text in zip(paragraphs, corrected_paragraphs):
        if corrected_text == paragraph.text:
            continue
        _replace_docx_paragraph_text_preserving_runs(paragraph, corrected_text)

    document.save(output_path)


def _summary_boundary_index(value: str, approximate: int, *, lower: int, upper: int) -> int:
    """Snap a proportional summary boundary to nearby whitespace."""
    approximate = max(lower, min(upper, approximate))
    if approximate in {lower, upper}:
        return approximate

    search_radius = min(96, max(8, (upper - lower) // 3))
    best: tuple[int, int] | None = None
    start = max(lower + 1, approximate - search_radius)
    stop = min(upper - 1, approximate + search_radius)
    for index in range(start, stop + 1):
        if not value[index].isspace():
            continue
        distance = abs(index - approximate)
        if best is None or distance < best[0]:
            best = (distance, index)
    return best[1] if best is not None else approximate


def _map_summary_to_source_units(
    source_units: list[str],
    summarized_content: str,
    *,
    collapse_unit_whitespace: bool,
) -> list[str]:
    """Map compressive summary text onto existing source layout units.

    Near-identity transforms use the stricter grammar alignment helpers above.
    Summaries are expected to remove text, so this mapper permits empty source
    units and falls back to deterministic proportional allocation when lexical
    anchors are too sparse for SequenceMatcher to produce a complete mapping.
    """
    if not source_units:
        raise RuntimeError("The source document contains no editable text units.")

    candidate = summarized_content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not candidate:
        raise RuntimeError("Summarization produced no text to place in the source layout.")

    candidate_lines = [line.strip() for line in candidate.split("\n") if line.strip()]
    if len(candidate_lines) == len(source_units):
        if collapse_unit_whitespace:
            return [re.sub(r"\s+", " ", line).strip() for line in candidate_lines]
        return candidate_lines

    normalized_units = [
        re.sub(r"\s+", " ", unit).strip() if collapse_unit_whitespace else unit.strip()
        for unit in source_units
    ]
    source = "\n".join(normalized_units)
    normalized_candidate = (
        re.sub(r"[ \t]+", " ", candidate).strip()
        if collapse_unit_whitespace
        else candidate
    )

    matcher = SequenceMatcher(a=source, b=normalized_candidate, autojunk=False)
    opcodes = list(matcher.get_opcodes())
    mapped_units: list[str] = []
    cursor = 0
    previous_candidate_end = 0

    for index, unit in enumerate(normalized_units):
        source_start = cursor
        source_end = source_start + len(unit)
        candidate_start = previous_candidate_end
        candidate_end = _alignment_boundary_index(
            opcodes,
            source_end,
            candidate_length=len(normalized_candidate),
        )
        candidate_end = max(candidate_start, candidate_end)
        if index == len(normalized_units) - 1:
            candidate_end = len(normalized_candidate)
        else:
            candidate_end = _summary_boundary_index(
                normalized_candidate,
                candidate_end,
                lower=candidate_start,
                upper=len(normalized_candidate),
            )
        value = normalized_candidate[candidate_start:candidate_end].strip("\n ")
        if collapse_unit_whitespace:
            value = re.sub(r"\s+", " ", value).strip()
        mapped_units.append(value)
        previous_candidate_end = candidate_end
        cursor = source_end + 1

    reconstructed = re.sub(r"\s+", " ", " ".join(mapped_units)).strip()
    candidate_comparable = re.sub(r"\s+", " ", normalized_candidate).strip()
    coverage = SequenceMatcher(
        a=candidate_comparable,
        b=reconstructed,
        autojunk=False,
    ).ratio()
    if coverage >= 0.90:
        return mapped_units

    # Low-overlap abstractive wording can leave weak lexical anchors. Allocate
    # the exact generated summary proportionally to source-unit sizes instead of
    # dropping text or guessing at semantics. Boundaries snap to whitespace, so
    # formula tokens and words are not normally split.
    total_weight = sum(max(1, len(unit)) for unit in normalized_units)
    proportional: list[str] = []
    candidate_length = len(normalized_candidate)
    cumulative_weight = 0
    previous = 0
    for index, unit in enumerate(normalized_units):
        cumulative_weight += max(1, len(unit))
        if index == len(normalized_units) - 1:
            boundary = candidate_length
        else:
            approximate = round(candidate_length * cumulative_weight / total_weight)
            boundary = _summary_boundary_index(
                normalized_candidate,
                approximate,
                lower=previous,
                upper=candidate_length,
            )
        value = normalized_candidate[previous:boundary].strip("\n ")
        if collapse_unit_whitespace:
            value = re.sub(r"\s+", " ", value).strip()
        proportional.append(value)
        previous = boundary

    return proportional


def _pdf_line_baseline(line: dict) -> float:
    spans = list(line.get("spans") or ())
    if spans and spans[0].get("origin"):
        return float(spans[0]["origin"][1])
    return float(line["bbox"][3])


def _pdf_line_font_size(line: dict) -> float:
    spans = [span for span in line.get("spans", ()) if str(span.get("text", ""))]
    if not spans:
        return 11.0
    return float(spans[0].get("size") or 11.0)


def _pdf_rect_union(rectangles: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (
        min(rect[0] for rect in rectangles),
        min(rect[1] for rect in rectangles),
        max(rect[2] for rect in rectangles),
        max(rect[3] for rect in rectangles),
    )


def _pdf_summary_rows(lines: list[dict]) -> list[dict]:
    """Combine same-baseline PDF line objects (for example bullet + item text)."""
    ordered = sorted(lines, key=lambda item: (float(item["bbox"][1]), float(item["bbox"][0])))
    rows: list[dict] = []
    for line in ordered:
        if not rows:
            rows.append({"lines": [line]})
            continue

        current = rows[-1]
        current_lines = current["lines"]
        current_baseline = median(_pdf_line_baseline(item) for item in current_lines)
        line_baseline = _pdf_line_baseline(line)
        font_reference = min(_pdf_line_font_size(line), 14.0)
        tolerance = max(1.5, font_reference * 0.18)
        current_right = max(float(item["bbox"][2]) for item in current_lines)
        horizontal_gap = float(line["bbox"][0]) - current_right
        same_visual_row = (
            abs(line_baseline - current_baseline) <= tolerance
            and horizontal_gap <= max(24.0, font_reference * 3.0)
        )
        if same_visual_row:
            current_lines.append(line)
        else:
            rows.append({"lines": [line]})

    for row in rows:
        row["lines"].sort(key=lambda item: float(item["bbox"][0]))
        row["bbox"] = _pdf_rect_union([tuple(item["bbox"]) for item in row["lines"]])
        row["text"] = " ".join(
            str(item.get("text", "")).strip()
            for item in row["lines"]
            if str(item.get("text", "")).strip()
        ).strip()
    return rows


def _pdf_row_has_list_marker(row: dict) -> bool:
    return any(_is_standalone_list_marker(str(line.get("text", ""))) for line in row["lines"])


def _pdf_summary_groups(document: fitz.Document) -> list[dict]:
    """Build paragraph-like geometric groups without changing PDF page structure."""
    all_lines = _pdf_layout_lines(document)
    by_page: dict[int, list[dict]] = {}
    for line in all_lines:
        by_page.setdefault(int(line["page_index"]), []).append(line)

    groups: list[dict] = []
    for page_index in sorted(by_page):
        rows = _pdf_summary_rows(by_page[page_index])
        page_groups: list[dict] = []
        for row in rows:
            if not page_groups:
                page_groups.append({"page_index": page_index, "rows": [row]})
                continue

            previous_group = page_groups[-1]
            previous_row = previous_group["rows"][-1]
            vertical_gap = float(row["bbox"][1]) - float(previous_row["bbox"][3])
            font_reference = median(
                [_pdf_line_font_size(line) for line in previous_row["lines"] + row["lines"]]
            )
            continuation_threshold = max(3.0, min(7.0, font_reference * 0.55))

            parallel_row = (
                vertical_gap < 0
                and abs(float(row["bbox"][0]) - float(previous_row["bbox"][0]))
                > max(36.0, font_reference * 4.0)
            )
            force_boundary = (
                _pdf_row_has_list_marker(row)
                or _pdf_row_has_list_marker(previous_row)
                or parallel_row
            )
            if not force_boundary and vertical_gap <= continuation_threshold:
                previous_group["rows"].append(row)
            else:
                page_groups.append({"page_index": page_index, "rows": [row]})

        for group in page_groups:
            group_lines = [line for row in group["rows"] for line in row["lines"]]
            group["lines"] = group_lines
            group["bbox"] = _pdf_rect_union([tuple(line["bbox"]) for line in group_lines])
            group["text"] = " ".join(row["text"] for row in group["rows"] if row["text"]).strip()
            groups.append(group)

    return groups


def _pdf_summary_primary_line(group: dict) -> dict:
    for row in group["rows"]:
        for line in row["lines"]:
            if not _is_standalone_list_marker(str(line.get("text", ""))):
                return line
    return group["lines"][0]


def _strip_summary_list_marker(value: str, marker_lines: list[dict]) -> str:
    result = value.strip()
    for line in marker_lines:
        marker = str(line.get("text", "")).strip()
        if marker and result.startswith(marker):
            return result[len(marker):].lstrip()
    return result


def _insert_summary_pdf_group(
    document: fitz.Document,
    page: fitz.Page,
    group: dict,
    summarized_text: str,
    *,
    page_text_right: float,
) -> None:
    marker_lines = [
        line
        for line in group["lines"]
        if _is_standalone_list_marker(str(line.get("text", "")))
    ]
    body_lines = [line for line in group["lines"] if line not in marker_lines]

    # List glyphs are structural, not summary prose. Keep their exact source
    # geometry and typography even when the item text is compressed.
    for marker_line in marker_lines:
        _insert_corrected_pdf_line(
            document,
            page,
            marker_line,
            str(marker_line.get("text", "")),
        )

    body_text = _strip_summary_list_marker(summarized_text, marker_lines)
    if not body_text or not body_lines:
        return

    primary_line = _pdf_summary_primary_line({**group, "lines": body_lines, "rows": group["rows"]})
    spans = [span for span in primary_line.get("spans", ()) if str(span.get("text", ""))]
    if not spans:
        raise RuntimeError("A summarized PDF text group has no source typography span.")
    source_span = spans[0]

    font_resource, _ = _pdf_font_resource(
        document,
        page,
        str(source_span.get("font", "")),
        required_text=body_text,
    )
    font_size = float(source_span.get("size") or 11.0)
    color = _pdf_color(int(source_span.get("color") or 0))
    alpha = max(0.0, min(1.0, float(source_span.get("alpha", 255)) / 255.0))
    rotation = _pdf_line_rotation(primary_line)

    body_rectangles = [tuple(line["bbox"]) for line in body_lines]
    x0, y0, x2, y2 = _pdf_rect_union(body_rectangles)
    right_edge = max(x2, page_text_right)

    baselines = sorted({_pdf_line_baseline(line) for line in body_lines})
    line_height: float | None = None
    if len(baselines) >= 2:
        spacings = [later - earlier for earlier, later in zip(baselines, baselines[1:]) if later > earlier]
        if spacings:
            line_height = max(0.8, median(spacings) / max(font_size, 0.1))

    # A small vertical tolerance is needed because insert_textbox operates on
    # font ascender/descender metrics rather than the exact glyph bbox returned
    # by extraction. It does not change page geometry or font size.
    rect = fitz.Rect(
        float(x0),
        float(y0) - 0.75,
        min(float(page.rect.x1) - 1.0, float(right_edge) + 1.0),
        min(float(page.rect.y1) - 1.0, float(y2) + max(2.0, font_size * 0.35)),
    )

    remaining = page.insert_textbox(
        rect,
        re.sub(r"\s+", " ", body_text).strip(),
        fontname=font_resource,
        fontsize=font_size,
        lineheight=line_height,
        color=color,
        align=0,
        rotate=rotation,
        overlay=True,
        stroke_opacity=alpha,
        fill_opacity=alpha,
    )
    if remaining < -0.5:
        raise RuntimeError(
            "A summarized PDF paragraph does not fit inside the source text region "
            "without changing the original typography."
        )


def _write_pdf_summary_preserving_source_layout(
    *,
    source_path: Path,
    summarized_content: str,
    output_path: Path,
) -> None:
    document = fitz.open(source_path)
    try:
        if bool(getattr(document, "needs_pass", False)):
            raise ValueError(
                "Layout-preserving summarization does not accept password-protected PDFs."
            )

        groups = _pdf_summary_groups(document)
        if not groups:
            raise RuntimeError(
                "The source PDF has no native text layer whose typography can be preserved."
            )

        source_units = [str(group["text"]) for group in groups]
        summarized_units = _map_summary_to_source_units(
            source_units,
            summarized_content,
            collapse_unit_whitespace=True,
        )

        groups_by_page: dict[int, list[tuple[dict, str]]] = {}
        for group, summarized_text in zip(groups, summarized_units):
            groups_by_page.setdefault(int(group["page_index"]), []).append(
                (group, summarized_text)
            )

        for page_index, replacements in groups_by_page.items():
            page = document[page_index]
            page_text_right = max(
                float(line["bbox"][2])
                for group, _ in replacements
                for line in group["lines"]
            )

            for group, _ in replacements:
                for line in group["lines"]:
                    page.add_redact_annot(
                        fitz.Rect(line["bbox"]),
                        fill=None,
                        cross_out=False,
                    )

            # Remove glyphs only; all images, vector graphics, annotations, page
            # sizes, and other non-text objects remain in their original positions.
            page.apply_redactions(images=0, graphics=0, text=0)

            for group, summarized_text in replacements:
                source_text = re.sub(r"\s+", " ", str(group["text"])).strip()
                target_text = re.sub(r"\s+", " ", summarized_text).strip()
                if target_text == source_text:
                    for line in group["lines"]:
                        _insert_corrected_pdf_line(
                            document,
                            page,
                            line,
                            str(line.get("text", "")),
                        )
                    continue

                _insert_summary_pdf_group(
                    document,
                    page,
                    group,
                    summarized_text,
                    page_text_right=page_text_right,
                )

        document.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
        )
    finally:
        document.close()


def _replace_docx_paragraph_text_preserving_runs_for_summary(
    paragraph,
    summarized_text: str,
) -> None:
    runs = list(paragraph.runs)
    if not runs:
        if summarized_text:
            paragraph.add_run(summarized_text)
        return

    source_run_texts = [run.text for run in runs]
    run_source = "".join(source_run_texts)
    if run_source != paragraph.text:
        raise RuntimeError(
            "A summarized DOCX paragraph contains unsupported complex inline content."
        )

    if not summarized_text:
        for run in runs:
            run.text = ""
        return

    if len(runs) == 1:
        runs[0].text = summarized_text
        return

    matcher = SequenceMatcher(a=run_source, b=summarized_text, autojunk=False)
    opcodes = list(matcher.get_opcodes())
    replacements: list[str] = []
    source_cursor = 0
    previous_candidate_end = 0
    for index, source_run in enumerate(source_run_texts):
        source_start = source_cursor
        source_end = source_start + len(source_run)
        candidate_start = _alignment_boundary_index(
            opcodes,
            source_start,
            candidate_length=len(summarized_text),
        )
        candidate_end = _alignment_boundary_index(
            opcodes,
            source_end,
            candidate_length=len(summarized_text),
        )
        if index == 0:
            candidate_start = 0
        if index == len(source_run_texts) - 1:
            candidate_end = len(summarized_text)
        candidate_start = max(previous_candidate_end, candidate_start)
        candidate_end = max(candidate_start, candidate_end)
        replacements.append(summarized_text[candidate_start:candidate_end])
        previous_candidate_end = candidate_end
        source_cursor = source_end

    for run, replacement in zip(runs, replacements):
        run.text = replacement


def _write_docx_summary_preserving_source_layout(
    *,
    source_path: Path,
    summarized_content: str,
    output_path: Path,
) -> None:
    document = Document(source_path)
    paragraph_entries = [
        (paragraph, _docx_paragraph_text_with_math(paragraph))
        for paragraph in _iter_layout_docx_paragraphs(document)
    ]
    paragraph_entries = [
        (paragraph, text)
        for paragraph, text in paragraph_entries
        if text and text.strip()
    ]
    if not paragraph_entries:
        raise RuntimeError("The source DOCX contains no editable text paragraphs.")

    source_paragraphs = [text for _, text in paragraph_entries]
    summarized_paragraphs = _map_summary_to_source_units(
        source_paragraphs,
        summarized_content,
        collapse_unit_whitespace=False,
    )
    native_math_fragments = [
        fragment
        for paragraph, _ in paragraph_entries
        for fragment in _docx_math_fragments(paragraph)
    ]

    for (paragraph, source_text), summarized_text in zip(
        paragraph_entries,
        summarized_paragraphs,
    ):
        # Native Office Math remains in the original XML with its formatting and
        # layout intact. Remove its plain-text representation from the generated
        # prose before updating Word runs so equations are never duplicated.
        editable_summary = _remove_preserved_docx_math(
            summarized_text,
            native_math_fragments,
        )
        if (
            summarized_text == source_text
            and editable_summary == paragraph.text
        ):
            continue
        math_spacing_requirements = _docx_math_spacing_requirements(paragraph)
        _replace_docx_paragraph_text_preserving_runs_for_summary(
            paragraph,
            editable_summary,
        )
        _restore_docx_math_spacing(paragraph, math_spacing_requirements)

    document.save(output_path)

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


def _normalize_language_tag(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("language_tag must be a string when provided.")
    raw = value.strip().replace("_", "-")
    if not raw:
        return None
    aliases = {
        "zh": "zh-Hans",
        "zh-cn": "zh-Hans",
        "zh-sg": "zh-Hans",
        "zh-hans": "zh-Hans",
        "zh-tw": "zh-Hant",
        "zh-hk": "zh-Hant",
        "zh-mo": "zh-Hant",
        "zh-hant": "zh-Hant",
    }
    return aliases.get(raw.casefold(), raw)


def _get_file_size_mb(path: Path) -> float:
    return round(path.stat().st_size / (1024 * 1024), 4)


def _contains_arabic(value: str) -> bool:
    return any(
        "\u0600" <= char <= "\u06FF"
        or "\u0750" <= char <= "\u077F"
        or "\u08A0" <= char <= "\u08FF"
        for char in value
    )


def _contains_devanagari(value: str) -> bool:
    return any("\u0900" <= char <= "\u097F" for char in value)


def _contains_hangul(value: str) -> bool:
    return any(
        "\u1100" <= char <= "\u11FF"
        or "\u3130" <= char <= "\u318F"
        or "\uAC00" <= char <= "\uD7AF"
        for char in value
    )


def _contains_japanese_kana(value: str) -> bool:
    return any(
        "\u3040" <= char <= "\u30FF"
        or "\u31F0" <= char <= "\u31FF"
        for char in value
    )


def _contains_cjk_ideographs(value: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4DBF"
        or "\u4E00" <= char <= "\u9FFF"
        or "\uF900" <= char <= "\uFAFF"
        for char in value
    )


def _is_rtl_text(value: str) -> bool:
    rtl_count = sum(
        unicodedata.bidirectional(char) in {"R", "AL", "AN"}
        for char in value
    )
    ltr_count = sum(unicodedata.bidirectional(char) == "L" for char in value)
    return rtl_count > ltr_count


def _set_docx_run_font(run, family: str) -> None:
    run.font.name = family
    run.font.size = Pt(11)
    run_properties = run._element.get_or_add_rPr()
    run_fonts = run_properties.rFonts
    if run_fonts is None:
        run_fonts = OxmlElement("w:rFonts")
        run_properties.insert(0, run_fonts)
    for attribute in ("ascii", "hAnsi", "eastAsia", "cs"):
        run_fonts.set(qn(f"w:{attribute}"), family)


def _set_docx_paragraph_rtl(paragraph) -> None:
    paragraph_properties = paragraph._p.get_or_add_pPr()
    if paragraph_properties.find(qn("w:bidi")) is None:
        paragraph_properties.append(OxmlElement("w:bidi"))


def _set_docx_run_rtl(run) -> None:
    run_properties = run._element.get_or_add_rPr()
    if run_properties.find(qn("w:rtl")) is None:
        run_properties.append(OxmlElement("w:rtl"))


def _preferred_font_role(
    content: str,
    *,
    language_tag: Optional[str],
) -> str:
    normalized_tag = (language_tag or "").casefold()
    if normalized_tag == "zh-hans":
        return "sc"
    if normalized_tag == "zh-hant":
        return "tc"
    if normalized_tag == "ja" or normalized_tag.startswith("ja-"):
        return "jp"
    if normalized_tag == "ko" or normalized_tag.startswith("ko-"):
        return "kr"
    if normalized_tag == "hi" or normalized_tag.startswith("hi-"):
        return "devanagari"
    if normalized_tag == "ar" or normalized_tag.startswith("ar-"):
        return "arabic"

    if _contains_arabic(content):
        return "arabic"
    if _contains_devanagari(content):
        return "devanagari"
    if _contains_hangul(content):
        return "kr"
    if _contains_japanese_kana(content):
        return "jp"
    if _contains_cjk_ideographs(content):
        return "tc" if _looks_traditional_chinese(content) else "sc"
    return "general"


def _looks_traditional_chinese(value: str) -> bool:
    # These high-frequency characters differ from their simplified forms and
    # provide a deterministic regional-font hint when no target tag is known.
    traditional_markers = frozenset(
        "國學體臺灣門風書車雲龍廣東話語萬與為這來時會開關電腦"
    )
    simplified_markers = frozenset(
        "国学体台湾门风书车云龙广东话语万与为这来时会开关电脑"
    )
    traditional_count = sum(char in traditional_markers for char in value)
    simplified_count = sum(char in simplified_markers for char in value)
    return traditional_count > simplified_count


def _docx_font_family(
    content: str,
    *,
    language_tag: Optional[str],
) -> str:
    role = _preferred_font_role(content, language_tag=language_tag)
    return {
        "arabic": "Noto Naskh Arabic",
        "devanagari": "Noto Sans Devanagari",
        "sc": "Noto Sans SC",
        "tc": "Noto Sans TC",
        "jp": "Noto Sans JP",
        "kr": "Noto Sans KR",
    }.get(role, "Noto Sans")


def _required_codepoints(content: str) -> set[int]:
    return {
        ord(char)
        for char in content
        if not char.isspace()
        and not unicodedata.category(char).startswith("C")
        and not (
            "\uFE00" <= char <= "\uFE0F"
            or "\U000E0100" <= char <= "\U000E01EF"
        )
    }


def _asset_font_candidates(*filenames: str) -> list[Path]:
    roots = (
        _BUNDLED_FONT_DIR,
        _SYSTEM_FONT_DIR,
        Path("assets/fonts"),
        Path("fonts"),
    )
    return [root / filename for filename in filenames for root in roots]


def _font_candidates_for_role(role: str) -> list[Path]:
    if role == "arabic":
        return _asset_font_candidates(
            "NotoNaskhArabic-Regular.ttf",
            "NotoSansArabic-Regular.ttf",
        ) + [
            Path("/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
        ]
    if role == "devanagari":
        return _asset_font_candidates(
            "NotoSansDevanagari-Regular.ttf",
        ) + [
            Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
            Path("/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf"),
        ]
    if role == "sc":
        return _asset_font_candidates(
            "NotoSansSC-Regular.ttf",
            "NotoSansSC-Regular.otf",
            "NotoSansCJKsc-Regular.otf",
        ) + [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf"),
        ]
    if role == "tc":
        return _asset_font_candidates(
            "NotoSansTC-Regular.ttf",
            "NotoSansTC-Regular.otf",
            "NotoSansCJKtc-Regular.otf",
        ) + [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf"),
        ]
    if role == "jp":
        return _asset_font_candidates(
            "NotoSansJP-Regular.ttf",
            "NotoSansJP-Regular.otf",
            "NotoSansCJKjp-Regular.otf",
        ) + [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf"),
        ]
    if role == "kr":
        return _asset_font_candidates(
            "NotoSansKR-Regular.ttf",
            "NotoSansKR-Regular.otf",
            "NotoSansCJKkr-Regular.otf",
        ) + [
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf"),
        ]
    if role == "math":
        return _asset_font_candidates("NotoSansMath-Regular.ttf") + [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuMathTeXGyre.ttf"),
        ]
    if role == "symbols":
        return _asset_font_candidates("NotoSansSymbols2-Regular.ttf")
    return _asset_font_candidates("NotoSans-Regular.ttf") + [
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]


def _ordered_font_candidates(
    content: str,
    *,
    language_tag: Optional[str],
) -> list[Path]:
    preferred_role = _preferred_font_role(
        content,
        language_tag=language_tag,
    )
    roles = [
        preferred_role,
        "general",
        "math",
        "symbols",
        "arabic",
        "devanagari",
        "sc",
        "tc",
        "jp",
        "kr",
    ]
    configured = os.getenv(PDF_UNICODE_FONT_PATH_ENV, "").strip()
    candidates = [Path(configured)] if configured else []
    for role in dict.fromkeys(roles):
        candidates.extend(_font_candidates_for_role(role))

    resolved_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        marker = str(resolved)
        if marker in seen or not resolved.is_file():
            continue
        seen.add(marker)
        resolved_candidates.append(resolved)
    return resolved_candidates


@lru_cache(maxsize=64)
def _font_codepoints(font_path: str) -> frozenset[int]:
    """Read a font cmap once so a PDF is never emitted with missing glyphs."""
    try:
        from fontTools.ttLib import TTCollection, TTFont
    except ImportError as exc:
        raise RuntimeError(
            "fonttools is required to verify Unicode PDF font coverage."
        ) from exc

    path = Path(font_path)
    fonts = []
    collection = None
    try:
        if path.suffix.lower() == ".ttc":
            collection = TTCollection(str(path), lazy=True)
            fonts = list(collection.fonts)
        else:
            fonts = [TTFont(str(path), lazy=True)]

        codepoints: set[int] = set()
        for font in fonts:
            cmap = font.get("cmap")
            if cmap is None:
                continue
            for table in cmap.tables:
                if table.isUnicode():
                    codepoints.update(table.cmap)
        return frozenset(codepoints)
    finally:
        if collection is not None:
            collection.close()
        else:
            for font in fonts:
                font.close()


def _font_covers_content(font_path: Path, content: str) -> bool:
    required = _required_codepoints(content)
    try:
        return required.issubset(_font_codepoints(str(font_path)))
    except RuntimeError:
        raise
    except Exception:
        return False


def _select_document_font_paths(
    content: str,
    *,
    language_tag: Optional[str] = None,
) -> list[Path]:
    required = _required_codepoints(content)
    if not required:
        raise RuntimeError("PDF content contains no renderable characters.")

    selected: list[Path] = []
    uncovered = set(required)
    for candidate in _ordered_font_candidates(
        content,
        language_tag=language_tag,
    ):
        try:
            candidate_codepoints = _font_codepoints(str(candidate))
        except RuntimeError:
            raise
        except Exception:
            continue

        newly_covered = uncovered.intersection(candidate_codepoints)
        if not newly_covered:
            continue
        selected.append(candidate)
        uncovered.difference_update(newly_covered)
        if not uncovered:
            break

    if not selected:
        raise RuntimeError(
            "No usable Unicode PDF font was found in backend/assets/fonts."
        )
    if uncovered:
        preview = ", ".join(
            f"U+{codepoint:04X} ({chr(codepoint)!r})"
            for codepoint in sorted(uncovered)[:12]
        )
        suffix = "" if len(uncovered) <= 12 else ", …"
        raise RuntimeError(
            "PDF output contains characters not covered by the bundled font "
            f"set: {preview}{suffix}"
        )
    return selected


def _find_unicode_font_path(
    content: str = "",
    *,
    language_tag: Optional[str] = None,
) -> Path | None:
    """Backward-compatible primary-font resolver."""
    try:
        return _select_document_font_paths(
            content,
            language_tag=language_tag,
        )[0]
    except RuntimeError:
        return None


__all__ = [
    "SUPPORTED_OUTPUT_FORMATS",
    "WrittenArtifact",
    "DocumentWriterBackend",
    "RealDocumentWriterBackend",
    "DocumentWriter",
    "write_document",
    "write_document_preserving_source_layout",
    "write_summary_preserving_source_layout",
]
