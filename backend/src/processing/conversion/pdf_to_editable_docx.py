from __future__ import annotations

"""Reconstruct the visible PDF page in Word using positioned, editable text."""

from dataclasses import dataclass, replace
from itertools import groupby
from io import BytesIO
import math
import os
from pathlib import Path
import re
import shutil
from typing import Callable, Iterator
import subprocess
import time
from xml.etree import ElementTree

import fitz
import numpy as np
from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph
from PIL import Image, ImageChops, ImageFont


class EditablePdfDocxError(RuntimeError):
    """A positioned Word reconstruction could not be generated."""


@dataclass(frozen=True)
class PageLink:
    target: str
    rect: fitz.Rect


@dataclass(frozen=True)
class TextStyle:
    family: str
    size: float
    bold: bool = False
    italic: bool = False
    color: int = 0
    serif: bool = False
    mono: bool = False


@dataclass(frozen=True)
class Glyph:
    text: str
    start: float
    end: float
    baseline: float
    style: TextStyle
    link: int | None


@dataclass(frozen=True)
class TextLine:
    glyphs: list[Glyph]
    direction: tuple[int, int]
    bounds: fitz.Rect
    block: int | None = None


@dataclass(frozen=True)
class TextRun:
    text: str
    width: float
    baseline: float
    style: TextStyle
    link: int | None


_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DRAWING_WORD_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHAPE_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_NATIVE_FLAGS = fitz.TEXTFLAGS_RAWDICT & ~(fitz.TEXT_PRESERVE_IMAGES | fitz.TEXT_PRESERVE_LIGATURES)
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_FONT_ALIASES = {
    "Arial": "Liberation Sans",
    "Calibri": "Carlito",
    "Cambria": "Caladea",
    "Courier New": "Liberation Mono",
    "Times New Roman": "Liberation Serif",
}
_MAX_PAGE_POINTS = 1584  # Word limits a page dimension to 22 inches.
_MAX_RASTER_PIXELS = 12_000_000
_MAX_ARTWORK_BYTES = 128 * 1024 * 1024
_MIN_OCR_CONFIDENCE = 88
_OCR_FONT_SIZE = 48
_OCR_MATCH_MINIMUM = 0.38
_BASELINE_FRACTION = 0.8  # Baseline of an exact-height Word line, measured from its top.
_FONT_METRIC_SIZE = 256
_WORD_TEXT_MODE = {(1, 0): "horz", (0, -1): "vert270", (0, 1): "vert"}
_SYMBOLS = {"\uf0b7": "•", "\uf0a7": "▪"}


class FontMetrics:
    """Resolve the same installed fonts used by the Word audit renderer."""

    def __init__(self) -> None:
        self._fc_match = shutil.which("fc-match")
        self._matches: dict[tuple[str, bool, bool], ImageFont.FreeTypeFont | None] = {}
        self._faces: dict[tuple[str, bool, bool, bool, bool], tuple[str, ImageFont.FreeTypeFont | None]] = {}

    def _installed(self, family: str, bold: bool, italic: bool) -> ImageFont.FreeTypeFont | None:
        key = family, bold, italic
        if key in self._matches:
            return self._matches[key]
        font = None
        if self._fc_match and family:
            # Fontconfig patterns use punctuation as delimiters.
            escaped = re.sub(r"([\\:,_=-])", r"\\\1", family)
            pattern = f"{escaped}:weight={'bold' if bold else 'regular'}:slant={'italic' if italic else 'roman'}"
            try:
                result = subprocess.run(
                    [self._fc_match, "--format", "%{family}\n%{file}\n%{index}", "--", pattern],
                    check=True, capture_output=True, text=True, timeout=3,
                )
                families, file, index = result.stdout.splitlines()
                if family.casefold() in {name.strip().casefold() for name in families.split(",")}:
                    font = ImageFont.truetype(file, _FONT_METRIC_SIZE, index=int(index or 0))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        self._matches[key] = font
        return font

    def face(self, style: TextStyle) -> tuple[str, ImageFont.FreeTypeFont | None]:
        key = style.family, style.bold, style.italic, style.serif, style.mono
        if key not in self._faces:
            family = style.family
            font = self._installed(family, style.bold, style.italic)
            if font is None and family in _FONT_ALIASES:
                font = self._installed(_FONT_ALIASES[family], style.bold, style.italic)
            if font is None:
                fallback = "Liberation Mono" if style.mono else "Liberation Serif" if style.serif else "Liberation Sans"
                font = self._installed(fallback, style.bold, style.italic)
                if family not in _FONT_ALIASES:
                    family = fallback
            self._faces[key] = family, font
        return self._faces[key]


