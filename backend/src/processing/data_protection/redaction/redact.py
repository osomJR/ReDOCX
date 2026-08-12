from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
from typing import Any, Mapping, Optional, Sequence
import uuid

import docx
import fitz  # PyMuPDF
import pytesseract
from PIL import Image, ImageDraw

from backend.src.extraction import OCR_CONFIG, preprocess_for_ocr, resolve_ocr_lang
from backend.src.processing.data_protection.client import (
    DEFAULT_DLP_LOCATION,
    DEFAULT_MIN_LIKELIHOOD,
    DetectionCandidate,
    GoogleSDPClient,
    TextFinding,
    build_google_sdp_client,
    id_document_field_findings,
    inspect_sensitive_image,
    inspect_sensitive_text,
    is_id_document_payload,
    merge_overlapping_findings,
)
from backend.src.schema import (
    AnalyzerRequest,
    AnalyzerResponse,
    DocumentInputFormat,
    DocumentPayload,
    FeatureType,
    HumanReviewRequirement,
    RedactionRequest,
)
from backend.src.validation import build_document_file_result, validate_analyzer_request, validate_analyzer_response

DEFAULT_PDF_RENDER_SCALE = 2.0
ID_DOCUMENT_FALLBACK_RENDER_SCALE = 4.0
ID_DOCUMENT_OCR_CONFIG = "--oem 3 --psm 11"
ID_DOCUMENT_OCR_TARGET_WIDTH = 1000
DEFAULT_BLACK = (0, 0, 0)
LANCZOS_RESAMPLING = getattr(Image, "Resampling", Image).LANCZOS
PDF_VISIBILITY_RENDER_SCALE = 2.0
PDF_VISIBLE_PIXEL_THRESHOLD = 245
VISUAL_SIGNATURE_QUOTE = "visual signature"
_PDF_REVIEWABLE_METADATA_KEYS = ("author", "title", "subject", "keywords")
_GENERIC_METADATA_AUTHORS = {"admin", "administrator", "anonymous", "unknown", "user"}

_SIGNATURE_ANCHOR_RE = re.compile(
    r"(?i)\b(?:authorized\s+signature|customer\s+signature|applicant\s+signature|"
    r"holder(?:'s)?\s+signature|signature|signed\s+by|signatory|"
    r"signature\s+autoris[eé]e|sign[eé]\s+par)\b"
)
_SIGNATURE_ROLE_RE = re.compile(
    r"(?i)\b(?:registrar|registar|istrar|piatrer|vice\s+chancellor|chancellor|authorized\s+officer|"
    r"approving\s+officer|director)\b"
)

_IMAGE_OUTPUT_MAP: dict[DocumentInputFormat, str] = {
    DocumentInputFormat.jpg: "JPEG",
    DocumentInputFormat.jpeg: "JPEG",
    DocumentInputFormat.png: "PNG",
}

_FILE_OUTPUT_MAP = {
    DocumentInputFormat.pdf: "pdf",
    DocumentInputFormat.docx: "docx",
    DocumentInputFormat.jpg: "jpg",
    DocumentInputFormat.jpeg: "jpeg",
    DocumentInputFormat.png: "png",
}

_SUPPORTED_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.jpg,
    DocumentInputFormat.jpeg,
    DocumentInputFormat.png,
}


@dataclass(frozen=True)
class OCRWord:
    text: str
    bbox: tuple[int, int, int, int]
    start: int
    end: int
    line_id: tuple[int, int, int]
    character_boxes: tuple[tuple[int, int, int, int], ...] = ()


@dataclass(frozen=True)
class RunSpan:
    start: int
    end: int
    text: str
    rpr_xml: Any


@dataclass(frozen=True)
class PDFWord:
    start: int
    end: int
    text: str
    rect: fitz.Rect
    block_no: int
    line_no: int
    word_no: int


@dataclass(frozen=True)
class OCRImageSurface:
    image: Image.Image
    page_rect: fitz.Rect


def redact_text_with_findings(text: str, findings: Sequence[TextFinding]) -> str:
    result = text
    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        replacement = "█" * max(1, finding.end - finding.start)
        result = result[:finding.start] + replacement + result[finding.end:]
    return result


def _ocr_words_from_image(
    image: Image.Image,
    *,
    ocr_lang: Optional[str] = None,
    sparse_text: bool = False,
    include_character_boxes: bool = False,
) -> tuple[str, list[OCRWord]]:
    source = image.convert("RGB")
    coordinate_scale = 1.0

    if sparse_text:
        coordinate_scale = min(
            4.0,
            max(1.0, ID_DOCUMENT_OCR_TARGET_WIDTH / max(1, source.width)),
        )
        if coordinate_scale > 1.0:
            source = source.resize(
                (
                    max(1, round(source.width * coordinate_scale)),
                    max(1, round(source.height * coordinate_scale)),
                ),
                LANCZOS_RESAMPLING,
            )
        processed = source
        ocr_config = ID_DOCUMENT_OCR_CONFIG
    else:
        processed = preprocess_for_ocr(source)
        ocr_config = OCR_CONFIG

    data = pytesseract.image_to_data(
        processed,
        lang=ocr_lang or resolve_ocr_lang(),
        config=ocr_config,
        output_type=pytesseract.Output.DICT,
    )

    words: list[OCRWord] = []
    text_parts: list[str] = []
    cursor = 0
    texts = data.get("text", [])
    lefts = data.get("left", [])
    tops = data.get("top", [])
    widths = data.get("width", [])
    heights = data.get("height", [])
    block_numbers = data.get("block_num", [])
    paragraph_numbers = data.get("par_num", [])
    line_numbers = data.get("line_num", [])
    previous_line_id: tuple[int, int, int] | None = None

    for idx, raw in enumerate(texts):
        token = (raw or "").strip()
        if not token:
            continue
        line_id = (
            int(block_numbers[idx]) if idx < len(block_numbers) else 0,
            int(paragraph_numbers[idx]) if idx < len(paragraph_numbers) else 0,
            int(line_numbers[idx]) if idx < len(line_numbers) else 0,
        )
        if text_parts:
            separator = "\n" if previous_line_id != line_id else " "
            text_parts.append(separator)
            cursor += len(separator)
        start = cursor
        text_parts.append(token)
        cursor += len(token)
        end = cursor
        bbox = (
            round(int(lefts[idx]) / coordinate_scale),
            round(int(tops[idx]) / coordinate_scale),
            round((int(lefts[idx]) + int(widths[idx])) / coordinate_scale),
            round((int(tops[idx]) + int(heights[idx])) / coordinate_scale),
        )
        words.append(
            OCRWord(
                text=token,
                bbox=bbox,
                start=start,
                end=end,
                line_id=line_id,
            )
        )
        previous_line_id = line_id
    if include_character_boxes and words:
        words = _attach_ocr_character_boxes(
            image=processed,
            words=words,
            ocr_lang=ocr_lang or resolve_ocr_lang(),
            ocr_config=ocr_config,
            coordinate_scale=coordinate_scale,
        )
    return "".join(text_parts), words


