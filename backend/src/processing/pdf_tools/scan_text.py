"""OCR and local raster repair used only by Edit PDF.

Recognition is an estimate, never a claim that the scan's original font was
recovered. Only selected OCR word regions are changed. Users must review the
generated PDF. Handwriting, stamps and artwork remain image content.
"""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from functools import lru_cache
from io import StringIO
import csv
import os
import shutil
import subprocess
import threading

import fitz
import numpy as np
from PIL import Image

_CACHE: OrderedDict[tuple, list[dict]] = OrderedDict()
_LOCK = threading.Lock()
_SLOTS = threading.BoundedSemaphore(2)
MAX_PIXELS = 12_000_000


class ScanTextError(ValueError):
    pass


@lru_cache(maxsize=1)
def _languages() -> str:
    binary = shutil.which("tesseract")
    if not binary:
        raise ScanTextError("OCR is unavailable: install Tesseract to edit scanned text.")
    try:
        result = subprocess.run([binary, "--list-langs"], capture_output=True,
                                text=True, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScanTextError("OCR language data is unavailable.") from exc
    available = set(result.stdout.splitlines()[1:])
    requested = os.getenv("REDOCX_EDIT_OCR_LANGUAGES", "eng+fra").split("+")
    chosen = [language for language in requested if language in available]
    if not chosen:
        raise ScanTextError("Install the OCR language data configured for Edit PDF.")
    return "+".join(chosen)


def needs_ocr(page: fitz.Page) -> bool:
    area = max(1, page.cropbox.get_area())
    # Logos and small photographs do not trigger recognition of a native page.
    if any(fitz.Rect(image["bbox"]).get_area() > area * 0.08 for image in page.get_image_info()):
        return True
    return not page.get_text().strip() and len(page.get_drawings()) > 500


def recognize_lines(page: fitz.Page, checksum: str, native_rects: list[fitz.Rect]) -> list[dict]:
    if not needs_ocr(page):
        return []
    language = _languages()
    key = (checksum, page.number, language)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached
    if not _SLOTS.acquire(blocking=False):
        raise ScanTextError("OCR is busy. Please retry shortly.")
    rotation = page.rotation
    try:
        page.set_rotation(0)
        scale = min(3.0, (MAX_PIXELS / max(1, page.rect.get_area())) ** 0.5)
        if scale < 0.5:
            raise ScanTextError("The scanned page is too large for safe OCR.")
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=False)
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            result = subprocess.run([shutil.which("tesseract"), "stdin", "stdout", "-l", language,
                                     "--psm", "11", "tsv"], input=pix.tobytes("png"),
                                    capture_output=True, check=True, timeout=40, env=environment)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ScanTextError("Scanned text could not be recognized. Retry or use Add text / Whiteout.") from exc
        if len(result.stdout) > 4 * 1024 * 1024:
            raise ScanTextError("The OCR response exceeds the page safety limit.")
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for row in csv.DictReader(StringIO(result.stdout.decode("utf-8", errors="replace")), delimiter="\t"):
            if row.get("level") != "5" or not row.get("text", "").strip():
                continue
            confidence = float(row["conf"])
            # Low-confidence detections are usually ornaments / handwriting.
            if confidence < 35:
                continue
            x, y, width, height = (float(row[field]) / scale for field in ("left", "top", "width", "height"))
            rect = fitz.Rect(x, y, x + width, y + height)
            if rect.is_empty or not page.rect.contains(rect):
                continue
            if any((rect & native).get_area() > rect.get_area() * 0.25 for native in native_rects):
                continue
            group = tuple(row[field] for field in ("block_num", "par_num", "line_num"))
            groups[group].append({"text": row["text"].strip(), "rect": tuple(rect), "confidence": confidence})
        lines = []
        for words in groups.values():
            words.sort(key=lambda word: word["rect"][0])
            rectangle = fitz.Rect(words[0]["rect"])
            for word in words[1:]:
                rectangle |= fitz.Rect(word["rect"])
            lines.append({"text": " ".join(word["text"] for word in words), "rect": tuple(rectangle),
                          "words": words, "confidence": round(sum(word["confidence"] for word in words) / len(words), 1),
                          "language": language})
        lines.sort(key=lambda line: (line["rect"][1], line["rect"][0]))
        with _LOCK:
            _CACHE[key] = lines
            while len(_CACHE) > 4:
                _CACHE.popitem(last=False)
        return lines
    finally:
        page.set_rotation(rotation)
        _SLOTS.release()


def erase_scan_words(page: fitz.Page, rectangles: list[fitz.Rect]) -> None:
    """Remove selected scan pixels and restore their local paper background.

    MuPDF redaction removes the source pixels, rather than hiding recoverable
    content behind an opaque box. Local inpainting fills the remaining paper;
    pixels outside the selected OCR word rectangles are not replaced.
    """
    if not rectangles:
        return
    try:
        import cv2
    except ImportError as exc:
        raise ScanTextError("Scanned editing requires opencv-python-headless.") from exc
    scale = min(3.0, (MAX_PIXELS / max(1, page.rect.get_area())) ** 0.5)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).copy()[:, :, :3]
    patches = []
    for rectangle in rectangles:
        rect = fitz.Rect(rectangle) & page.rect
        if rect.is_empty:
            continue
        # A few paper pixels around the detected glyphs provide the fill colour.
        outer = (rect + (-2, -2, 2, 2)) & page.rect
        x0, y0 = max(0, int(outer.x0 * scale)), max(0, int(outer.y0 * scale))
        x1, y1 = min(pix.width, int(outer.x1 * scale + 1)), min(pix.height, int(outer.y1 * scale + 1))
        crop = rgb[y0:y1, x0:x1].copy()
        if not crop.size:
            continue
        mask = np.zeros(crop.shape[:2], dtype=np.uint8)
        a, b = max(0, int(rect.x0 * scale) - x0), max(0, int(rect.y0 * scale) - y0)
        c, d = min(crop.shape[1], int(rect.x1 * scale + 1) - x0), min(crop.shape[0], int(rect.y1 * scale + 1) - y0)
        mask[b:d, a:c] = 255
        repaired = cv2.inpaint(crop, mask, 3, cv2.INPAINT_TELEA)
        # Only the selected word rectangle is copied back into the PDF.
        image = Image.fromarray(repaired[b:d, a:c])
        from io import BytesIO
        data = BytesIO()
        image.save(data, format="PNG")
        patches.append((rect, data.getvalue()))
    for rect, _data in patches:
        page.add_redact_annot(rect, fill=None, cross_out=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                          graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    for rect, data in patches:
        page.insert_image(rect, stream=data, keep_proportion=False, overlay=True)