def write_editable_pdf_docx(
    source_path: Path,
    output_path: Path,
    *,
    links: dict[int, list[PageLink]],
    font_families: Callable[[fitz.Page, dict[int, str]], dict[str, str]],
    fallback_family: Callable[[str], str],
    max_pages: int,
    artwork_dpi: int,
    ocr_dpi: int,
    ocr_language: str,
    ocr_timeout: int,
) -> list[str]:
    document = Document()
    font_cache: dict[int, str] = {}
    metrics = FontMetrics()
    expected_text: list[str] = []
    artwork_bytes = 0
    object_id = 0
    ocr_deadline: float | None = None

    with fitz.open(source_path) as pdf:
        if pdf.is_encrypted or pdf.needs_pass or not pdf.page_count:
            raise EditablePdfDocxError("PDF is locked or has no pages.")
        if pdf.page_count > max_pages:
            raise EditablePdfDocxError(f"Editable PDF-to-Word conversion is capped at {max_pages} pages.")

        for page in pdf:
            rect = page.rect
            if not (7.2 <= rect.width <= _MAX_PAGE_POINTS and 7.2 <= rect.height <= _MAX_PAGE_POINTS):
                raise EditablePdfDocxError(f"PDF page {page.number + 1} is outside Word's supported page size.")

            page_links = links.get(page.number + 1, [])
            families = font_families(page, font_cache)
            blocks = page.get_text("rawdict", flags=_NATIVE_FLAGS)["blocks"]
            has_text = any(
                span.get("alpha", 255) and (span.get("char_flags", 24) & 24)
                for block in blocks for line in block.get("lines", []) for span in line["spans"]
            )
            artwork, visible_ink = _native_artwork(page, artwork_dpi) if has_text else (None, None)
            lines = _native_lines(page, page_links, families, fallback_family, blocks, visible_ink)
            visible_chars = sum(glyph.text.isalnum() for line in lines for glyph in line.glyphs)
            mostly_raster = visible_chars < 24 and any(
                (fitz.Rect(image["bbox"]) & rect).get_area() >= rect.get_area() * 0.5
                for image in page.get_image_info()
            )
            if not lines or mostly_raster:
                if ocr_deadline is None:
                    ocr_deadline = time.monotonic() + max(1, ocr_timeout)
                scanned_lines, artwork, actual_dpi = _scan_page(
                    page, metrics, page_links, ocr_dpi, ocr_language, ocr_deadline,
                )
                lines.extend(scanned_lines)
                scale = max(0.01, min(1.0, artwork_dpi / actual_dpi))
                if scale < 1:
                    artwork = artwork.resize(
                        (max(1, round(artwork.width * scale)), max(1, round(artwork.height * scale))),
                        Image.Resampling.LANCZOS,
                    )

            section = document.sections[0] if not expected_text else document.add_section(WD_SECTION.NEW_PAGE)
            _configure_section(section, rect, lines)
            anchor_paragraph = document.add_paragraph()
            anchor_paragraph.paragraph_format.line_spacing = Pt(1)
            anchor_paragraph.paragraph_format.space_before = Pt(0)
            anchor_paragraph.paragraph_format.space_after = Pt(0)
            if artwork is not None and (cropped := _crop_artwork(artwork, rect)) is not None:
                image, position = cropped
                stream = _png(image)
                artwork_bytes += stream.getbuffer().nbytes
                if artwork_bytes > _MAX_ARTWORK_BYTES:
                    raise EditablePdfDocxError("PDF-to-Word conversion exceeded the page artwork budget.")
                object_id += 1
                _add_picture(anchor_paragraph, stream, position, object_id, behind=True)

            used_links: set[int] = set()
            page_text: list[str] = []
            for group in _group_text_lines(lines):
                object_id += 1
                paragraphs = _add_text_box(anchor_paragraph, group, object_id)
                for paragraph, (_, runs) in zip(paragraphs, group):
                    _add_runs(paragraph, runs, page_links, metrics, used_links)
                    page_text.append("".join(run.text for run in runs))

            # Some links cover a logo, a drawing, or invisible source text.
            # Preserve the original clickable region without manufacturing visible text.
            for index, link in enumerate(page_links):
                if index in used_links:
                    continue
                position = link.rect & rect
                if position.is_empty:
                    continue
                object_id += 1
                _add_picture(
                    anchor_paragraph, _png(Image.new("RGBA", (1, 1))), position,
                    object_id, behind=False, target=link.target,
                )

            expected_text.append("\n".join(page_text))

    if not any(character.isalnum() for page_text in expected_text for character in page_text):
        raise EditablePdfDocxError("The PDF did not contain readable text for an editable Word document.")
    document.save(output_path)
    return expected_text


