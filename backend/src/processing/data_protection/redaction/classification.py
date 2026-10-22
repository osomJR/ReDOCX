"""Adaptive OCR for Redaction's document-type preview, never for removal.

Native evidence and a high-confidence English/French pass avoid loading every
installed OCR model for common documents. Ambiguous or weak evidence takes the
original all-language, complementary-pass path. No extracted content is cached.
"""
from __future__ import annotations

import csv
from io import BytesIO, StringIO
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from typing import Optional, Sequence

import docx
import fitz
from PIL import Image, ImageChops, ImageOps
import pytesseract

from backend.src.schema import DocumentInputFormat
from backend.src.extraction import (
    OCR_SPARSE_CONFIG, _ocr_data_score, _redaction_docx_parts,
    extract_redaction_ocr_passes, get_available_tesseract_languages,
    ocr_text_from_data, redaction_pdf_surfaces, resolve_ocr_lang,
)
from ..document_type_detection import detect_privacy_document_type


def _decisive(text: str, *, minimum_confidence: float = 0.85) -> bool:
    if len(text.split()) < 20:
        return False
    result = detect_privacy_document_type(text=text)
    return not result.is_fallback and result.confidence >= minimum_confidence


def _preview_image(image: Image.Image) -> Image.Image:
    """Suppress coloured security backgrounds for this optional preview only.

    A weak result falls back to the unaltered image and full language bundle.
    Removal and candidate detection never use these thresholded pixels.
    """
    source = image.convert("RGB")
    source.thumbnail((1800, 1800), getattr(Image, "Resampling", Image).LANCZOS)
    red, green, blue = source.split()
    bright = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return bright.point(lambda value: 255 if value > 160 else 0).convert("RGB")


def _preview_ocr_data(image: Image.Image, language: str):
    """Same recognition engine, TSV only; no removal-coordinate/HOCR work."""
    with tempfile.TemporaryDirectory(prefix="redocx-classify-") as directory:
        source = Path(directory) / "preview.png"
        image.save(source, format="PNG")
        environment = os.environ.copy()
        environment.setdefault("OMP_THREAD_LIMIT", "1")
        result = subprocess.run(
            [pytesseract.pytesseract.tesseract_cmd, str(source), "stdout", "-l", language,
             *shlex.split(OCR_SPARSE_CONFIG), "tsv"],
            capture_output=True, text=True, check=True, timeout=15, env=environment,
        )
        reader = csv.DictReader(StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE)
        data = {name: [] for name in reader.fieldnames or []}
        for row in reader:
            for name in data:
                data[name].append(row.get(name) or "")
        return data


def _native_text(path: Path, fmt: DocumentInputFormat) -> str:
    if fmt == DocumentInputFormat.pdf:
        with fitz.open(path) as document:
            return "\n".join(document[i].get_text("text", sort=True).strip()
                             for i in range(min(2, len(document)))).strip()
    if fmt == DocumentInputFormat.docx:
        document = docx.Document(path)
        chunks = []
        prefix = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        for _part, root in _redaction_docx_parts(document):
            for paragraph in root.iter(prefix + "p"):
                pieces = []
                for node in paragraph.iter():
                    parent = node.getparent()
                    while parent is not None and parent.tag != prefix + "p":
                        parent = parent.getparent()
                    if parent is paragraph:
                        if node.tag == prefix + "t":
                            pieces.append(node.text or "")
                        elif node.tag in {prefix+"tab", prefix+"br", prefix+"cr"}:
                            pieces.append(" ")
                if pieces:
                    chunks.append("".join(pieces))
        return "\n".join(chunks).strip()
    return ""


def _images(path: Path, fmt: DocumentInputFormat):
    if fmt == DocumentInputFormat.pdf:
        with fitz.open(path) as document:
            for index in range(min(2, len(document))):
                page = document[index]
                if page.get_image_info() or not page.get_text("text").strip():
                    for image, _rect in redaction_pdf_surfaces(page):
                        yield image
    elif fmt == DocumentInputFormat.docx:
        document = docx.Document(path)
        inspected = 0
        for part in document.part.package.parts:
            if not part.content_type.startswith("image/"):
                continue
            with Image.open(BytesIO(part.blob)) as image:
                yield ImageOps.exif_transpose(image).convert("RGB")
            inspected += 1
            if inspected >= 2:
                break
    else:
        with Image.open(path) as image:
            yield ImageOps.exif_transpose(image).convert("RGB")


def _legacy_text(path, fmt, native, ocr_languages):
    """Preserve the previous preview's evidence, order and language coverage."""
    chunks = []
    language = None
    used_ocr = False

    def add(image):
        nonlocal language, used_ocr
        used_ocr = True
        if language is None:
            language = resolve_ocr_lang(ocr_languages)
        seen = set()
        for data, _candidate, _config in extract_redaction_ocr_passes(image, ocr_lang=language, fast=True):
            text = ocr_text_from_data(data).strip()
            if text and text not in seen:
                chunks.append(text)
                seen.add(text)

    if fmt == DocumentInputFormat.pdf:
        with fitz.open(path) as document:
            for index in range(min(2, len(document))):
                page = document[index]
                text = page.get_text("text", sort=True).strip()
                if text:
                    chunks.append(text)
                if page.get_image_info() or not text:
                    for image, _rect in redaction_pdf_surfaces(page):
                        add(image)
    else:
        if native:
            chunks.append(native)
        for image in _images(path, fmt):
            add(image)
    return "\n".join(chunks).strip(), used_ocr


def extract_classification_text(
    file_path, fmt: DocumentInputFormat, *, ocr_languages: Optional[Sequence[str]] = None,
) -> tuple[str, bool]:
    path = Path(file_path)
    native = _native_text(path, fmt)
    # Native evidence from BOTH preview pages precedes any early return.
    if _decisive(native, minimum_confidence=0.97):
        return native, False
    if len(native) >= 200:
        # Mixed documents with ambiguous native evidence need the original
        # coverage; do not spend an extra speculative OCR pass on them.
        return _legacy_text(path, fmt, native, ocr_languages)
    preferred = None
    fast_chunks = [native] if native else []
    fast_quality = True
    used_ocr = False
    for image in _images(path, fmt):
        used_ocr = True
        if preferred is None:
            available = set(get_available_tesseract_languages())
            preferred = [lang for lang in ("eng", "fra") if lang in available]
            if ocr_languages:
                preferred = [lang for lang in resolve_ocr_lang(ocr_languages).split("+") if lang != "osd"]
        source = _preview_image(image)
        if not preferred:
            fast_quality = False
            break
        try:
            data = _preview_ocr_data(source, "+".join(preferred))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            fast_quality = False
            break
        _quality, confidence, tokens = _ocr_data_score(data)
        fast_quality = fast_quality and confidence >= 75 and tokens >= 8
        text = ocr_text_from_data(data).strip()
        if text:
            fast_chunks.append(text)
    fast_text = "\n".join(fast_chunks).strip()
    if not used_ocr:
        return native, False
    if fast_quality and _decisive(fast_text):
        return fast_text, True

    # Discard uncertain fast evidence. Re-run the existing path with ALL installed
    # models (or the explicit hints), retaining the previous coverage and limits.
    return _legacy_text(path, fmt, native, ocr_languages)
