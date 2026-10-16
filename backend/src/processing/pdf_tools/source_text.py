"""Source-derived text editing, used exclusively by Edit PDF.

The browser identifies a paragraph; it never decides its output font or metrics.
Every edit is resolved against the original PDF, laid out before any deletion,
and rejected when no available font or safe layout can represent it. Embedded
fonts are preferred; substitutions and OCR estimates are disclosed for review.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from hashlib import sha256
from io import BytesIO
from math import atan2, degrees, hypot, isfinite
from pathlib import Path
from statistics import median
from typing import Any, Iterator
import base64
import re
import threading
import time
import unicodedata

import fitz
from .edit_fonts import FontProgram as SourceFont, font_program, installed_font, font_for_character, available_families, recover_embedded_cmap
from .scan_text import recognize_lines, erase_scan_words, ScanTextError
from fontTools.ttLib import TTFont, TTLibError


SOURCE_OPERATIONS = frozenset({"edit_source_text", "insert_source_text"})
MAX_BLOCKS = 1000
MAX_PAGE_CHARACTERS = 200_000
MAX_FONT_BYTES = 8 * 1024 * 1024
MAX_PAGE_FONT_BYTES = 16 * 1024 * 1024
MAX_BACKGROUND_PIXELS = 8_000_000
_PROFILE_SLOTS = threading.BoundedSemaphore(2)
_PROFILE_LOCK = threading.Lock()
_PROFILE_REQUESTS: dict[str, deque[float]] = {}


class SourceTextError(ValueError):
    """A bounded, actionable Edit PDF error with an English/French key."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@contextmanager