def _native_lines(
    page: fitz.Page, links: list[PageLink], families: dict[str, str], fallback_family: Callable[[str], str],
    blocks: list[dict], visible_ink: VisibleInk | None,
) -> list[TextLine]:
    lines: list[TextLine] = []
    rotation = page.rotation_matrix
    for block_index, block in enumerate(blocks):
        for line in block.get("lines", []):
            glyphs: list[Glyph] = []
            bounds = fitz.Rect()
            direction = fitz.Point(line["dir"]) * rotation - fitz.Point(0, 0) * rotation
            axis = (round(direction.x), round(direction.y))
            for span in line["spans"]:
                if not span.get("alpha", 255) or (span.get("char_flags", 24) & 24) == 0:
                    continue
                name = span["font"]
                flags = span["flags"]
                family = _INVALID_XML_CHARS.sub("", families.get(name) or fallback_family(name))[:120]
                size = max(1.0, min(500.0, round(float(span["size"]) * 2) / 2))
                style = TextStyle(
                    family, size,
                    bold=bool(flags & 16 or "bold" in name.casefold()),
                    italic=bool(flags & 2 or re.search(r"italic|oblique", name, re.I)),
                    color=span["color"], serif=bool(flags & 4), mono=bool(flags & 8),
                )
                last_visible = False
                for char in span["chars"]:
                    value = _INVALID_XML_CHARS.sub("", char["c"])
                    if not value:
                        continue
                    if value.isspace():
                        value = " "
                    origin = fitz.Point(char["origin"]) * rotation
                    box = fitz.Rect(char["bbox"]) * rotation
                    if visible_ink is not None and not value.isspace():
                        # MuPDF decomposes ligatures into a full-width glyph
                        # followed by zero-advance characters.
                        if not box.is_empty:
                            last_visible = visible_ink.intersects(box)
                        if not last_visible:
                            continue
                    start, baseline = _project(origin, axis)
                    end = max(_project(point, axis)[0] for point in (box.tl, box.tr, box.bl, box.br))
                    link = _link_index(box, links)
                    glyph_style = style
                    if value in _SYMBOLS:
                        value = _SYMBOLS[value]
                        glyph_style = replace(style, family="Arial", serif=False)
                    glyphs.append(Glyph(value, start, end, baseline, glyph_style, link))
                    bounds |= box
            while glyphs and glyphs[-1].text.isspace():
                glyphs.pop()
            if glyphs:
                first_visible = next(index for index, glyph in enumerate(glyphs) if not glyph.text.isspace())
                glyphs = glyphs[first_visible:]
                if axis not in _WORD_TEXT_MODE or math.hypot(direction.x - axis[0], direction.y - axis[1]) > 0.08:
                    raise EditablePdfDocxError(f"PDF page {page.number + 1} uses unsupported angled text.")
                lines.append(TextLine(glyphs, axis, bounds, block_index))
    return lines


