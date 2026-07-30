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
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Protocol

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
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
]