def source_profile_lease(identity: str) -> Iterator[None]:
    """Bound inspection work without consuming a document-processing credit.

    This per-worker guard supplements the deployment's ingress limits. No quota,
    entitlement, billing or shared feature limiter is changed by inspection.
    """
    now = time.monotonic()
    key = sha256(identity.encode("utf-8")).hexdigest()
    with _PROFILE_LOCK:
        for old_key in list(_PROFILE_REQUESTS):
            queue = _PROFILE_REQUESTS[old_key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if not queue:
                del _PROFILE_REQUESTS[old_key]
        if key not in _PROFILE_REQUESTS and len(_PROFILE_REQUESTS) >= 4096:
            raise SourceTextError("SOURCE_INSPECTION_BUSY", "Document inspection is busy. Please retry shortly.")
        queue = _PROFILE_REQUESTS.setdefault(key, deque())
        if len(queue) >= 30:
            raise SourceTextError("SOURCE_INSPECTION_BUSY", "Please wait before inspecting another page.")
        queue.append(now)
    if not _PROFILE_SLOTS.acquire(blocking=False):
        raise SourceTextError("SOURCE_INSPECTION_BUSY", "Document inspection is busy. Please retry shortly.")
    try:
        yield
    finally:
        _PROFILE_SLOTS.release()


def document_checksum(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _value(operation: Any) -> str:
    value = getattr(operation, "operation", "")
    return str(getattr(value, "value", value))


def _font_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", value).lower())
    return re.sub(r"regular$", "", normalized)


def _complex_text(text: str) -> bool:
    # These characters need the HarfBuzz / Story shaping path, rather than
    # independently positioned original-font glyphs.
    return any(unicodedata.bidirectional(ch) in {"R", "AL", "AN"}
               or unicodedata.combining(ch) or 0x0900 <= ord(ch) <= 0x0DFF
               or 0x0E00 <= ord(ch) <= 0x0FFF for ch in text)


@dataclass
class SourceStyle:
    font: SourceFont
    size: float
    color: int
    opacity: float
    horizontal_scale: float = 1.0
    baseline_shift: float = 0.0
    native_color: tuple[float, ...] | None = None

    @property
    def key(self) -> tuple:
        return (self.font.key, self.size, self.color, self.opacity, self.horizontal_scale, self.baseline_shift,self.native_color)


@dataclass
class SourceBlock:
    id: str
    text: str
    styles: list[SourceStyle]
    characters: list[tuple[str, tuple[float, ...]]]
    rect: fitz.Rect
    origin: fitz.Point
    direction: tuple[float, float]
    line_step: float
    line_count: int
    width: float
    max_height: float
    editable: bool
    reason: str
    display_rect: fitz.Rect
    display_origin: fitz.Point
    display_angle: float
    line_rects: list[fitz.Rect] = field(default_factory=list)
    line_offsets: list[float] = field(default_factory=list)
    alignment: str = "left"
    first_indent: float = 0.0
    layout_origin: fitz.Point | None = None
    source_kind: str = "native"
    ocr_confidence: float | None = None
    scan_words: list[fitz.Rect] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    hidden_text: bool = False


@dataclass
class PlacedRun:
    text: str
    style: SourceStyle
    point: fitz.Point
    width: float


@dataclass
class SourcePlan:
    operation: Any
    block: SourceBlock
    text: str
    runs: list[PlacedRun]
    bounds: fitz.Rect
    removed: int = 0
    unicode_maps: dict[str, dict[int, int]] = field(default_factory=dict)
    shaped_pdf: bytes | None = None


def _fonts(page: fitz.Page) -> dict[str, SourceFont]:
    candidates: dict[str, list[SourceFont]] = defaultdict(list)
    total = 0
    seen = set()
    traces = page.get_texttrace()
    for xref, extension, kind, name, _reference, *_ in page.get_fonts(full=True):
        try:
            _base, ext, _kind, buffer = page.parent.extract_font(xref)
            program = font_program(buffer, ext, re.sub(r"^[A-Z]{6}\+", "", name)) if buffer else None
            if (program is None or not program.codepoints) and buffer:
                glyphs = tuple(sorted({(int(char[0]), int(char[1])) for trace in traces
                                       if _font_name(trace.get("font", "")) == _font_name(name)
                                       for char in trace.get("chars", [])}))
                program = recover_embedded_cmap(buffer, ext, re.sub(r"^[A-Z]{6}\+", "", name), glyphs)
            if program is None or not program.codepoints:
                program = installed_font(name)
            if program.key not in seen:
                total += len(program.buffer)
                seen.add(program.key)
            if total > MAX_PAGE_FONT_BYTES:
                raise SourceTextError("SOURCE_RESOURCE_LIMIT", "This page contains too much font data to edit safely.")
            candidates[_font_name(name)].append(program)
        except SourceTextError:
            raise
        except (ValueError, KeyError, RuntimeError):
            candidates[_font_name(name)].append(installed_font(name))
    resolved = {}
    for name, values in candidates.items():
        unique = {item.key: item for item in values}
        # Ambiguous PDF resource names use a disclosed installed substitute.
        resolved[name] = next(iter(unique.values())) if len(unique) == 1 else installed_font(name)
    return resolved


def _rect_json(rect: fitz.Rect, page: fitz.Page) -> dict[str, float]:
    left = max(0.0, min(1.0, rect.x0 / page.rect.width))
    top = max(0.0, min(1.0, rect.y0 / page.rect.height))
    right = max(left, min(1.0, rect.x1 / page.rect.width))
    bottom = max(top, min(1.0, rect.y1 / page.rect.height))
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def source_blocks(page: fitz.Page, checksum: str, warnings: list[str] | None = None) -> list[SourceBlock]:
    fonts = _fonts(page)
    flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES
    raw = page.get_text("rawdict", flags=flags)
    raw_blocks = []
    protected = [fitz.Rect(link["from"]) for link in page.get_links()]
    protected += [widget.rect for widget in page.widgets() or []]
    protected += [annotation.rect for annotation in page.annots() or []]
    for raw_block in raw["blocks"]:
        if raw_block.get("type") != 0:
            continue
        if any(fitz.Rect(raw_block["bbox"]).intersects(rect) for rect in protected):
            # Protect only the line with the link/form/annotation. A linked
            # email address must not lock the user's separate name/address lines.
            raw_blocks.extend({**raw_block, "bbox":line["bbox"], "lines":[line]}
                              for line in raw_block["lines"])
        else:
            raw_blocks.append(raw_block)
    separated = []
    for raw_block in raw_blocks:
        origins = [line["spans"][0]["origin"] for line in raw_block["lines"] if line["spans"]]
        if any(abs(second[1]-first[1]) < 0.5 and abs(second[0]-first[0]) > 3
               for first,second in zip(origins,origins[1:])):
            for line in raw_block["lines"]:
                separated.append({**raw_block,"bbox":line["bbox"],"lines":[line]})
            continue
        if any(any(right["bbox"][0]-left["bbox"][2] > 2*max(left["size"],right["size"])
                   for left,right in zip(line["spans"],line["spans"][1:])) for line in raw_block["lines"]):
            for line in raw_block["lines"]:
                groups = []
                for span in line["spans"]:
                    if groups and span["bbox"][0]-groups[-1][-1]["bbox"][2] <= 2*max(span["size"],groups[-1][-1]["size"]):
                        groups[-1].append(span)
                    else:
                        groups.append([span])
                for group in groups:
                    rectangle = fitz.Rect(group[0]["bbox"])
                    for span in group[1:]: rectangle |= fitz.Rect(span["bbox"])
                    separated.append({**raw_block,"bbox":tuple(rectangle),"lines":[{**line,"bbox":tuple(rectangle),"spans":group}]})
        else:
            separated.append(raw_block)
    raw_blocks = separated
    traces = page.get_texttrace()
    if len(raw_blocks) > MAX_BLOCKS:
        raise SourceTextError("SOURCE_RESOURCE_LIMIT", "This page has too many text blocks to edit safely.")
    if sum(len(s["chars"]) for b in raw_blocks for l in b["lines"] for s in l["spans"]) > MAX_PAGE_CHARACTERS:
        raise SourceTextError("SOURCE_RESOURCE_LIMIT", "This page contains too much text to edit safely.")
    blocks: list[SourceBlock] = []
    for index, raw_block in enumerate(raw_blocks):
        text_parts: list[str] = []
        styles: list[SourceStyle] = []
        characters = []
        lines = raw_block["lines"]
        if not lines or not lines[0]["spans"]:
            continue
        direction = tuple(lines[0]["dir"])
        rect = fitz.Rect(raw_block["bbox"])
        origin = fitz.Point(lines[0]["spans"][0]["origin"])
        reason = ""
        if any(trace.get("type",0) not in {0, 1} for trace in traces
               if fitz.Rect(trace["bbox"]).intersects(rect) and trace.get("font") in {span["font"] for line in lines for span in line["spans"]}):
            reason = "SOURCE_COMPLEX_LAYOUT"
        for line_index, line in enumerate(lines):
            if line.get("wmode") or hypot(line["dir"][0] - direction[0], line["dir"][1] - direction[1]) > 0.005:
                reason = "SOURCE_COMPLEX_LAYOUT"
            if line_index and text_parts and styles:
                # Extraction line endings are soft wraps within a paragraph.
                # Lists / addresses remain separate blocks or explicit newlines
                # when their baseline spacing is not a normal paragraph step.
                text_parts.append(" ")
                styles.append(styles[-1])
            for span in line["spans"]:
                font = fonts.get(_font_name(span["font"])) or installed_font(span["font"])
                span_chars = span["chars"]
                if not font:
                    reason = "SOURCE_FONT_UNAVAILABLE"
                    continue
                scale_samples = []
                for first, second in zip(span_chars, span_chars[1:]):
                    advance = font.font.glyph_advance(ord(first["c"])) * float(span["size"])
                    actual = ((second["origin"][0] - first["origin"][0]) * direction[0]
                              + (second["origin"][1] - first["origin"][1]) * direction[1])
                    if advance > 0.2 and actual > 0:
                        scale_samples.append(actual / advance)
                scale = median(scale_samples) if scale_samples else 1.0
                # PDF writers round individual character advances. Do not
                # mistake those small rounding differences for condensed text.
                if abs(scale - 1.0) < 0.005:
                    scale = 1.0
                size = float(span["size"])
                if scale != 1.0:
                    # MuPDF reports the geometric mean of the two text axes.
                    # Confirm the vertical axis from glyph bounds before
                    # preserving a condensed/expanded text matrix.
                    box = fitz.Rect(span["bbox"])
                    extent = box.height if abs(direction[1]) < 0.01 else box.width if abs(direction[0]) < 0.01 else 0.0
                    metrics = float(span.get("ascender",0))-float(span.get("descender",0))
                    measured = extent/metrics if metrics > 0 else 0.0
                    if measured > 0 and abs(measured-size/scale) < size*0.015:
                        scale *= size/measured
                        size = measured
                    else:
                        scale = max(0.5, min(2.0, scale))
                if not 0.5 <= scale <= 2:
                    reason = "SOURCE_COMPLEX_LAYOUT"
                    scale = 1.0
                style = SourceStyle(font, size, int(span["color"]),
                                    int(span.get("alpha", 255)) / 255, scale)
                for trace in traces:
                    if (_font_name(trace.get("font","")) == _font_name(span["font"])
                        and fitz.Rect(trace["bbox"]).intersects(fitz.Rect(span["bbox"]))
                        and abs(float(trace["size"])-style.size*style.horizontal_scale) < 0.1):
                        components = tuple(float(value) for value in trace["color"])
                        if len(components) in {1,3}:
                            rgb = components*3 if len(components) == 1 else components
                            packed = sum(round(value*255) << shift for value,shift in zip(rgb,(16,8,0)))
                            if packed != style.color:
                                continue
                            style.native_color = components
                            style.opacity = float(trace.get("opacity",style.opacity))
                            break
                line_origin = line["spans"][0]["origin"]
                style.baseline_shift = ((span["origin"][0]-line_origin[0]) * -direction[1]
                                        + (span["origin"][1]-line_origin[1]) * direction[0])
                for char in span_chars:
                    text_parts.append(char["c"])
                    styles.append(style)
                    characters.append((char["c"], tuple(char["bbox"])))
            while text_parts and text_parts[-1] in {" ", "\t"}:
                text_parts.pop()
                styles.pop()
        text = "".join(text_parts)
        if not text.strip() or not styles:
            continue
        resolved_styles = []
        for char, style in zip(text, styles):
            try:
                resolved_styles.append(_character_style(style, char))
            except SourceTextError as exc:
                # One unsupported symbol must not disable unrelated paragraphs.
                reason = exc.code
                resolved_styles.append(style)
        styles = resolved_styles
        # Readable, ordinary text only: do not rewrite hidden OCR / clipping or
        # transparent content using a different rendering model.
        if any(style.opacity <= 0 for style in styles):
            reason = "SOURCE_COMPLEX_LAYOUT"
        if not fitz.Rect(0,0,page.cropbox.width,page.cropbox.height).contains(rect):
            reason = "SOURCE_COMPLEX_LAYOUT"
        if any(not isfinite(style.size) or not 0.5 <= style.size <= 512 for style in styles):
            reason = "SOURCE_COMPLEX_LAYOUT"
        for widget in page.widgets() or []:
            if widget.rect.intersects(rect):
                reason = "SOURCE_PROTECTED_OBJECT"
        if any(annotation.rect.intersects(rect) for annotation in page.annots() or []):
            reason = "SOURCE_PROTECTED_OBJECT"
        line_origins = [fitz.Point(line["spans"][0]["origin"]) for line in lines if line["spans"]]
        normal = fitz.Point(-direction[1], direction[0])
        steps = [(b.x-a.x)*normal.x + (b.y-a.y)*normal.y for a, b in zip(line_origins, line_origins[1:])]
        positive_steps = [step for step in steps if step > 0]
        line_step = median(positive_steps) if positive_steps else styles[0].size * 1.2
        display = rect * page.rotation_matrix
        display_origin = origin * page.rotation_matrix
        rotated_direction = fitz.Point(*direction) * fitz.Matrix(page.rotation)
        identifier = "b_" + sha256(f"{checksum}:{page.number}:{index}:{text}:{tuple(rect)}".encode()).hexdigest()[:32]
        blocks.append(SourceBlock(identifier, text, styles, characters, rect, origin, direction,
                                  line_step, len(lines), rect.width, rect.height,
                                  not bool(reason), reason, display, display_origin,
                                  degrees(atan2(rotated_direction.y, rotated_direction.x)),
                                  [fitz.Rect(line["bbox"]) for line in lines]))
        blocks[-1].links = [dict(link) for link in page.get_links() if fitz.Rect(link["from"]).intersects(rect)]
        block_traces = [trace for trace in traces if fitz.Rect(trace["bbox"]).intersects(rect)
                        and trace.get("font") in {span["font"] for line in lines for span in line["spans"]}]
        blocks[-1].hidden_text = bool(block_traces) and all(
            trace.get("type") == 3 or float(trace.get("opacity", 1)) <= 0 for trace in block_traces)
        blocks[-1].line_offsets = [(point.x-origin.x)*normal.x + (point.y-origin.y)*normal.y
                                   for point in line_origins]
        if abs(direction[1]) < 0.01:
            lefts = [point.x for point in line_origins]
            rights = [line["bbox"][2] for line in lines]
            centers = [(left+right)/2 for left,right in zip(lefts,rights)]
            if len(lines) > 1 and max(lefts)-min(lefts) > 2:
                if max(rights)-min(rights) < 1.5:
                    blocks[-1].alignment = "right"
                elif max(centers)-min(centers) < 1.5:
                    blocks[-1].alignment = "center"
            elif len(lines) == 1 and abs(rect.x0+rect.width/2-page.cropbox.width/2) < 2:
                blocks[-1].alignment = "center"
            elif len(lines) > 2 and max(rights[:-1])-min(rights[:-1]) < 1 and max(lefts)-min(lefts) < 1:
                blocks[-1].alignment = "justify"
    native_rects = [block.rect for block in blocks if not block.hidden_text]
    try:
        recognized = _ocr_blocks(page, checksum, recognize_lines(page, checksum, native_rects))
        # Searchable scans often already carry an invisible OCR layer. The
        # visible image is the edit target; do not let that hidden layer lock
        # the same recognized words or create duplicate editor hit targets.
        blocks = [block for block in blocks if not (block.hidden_text and any(
            block.rect.intersects(other.rect) for other in recognized))]
        blocks.extend(recognized)
    except ScanTextError as exc:
        # Native text stays usable when recognition is unavailable; the profile
        # exposes this OCR error separately instead of failing the entire page.
        if not blocks:
            raise SourceTextError("SOURCE_OCR_UNAVAILABLE", str(exc)) from exc
        if warnings is not None:
            warnings.append("SOURCE_OCR_UNAVAILABLE")
    for block in blocks:
        dx, dy = block.direction
        if abs(dy) < 0.01 and dx > 0:
            peers = [b for b in blocks if b is not block and abs(b.rect.x0-block.rect.x0) <= 3
                     and abs(b.direction[1]) < 0.01 and b.rect.width > block.styles[0].size * 8]
            block.width = max([block.rect.width, *[p.rect.x1-block.rect.x0 for p in peers]])
            right = min(page.cropbox.width, block.rect.x0 + block.width)
            block.width = right - block.rect.x0
            if block.alignment in {"center","right"}:
                wide = [other.rect.width for other in blocks if other is not block
                        and other.rect.x0 <= block.rect.x0+block.rect.width/2 <= other.rect.x1]
                block.width = max([block.width,*wide])
                left = block.rect.x0+block.rect.width/2-block.width/2 if block.alignment == "center" else block.rect.x1-block.width
                right = left+block.width
            else:
                left = block.rect.x0
                block.first_indent = max(0.0,block.origin.x-left)
            block.layout_origin = fitz.Point(left,block.origin.y)
            neighbors = [other.rect.x0 for other in blocks if other is not block
                         and other.rect.x0 >= block.rect.x1-0.1
                         and other.rect.y0 < block.rect.y1 and other.rect.y1 > block.rect.y0]
            if neighbors:
                right = min(right,min(neighbors)-0.15*block.styles[0].size)
                block.width = min(block.width,right-left)
            obstacles = [b.rect.y0 for b in blocks if b is not block and b.rect.y0 >= block.rect.y1 - 0.2
                         and b.rect.x0 < right and b.rect.x1 > left]
            for image in page.get_image_info():
                image_rect = fitz.Rect(image["bbox"])
                if image_rect.y0 >= block.rect.y1 and image_rect.x0 < right and image_rect.x1 > block.rect.x0:
                    obstacles.append(image_rect.y0)
            limit = min(obstacles, default=page.cropbox.height - 12)
            block.max_height = max(block.rect.height, limit-block.rect.y0-0.15*block.styles[0].size)
        else:
            # Arbitrary orientation keeps its original local extent / margins.
            projections = [(point.x-block.origin.x)*dx + (point.y-block.origin.y)*dy
                           for rectangle in block.line_rects
                           for point in (rectangle.tl,rectangle.tr,rectangle.bl,rectangle.br)]
            block.width = max(projections, default=0) - min(0.0,min(projections,default=0))
            block.max_height = max(block.styles[0].size, block.line_count * block.line_step)
            block.layout_origin = block.origin
        # Font-ascent rectangles can overlap even when the ink does not (table
        # rows, superscripts and diagonal watermarks). Only actual inseparable
        # glyph targets block an edit, not the union rectangle of a paragraph.
        if block.source_kind == "native" and block.editable:
            try:
                _removal_bands(block, [char for other in blocks if other is not block and other.source_kind == "native" for char in other.characters])
            except SourceTextError:
                block.editable, block.reason = False, "SOURCE_OVERLAPPING_TEXT"
    return blocks


def _character_style(style: SourceStyle, character: str) -> SourceStyle:
    try:
        program = font_for_character(style.font, character)
    except ValueError as exc:
        raise SourceTextError("SOURCE_FONT_MISSING_GLYPH", str(exc)) from exc
    return style if program.key == style.font.key else replace(style, font=program)


def _ocr_blocks(page: fitz.Page, checksum: str, lines: list[dict]) -> list[SourceBlock]:
    result = []
    for index, line in enumerate(lines):
        ink = fitz.Rect(line["rect"])
        text = line["text"]
        program = installed_font("Times New Roman")
        program = replace(program, source_name="OCR estimate", substituted=True)
        size = max(4.0, min(144.0, ink.height / 0.75))
        length = program.font.text_length(text, fontsize=size)
        horizontal_scale = max(0.35, min(3.0, ink.width / max(1, length)))
        style = SourceStyle(program, size, 0, 1.0, horizontal_scale)
        origin = fitz.Point(ink.x0, ink.y1)
        rectangle = fitz.Rect(ink.x0, origin.y - program.font.ascender * size,
                              ink.x1, origin.y - program.font.descender * size)
        rectangle &= fitz.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        identifier = "b_" + sha256(f"{checksum}:{page.number}:ocr:{index}:{text}:{line['rect']}".encode()).hexdigest()[:32]
        direction = fitz.Point(1, 0) * fitz.Matrix(page.rotation)
        words = [fitz.Rect(word["rect"]) + (-.35, -.35, .35, .35) for word in line["words"]]
        protected = any(widget.rect.intersects(rectangle) for widget in page.widgets() or [])
        protected |= any(annotation.rect.intersects(rectangle) for annotation in page.annots() or [])
        protected |= any(fitz.Rect(link["from"]).intersects(rectangle) for link in page.get_links())
        result.append(SourceBlock(
            identifier, text, [_character_style(style, character) for character in text], [],
            rectangle, origin, (1, 0), size * 1.2, 1, ink.width, rectangle.height,
            not protected, "SOURCE_PROTECTED_OBJECT" if protected else "",
            rectangle * page.rotation_matrix, origin * page.rotation_matrix,
            degrees(atan2(direction.y, direction.x)), [rectangle], [0.0],
            source_kind="ocr", ocr_confidence=line["confidence"], scan_words=words,
        ))
    return result


def _shaped_text(block, text, styles, origin, width, height, page):
    """HarfBuzz layout for RTL / combining / Indic text, before deleting ink."""
    from tempfile import TemporaryDirectory
    from html import escape
    from .edit import _UnicodeStory
    if abs(block.direction[1]) > 0.001:
        raise SourceTextError("SOURCE_COMPLEX_LAYOUT", "Complex-script text at this angle needs an independent text box.")
    with TemporaryDirectory(prefix="redocx-source-fonts-") as directory:
        faces = []
        programs = {style.font.key: style.font for style in styles}
        for program in programs.values():
            filename = f"{program.key}.{program.extension}"
            (Path(directory) / filename).write_bytes(program.buffer)
            faces.append(f'@font-face {{font-family:F{program.key};src:url("{filename}");}}')
        chunks = []
        for char, style in zip(text, styles):
            css = f"font-family:F{style.font.key};font-size:{style.size}pt;color:#{style.color:06x};"
            value = "<br>" if char == "\n" else escape(char)
            if chunks and chunks[-1][0] == css:
                chunks[-1][1] += value
            else:
                chunks.append([css, value])
        body = "".join(f'<span style="{css}">{value}</span>' for css, value in chunks)
        css = "".join(faces) + f"body,div {{margin:0;padding:0;}} div {{line-height:{block.line_step}pt;white-space:pre-wrap;text-align:{block.alignment};}}"
        archive = fitz.Archive(directory)
        story = _UnicodeStory(f"<div>{body}</div>", css, sha256(text.encode()).hexdigest()[:24], archive=archive)
        top = origin.y - styles[0].font.font.ascender * styles[0].size
        rect = fitz.Rect(origin.x, top, origin.x + width, top + height)
        with fitz.open() as rendered:
            target = rendered.new_page(width=page.cropbox.width, height=page.cropbox.height)
            spare, _scale = target.insert_htmlbox(rect, story, scale_low=1, archive=archive)
            if spare < 0:
                raise SourceTextError("SOURCE_TEXT_OVERFLOW", "The shaped text does not fit. Enlarge the text box or shorten the text.")
            boxes = [fitz.Rect(span["bbox"]) for part in target.get_text("dict")["blocks"] if part.get("type") == 0 for line in part["lines"] for span in line["spans"]]
            bounds = fitz.Rect(rect)
            if boxes:
                bounds = fitz.Rect(boxes[0])
                for box in boxes[1:]:
                    bounds |= box
            return rendered.tobytes(garbage=4, deflate=True), bounds


def _styled_text(block: SourceBlock, text: str) -> list[SourceStyle]:
    if len({style.key for style in block.styles}) == 1:
        return [_character_style(block.styles[0], char) for char in text]
    if len(block.text)+len(text) > 20_000:
        raise SourceTextError("SOURCE_COMPLEX_LAYOUT", "This mixed-style paragraph is too large to rewrite faithfully.")
    result: list[SourceStyle] = []
    matcher = SequenceMatcher(a=block.text, b=text, autojunk=True)
    for tag, start, end, new_start, new_end in matcher.get_opcodes():
        if tag == "equal":
            result.extend(block.styles[start:end])
        elif tag in {"insert", "replace"}:
            inherited = block.styles[max(0, min(start - 1, len(block.styles)-1))]
            result.extend([inherited] * (new_end-new_start))
    return [_character_style(style, char) for char, style in zip(text, result)]


def _layout(block: SourceBlock, text: str, styles: list[SourceStyle], origin: fitz.Point,
            width: float, max_height: float) -> tuple[list[PlacedRun], fitz.Rect]:
    if _complex_text(text):
        raise SourceTextError("SOURCE_COMPLEX_LAYOUT", "This text requires shaping that cannot preserve the source font safely.")
    advances = []
    for char, style in zip(text, styles):
        if not style.font.supports(char):
            raise SourceTextError("SOURCE_FONT_MISSING_GLYPH", "No installed font can represent one of the submitted characters.")
        advances.append(0.0 if char in "\n\r" else style.font.font.glyph_advance(ord(char)) * style.size * style.horizontal_scale)
    # Greedy wrapping at spaces, with a glyph boundary for a long word. No
    # shrink-to-fit, margins or font-size changes are permitted on this path.
    lines: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        end, last_space, used = start, -1, 0.0
        available = width - (block.first_indent if not lines else 0)
        while end < len(text) and text[end] != "\n":
            if used + advances[end] > available + 0.05:
                break
            used += advances[end]
            if text[end].isspace():
                last_space = end
            end += 1
        if end == start and text[end] != "\n":
            raise SourceTextError("SOURCE_TEXT_OVERFLOW", "The text cannot fit at the source font size and margins.")
        next_start = end
        if end < len(text) and text[end] == "\n":
            next_start += 1
        elif end < len(text) and last_space >= start:
            end, next_start = last_space, last_space + 1
        lines.append((start, end))
        start = next_start
    if not lines:
        return [], fitz.Rect(block.rect)
    def offset(line_number: int) -> float:
        if line_number < len(block.line_offsets):
            return block.line_offsets[line_number]
        return (block.line_offsets[-1] if block.line_offsets else 0) + (line_number-max(0,len(block.line_offsets)-1))*block.line_step
    if offset(len(lines)-1) + max(s.size for s in styles) > max_height + 0.2:
        raise SourceTextError("SOURCE_TEXT_OVERFLOW", "The paragraph would overlap the next content at its original font size. Shorten it; the document margins were preserved.")
    dx, dy = block.direction
    nx, ny = -dy, dx
    runs: list[PlacedRun] = []
    bounds = fitz.Rect()
    for line_number, (first, last) in enumerate(lines):
        line_width = sum(advances[first:last])
        cursor = (width-line_width)/2 if block.alignment == "center" else width-line_width if block.alignment == "right" else block.first_indent if line_number == 0 else 0.0
        extra = 0.0
        spaces = sum(text[position] == " " for position in range(first,last))
        if block.alignment == "justify" and line_number < len(lines)-1 and spaces:
            extra = max(0,(width-cursor-line_width)/spaces)
        run_start = first
        while run_start < last:
            style = styles[run_start]
            run_end = run_start + 1
            while run_end < last and styles[run_end].key == style.key and not (extra and (text[run_end-1] == " " or text[run_end] == " ")):
                run_end += 1
            point = fitz.Point(origin.x + dx*cursor + nx*(offset(line_number)+style.baseline_shift),
                               origin.y + dy*cursor + ny*(offset(line_number)+style.baseline_shift))
            length = sum(advances[run_start:run_end])
            run = PlacedRun(text[run_start:run_end], style, point, length)
            runs.append(run)
            ascent = style.font.font.ascender * style.size
            descent = -style.font.font.descender * style.size
            corners = [fitz.Point(point.x + dx*u + nx*v, point.y + dy*u + ny*v)
                       for u,v in [(0,-ascent),(length,-ascent),(0,descent),(length,descent)]]
            run_rect = fitz.Rect(min(p.x for p in corners), min(p.y for p in corners),
                                 max(p.x for p in corners), max(p.y for p in corners))
            bounds |= run_rect
            cursor += length
            cursor += extra * sum(text[position] == " " for position in range(run_start,run_end))
            run_start = run_end
    return runs, bounds


def _safe_text(value: str) -> str:
    if len(value) > 100_000 or len(value.encode("utf-8", errors="strict")) > 400_000:
        raise SourceTextError("SOURCE_RESOURCE_LIMIT", "The paragraph is too large to edit safely.")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if any((ord(ch) < 32 and ch != "\n") or 0x7F <= ord(ch) <= 0x9F
           or unicodedata.category(ch) == "Cs" or (unicodedata.category(ch) == "Cf" and ord(ch) not in {0x200C, 0x200D}) for ch in text):
        raise SourceTextError("SOURCE_UNSAFE_TEXT", "The edited text contains an unsupported control character.")
    return text


def prepare_source_operations(pdf: fitz.Document, operations: list[Any], checksum: str) -> dict[int, SourcePlan]:
    pages: dict[int, dict[str, SourceBlock]] = {}
    plans: dict[int, SourcePlan] = {}
    edited: set[tuple[int, str]] = set()
    for index, operation in enumerate(operations):
        kind = _value(operation)
        if kind not in SOURCE_OPERATIONS:
            continue
        if str(getattr(operation, "source_checksum_sha256", "")) != checksum:
            raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "The source document changed. Open it again before editing.")
        number = int(operation.page_number) - 1
        if not 0 <= number < len(pdf):
            raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "The selected source page no longer exists.")
        if number not in pages:
            pages[number] = {b.id: b for b in source_blocks(pdf[number], checksum)}
        block = pages[number].get(str(getattr(operation, "source_block_id", "")))
        if block is None or sha256(block.text.encode()).hexdigest() != str(getattr(operation, "original_text_sha256", "")):
            raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "The selected source paragraph changed. Open it again.")
        if not block.editable:
            raise SourceTextError(block.reason, "This paragraph cannot be rewritten while preserving its source objects and font.")
        text = _safe_text(str(getattr(operation, "text", "")))
        if kind == "edit_source_text":
            key = (number, block.id)
            if key in edited:
                raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "A source paragraph may only be edited once per request.")
            edited.add(key)
            if text == block.text:
                raise SourceTextError("SOURCE_TEXT_UNCHANGED", "This paragraph has no changes.")
            styles = _styled_text(block, text)
            origin, width, height = block.layout_origin or block.origin, block.width, block.max_height
        else:
            if not text.strip():
                raise SourceTextError("SOURCE_TEXT_UNCHANGED", "Enter text on the document before inserting it.")
            page = pdf[number]
            rectangle = operation.rectangle
            displayed = fitz.Rect(rectangle.x*page.rect.width, rectangle.y*page.rect.height,
                                  (rectangle.x+rectangle.width)*page.rect.width,
                                  (rectangle.y+rectangle.height)*page.rect.height)
            unrotated = displayed * page.derotation_matrix
            style = block.styles[0]
            origin = fitz.Point(unrotated.x0, unrotated.y0 + style.size*style.font.font.ascender)
            width, height = unrotated.width, unrotated.height
            styles = [style] * len(text)
        shaped_pdf = None
        if _complex_text(text):
            shaped_pdf, bounds = _shaped_text(block, text, styles, origin, width, height, pdf[number])
            runs = [PlacedRun(text, styles[0], origin, width)] if text else []
        else:
            runs, bounds = _layout(block, text, styles, origin, width, height)
        page_bounds = fitz.Rect(0,0,pdf[number].cropbox.width,pdf[number].cropbox.height)
        if runs and not page_bounds.contains(bounds):
            raise SourceTextError("SOURCE_TEXT_OVERFLOW", "The edited text would leave the source page margins.")
        other_chars = [char for other in pages[number].values() if other is not block or kind == "insert_source_text"
                       for char in other.characters]
        if runs and any(bounds.intersects(fitz.Rect(char[1])) for char in other_chars):
            raise SourceTextError("SOURCE_TEXT_OVERFLOW", "The edited text would overlap existing document content.")
        for widget in pdf[number].widgets() or []:
            if bounds.intersects(widget.rect):
                raise SourceTextError("SOURCE_PROTECTED_OBJECT", "The edited text would overlap a form field.")
        plans[index] = SourcePlan(operation, block, text, runs, bounds, shaped_pdf=shaped_pdf)
    # Preflight all edits together before mutating any source content.
    for index, plan in plans.items():
        for other_index, other in plans.items():
            if other_index > index and plan.operation.page_number == other.operation.page_number and plan.runs and other.runs and plan.bounds.intersects(other.bounds):
                raise SourceTextError("SOURCE_TEXT_OVERFLOW", "Two edited paragraphs would overlap. Shorten one before previewing.")
        for operation in operations:
            if _value(operation) in SOURCE_OPERATIONS or operation.page_number != plan.operation.page_number:
                continue
            if _value(operation) in {"replace_text","remove_text","remove_image","whiteout","remove_signature"}:
                rectangle = getattr(operation, "source_rectangle", operation.rectangle)
                page = pdf[int(operation.page_number)-1]
                target = fitz.Rect(rectangle.x*page.rect.width,rectangle.y*page.rect.height,
                                   (rectangle.x+rectangle.width)*page.rect.width,(rectangle.y+rectangle.height)*page.rect.height)*page.derotation_matrix
                if target.intersects(plan.block.rect) or target.intersects(plan.bounds):
                    raise SourceTextError("SOURCE_PROTECTED_OBJECT", "An erasing operation overlaps a directly edited paragraph.")
    font_maps: dict[str, dict[int,int]] = defaultdict(dict)
    for plan in plans.values():
        for run in ([] if plan.shaped_pdf else plan.runs):
            mapping = font_maps[run.style.font.key]
            for char in run.text:
                glyph = run.style.font.font.has_glyph(ord(char))
                if glyph in mapping and mapping[glyph] != ord(char):
                    raise SourceTextError("SOURCE_COMPLEX_LAYOUT", "This source font cannot preserve the distinction between two of the submitted characters.")
                mapping[glyph] = ord(char)
    for plan in plans.values():
        plan.unicode_maps = font_maps
    return plans


