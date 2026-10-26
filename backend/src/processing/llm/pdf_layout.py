"""PDF template writing at original point sizes, with bounded continuation pages."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from html import escape
import io
import logging
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import unicodedata

import fitz

from .document_layout import DocumentLayout, LayoutUnit, distribute_text

logger = logging.getLogger(__name__)
MAX_CONTINUATION_PAGES = 200


class PdfLayoutError(ValueError):
    """Processed PDF text cannot be placed safely in the source layout."""

    def __init__(self, message: str, *, can_reflow: bool = False) -> None:
        super().__init__(message)
        self.can_reflow = can_reflow


def _font_key(name: str) -> str:
    name = name.split("+", 1)[-1].casefold()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name.removesuffix("regular").removesuffix("mt")


def _same_visible_words(left: str, right: str) -> bool:
    # PDF extraction often inserts/removes wrap whitespace. Preserve the actual
    # source display when the processed words and punctuation are unchanged.
    return " ".join(left.split()) == " ".join(right.split())


@dataclass(frozen=True)
class _Geometry:
    media: tuple[float, ...]
    crop: tuple[float, ...]
    rotation: int


class _Fonts:
    def __init__(self, source, directory: Path, language_tag: str | None):
        from .writer import _ordered_font_candidates
        self.directory = directory
        self.language_tag = language_tag
        self.fonts: dict[str, tuple[str, fitz.Font]] = {}
        self.faces: list[str] = []
        self.fallbacks = _ordered_font_candidates("", language_tag=language_tag)
        source_glyphs: dict[str, dict[int, int]] = {}
        for page in source:
            for trace in page.get_texttrace():
                mapping = source_glyphs.setdefault(_font_key(trace["font"]), {})
                for codepoint, glyph, *_ in trace["chars"]:
                    if 0 < codepoint <= 0x10ffff and glyph > 0:
                        mapping[codepoint] = glyph
        for page in source:
            for xref, extension, _, name, *_ in page.get_fonts(full=True):
                clean = name.split("+", 1)[-1]
                if _font_key(clean) in self.fonts:
                    continue
                try:
                    buffer = source.extract_font(xref)[3] if xref else b""
                    if buffer:
                        buffer = _recover_unicode_cmap(buffer, source_glyphs.get(_font_key(clean), {}))
                    font = fitz.Font(fontbuffer=buffer) if buffer else fitz.Font(fontname=clean)
                    if not buffer:
                        buffer = font.buffer
                    registered = self._register(buffer, extension if extension in {"ttf", "otf"} else "ttf", font)
                    self.fonts[_font_key(clean)] = registered
                    self.fonts.setdefault(_font_key(font.name), registered)
                except Exception:
                    # Never claim an absent source font was recovered.
                    continue

    def _register(self, buffer: bytes, extension: str, font: fitz.Font):
        family = f"RedocxFont{len(self.faces)}"
        filename = family + "." + extension
        (self.directory / filename).write_bytes(buffer)
        self.faces.append(f"@font-face{{font-family:{family};src:url('{filename}');}}")
        return family, font

    @staticmethod
    def _required(text: str) -> set[int]:
        return {ord(c) for c in text if not c.isspace() and not unicodedata.category(c).startswith("C") and not 0xfe00 <= ord(c) <= 0xfe0f}

    def family(self, name: str, text: str) -> str:
        from .writer import _ordered_font_candidates
        required = self._required(text)
        names = []
        covered: set[int] = set()
        original = self.fonts.get(_font_key(name))
        if original is not None:
            family, font = original
            if not required:
                return family
            supported = {c for c in required if font.has_glyph(c)}
            if supported:
                names.append(family)
                covered.update(supported)
        for path in _ordered_font_candidates(text, language_tag=self.language_tag):
            if required <= covered:
                break
            key = str(path)
            if key not in self.fonts:
                try:
                    buffer = path.read_bytes()
                    self.fonts[key] = self._register(buffer, path.suffix.lstrip("."), fitz.Font(fontbuffer=buffer))
                except Exception:
                    continue
            family, font = self.fonts[key]
            added = {c for c in required - covered if font.has_glyph(c)}
            if added:
                names.append(family)
                covered.update(added)
        if required - covered:
            raise RuntimeError("The source and bundled fonts do not cover all generated PDF characters.")
        return ",".join(names) or "sans-serif"


def _unit_html(unit: LayoutUnit, value: str, fonts: _Fonts) -> str:
    spans = list(unit.spans) or [{"text": unit.text, "font": "", "size": 11, "color": 0}]
    pieces = distribute_text([s.get("text", "") for s in spans], value)
    markup = []
    preserve_wraps = SequenceMatcher(None, unit.text, value, autojunk=False).ratio() > .90
    previous_baseline = None
    for span, text in zip(spans, pieces):
        if not text:
            continue
        size = max(1., float(span.get("size", 11)))
        color = int(span.get("color", 0)) & 0xffffff
        family = fonts.family(str(span.get("font", "")), text)
        baseline = round(span.get("origin", (0, 0))[1], 1)
        if preserve_wraps and previous_baseline is not None and abs(baseline - previous_baseline) > .5:
            markup.append("<br>")
        previous_baseline = baseline
        weight = "font-weight:bold;" if int(span.get("flags", 0)) & 16 else ""
        slant = "font-style:italic;" if int(span.get("flags", 0)) & 2 else ""
        markup.append(f'<span style="font-family:{family};font-size:{size:.4f}pt;{weight}{slant}color:#{color:06x}">{escape(text).replace(chr(10), "<br>")}</span>')
    direction = ' dir="rtl"' if fonts.language_tag == "ar" else ""
    dominant = max(spans, key=lambda s: len(s.get("text", "")))
    return f'<div{direction} style="font-size:{float(dominant.get("size", 11)):.4f}pt">' + "".join(markup) + "</div>"


def _recover_unicode_cmap(buffer: bytes, source_glyphs: dict[int, int]) -> bytes:
    """Restore missing subset cmaps using the PDF's actual Unicode/glyph trace.

    Some producers intentionally remove a TrueType cmap and keep only the PDF
    ToUnicode mapping. Rebuilding the cmap preserves the real font rather than
    substituting a different face merely because the subset lacks a cmap.
    """
    if not source_glyphs:
        return buffer
    try:
        from fontTools.ttLib import TTFont, newTable
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
        font = TTFont(io.BytesIO(buffer))
        try:
            if font.getBestCmap():
                return buffer
            glyphs = font.getGlyphOrder()
            mapping = {code: glyphs[glyph] for code, glyph in source_glyphs.items() if glyph < len(glyphs)}
            if not mapping:
                return buffer
            table = newTable("cmap")
            table.tableVersion = 0
            subtable = CmapSubtable.newSubtable(12)
            subtable.platformID, subtable.platEncID, subtable.language = 3, 10, 0
            subtable.cmap = mapping
            table.tables = [subtable]
            font["cmap"] = table
            output = io.BytesIO()
            font.save(output)
            return output.getvalue()
        finally:
            font.close()
    except Exception:
        return buffer


def _render_story(html: str, fonts: _Fonts, first_size: tuple[float, float], continuation_size: tuple[float, float], *, page_sizes=None):
    css = "".join(fonts.faces) + "*{box-sizing:border-box;}body{margin:0;padding:0;}div,p{margin:0;padding:0;line-height:1.05;overflow-wrap:anywhere;}"
    story = fitz.Story(html=html, user_css=css, archive=fitz.Archive(str(fonts.directory)))
    stream = io.BytesIO()
    writer = fitz.DocumentWriter(stream)
    more = True
    count = 0
    try:
        while more:
            if count > MAX_CONTINUATION_PAGES:
                raise PdfLayoutError("Generated PDF would require too many continuation pages.")
            width, height = (page_sizes[min(count, len(page_sizes) - 1)] if page_sizes else
                             first_size if count == 0 else continuation_size)
            rect = fitz.Rect(0, 0, width, height)
            device = writer.begin_page(rect)
            more, filled = story.place(rect)
            filled = fitz.Rect(filled)
            story.draw(device)
            writer.end_page()
            if count and more and (filled.is_empty or filled.height < 1):
                raise PdfLayoutError("The source typography cannot fit the available page area without clipping.", can_reflow=True)
            if filled.x1 > rect.x1 + .5:
                raise PdfLayoutError("Generated PDF text would overflow the source column width.", can_reflow=True)
            count += 1
    finally:
        writer.close()
    return fitz.open(stream=stream.getvalue(), filetype="pdf")


def _new_page(document, geometry: _Geometry):
    media = fitz.Rect(geometry.media)
    page = document.new_page(width=media.width, height=media.height)
    page.set_mediabox(media)
    page.set_cropbox(fitz.Rect(geometry.crop))
    return page


def _placement(unit, units, crop):
    rect = fitz.Rect(unit.bbox) & fitz.Rect(0, 0, crop.width, crop.height)
    below = [u.bbox[1] for _, u in units if u.bbox[1] > rect.y1 and u.bbox[0] < rect.x1 and u.bbox[2] > rect.x0]
    bottom = min(below) if below else crop.height - 12
    rect.y1 = min(bottom - .2, max(rect.y1, rect.y0 + max(s.get("size", 11) for s in unit.spans) * 1.2))
    if rect.is_empty or rect.width < 2 or rect.height < 2:
        raise PdfLayoutError("A source PDF text region is outside the printable page.")
    continuation = fitz.Rect(max(12, rect.x0), 36, max(rect.x0 + 12, min(crop.width - 12, rect.x1)), crop.height - 36)
    return rect, continuation


def _has_parallel_columns(units):
    for index, (_, unit) in enumerate(units):
        a = fitz.Rect(unit.bbox)
        for _, other in units[index + 1:]:
            b = fitz.Rect(other.bbox)
            overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
            if overlap > min(a.height, b.height) * .5 and (a.x1 + 3 < b.x0 or b.x1 + 3 < a.x0):
                return True
    return False


def _flow_page(document, index, geometry, units, replacements, fonts):
    """Reflow a whole source page in order instead of splitting paragraphs
    around later material. Complex columns/graphics require an editable source.
    """
    page = document[index]
    crop = fitz.Rect(geometry.crop)
    bounds = fitz.Rect(units[0][1].bbox)
    for _, unit in units[1:]:
        bounds |= fitz.Rect(unit.bbox)
    bounds &= fitz.Rect(12, 12, crop.width - 12, crop.height - 12)
    if _has_parallel_columns(units):
        raise PdfLayoutError("Processed text exceeds a PDF column at its original font size. Use the editable DOCX source to preserve this complex layout.")
    if any(not u.ocr for _, u in units):
        if any(fitz.Rect(info["bbox"]).intersects(bounds) for info in page.get_image_info()):
            raise PdfLayoutError("Expanded text would overlap a PDF graphic. Use the editable DOCX source for layout-safe reflow.")
    # Keep the original left/right text margins and continue on pages with the
    # same geometry. This changes wrapping, never font size or reading order.
    frame = fitz.Rect(bounds.x0, bounds.y0, bounds.x1, crop.height - 36)
    blocks = []
    for key, unit in units:
        value = replacements[key].strip()
        if not value:
            continue
        inset = min(max(0, unit.bbox[0] - frame.x0), frame.width * .65)
        blocks.append(f'<section style="margin-left:{inset:.3f}pt;margin-bottom:5pt">' + _unit_html(unit, value, fonts) + "</section>")
    rendered = _render_story("".join(blocks), fonts, (frame.width, frame.height), (frame.width, frame.height))
    try:
        if all(u.ocr for _, u in units):
            # Raster glyphs have no recoverable original font identity. A clean
            # page prevents old scanned words from surviving behind a reflow.
            page.add_redact_annot(bounds, fill=(1, 1, 1), cross_out=False)
            page.apply_redactions(images=2, graphics=0)
        else:
            page.add_redact_annot(bounds, fill=False, cross_out=False)
            page.apply_redactions(images=0, graphics=1)
        page.show_pdf_page(frame, rendered, 0, keep_proportion=False)
        continuations = []
        for part in range(1, len(rendered)):
            next_page = _new_page(document, geometry)
            continuations.append(next_page.number)
            next_page.show_pdf_page(frame, rendered, part, keep_proportion=False)
            next_page.set_rotation(geometry.rotation)
        logger.info("AI PDF page reflowed at source point sizes with %d continuation pages", len(continuations))
        return continuations
    finally:
        rendered.close()


def write_pdf_from_source(layout: DocumentLayout, destination: Path, content: str, *, language_tag: str | None = None) -> None:
    if layout.replacements is not None and all(_same_visible_words(layout.replacements[key], unit.text) for key, unit in zip(layout.markers, layout.units)):
        shutil.copyfile(layout.source_path, destination)
        return
    with fitz.open(layout.source_path) as original, TemporaryDirectory(prefix="redocx-layout-fonts-") as work:
        geometry = [_Geometry(tuple(p.mediabox), tuple(p.cropbox), p.rotation) for p in original]
        fonts = _Fonts(original, Path(work), language_tag)
        if layout.replacements is None:
            _write_generated_pdf(original, geometry, layout, destination, content, fonts)
            return
        if set(layout.replacements) != set(layout.markers):
            raise ValueError("Source PDF layout/output alignment is incomplete.")
        document = fitz.open(layout.source_path)
        try:
            continuations = {}
            by_page = {}
            for key, unit in zip(layout.markers, layout.units):
                by_page.setdefault(unit.page, []).append((key, unit))
            for index, current_geometry in enumerate(geometry):
                document[index].set_rotation(0)
                units = by_page.get(index, [])
                changed = [(key, unit) for key, unit in units if not _same_visible_words(layout.replacements[key], unit.text)]
                plans = []
                try:
                    overflow = False
                    for key, unit in changed:
                        value = layout.replacements[key].strip()
                        if not value:
                            continue
                        frame, next_frame = _placement(unit, units, fitz.Rect(current_geometry.crop))
                        try:
                            rendered = _render_story(_unit_html(unit, value, fonts), fonts, (frame.width, frame.height), (next_frame.width, next_frame.height))
                        except PdfLayoutError as exc:
                            if not exc.can_reflow:
                                raise
                            # A word can exceed its original short heading frame
                            # while fitting the page. Reuse the guarded reflow
                            # path; actual columns and graphics remain protected.
                            overflow = True
                            break
                        plans.append((frame, rendered))
                        if len(rendered) > 1:
                            overflow = True
                            break
                    if overflow:
                        continuations[index] = _flow_page(document, index, current_geometry, units, layout.replacements, fonts)
                    elif changed:
                        page = document[index]
                        for _, unit in changed:
                            page.add_redact_annot(fitz.Rect(unit.bbox), fill=(1, 1, 1) if unit.ocr else False, cross_out=False)
                        page.apply_redactions(images=2 if any(u.ocr for _, u in changed) else 0, graphics=0)
                        for frame, rendered in plans:
                            if rendered[0].get_contents():
                                page.show_pdf_page(frame, rendered, 0, keep_proportion=False)
                finally:
                    for _, rendered in plans:
                        rendered.close()
                document[index].set_rotation(current_geometry.rotation)
                if len(document) - len(geometry) > MAX_CONTINUATION_PAGES:
                    raise PdfLayoutError("Generated PDF exceeds the continuation-page limit.")
            if any(continuations.values()):
                order = [number for i in range(len(geometry)) for number in [i, *continuations.get(i, [])]]
                document.select(order)
            document.save(destination, garbage=4, deflate=True)
        finally:
            document.close()


def _write_generated_pdf(original, geometry, layout, destination, content, fonts):
    from .writer import _split_paragraphs
    spans = [s for unit in layout.units for s in unit.spans if s.get("text", "").strip()]
    if not spans:
        raise ValueError("The source PDF has no typography to inherit.")
    counts = Counter((s.get("font", ""), round(s.get("size", 11), 2)) for s in spans for _ in range(min(len(s["text"]), 500)))
    name, size = counts.most_common(1)[0][0]
    family = fonts.family(name, content)
    direction = ' dir="rtl"' if fonts.language_tag == "ar" else ""
    html = f'<div{direction} style="font-family:{family};font-size:{size}pt">' + "".join(f'<p style="margin-bottom:{size * .5}pt">{escape(p).replace(chr(10), "<br>")}</p>' for p in _split_paragraphs(content)) + "</div>"
    first = fitz.Rect(geometry[0].crop)
    source_boxes = [fitz.Rect(u.bbox) for u in layout.units if u.page == 0]
    left = max(12, min(b.x0 for b in source_boxes))
    right = min(first.width - 12, max(b.x1 for b in source_boxes))
    top = max(12, min(b.y0 for b in source_boxes))
    frame = fitz.Rect(left, top, right, first.height - 36)
    right_margin = first.width - right
    frames = [fitz.Rect(min(left, fitz.Rect(g.crop).width * .25),
                        min(top, fitz.Rect(g.crop).height * .25),
                        fitz.Rect(g.crop).width - right_margin,
                        fitz.Rect(g.crop).height - 36) for g in geometry]
    rendered = _render_story(html, fonts, (frame.width, frame.height), (frame.width, frame.height),
                             page_sizes=[(r.width, r.height) for r in frames])
    output = fitz.open()
    try:
        for i in range(len(rendered)):
            current = geometry[min(i, len(geometry) - 1)]
            target = frames[min(i, len(frames) - 1)]
            page = _new_page(output, current)
            page.show_pdf_page(target, rendered, i, keep_proportion=False)
            page.set_rotation(current.rotation)
        output.save(destination, garbage=4, deflate=True)
    finally:
        rendered.close()
        output.close()