def _project(point: fitz.Point, direction: tuple[int, int]) -> tuple[float, float]:
    dx, dy = direction
    return point.x * dx + point.y * dy, point.y * dx - point.x * dy


def _link_index(box: fitz.Rect, links: list[PageLink]) -> int | None:
    center = (box.tl + box.br) * 0.5
    for index, link in enumerate(links):
        if link.rect.contains(center):
            return index
    return None


def _line_runs(glyphs: list[Glyph]) -> list[TextRun]:
    groups = [
        list(group)
        for _, group in groupby(glyphs, lambda glyph: (glyph.style, glyph.link, glyph.text.isspace()))
    ]
    runs: list[TextRun] = []
    for index, group in enumerate(groups):
        first = group[0]
        end = max(glyph.end for glyph in group)
        following = groups[index + 1][0] if index + 1 < len(groups) else None
        value = "".join(glyph.text for glyph in group)
        gap = 0.0
        if following is not None and following.start > first.start:
            gap = following.start - end
            if value.isspace() or gap <= first.style.size * 0.18:
                end = following.start
                gap = 0.0
        runs.append(TextRun(value, max(0.1, end - first.start), first.baseline, first.style, first.link))
        if gap > 0 and following is not None and not following.text.isspace():
            runs.append(TextRun(" ", gap, first.baseline, first.style, None))
    return runs


def _line_height(runs: list[TextRun]) -> float:
    return max(run.style.size for run in runs)


def _group_text_lines(lines: list[TextLine]) -> Iterator[list[tuple[TextLine, list[TextRun]]]]:
    """Keep adjacent, aligned lines of the same source block together for editing."""
    group: list[tuple[TextLine, list[TextRun]]] = []
    for line in lines:
        runs = _line_runs(line.glyphs)
        if group:
            previous, previous_runs = group[-1]
            height, previous_height = _line_height(runs), _line_height(previous_runs)
            gap = line.glyphs[0].baseline - previous.glyphs[0].baseline
            natural_gap = previous_height * (1 - _BASELINE_FRACTION) + height * _BASELINE_FRACTION
            if not (
                line.block is not None and line.block == previous.block
                and line.direction == previous.direction == (1, 0)
                and abs(line.glyphs[0].start - group[0][0].glyphs[0].start) <= 1.0
                and natural_gap - 0.05 <= gap <= natural_gap + 0.8 * height
            ):
                yield group
                group = []
        group.append((line, runs))
    if group:
        yield group


def _configure_section(section, rect: fitz.Rect, lines: list[TextLine]) -> None:
    section.orientation = WD_ORIENT.LANDSCAPE if rect.width > rect.height else WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = Pt(rect.width), Pt(rect.height)
    bounds = fitz.Rect()
    for line in lines:
        bounds |= line.bounds
    if bounds.is_empty:
        bounds = rect
    section.left_margin = Pt(max(0, min(rect.width / 3, bounds.x0)))
    section.right_margin = Pt(max(0, min(rect.width / 3, rect.width - bounds.x1)))
    section.top_margin = Pt(max(0, min(rect.height / 3, bounds.y0)))
    section.bottom_margin = Pt(max(0, min(rect.height / 3, rect.height - bounds.y1)))
    section.header_distance = section.footer_distance = Pt(0)