def _characters(page: fitz.Page) -> Counter:
    return Counter((char["c"], tuple(round(value, 3) for value in char["bbox"]))
                   for b in page.get_text("rawdict", flags=fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]
                   if b.get("type") == 0 for line in b["lines"] for span in line["spans"] for char in span["chars"])


def _removal_bands(block: SourceBlock, others: list | None = None) -> list[fitz.Rect]:
    """Find an owned point in each glyph; never bridge unrelated table cells."""
    other_boxes = [box for _char, rect in (others or []) if (box := fitz.Rect(rect)).intersects(block.rect)]
    patches = []
    for char, bounds in block.characters:
        box = fitz.Rect(bounds)
        found = None
        for y in (0.5, 0.35, 0.65, 0.2, 0.8):
            for x in (0.5, 0.25, 0.75, 0.1, 0.9):
                point = fitz.Point(box.x0 + box.width*x, box.y0 + box.height*y)
                patch = fitz.Rect(point.x-.01, point.y-.01, point.x+.01, point.y+.01)
                if not any(other.intersects(patch) for other in other_boxes):
                    found = patch
                    break
            if found is not None:
                break
        if found is None:
            raise SourceTextError("SOURCE_OVERLAPPING_TEXT", "Overlapping glyph objects cannot be separated safely.")
        patches.append(found)
    # Coalesce owned points on a common row / column only when the combined
    # strip still cannot intersect an unselected glyph. Thousands of individual
    # redact annotations otherwise make ordinary multi-paragraph pages unusable.
    merged = []
    dx, dy = block.direction
    for patch in patches:
        found = False
        for index, prior in enumerate(merged):
            same_row = abs(patch.y0 - prior.y0) < .005 and abs(dy) < .01
            same_column = abs(patch.x0 - prior.x0) < .005 and abs(dx) < .01
            if not (same_row or same_column):
                continue
            combined = prior | patch
            if not any(box.intersects(combined) for box in other_boxes):
                merged[index] = combined
                found = True
                break
        if not found:
            merged.append(patch)
    return merged


