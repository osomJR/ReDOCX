"""Adaptive typography and pagination for Translate only.

Compatible PDFs first use the existing template-preserving writer. Complex-script
or overflowing translations get a semantic, shaped layout at readable sizes.
Neither the summary renderer nor its paragraph-spacing policy is changed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import re
from statistics import median
from tempfile import TemporaryDirectory
import unicodedata
from zipfile import ZipFile

import fitz
from fpdf import FPDF
from lxml import etree

from .document_layout import DocumentLayout, W, write_source_document
from .pdf_layout import PdfLayoutError

_COMPLEX = re.compile(r"[\u0590-\u08ff\u0900-\u097f\u1100-\u11ff\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_RTL = re.compile(r"[\u0590-\u08ff]")
_CONTACT = re.compile(r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+\d[\d ().-]{6,}\d")
_FORMATTING = re.compile(r"[\u200b\u200e\u200f\ufeff\u202a-\u202e\u2066-\u2069]")


def _inventory(text):
    return Counter(c for c in unicodedata.normalize("NFKC", _FORMATTING.sub("", text))
                   if not c.isspace() and c != "\xad")


def _rtl(text):
    for c in text:
        direction = unicodedata.bidirectional(c)
        if direction in {"R", "AL"}:
            return True
        if direction == "L":
            return False
    return False


def prepare_translation_layout(layout: DocumentLayout) -> DocumentLayout:
    """Join physical RTL wraps into paragraphs before assigning coverage markers.

    Source bullets, fields on the same row, distinct blocks and font/indent
    boundaries remain separate. This function is never used by Summarize.
    """
    if (layout.action != "translate" or layout.kind != "pdf"
            or not _RTL.search(unicodedata.normalize("NFKC", "\n".join(u.text for u in layout.units)))):
        return layout
    merged = []
    for unit in layout.units:
        previous = merged[-1] if merged else None
        if previous and previous.page == unit.page and previous.pdf_lines and unit.pdf_lines:
            a, b = previous.pdf_lines[-1], unit.pdf_lines[0]
            size = median([float(s.get("size", 11)) for s in unit.spans] or [11])
            previous_span = previous.spans[0] if previous.spans else {}
            next_span = unit.spans[0] if unit.spans else {}
            gap = unit.bbox[1] - previous.bbox[3]
            if (_rtl(previous.text) and _rtl(unit.text)
                    and a.get("block") == b.get("block")
                    and not previous.text.rstrip().endswith((".", ":", ";", "?", "!", "،", "؛", "؟", "。"))
                    and bool(int(previous_span.get("flags", 0)) & 16) == bool(int(next_span.get("flags", 0)) & 16)
                    and not b.get("bullet") and not re.match(r"^\s*[•●▪◦\d]+[.)]?\s", unit.text)
                    and 0 <= gap <= size * .65
                    and abs(previous.bbox[2] - unit.bbox[2]) <= size * .8
                    and abs(float(previous_span.get("size", size)) - size) <= .5):
                merged[-1] = replace(previous, text=previous.text.rstrip() + " " + unit.text.lstrip(),
                                     bbox=tuple(fitz.Rect(previous.bbox) | fitz.Rect(unit.bbox)),
                                     spans=previous.spans + unit.spans,
                                     pdf_lines=previous.pdf_lines + unit.pdf_lines)
                continue
        merged.append(unit)
    return replace(layout, units=tuple(merged))


def write_translated_document(
    layout: DocumentLayout, destination: Path, content: str, *, language_tag: str | None = None,
) -> None:
    if layout.action != "translate":
        raise ValueError("The adaptive translation writer only accepts Translate documents.")
    if layout.replacements is None or set(layout.replacements) != set(layout.markers):
        raise RuntimeError("Translation coverage is incomplete; no document was published.")
    if any(not value.strip() for value in layout.replacements.values()):
        raise RuntimeError("Translation omitted a source region; no document was published.")
    source_text = "\n".join(u.text for u in layout.units)
    if layout.kind == "docx":
        write_source_document(layout, destination, content, language_tag=language_tag)
        _adapt_word_typography(layout, destination, language_tag)
        return
    if layout.kind != "pdf":
        raise ValueError("The translation document writer supports PDF and DOCX.")
    if not _COMPLEX.search(unicodedata.normalize("NFKC", source_text + content)):
        try:
            write_source_document(layout, destination, content, language_tag=language_tag)
            return
        except PdfLayoutError:
            # A valid translation must not be shortened to satisfy a fixed PDF box.
            # Font, dependency, I/O and programming errors are deliberately not caught.
            pass
    _write_reflowed_pdf(layout, destination, language_tag)


def _source_size(unit):
    sizes = [float(s.get("size", 11)) for s in unit.spans if s.get("text", "").strip()]
    return median(sizes or [11.])


def _body_size(units):
    sizes = [_source_size(u) for u in units if len(u.text) > 80]
    return max(10., min(12., median(sizes or [_source_size(u) for u in units] or [11.])))


def _style(unit, body):
    size = _source_size(unit)
    total = sum(len(s.get("text", "")) for s in unit.spans) or 1
    bold = sum(len(s.get("text", "")) for s in unit.spans if int(s.get("flags", 0)) & 16) / total > .7
    heading = size >= body * 1.07 or (bold and len(unit.text) < 120)
    return max(body, min(30., size)) if heading else body, bold or heading, heading


def _display_text(text):
    # Isolate LTR contacts in RTL prose. NBSP keeps an ordinary telephone number
    # together; no digits are reversed and no directional marks enter the AI text.
    return _CONTACT.sub(lambda m: "\u2066" + m.group().replace(" ", "\u00a0") + "\u2069", text)


def _font_setup(pdf, content, language_tag):
    from .writer import _select_document_font_paths
    paths = _select_document_font_paths(content + " •", language_tag=language_tag)
    families = []
    for i, path in enumerate(paths):
        family = f"Translation{i}"
        pdf.add_font(family, fname=str(path))
        bold = Path(str(path).replace("-Regular.", "-Bold."))
        pdf.add_font(family, "B", fname=str(bold if bold.is_file() else path))
        families.append(family)
    pdf.set_fallback_fonts(families[1:], exact_match=False)
    return families[0]


def _finish_pdf(pdf, destination, expected, links=()):
    """Complete missing mappings for continuation glyphs of shaped clusters.

    fpdf2 2.8.7 assigns the cluster's Unicode sequence to its first glyph; extra
    positioning glyphs intentionally have no sequence. Map just those known CIDs
    to a zero-width nonbreaking format character, rather than letting a PDF reader
    invent ASCII/control characters from their numeric glyph codes. No outline,
    glyph position, or existing Unicode mapping is modified.
    """
    silent = {f"F{font.i}": [code for glyph, code in font.subset.items()
                            if glyph is not None and glyph.unicode == ()]
              for font in pdf.fonts.values()}
    raw = bytes(pdf.output())
    with fitz.open(stream=raw, filetype="pdf") as document:
        done = set()
        for page in document:
            for font in page.get_fonts():
                xref, resource = font[0], font[4]
                if xref in done or not silent.get(resource):
                    continue
                done.add(xref)
                kind, reference = document.xref_get_key(xref, "ToUnicode")
                if kind != "xref":
                    raise RuntimeError("A shaped translation font has no Unicode mapping.")
                cmap_xref = int(reference.split()[0])
                cmap = document.xref_stream(cmap_xref)
                codes = silent[resource]
                entries = b"".join(
                    (f"{len(batch)} beginbfchar\n" + "".join(f"<{code:04X}> <FEFF>\n" for code in batch)
                     + "endbfchar\n").encode("ascii")
                    for batch in (codes[i:i + 100] for i in range(0, len(codes), 100)))
                if b"endcmap" not in cmap:
                    raise RuntimeError("A shaped translation font has an invalid Unicode mapping.")
                document.update_stream(cmap_xref, cmap.replace(b"endcmap", entries + b"endcmap"))
        observed = "\n".join(page.get_text() for page in document)
        if _inventory(observed) != _inventory(expected):
            raise RuntimeError("The translated PDF failed complete-text verification; no output was published.")
        for uri, label, regions, unambiguous in links:
            inserted = False
            for page_index, bounds in regions:
                page = document[page_index]
                matches = page.search_for(label) if label.strip() else []
                matches = [box for box in matches if box.intersects(fitz.Rect(bounds))]
                if matches:
                    for box in matches:
                        page.insert_link({"kind": fitz.LINK_URI, "from": box, "uri": uri})
                    inserted = True
                    break
            if not inserted and unambiguous and regions:
                # The link's visible label was translated. Its identified semantic
                # region retains the original target without inventing a new URL.
                page_index, bounds = regions[0]
                box = fitz.Rect(bounds); box.y1 = min(box.y1, box.y0 + 18)
                document[page_index].insert_link({"kind": fitz.LINK_URI, "from": box, "uri": uri})
        # Save only after the complete, reopened PDF has passed verification.
        document.save(str(destination), garbage=4, deflate=True)


def _page_metrics(page, units):
    # Work in displayed coordinates so portrait/landscape and cropped pages retain
    # their physical orientation even when an input uses a /Rotate entry.
    width, height = page.rect.width, page.rect.height
    boxes = [fitz.Rect(u.bbox) * page.rotation_matrix for u in units]
    if boxes:
        left = min(r.x0 for r in boxes); right = width - max(r.x1 for r in boxes)
        top = min(r.y0 for r in boxes)
    else:
        left = right = top = 42.
    # Text bounds are estimates of PDF margins, not editable page settings.
    left = min(max(24., left), width * .18)
    right = min(max(24., right), width * .18)
    top = min(max(30., top), height * .12)
    bottom = min(54., max(30., top * .8))
    if width - left - right < 100:
        left = right = min(24., width * .1)
    return (float(width), float(height)), (left, top, right, bottom)


def _graphics(page, units):
    """Keep image/figure objects; never reinsert a page-sized scanned text layer."""
    result = []
    seen = set()
    text_boxes = [fitz.Rect(u.bbox) for u in units]
    for info in page.get_image_info(xrefs=True):
        box = fitz.Rect(info["bbox"])
        key = tuple(round(v, 1) for v in box)
        if key in seen or box.width < 6 or box.height < 6:
            continue
        seen.add(key)
        if any(u.ocr and box.contains(fitz.Rect(u.bbox)) for u in units):
            continue
        # Exclude full-page background bitmaps behind native text as well.
        if box.get_area() > page.rect.get_area() * .8 and any(box.intersects(r) for r in text_boxes):
            continue
        result.append((box.y0, page.get_pixmap(clip=box, matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")))
    # Preserve standalone vector illustrations, while source table/heading rules
    # are replaced by the new flow. Text is translated separately, never overlaid.
    if hasattr(page, "cluster_drawings"):
        for box in page.cluster_drawings():
            box = fitz.Rect(box)
            if (box.width < 20 or box.height < 20
                    or any(box.intersects(r) for r in text_boxes)
                    or any(box.intersects(fitz.Rect(info["bbox"])) for info in page.get_image_info())):
                continue
            result.append((box.y0, page.get_pixmap(clip=box, matrix=fitz.Matrix(1.5, 1.5), alpha=False).tobytes("png")))
    return result


def _write_reflowed_pdf(layout, destination, language_tag):
    values = [(u, layout.replacements[key]) for key, u in zip(layout.markers, layout.units)]
    expected = []
    pdf = FPDF(unit="pt")
    pdf.set_lang(language_tag or "")
    pdf.set_title(Path(layout.source_path).stem)
    family = _font_setup(pdf, "\n".join(t for _, t in values), language_tag)
    body = _body_size(layout.units)
    active_metrics = None
    placements = {}
    source_links = []
    with fitz.open(layout.source_path) as source:
        def add_page(metrics):
            nonlocal active_metrics
            (width, height), (left, top, right, bottom) = metrics
            pdf.set_margins(left, top, right)
            pdf.set_auto_page_break(True, margin=bottom)
            pdf.add_page(format=(width, height))
            active_metrics = metrics

        def ensure_space(height):
            if pdf.get_y() + height > pdf.h - pdf.b_margin and pdf.get_y() > pdf.t_margin + .1:
                add_page(active_metrics)

        def text_block(unit, text, *, frame=None, compact=False):
            size, bold, heading = _style(unit, body)
            # Direction follows the translated paragraph, not the source alignment.
            paragraph_rtl = ((language_tag or "").split("-")[0] == "ar"
                             if language_tag else _rtl(text))
            direction = "rtl" if paragraph_rtl else "ltr"
            pdf.set_text_shaping(True, direction=direction)
            pdf.set_font(family, "B" if bold else "", size)
            leading = size * (1.65 if re.search(r"[\u0900-\u097f]", text) else 1.55 if _RTL.search(text) else 1.38)
            bullet = bool(unit.pdf_lines and unit.pdf_lines[0].get("bullet"))
            # Source bullet ornaments are not part of the translated prose.
            marker = unit.pdf_lines[0].get("bullet", {}).get("text", "•") if bullet else ""
            if bullet and not text.lstrip().startswith(("•", "●", "▪", "◦")):
                # Preserve numbered markers; private-use symbol bullets become •.
                marker = "•" if any(unicodedata.category(c) == "Co" for c in marker) else marker
                text = marker.strip() + " " + text
                expected.append(marker.strip())
            original_center = abs((unit.bbox[0] + unit.bbox[2]) / 2 - source[unit.page].cropbox.width / 2) < 20
            align = "C" if heading and original_center and size >= body * 1.2 else "R" if direction == "rtl" else "L"
            gap = body * (.55 if heading else .28)
            if not compact and pdf.get_y() > pdf.t_margin + 1:
                pdf.ln(gap)
            indent = min(14., body * 1.1) if bullet else 0.
            origin, frame_width = frame or (pdf.l_margin, pdf.w - pdf.l_margin - pdf.r_margin)
            width = frame_width - indent
            display = _display_text(text)
            # fpdf2 performs Unicode bidi resolution and HarfBuzz shaping before
            # measurement; PDF coordinates are never measured with Latin metrics.
            pdf.set_x(origin + (indent if direction == "ltr" else 0))
            lines = pdf.multi_cell(width, leading, display, align=align, dry_run=True, output="LINES")
            # Guard against a line breaker or an overlong token dropping text.
            if _inventory("".join(lines)) != _inventory(text):
                raise RuntimeError("Translation line wrapping lost document text.")
            if heading:
                ensure_space((len(lines) + 1) * leading)
            pdf.set_x(origin + (indent if direction == "ltr" else 0))
            start_page, start_y = pdf.page_no(), pdf.get_y()
            # Isolate fallback state: fpdf2 2.8.7 can leave the first fragment's
            # fallback face active after rendering a mixed-script paragraph.
            with pdf.local_context():
                pdf.multi_cell(width, leading, display, align=align,
                               new_x="LMARGIN", new_y="NEXT")
            regions = []
            for page_number in range(start_page, pdf.page_no() + 1):
                top = start_y if page_number == start_page else pdf.t_margin
                bottom = pdf.get_y() if page_number == pdf.page_no() else pdf.h - pdf.b_margin
                if bottom > top:
                    regions.append((page_number - 1, (origin, top, origin + frame_width, bottom)))
            placements[id(unit)] = regions

        by_page = {}
        for unit, text in values:
            by_page.setdefault(unit.page, []).append((unit, text))
        for page_index, page in enumerate(source):
            pairs = by_page.get(page_index, [])
            metrics = _page_metrics(page, [u for u, _ in pairs])
            rotation = page.rotation
            page.set_rotation(0)
            for link in page.get_links():
                if link.get("kind") != fitz.LINK_URI or not link.get("uri"):
                    continue
                box = fitz.Rect(link["from"])
                owners = [u for u, _ in pairs if box.intersects(fitz.Rect(u.bbox))]
                if owners:
                    owner = max(owners, key=lambda u: (box & fitz.Rect(u.bbox)).get_area())
                    label = page.get_textbox(box).strip(" \t\n|,.;:،")
                    source_links.append((id(owner), link["uri"], label))
            page.set_rotation(rotation)
            enriched = []
            for unit, text in pairs:
                references = [(index, uri, label) for index, (key, uri, label) in enumerate(source_links)
                              if key == id(unit)]
                if len(references) > 1:
                    for index, uri, label in references:
                        if label and label in text:
                            continue
                        # Several translated anchors in one paragraph cannot all
                        # share one invisible click target. Retain the source URI
                        # as a compact reference instead of discarding the link.
                        display = uri
                        if uri.lower().startswith("mailto:"):
                            from urllib.parse import unquote
                            match = _CONTACT.search(unquote(uri[7:]))
                            display = match.group() if match else uri
                        if display not in text:
                            text += "\n" + display
                        source_links[index] = (id(unit), uri, display)
                enriched.append((unit, text))
            pairs = enriched
            if active_metrics is None or any(abs(a - b) > 1 for a, b in zip(metrics[0], active_metrics[0])):
                add_page(metrics)
            # Retain logical input page order, without carrying its old whitespace
            # or forcing a half-empty page after a translated paragraph expands.
            graphics = sorted(_graphics(page, [u for u, _ in pairs]), key=lambda item: item[0])
            def image_block(data):
                image = BytesIO(data)
                from PIL import Image
                with Image.open(image) as im:
                    ratio = im.height / im.width
                image.seek(0)
                width = min(pdf.w - pdf.l_margin - pdf.r_margin, 200.)
                height = min(width * ratio, (pdf.h - pdf.t_margin - pdf.b_margin) * .45)
                width = height / ratio
                ensure_space(height + body)
                pdf.image(image, x=pdf.l_margin, y=pdf.get_y(), w=width, h=height)
                pdf.set_y(pdf.get_y() + height + body * .5)
            rows = []
            for pair in pairs:
                unit = pair[0]
                previous = rows[-1][-1][0] if rows else None
                same_row = (previous is not None and len(rows[-1]) < 4
                            and abs(previous.bbox[1] - unit.bbox[1]) < 2
                            and abs(previous.bbox[3] - unit.bbox[3]) < 2
                            and previous.bbox[2] + 3 < unit.bbox[0]
                            and not (unit.pdf_lines and unit.pdf_lines[0].get("bullet")))
                if same_row:
                    rows[-1].append(pair)
                else:
                    rows.append([pair])
            for row in rows:
                unit = row[0][0]
                while graphics and graphics[0][0] < unit.bbox[1]:
                    image_block(graphics.pop(0)[1])
                if len(row) == 1:
                    text_block(*row[0])
                else:
                    available = pdf.w - pdf.l_margin - pdf.r_margin
                    raw_widths = [max(body * 7, u.bbox[2] - u.bbox[0]) for u, _ in row]
                    widths = [(available - 12 * (len(row) - 1)) * w / sum(raw_widths) for w in raw_widths]
                    heights = []
                    for (u, text), width in zip(row, widths):
                        size, bold, _ = _style(u, body)
                        with pdf.local_context():
                            pdf.set_font(family, "B" if bold else "", size)
                            pdf.set_text_shaping(True, direction="rtl" if language_tag == "ar" else "ltr")
                            heights.append(pdf.multi_cell(width, size * 1.65, _display_text(text),
                                                          dry_run=True, output="HEIGHT"))
                    # Short source fields/table cells stay on a shared row; longer
                    # fields stack together instead of shrinking or clipping text.
                    if max(heights) <= body * 5.5:
                        ensure_space(max(heights) + body * 2)
                        if pdf.get_y() > pdf.t_margin + 1:
                            pdf.ln(body * .3)
                        top = pdf.get_y(); bottom = top; offset = 0.
                        for (u, text), width in zip(row, widths):
                            x = (pdf.w - pdf.r_margin - offset - width if language_tag == "ar"
                                 else pdf.l_margin + offset)
                            pdf.set_y(top)
                            text_block(u, text, frame=(x, width), compact=True)
                            bottom = max(bottom, pdf.get_y()); offset += width + 12
                        pdf.set_y(bottom)
                    else:
                        for index, pair in enumerate(row):
                            text_block(*pair, compact=index > 0)
                expected.extend(text for _, text in row)
            for _, data in graphics:
                image_block(data)
    per_unit = Counter(key for key, _, _ in source_links)
    links = [(uri, label, placements.get(key, ()), per_unit[key] == 1)
             for key, uri, label in source_links]
    _finish_pdf(pdf, destination, "\n".join(expected), links)


def _adapt_word_typography(layout, path, language_tag):
    """Word keeps its editable structure; adapt complex-script properties only."""
    from .writer import _docx_font_family
    target_rtl = (language_tag or "").split("-")[0] == "ar"
    source_rtl = bool(_RTL.search("\n".join(u.text for u in layout.units)))
    family = _docx_font_family("\n".join(layout.replacements.values()), language_tag=language_tag)
    complex_target = bool(_COMPLEX.search("\n".join(layout.replacements.values())))
    if not (target_rtl or source_rtl or complex_target):
        return
    updates = {}
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    with ZipFile(path) as package:
        by_part = {}
        for key, unit in zip(layout.markers, layout.units):
            by_part.setdefault(unit.part, []).append((unit, layout.replacements[key]))
        for part, entries in by_part.items():
            root = etree.fromstring(package.read(part), parser)
            paragraphs = list(root.iter(W + "p"))
            for unit, text in entries:
                paragraph = paragraphs[unit.paragraph]
                props = paragraph.find(W + "pPr")
                if props is None:
                    props = etree.Element(W + "pPr"); paragraph.insert(0, props)
                def setting(name, value):
                    node = props.find(W + name)
                    if node is None:
                        node = etree.SubElement(props, W + name)
                    node.set(W + "val", value)
                setting("bidi", "1" if target_rtl else "0")
                alignment = props.find(W + "jc")
                if alignment is None or alignment.get(W + "val") not in {"center", "both", "distribute"}:
                    setting("jc", "right" if target_rtl else "left")
                spacing = props.find(W + "spacing")
                if spacing is not None and spacing.get(W + "lineRule") == "exact":
                    spacing.set(W + "lineRule", "auto"); spacing.set(W + "line", "300")
                for run in paragraph.iter(W + "r"):
                    rp = run.find(W + "rPr")
                    if rp is None:
                        rp = etree.Element(W + "rPr"); run.insert(0, rp)
                    fonts = rp.find(W + "rFonts")
                    if fonts is None:
                        fonts = etree.SubElement(rp, W + "rFonts")
                    for slot in ("ascii", "hAnsi", "cs", "eastAsia"):
                        fonts.set(W + slot, family)
                        fonts.attrib.pop(W + slot + "Theme", None)
                    fonts.attrib.pop(W + "cstheme", None)
                    lang = rp.find(W + "lang")
                    if lang is None:
                        lang = etree.SubElement(rp, W + "lang")
                    for slot in ("val", "bidi", "eastAsia"):
                        lang.set(W + slot, language_tag or "und")
                    direction = rp.find(W + "rtl")
                    if direction is None:
                        direction = etree.SubElement(rp, W + "rtl")
                    direction.set(W + "val", "1" if target_rtl else "0")
                    # Complex scripts need the same size/bold as their Latin peers.
                    for normal, complex_name in (("sz", "szCs"), ("b", "bCs"), ("i", "iCs")):
                        node = rp.find(W + normal)
                        if node is not None:
                            counterpart = rp.find(W + complex_name)
                            if counterpart is None:
                                counterpart = etree.SubElement(rp, W + complex_name)
                            counterpart.attrib.update(node.attrib)
            for table in root.iter(W + "tbl"):
                properties = table.find(W + "tblPr")
                if properties is None:
                    properties = etree.Element(W + "tblPr"); table.insert(0, properties)
                direction = properties.find(W + "bidiVisual")
                if direction is None and target_rtl:
                    direction = etree.SubElement(properties, W + "bidiVisual")
                if direction is not None:
                    direction.set(W + "val", "1" if target_rtl else "0")
            updates[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with TemporaryDirectory(prefix="translation-word-") as work:
            temp = Path(work) / "translated.docx"
            with ZipFile(temp, "w") as output:
                for item in package.infolist():
                    output.writestr(item, updates.get(item.filename, package.read(item.filename)))
            result = temp.read_bytes()
    path.write_bytes(result)