def _anchor(rect: fitz.Rect, object_id: int, *, behind: bool):
    return parse_xml(f"""
        <wp:anchor xmlns:wp="{_DRAWING_WORD_NS}" simplePos="0"
            relativeHeight="{object_id}" behindDoc="{int(behind)}" locked="0"
            layoutInCell="0" allowOverlap="1" distT="0" distB="0" distL="0" distR="0">
          <wp:simplePos x="0" y="0"/>
          <wp:positionH relativeFrom="page"><wp:posOffset>{Pt(rect.x0)}</wp:posOffset></wp:positionH>
          <wp:positionV relativeFrom="page"><wp:posOffset>{Pt(rect.y0)}</wp:posOffset></wp:positionV>
          <wp:extent cx="{Pt(rect.width)}" cy="{Pt(rect.height)}"/>
          <wp:wrapNone/>
          <wp:docPr id="{object_id}" name="PDF object {object_id}"/>
          <wp:cNvGraphicFramePr/>
        </wp:anchor>
    """)


def _add_text_box(
    host: Paragraph, group: list[tuple[TextLine, list[TextRun]]], object_id: int,
) -> list[Paragraph]:
    line, first_runs = group[0]
    height = _line_height(first_runs)
    baseline = line.glyphs[0].baseline
    start = min(entry.glyphs[0].start for entry, _ in group)
    length = max(
        entry.glyphs[0].start - start + sum(run.width for run in runs)
        for entry, runs in group
    ) + max(6, height * 0.7)
    ascent = height * _BASELINE_FRACTION
    last_line, last_runs = group[-1]
    thickness = last_line.glyphs[0].baseline - baseline + ascent + _line_height(last_runs) * 0.65 + 2
    if line.direction == (1, 0):
        x, y = start, baseline - ascent
        width, box_height = length, thickness
    elif line.direction == (0, -1):
        x, y = baseline - ascent, -start - length
        width, box_height = thickness, length
    else:
        x, y = -baseline - thickness + ascent, start
        width, box_height = thickness, length
    rect = fitz.Rect(x, y, x + width, y + box_height)
    anchor = _anchor(rect, object_id, behind=False)
    # Writer can resize the cross-axis of a vertical textbox under autofit.
    wrap, fit = ("square", "spAutoFit") if line.direction == (1, 0) else ("none", "noAutofit")
    graphic = parse_xml(f"""
        <a:graphic xmlns:a="{_DRAWING_NS}" xmlns:w="{_WORD_NS}" xmlns:wps="{_SHAPE_NS}">
          <a:graphicData uri="{_SHAPE_NS}">
            <wps:wsp>
              <wps:cNvSpPr txBox="1"/>
              <wps:spPr>
                <a:xfrm><a:off x="0" y="0"/><a:ext cx="{Pt(width)}" cy="{Pt(box_height)}"/></a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
                <a:noFill/><a:ln><a:noFill/></a:ln>
              </wps:spPr>
              <wps:txbx><w:txbxContent/></wps:txbx>
              <wps:bodyPr lIns="0" tIns="0" rIns="0" bIns="0"
                  anchor="t" vert="{_WORD_TEXT_MODE[line.direction]}" wrap="{wrap}"><a:{fit}/></wps:bodyPr>
            </wps:wsp>
          </a:graphicData>
        </a:graphic>
    """)
    anchor.append(graphic)
    host.add_run()._element.add_drawing(anchor)
    content = next(graphic.iter(qn("w:txbxContent")))
    paragraphs: list[Paragraph] = []
    previous_bottom = baseline - ascent
    for entry, runs in group:
        size = _line_height(runs)
        top = entry.glyphs[0].baseline - size * _BASELINE_FRACTION
        element = OxmlElement("w:p")
        content.append(element)
        paragraph = Paragraph(element, host)
        formatting = paragraph.paragraph_format
        formatting.left_indent = Pt(entry.glyphs[0].start - start)
        formatting.space_before = Pt(max(0, top - previous_bottom))
        formatting.space_after = Pt(0)
        formatting.line_spacing = Pt(size)
        previous_bottom = top + size
        paragraphs.append(paragraph)
    return paragraphs


