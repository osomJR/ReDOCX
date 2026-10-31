from __future__ import annotations

"""
Evidence extraction utilities for compliance evaluation.

Design goals:
- deterministic, text-first evidence discovery
- preserve source_document_index always
- preserve page_number for PDFs when possible
- preserve section_label when inferable from nearby text
- return schema-aligned EvidenceReference objects
- avoid inventing evidence when the source text is weak or unavailable
"""

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterable, Optional, Sequence
import hashlib
import io
import math
import os
import re
import time
from xml.etree import ElementTree

import fitz  # PyMuPDF

try:
    import regex as bounded_regex
except ImportError:
    bounded_regex = None

try:
    from backend.src.schema import DocumentInputFormat, DocumentPayload, DocumentSetPayload, EvidenceReference
except ImportError:  # pragma: no cover
    from backend.src.schema import DocumentInputFormat, DocumentPayload, DocumentSetPayload, EvidenceReference

try:
    from backend.src.extraction import extract_text_by_format
except ImportError:  # pragma: no cover
    from backend.src.extraction import extract_text_by_format

DEFAULT_EXCERPT_WINDOW = 160
DEFAULT_MAX_MATCHES_PER_SIGNAL = 5
MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT = 10_000_000


@dataclass(frozen=True)
class PageText:
    page_number: int
    text: str


@dataclass(frozen=True)
class EvidenceDocument:
    source_document_index: int
    input_format: DocumentInputFormat
    source_reference: Optional[str]
    display_name: str
    file_size_mb: float
    checksum_sha256: str
    ocr_used: bool
    text: str
    pages: tuple[PageText, ...]
    page_count: Optional[int] = None
    warnings: tuple[str, ...] = ()
    coverage_complete: bool = True


@dataclass(frozen=True)
class SignalMatch:
    source_document_index: int
    signal: str
    start: int
    end: int
    page_number: Optional[int]
    section_label: Optional[str]
    locator_text: str
    excerpt: str

    def to_reference(self) -> EvidenceReference:
        return EvidenceReference(
            source_document_index=self.source_document_index,
            page_number=self.page_number,
            section_label=self.section_label,
            locator_text=self.locator_text,
            excerpt=self.excerpt,
        )


@dataclass(frozen=True)
class EvidenceSearchResult:
    signal: str
    matches: tuple[SignalMatch, ...]


class EvidenceExtractionError(RuntimeError):
    """Raised when evidence extraction cannot proceed safely."""


_SEARCH_DEADLINE: ContextVar[Optional[float]] = ContextVar("compliance_search_deadline", default=None)


