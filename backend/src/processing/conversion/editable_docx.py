"""Page-local text, hyperlink and paragraph reconstruction for editable PDF imports."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from itertools import accumulate, groupby
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlsplit

import re
import unicodedata

import fitz
from docx import Document
from docx.document import Document as WordDocument
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from docx.text.paragraph import Paragraph as WordParagraph


class LayoutMismatch(ValueError):
    """An editable paragraph or link could not be faithfully placed."""


_BULLETS = {"•", "●", "▪", "◦", "\uf0b7", "\uf0a7"}
_TRANSLATE = str.maketrans({"\uf0b7": "•", "\uf0a7": "▪", "\u00ad": ""})
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
Box = tuple[float, float, float, float]
TextRange = tuple[int, int]
_P, _R, _RPR, _T, _LINK = map(qn, ("w:p", "w:r", "w:rPr", "w:t", "w:hyperlink"))


def _compact(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text.translate(_TRANSLATE)).split())


@dataclass(frozen=True, slots=True)
class Style:
    font: str
    size: float
    flags: int
    color: int
    rise: int


@dataclass(slots=True)
class Glyph:
    text: str
    box: Box
    style: Style
    space: bool
    underline: bool = False


@dataclass(slots=True)
class Row:
    glyphs: list[Glyph]
    baseline: float
    block: int
    start: int = 0
    end: int = 0
    split: int = 0
    bullet: bool = False
    rule: tuple[float, Sequence[float]] | None = None

    @property
    def left(self) -> float:
        return self.glyphs[0].box[0]

    @property
    def right(self) -> float:
        return self.glyphs[-1].box[2]

    @property
    def text(self) -> str:
        return "".join((" " if g.space and i else "") + g.text for i, g in enumerate(self.glyphs))

    @property
    def size(self) -> float:
        return self.glyphs[-1].style.size


@dataclass(slots=True)
class PageText:
    rows: list[Row]
    text: str
    glyphs: list[Glyph]
    row_numbers: list[int]
    horizontal: bool

    @classmethod
    def read(cls, page: fitz.Page) -> PageText:
        # Geometry returned by get_text is unrotated. Source and rendered links
        # are transformed into this coordinate system before hit-testing.
        lines = []
        horizontal = not page.rotation
        data = page.get_text("rawdict", flags=fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_PRESERVE_LIGATURES)
        for block in data["blocks"]:
            for line in block.get("lines", ()):
                direction = line["dir"]
                if abs(direction[0] - 1) > 0.001 or abs(direction[1]) > 0.001 or line["wmode"]:
                    horizontal = False
                glyphs = []
                pending_space = False
                origins = []
                spans = line["spans"]
                ordinary_baselines = [s["origin"][1] for s in spans if not s["flags"] & 1]
                base = median(ordinary_baselines) if ordinary_baselines else None
                for span in spans:
                    rise = round(2 * (base - span["origin"][1])) if base is not None and span["flags"] & 1 else 0
                    style = Style(span["font"], span["size"], span["flags"], span["color"], rise)
                    painted = span.get("char_flags", 16) & (8 | 16)
                    if not painted or span.get("alpha", 255) != 255 or span.get("bidi", 0) % 2:
                        horizontal = False
                    for char in span["chars"]:
                        value = char["c"]
                        if value.isspace():
                            pending_space = True
                        elif _compact(value):
                            box = char["bbox"]
                            gap = box[0] - glyphs[-1].box[2] if glyphs else 0
                            glyphs.append(Glyph(value, box, style, pending_space or gap > 0.22 * style.size))
                            origins.append(char["origin"][1])
                            pending_space = False
                if glyphs:
                    lines.append(Row(glyphs, median(origins), block["number"]))
        bands = []
        for line in sorted(lines, key=lambda item: (item.baseline, item.left)):
            if bands and abs(bands[-1][0].baseline - line.baseline) <= 1.5:
                bands[-1].append(line)
            else:
                bands.append([line])
        rows = []
        for band in bands:
            # Sort line objects, never individual characters. PDF ligatures can
            # have a zero-width continuation positioned beyond the next glyph.
            parts = sorted(band, key=lambda item: item.left)
            row = parts[0]
            for part in parts[1:]:
                part.glyphs[0].space = True
                row.glyphs.extend(part.glyphs)
            rows.append(row)
        letters = []
        text = []
        row_numbers = []
        for i, row in enumerate(rows):
            row.start = len(letters)
            for glyph in row.glyphs:
                key = _compact(glyph.text)
                text.append(key)
                letters.extend([glyph] * len(key))
                row_numbers.extend([i] * len(key))
            row.end = len(letters)
        return cls(rows, "".join(text), letters, row_numbers, horizontal)


def _locate(source: str, start: int, end: int, output: str) -> int | None:
    """Resolve a particular occurrence, including repeated labels in plain text."""
    needle = source[start:end]
    if not needle:
        return None
    if source == output:
        return start
    first = output.find(needle)
    if first < 0:
        return None
    if output.find(needle, first + 1) < 0:
        if source.find(needle) == source.rfind(needle):
            return first
        # A dropped duplicate can leave just the wrong label in Word. Reverse
        # the match so that the surviving label must identify this source occurrence.
        return first if _locate(output, first, first + len(needle), source) == start else None
    # Prefer an occurrence whose surrounding words also survived reconstruction.
    # Returning an ambiguous first match can silently link the wrong recipient.
    candidates = []
    at = first
    while at >= 0:
        before = 0
        while before < min(48, at, start) and source[start - before - 1] == output[at - before - 1]:
            before += 1
        after = 0
        stop = at + len(needle)
        while after < min(48, len(source) - end, len(output) - stop) and source[end + after] == output[stop + after]:
            after += 1
        candidates.append((before + after, at))
        at = output.find(needle, at + 1)
    best = max(score for score, _ in candidates)
    matches = [at for score, at in candidates if score == best]
    if len(matches) == 1:
        return matches[0]
    positions = [m.start() for m in re.finditer(f"(?={re.escape(needle)})", source)]
    if len(positions) == len(candidates) and start in positions:
        ranked = candidates[positions.index(start)][1]
        if ranked in matches:
            return ranked
    return None


def uri_key(target: str) -> str:
    # Only fold equivalent encodings. In particular %2F and a path separator
    # can resolve to different resources. Writer also normalizes a bare web root.
    try:
        parts = urlsplit(target)
        if parts.scheme in ("http", "https") and parts.netloc and not parts.path:
            target = parts._replace(path="/").geturl()
    except ValueError:
        pass

    def decode(match):
        value = chr(int(match.group(1), 16))
        if value.isascii() and (value.isalnum() or value in "-._~| "):
            return value
        return match.group(0).upper()

    return re.sub(r"%([0-9a-fA-F]{2})", decode, target)


def _hits(box: Box, rect: fitz.Rect) -> bool:
    x = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2
    return rect.x0 - 0.35 <= x <= rect.x1 + 0.35 and rect.y0 - 0.8 <= y <= rect.y1 + 0.8


@dataclass(slots=True)
class Anchor:
    target: str
    rect: fitz.Rect
    ranges: list[TextRange]


def _anchors(page: fitz.Page, model: PageText) -> list[Anchor]:
    links = [link for link in page.get_links() if link.get("uri")]
    if not links:
        return []
    anchors = []
    baselines = [row.baseline for row in model.rows]
    reach = max((max(abs(row.baseline - g.box[1]), abs(row.baseline - g.box[3]))
                 for row in model.rows for g in row.glyphs), default=0) + 1
    for link in links:
        rect = fitz.Rect(link["from"]) * page.derotation_matrix
        nearby = model.rows[bisect_left(baselines, rect.y0 - reach):bisect_right(baselines, rect.y1 + reach)]
        positions = [i for row in nearby for i in range(row.start, row.end)
                     if _hits(model.glyphs[i].box, rect)]
        ranges = []
        for _, indexes in groupby(enumerate(positions), lambda pair: pair[1] - pair[0]):
            seq = [item[1] for item in indexes]
            ranges.append((seq[0], seq[-1] + 1))
        anchors.append(Anchor(link["uri"], rect, ranges))
    return anchors


def _run_shell(run: Any) -> Any:
    shell = OxmlElement("w:r")
    shell.attrib.update(run.attrib)
    if run.rPr is not None:
        shell.append(deepcopy(run.rPr))
    return shell


def promote_links(paragraph: Any) -> None:
    """Split a mixed wrapper in XML order, retaining properties and non-text nodes."""
    for wrapper in paragraph.findall(_R):
        if wrapper.find(_LINK) is None:
            continue
        segment = None
        for child in list(wrapper):
            if child.tag == _RPR:
                continue
            if child.tag == _LINK:
                if wrapper.rPr is not None:
                    for inner in child.findall(_R):
                        props = inner.get_or_add_rPr()
                        for prop in wrapper.rPr:
                            if props.find(prop.tag) is None:
                                props.append(deepcopy(prop))
                wrapper.addprevious(child)
                segment = None
            else:
                if segment is None:
                    segment = _run_shell(wrapper)
                    wrapper.addprevious(segment)
                segment.append(child)
        paragraph.remove(wrapper)


def _paragraphs(blocks: Iterable[Any]) -> list[Any]:
    return [p for block in blocks for p in block.iter(_P)]


def _visible_run(run: Any) -> bool:
    hidden = run.find(f"{_RPR}/{qn('w:vanish')}")
    return hidden is None or hidden.get(qn("w:val")) in {"0", "false", "off"}


def _write_link_ranges(paragraph: Any, nodes: dict, document: WordDocument) -> None:
    """Apply node-local intervals without flattening tabs, breaks, fields or drawings."""
    active = None
    container = None
    for run in list(paragraph):
        if run.tag != _R or not any(node in nodes for node in run):
            active = None
            continue
        output = []

        def append(key, child):
            if not output or output[-1][0] != key:
                output.append((key, _run_shell(run)))
            output[-1][1].append(child)

        for child in list(run):
            if child.tag == _RPR:
                continue
            if child not in nodes:
                append(None, child)
                continue
            if child.tag != _T:
                append(nodes[child][0][2], child)
                continue
            text = child.text or ""
            cursor = 0
            for start, end, key in sorted(nodes[child]):
                if cursor < start:
                    t = deepcopy(child)
                    t.text = text[cursor:start]
                    t.set(_SPACE, "preserve")
                    append(None, t)
                t = deepcopy(child)
                t.text = text[start:end]
                t.set(_SPACE, "preserve")
                append(key, t)
                cursor = end
            if cursor < len(text):
                t = deepcopy(child)
                t.text = text[cursor:]
                t.set(_SPACE, "preserve")
                append(None, t)
        for key, part in output:
            if key is None:
                run.addprevious(part)
            else:
                if active != key:
                    container = OxmlElement("w:hyperlink")
                    container.set(qn("r:id"), document.part.relate_to(key[1], RT.HYPERLINK, is_external=True))
                    run.addprevious(container)
                container.append(part)
            active = key
        paragraph.remove(run)


def repair_links(document: WordDocument, page: fitz.Page, model: PageText, blocks: Sequence[Any]) -> None:
    paragraphs = _paragraphs(blocks)
    for paragraph in paragraphs:
        promote_links(paragraph)
    anchors = _anchors(page, model)
    if not any(a.ranges for a in anchors):
        return
    for paragraph in paragraphs:
        for link in paragraph.findall(_LINK):
            rel = document.part.rels.get(link.get(qn("r:id")))
            if (rel is not None and rel.is_external and rel.reltype == RT.HYPERLINK
                    and link.find(f".//{_T}") is not None and link.find(f".//{qn('w:drawing')}") is None):
                # Recover the URL from the PDF annotation, even if the importer
                # put the wrong target on an otherwise correct label.
                for child in list(link):
                    link.addprevious(child)
                paragraph.remove(link)
    chars = []
    addresses = []
    surface = []
    controls = {qn("w:br"), qn("w:cr"), qn("w:tab")}
    for paragraph in paragraphs:
        for run in paragraph.findall(_R):
            if not _visible_run(run):
                continue
            for node in run:
                if node.tag == _T:
                    for i, char in enumerate(node.text or ""):
                        value = _compact(char)
                        chars.append(value)
                        addresses.extend([len(surface)] * len(value))
                        surface.append((node, i))
                elif node.tag in controls:
                    surface.append((node, 0))
    word_text = "".join(chars)
    intervals = defaultdict(list)
    for serial, anchor in enumerate(anchors):
        for start, end in anchor.ranges:
            at = _locate(model.text, start, end, word_text)
            if at is None:
                raise LayoutMismatch(f"Could not place an external hyperlink at its visible label on page {page.number + 1}.")
            selection = surface[addresses[at]:addresses[at + end - start - 1] + 1]
            for node, group in groupby(selection, key=lambda item: item[0]):
                offsets = [item[1] for item in group]
                intervals[node].append((offsets[0], offsets[-1] + 1, (serial, anchor.target)))
    for node, ranges in intervals.items():
        unique = []
        for start, end, key in sorted(ranges):
            if unique and start < unique[-1][1]:
                old_start, old_end, old_key = unique[-1]
                if old_key[1] != key[1]:
                    raise LayoutMismatch(f"Overlapping external hyperlinks on page {page.number + 1} have distinct destinations.")
                unique[-1] = (old_start, max(old_end, end), old_key)
            else:
                unique.append((start, end, key))
        intervals[node] = unique
    for paragraph in paragraphs:
        _write_link_ranges(paragraph, intervals, document)


def _check_link_neighbors(page_number: int, before: PageText, start: int, end: int, after: PageText, at: int) -> None:
    # Compare one neighboring word on each side within the original line. This
    # catches a lost separator or word even when the page meets its token quota.
    first = before.rows[before.row_numbers[start]]
    last = before.rows[before.row_numbers[end - 1]]
    left = start
    if left > first.start:
        left -= 1
        while left > first.start and start - left < 32 and not before.glyphs[left].space:
            left -= 1
    right = end
    if right < last.end:
        right += 1
        while right < last.end and right - end < 32 and not before.glyphs[right].space:
            right += 1
    stop = at + end - start
    for i in range(max(1, start), min(end + 1, len(before.text))):
        j = at + i - start
        if (0 < j < len(after.text) and before.row_numbers[i] == before.row_numbers[i - 1]
                and after.row_numbers[j] == after.row_numbers[j - 1]
                and before.glyphs[i].space != after.glyphs[j].space):
            raise LayoutMismatch(f"Rendered page {page_number} changed a word boundary at a hyperlink.")
    if (before.text[left:start] != after.text[max(0, at - (start - left)):at]
            or before.text[end:right] != after.text[stop:stop + right - end]):
        raise LayoutMismatch(f"Rendered page {page_number} changed text next to a hyperlink.")
    # Standalone labels have a visible line break on each available side.
    if start == first.start and end == last.end:
        for neighbor, anchor in ((at - 1, at), (stop, stop - 1)):
            if 0 <= neighbor < len(after.text) and after.row_numbers[neighbor] == after.row_numbers[anchor]:
                raise LayoutMismatch(f"Rendered page {page_number} lost a break next to a hyperlink.")


def _check_links(source: fitz.Page, before: PageText, rendered: fitz.Page, after: PageText) -> None:
    expected = _anchors(source, before)
    if not expected:
        return
    actual = defaultdict(list)
    click_targets = defaultdict(set)
    for link in _anchors(rendered, after):
        key = uri_key(link.target)
        actual[key].append(link.rect)
        for start, end in link.ranges:
            for index in range(start, end):
                click_targets[index].add(key)
    source_coverage = defaultdict(set)
    for anchor in expected:
        for start, end in anchor.ranges:
            source_coverage[uri_key(anchor.target)].update(range(start, end))
    for anchor in expected:
        key = uri_key(anchor.target)
        boxes = actual[key]
        if not anchor.ranges:
            # For a graphic-only link the visible object is the coordinate anchor.
            if any((box & anchor.rect).get_area() >= 0.5 * anchor.rect.get_area() > 0 for box in boxes):
                continue
            raise LayoutMismatch(f"Rendered page {source.number + 1} lost a graphic hyperlink.")
        for start, end in anchor.ranges:
            at = _locate(before.text, start, end, after.text)
            if at is None or not all(any(_hits(g.box, box) for box in boxes)
                                     for g in after.glyphs[at:at + end - start]):
                raise LayoutMismatch(f"Rendered page {source.number + 1} lost an external hyperlink at its visible label.")
            if any(click_targets[i] - {key} for i in range(at, at + end - start)):
                raise LayoutMismatch(f"Rendered page {source.number + 1} has conflicting destinations at a hyperlink.")
            _check_link_neighbors(source.number + 1, before, start, end, after, at)
            # A row-wide hyperlink also covers its label. Check the immediately
            # adjacent, unlinked glyphs so a correct URL cannot swallow its neighbors.
            for offset, pos in ((start - 1, at - 1), (end, at + end - start)):
                if (not 0 <= offset < len(before.text) or not 0 <= pos < len(after.text)
                        or offset in source_coverage[key] or before.text[offset] != after.text[pos]):
                    continue
                glyph = after.glyphs[pos]
                center = fitz.Point((glyph.box[0] + glyph.box[2]) / 2, (glyph.box[1] + glyph.box[3]) / 2)
                if any(center in (box + (0.4, 0.4, -0.4, -0.4)) for box in boxes):
                    raise LayoutMismatch(f"Rendered page {source.number + 1} linked text outside its visible label.")


@dataclass(slots=True)
class Paragraph:
    rows: list[Row]
    justified: bool

    @property
    def pitch(self) -> float:
        rows = self.rows
        if len(rows) == 1:
            return rows[0].size * 1.16
        return median(b.baseline - a.baseline for a, b in zip(rows, rows[1:]))


def _word_width(row: Row) -> float:
    first = row.glyphs[0]
    word_end = next((i for i, g in enumerate(row.glyphs[1:], 1) if g.space), len(row.glyphs))
    return row.glyphs[word_end - 1].box[2] - first.box[0]


def _columns(row: Row) -> bool:
    """Separate a hanging marker or a single right-hand date/location field."""
    gs = row.glyphs
    row.bullet = gs[0].text in _BULLETS and len(gs) > 1 and gs[1].space
    gaps = [i for i in range(1, len(gs)) if gs[i].box[0] - gs[i - 1].box[2] > max(18, 2.1 * row.size)]
    if row.bullet:
        row.split = 1
        return not any(i != 1 for i in gaps)
    if not gaps:
        return True
    if len(gaps) != 1:
        return False
    split = gaps[0]
    tail = "".join((" " if g.space else "") + g.text for g in gs[split:]).strip()
    if len(tail) > 49 or not re.search(r"\b(?:\d{4}|present|current)\b|^[^,\d]{1,22},\s*[^,\d]{2,22}$", tail, re.I):
        return False
    row.split = split
    return True


def _decorations(page: fitz.Page, rows: list[Row]) -> bool:
    """Represent only heading rules and text underlines; leave other art to the layout engine."""
    if page.get_image_info() or page.first_annot or page.first_widget:
        return False
    left = min(r.left for r in rows)
    right = max(r.right for r in rows)
    for path in page.get_drawings():
        rect = path["rect"]
        items = path["items"]
        if (len(items) != 1 or items[0][0] not in ("l", "re") or rect.height > 1.6
                or rect.width <= 0 or (path.get("dashes") or "").replace(" ", "") not in ("[]0", "")):
            return False
        row = min(rows, key=lambda r: abs(rect.y0 - r.baseline))
        if not -0.6 <= rect.y0 - row.baseline <= 0.55 * row.size:
            return False
        color = path.get("fill") or path.get("color")
        if color is None or len(color) != 3:
            return False
        if rect.x0 <= left + 3 and rect.x1 >= right - 5:
            row.rule = (max(0.4, rect.height, path.get("width") or 0), color)
        else:
            # Short rules are typographic underlines only when they cover real glyphs.
            selected = [g for g in row.glyphs if rect.x0 - 0.5 <= (g.box[0] + g.box[2]) / 2 <= rect.x1 + 0.5]
            if (not selected or abs(selected[0].box[0] - rect.x0) > 4
                    or abs(selected[-1].box[2] - rect.x1) > 4):
                return False
            for glyph in selected:
                glyph.underline = True
    return True


def _continues(group: list[Row], row: Row, right: float) -> bool:
    first, prev = group[0], group[-1]
    if row.split or row.bullet or prev.rule or (prev.split and not prev.bullet):
        return False
    left = first.glyphs[first.split].box[0] if first.bullet else group[-1].left
    spacing = row.baseline - prev.baseline
    first_indent = len(group) == 1 and not first.bullet and 4 < left - row.left < 35
    if ((abs(row.left - left) > 2.6 and not first_indent) or abs(prev.size - row.size) > 0.85 or
            not 0.83 * row.size <= spacing <= 1.55 * row.size):
        return False
    if len(group) > 1 and abs(spacing - (prev.baseline - group[-2].baseline)) > max(1.7, 0.14 * row.size):
        return False
    if prev.text.isupper() or row.text.isupper():
        return False
    if row.glyphs[0].style.flags & 16 and not prev.glyphs[-1].style.flags & 16:
        return False
    # A short final line or a line that could have held the next word ends the paragraph.
    # Some exporters emit a separate text block for every physical line.
    spare = right - prev.right
    if first.bullet and spare < 0.25 * (right - left):
        return True
    return spare <= max(_word_width(row) + 0.9 * row.size, 0.085 * (right - left))


def _groups(rows: list[Row], right: float) -> list[Paragraph]:
    groups = []
    for row in rows:
        if groups and _continues(groups[-1], row, right):
            groups[-1].append(row)
        else:
            groups.append([row])
    result = []
    for group in groups:
        ends = [r.right for r in group[:-1]]
        justified = bool(ends and all(abs(x - right) <= 4 for x in ends)
                         and group[-1].right < right - group[0].size)
        result.append(Paragraph(group, justified))
    return result


def flow_groups(page: fitz.Page, model: PageText) -> list[Paragraph] | None:
    rows = model.rows
    if not rows or not model.horizontal or not _decorations(page, rows):
        return None
    if not all(_columns(row) for row in rows):
        return None
    right = max(r.right for r in rows)
    # Do not reinterpret two independent columns, marginal notes, or overlapping text.
    left = min(r.left for r in rows)
    for row in rows:
        if row.left > left + 0.38 * (right - left) and len(row.text) > 35:
            return None
    return _groups(rows, right)


def _complex_prose(model: PageText) -> list[Paragraph]:
    # A wide, isolated block with a shorter final line is high-confidence prose
    # even when another part of the page is a table or contains artwork.
    groups = []
    if not model.horizontal:
        return groups
    for _, members in groupby(model.rows, key=lambda r: r.block):
        rows = list(members)
        if len(rows) < 2 or any(not _columns(row) or row.split for row in rows):
            continue
        right = max(row.right for row in rows)
        for spec in _groups(rows, right):
            if (len(spec.rows) >= 2 and min(r.right - r.left for r in spec.rows[:-1]) > 220
                    and spec.rows[-1].right < right - 12):
                groups.append(spec)
    return groups


def _paragraph_index(blocks: Sequence[Any]) -> tuple[list[Any], list[int], str]:
    paragraphs = _paragraphs(blocks)
    texts = [_compact("".join(t.text or "" for t in p.iter(_T))) for p in paragraphs]
    return paragraphs, list(accumulate(map(len, texts))), "".join(texts)


def require_flowing_paragraphs(model: PageText, groups: Sequence[Paragraph], blocks: Sequence[Any]) -> None:
    """A visually aligned row of independent paragraphs is not editable prose."""
    _, ends, text = _paragraph_index(blocks)
    for group in groups:
        if len(group.rows) < 2:
            continue
        start, end = group.rows[0].start, group.rows[-1].end
        at = _locate(model.text, start, end, text)
        if at is None or bisect_right(ends, at) != bisect_right(ends, at + end - start - 1):
            raise LayoutMismatch("The editable Word output fragmented a source paragraph.")


def _unwrap_prose_lines(paragraph: Any, group: Paragraph) -> None:
    # Only ordinary text-wrapping breaks internal to this geometrically matched
    # paragraph are eligible. Fields, links and explicit page/column breaks keep
    # their original run structure.
    if any(c.tag not in (qn("w:pPr"), _R) for c in paragraph):
        return
    nodes = [c for run in paragraph.findall(_R) for c in run if c.tag != _RPR]
    if any(node.tag not in (_T, qn("w:br"), qn("w:cr")) for node in nodes):
        return
    breaks = {row.end - group.rows[0].start for row in group.rows[:-1]}
    offset = 0
    for i, node in enumerate(nodes):
        if node.tag == _T:
            offset += len(_compact(node.text or ""))
        elif (node.tag in (qn("w:br"), qn("w:cr")) and offset in breaks and 0 < i < len(nodes) - 1
              and node.get(qn("w:type"), "textWrapping") == "textWrapping" and not node.get(qn("w:clear"))
              and nodes[i - 1].tag == _T and nodes[i + 1].tag == _T):
            left, right = nodes[i - 1].text or "", nodes[i + 1].text or ""
            if left[-1:].isspace() or right[:1].isspace() or left.endswith(("-", "‐")):
                node.getparent().remove(node)
            else:
                space = OxmlElement("w:t")
                space.text = " "
                space.set(_SPACE, "preserve")
                node.getparent().replace(node, space)


def repair_split_prose(
    model: PageText, blocks: Sequence[Any], groups: Sequence[Paragraph] | None = None
) -> list[TextRange]:
    """Join only an exact, adjacent set of Word paragraphs in the same container."""
    paragraphs, ends, text = _paragraph_index(blocks)
    repaired = []
    for group in groups if groups is not None else _complex_prose(model):
        if len(group.rows) < 2:
            continue
        start, end = group.rows[0].start, group.rows[-1].end
        at = _locate(model.text, start, end, text)
        if at is None:
            continue
        first_ix = bisect_right(ends, at)
        last_ix = bisect_right(ends, at + end - start - 1)
        if (first_ix and ends[first_ix - 1] != at) or ends[last_ix] != at + end - start:
            continue
        if first_ix == last_ix:
            _unwrap_prose_lines(paragraphs[first_ix], group)
            # Text in a table cell wraps to its cell's width. The layout engine
            # owns that constraint; independent body paragraphs use the PDF rows.
            if paragraphs[first_ix].getparent().tag == qn("w:body"):
                repaired.append((start, end))
            continue
        chunk = paragraphs[first_ix:last_ix + 1]
        if any(p.getnext() is not q for p, q in zip(chunk, chunk[1:])):
            continue
        if any(p.find(f".//{qn('w:sectPr')}") is not None or
               any(c.tag not in (qn("w:pPr"), _R, _LINK) for c in p) or
               any(c.tag not in (_T, _RPR) for r in p.iter(_R) for c in r) for p in chunk):
            continue
        first = WordParagraph(chunk[0], None)
        first.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if group.justified else WD_ALIGN_PARAGRAPH.LEFT
        first.paragraph_format.line_spacing = Pt(group.pitch)
        for donor in chunk[1:]:
            tail = "".join(t.text or "" for t in first._p.iter(_T))
            head = "".join(t.text or "" for t in donor.iter(_T))
            if not tail.endswith((" ", "-", "‐")) and not head.startswith(" "):
                first.add_run(" ")
            for child in list(donor):
                if child.tag != qn("w:pPr"):
                    first._p.append(child)
            donor.getparent().remove(donor)
        repaired.append((start, end))
    return repaired


def _emit_glyphs(paragraph: WordParagraph, glyphs: list[Glyph], families: dict[str, str]) -> None:
    for (style, underline), same in groupby(glyphs, key=lambda g: (g.style, g.underline)):
        text = "".join((" " if g.space else "") + g.text.translate(_TRANSLATE) for g in same)
        run = paragraph.add_run(text)
        run.font.name = families.get(style.font, style.font)
        if any(b in text for b in ("•", "▪")) and "symbol" in run.font.name.lower():
            run.font.name = "Arial"
        run.font.size = Pt(style.size)
        if style.rise:
            position = OxmlElement("w:position")
            position.set(qn("w:val"), str(style.rise))
            run._r.get_or_add_rPr().insert_element_before(position, "w:sz", "w:szCs")
        run.bold = bool(style.flags & 16)
        run.italic = bool(style.flags & 2)
        run.underline = underline
        run.font.color.rgb = RGBColor.from_string(f"{style.color & 0xffffff:06X}")


def _emit_paragraph(document, group, section, right, previous_baseline, families):
    rows = group.rows
    row = rows[0]
    p = document.add_paragraph()
    fmt = p.paragraph_format
    margin = section.left_margin.pt
    pitch = group.pitch
    fmt.line_spacing = Pt(pitch)
    fmt.space_before = Pt(max(0, row.baseline - previous_baseline - pitch))
    fmt.space_after = Pt(0)
    fmt.widow_control = False
    fmt.keep_with_next = False
    fmt.keep_together = False
    fmt.left_indent = Pt(row.left - margin)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY if group.justified else WD_ALIGN_PARAGRAPH.LEFT
    if row.bullet:
        body_left = row.glyphs[row.split].box[0]
        fmt.left_indent = Pt(body_left - margin)
        fmt.first_line_indent = Pt(row.left - body_left)
        fmt.tab_stops.add_tab_stop(Pt(body_left - margin))
    elif row.split:
        fmt.tab_stops.add_tab_stop(Pt(row.right - margin), WD_TAB_ALIGNMENT.RIGHT)
    elif len(rows) > 1 and row.left - rows[1].left > 3:
        fmt.left_indent = Pt(rows[1].left - margin)
        fmt.first_line_indent = Pt(row.left - rows[1].left)
    elif (len(rows) == 1 and row.left > margin + 14
          and abs((row.left + row.right) - (right + margin)) < 9):
        fmt.left_indent = Pt(0)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if len(rows) >= 2:
        # A non-final line fits; appending the first word of its successor did
        # not. Intersect those bounds to recover a paragraph-specific right edge.
        lower = max(line.right for line in rows)
        upper = min(line.right + _word_width(next_line) + 0.24 * line.size
                    for line, next_line in zip(rows, rows[1:]))
        if lower < upper < right + 1.5:
            edge = (lower + upper) / 2
            fmt.right_indent = Pt(max(0, section.page_width.pt - section.right_margin.pt - edge))
    for i, line in enumerate(rows):
        if i and not rows[i - 1].text.rstrip().endswith(("-", "‐")):
            p.add_run(" ")
        parts = [line.glyphs]
        if line.split:
            parts = [line.glyphs[:line.split], line.glyphs[line.split:]]
        for part_index, glyphs in enumerate(parts):
            if part_index:
                p.add_run("\t")
            glyphs[0].space = False
            _emit_glyphs(p, glyphs, families)
    if row.rule:
        thickness, color = row.rule
        borders = OxmlElement("w:pBdr")
        border = OxmlElement("w:bottom")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), str(max(2, round(thickness * 8))))
        border.set(qn("w:color"), "".join(f"{round(c * 255):02X}" for c in color))
        border.set(qn("w:space"), "0")
        borders.append(border)
        p._p.get_or_add_pPr().insert_element_before(borders, "w:spacing", "w:ind", "w:jc")
    return p._p


def needs_list_repair(groups: Sequence[Paragraph], blocks: Sequence[Any]) -> bool:
    expected = sum(group.rows[0].bullet for group in groups)
    if expected == 0:
        return False
    # Some PDFs put several list items in one text block. A marker at the end
    # of the previous Word line is not a hanging indent on the new item.
    paragraphs = ["".join(t.text or "" for t in p.iter(_T)).lstrip() for p in _paragraphs(blocks)]
    return sum(bool(text) and text[0] in _BULLETS for text in paragraphs) < expected


def append_flow_page(
    document: WordDocument, page: fitz.Page, model: PageText, groups: list[Paragraph],
    family: dict[str, str], *, replace: Sequence[Any] = (),
) -> None:
    if replace:
        section = document.sections[-1]
        for block in replace:
            if block.tag in (_P, qn("w:tbl")) and block.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None:
                document.element.body.remove(block)
    elif len(document.element.body) > 1:
        section = document.add_section(WD_SECTION.NEW_PAGE)
        separator = document.paragraphs[-1]._p
        previous = separator.getprevious()
        if previous.tag == _P and previous.find(f"{qn('w:pPr')}/{qn('w:sectPr')}") is None:
            previous.get_or_add_pPr().append(separator.pPr.sectPr)
            separator.getparent().remove(separator)
    else:
        section = document.sections[0]
    left = min(r.left for r in model.rows)
    right = max(r.right for r in model.rows)
    first = groups[0]
    start = max(0, first.rows[0].baseline - first.pitch + 0.20 * first.rows[0].size)
    section.page_width = Pt(page.rect.width)
    section.page_height = Pt(page.rect.height)
    section.left_margin = Pt(left)
    section.right_margin = Pt(max(0, page.rect.width - right - 1))
    section.top_margin = Pt(start)
    section.bottom_margin = Pt(max(0, min(18, page.rect.height - model.rows[-1].baseline - 5)))
    section.header_distance = section.footer_distance = Pt(0)
    blocks = []
    previous = first.rows[0].baseline - first.pitch
    for group in groups:
        blocks.append(_emit_paragraph(document, group, section, right, previous, family))
        previous = group.rows[-1].baseline
    repair_links(document, page, model, blocks)
    require_flowing_paragraphs(model, groups, blocks)


def write_flow(
    source: Path, destination: Path, font_families: Callable[[fitz.Page, dict[int, str]], dict[str, str]]
) -> bool:
    """Try actual flowing paragraphs on horizontally written, single-column pages."""
    document = Document()
    fonts = {}
    with fitz.open(source) as pdf:
        for page in pdf:
            model = PageText.read(page)
            groups = flow_groups(page, model)
            if not groups:
                return False
            append_flow_page(document, page, model, groups, font_families(page, fonts))
    document.save(destination)
    return True


def _check_group(
    page_number: int, group: Paragraph, before: PageText, after: PageText, *, mixed: bool = False
) -> None:
    rows = group.rows
    start, end = rows[0].start, rows[-1].end
    at = _locate(before.text, start, end, after.text)
    if at is None:
        raise LayoutMismatch(f"Rendered page {page_number} lost the text of an editable paragraph.")
    indexes = range(at, at + end - start)
    actual = [(number, list(items)) for number, items in groupby(indexes, key=after.row_numbers.__getitem__)]
    if len(actual) != len(rows):
        raise LayoutMismatch(f"Rendered page {page_number} changed paragraph wrapping ({len(rows)} to {len(actual)} lines).")
    for i, (original, (number, positions)) in enumerate(zip(rows, actual)):
        if i < len(rows) - 1:
            expected_break = original.end
            actual_break = start + positions[-1] - at + 1
            lo, hi = sorted((expected_break, actual_break))
            # Allow one word to wrap on the other side of a line break when font
            # metrics differ. Several short words are tolerated within one em.
            moved_words = sum(g.space for g in before.glyphs[lo:hi])
            if moved_words > 1 and hi - lo > original.size:
                raise LayoutMismatch(f"Rendered page {page_number} changed a paragraph's line endings.")
        line = after.rows[number]
        left = after.glyphs[positions[0]].box[0]
        right = after.glyphs[positions[-1]].box[2]
        if abs(left - original.left) > max(3.5, 0.4 * original.size):
            raise LayoutMismatch(f"Rendered page {page_number} changed a paragraph's left edge.")
        fixed_right_edge = (group.justified and i < len(rows) - 1) or (original.split and not original.bullet)
        if fixed_right_edge and abs(right - original.right) > max(5, 0.55 * original.size):
            raise LayoutMismatch(f"Rendered page {page_number} changed a paragraph's right edge.")
        # A preceding fixed object can shift a whole paragraph by one line.
        # Keep the line count and wrapping checks identical on mixed pages.
        vertical = max(group.pitch + 1, original.size) if mixed else max(9, original.size)
        relative = max(6, 0.55 * original.size) if mixed else max(4, 0.45 * original.size)
        source_offset = original.baseline - rows[0].baseline
        rendered_offset = line.baseline - after.rows[actual[0][0]].baseline
        if abs(line.baseline - original.baseline) > vertical or abs(rendered_offset - source_offset) > relative:
            raise LayoutMismatch(f"Rendered page {page_number} shifted an editable paragraph.")
        if original.split:
            offset = len(_compact("".join(g.text for g in original.glyphs[:original.split])))
            x = after.glyphs[at + original.start - start + offset].box[0]
            if abs(x - original.glyphs[original.split].box[0]) > max(6, 0.65 * original.size):
                raise LayoutMismatch(f"Rendered page {page_number} displaced a bullet indent or tabular field.")


def validate_page(source: fitz.Page, rendered: fitz.Page, *, repaired: Sequence[TextRange] = ()) -> None:
    before = PageText.read(source)
    after = PageText.read(rendered)
    _check_links(source, before, rendered, after)
    groups = flow_groups(source, before)
    mixed = groups is None
    if groups is None:
        if not repaired:
            return
        signatures = set(repaired)
        groups = [
            group for group in _complex_prose(before)
            if (group.rows[0].start, group.rows[-1].end) in signatures
        ]
    for group in groups:
        _check_group(source.number + 1, group, before, after, mixed=mixed)