def _add_runs(
    paragraph: Paragraph, runs: list[TextRun], links: list[PageLink], metrics: FontMetrics, used_links: set[int],
) -> None:
    baseline = runs[0].baseline
    for link_index, group in groupby(runs, lambda run: run.link):
        hyperlink = None
        if link_index is not None:
            used_links.add(link_index)
            hyperlink = OxmlElement("w:hyperlink")
            relationship = paragraph.part.relate_to(links[link_index].target, RT.HYPERLINK, is_external=True)
            hyperlink.set(qn("r:id"), relationship)
            paragraph._p.append(hyperlink)
        for token in group:
            run = paragraph.add_run(token.text)
            if hyperlink is not None:
                hyperlink.append(run._element)
            style = token.style
            family, font = metrics.face(style)
            run.font.name = family
            run.font.size = Pt(style.size)
            run.font.bold = style.bold
            run.font.italic = style.italic
            run.font.color.rgb = RGBColor.from_string(f"{style.color:06X}")
            properties = run._element.get_or_add_rPr()
            for attribute in ("eastAsia", "cs"):
                properties.rFonts.set(qn(f"w:{attribute}"), family)
            _set_run_property(properties, "szCs", round(style.size * 2))

            offset = round((baseline - token.baseline) * 2)
            if offset:
                _set_run_property(properties, "position", offset)
            if font is None:
                continue
            natural = font.getlength(token.text) * style.size / _FONT_METRIC_SIZE
            if natural <= 0 or abs(token.width - natural) < 0.12:
                continue
            if token.text.isspace():
                _set_run_property(properties, "spacing", round(20 * (token.width - natural) / len(token.text)))
            else:
                _set_run_property(properties, "w", max(25, min(300, round(100 * token.width / natural))))


def _set_run_property(properties, name: str, value: int) -> None:
    node = OxmlElement(f"w:{name}")
    node.set(qn("w:val"), str(value))
    properties.append(node)


def _render_page(page: fitz.Page, dpi: int) -> tuple[Image.Image, int]:
    scale = min(max(1.0, min(400, dpi) / 72), math.sqrt(_MAX_RASTER_PIXELS / page.rect.get_area()))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples), max(1, round(scale * 72))


class VisibleInk:
    """Measure native glyphs that actually contribute to the painted page."""

    def __init__(self, original: Image.Image, artwork: Image.Image, rect: fitz.Rect) -> None:
        difference = ImageChops.difference(original, artwork)
        self._mask = np.asarray(difference).max(axis=2) >= 12
        self._width, self._height = artwork.size
        self._sx, self._sy = self._width / rect.width, self._height / rect.height

    def intersects(self, rect: fitz.Rect) -> bool:
        x0 = max(0, round(rect.x0 * self._sx))
        y0 = max(0, round(rect.y0 * self._sy))
        x1 = min(self._width, max(x0 + 1, round(rect.x1 * self._sx)))
        y1 = min(self._height, max(y0 + 1, round(rect.y1 * self._sy)))
        return bool(self._mask[y0:y1, x0:x1].any())


def _native_artwork(page: fitz.Page, dpi: int) -> tuple[Image.Image | None, VisibleInk | None]:
    if not (page.first_annot or page.first_widget or any(
        kind not in {"fill-text", "stroke-text", "ignore-text"} for kind, _ in page.get_bboxlog()
    )):
        return None, None
    original, _ = _render_page(page, dpi)
    page.add_redact_annot(page.rect * page.derotation_matrix, fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    artwork, _ = _render_page(page, dpi)
    return artwork, VisibleInk(original, artwork, page.rect)


def _crop_artwork(image: Image.Image, rect: fitz.Rect) -> tuple[Image.Image, fitz.Rect] | None:
    bounds = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    if not bounds:
        return None
    x0, y0, x1, y1 = bounds
    sx, sy = rect.width / image.width, rect.height / image.height
    return image.crop(bounds), fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)