def _positive_limit(name: str, default: float, *, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise EvidenceExtractionError(f"Compliance processing limit {name} is invalid.") from exc
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise EvidenceExtractionError(f"Compliance processing limit {name} is invalid.")
    return value


@contextmanager
def evidence_search_budget():
    limit = _positive_limit("COMPLIANCE_EVALUATION_TIMEOUT_SECONDS", 30, maximum=300)
    parent = _SEARCH_DEADLINE.get()
    deadline = min(parent, time.monotonic() + limit) if parent is not None else time.monotonic() + limit
    token = _SEARCH_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _SEARCH_DEADLINE.reset(token)



def build_evidence_documents(
    input_artifact: DocumentPayload | DocumentSetPayload,
) -> list[EvidenceDocument]:
    """Extract Compliance evidence once, with page/image coverage and budgets."""
    documents = [input_artifact] if isinstance(input_artifact, DocumentPayload) else list(input_artifact.documents)
    corpus: list[EvidenceDocument] = []
    budget = _ExtractionBudget()
    for index, document in enumerate(documents):
        budget.check()
        source_path = _resolve_source_path(document.filename)
        fmt = document.metadata.input_format
        pages: list[PageText] = []
        text = _normalize_text(document.text)
        ocr_used = bool(document.metadata.ocr_used)
        warnings: list[str] = []
        complete = True
        page_count = None
        try:
            if source_path is not None and fmt == DocumentInputFormat.pdf:
                pages, ocr_used, warnings, complete = _extract_compliance_pdf(source_path, budget)
                page_count = len(pages)
                text = "\n\n".join(page.text for page in pages if page.text.strip())
            elif source_path is not None and fmt == DocumentInputFormat.docx:
                text, ocr_used, warnings, complete = _extract_compliance_docx(source_path, budget)
            elif source_path is not None and fmt in {
                DocumentInputFormat.jpg, DocumentInputFormat.jpeg, DocumentInputFormat.png,
            }:
                from PIL import Image
                with Image.open(source_path) as image:
                    text = _ocr_compliance_image(image, budget)
                ocr_used = True
                complete = bool(text.strip())
                if not complete:
                    warnings.append("image_text_unreadable")
            elif not text and source_path is not None:
                text, ocr_used = _extract_text_with_fallback(source_path, fmt)
        except EvidenceExtractionError:
            raise
        except Exception as exc:
            raise EvidenceExtractionError(
                f"Could not extract reliable text from document {index + 1}. "
                "Use a clearer searchable copy and try again."
            ) from exc
        text = _normalize_text(text)
        if len(text) > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT:
            raise EvidenceExtractionError(
                f"Document {index + 1} exceeds the Compliance searchable-text limit."
            )
        budget.characters += len(text)
        if budget.characters > 25_000_000:
            raise EvidenceExtractionError("The document set exceeds the Compliance searchable-text limit.")
        display_name = Path(
            getattr(document, "_compliance_original_filename", None)
            or document.filename or f"document-{index + 1}"
        ).name
        corpus.append(EvidenceDocument(
            source_document_index=index,
            input_format=fmt,
            source_reference=str(source_path) if source_path is not None else document.filename,
            display_name=display_name,
            file_size_mb=float(document.metadata.file_size_mb),
            checksum_sha256=_source_checksum(source_path, text),
            ocr_used=ocr_used,
            text=text,
            pages=tuple(page for page in pages if page.text.strip()),
            page_count=page_count,
            warnings=tuple(dict.fromkeys(warnings)),
            coverage_complete=complete,
        ))
    return corpus


def collect_evidence_references(
    documents: Sequence[EvidenceDocument],
    *,
    signals: Sequence[str],
    search_mode: str = "substring",
    case_sensitive: bool = False,
    excerpt_window: int = DEFAULT_EXCERPT_WINDOW,
    max_matches_per_signal: int = DEFAULT_MAX_MATCHES_PER_SIGNAL,
) -> list[EvidenceReference]:
    references: list[EvidenceReference] = []
    for signal in signals:
        result = search_signal(
            documents,
            signal=signal,
            search_mode=search_mode,
            case_sensitive=case_sensitive,
            excerpt_window=excerpt_window,
            max_matches=max_matches_per_signal,
        )
        references.extend(match.to_reference() for match in result.matches)
    return _dedupe_references(references)


def search_signal(
    documents: Sequence[EvidenceDocument],
    *,
    signal: str,
    search_mode: str = "substring",
    case_sensitive: bool = False,
    excerpt_window: int = DEFAULT_EXCERPT_WINDOW,
    max_matches: int = DEFAULT_MAX_MATCHES_PER_SIGNAL,
) -> EvidenceSearchResult:
    normalized_signal = signal.strip()
    if not normalized_signal:
        return EvidenceSearchResult(signal=signal, matches=())
    bounded_excerpt_window = max(40, min(int(excerpt_window), 1000))
    bounded_max_matches = max(1, min(int(max_matches), 25))

    matches: list[SignalMatch] = []
    for document in documents:
        if not document.text:
            continue

        if document.pages:
            for page in document.pages:
                page_matches = _find_matches_in_text(
                    page.text,
                    normalized_signal,
                    search_mode=search_mode,
                    case_sensitive=case_sensitive,
                    excerpt_window=bounded_excerpt_window,
                    max_matches=bounded_max_matches,
                )
                for start, end, locator_text, excerpt in page_matches:
                    matches.append(
                        SignalMatch(
                            source_document_index=document.source_document_index,
                            signal=normalized_signal,
                            start=start,
                            end=end,
                            page_number=page.page_number,
                            section_label=_infer_section_label(page.text, start),
                            locator_text=locator_text,
                            excerpt=excerpt,
                        )
                    )
                    if len(matches) >= bounded_max_matches:
                        return EvidenceSearchResult(signal=normalized_signal, matches=tuple(matches))
            # OCR or supplied aggregate text can contain content absent from the
            # native PDF page stream. Only use it as a locator-less fallback when
            # no native page match was found for this document.
            if any(
                match.source_document_index == document.source_document_index
                for match in matches
            ):
                continue

        if document.text:
            document_matches = _find_matches_in_text(
                document.text,
                normalized_signal,
                search_mode=search_mode,
                case_sensitive=case_sensitive,
                excerpt_window=bounded_excerpt_window,
                max_matches=bounded_max_matches,
            )
            for start, end, locator_text, excerpt in document_matches:
                matches.append(
                    SignalMatch(
                        source_document_index=document.source_document_index,
                        signal=normalized_signal,
                        start=start,
                        end=end,
                        page_number=None,
                        section_label=_infer_section_label(document.text, start),
                        locator_text=locator_text,
                        excerpt=excerpt,
                    )
                )
                if len(matches) >= bounded_max_matches:
                    return EvidenceSearchResult(signal=normalized_signal, matches=tuple(matches))

    return EvidenceSearchResult(signal=normalized_signal, matches=tuple(matches))


def get_source_reference(
    input_artifact: DocumentPayload | DocumentSetPayload,
    *,
    source_document_index: int,
) -> Optional[str]:
    documents = [input_artifact] if isinstance(input_artifact, DocumentPayload) else list(input_artifact.documents)
    if source_document_index < 0 or source_document_index >= len(documents):
        return None
    return documents[source_document_index].filename


def _resolve_source_path(source_reference: Optional[str]) -> Optional[Path]:
    if not source_reference:
        return None
    candidate = Path(source_reference)
    return candidate if candidate.is_file() else None


def _extract_pdf_pages(source_path: Path) -> list[PageText]:
    pages: list[PageText] = []
    with fitz.open(source_path) as pdf:
        for index, page in enumerate(pdf, start=1):
            page_text = page.get_text().strip()
            pages.append(PageText(page_number=index, text=page_text))
    return pages


def _extract_text_with_fallback(
    source_path: Path, input_format: DocumentInputFormat
) -> tuple[str, bool]:
    if input_format == DocumentInputFormat.txt:
        return source_path.read_text(encoding="utf-8-sig").strip(), False
    extracted_text, ocr_used = extract_text_by_format(source_path, input_format)
    return extracted_text.strip(), bool(ocr_used)



def _find_matches_in_text(
    text: str, signal: str, *, search_mode: str, case_sensitive: bool,
    excerpt_window: int, max_matches: int,
) -> list[tuple[int, int, str, str]]:
    if not text:
        return []
    mode = search_mode.lower().strip()
    if mode not in {"substring", "exact_phrase", "word", "regex"}:
        raise EvidenceExtractionError(f"Unsupported evidence search mode: {search_mode}.")
    if bounded_regex is None:
        raise EvidenceExtractionError("Bounded Compliance matching is unavailable. Contact your administrator.")
    deadline = _SEARCH_DEADLINE.get()
    remaining = deadline - time.monotonic() if deadline is not None else 30.0
    if remaining <= 0:
        raise EvidenceExtractionError("Compliance evaluation exceeded its processing time limit.")
    timeout = min(remaining, _positive_limit(
        "COMPLIANCE_SEARCH_TIMEOUT_SECONDS", 0.2, maximum=5,
    ))
    expression = signal if mode == "regex" else re.escape(signal)
    if mode in {"exact_phrase", "word"}:
        expression = r"\s+".join(re.escape(part) for part in signal.split())
        if mode == "word":
            expression = rf"(?<!\w){expression}(?!\w)"
    flags = bounded_regex.VERSION0 | (0 if case_sensitive else bounded_regex.IGNORECASE)
    found: list[tuple[int, int, str, str]] = []
    try:
        pattern = bounded_regex.compile(expression, flags)
        for match in pattern.finditer(text, timeout=timeout):
            start, end = match.span()
            locator = text[start:end].strip()
            if end <= start or not locator:
                raise EvidenceExtractionError("A Compliance rule produced an empty evidence match.")
            found.append((start, end, locator, _slice_excerpt(text, start, end, excerpt_window)))
            if len(found) >= max_matches:
                break
    except TimeoutError as exc:
        raise EvidenceExtractionError("Compliance evidence matching exceeded its processing time limit.") from exc
    return found


def _source_checksum(source_path: Optional[Path], normalized_text: str) -> str:
    digest = hashlib.sha256()
    if source_path is not None:
        with source_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    else:
        digest.update(normalized_text.encode("utf-8"))
    return digest.hexdigest()


def _slice_excerpt(text: str, start: int, end: int, excerpt_window: int) -> str:
    left = max(0, start - excerpt_window)
    right = min(len(text), end + excerpt_window)
    excerpt = text[left:right].strip()
    excerpt = re.sub(r"\s+", " ", excerpt)
    if len(excerpt) > 400:
        excerpt = excerpt[:397] + "..."
    return excerpt


def _infer_section_label(text: str, position: int) -> Optional[str]:
    if not text:
        return None

    prefix = text[:position]
    lines = [line.strip() for line in prefix.splitlines() if line.strip()]
    for candidate in reversed(lines[-8:]):
        if _looks_like_heading(candidate):
            return candidate[:200]
    return None


def _looks_like_heading(value: str) -> bool:
    if not value:
        return False
    compact = re.sub(r"\s+", " ", value).strip()
    if len(compact) > 120:
        return False
    if compact.endswith(":"):
        return True
    if compact.isupper() and len(compact) <= 80:
        return True
    words = compact.split()
    titleish = sum(1 for word in words if word[:1].isupper())
    return len(words) <= 10 and titleish >= max(1, len(words) // 2)


def _normalize_text(value: Optional[str]) -> str:
    return value.strip() if isinstance(value, str) else ""


def _dedupe_references(references: Iterable[EvidenceReference]) -> list[EvidenceReference]:
    seen: set[tuple[int, Optional[int], Optional[str], Optional[str], Optional[str]]] = set()
    deduped: list[EvidenceReference] = []
    for reference in references:
        key = (
            reference.source_document_index,
            reference.page_number,
            reference.section_label,
            reference.locator_text,
            reference.excerpt,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reference)
    return deduped



@dataclass
class _ExtractionBudget:
    deadline: float = 0
    pages: int = 0
    ocr_images: int = 0
    characters: int = 0

    def __post_init__(self) -> None:
        self.deadline = time.monotonic() + _positive_limit(
            "COMPLIANCE_EXTRACTION_TIMEOUT_SECONDS", 120, maximum=600,
        )

    def check(self) -> None:
        if time.monotonic() >= self.deadline:
            raise EvidenceExtractionError("Compliance extraction exceeded its processing time limit. Split the document set.")

    def add_pages(self, count: int) -> None:
        self.check()
        per_document = int(_positive_limit("COMPLIANCE_MAX_PAGES_PER_DOCUMENT", 200, maximum=2000))
        per_request = int(_positive_limit("COMPLIANCE_MAX_PAGES_PER_REQUEST", 400, maximum=4000))
        if count > per_document or self.pages + count > per_request:
            raise EvidenceExtractionError("The documents exceed the Compliance page limit. Split the document set.")
        self.pages += count


def _ocr_compliance_image(image, budget: _ExtractionBudget) -> str:
    # A deadline is passed to the existing OCR helper only for Compliance.
    # Existing OCR callers retain their original defaults.
    from backend.src.extraction import extract_ocr_data, ocr_text_from_data, resolve_ocr_lang
    budget.check()
    maximum_images = int(_positive_limit("COMPLIANCE_MAX_OCR_IMAGES", 100, maximum=1000))
    if budget.ocr_images >= maximum_images:
        raise EvidenceExtractionError("The documents exceed the Compliance OCR limit. Split the document set.")
    maximum_pixels = int(_positive_limit("COMPLIANCE_MAX_IMAGE_PIXELS", 20_000_000, maximum=80_000_000))
    width, height = image.size
    if width < 1 or height < 1 or width * height > maximum_pixels:
        raise EvidenceExtractionError("An image exceeds the Compliance pixel limit. Upload a smaller readable image.")
    budget.ocr_images += 1
    timeout = min(
        _positive_limit("COMPLIANCE_OCR_TIMEOUT_SECONDS", 20, maximum=120),
        max(0.01, (budget.deadline - time.monotonic()) / 2),
    )
    data = extract_ocr_data(
        image, ocr_lang=resolve_ocr_lang(), timeout_seconds=timeout,
    )
    budget.check()
    text = ocr_text_from_data(data).strip()
    if len(text) > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT:
        raise EvidenceExtractionError("OCR output exceeds the Compliance searchable-text limit.")
    return text


def _extract_compliance_pdf(source: Path, budget: _ExtractionBudget):
    from PIL import Image
    pages: list[PageText] = []
    warnings: list[str] = []
    used_ocr = False
    complete = True
    characters = 0
    with fitz.open(source) as pdf:
        if pdf.needs_pass:
            raise EvidenceExtractionError("Use an unlocked searchable PDF for Compliance.")
        if pdf.get_sigflags() > 0:
            warnings.append("pdf_signature_original_required")
        budget.add_pages(len(pdf))
        for number, page in enumerate(pdf, start=1):
            budget.check()
            native = page.get_text().strip()
            images = page.get_images(full=True)
            # Blank pages are not failed OCR. Image-bearing pages are rendered
            # even when a header or another native text fragment exists.
            needs_ocr = bool(images) or (not native and bool(page.get_drawings()))
            text = native
            if needs_ocr:
                maximum_pixels = _positive_limit("COMPLIANCE_MAX_IMAGE_PIXELS", 20_000_000, maximum=80_000_000)
                area = max(1.0, page.rect.width * page.rect.height)
                zoom = min(3.0, math.sqrt(maximum_pixels / area) * 0.99)
                if zoom < 1.0:
                    raise EvidenceExtractionError("A PDF page exceeds the Compliance rendering limit.")
                pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                try:
                    extracted = _ocr_compliance_image(image, budget)
                finally:
                    image.close()
                used_ocr = True
                if extracted and extracted not in native:
                    text = "\n".join(item for item in (native, extracted) if item)
                elif not extracted:
                    complete = False
                    warnings.append(f"pdf_image_text_unreadable:{number}")
            characters += len(text)
            if characters > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT:
                raise EvidenceExtractionError("A PDF exceeds the Compliance searchable-text limit.")
            pages.append(PageText(page_number=number, text=text))
    return pages, used_ocr, warnings, complete


def _extract_compliance_docx(source: Path, budget: _ExtractionBudget):
    import docx
    from PIL import Image, UnidentifiedImageError
    from backend.src.extraction import _iter_docx_paragraphs
    budget.check()
    document = docx.Document(source)
    chunks: list[str] = []
    characters = 0
    for paragraph in _iter_docx_paragraphs(document):
        budget.check()
        if paragraph.text.strip():
            chunks.append(paragraph.text)
            characters += len(paragraph.text)
        if characters > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT:
            raise EvidenceExtractionError("A DOCX exceeds the Compliance searchable-text limit.")
    warnings: list[str] = []
    used_ocr = False
    complete = True
    for part in document.part.package.parts:
        budget.check()
        name = str(part.partname)
        if name in {"/word/footnotes.xml", "/word/endnotes.xml"}:
            blob = part.blob
            if len(blob) > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT or b"<!DOCTYPE" in blob.upper() or b"<!ENTITY" in blob.upper():
                raise EvidenceExtractionError("A DOCX note part exceeds the Compliance safety limit.")
            root = ElementTree.fromstring(blob)
            chunks.append("\n".join(
                node.text for node in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")
                if node.text
            ))
        elif str(part.content_type).startswith("image/") and name.startswith("/word/"):
            try:
                with Image.open(io.BytesIO(part.blob)) as image:
                    extracted = _ocr_compliance_image(image, budget)
                used_ocr = True
                if extracted:
                    chunks.append(extracted)
                else:
                    complete = False
                    warnings.append("docx_image_text_unreadable")
            except (UnidentifiedImageError, OSError):
                complete = False
                warnings.append("docx_image_format_unassessed")
        elif name.startswith("/word/charts/") and name.endswith(".xml"):
            complete = False
            warnings.append("docx_chart_unassessed")
        if sum(len(chunk) for chunk in chunks) > MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT:
            raise EvidenceExtractionError("A DOCX exceeds the Compliance searchable-text limit.")
    return "\n".join(chunks), used_ocr, warnings, complete


__all__ = [
    "DEFAULT_EXCERPT_WINDOW",
    "DEFAULT_MAX_MATCHES_PER_SIGNAL",
    "MAX_SEARCHABLE_CHARACTERS_PER_DOCUMENT",
    "EvidenceDocument",
    "EvidenceExtractionError",
    "EvidenceSearchResult",
    "PageText",
    "SignalMatch",
    "build_evidence_documents",
    "collect_evidence_references",
    "get_source_reference",
    "search_signal",
]