def _attach_ocr_character_boxes(
    *,
    image: Image.Image,
    words: Sequence[OCRWord],
    ocr_lang: str,
    ocr_config: str,
    coordinate_scale: float = 1.0,
) -> list[OCRWord]:
    """Attach exact Tesseract symbol boxes when they align with an OCR word."""
    try:
        raw_boxes = pytesseract.image_to_boxes(
            image,
            lang=ocr_lang,
            config=ocr_config,
        )
    except Exception:
        return list(words)

    symbols: list[tuple[str, tuple[int, int, int, int]]] = []
    for raw_line in str(raw_boxes or "").splitlines():
        fields = raw_line.rsplit(maxsplit=5)
        if len(fields) < 6:
            continue
        symbol = fields[0]
        try:
            left, bottom, right, top = (int(value) for value in fields[1:5])
        except ValueError:
            continue
        box = (
            round(left / coordinate_scale),
            round((image.height - top) / coordinate_scale),
            round(right / coordinate_scale),
            round((image.height - bottom) / coordinate_scale),
        )
        if symbol and box[2] > box[0] and box[3] > box[1]:
            symbols.append((symbol, box))

    attached: list[OCRWord] = []
    for word in words:
        x0, y0, x1, y1 = word.bbox
        candidates = [
            (symbol, box)
            for symbol, box in symbols
            if x0 - 3 <= (box[0] + box[2]) / 2 <= x1 + 3
            and y0 - 3 <= (box[1] + box[3]) / 2 <= y1 + 3
        ]
        candidates.sort(key=lambda item: (item[1][0], item[1][1]))
        character_boxes = (
            tuple(box for _symbol, box in candidates)
            if len(candidates) == len(word.text)
            else ()
        )
        attached.append(
            OCRWord(
                text=word.text,
                bbox=word.bbox,
                start=word.start,
                end=word.end,
                line_id=word.line_id,
                character_boxes=character_boxes,
            )
        )
    return attached


def _normalize_text_for_compare(value: str) -> str:
    import re

    return re.sub(r"\s+", " ", value.strip()).casefold()


