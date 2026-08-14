from __future__ import annotations

"""PDF layout analysis and safe-placement helpers for ReDOCX Sign.

This module keeps sender-side placement suggestions advisory while making collision
checks deterministic and server-enforced. It intentionally uses only local PDF
content/OCR; no document content is sent to a third party.
"""

from base64 import b64encode
from io import BytesIO
import math
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Mapping, Optional, Sequence

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

try:  # OCR is optional; native PDF extraction remains available without it.
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover - deployment dependency is optional.
    pytesseract = None  # type: ignore

try:
    from backend.src.schema import ESIGN_SIGNERS_PER_SIGNATURE_PAGE
except ImportError:  # pragma: no cover - direct src-package imports in tests.
    from ...schema import ESIGN_SIGNERS_PER_SIGNATURE_PAGE


SIGNERS_PER_SIGNATURE_PAGE = ESIGN_SIGNERS_PER_SIGNATURE_PAGE
SIGNATURE_PAGE_WIDTH = 612.0
SIGNATURE_PAGE_HEIGHT = 792.0
SIGNATURE_TERMS = {
    "signature",
    "sign",
    "signed",
    "signer",
    "initial",
    "initials",
    "paraphe",
    "paraphes",
    "signez",
    "signé",
    "signée",
}
_WORD_CLEAN_RE = re.compile(r"[^\wÀ-ÿ'-]+", re.UNICODE)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="python")
    data = getattr(value, "__dict__", None)
    return data if isinstance(data, Mapping) else {}


def _rectangle_mapping(field: Any) -> Mapping[str, Any]:
    value = _mapping(field).get("rectangle")
    return _mapping(value)


def _field_page_number(field: Any) -> int:
    try:
        return int(_mapping(field).get("page_number") or 1)
    except (TypeError, ValueError):
        return 1


def _field_id(field: Any, index: int) -> str:
    data = _mapping(field)
    return str(data.get("field_id") or data.get("id") or f"field_{index}")


def _field_type(field: Any) -> str:
    value = _mapping(field).get("field_type")
    return str(getattr(value, "value", value) or "text")


def _native_widget_name(field: Any) -> str:
    return str(_mapping(field).get("native_widget_name") or "").strip()


def _placement_source(field: Any) -> str:
    return str(_mapping(field).get("placement_source") or "manual").strip().lower()


def _fitz_rect_from_data(page_rect: fitz.Rect, rectangle: Mapping[str, Any]) -> fitz.Rect:
    x = float(rectangle.get("x", 0.0))
    y = float(rectangle.get("y", 0.0))
    width = float(rectangle.get("width", 0.0))
    height = float(rectangle.get("height", 0.0))
    values = (x, y, width, height)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("E-signature rectangles must contain finite coordinates.")
    if x < 0.0 or y < 0.0 or width <= 0.0 or height <= 0.0:
        raise ValueError("E-signature rectangles must have non-negative origins and positive size.")
    if x > 1.0 or y > 1.0 or x + width > 1.0 or y + height > 1.0:
        raise ValueError("E-signature rectangles must remain inside the normalized PDF page.")
    return fitz.Rect(
        page_rect.x0 + x * page_rect.width,
        page_rect.y0 + y * page_rect.height,
        page_rect.x0 + (x + width) * page_rect.width,
        page_rect.y0 + (y + height) * page_rect.height,
    )


def _normalized_rect(page_rect: fitz.Rect, rect: fitz.Rect) -> dict[str, float]:
    return {
        "x": round((rect.x0 - page_rect.x0) / page_rect.width, 6),
        "y": round((rect.y0 - page_rect.y0) / page_rect.height, 6),
        "width": round(rect.width / page_rect.width, 6),
        "height": round(rect.height / page_rect.height, 6),
    }


def _intersection_area(left: fitz.Rect, right: fitz.Rect) -> float:
    intersection = left & right
    if intersection.is_empty or intersection.width <= 0 or intersection.height <= 0:
        return 0.0
    return float(intersection.width * intersection.height)


def _overlaps(left: fitz.Rect, right: fitz.Rect, *, min_area: float = 0.5) -> bool:
    return _intersection_area(left, right) >= min_area


def signature_page_count_for_signers(signers: Sequence[Mapping[str, Any]] | Sequence[Any]) -> int:
    signer_count = max(1, len(signers))
    return max(1, math.ceil(signer_count / SIGNERS_PER_SIGNATURE_PAGE))