def remove_source_text(page: fitz.Page, plan: SourcePlan, guard: Any) -> int:
    if plan.block.source_kind == "ocr":
        for rect in plan.block.scan_words:
            guard(page, rect, include_annotations=True)
        try:
            erase_scan_words(page, plan.block.scan_words)
        except ScanTextError as exc:
            raise SourceTextError("SOURCE_OCR_UNAVAILABLE", str(exc)) from exc
        plan.removed = len(plan.block.text)
        return max(1, plan.removed)
    # Link annotations belong to the selected text. Preserve and recreate them
    # after text redaction instead of locking the entire linked paragraph.
    for link in plan.block.links:
        current = next((item for item in page.get_links() if item.get("xref") == link.get("xref")), None)
        if current is not None:
            page.delete_link(current)
    before = _characters(page)
    selected = Counter((char,tuple(round(v,3) for v in rect)) for char,rect in plan.block.characters)
    if selected - before:
        raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "The source paragraph changed before it could be edited.")
    others = before - selected
    bands = _removal_bands(plan.block, list(others.elements()))
    for patch in bands:
        if any(fitz.Rect(other_rect).intersects(patch) for _,other_rect in others):
            raise SourceTextError("SOURCE_OVERLAPPING_TEXT", "Overlapping glyphs cannot be separated safely.")
        guard(page, patch, include_annotations=True)
    from .edit import _strip_unicode_clusters
    _strip_unicode_clusters(page, bands)
    # Preflight every patch before creating this operation's annotations.
    # Otherwise the guard would correctly reject our own pending redactions.
    for patch in bands:
        page.add_redact_annot(patch, fill=None, cross_out=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                          text=fitz.PDF_REDACT_TEXT_REMOVE)
    if _characters(page) != others:
        raise RuntimeError("Source text verification failed: surrounding characters changed.")
    plan.removed = sum(selected.values())
    return max(1, plan.removed)