def _normalize_manual_redactions(values: Optional[Sequence[str]] = None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values or []:
        term = str(value).strip()
        if not term:
            continue
        key = _normalize_text_for_compare(term)
        if key in seen:
            continue
        cleaned.append(term)
        seen.add(key)

    return cleaned


def _literal_text_findings(
    text: str,
    *,
    custom_redactions: Optional[Sequence[str]] = None,
    review_exclusions: Sequence[str] = (),
) -> list[TextFinding]:
    import re

    terms = _normalize_manual_redactions(custom_redactions)
    if not text or not terms:
        return []

    excluded = {_normalize_text_for_compare(item) for item in review_exclusions if item and item.strip()}
    findings: list[TextFinding] = []

    for term in terms:
        if _normalize_text_for_compare(term) in excluded:
            continue

        pattern = re.compile(rf"(?=({re.escape(term)}))", re.IGNORECASE)
        for match in pattern.finditer(text):
            start, end = match.span(1)
            quote = text[start:end]
            if not quote:
                continue
            findings.append(
                TextFinding(
                    start=start,
                    end=end,
                    quote=quote,
                    label="custom_redaction",
                    source="manual",
                )
            )

    return findings


def _manual_candidates_from_text(
    text: str,
    *,
    custom_redactions: Optional[Sequence[str]] = None,
    review_exclusions: Sequence[str] = (),
) -> list[DetectionCandidate]:
    terms = _normalize_manual_redactions(custom_redactions)
    if not text or not terms:
        return []

    excluded = {_normalize_text_for_compare(item) for item in review_exclusions if item and item.strip()}
    candidates: list[DetectionCandidate] = []

    for term in terms:
        if _normalize_text_for_compare(term) in excluded:
            continue

        findings = _literal_text_findings(
            text,
            custom_redactions=[term],
            review_exclusions=review_exclusions,
        )
        if not findings:
            continue

        candidates.append(
            DetectionCandidate(
                label="custom_redaction",
                quote=term,
                occurrences=len(findings),
                source="manual",
            )
        )

    return candidates


def _is_id_document_payload(payload: RedactionRequest) -> bool:
    return is_id_document_payload(payload)


def _id_document_field_findings(
    text: str,
    *,
    payload: RedactionRequest,
) -> list[TextFinding]:
    return id_document_field_findings(text, payload=payload)


def _redaction_findings(
    *,
    sdp: GoogleSDPClient,
    text: str,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
) -> list[TextFinding]:
    detected = inspect_sensitive_text(
        sdp=sdp,
        text=text,
        targets=payload.target_data,
        review_exclusions=payload.review_exclusions,
    )
    if _is_id_document_payload(payload):
        # Generic PERSON_NAME detection is noisy on compact multilingual ID
        # labels. Use the document's explicit field labels instead.
        detected = [
            finding
            for finding in detected
            if finding.label not in {"name", "contact_address"}
        ]
        detected.extend(_id_document_field_findings(text, payload=payload))
    manual = _literal_text_findings(
        text,
        custom_redactions=custom_redactions,
        review_exclusions=payload.review_exclusions,
    )
    return merge_overlapping_findings([*detected, *manual], original_text=text)


def _ocr_word_overlap_box(
    word: OCRWord,
    finding: TextFinding,
) -> tuple[int, int, int, int] | None:
    overlap_start = max(word.start, finding.start)
    overlap_end = min(word.end, finding.end)
    if overlap_end <= overlap_start:
        return None

    x0, y0, x1, y1 = word.bbox
    character_count = max(1, word.end - word.start)
    if overlap_start == word.start and overlap_end == word.end:
        return word.bbox

    local_start = overlap_start - word.start
    local_end = overlap_end - word.start
    if len(word.character_boxes) == character_count:
        selected = word.character_boxes[local_start:local_end]
        if selected:
            return (
                min(box[0] for box in selected),
                min(box[1] for box in selected),
                max(box[2] for box in selected),
                max(box[3] for box in selected),
            )

    start_ratio = local_start / character_count
    end_ratio = local_end / character_count
    clipped_x0 = round(x0 + ((x1 - x0) * start_ratio))
    clipped_x1 = round(x0 + ((x1 - x0) * end_ratio))
    if clipped_x1 <= clipped_x0:
        clipped_x1 = min(x1, clipped_x0 + 1)
    return clipped_x0, y0, clipped_x1, y1


def _fallback_ocr_words_for_quote(
    quote: str,
    words: Sequence[OCRWord],
) -> list[OCRWord]:
    needle = _normalize_text_for_compare(quote)
    if not needle:
        return []

    # The window scales with the requested literal/name instead of imposing a
    # fixed word limit. This keeps extremely long names mappable while still
    # terminating as soon as a candidate grows beyond the needle.
    maximum_window = max(1, len(needle.split()) + 2)
    for start_index in range(len(words)):
        buffered: list[OCRWord] = []
        for end_index in range(start_index, min(len(words), start_index + maximum_window)):
            buffered.append(words[end_index])
            candidate = _normalize_text_for_compare(
                " ".join(item.text for item in buffered)
            )
            if candidate == needle:
                return buffered
            if len(candidate) > len(needle):
                break
    return []


def _boxes_for_text_spans(findings: Sequence[TextFinding], words: Sequence[OCRWord]) -> list[tuple[int, int, int, int]]:
    boxes: list[tuple[int, int, int, int]] = []
    for finding in findings:
        matched = [w for w in words if not (w.end <= finding.start or w.start >= finding.end)]
        use_full_word_boxes = False
        if not matched:
            matched = _fallback_ocr_words_for_quote(finding.quote, words)
            use_full_word_boxes = True
        if not matched:
            continue

        # Never bridge unrelated OCR lines with one large rectangle. Addresses
        # and other multi-line findings are redacted line-by-line.
        by_line: dict[tuple[int, int, int], list[tuple[int, int, int, int]]] = {}
        for word in matched:
            box = word.bbox if use_full_word_boxes else _ocr_word_overlap_box(word, finding)
            if box is not None:
                by_line.setdefault(word.line_id, []).append(box)

        for line_boxes in by_line.values():
            x0 = min(box[0] for box in line_boxes)
            y0 = min(box[1] for box in line_boxes)
            x1 = max(box[2] for box in line_boxes)
            y1 = max(box[3] for box in line_boxes)
            if x1 > x0 and y1 > y0:
                boxes.append((x0, y0, x1, y1))

    unique: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for box in boxes:
        if box not in seen:
            unique.append(box)
            seen.add(box)
    return unique


def draw_redaction_boxes(image: Image.Image, boxes: Sequence[tuple[int, int, int, int]]) -> Image.Image:
    out = image.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    for box in boxes:
        draw.rectangle(box, fill=DEFAULT_BLACK)
    return out


def _signature_target_selected(payload: RedactionRequest) -> bool:
    return any(
        str(getattr(target, "value", target)) == "signature"
        for target in payload.target_data
    )


def _visual_signature_is_excluded(payload: RedactionRequest) -> bool:
    excluded = {
        _normalize_text_for_compare(item)
        for item in payload.review_exclusions
        if item and item.strip()
    }
    return _normalize_text_for_compare(VISUAL_SIGNATURE_QUOTE) in excluded


def _clip_pixel_box(
    box: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    padding: int = 0,
) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = box
    clipped = (
        max(0, min(width, x0 - padding)),
        max(0, min(height, y0 - padding)),
        max(0, min(width, x1 + padding)),
        max(0, min(height, y1 + padding)),
    )
    return clipped if clipped[2] > clipped[0] and clipped[3] > clipped[1] else None


def _dark_content_bbox(
    image: Image.Image,
    search_box: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    clipped = _clip_pixel_box(search_box, width=image.width, height=image.height)
    if clipped is None:
        return None

    grayscale = image.crop(clipped).convert("L")
    ink = grayscale.point(lambda value: 255 if value < 232 else 0)
    local = ink.getbbox()
    if local is None:
        return None

    left, top, right, bottom = local
    histogram = ink.histogram()
    ink_pixels = sum(histogram[1:])
    if right - left < 5 or bottom - top < 3 or ink_pixels < 16:
        return None
    return (
        clipped[0] + left,
        clipped[1] + top,
        clipped[0] + right,
        clipped[1] + bottom,
    )


def _ocr_signature_boxes(
    image: Image.Image,
    words: Sequence[OCRWord],
) -> list[tuple[int, int, int, int]]:
    lines: dict[tuple[int, int, int], list[OCRWord]] = {}
    for word in words:
        lines.setdefault(word.line_id, []).append(word)

    boxes: list[tuple[int, int, int, int]] = []
    for line_words in lines.values():
        ordered = sorted(line_words, key=lambda word: (word.bbox[0], word.start))
        line_text = " ".join(word.text for word in ordered)
        explicit_anchor = bool(_SIGNATURE_ANCHOR_RE.search(line_text))
        role_anchor = bool(
            _SIGNATURE_ROLE_RE.search(line_text)
            and min(word.bbox[1] for word in ordered) >= image.height * 0.45
        )
        if not explicit_anchor and not role_anchor:
            continue

        anchor = (
            min(word.bbox[0] for word in ordered),
            min(word.bbox[1] for word in ordered),
            max(word.bbox[2] for word in ordered),
            max(word.bbox[3] for word in ordered),
        )
        anchor_height = max(8, anchor[3] - anchor[1])
        above = (
            max(0, anchor[0] - anchor_height * 2),
            max(0, anchor[1] - max(90, anchor_height * 8)),
            min(image.width, anchor[2] + anchor_height * 3),
            max(0, anchor[1] - 2),
        )
        search_boxes = [above]
        if explicit_anchor:
            search_boxes.extend(
                [
                    (
                        anchor[2] + 2,
                        max(0, anchor[1] - anchor_height),
                        min(image.width, anchor[2] + max(180, anchor_height * 18)),
                        min(image.height, anchor[3] + max(45, anchor_height * 3)),
                    ),
                    (
                        max(0, anchor[0] - anchor_height),
                        anchor[3] + 2,
                        min(image.width, anchor[0] + max(220, anchor_height * 22)),
                        min(image.height, anchor[3] + max(70, anchor_height * 6)),
                    ),
                ]
            )

        for search_box in search_boxes:
            content_box = _dark_content_bbox(image, search_box)
            if content_box is not None:
                boxes.append(content_box)

    unique: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for box in boxes:
        clipped = _clip_pixel_box(
            box,
            width=image.width,
            height=image.height,
            padding=3,
        )
        if clipped is not None and clipped not in seen:
            unique.append(clipped)
            seen.add(clipped)
    return unique


def _google_signature_boxes(
    *,
    image: Image.Image,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
) -> list[tuple[int, int, int, int]]:
    if not _signature_target_selected(payload) or _visual_signature_is_excluded(payload):
        return []

    encoded = BytesIO()
    image.convert("RGB").save(encoded, format="JPEG", quality=90)
    findings = inspect_sensitive_image(
        sdp=sdp,
        image_bytes=encoded.getvalue(),
        image_type="IMAGE_JPEG",
        targets=[target for target in payload.target_data if str(getattr(target, "value", target)) == "signature"],
        review_exclusions=payload.review_exclusions,
        min_likelihood=getattr(sdp, "min_likelihood", DEFAULT_MIN_LIKELIHOOD),
    )
    return [finding.bbox for finding in findings if finding.label == "signature"]


def _signature_boxes_for_image(
    *,
    image: Image.Image,
    words: Sequence[OCRWord],
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
) -> list[tuple[int, int, int, int]]:
    if not _signature_target_selected(payload) or _visual_signature_is_excluded(payload):
        return []
    boxes = [*_ocr_signature_boxes(image, words), *_google_signature_boxes(image=image, sdp=sdp, payload=payload)]
    unique: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for box in boxes:
        clipped = _clip_pixel_box(box, width=image.width, height=image.height, padding=2)
        if clipped is not None and clipped not in seen:
            unique.append(clipped)
            seen.add(clipped)
    return unique


def _render_page_surface_for_signatures(page: fitz.Page) -> OCRImageSurface:
    scale = min(
        4.0,
        max(2.5, ID_DOCUMENT_OCR_TARGET_WIDTH / max(1.0, page.rect.width)),
    )
    pix = page.get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
        colorspace=fitz.csRGB,
        annots=False,
    )
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return OCRImageSurface(image=image, page_rect=fitz.Rect(page.rect))


def _visual_signature_occurrence_count(
    *,
    source_path: Path,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    ocr_lang: str,
) -> int:
    if not _signature_target_selected(payload) or _visual_signature_is_excluded(payload):
        return 0

    suffix = source_path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        image = Image.open(source_path).convert("RGB")
        _text, words = _ocr_words_from_image(image, ocr_lang=ocr_lang, sparse_text=True)
        return len(_signature_boxes_for_image(image=image, words=words, sdp=sdp, payload=payload))

    if suffix != ".pdf":
        return 0

    count = 0
    with fitz.open(source_path) as document:
        for page in document:
            surface = _render_page_surface_for_signatures(page)
            _text, words = _ocr_words_from_image(
                surface.image,
                ocr_lang=ocr_lang,
                sparse_text=True,
            )
            count += len(
                _signature_boxes_for_image(
                    image=surface.image,
                    words=words,
                    sdp=sdp,
                    payload=payload,
                )
            )
    return count


def _primary_page_image_surface(doc: fitz.Document, page: fitz.Page) -> OCRImageSurface | None:
    candidates: list[tuple[int, float, int, int, fitz.Rect]] = []
    seen: set[tuple[int, float, float, float, float]] = set()

    for image_info in page.get_images(full=True):
        xref = int(image_info[0])
        width = int(image_info[2])
        height = int(image_info[3])
        if xref <= 0 or width < 160 or height < 90:
            continue
        for placement in page.get_image_rects(xref):
            rect = fitz.Rect(placement) & page.rect
            key = (
                xref,
                round(rect.x0, 3),
                round(rect.y0, 3),
                round(rect.x1, 3),
                round(rect.y1, 3),
            )
            if key in seen or rect.is_empty or rect.is_infinite:
                continue
            seen.add(key)
            candidates.append((width * height, rect.get_area(), width, height, rect))

    if not candidates:
        return None

    _pixel_area, _page_area, source_width, source_height, rect = max(
        candidates,
        key=lambda item: (item[0], item[1]),
    )
    del doc
    del source_width, source_height
    render_scale = 2.0
    pix = page.get_pixmap(
        matrix=fitz.Matrix(render_scale, render_scale),
        clip=rect,
        alpha=False,
        colorspace=fitz.csRGB,
        annots=False,
    )
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return OCRImageSurface(image=image, page_rect=fitz.Rect(rect))


def _id_document_text_from_source(
    source_path: str | Path,
    *,
    ocr_lang: Optional[str] = None,
) -> str:
    source = Path(source_path)
    suffix = source.suffix.lower()
    chunks: list[str] = []

    if suffix == ".pdf":
        with fitz.open(source) as doc:
            for page in doc:
                native_text, _native_words = _page_words_with_offsets(page)
                native_text = native_text.strip()
                if native_text:
                    chunks.append(native_text)
                    continue
                surface = _primary_page_image_surface(doc, page)
                if surface is not None:
                    text, _words = _ocr_words_from_image(
                        surface.image,
                        ocr_lang=ocr_lang,
                        sparse_text=True,
                    )
                else:
                    pix = page.get_pixmap(
                        matrix=fitz.Matrix(
                            ID_DOCUMENT_FALLBACK_RENDER_SCALE,
                            ID_DOCUMENT_FALLBACK_RENDER_SCALE,
                        )
                    )
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    text, _words = _ocr_words_from_image(
                        image,
                        ocr_lang=ocr_lang,
                        sparse_text=True,
                    )
                if text.strip():
                    chunks.append(text)
        return "\n".join(chunks).strip()

    if suffix in {".jpg", ".jpeg", ".png"}:
        image = Image.open(source).convert("RGB")
        text, _words = _ocr_words_from_image(
            image,
            ocr_lang=ocr_lang,
            sparse_text=True,
        )
        return text.strip()

    return ""


def _image_box_to_page_rect(
    box: tuple[int, int, int, int],
    *,
    image_size: tuple[int, int],
    page_rect: fitz.Rect,
) -> fitz.Rect | None:
    image_width, image_height = image_size
    x0, y0, x1, y1 = box
    if image_width <= 0 or image_height <= 0 or x1 <= x0 or y1 <= y0:
        return None

    box_area_ratio = ((x1 - x0) * (y1 - y0)) / (image_width * image_height)
    if box_area_ratio > 0.25:
        return None

    x0 = max(0, min(image_width, x0))
    x1 = max(0, min(image_width, x1))
    y0 = max(0, min(image_height, y0))
    y1 = max(0, min(image_height, y1))
    if x1 <= x0 or y1 <= y0:
        return None

    return fitz.Rect(
        page_rect.x0 + (x0 / image_width) * page_rect.width,
        page_rect.y0 + (y0 / image_height) * page_rect.height,
        page_rect.x0 + (x1 / image_width) * page_rect.width,
        page_rect.y0 + (y1 / image_height) * page_rect.height,
    )


def _iter_table_paragraphs(table: Any):
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                yield paragraph
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested)