def signers_from_esignature_payload(payload: Any) -> list[dict[str, str]]:
    data = _mapping(payload)
    signers: list[dict[str, str]] = []
    self_signer = data.get("self_signer")
    if self_signer:
        item = _mapping(self_signer)
        signers.append(
            {
                "name": str(item.get("name") or "Document owner").strip(),
                "email": str(item.get("email") or "").strip().lower(),
            }
        )
    for recipient in data.get("recipients") or []:
        item = _mapping(recipient)
        signers.append(
            {
                "name": str(item.get("name") or "Signer").strip(),
                "email": str(item.get("email") or "").strip().lower(),
            }
        )
    return signers


def effective_page_count(original_page_count: int, *, add_signature_page: bool, signers: Sequence[Any]) -> int:
    return original_page_count + (
        signature_page_count_for_signers(signers) if add_signature_page else 0
    )


def _signature_slot_rects(slot_index: int) -> tuple[fitz.Rect, fitz.Rect]:
    top = 116.0 + slot_index * 104.0
    signature_rect = fitz.Rect(54.0, top + 28.0, 395.0, top + 76.0)
    date_rect = fitz.Rect(425.0, top + 38.0, 558.0, top + 68.0)
    return signature_rect, date_rect


def _draw_signature_page(page: fitz.Page, signers: Sequence[Mapping[str, Any]], page_index: int) -> None:
    page.insert_text((54, 56), "ReDOCX Signature Page", fontsize=20, fontname="helv", color=(0.08, 0.08, 0.1))
    page.insert_text(
        (54, 80),
        "This page was appended before signing to preserve the original document layout.",
        fontsize=9,
        fontname="helv",
        color=(0.35, 0.35, 0.38),
    )

    start = page_index * SIGNERS_PER_SIGNATURE_PAGE
    chunk = list(signers[start : start + SIGNERS_PER_SIGNATURE_PAGE])
    if not chunk:
        chunk = [{"name": "Signer", "email": ""}]

    for local_index, signer in enumerate(chunk):
        signature_rect, date_rect = _signature_slot_rects(local_index)
        name = str(signer.get("name") or "Signer").strip()
        email = str(signer.get("email") or "").strip()
        identity = f"{name} <{email}>" if email else name
        page.insert_text(
            (54, signature_rect.y0 - 12),
            identity[:110],
            fontsize=9.5,
            fontname="helv",
            color=(0.12, 0.12, 0.15),
        )
        page.insert_text(
            (54, signature_rect.y0 - 6),
            "Signature",
            fontsize=7.5,
            fontname="helv",
            color=(0.45, 0.45, 0.48),
        )
        page.insert_text(
            (425, date_rect.y0 - 7),
            "Date signed",
            fontsize=7.5,
            fontname="helv",
            color=(0.45, 0.45, 0.48),
        )
        # Borders make the available placement area explicit without covering any
        # original document content. Rendered signer values remain transparent.
        page.draw_rect(signature_rect, color=(0.72, 0.72, 0.75), width=0.7)
        page.draw_rect(date_rect, color=(0.72, 0.72, 0.75), width=0.7)

    page.insert_text(
        (54, 760),
        f"ReDOCX Sign • appended signature page {page_index + 1}",
        fontsize=7,
        fontname="helv",
        color=(0.5, 0.5, 0.52),
    )