def insert_source_text(page: fitz.Page, plan: SourcePlan) -> dict[str, Any]:
    if plan.shaped_pdf:
        with fitz.open(stream=plan.shaped_pdf, filetype="pdf") as rendered:
            page.show_pdf_page(page.rect, rendered, 0, keep_proportion=False, overlay=True)
    registered: set[str] = set()
    for run in ([] if plan.shaped_pdf else plan.runs):
        # A reopened PDF may already contain our previous font resources. Never
        # overwrite their ToUnicode maps while editing a different paragraph.
        font_name = "RdxSource" + sha256((run.style.font.key + str(plan.operation.source_checksum_sha256)).encode()).hexdigest()[:24]
        xref = page.insert_font(fontname=font_name, fontbuffer=run.style.font.buffer)
        if font_name not in registered:
            mapping = plan.unicode_maps[run.style.font.key]
            pairs = [f"<{glyph:04X}> <{chr(codepoint).encode('utf-16-be').hex().upper()}>"
                     for glyph,codepoint in sorted(mapping.items())]
            cmap = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
                    "/CIDSystemInfo <</Registry (Adobe) /Ordering (UCS) /Supplement 0>> def",
                    "/CMapName /ReDOCXSourceUnicode def", "/CMapType 2 def",
                    "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
            for start in range(0,len(pairs),100):
                chunk = pairs[start:start+100]
                cmap.extend([f"{len(chunk)} beginbfchar",*chunk,"endbfchar"])
            cmap.extend(["endcmap","CMapName currentdict /CMap defineresource pop","end","end"])
            kind,reference = page.parent.xref_get_key(xref,"ToUnicode")
            if kind != "xref":
                raise SourceTextError("SOURCE_FONT_UNAVAILABLE", "The source font could not preserve searchable text.")
            page.parent.update_stream(int(reference.split()[0]),"\n".join(cmap).encode("ascii"))
            registered.add(font_name)
        color = run.style.native_color or tuple(((run.style.color >> shift) & 255)/255 for shift in (16,8,0))
        dx,dy = plan.block.direction
        scale = run.style.horizontal_scale
        matrix = fitz.Matrix(dx*scale, -dy*scale, dy, dx, 0, 0)
        page.insert_text(run.point, run.text, fontname=font_name, fontsize=run.style.size,
                         color=color, fill_opacity=run.style.opacity,
                         morph=(run.point,matrix), overlay=True)
    if plan.text.strip():
        for original_link in plan.block.links:
            link = {key: value for key, value in original_link.items() if key not in {"xref", "id"}}
            link["from"] = plan.bounds
            if str(link.get("uri", "")).startswith("mailto:") and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", plan.text.strip()):
                link["uri"] = "mailto:" + plan.text.strip()
            page.insert_link(link)
    return {"operation_id": getattr(plan.operation,"operation_id",None), "operation": _value(plan.operation),
            "page_number": int(plan.operation.page_number), "status": "applied",
            "source_kind":plan.block.source_kind,
            "font_substituted":any(style.font.substituted for style in _styled_text(plan.block, plan.text)),
            "targets_changed": max(1,plan.removed,len(plan.runs)),
            "verification": "OCR text replaced in selected image regions; output review required" if plan.block.source_kind == "ocr" else "styled source text replaced; font substitutions disclosed in source profile; layout preflight passed"}