def _png(image: Image.Image) -> BytesIO:
    stream = BytesIO()
    image.save(stream, "PNG")
    stream.seek(0)
    return stream


def _add_picture(host: Paragraph, stream: BytesIO, rect: fitz.Rect, object_id: int, *,
                 behind: bool, target: str | None = None) -> None:
    shape = host.add_run().add_picture(stream, width=Pt(rect.width), height=Pt(rect.height))
    inline = shape._inline
    anchor = _anchor(rect, object_id, behind=behind)
    if target:
        click = OxmlElement("a:hlinkClick")
        click.set(qn("r:id"), host.part.relate_to(target, RT.HYPERLINK, is_external=True))
        anchor.find(qn("wp:docPr")).append(click)
    anchor.append(inline.find(qn("a:graphic")))
    inline.getparent().replace(inline, anchor)


def _scan_page(
    page: fitz.Page, metrics: FontMetrics, links: list[PageLink], dpi: int, language: str, deadline: float,
) -> tuple[list[TextLine], Image.Image, int]:
    image, actual_dpi = _render_page(page, dpi)
    # A blank page can be part of a genuinely editable multi-page document.
    if image.getextrema() == ((255, 255), (255, 255), (255, 255)):
        return [], image, actual_dpi
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise EditablePdfDocxError("The PDF-to-Word OCR runtime could not be found.")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EditablePdfDocxError("PDF-to-Word OCR exceeded its time budget.")
    try:
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "-l", language, "--dpi", str(actual_dpi),
             "-c", "hocr_font_info=1", "hocr"],
            input=_png(image).getvalue(), capture_output=True, check=True,
            timeout=min(25, remaining), env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
        hocr = ElementTree.fromstring(result.stdout)
    except (OSError, subprocess.SubprocessError, ElementTree.ParseError) as exc:
        raise EditablePdfDocxError(f"OCR could not read PDF page {page.number + 1}.") from exc

    sx, sy = page.rect.width / image.width, page.rect.height / image.height
    output = image.copy()
    fonts = _ocr_fonts(metrics)
    lines: list[TextLine] = []
    for node in hocr.iter():
        if node.get("class") not in {"ocr_line", "ocr_header", "ocr_caption", "ocr_textfloat"}:
            continue
        props = _ocr_properties(node)
        if "bbox" not in props:
            continue
        line_x, _, _, line_bottom = (int(value) for value in props["bbox"])
        slope, offset = map(float, props.get("baseline", ["0", "0"]))
        words: list[tuple[str, tuple[int, int, int, int], TextStyle]] = []
        for word in node.iter():
            if word.get("class") != "ocrx_word":
                continue
            word_props = _ocr_properties(word)
            text = _INVALID_XML_CHARS.sub("", "".join(word.itertext())).strip()
            confidence = int(word_props.get("x_wconf", ["0"])[0])
            if not 0 < len(text) <= 128 or "bbox" not in word_props or confidence < _MIN_OCR_CONFIDENCE:
                continue
            x0, y0, x1, y1 = (int(value) for value in word_props["bbox"])
            if 0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height:
                box = (x0, y0, x1, y1)
                style = _scan_word_style(image.crop(box), text, fonts, sy)
                if style is not None:
                    words.append((text, box, style))

        glyphs: list[Glyph] = []
        bounds = fitz.Rect()
        for text, box, style in words:
            x0, y0, x1, y1 = box
            rect = fitz.Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy)
            word_baseline = (line_bottom + offset + slope * (x0 - line_x)) * sy
            _remove_scan_word(image, output, box)
            if glyphs:
                previous = glyphs[-1]
                glyphs.append(Glyph(" ", previous.end, rect.x0, previous.baseline, previous.style, None))
            glyphs.append(Glyph(text, rect.x0, rect.x1, word_baseline, style, _link_index(rect, links)))
            bounds |= rect
        if glyphs:
            lines.append(TextLine(glyphs, (1, 0), bounds))
    return lines, output, actual_dpi