def _iter_document_paragraphs(document: docx.Document):
    for paragraph in document.paragraphs:
        yield paragraph
    for table in document.tables:
        yield from _iter_table_paragraphs(table)
    for section in document.sections:
        for paragraph in section.header.paragraphs:
            yield paragraph
        for table in section.header.tables:
            yield from _iter_table_paragraphs(table)
        for paragraph in section.footer.paragraphs:
            yield paragraph
        for table in section.footer.tables:
            yield from _iter_table_paragraphs(table)


def _ensure_redaction_request(request: AnalyzerRequest) -> tuple[DocumentPayload, RedactionRequest]:
    if request.action != FeatureType.redact:
        raise ValueError("redact.py only handles action='redact'.")
    if not isinstance(request.input, DocumentPayload):
        raise ValueError("redact requires DocumentPayload input.")
    if not isinstance(request.payload, RedactionRequest):
        raise ValueError("redact requires RedactionRequest payload.")
    if request.input.metadata.input_format not in _SUPPORTED_INPUTS:
        raise ValueError("redact only supports pdf, docx, jpg, jpeg, png.")
    if request.policy.structure_preservation is not True:
        raise ValueError("structure_preservation must be True for redact.")
    return request.input, request.payload


def _output_path(source_path: Path, output_dir: Path, suffix: str = "_redacted") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{source_path.stem}{suffix}{source_path.suffix.lower()}"


