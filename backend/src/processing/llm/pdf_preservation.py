"""Measured PDF text replacement for summarize, grammar_correct and translate.

PDFs are fixed pages, not word-processing documents. Measure at the original
point sizes, preserve independent regions, and move dependent text/rules together
only when expansion needs space. Never force-fit, truncate, or rasterize output.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from html import escape
import hashlib
import io
from pathlib import Path
import re
import shutil
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
import unicodedata

import fitz

from .document_layout import DocumentLayout, LayoutUnit, distribute_text
from .pdf_layout import MAX_CONTINUATION_PAGES, PdfLayoutError, _font_key, _recover_unicode_cmap


@dataclass
class _Render:
    document: fitz.Document
    bounds: fitz.Rect
    lines: list[fitz.Rect]


@dataclass
class _Piece:
    unit: LayoutUnit
    segment: int
    target: fitz.Rect
    clip: fitz.Rect | None
    rendered: _Render | None
    value: str
    first: bool = True


def _words(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


class _TemplateFonts:
    """Use embedded fonts first; use a full matching face before Unicode fallback."""
    def __init__(self, document: fitz.Document, directory: Path, language_tag: str | None):
        self.directory, self.language_tag = directory, language_tag
        self.faces: list[str] = []
        self.originals: dict[str, tuple[str, fitz.Font]] = {}
        self.loaded: dict[str, tuple[str, fitz.Font]] = {}
        self.selected: dict[tuple[str, int, str], str] = {}
        self.full_faces: dict[str, list[Path]] = {}
        self.unicode_maps: dict[str, dict[int, str]] = {}
        glyphs: dict[str, dict[int, int]] = {}
        for page in document:
            for trace in page.get_texttrace():
                mapping = glyphs.setdefault(_font_key(trace["font"]), {})
                for code, glyph, *_ in trace["chars"]:
                    if 0 < code <= 0x10ffff and glyph > 0:
                        mapping[code] = glyph
        for page in document:
            for xref, extension, _, name, *_ in page.get_fonts(full=True):
                key = _font_key(name)
                if key in self.originals:
                    continue
                try:
                    buffer = document.extract_font(xref)[3] if xref else b""
                    if buffer:
                        buffer = _recover_unicode_cmap(buffer, glyphs.get(key, {}))
                    font = fitz.Font(fontbuffer=buffer) if buffer else fitz.Font(fontname=name)
                    self.originals[key] = self._register(buffer or font.buffer, font)
                except (RuntimeError, ValueError):
                    # Type 3/bitmap fonts cannot always be reused as outline fonts.
                    continue

    def _register(self, buffer: bytes, font: fitz.Font) -> tuple[str, fitz.Font]:
        family = f"RDTemplate{len(self.faces)}"
        filename = family + ".otf"
        (self.directory / filename).write_bytes(buffer)
        self.faces.append(f"@font-face{{font-family:{family};src:url('{filename}');}}")
        return family, font

    def _matching_paths(self, name: str) -> list[Path]:
        key = _font_key(name)
        if key not in self.full_faces:
            paths: list[Path] = []
            if shutil.which("fc-match"):
                try:
                    query = name.split("+", 1)[-1].replace("-", ":style=")
                    result = subprocess.run(["fc-match", "-f", "%{file}", query], capture_output=True,
                                            text=True, timeout=3, check=True)
                    candidate = Path(result.stdout.strip())
                    if candidate.is_file():
                        font = fitz.Font(fontfile=str(candidate))
                        # Fontconfig may return an unrelated default. Do not call
                        # that the source face, or let it outrank the embedded font.
                        if _font_key(font.name) == key:
                            paths.append(candidate)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    pass
            self.full_faces[key] = paths
        return self.full_faces[key]

    def family(self, span: dict, text: str) -> str:
        from .writer import _ordered_font_candidates
        name = str(span.get("font", ""))
        flags = int(span.get("flags", 0))
        required = {ord(c) for c in text if not c.isspace() and not unicodedata.category(c).startswith("C")}
        cache_key = (name, flags, "".join(map(chr, sorted(required))))
        if cache_key in self.selected:
            return self.selected[cache_key]
        original = self.originals.get(_font_key(name))
        if original and all(original[1].has_glyph(code) for code in required):
            self.selected[cache_key] = original[0]
            return original[0]
        matching = self._matching_paths(name)
        candidates = matching + _ordered_font_candidates(text, language_tag=self.language_tag)
        styled: list[Path] = []
        # Match weight/slant when a source subset lacks new translated glyphs.
        for path in candidates:
            suffix = "-BoldOblique" if flags & 16 and flags & 2 else "-Bold" if flags & 16 else "-Oblique" if flags & 2 else ""
            if suffix:
                alternative = path.with_name(path.stem.removesuffix("-Regular") + suffix + path.suffix)
                if alternative.is_file():
                    styled.append(alternative)
            styled.append(path)
        for path in dict.fromkeys(styled):
            key = str(path)
            try:
                if key not in self.loaded:
                    buffer = path.read_bytes()
                    self.loaded[key] = self._register(buffer, fitz.Font(fontbuffer=buffer))
                family, font = self.loaded[key]
                if all(font.has_glyph(code) for code in required):
                    if original and path not in matching:
                        family = original[0] + "," + family
                    self.selected[cache_key] = family
                    return family
            except (OSError, RuntimeError, ValueError):
                continue
        # Keep valid source glyphs and use the existing verified font cascade for
        # mixed-script runs. Missing glyphs are an error, never empty squares.
        available = ([original] if original else []) + list(self.loaded.values())
        if not all(any(font.has_glyph(code) for _, font in available) for code in required):
            raise PdfLayoutError("The available PDF fonts do not cover the processed document's characters.")
        family = ",".join(dict.fromkeys(face for face, _ in available))
        self.selected[cache_key] = family
        return family

    def draw_marker(self, page: fitz.Page, marker: dict, shift: float) -> None:
        text = marker.get("text", "•").strip()
        selected = self.originals.get(_font_key(marker.get("font", "")))
        if selected is None or not all(selected[1].has_glyph(ord(c)) for c in text):
            text = "•"
            family = self.family(dict(marker, font=""), text).split(",")[-1]
            selected = next(pair for pair in self.loaded.values() if pair[0] == family)
        family, font = selected
        page.insert_font(fontname=family, fontbuffer=font.buffer)
        color = int(marker.get("color", 0))
        page.insert_text(fitz.Point(marker["origin"]) + (0, shift), text, fontname=family,
                         fontsize=float(marker.get("size", 11)),
                         color=tuple(((color >> bits) & 255) / 255 for bits in (16, 8, 0)))


def _leading(unit: LayoutUnit) -> float:
    size = max(float(s.get("size", 11)) for s in unit.spans)
    baselines = [float(line["spans"][0]["origin"][1]) for line in unit.pdf_lines]
    steps = [b - a for a, b in zip(baselines, baselines[1:]) if b - a > size * .7]
    return median(steps) if steps else size * 1.22


def _rotation(unit: LayoutUnit) -> int:
    direction = unit.pdf_lines[0].get("dir", (1., 0.)) if unit.pdf_lines else (1., 0.)
    for vector, angle in [((1, 0), 0), ((0, -1), 90), ((-1, 0), 180), ((0, 1), 270)]:
        if abs(direction[0] - vector[0]) < .001 and abs(direction[1] - vector[1]) < .001:
            return angle
    raise PdfLayoutError("This PDF contains angled text that cannot be safely replaced without changing its layout.")


def _unencoded_glyph_text(buffer: bytes) -> dict[int, str]:
    """Recover unambiguous Unicode for unencoded OpenType substitutions.

    Some outline fonts contain an unencoded `ti` ligature. MuPDF can draw it
    correctly but omit it from the generated PDF's ToUnicode CMap. The resulting
    extraction exposes a glyph ID as an unrelated Unicode character. Recover
    the text from the font's own GSUB rules without changing outlines, shaping,
    point size, or a font's existing encoded characters.
    """
    from fontTools.ttLib import TTFont, TTLibError
    try:
        font = TTFont(io.BytesIO(buffer), lazy=True)
    except (TTLibError, ValueError):
        return {}
    try:
        if "GSUB" not in font:
            return {}
        lookup_list = font["GSUB"].table.LookupList
        if lookup_list is None:
            return {}
        encoded: dict[str, set[str]] = {}
        for code, glyph in (font.getBestCmap() or {}).items():
            if 0 < code <= 0x10ffff and not 0xd800 <= code <= 0xdfff:
                encoded.setdefault(glyph, set()).add(unicodedata.normalize("NFKC", chr(code)))
        rules = []
        for lookup in lookup_list.Lookup:
            for subtable in lookup.SubTable:
                subtable = getattr(subtable, "ExtSubTable", subtable)
                rules.extend((target, (origin,)) for origin, target in getattr(subtable, "mapping", {}).items()
                             if isinstance(target, str))
                for origin, ligatures in getattr(subtable, "ligatures", {}).items():
                    rules.extend((item.LigGlyph, (origin, *item.Component)) for item in ligatures)
        known = {glyph: set(values) for glyph, values in encoded.items()}
        for _ in range(8):
            changed = False
            for target, components in rules:
                if target in encoded or not all(len(known.get(glyph, ())) == 1 for glyph in components):
                    continue
                value = "".join(next(iter(known[glyph])) for glyph in components)
                if not value or len(value) > 32:
                    continue
                values = known.setdefault(target, set())
                if value not in values:
                    values.add(value)
                    changed = True
            if not changed:
                break
        return {font.getGlyphID(glyph): next(iter(values)) for glyph, values in known.items()
                if glyph not in encoded and len(values) == 1}
    except (TTLibError, KeyError, AttributeError, IndexError, TypeError, ValueError):
        # Optional font metadata may be absent or unusable. The independent
        # rendered-text check still rejects any unresolved text corruption.
        return {}
    finally:
        font.close()


def _cmap_codes(data: bytes) -> set[int]:
    codes: set[int] = set()
    for block in re.findall(rb"\bbeginbfchar\b(.*?)\bendbfchar\b", data, re.S):
        codes.update(int(code, 16) for code, _ in re.findall(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block))
    for block in re.findall(rb"\bbeginbfrange\b(.*?)\bendbfrange\b", data, re.S):
        for first, last in re.findall(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(?:<[0-9a-fA-F]+>|\[[^\]]*\])", block):
            start, end = int(first, 16), int(last, 16)
            if 0 <= start <= end <= 0xffff:
                codes.update(range(start, end + 1))
    return codes


def _repair_story_unicode(document: fitz.Document, fonts: _TemplateFonts) -> None:
    """Fill missing mappings only; preserve all existing PDF Unicode mappings."""
    handled: set[int] = set()
    for page in document:
        for xref, _, kind, _, _, encoding, *_ in page.get_fonts(full=True):
            if xref in handled or kind != "Type0" or encoding not in {"Identity-H", "Identity-V"}:
                continue
            handled.add(xref)
            key_type, reference = document.xref_get_key(xref, "ToUnicode")
            if key_type != "xref":
                continue
            cmap_xref = int(reference.split()[0])
            data = document.xref_stream(cmap_xref)
            if not data or b"endcmap" not in data:
                continue
            buffer = document.extract_font(xref)[3]
            fingerprint = hashlib.sha256(buffer).hexdigest()
            if fingerprint not in fonts.unicode_maps:
                fonts.unicode_maps[fingerprint] = _unencoded_glyph_text(buffer)
            glyphs = fonts.unicode_maps[fingerprint]
            if not glyphs:
                continue
            _, descendants = document.xref_get_key(xref, "DescendantFonts")
            match = re.search(r"(\d+)\s+0\s+R", descendants)
            if not match:
                continue
            map_type, mapping = document.xref_get_key(int(match[1]), "CIDToGIDMap")
            if map_type == "xref":
                raw = document.xref_stream(int(mapping.split()[0]))
                if not raw:
                    continue
                cid_pairs = [(cid, int.from_bytes(raw[2 * cid:2 * cid + 2], "big")) for cid in range(min(len(raw) // 2, 65536))]
            elif map_type == "null" or mapping == "/Identity":
                cid_pairs = [(glyph, glyph) for glyph in glyphs if 0 <= glyph <= 0xffff]
            else:
                continue
            existing = _cmap_codes(data)
            additions = [(cid, glyphs[glyph]) for cid, glyph in cid_pairs if cid not in existing and glyph in glyphs]
            if not additions:
                continue
            blocks = []
            for start in range(0, len(additions), 100):
                chunk = additions[start:start + 100]
                lines = [f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>" for cid, text in chunk]
                blocks.append(f"{len(chunk)} beginbfchar\n" + "\n".join(lines) + "\nendbfchar\n")
            document.update_stream(cmap_xref, data.replace(b"endcmap", "".join(blocks).encode("ascii") + b"endcmap", 1))


def _render(unit: LayoutUnit, value: str, width: float, fonts: _TemplateFonts,
            align: str, source: fitz.Page, underlines: list[fitz.Rect]) -> _Render:
    leading = _leading(unit)
    styled = []
    for original_span in unit.spans:
        fragments = [(original_span.get("text", ""), False)]
        box = fitz.Rect(original_span["bbox"])
        baseline = original_span["origin"][1]
        for rule in underlines:
            if not (baseline - .2 <= rule.y0 <= baseline + original_span.get("size", 11) * .35
                    and rule.x1 > box.x0 and rule.x0 < box.x1):
                continue
            label = source.get_textbox(fitz.Rect(rule.x0 - .2, box.y0, rule.x1 + .2, box.y1)).strip()
            if not label:
                continue
            expanded = []
            for text, underline in fragments:
                start = text.find(label)
                if start < 0:
                    expanded.append((text, underline))
                else:
                    expanded.extend([(text[:start], underline), (label, True), (text[start + len(label):], underline)])
            fragments = expanded
        styled.extend(dict(original_span, text=text, rd_underline=underline) for text, underline in fragments if text)
    parts = distribute_text([s.get("text", "") for s in styled], value)
    markup = []
    for span, part in zip(styled, parts):
        if not part:
            continue
        family = fonts.family(span, part)
        color = int(span.get("color", 0)) & 0xffffff
        style = f"font-family:{family};font-size:{float(span.get('size', 11)):.5f}pt;color:#{color:06x};"
        if span.get("rd_underline"):
            style += "text-decoration:underline;"
        markup.append(f'<span style="{style}">{escape(part).replace(chr(10), "<br>")}</span>')
    rtl = fonts.language_tag in {"ar", "fa", "he", "ur"}
    css = "".join(fonts.faces) + "*{box-sizing:border-box;}body,div{margin:0;padding:0;}div{overflow-wrap:normal;white-space:normal;}"
    # A measurement surface, not a user-visible page. Never shrink the story.
    # Top clearance also protects accents in fonts whose ascent exceeds the
    # source face's ascent; they must not be clipped by the measurement page.
    surface = fitz.Rect(0, 0, width, 14400)
    for attempt in range(2):
        html = f'<div dir="{"rtl" if rtl else "ltr"}" style="line-height:{leading:.5f}pt;text-align:{"right" if rtl and align == "left" else align}">' + "".join(markup) + "</div>"
        story = fitz.Story(html=html, user_css=css, archive=fitz.Archive(str(fonts.directory)))
        more, _ = story.place(fitz.Rect(0, 32, width, 14368))
        if more:
            raise PdfLayoutError("A processed paragraph exceeds the bounded PDF layout capacity.")
        stream = io.BytesIO()
        with fitz.DocumentWriter(stream) as writer:
            device = writer.begin_page(surface)
            story.draw(device)
            writer.end_page()
        document = fitz.open(stream=stream.getvalue(), filetype="pdf")
        try:
            _repair_story_unicode(document, fonts)
            lines = [fitz.Rect(line["bbox"]) for block in document[0].get_text("dict")["blocks"]
                     if block.get("type") == 0 for line in block["lines"]]
        except Exception:
            document.close()
            raise
        overlap = max((a.y1 - b.y0 for a, b in zip(lines, lines[1:])), default=0.)
        if overlap <= .05:
            break
        document.close()
        if attempt:
            raise PdfLayoutError("The processed PDF typography would create overlapping text lines.")
        # A Unicode fallback can require more vertical clearance than a subset
        # Latin face. Preserve the point size, expanding leading only as needed.
        leading += overlap + .25
    try:
        if not lines:
            raise PdfLayoutError("The processed PDF region rendered no text.")
        bounds = fitz.Rect(lines[0])
        for line in lines[1:]:
            bounds |= line
        if bounds.x0 < -.5 or bounds.x1 > width + .5:
            raise PdfLayoutError("Processed text cannot fit this PDF column at the source font size.")
        actual = document[0].get_text(sort=False)
        # Ignore wrap whitespace only. Do not silently omit even one character.
        compact = lambda s: re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))
        if compact(actual) != compact(value):
            raise PdfLayoutError("PDF text verification failed; the rendered region differs from its processed text.")
        return _Render(document, bounds, lines)
    except Exception:
        document.close()
        raise


def _horizontal_rules(page: fitz.Page, units: list[LayoutUnit]) -> tuple[list[tuple[dict, LayoutUnit]], list[dict]]:
    anchored, fixed = [], []
    for drawing in page.get_drawings():
        items = drawing.get("items", [])
        if drawing.get("type") == "s" and items and all(item[0] == "l" and abs(item[1].y - item[2].y) < .01 for item in items):
            rect = fitz.Rect(drawing["rect"])
            candidates = [u for u in units if -.5 <= rect.y0 - u.bbox[3] <= 5
                          and rect.x0 <= u.bbox[2] and rect.x1 >= u.bbox[0]]
            inline = [u for u in units if rect.width < (u.bbox[2] - u.bbox[0]) * .8
                      and u.bbox[1] <= rect.y0 <= u.bbox[3]
                      and rect.x0 >= u.bbox[0] - 1 and rect.x1 <= u.bbox[2] + 1]
            if inline:
                drawing = dict(drawing, rd_inline=True)
                anchored.append((drawing, min(inline, key=lambda u: u.bbox[3] - rect.y0)))
                continue
            if candidates:
                anchor = min(candidates, key=lambda u: abs(rect.y0 - u.bbox[3]))
                anchored.append((drawing, anchor))
                continue
        fixed.append(drawing)
    return anchored, fixed


def _page_bounds(page: fitz.Page, units: list[LayoutUnit], top_margin: float) -> fitz.Rect:
    crop = page.cropbox
    left = min(u.bbox[0] for u in units)
    right = max(max(u.bbox[2] for u in units), crop.width - left)
    bottom_margin = min(top_margin, max(12., crop.height - max(u.bbox[3] for u in units)))
    return fitz.Rect(left, min(u.bbox[1] for u in units), right, crop.height - bottom_margin)


def _running_region(unit: LayoutUnit, height: float) -> bool:
    return unit.bbox[3] < height * .08 or unit.bbox[1] > height * .92


def _summary_gap(unit: LayoutUnit, original_gap: float) -> float:
    spans = [span for span in unit.spans if span.get("text", "").strip()]
    size = max((float(span.get("size", 11)) for span in spans), default=11.)
    heading = bool(spans) and all(span.get("flags", 0) & 16 for span in spans)
    cap = min(12., size) if heading else min(10., max(4., size * .65))
    return min(max(0., original_gap), cap)


def _frame(unit: LayoutUnit, units: list[LayoutUnit], bounds: fitz.Rect, fixed: list[dict]) -> tuple[fitz.Rect, str, float]:
    box = fitz.Rect(unit.bbox)
    size = max(float(s.get("size", 11)) for s in unit.spans)
    frame = fitz.Rect(box.x0, box.y0, bounds.x1, bounds.y1)
    align = "left"
    peers = [fitz.Rect(u.bbox) for u in units if u is not unit and u.bbox[0] >= box.x1 + 2
             and min(u.bbox[3], box.y1) - max(u.bbox[1], box.y0) > min(u.bbox[3] - u.bbox[1], box.height) * .35]
    if peers:
        edge = min(p.x0 for p in peers)
        column = [u.bbox[2] for u in units if abs(u.bbox[0] - box.x0) < 3 and u.bbox[2] < edge - 2]
        repeated_column = sum(abs(u.bbox[0] - edge) < 3 for u in units) >= 3
        midpoint = (box.x1 + edge) / 2
        frame.x1 = min(midpoint - 1.5, max(column, default=box.x1) + .5) if repeated_column else midpoint - 1.5
    # Vertical rules define table cells. Horizontal boundaries prevent a cell
    # from growing into the row below; the writer rejects impossible cell fits.
    barrier = bounds.y1
    for drawing in fixed:
        for item in drawing.get("items", []):
            if item[0] == "re":
                r = fitz.Rect(item[1])
                if r.contains(box) and (drawing.get("fill") is None or r.width < bounds.width * .98):
                    frame.x1 = min(frame.x1, r.x1 - 2)
                    barrier = min(barrier, r.y1 - 1)
            elif item[0] == "l":
                a, b = item[1:3]
                if abs(a.x - b.x) < .1 and a.x > box.x1 and min(a.y, b.y) <= box.y0 and max(a.y, b.y) >= box.y1:
                    frame.x1 = min(frame.x1, a.x - 2)
                if abs(a.y - b.y) < .1 and a.y > box.y1 and min(a.x, b.x) <= box.x0 and max(a.x, b.x) >= box.x1:
                    barrier = min(barrier, a.y - 1)
    # Recognise centred titles without treating short left-aligned headings as
    # centred. A large title retains the surrounding column's centre.
    common_size = Counter(round(float(s.get("size", 11)), 1) for u in units for s in u.spans
                          for _ in range(min(len(s.get("text", "")), 200))).most_common(1)[0][0]
    if not peers and size > common_size * 1.15 and abs((box.x0 + box.x1 - bounds.x0 - bounds.x1) / 2) < 4:
        frame.x0, frame.x1, align = bounds.x0, bounds.x1, "center"
    left_peers = [u for u in units if u is not unit and u.bbox[2] < box.x0 and abs(u.bbox[1] - box.y0) < size * .4]
    repeated_column = sum(abs(u.bbox[0] - box.x0) < 3 for u in units) >= 3
    right_field = (bounds.x1 - box.x1 < max(8., size) and not repeated_column
                   and box.height < size * 1.6 and box.width < bounds.width * .38)
    if left_peers and right_field:
        frame.x0 = (max(u.bbox[2] for u in left_peers) + box.x0) / 2 + 1.5
        frame.x1, align = box.x1, "right"
    if frame.width < 2:
        raise PdfLayoutError("The source PDF has no usable width for a text region.")
    return frame, align, barrier


def _redact_unit(page: fitz.Page, unit: LayoutUnit) -> None:
    for span in unit.spans:
        page.add_redact_annot(fitz.Rect(span["bbox"]), fill=(1, 1, 1) if unit.ocr else False, cross_out=False)
    for line in unit.pdf_lines:
        if "bullet" in line:
            page.add_redact_annot(fitz.Rect(line["bullet"]["bbox"]), fill=(1, 1, 1) if unit.ocr else False, cross_out=False)


def _draw_rule(page: fitz.Page, drawing: dict, delta: float) -> None:
    shape = page.new_shape()
    for item in drawing["items"]:
        shape.draw_line(item[1] + (0, delta), item[2] + (0, delta))
    shape.finish(color=drawing.get("color"), width=drawing.get("width", 1),
                 dashes=drawing.get("dashes"), closePath=False,
                 stroke_opacity=drawing.get("stroke_opacity", 1))
    shape.commit()


def _new_page(document: fitz.Document, original: fitz.Page) -> int:
    media, crop, rotation = original.mediabox, original.cropbox, original.rotation
    page = document.new_page(width=media.width, height=media.height)
    page.set_mediabox(media)
    page.set_cropbox(crop)
    # Content placement uses unrotated crop coordinates throughout.
    page.set_rotation(rotation)
    return page.number


def _can_carry(source: fitz.Page, rotation: int, following: fitz.Page,
               following_units: list[LayoutUnit]) -> bool:
    """Reuse spare space on a compatible following source page, not a new page.

    Do not pour overflow into a different page design, table, scan, image or
    parallel newspaper columns. Those retain their existing independent pages.
    """
    if (source.mediabox != following.mediabox or source.cropbox != following.cropbox
            or rotation != following.rotation or not following_units or following.get_image_info()):
        return False
    if not any(not _running_region(unit, following.cropbox.height) for unit in following_units):
        return False
    starts = Counter(round(unit.bbox[0] / 4) * 4 for unit in following_units)
    columns = [x for x, count in starts.items() if count >= 3]
    if any(abs(a - b) > following.cropbox.width * .2 for a in columns for b in columns):
        return False
    for drawing in following.get_drawings():
        if drawing.get("fill") is not None:
            return False
        if any(item[0] != "l" or abs(item[1].y - item[2].y) > .1 for item in drawing.get("items", [])):
            return False
    for i, unit in enumerate(following_units):
        a = fitz.Rect(unit.bbox)
        for other in following_units[i + 1:]:
            b = fitz.Rect(other.bbox)
            if (min(a.y1, b.y1) - max(a.y0, b.y0) > min(a.height, b.height) * .5
                    and min(a.height, b.height) > 18 and (a.x1 < b.x0 or b.x1 < a.x0)):
                return False
    return True


def write_preserved_pdf(layout: DocumentLayout, destination: Path, *, language_tag: str | None = None) -> None:
    replacements = layout.replacements
    if replacements is None or set(replacements) != set(layout.markers):
        raise ValueError("Source PDF layout/output alignment is incomplete.")
    if layout.action != "summarize" and any(not replacements[key].strip() for key in layout.markers):
        raise ValueError("A non-summary PDF output omitted a source region.")
    if not any(value.strip() for value in replacements.values()):
        raise ValueError("The processed document is empty.")
    if all(_words(replacements[key]) == _words(unit.text) for key, unit in zip(layout.markers, layout.units)):
        shutil.copyfile(layout.source_path, destination)
        return
    by_page: dict[int, list[tuple[str, LayoutUnit]]] = {}
    for key, unit in zip(layout.markers, layout.units):
        by_page.setdefault(unit.page, []).append((key, unit))
    with fitz.open(layout.source_path) as original, fitz.open(layout.source_path) as document, TemporaryDirectory(prefix="redocx-pdf-template-") as work:
        fonts = _TemplateFonts(original, Path(work), language_tag)
        top_margin = median(min(u.bbox[1] for _, u in pairs
                                if not _running_region(u, original[index].cropbox.height))
                            if any(not _running_region(u, original[index].cropbox.height) for _, u in pairs)
                            else min(u.bbox[1] for _, u in pairs) for index, pairs in by_page.items())
        continuations: dict[int, list[int]] = {}
        incoming: dict[int, list[_Piece]] = {}
        incoming_rules: dict[int, list[tuple[dict, _Piece, float]]] = {}
        opened: list[_Render] = []
        try:
            # All placement is planned and measured before modifying each page.
            for page_index, pairs in by_page.items():
                source = original[page_index]
                source_rotation = source.rotation
                source.set_rotation(0)
                units = [u for _, u in pairs]
                body = [u for u in units if not _running_region(u, source.cropbox.height)]
                bounds = _page_bounds(source, body or units, top_margin)
                rules, fixed = _horizontal_rules(source, units)
                underlines = [fitz.Rect(drawing["rect"]) for drawing, _ in rules if drawing.get("rd_inline")]
                images = [fitz.Rect(info["bbox"]) for info in source.get_image_info()
                          if not any(u.ocr and fitz.Rect(info["bbox"]).contains(fitz.Rect(u.bbox)) for u in units)]
                carried_in = incoming.pop(page_index, [])
                for p in carried_in:
                    p.segment = 0
                pieces: list[_Piece] = list(carried_in)
                first_piece: dict[int, _Piece] = {}
                states: list[tuple[LayoutUnit, fitz.Rect, int, float]] = [
                    (replace(p.unit, bbox=(p.target.x0, bounds.y0 - 1, p.target.x1, bounds.y0)),
                     p.target, 0, p.target.y1 + 2.) for p in carried_in]
                for key, unit in pairs:
                    frame, align, barrier = _frame(unit, units, bounds, fixed)
                    angle = _rotation(unit)
                    value = replacements[key].strip()
                    segment, y = 0, unit.bbox[1]
                    running = _running_region(unit, source.cropbox.height) and bool(body)
                    compact_summary = (layout.action == "summarize" and not running and not angle
                                       and barrier >= bounds.y1 - .5)
                    if compact_summary:
                        # Summary text is shorter; retaining every old Y position
                        # leaves the space occupied by removed text as blank holes.
                        y = bounds.y0
                    for prior, prior_frame, prior_segment, bottom in ([] if running else states):
                        if prior.bbox[3] > unit.bbox[1] + .5:
                            continue  # independent fields on the same source row
                        if min(prior_frame.x1, frame.x1) <= max(prior_frame.x0, frame.x0) + .5:
                            continue
                        gap = max(0., unit.bbox[1] - prior.bbox[3])
                        if compact_summary:
                            gap = _summary_gap(unit, gap)
                        if prior_segment > segment:
                            segment, y = prior_segment, top_margin
                        if prior_segment == segment:
                            y = max(y, bottom + gap)
                    if compact_summary and segment == 0 and y < unit.bbox[1]:
                        obstacles = images + [fitz.Rect(drawing["rect"]) for drawing in fixed
                                              if fitz.Rect(drawing["rect"]).width > 2
                                              and fitz.Rect(drawing["rect"]).height > 2]
                        for obstacle in obstacles:
                            if (obstacle.y1 <= unit.bbox[1] + .1
                                    and min(frame.x1, obstacle.x1) > max(frame.x0, obstacle.x0)
                                    and not obstacle.intersects(fitz.Rect(unit.bbox))):
                                # Reclaim text space, not the space reserved for a
                                # picture/chart between source paragraphs.
                                y = max(y, obstacle.y1 + _summary_gap(unit, unit.bbox[1] - obstacle.y1))
                    same = _words(value) == _words(unit.text)
                    rendered = None
                    if value and (not same or segment or abs(y - unit.bbox[1]) > .5):
                        if angle:
                            width = unit.bbox[3] - unit.bbox[1]
                        else:
                            width = frame.width
                        rendered = _render(unit, value, width, fonts, align, source, underlines)
                        opened.append(rendered)
                    if angle and (segment or abs(y - unit.bbox[1]) > .5):
                        raise PdfLayoutError("Expanded neighbouring text cannot move this rotated PDF region safely.")
                    if rendered is None:
                        height = unit.bbox[3] - unit.bbox[1]
                        clip = None
                    else:
                        height = rendered.bounds.width if angle in {90, 270} else rendered.bounds.height
                        clip = fitz.Rect(0, rendered.bounds.y0, frame.width, rendered.bounds.y1)
                        if angle:
                            # Individually rotated text remains inside its exact
                            # source box; page rotation is a separate property.
                            if height > unit.bbox[3] - unit.bbox[1] + .5 or rendered.bounds.height > unit.bbox[2] - unit.bbox[0] + .5:
                                raise PdfLayoutError("Processed rotated text does not fit its source region at the original point size.")
                            clip = fitz.Rect(0, rendered.bounds.y0, unit.bbox[3] - unit.bbox[1], rendered.bounds.y1)
                    if not value:
                        height = 0.
                    if running:
                        barrier = unit.bbox[3] + .5
                        if height > unit.bbox[3] - unit.bbox[1] + .5:
                            raise PdfLayoutError("Processed header/footer text exceeds its fixed source region.")
                    limit = min(bounds.y1, barrier)
                    if y + height > barrier + .5 and barrier < bounds.y1 - .5:
                        raise PdfLayoutError("Processed text exceeds a fixed PDF table cell at its original font size.")
                    if any(fitz.Rect(frame.x0, y, frame.x1, y + height).intersects(image)
                           and not fitz.Rect(unit.bbox).intersects(image) for image in images):
                        raise PdfLayoutError("Expanded PDF text would overlap an image; an editable source is needed for this layout.")
                    for drawing in fixed:
                        obstacle = fitz.Rect(drawing["rect"])
                        if (obstacle.width > 2 and obstacle.height > 2
                                and fitz.Rect(frame.x0, y, frame.x1, y + height).intersects(obstacle)
                                and not fitz.Rect(unit.bbox).intersects(obstacle)):
                            raise PdfLayoutError("Expanded PDF text would overlap fixed artwork; an editable source is needed for this layout.")
                    if not running and y + height > limit + .5:
                        # Large paragraphs may continue, but only between complete
                        # rendered lines. No word splitting or text loss is allowed.
                        if rendered is None or angle:
                            raise PdfLayoutError("The source PDF region cannot fit a continuation page.")
                        start = clip.y0
                        while start < clip.y1 - .1:
                            candidates = [line.y1 for line in rendered.lines if line.y0 >= start - .1 and line.y1 - start <= bounds.y1 - y + .1]
                            if not candidates:
                                if y > top_margin + .5:
                                    segment, y = segment + 1, top_margin
                                    continue
                                raise PdfLayoutError("A PDF text line cannot fit the source page margins.")
                            end = max(candidates)
                            piece = _Piece(unit, segment, fitz.Rect(frame.x0, y, frame.x1, y + end - start),
                                           fitz.Rect(0, start, frame.width, end), rendered, value,
                                           first=id(unit) not in first_piece)
                            pieces.append(piece)
                            first_piece.setdefault(id(unit), piece)
                            if end >= clip.y1 - .1:
                                y += end - start
                                break
                            start = min(line.y0 for line in rendered.lines if line.y0 > end - .1)
                            segment, y = segment + 1, top_margin
                        bottom = y
                    else:
                        target = fitz.Rect(unit.bbox) if angle else fitz.Rect(frame.x0, y, frame.x1, y + height)
                        piece = _Piece(unit, segment, target, clip, rendered, value)
                        pieces.append(piece)
                        first_piece[id(unit)] = piece
                        bottom = y + height
                    if not running and (value or layout.action != "summarize"):
                        states.append((unit, frame, segment, bottom))
                segments = max((p.segment for p in pieces), default=0)
                if len(document) - len(original) + segments > MAX_CONTINUATION_PAGES:
                    raise PdfLayoutError("Generated PDF exceeds the continuation-page limit.")
                carried_out = []
                next_index = page_index + 1
                if (segments == 1 and next_index < len(original)
                        and _can_carry(source, source_rotation, original[next_index],
                                       [u for _, u in by_page.get(next_index, [])])):
                    carried_out = [p for p in pieces if p.segment == 1]
                    next_top = min(u.bbox[1] for _, u in by_page[next_index]
                                   if not _running_region(u, original[next_index].cropbox.height))
                    for p in carried_out:
                        p.target += (0, next_top - top_margin, 0, next_top - top_margin)
                    incoming[next_index] = carried_out
                page = document[page_index]
                page.set_rotation(0)
                changed = [u for u in units if (p := first_piece[id(u)]).rendered is not None or not p.value
                           or p.segment or abs(p.target.y0 - u.bbox[1]) > .5]
                for unit in changed:
                    _redact_unit(page, unit)
                if changed:
                    page.apply_redactions(images=2 if any(u.ocr for u in changed) else 0, graphics=0)
                moved_rules = list(incoming_rules.pop(page_index, []))
                for drawing, unit in rules:
                    p = first_piece[id(unit)]
                    delta = p.target.y1 - unit.bbox[3]
                    if p.segment or abs(delta) > .5 or not p.value or (drawing.get("rd_inline") and p.rendered is not None):
                        r = fitz.Rect(drawing["rect"])
                        r.y0 -= max(.5, float(drawing.get("width", 1)))
                        r.y1 += max(.5, float(drawing.get("width", 1)))
                        page.add_redact_annot(r, fill=False, cross_out=False)
                        moved_rules.append((drawing, p, delta))
                if moved_rules:
                    if any(annot.type[0] == fitz.PDF_ANNOT_REDACT for annot in page.annots() or []):
                        page.apply_redactions(images=0, graphics=2, text=1)
                source.set_rotation(source_rotation)
                continuation_pages = []
                for _ in range(0 if carried_out else segments):
                    continuation_pages.append(_new_page(document, original[page_index]))
                continuations[page_index] = continuation_pages
                source.set_rotation(0)
                targets = [page_index, *continuation_pages]
                for p in pieces:
                    if any(p is out for out in carried_out):
                        continue
                    if not p.value:
                        continue
                    target_page = document[targets[p.segment]]
                    target_page.set_rotation(0)
                    if p.rendered is not None:
                        target_page.show_pdf_page(p.target, p.rendered.document, 0, clip=p.clip,
                                                  keep_proportion=True, rotate=_rotation(p.unit))
                    elif p.segment or abs(p.target.y0 - p.unit.bbox[1]) > .5:
                        raise PdfLayoutError("A moved PDF text region was not measured before placement.")
                    if p.first:
                        for line in p.unit.pdf_lines:
                            if "bullet" in line and (p.unit in changed or any(p is item for item in carried_in)):
                                shift = p.target.y0 - p.unit.bbox[1]
                                fonts.draw_marker(target_page, line["bullet"], shift)
                    # Restore clickable source URIs only where their visible label
                    # survived. Never invent destinations from generated text.
                    if p.unit in changed or any(p is item for item in carried_in):
                        link_source = original[p.unit.page]
                        for link in link_source.get_links():
                            if link.get("kind") != fitz.LINK_URI or not fitz.Rect(link["from"]).intersects(fitz.Rect(p.unit.bbox)):
                                continue
                            label = link_source.get_textbox(link["from"]).strip()
                            if label and label not in p.value:
                                start = p.unit.text.find(label)
                                if start >= 0:
                                    end = start + len(label)
                                    label = distribute_text([p.unit.text[:start], label, p.unit.text[end:]], p.value)[1].strip()
                            if label and label in p.value:
                                for rect in target_page.search_for(label, clip=p.target):
                                    target_page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": link["uri"]})
                    target_page.set_rotation(source_rotation)
                for drawing, p, delta in moved_rules:
                    if any(p is out for out in carried_out):
                        incoming_rules.setdefault(next_index, []).append((drawing, p, delta))
                        continue
                    if p.value and not drawing.get("rd_inline"):
                        _draw_rule(document[targets[p.segment]], drawing, delta)
                document[page_index].set_rotation(source_rotation)
                source.set_rotation(source_rotation)
            if any(continuations.values()):
                document.select([p for index in range(len(original)) for p in [index, *continuations.get(index, [])]])
            # A failed render never replaces an existing destination.
            temporary = Path(work) / "result.pdf"
            document.save(temporary, garbage=4, deflate=True)
            with fitz.open(temporary) as check:
                if not len(check) or any(not page.rect.is_valid for page in check):
                    raise PdfLayoutError("The generated PDF failed its final structural check.")
            shutil.copyfile(temporary, destination)
        finally:
            for rendered in opened:
                rendered.document.close()