def inspect_source_page(source_path: str | Path, page_number: int) -> dict[str, Any]:
    checksum = document_checksum(source_path)
    with fitz.open(source_path) as pdf:
        if pdf.is_encrypted or pdf.needs_pass or pdf.xref_get_key(-1,"Encrypt")[0] != "null":
            raise SourceTextError("SOURCE_PROTECTED_OBJECT", "Use an unlocked source document for editing.")
        if not 1 <= page_number <= len(pdf):
            raise SourceTextError("SOURCE_DOCUMENT_CHANGED", "The requested source page does not exist.")
        if pdf.xref_get_key(pdf.pdf_catalog(), "Perms")[0] != "null" or any(
            pdf.xref_get_key(xref,"ByteRange")[0] == "array" and pdf.xref_get_key(xref,"ByteRange")[1] not in {"[]","[0 0 0 0]"}
            for xref in range(1,pdf.xref_length())):
            raise SourceTextError("SOURCE_PROTECTED_OBJECT", "Editing a certificate-signed PDF would invalidate its signature. Use an unsigned original.")
        page = pdf[page_number-1]
        warnings: list[str] = []
        blocks = source_blocks(page,checksum,warnings)
        fonts = {style.font.key: style.font for block in blocks for style in block.styles}
        response_blocks = []
        for block in blocks:
            style = block.styles[0]
            spans = []
            for char, item_style in zip(block.text, block.styles):
                if spans and spans[-1]["_key"] == item_style.key:
                    spans[-1]["text"] += char
                else:
                    spans.append({"_key": item_style.key, "text": char, "font_key": item_style.font.key,
                                  "font_size": item_style.size, "color": f"#{item_style.color:06x}",
                                  "baseline_shift":item_style.baseline_shift,"horizontal_scale":item_style.horizontal_scale})
            for span in spans:
                span.pop("_key")
            response_blocks.append({"id":block.id,"text":block.text,"text_sha256":sha256(block.text.encode()).hexdigest(),
                "rectangle":_rect_json(block.display_rect,page), "line_rectangles":[_rect_json(r*page.rotation_matrix,page) for r in block.line_rects],
                "origin":{"x":((block.layout_origin or block.origin)*page.rotation_matrix).x/page.rect.width,
                          "y":((block.layout_origin or block.origin)*page.rotation_matrix).y/page.rect.height},
                "font_key":style.font.key,"font_name":style.font.name,"font_size":style.size,
                "color":f"#{style.color:06x}","line_height":block.line_step,"layout_width":block.width,
                "max_height":block.max_height,"baseline_offset":(block.origin.y-block.rect.y0),
                "angle":block.display_angle,"horizontal_scale":style.horizontal_scale,
                "alignment":block.alignment,"first_indent":block.first_indent,
                "editable":block.editable,"reason":block.reason,"spans":spans,
                "source_kind":block.source_kind,"ocr_confidence":block.ocr_confidence,
                "font_substituted":any(item.font.substituted for item in block.styles)})
        # Render an authentic text-free backing layer. Active editing never
        # hides existing text using an assumed white page background.
        with fitz.open() as background:
            background.insert_pdf(pdf,from_page=page_number-1,to_page=page_number-1)
            backing = background[0]
            rotation = backing.rotation
            backing.set_rotation(0)
            native_chars = [char for block in blocks if block.source_kind == "native" for char in block.characters]
            for block in blocks:
                if block.source_kind == "ocr" or not block.editable:
                    continue
                others = list((Counter(native_chars) - Counter(block.characters)).elements())
                for patch in _removal_bands(block, others):
                    backing.add_redact_annot(patch,fill=None,cross_out=False)
            backing.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,graphics=fitz.PDF_REDACT_LINE_ART_NONE,text=fitz.PDF_REDACT_TEXT_REMOVE)
            try:
                erase_scan_words(backing, [rect for block in blocks if block.editable and block.source_kind == "ocr" for rect in block.scan_words])
            except ScanTextError as exc:
                raise SourceTextError("SOURCE_OCR_UNAVAILABLE", str(exc)) from exc
            backing.set_rotation(rotation)
            scale = min(2.0, (MAX_BACKGROUND_PIXELS / max(1,page.rect.get_area())) ** 0.5)
            if scale < 0.1:
                raise SourceTextError("SOURCE_RESOURCE_LIMIT", "The page dimensions are too large to edit safely.")
            pixmap = backing.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
            data = pixmap.tobytes("png")
            if len(data) > 8*1024*1024:
                raise SourceTextError("SOURCE_RESOURCE_LIMIT", "The source background is too large to preview safely.")
        return {"source_checksum_sha256":checksum,"page_number":page_number,"page_count":len(pdf),
                "width":page.rect.width,"height":page.rect.height,"rotation":page.rotation,
                "font_families":available_families(),
                "warnings":warnings,
                "has_ocr":any(block.source_kind == "ocr" for block in blocks),
                "has_font_substitutions":any(style.font.substituted for block in blocks for style in block.styles),
                "blocks":response_blocks,"background":"data:image/png;base64,"+base64.b64encode(data).decode(),
                "fonts":[{"key":font.key,"name":font.name,"extension":font.extension,
                          "ascender":font.font.ascender,"descender":font.font.descender,
                          "codepoints":sorted(font.codepoints),"source_name":font.source_name,"substituted":font.substituted,"data":base64.b64encode(font.buffer).decode() if font.browser_embeddable else ""}
                         for font in fonts.values()]}