def append_signature_pages(
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    *,
    signers: Sequence[Mapping[str, Any]],
) -> Path:
    source = Path(source_pdf_path).expanduser().resolve()
    output = Path(output_pdf_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Source PDF not found: {source}")
    if source.suffix.lower() != ".pdf" or output.suffix.lower() != ".pdf":
        raise ValueError("Signature-page preparation requires PDF paths.")
    output.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(source) as pdf:
        if bool(getattr(pdf, "needs_pass", False)):
            raise ValueError("Password-protected PDFs cannot receive a signature page before unlock.")
        for page_index in range(signature_page_count_for_signers(signers)):
            page = pdf.new_page(width=SIGNATURE_PAGE_WIDTH, height=SIGNATURE_PAGE_HEIGHT)
            _draw_signature_page(page, signers, page_index)
        pdf.save(output, garbage=4, deflate=True)
    return output


def signature_page_suggestions(
    original_page_count: int,
    *,
    signers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    resolved_signers = list(signers) or [{"name": "Signer", "email": ""}]
    page_rect = fitz.Rect(0, 0, SIGNATURE_PAGE_WIDTH, SIGNATURE_PAGE_HEIGHT)
    for index, signer in enumerate(resolved_signers):
        signature_page_index = index // SIGNERS_PER_SIGNATURE_PAGE
        local_index = index % SIGNERS_PER_SIGNATURE_PAGE
        signature_rect, date_rect = _signature_slot_rects(local_index)
        page_number = original_page_count + signature_page_index + 1
        email = str(signer.get("email") or "").strip().lower()
        name = str(signer.get("name") or email or "Signer").strip()
        suggestions.extend(
            [
                {
                    "suggestion_id": f"signature-page:{index}:signature",
                    "source": "signature_page",
                    "confidence": 1.0,
                    "field_type": "signature",
                    "page_number": page_number,
                    "rectangle": _normalized_rect(page_rect, signature_rect),
                    "label": f"{name} signature",
                    "assigned_to_email": email,
                    "reason": "Dedicated signature-page slot",
                },
                {
                    "suggestion_id": f"signature-page:{index}:date",
                    "source": "signature_page",
                    "confidence": 1.0,
                    "field_type": "date_signed",
                    "page_number": page_number,
                    "rectangle": _normalized_rect(page_rect, date_rect),
                    "label": f"{name} date signed",
                    "assigned_to_email": email,
                    "reason": "Dedicated signature-page date slot",
                },
            ]
        )
    return suggestions


def _page_words(page: fitz.Page) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for raw in page.get_text("words") or []:
        if len(raw) < 5:
            continue
        x0, y0, x1, y1, text = raw[:5]
        clean = str(text or "").strip()
        if not clean:
            continue
        rect = fitz.Rect(float(x0), float(y0), float(x1), float(y1))
        words.append({"rect": rect, "text": clean})
    return words


def _page_small_images(page: fitz.Page) -> list[fitz.Rect]:
    images: list[fitz.Rect] = []
    page_area = max(1.0, page.rect.width * page.rect.height)
    try:
        infos = page.get_image_info(xrefs=True) or []
    except Exception:
        infos = []
    for info in infos:
        bbox = info.get("bbox") if isinstance(info, Mapping) else None
        if not bbox:
            continue
        rect = fitz.Rect(bbox)
        coverage = (rect.width * rect.height) / page_area
        # A scan/background image often covers the whole page. Treat its visual
        # ink, not the image object itself, as the collision signal.
        if coverage >= 0.72:
            continue
        if rect.width > 1 and rect.height > 1:
            images.append(rect)
    return images


def _page_vector_regions(page: fitz.Page) -> list[fitz.Rect]:
    """Return stroke/fill regions precise enough for placement collision checks.

    Rectangle outlines are represented by their four thin edges so a large empty
    box does not make its entire interior look occupied. Filled vector shapes use
    their full bounds.
    """
    regions: list[fitz.Rect] = []
    try:
        drawings = page.get_drawings() or []
    except Exception:
        drawings = []

    for drawing in drawings:
        if not isinstance(drawing, Mapping):
            continue
        drawing_rect = drawing.get("rect")
        fill = drawing.get("fill")
        if fill is not None and drawing_rect is not None:
            rect = fitz.Rect(drawing_rect)
            if rect.width > 0.5 and rect.height > 0.5:
                regions.append(rect)

        stroke_width = max(1.0, float(drawing.get("width") or 1.0) + 0.75)
        half = stroke_width / 2.0
        for item in drawing.get("items", []) or []:
            if not item:
                continue
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                regions.append(
                    fitz.Rect(
                        min(float(p1.x), float(p2.x)) - half,
                        min(float(p1.y), float(p2.y)) - half,
                        max(float(p1.x), float(p2.x)) + half,
                        max(float(p1.y), float(p2.y)) + half,
                    )
                )
            elif kind == "re" and len(item) >= 2 and fill is None:
                rect = fitz.Rect(item[1])
                regions.extend(
                    [
                        fitz.Rect(rect.x0 - half, rect.y0 - half, rect.x1 + half, rect.y0 + half),
                        fitz.Rect(rect.x0 - half, rect.y1 - half, rect.x1 + half, rect.y1 + half),
                        fitz.Rect(rect.x0 - half, rect.y0 - half, rect.x0 + half, rect.y1 + half),
                        fitz.Rect(rect.x1 - half, rect.y0 - half, rect.x1 + half, rect.y1 + half),
                    ]
                )
            elif kind in {"c", "qu"}:
                points = []
                for value in item[1:]:
                    if hasattr(value, "x") and hasattr(value, "y"):
                        points.append(value)
                    elif hasattr(value, "rect"):
                        candidate = fitz.Rect(value.rect)
                        regions.append(fitz.Rect(candidate.x0 - half, candidate.y0 - half, candidate.x1 + half, candidate.y1 + half))
                if points:
                    xs = [float(point.x) for point in points]
                    ys = [float(point.y) for point in points]
                    regions.append(
                        fitz.Rect(min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
                    )
    return [rect & page.rect for rect in regions if not (rect & page.rect).is_empty]


def _visual_ink_ratio(page: fitz.Page, rect: fitz.Rect, *, zoom: float = 1.5) -> float:
    clip = rect & page.rect
    if clip.is_empty or clip.width <= 1 or clip.height <= 1:
        return 1.0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False, annots=True)
        channels = max(1, int(getattr(pix, "n", 3)))
        array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, channels)
        rgb = array[:, :, :3].astype(np.float32)
        gray = rgb.mean(axis=2)
        if gray.size == 0:
            return 0.0
        # PDF pages render onto an opaque light background. Count materially dark
        # pixels rather than estimating the background from the selection border:
        # a sender-created empty field may itself contain a thin gray border, and
        # using that border as the background would incorrectly classify the white
        # interior as nearly 100% ink. The fixed luminance gate is intentionally
        # conservative: normal text/vector/image content is detected, while thin
        # signature lines and empty AcroForm borders remain below the density gate.
        ink = gray <= 225.0
        return float(ink.mean())
    except Exception:
        return 0.0


def _native_widgets(page: fitz.Page, page_number: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        widgets = list(page.widgets() or [])
    except Exception:
        widgets = []
    for index, widget in enumerate(widgets):
        rect = fitz.Rect(widget.rect)
        type_name = str(getattr(widget, "field_type_string", "") or "").lower()
        if "sign" in type_name:
            field_type = "signature"
        elif "check" in type_name:
            field_type = "checkbox"
        elif "text" in type_name:
            field_type = "text"
        else:
            continue
        name = str(getattr(widget, "field_name", "") or f"native_field_{page_number}_{index + 1}")
        results.append(
            {
                "native_widget_name": name,
                "field_type": field_type,
                "page_number": page_number,
                "rectangle": _normalized_rect(page.rect, rect),
                "label": str(getattr(widget, "field_label", "") or name),
                "current_value": getattr(widget, "field_value", None),
                "source": "native_form_field",
            }
        )
    return results


def _horizontal_lines(page: fitz.Page) -> list[fitz.Rect]:
    lines: list[fitz.Rect] = []
    try:
        drawings = page.get_drawings() or []
    except Exception:
        drawings = []
    for drawing in drawings:
        for item in drawing.get("items", []) if isinstance(drawing, Mapping) else []:
            if not item:
                continue
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                if abs(float(p1.y) - float(p2.y)) <= 2.0 and abs(float(p2.x) - float(p1.x)) >= 70.0:
                    lines.append(
                        fitz.Rect(
                            min(float(p1.x), float(p2.x)),
                            min(float(p1.y), float(p2.y)) - 1.0,
                            max(float(p1.x), float(p2.x)),
                            max(float(p1.y), float(p2.y)) + 1.0,
                        )
                    )
            elif kind == "re" and len(item) >= 2:
                rect = fitz.Rect(item[1])
                if rect.width >= 70.0 and rect.height <= 4.0:
                    lines.append(rect)
    return lines


def _normalized_term(text: str) -> str:
    return _WORD_CLEAN_RE.sub("", str(text or "").lower())


def _rect_has_text(words: Sequence[dict[str, Any]], rect: fitz.Rect) -> bool:
    return any(_overlaps(rect, item["rect"], min_area=0.5) for item in words)


def _candidate_rects(page: fitz.Page, anchor: fitz.Rect) -> list[fitz.Rect]:
    width = min(250.0, max(150.0, page.rect.width * 0.30))
    height = min(52.0, max(38.0, page.rect.height * 0.055))
    gap = 8.0
    candidates = [
        fitz.Rect(anchor.x0, anchor.y1 + gap, anchor.x0 + width, anchor.y1 + gap + height),
        fitz.Rect(anchor.x1 + gap, anchor.y0 - 8.0, anchor.x1 + gap + width, anchor.y0 - 8.0 + height),
        fitz.Rect(anchor.x0, anchor.y0 - gap - height, anchor.x0 + width, anchor.y0 - gap),
        fitz.Rect(anchor.x0 - gap - width, anchor.y0 - 8.0, anchor.x0 - gap, anchor.y0 - 8.0 + height),
    ]
    safe: list[fitz.Rect] = []
    margin = 12.0
    inner = fitz.Rect(page.rect.x0 + margin, page.rect.y0 + margin, page.rect.x1 - margin, page.rect.y1 - margin)
    for candidate in candidates:
        shifted = fitz.Rect(candidate)
        if shifted.x0 < inner.x0:
            shifted += (inner.x0 - shifted.x0, 0)
        if shifted.x1 > inner.x1:
            shifted += (inner.x1 - shifted.x1, 0)
        if shifted.y0 < inner.y0:
            shifted += (0, inner.y0 - shifted.y0)
        if shifted.y1 > inner.y1:
            shifted += (0, inner.y1 - shifted.y1)
        if inner.contains(shifted):
            safe.append(shifted)
    return safe


def _safe_signature_rect(page: fitz.Page, anchor: fitz.Rect, words: Sequence[dict[str, Any]]) -> Optional[fitz.Rect]:
    nearby_lines = sorted(
        _horizontal_lines(page),
        key=lambda line: abs(line.y0 - anchor.y1) + max(0.0, anchor.x0 - line.x1, line.x0 - anchor.x1),
    )
    for line in nearby_lines[:6]:
        if abs(line.y0 - anchor.y1) > 100:
            continue
        rect = fitz.Rect(line.x0, max(page.rect.y0 + 8, line.y0 - 44), line.x1, line.y0 + 3)
        if rect.width < 90 or _rect_has_text(words, rect):
            continue
        if _visual_ink_ratio(page, rect) <= 0.10:
            return rect

    ranked: list[tuple[float, fitz.Rect]] = []
    for rect in _candidate_rects(page, anchor):
        if _rect_has_text(words, rect):
            continue
        ink = _visual_ink_ratio(page, rect)
        ranked.append((ink, rect))
    ranked.sort(key=lambda item: item[0])
    if ranked and ranked[0][0] <= 0.08:
        return ranked[0][1]
    return None


def _ocr_signature_anchors(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    if pytesseract is None:
        return []
    try:
        zoom = 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 11",
        )
    except Exception:
        return []

    anchors: list[tuple[fitz.Rect, str]] = []
    for index, raw_text in enumerate(data.get("text", [])):
        term = _normalized_term(raw_text)
        if term not in SIGNATURE_TERMS:
            continue
        try:
            confidence = float(data.get("conf", [0])[index])
        except (TypeError, ValueError, IndexError):
            confidence = 0.0
        if confidence < 35:
            continue
        x = float(data["left"][index]) / zoom
        y = float(data["top"][index]) / zoom
        width = float(data["width"][index]) / zoom
        height = float(data["height"][index]) / zoom
        anchors.append((fitz.Rect(x, y, x + width, y + height), str(raw_text)))
    return anchors


def detect_signature_line_suggestions(pdf: fitz.Document, *, use_ocr: bool = False) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for page_index in range(pdf.page_count):
        page = pdf[page_index]
        words = _page_words(page)
        anchors = [
            (item["rect"], item["text"], "pdf_text", 0.92)
            for item in words
            if _normalized_term(item["text"]) in SIGNATURE_TERMS
        ]
        if use_ocr and not anchors:
            anchors.extend((rect, text, "ocr", 0.72) for rect, text in _ocr_signature_anchors(page))

        for anchor, label, source, confidence in anchors:
            rect = _safe_signature_rect(page, anchor, words)
            if rect is None:
                continue
            normalized = _normalized_rect(page.rect, rect)
            key = (
                page_index + 1,
                round(normalized["x"] * 100),
                round(normalized["y"] * 100),
            )
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(
                {
                    "suggestion_id": f"signature-line:{page_index + 1}:{len(suggestions) + 1}",
                    "source": source,
                    "confidence": confidence,
                    "field_type": "signature",
                    "page_number": page_index + 1,
                    "rectangle": normalized,
                    "label": "Signature",
                    "reason": f"Signature-related label detected near '{label}'. Sender confirmation required.",
                }
            )
    return suggestions


def _page_content_regions(page: fitz.Page) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for item in _page_words(page):
        regions.append(
            {
                "kind": "text",
                "rectangle": _normalized_rect(page.rect, item["rect"]),
                "text": item["text"][:256],
            }
        )
    for rect in _page_small_images(page):
        regions.append(
            {
                "kind": "image",
                "rectangle": _normalized_rect(page.rect, rect),
                "text": "",
            }
        )
    for rect in _page_vector_regions(page):
        regions.append(
            {
                "kind": "vector",
                "rectangle": _normalized_rect(page.rect, rect),
                "text": "",
            }
        )
    return regions


def _preview_data_url(page: fitz.Page, *, max_width: int = 1180) -> str:
    zoom = min(2.0, max(0.7, max_width / max(1.0, page.rect.width)))
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=True)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + b64encode(buffer.getvalue()).decode("ascii")


def analyze_field_collisions(pdf: fitz.Document, fields: Sequence[Any]) -> list[dict[str, Any]]:
    collisions: list[dict[str, Any]] = []
    per_page: dict[int, list[tuple[int, Any, fitz.Rect]]] = {}

    for index, field in enumerate(fields):
        page_number = _field_page_number(field)
        if page_number < 1 or page_number > pdf.page_count:
            collisions.append(
                {
                    "field_id": _field_id(field, index),
                    "page_number": page_number,
                    "blocking": True,
                    "kinds": ["page_out_of_range"],
                    "overlapping_text": "",
                    "ink_ratio": 1.0,
                    "overlapping_field_ids": [],
                }
            )
            continue
        page = pdf[page_number - 1]
        try:
            rect = _fitz_rect_from_data(page.rect, _rectangle_mapping(field))
        except (TypeError, ValueError):
            collisions.append(
                {
                    "field_id": _field_id(field, index),
                    "page_number": page_number,
                    "blocking": True,
                    "kinds": ["invalid_rectangle"],
                    "overlapping_text": "",
                    "ink_ratio": 1.0,
                    "overlapping_field_ids": [],
                }
            )
            continue
        per_page.setdefault(page_number, []).append((index, field, rect))

    for page_number, page_fields in per_page.items():
        page = pdf[page_number - 1]
        words = _page_words(page)
        images = _page_small_images(page)
        vectors = _page_vector_regions(page)
        native_widgets = _native_widgets(page, page_number)
        native_by_name = {item["native_widget_name"]: item for item in native_widgets}

        for index, field, rect in page_fields:
            field_identifier = _field_id(field, index)
            text_hits = [item["text"] for item in words if _overlaps(rect, item["rect"], min_area=0.5)]
            image_hit = any(
                _intersection_area(rect, image_rect) / max(1.0, rect.width * rect.height) >= 0.08
                for image_rect in images
            )
            placement_source = _placement_source(field)
            vector_hit = any(_overlaps(rect, vector_rect, min_area=0.5) for vector_rect in vectors)
            vector_is_expected = placement_source in {
                "signature_line_suggestion",
                "signature_page",
                "native_form_field",
            }
            overlap_ids: list[str] = []
            for other_index, other_field, other_rect in page_fields:
                if other_index == index:
                    continue
                overlap = _intersection_area(rect, other_rect)
                if overlap / max(1.0, min(rect.width * rect.height, other_rect.width * other_rect.height)) >= 0.03:
                    overlap_ids.append(_field_id(other_field, other_index))

            ink_ratio = _visual_ink_ratio(page, rect)
            native_name = _native_widget_name(field)
            native_match = native_by_name.get(native_name) if native_name else None
            kinds: list[str] = []
            if text_hits:
                kinds.append("text")
            if image_hit:
                kinds.append("image")
            if vector_hit and not vector_is_expected:
                kinds.append("vector")
            if overlap_ids:
                kinds.append("field")
            # Native widgets and thin signature lines have low visual density. A
            # high-density region is a conservative fallback for scans/vector art.
            if ink_ratio >= 0.20 and not native_match:
                kinds.append("visual_content")

            blocking = bool(kinds)
            collisions.append(
                {
                    "field_id": field_identifier,
                    "page_number": page_number,
                    "blocking": blocking,
                    "kinds": sorted(set(kinds)),
                    "overlapping_text": " ".join(text_hits)[:1000],
                    "ink_ratio": round(ink_ratio, 4),
                    "overlapping_field_ids": sorted(set(overlap_ids)),
                }
            )
    return collisions


def assert_safe_field_placements(source_pdf_path: str | Path, fields: Sequence[Any]) -> None:
    with fitz.open(source_pdf_path) as pdf:
        collisions = analyze_field_collisions(pdf, fields)
    blocked = [item for item in collisions if item.get("blocking")]
    if not blocked:
        return
    summaries: list[str] = []
    for item in blocked[:8]:
        kinds = ", ".join(item.get("kinds") or ["content"])
        text = str(item.get("overlapping_text") or "").strip()
        detail = f"{item.get('field_id')} on page {item.get('page_number')} overlaps {kinds}"
        if text:
            detail += f" ('{text[:120]}')"
        summaries.append(detail)
    suffix = "" if len(blocked) <= 8 else f"; plus {len(blocked) - 8} more field(s)"
    raise ValueError(
        "Unsafe e-signature field placement detected. Move the field to empty space or add a signature page: "
        + "; ".join(summaries)
        + suffix
    )


def analyze_esignature_pdf(
    source_pdf_path: str | Path,
    *,
    fields: Sequence[Any] = (),
    preview_page_number: int = 1,
    add_signature_page: bool = False,
    signers: Sequence[Mapping[str, Any]] = (),
    detect_signature_lines: bool = False,
) -> dict[str, Any]:
    source = Path(source_pdf_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Source PDF not found: {source}")

    with fitz.open(source) as original:
        original_page_count = int(original.page_count)
        if original_page_count < 1:
            raise ValueError("The PDF has no pages.")
        native_fields: list[dict[str, Any]] = []
        for page_index in range(original.page_count):
            native_fields.extend(_native_widgets(original[page_index], page_index + 1))
        signature_suggestions = (
            detect_signature_line_suggestions(original, use_ocr=True)
            if detect_signature_lines
            else []
        )

    signature_page_count = signature_page_count_for_signers(signers) if add_signature_page else 0
    effective_count = original_page_count + signature_page_count
    if preview_page_number < 1 or preview_page_number > effective_count:
        raise ValueError(
            f"preview_page_number must be between 1 and {effective_count}."
        )

    if add_signature_page:
        signature_suggestions.extend(
            signature_page_suggestions(original_page_count, signers=signers)
        )

    with TemporaryDirectory(prefix="redocx-esign-layout-") as tmp:
        analysis_path = source
        if add_signature_page:
            analysis_path = append_signature_pages(
                source,
                Path(tmp) / "with-signature-pages.pdf",
                signers=signers,
            )
        with fitz.open(analysis_path) as pdf:
            page = pdf[preview_page_number - 1]
            current_regions = _page_content_regions(page)
            preview_data_url = _preview_data_url(page)
            collisions = analyze_field_collisions(pdf, fields)
            pages = [
                {
                    "page_number": index + 1,
                    "width": round(float(pdf[index].rect.width), 3),
                    "height": round(float(pdf[index].rect.height), 3),
                    "is_signature_page": index >= original_page_count,
                }
                for index in range(pdf.page_count)
            ]

    return {
        "page_count": original_page_count,
        "effective_page_count": effective_count,
        "signature_page_count": signature_page_count,
        "preview_page_number": preview_page_number,
        "preview_data_url": preview_data_url,
        "pages": pages,
        "content_regions": current_regions,
        "native_form_fields": native_fields,
        "signature_suggestions": signature_suggestions,
        "collisions": collisions,
        "ocr_used": bool(detect_signature_lines and pytesseract is not None),
    }


__all__ = [
    "SIGNERS_PER_SIGNATURE_PAGE",
    "signature_page_count_for_signers",
    "signers_from_esignature_payload",
    "effective_page_count",
    "append_signature_pages",
    "signature_page_suggestions",
    "detect_signature_line_suggestions",
    "analyze_field_collisions",
    "assert_safe_field_placements",
    "analyze_esignature_pdf",
]