def _build_redaction_response(*, request: AnalyzerRequest, output_path: Path) -> AnalyzerResponse:
    input_payload, _ = _ensure_redaction_request(request)
    result = build_document_file_result(
        filename=output_path.name,
        output_format=_FILE_OUTPUT_MAP[input_payload.metadata.input_format],
        file_size_mb=round(output_path.stat().st_size / (1024 * 1024), 4),
        algorithm_version="google-sdp-redact-v1",
    )
    response = AnalyzerResponse(
        action=FeatureType.redact,
        input_format=input_payload.metadata.input_format,
        policy=request.policy,
        system_language=request.system_language,
        result=result,
        human_review=HumanReviewRequirement(),
    )
    return validate_analyzer_response(response, request=request)


def _resolve_sdp(
    *,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> GoogleSDPClient:
    if sdp is not None:
        return sdp
    if not project_id:
        raise ValueError("Provide either sdp or project_id.")
    return build_google_sdp_client(
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )


def preview_redaction_candidates(
    request: AnalyzerRequest | Mapping[str, Any],
    *,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> list[DetectionCandidate]:
    req = validate_analyzer_request(request)
    input_payload, payload = _ensure_redaction_request(req)
    if not input_payload.text:
        raise ValueError(
            "preview_redaction_candidates requires extracted document text. "
            "Build the request with extraction.py before previewing."
        )
    resolved = _resolve_sdp(
        sdp=sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )
    review_text = input_payload.text
    if _is_id_document_payload(payload) and input_payload.filename:
        source = Path(str(input_payload.filename))
        if source.exists() and source.is_file():
            id_document_text = _id_document_text_from_source(
                source,
                ocr_lang=resolve_ocr_lang(),
            )
            if id_document_text:
                review_text = id_document_text

    grouped: dict[tuple[str, str, str], int] = {}
    for finding in _redaction_findings(
        sdp=resolved,
        text=review_text,
        payload=payload,
        custom_redactions=custom_redactions,
    ):
        key = (finding.label, finding.quote, finding.source)
        grouped[key] = grouped.get(key, 0) + 1

    if input_payload.filename:
        source = Path(str(input_payload.filename))
        if source.exists() and source.is_file():
            if source.suffix.lower() == ".pdf":
                with fitz.open(source) as document:
                    for _key, _value, metadata_findings in _pdf_metadata_findings(
                        document=document,
                        sdp=resolved,
                        payload=payload,
                        custom_redactions=custom_redactions,
                    ):
                        for finding in metadata_findings:
                            candidate_key = (
                                finding.label,
                                finding.quote,
                                finding.source,
                            )
                            grouped[candidate_key] = grouped.get(candidate_key, 0) + 1
            signature_occurrences = _visual_signature_occurrence_count(
                source_path=source,
                sdp=resolved,
                payload=payload,
                ocr_lang=resolve_ocr_lang(),
            )
            if signature_occurrences:
                grouped[("signature", VISUAL_SIGNATURE_QUOTE, "visual_signature")] = (
                    signature_occurrences
                )

    return [
        DetectionCandidate(
            label=label,
            quote=quote,
            occurrences=occurrences,
            source=source,
        )
        for (label, quote, source), occurrences in sorted(
            grouped.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
    ]


def _paragraph_run_spans(paragraph: Any) -> tuple[str, list[RunSpan]]:
    spans: list[RunSpan] = []
    parts: list[str] = []
    cursor = 0

    for run in paragraph.runs:
        text = run.text or ""
        if not text:
            continue
        start = cursor
        end = start + len(text)
        parts.append(text)
        spans.append(
            RunSpan(
                start=start,
                end=end,
                text=text,
                rpr_xml=deepcopy(run._r.rPr) if run._r.rPr is not None else None,
            )
        )
        cursor = end

    return "".join(parts), spans


def _copy_rpr(dst_run: Any, rpr_xml: Any) -> None:
    if dst_run._r.rPr is not None:
        dst_run._r.remove(dst_run._r.rPr)
    if rpr_xml is not None:
        dst_run._r.insert(0, deepcopy(rpr_xml))


def _rewrite_paragraph_from_fragments(paragraph: Any, fragments: list[tuple[Any, str]]) -> None:
    for run in list(paragraph.runs):
        paragraph._p.remove(run._r)

    for rpr_xml, text in fragments:
        if not text:
            continue
        new_run = paragraph.add_run()
        _copy_rpr(new_run, rpr_xml)
        new_run.text = text


def _run_span_for_offset(spans: Sequence[RunSpan], offset: int) -> RunSpan:
    for span in spans:
        if span.start <= offset < span.end:
            return span
    return spans[-1]


def redact_paragraph_runs(paragraph: Any, findings: Sequence[TextFinding]) -> None:
    full_text, spans = _paragraph_run_spans(paragraph)
    if not full_text or not spans or not findings:
        return

    merged = merge_overlapping_findings(findings, original_text=full_text)

    boundaries = {0, len(full_text)}
    for span in spans:
        boundaries.add(span.start)
        boundaries.add(span.end)
    for finding in merged:
        boundaries.add(finding.start)
        boundaries.add(finding.end)

    cuts = sorted(boundaries)
    fragments: list[tuple[Any, str]] = []

    for a, b in zip(cuts, cuts[1:]):
        if a == b:
            continue

        owner = _run_span_for_offset(spans, a)
        covered = any(f.start <= a and b <= f.end for f in merged)

        if covered:
            text = "█" * (b - a)
        else:
            text = full_text[a:b]

        fragments.append((owner.rpr_xml, text))

    _rewrite_paragraph_from_fragments(paragraph, fragments)


def _page_words_with_offsets(page: fitz.Page) -> tuple[str, list[PDFWord]]:
    raw = page.get_text("words", sort=True)
    words: list[PDFWord] = []
    parts: list[str] = []
    cursor = 0
    prev_line: tuple[int, int] | None = None

    for x0, y0, x1, y1, token, block_no, line_no, word_no in raw:
        token = (token or "").strip()
        if not token:
            continue

        current_line = (block_no, line_no)
        if parts:
            separator = "\n" if prev_line != current_line else " "
            parts.append(separator)
            cursor += len(separator)

        start = cursor
        parts.append(token)
        cursor += len(token)
        end = cursor

        words.append(
            PDFWord(
                start=start,
                end=end,
                text=token,
                rect=fitz.Rect(x0, y0, x1, y1),
                block_no=block_no,
                line_no=line_no,
                word_no=word_no,
            )
        )
        prev_line = current_line

    return "".join(parts), words


def _pdf_rects_for_findings(
    findings: Sequence[TextFinding],
    words: Sequence[PDFWord],
    *,
    original_text: str,
) -> list[fitz.Rect]:
    rects: list[fitz.Rect] = []
    merged = merge_overlapping_findings(findings, original_text=original_text)

    for finding in merged:
        matched = [w for w in words if not (w.end <= finding.start or w.start >= finding.end)]
        if not matched:
            continue

        by_line: dict[tuple[int, int], list[fitz.Rect]] = {}
        for word in matched:
            overlap_start = max(word.start, finding.start)
            overlap_end = min(word.end, finding.end)
            if overlap_end <= overlap_start:
                continue

            rect = fitz.Rect(word.rect)
            if overlap_start != word.start or overlap_end != word.end:
                character_count = max(1, word.end - word.start)
                original_x0 = rect.x0
                width = rect.width
                rect.x0 = original_x0 + width * (
                    (overlap_start - word.start) / character_count
                )
                rect.x1 = original_x0 + width * (
                    (overlap_end - word.start) / character_count
                )
            by_line.setdefault((word.block_no, word.line_no), []).append(rect)

        for line_rects in by_line.values():
            rect = line_rects[0]
            for word_rect in line_rects[1:]:
                rect = rect | word_rect
            rects.append(rect)

    unique: list[fitz.Rect] = []
    seen: set[tuple[float, float, float, float]] = set()
    for rect in rects:
        key = (round(rect.x0, 3), round(rect.y0, 3), round(rect.x1, 3), round(rect.y1, 3))
        if key not in seen:
            seen.add(key)
            unique.append(rect)
    return unique


def _pdf_word_subrect(
    word: PDFWord,
    start_index: int,
    end_index: int,
) -> fitz.Rect:
    rect = fitz.Rect(word.rect)
    character_count = max(1, len(word.text))
    original_x0 = rect.x0
    width = rect.width
    rect.x0 = original_x0 + width * (start_index / character_count)
    rect.x1 = original_x0 + width * (end_index / character_count)
    return rect


def _native_redaction_word_specs(
    findings: Sequence[TextFinding],
    words: Sequence[PDFWord],
    *,
    original_text: str,
) -> list[
    tuple[
        fitz.Rect,
        list[tuple[fitz.Rect, str]],
        list[fitz.Rect],
    ]
]:
    merged = merge_overlapping_findings(findings, original_text=original_text)
    specs: list[
        tuple[fitz.Rect, list[tuple[fitz.Rect, str]], list[fitz.Rect]]
    ] = []

    for word in words:
        covered = [False] * len(word.text)
        for finding in merged:
            overlap_start = max(word.start, finding.start)
            overlap_end = min(word.end, finding.end)
            for offset in range(overlap_start, overlap_end):
                index = offset - word.start
                if 0 <= index < len(covered):
                    covered[index] = True
        if not any(covered):
            continue

        safe_fragments: list[tuple[fitz.Rect, str]] = []
        sensitive_rects: list[fitz.Rect] = []
        run_start = 0
        run_value = covered[0]
        for index in range(1, len(covered) + 1):
            value = covered[index] if index < len(covered) else not run_value
            if value == run_value:
                continue
            rect = _pdf_word_subrect(word, run_start, index)
            if run_value:
                sensitive_rects.append(rect)
            else:
                safe_fragments.append((rect, word.text[run_start:index]))
            if index < len(covered):
                run_start = index
                run_value = value

        specs.append((fitz.Rect(word.rect), safe_fragments, sensitive_rects))

    return specs


def _fit_pdf_fragment_font_size(text: str, rect: fitz.Rect) -> float:
    font_size = max(3.5, min(10.0, rect.height * 0.78))
    if not text or rect.width <= 0:
        return font_size
    try:
        width = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
    except Exception:
        return font_size
    if width > rect.width and width > 0:
        font_size = max(3.0, font_size * (rect.width / width) * 0.96)
    return font_size


def _restore_native_redaction_word(
    page: fitz.Page,
    *,
    safe_fragments: Sequence[tuple[fitz.Rect, str]],
    sensitive_rects: Sequence[fitz.Rect],
) -> None:
    for rect, text in safe_fragments:
        if not text:
            continue
        page.insert_text(
            fitz.Point(rect.x0, rect.y1 - max(0.5, rect.height * 0.15)),
            text,
            fontname="helv",
            fontsize=_fit_pdf_fragment_font_size(text, rect),
            color=(0, 0, 0),
            overlay=True,
        )
    for rect in sensitive_rects:
        page.draw_rect(
            rect,
            color=DEFAULT_BLACK,
            fill=DEFAULT_BLACK,
            width=0,
            overlay=True,
        )


def _pdf_rect_has_visible_content(page: fitz.Page, rect: fitz.Rect) -> bool:
    """Reject coordinates belonging only to invisible/covered PDF text.

    Some PDFs retain a hidden text layer from a different page. Redacting those
    coordinates creates bars in visually blank areas. A small rendered clip is
    the source of truth: if no visible ink exists there, no annotation is added.
    """
    candidate = fitz.Rect(rect)
    candidate_area = max(1.0, candidate.get_area())
    try:
        paint_log = list(page.get_bboxlog())
    except Exception:
        paint_log = []
    text_indexes = [
        index
        for index, item in enumerate(paint_log)
        if str(item[0]).endswith("text")
        and not (fitz.Rect(item[1]) & candidate).is_empty
    ]
    if text_indexes:
        text_index = max(text_indexes)
        for item in paint_log[text_index + 1:]:
            if item[0] != "fill-image":
                continue
            covered = fitz.Rect(item[1]) & candidate
            if not covered.is_empty and covered.get_area() / candidate_area >= 0.95:
                return False

    clipped = fitz.Rect(
        candidate.x0 - 0.5,
        candidate.y0 - 0.5,
        candidate.x1 + 0.5,
        candidate.y1 + 0.5,
    ) & page.rect
    if clipped.is_empty:
        return False
    pix = page.get_pixmap(
        matrix=fitz.Matrix(PDF_VISIBILITY_RENDER_SCALE, PDF_VISIBILITY_RENDER_SCALE),
        clip=clipped,
        alpha=False,
        colorspace=fitz.csRGB,
        annots=False,
    )
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("L")
    histogram = image.histogram()
    visible_pixels = sum(histogram[:PDF_VISIBLE_PIXEL_THRESHOLD])
    minimum_pixels = max(2, round(image.width * image.height * 0.002))
    return visible_pixels >= minimum_pixels


def _apply_page_redactions(
    page: fitz.Page,
    *,
    image_mode: str,
    remove_graphics: bool = False,
) -> None:
    kwargs: dict[str, Any] = {}

    if image_mode == "none" and hasattr(fitz, "PDF_REDACT_IMAGE_NONE"):
        kwargs["images"] = fitz.PDF_REDACT_IMAGE_NONE
    elif image_mode == "pixels" and hasattr(fitz, "PDF_REDACT_IMAGE_PIXELS"):
        kwargs["images"] = fitz.PDF_REDACT_IMAGE_PIXELS

    if remove_graphics and hasattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED"):
        kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED
    elif hasattr(fitz, "PDF_REDACT_LINE_ART_NONE"):
        kwargs["graphics"] = fitz.PDF_REDACT_LINE_ART_NONE
    if hasattr(fitz, "PDF_REDACT_TEXT_REMOVE"):
        kwargs["text"] = fitz.PDF_REDACT_TEXT_REMOVE

    page.apply_redactions(**kwargs)


def _page_has_rendered_content(page: fitz.Page) -> bool:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(0.5, 0.5),
        alpha=False,
        colorspace=fitz.csRGB,
        annots=False,
    )
    return min(pixmap.samples, default=255) < 250


def _save_pdf_checked(
    document: fitz.Document,
    output_path: Path,
    *,
    temporary_paths: list[Path],
) -> None:
    """Atomically publish a readable PDF without accepting blank-page loss."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    expected_content = [
        _page_has_rendered_content(page)
        for page in document
    ]
    last_error: Exception | None = None

    for _attempt in range(2):
        temporary_path = output_path.with_name(
            f".{output_path.stem}.{uuid.uuid4().hex}.tmp{output_path.suffix}"
        )
        temporary_paths.append(temporary_path)
        try:
            document.save(temporary_path, garbage=4, deflate=True, clean=True)
            if not temporary_path.exists() or temporary_path.stat().st_size <= 0:
                raise RuntimeError("PDF writer produced an empty file.")

            with fitz.open(temporary_path) as verification:
                if len(verification) != len(expected_content):
                    raise RuntimeError("PDF writer changed the page count.")
                for page_index, expected in enumerate(expected_content):
                    if expected and not _page_has_rendered_content(verification[page_index]):
                        raise RuntimeError(
                            f"PDF writer lost visible page content on page {page_index + 1}."
                        )

            temporary_path.replace(output_path)
            return
        except Exception as exc:
            last_error = exc
        finally:
            temporary_path.unlink(missing_ok=True)

    raise RuntimeError("Unable to create a complete redacted PDF safely.") from last_error


def _redact_docx(
    *,
    source_path: Path,
    output_path: Path,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
) -> None:
    document = docx.Document(source_path)
    for paragraph in _iter_document_paragraphs(document):
        full_text, _ = _paragraph_run_spans(paragraph)
        if not full_text.strip():
            continue

        findings = _redaction_findings(
            sdp=sdp,
            text=full_text,
            payload=payload,
            custom_redactions=custom_redactions,
        )

        if findings:
            redact_paragraph_runs(paragraph, findings)

    document.save(output_path)


def _redact_image_file(
    *,
    source_path: Path,
    output_path: Path,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
    ocr_languages: Optional[Sequence[str]] = None,
) -> None:
    image = Image.open(source_path).convert("RGB")
    page_text, words = _ocr_words_from_image(
        image,
        ocr_lang=resolve_ocr_lang(ocr_languages),
        sparse_text=_is_id_document_payload(payload),
        include_character_boxes=bool(_normalize_manual_redactions(custom_redactions)),
    )
    findings = _redaction_findings(
        sdp=sdp,
        text=page_text,
        payload=payload,
        custom_redactions=custom_redactions,
    )
    boxes = _boxes_for_text_spans(findings, words)
    boxes.extend(
        _signature_boxes_for_image(
            image=image,
            words=words,
            sdp=sdp,
            payload=payload,
        )
    )
    result = draw_redaction_boxes(image, boxes)
    fmt = DocumentInputFormat(source_path.suffix.lower().lstrip("."))
    result.save(output_path, format=_IMAGE_OUTPUT_MAP[fmt])


def _pdf_metadata_findings(
    *,
    document: fitz.Document,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
) -> list[tuple[str, str, list[TextFinding]]]:
    metadata = dict(document.metadata or {})
    target_values = {
        str(getattr(target, "value", target))
        for target in payload.target_data
    }
    exclusions = {
        _normalize_text_for_compare(item)
        for item in payload.review_exclusions
        if item and item.strip()
    }
    results: list[tuple[str, str, list[TextFinding]]] = []

    for key in _PDF_REVIEWABLE_METADATA_KEYS:
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        findings = _redaction_findings(
            sdp=sdp,
            text=value,
            payload=payload,
            custom_redactions=custom_redactions,
        )
        normalized_value = _normalize_text_for_compare(value)
        if (
            key == "author"
            and "name" in target_values
            and normalized_value not in exclusions
            and normalized_value not in _GENERIC_METADATA_AUTHORS
        ):
            findings = merge_overlapping_findings(
                [
                    *findings,
                    TextFinding(
                        start=0,
                        end=len(value),
                        quote=value,
                        label="name",
                        source="pdf_metadata",
                    ),
                ],
                original_text=value,
            )
        if findings:
            results.append((key, value, findings))
    return results


def _remove_metadata_findings(value: str, findings: Sequence[TextFinding]) -> str:
    result = value
    for finding in sorted(findings, key=lambda item: item.start, reverse=True):
        result = result[:finding.start] + result[finding.end:]
    return result.strip()


def _redact_pdf_metadata(
    *,
    document: fitz.Document,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
) -> None:
    metadata = dict(document.metadata or {})
    changed = False
    for key, value, findings in _pdf_metadata_findings(
        document=document,
        sdp=sdp,
        payload=payload,
        custom_redactions=custom_redactions,
    ):
        metadata[key] = _remove_metadata_findings(value, findings)
        changed = True
    if changed:
        document.set_metadata(metadata)


def _redact_pdf(
    *,
    source_path: Path,
    output_path: Path,
    sdp: GoogleSDPClient,
    payload: RedactionRequest,
    custom_redactions: Optional[Sequence[str]] = None,
    ocr_languages: Optional[Sequence[str]] = None,
    render_scale: float = DEFAULT_PDF_RENDER_SCALE,
) -> None:
    doc = fitz.open(source_path)
    temporary_paths: list[Path] = []
    detected_findings = 0
    applied_regions = 0
    ocr_lang = resolve_ocr_lang(ocr_languages)
    try:
        _redact_pdf_metadata(
            document=doc,
            sdp=sdp,
            payload=payload,
            custom_redactions=custom_redactions,
        )
        for embedded_name in list(doc.embfile_names()):
            doc.embfile_del(embedded_name)

        for page in doc:
            page_text, words = _page_words_with_offsets(page)
            regular_rects: list[fitz.Rect] = []
            signature_rects: list[fitz.Rect] = []
            native_word_specs: list[
                tuple[fitz.Rect, list[tuple[fitz.Rect, str]], list[fitz.Rect]]
            ] = []
            touches_images = False

            if words:
                findings = _redaction_findings(
                    sdp=sdp,
                    text=page_text,
                    payload=payload,
                    custom_redactions=custom_redactions,
                )
                detected_findings += len(findings)

                for word_rect, safe_fragments, sensitive_rects in _native_redaction_word_specs(
                    findings,
                    words,
                    original_text=page_text,
                ):
                    if _pdf_rect_has_visible_content(page, word_rect):
                        native_word_specs.append(
                            (word_rect, safe_fragments, sensitive_rects)
                        )

            else:
                surface = (
                    _primary_page_image_surface(doc, page)
                    if _is_id_document_payload(payload)
                    else None
                )

                if surface is not None:
                    page_text, ocr_words = _ocr_words_from_image(
                        surface.image,
                        ocr_lang=ocr_lang,
                        sparse_text=True,
                        include_character_boxes=bool(
                            _normalize_manual_redactions(custom_redactions)
                        ),
                    )
                    findings = _redaction_findings(
                        sdp=sdp,
                        text=page_text,
                        payload=payload,
                        custom_redactions=custom_redactions,
                    )
                    detected_findings += len(findings)
                    boxes = _boxes_for_text_spans(findings, ocr_words)

                    for box in boxes:
                        page_box = _image_box_to_page_rect(
                            box,
                            image_size=surface.image.size,
                            page_rect=surface.page_rect,
                        )
                        if page_box is not None:
                            regular_rects.append(page_box)

                    touches_images = True
                else:
                    effective_render_scale = max(
                        3.0,
                        ID_DOCUMENT_FALLBACK_RENDER_SCALE
                        if _is_id_document_payload(payload)
                        else render_scale,
                    )
                    pix = page.get_pixmap(
                        matrix=fitz.Matrix(
                            effective_render_scale,
                            effective_render_scale,
                        )
                    )
                    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    page_text, ocr_words = _ocr_words_from_image(
                        image,
                        ocr_lang=ocr_lang,
                        sparse_text=_is_id_document_payload(payload),
                        include_character_boxes=bool(
                            _normalize_manual_redactions(custom_redactions)
                        ),
                    )
                    findings = _redaction_findings(
                        sdp=sdp,
                        text=page_text,
                        payload=payload,
                        custom_redactions=custom_redactions,
                    )
                    detected_findings += len(findings)
                    boxes = _boxes_for_text_spans(findings, ocr_words)

                    for x0, y0, x1, y1 in boxes:
                        scaled = fitz.Rect(
                            x0 / effective_render_scale,
                            y0 / effective_render_scale,
                            x1 / effective_render_scale,
                            y1 / effective_render_scale,
                        )
                        regular_rects.append(scaled)

                    touches_images = True

            # Run signature OCR from one stable page rendering regardless of
            # whether the PDF also exposes a native text layer. Reusing the
            # text-redaction OCR pass made signature detection depend on page
            # construction and missed low-contrast handwritten signatures.
            if _signature_target_selected(payload) and not _visual_signature_is_excluded(payload):
                signature_surface = _render_page_surface_for_signatures(page)
                _signature_text, signature_words = _ocr_words_from_image(
                    signature_surface.image,
                    ocr_lang=ocr_lang,
                    sparse_text=True,
                )
                for box in _signature_boxes_for_image(
                    image=signature_surface.image,
                    words=signature_words,
                    sdp=sdp,
                    payload=payload,
                ):
                    page_box = _image_box_to_page_rect(
                        box,
                        image_size=signature_surface.image.size,
                        page_rect=signature_surface.page_rect,
                    )
                    if page_box is not None:
                        signature_rects.append(page_box)

            seen_regular: set[tuple[float, float, float, float]] = set()
            seen_native: set[tuple[float, float, float, float]] = set()
            applied_native_specs: list[
                tuple[list[tuple[fitz.Rect, str]], list[fitz.Rect]]
            ] = []
            for rect, safe_fragments, sensitive_rects in native_word_specs:
                clipped = fitz.Rect(rect) & page.rect
                key = tuple(round(value, 3) for value in clipped)
                if clipped.is_empty or key in seen_native:
                    continue
                page.add_redact_annot(clipped, fill=(1, 1, 1), cross_out=False)
                seen_native.add(key)
                applied_native_specs.append((safe_fragments, sensitive_rects))
                applied_regions += 1

            for rect in regular_rects:
                clipped = fitz.Rect(rect) & page.rect
                key = tuple(round(value, 3) for value in clipped)
                if clipped.is_empty or key in seen_regular:
                    continue
                page.add_redact_annot(clipped, fill=DEFAULT_BLACK, cross_out=False)
                seen_regular.add(key)
                applied_regions += 1

            seen_signatures: set[tuple[float, float, float, float]] = set()
            for rect in signature_rects:
                clipped = fitz.Rect(rect) & page.rect
                key = tuple(round(value, 3) for value in clipped)
                if clipped.is_empty or key in seen_signatures:
                    continue
                page.add_redact_annot(clipped, fill=DEFAULT_BLACK, cross_out=False)
                seen_signatures.add(key)
                applied_regions += 1

            if seen_native or seen_regular or seen_signatures:
                _apply_page_redactions(
                    page,
                    image_mode="pixels" if touches_images or seen_signatures else "none",
                    remove_graphics=bool(seen_signatures),
                )
                for safe_fragments, sensitive_rects in applied_native_specs:
                    _restore_native_redaction_word(
                        page,
                        safe_fragments=safe_fragments,
                        sensitive_rects=sensitive_rects,
                    )

        if detected_findings > 0 and applied_regions == 0:
            raise RuntimeError(
                "Sensitive data was detected, but no visible PDF regions could be mapped for redaction."
            )

        _save_pdf_checked(
            doc,
            output_path,
            temporary_paths=temporary_paths,
        )
    finally:
        doc.close()
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def apply_redaction(
    request: AnalyzerRequest | Mapping[str, Any],
    *,
    source_path: str | Path,
    output_dir: str | Path,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
    ocr_languages: Optional[Sequence[str]] = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> AnalyzerResponse:
    req = validate_analyzer_request(request)
    input_payload, payload = _ensure_redaction_request(req)
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Source file not found: {source}")
    output_path = _output_path(source, Path(output_dir), suffix="_redacted")
    resolved = _resolve_sdp(
        sdp=sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )

    fmt = input_payload.metadata.input_format
    if fmt == DocumentInputFormat.docx:
        _redact_docx(
            source_path=source,
            output_path=output_path,
            sdp=resolved,
            payload=payload,
            custom_redactions=custom_redactions,
        )
    elif fmt == DocumentInputFormat.pdf:
        _redact_pdf(
            source_path=source,
            output_path=output_path,
            sdp=resolved,
            payload=payload,
            custom_redactions=custom_redactions,
            ocr_languages=ocr_languages,
        )
    elif fmt in {DocumentInputFormat.jpg, DocumentInputFormat.jpeg, DocumentInputFormat.png}:
        _redact_image_file(
            source_path=source,
            output_path=output_path,
            sdp=resolved,
            payload=payload,
            custom_redactions=custom_redactions,
            ocr_languages=ocr_languages,
        )
    else:
        raise ValueError("Unsupported redact input format.")
    return _build_redaction_response(request=req, output_path=output_path)


__all__ = [
    "preview_redaction_candidates",
    "redact_text_with_findings",
    "draw_redaction_boxes",
    "redact_paragraph_runs",
    "apply_redaction",
]