def _ocr_properties(node) -> dict[str, list[str]]:
    properties = [entry.split() for entry in node.get("title", "").split(";")]
    return {entry[0]: entry[1:] for entry in properties if len(entry) > 1}


def _ocr_fonts(metrics: FontMetrics) -> list[tuple[TextStyle, ImageFont.FreeTypeFont]]:
    choices = []
    for family, serif in (("Arial", False), ("Times New Roman", True)):
        for bold in (False, True):
            style = TextStyle(family, _OCR_FONT_SIZE, bold=bold, serif=serif)
            _, font = metrics.face(style)
            if font is not None:
                choices.append((style, font.font_variant(size=_OCR_FONT_SIZE)))
    return choices


def _scan_word_style(
    image: Image.Image, text: str, fonts: list[tuple[TextStyle, ImageFont.FreeTypeFont]], points_per_pixel: float,
) -> TextStyle | None:
    colors = np.asarray(image).reshape(-1, 3)
    brightness = colors.mean(axis=1)
    dark, light = np.quantile(brightness, [0.2, 0.85])
    ink = np.median(colors[brightness <= dark], axis=0).astype(int)
    # Colored strokes and poor OCR matches retain their original appearance.
    if int(ink.max() - ink.min()) > 16 or light - brightness.min() < 24:
        return None
    levels = 255 * np.clip((light - brightness) / max(24, light - dark), 0, 1)
    observed = Image.fromarray(levels.reshape(image.height, image.width).astype(np.uint8))
    size = (max(4, min(192, image.width)), max(4, min(32, image.height)))
    source = np.asarray(observed.resize(size, Image.Resampling.BILINEAR), dtype=float)

    best_score = _OCR_MATCH_MINIMUM
    best_style = None
    for style, font in fonts:
        glyphs = font.getmask(text, mode="L")
        mask = Image.frombytes("L", glyphs.size, bytes(glyphs))
        bounds = mask.getbbox()
        if bounds is None:
            continue
        mask = mask.crop(bounds)
        aspect_ratio = (image.width / image.height) / (mask.width / mask.height)
        if not 0.60 <= aspect_ratio <= 1.65:
            continue
        rendered = np.asarray(mask.resize(size, Image.Resampling.BILINEAR), dtype=float)
        score = np.minimum(source, rendered).sum() / np.maximum(source, rendered).sum()
        score *= min(aspect_ratio, 1 / aspect_ratio) ** 0.5
        if score > best_score:
            best_score = score
            points = image.height * points_per_pixel * _OCR_FONT_SIZE / mask.height
            color = int(ink[0]) << 16 | int(ink[1]) << 8 | int(ink[2])
            best_style = replace(style, size=max(2, min(200, round(points * 2) / 2)), color=color)
    return best_style


def _remove_scan_word(source: Image.Image, output: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - 1), max(0, y0 - 1)
    x1, y1 = min(source.width, x1 + 1), min(source.height, y1 + 1)
    top = np.asarray(source.crop((x0, max(0, y0 - 4), x1, max(1, y0))))
    bottom = np.asarray(source.crop((x0, min(y1, source.height - 1), x1, min(source.height, y1 + 4))))
    edge = np.concatenate((top.reshape(-1, 3), bottom.reshape(-1, 3)))
    # A high percentile avoids pulling neighboring ink into a cleaned word.
    paper = tuple(np.rint(np.quantile(edge, 0.8, axis=0)).astype(int))
    output.paste(paper, (x0, y0, x1, y1))
