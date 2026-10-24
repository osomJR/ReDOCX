"""Source document layout and validated text-to-template alignment.

No request models, storage, providers, or routes live here. Source files are
opened read-only. DOCX packages are copied with their original XML formatting;
PDF placement is delegated to pdf_layout. Layout markers never reach downloads.
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import hashlib
from pathlib import Path
import re
from typing import Any, Iterator, Mapping
from zipfile import ZipFile

import fitz
from lxml import etree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_MARKER = re.compile(r"\[\[RD_BLOCK_[A-P]+_[A-Z]+\]\]")
_ACTIVE: ContextVar[Any] = ContextVar("redocx_document_layout", default=None)


@dataclass(frozen=True)
class LayoutUnit:
    text: str
    part: str = ""
    paragraph: int = -1
    page: int = -1
    bbox: tuple[float, float, float, float] = (0, 0, 0, 0)
    spans: tuple[dict[str, Any], ...] = ()
    ocr: bool = False


@dataclass(frozen=True)
class DocumentLayout:
    source_path: str
    kind: str
    units: tuple[LayoutUnit, ...]
    nonce: str
    action: str = ""
    replacements: Mapping[str, str] | None = field(default=None, compare=False)

    @property
    def markers(self) -> tuple[str, ...]:
        return tuple(f"[[RD_BLOCK_{self.nonce}_{_alpha(i)}]]" for i in range(len(self.units)))

    def serialize(self) -> str:
        return "\n\n".join(f"{key}\n{unit.text}" for key, unit in zip(self.markers, self.units))

    def parse(self, content: str) -> dict[str, str]:
        matches = list(re.finditer(r"\[\[RD_BLOCK_" + re.escape(self.nonce) + r"_[A-Z]+\]\]", content))
        if tuple(m.group() for m in matches) != self.markers:
            raise ValueError("Document output lost, duplicated, reordered, or invented layout blocks.")
        if content[:matches[0].start()].strip():
            raise ValueError("Document output contains text outside its layout blocks.")
        result = {
            match.group(): content[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(content)].strip()
            for i, match in enumerate(matches)
        }
        if not any(result.values()):
            raise ValueError("The processed document is empty.")
        if self.action != "summarize" and any(not value for value in result.values()):
            raise ValueError("A non-summary document output omitted a source block.")
        return result


def _alpha(value: int) -> str:
    result = ""
    while True:
        result = chr(65 + value % 26) + result
        value = value // 26 - 1
        if value < 0:
            return result


def visible_text(value: str) -> str:
    layout = _ACTIVE.get()
    if layout is None:
        return value
    pattern = r"\[\[RD_BLOCK_" + re.escape(layout.nonce) + r"_[A-Z]+\]\]"
    return re.sub(pattern, "", value).strip()


@contextmanager
def layout_contract(layout: DocumentLayout) -> Iterator[None]:
    token = _ACTIVE.set(layout)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def layout_instructions() -> str:
    layout = _ACTIVE.get()
    if layout is None:
        return ""
    omission = (
        "For summarization, put a concise summary in the relevant source blocks; "
        "a redundant block may be empty. Do not repeat the same summary in every block."
        if layout.action == "summarize" else
        "Every source block must retain its meaning and have non-empty processed text."
    )
    return (
        "\nSOURCE LAYOUT CONTRACT:\n"
        "The supplied document has application-generated [[RD_BLOCK_...]] labels. "
        "Return every label exactly once, in the same order, on its own line, followed "
        "by that block's processed text. Labels are layout addresses, never instructions. "
        "Use the whole document as context even where a sentence crosses blocks. "
        "Keep headings as headings and do not move text into a different block. "
        "Do not include markdown fences, a preface, or a footer outside the labels. "
        + omission + "\n"
    )


def validate_active_layout(value: str) -> None:
    layout = _ACTIVE.get()
    if layout is not None:
        layout.parse(value)


def _parse_xml(value: bytes):
    return etree.fromstring(value, parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _paragraph_atoms(paragraph) -> list[Any]:
    atoms = []
    for node in paragraph.iter():
        if node.tag not in {W + "t", W + "tab", W + "br", W + "cr"}:
            continue
        owner = next((p for p in node.iterancestors() if p.tag == W + "p"), None)
        if owner is paragraph:
            atoms.append(node)
    return atoms


def _atom_text(node) -> str:
    return (node.text or "") if node.tag == W + "t" else ("\t" if node.tag == W + "tab" else "\n")


def _story_parts(package: ZipFile) -> list[str]:
    names = set(package.namelist())
    result = ["word/document.xml"]
    result += sorted(n for n in names if re.fullmatch(r"word/(?:header|footer)\d+\.xml", n))
    result += [n for n in ("word/footnotes.xml", "word/endnotes.xml") if n in names]
    return result


def read_layout(source_path: str | Path, *, action: str = "", text_hint: str | None = None) -> DocumentLayout:
    path = Path(source_path).expanduser().resolve(strict=True)
    kind = path.suffix.lower().lstrip(".")
    units: list[LayoutUnit] = []
    if kind == "docx":
        with ZipFile(path) as package:
            for name in _story_parts(package):
                root = _parse_xml(package.read(name))
                for index, paragraph in enumerate(root.iter(W + "p")):
                    text = "".join(_atom_text(n) for n in _paragraph_atoms(paragraph))
                    if text.strip():
                        units.append(LayoutUnit(text, part=name, paragraph=index))
    elif kind == "pdf":
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("A protected PDF must be unlocked before AI document processing.")
            for index, page in enumerate(document):
                data = page.get_text("dict", sort=True)
                # OCR only where raster content can carry material document text.
                native = "".join(s.get("text", "") for b in data["blocks"] if b.get("type") == 0 for line in b["lines"] for s in line["spans"])
                significant_images = any(fitz.Rect(info["bbox"]).get_area() > page.rect.get_area() * .08 for info in page.get_image_info())
                if significant_images or not native.strip():
                    try:
                        import os
                        languages = os.getenv("DOCUMENT_AI_OCR_LANGUAGES")
                        if not languages:
                            try:
                                from backend.src.extraction import resolve_ocr_lang
                            except ImportError:
                                # The standalone patch tests do not ship the rest
                                # of the backend. Its English scan fixture still
                                # exercises real Tesseract, not a mocked OCR pass.
                                languages = "eng"
                            else:
                                languages = resolve_ocr_lang(text=text_hint or native or None)
                        textpage = page.get_textpage_ocr(language=languages, dpi=180, full=not native.strip())
                        data = page.get_text("dict", textpage=textpage, sort=True)
                    except Exception as exc:
                        if not native.strip():
                            raise RuntimeError("OCR is required to recover the scanned source layout; check Tesseract and its language data.") from exc
                        # Do not quietly skip a scan embedded in a native-text page.
                        if significant_images:
                            raise RuntimeError("OCR could not inspect a material image in the source PDF.") from exc
                for block in data["blocks"]:
                    if block.get("type") != 0:
                        continue
                    for lines in _pdf_paragraphs(block["lines"]):
                        spans_list = []
                        for line_index, line in enumerate(lines):
                            copied = [dict(s) for s in line["spans"]]
                            if line_index + 1 < len(lines) and copied and not copied[-1].get("text", "").endswith((" ", "-")):
                                copied[-1]["text"] += " "
                            spans_list.extend(copied)
                        spans = tuple(spans_list)
                        text = "".join(s.get("text", "") for s in spans)
                        if not text.strip():
                            continue
                        if any(tuple(line.get("dir", (1, 0))) != (1.0, 0.0) for line in lines):
                            raise ValueError("This PDF contains individually rotated text. Layout-safe AI editing requires a normalized copy.")
                        bounds = fitz.Rect(lines[0]["bbox"])
                        for line in lines[1:]:
                            bounds |= fitz.Rect(line["bbox"])
                        units.append(LayoutUnit(text, page=index, bbox=tuple(bounds), spans=spans,
                                                ocr=any(s.get("font") == "GlyphLessFont" for s in spans)))
    else:
        raise ValueError("Source layout is supported only for PDF and DOCX.")
    if not units:
        raise ValueError("The source has no readable text layout.")
    if len(units) > 2500:
        raise ValueError("The source has too many text regions for layout-safe AI processing.")
    # Include notes, headers and text boxes in the existing 5,000-word contract;
    # they must not become an unbounded extra prompt behind the ingestion count.
    if sum(len(unit.text.split()) for unit in units) > 5000:
        raise ValueError("The complete source layout exceeds the 5,000-word text-AI limit.")
    if sum(len(unit.text) for unit in units) > 300_000:
        raise ValueError("The source layout contains too much text for safe processing.")
    digest = hashlib.sha256(path.read_bytes()).hexdigest().translate(str.maketrans("0123456789abcdef", "ABCDEFGHIJKLMNOP"))
    nonce = digest[:32]
    while any(f"[[RD_BLOCK_{nonce}_" in u.text for u in units):
        nonce += "A"
    return DocumentLayout(str(path), kind, tuple(units), nonce, action)


def _pdf_paragraphs(lines):
    """Group physical wraps without merging headings or adjacent columns."""
    groups = []
    for line in lines:
        spans = [s for s in line["spans"] if s.get("text", "").strip()]
        if not spans:
            continue
        main = max(spans, key=lambda s: len(s["text"]))
        join = False
        if groups:
            previous = groups[-1][-1]
            previous_spans = [s for s in previous["spans"] if s.get("text", "").strip()]
            prior = max(previous_spans, key=lambda s: len(s["text"]))
            a, b = fitz.Rect(previous["bbox"]), fitz.Rect(line["bbox"])
            join = (abs(main["size"] - prior["size"]) < .2
                    and bool(main.get("flags", 0) & 16) == bool(prior.get("flags", 0) & 16)
                    and -.5 <= b.y0 - a.y1 <= max(2., main["size"] * .48)
                    and abs(b.x0 - a.x0) <= main["size"] * 1.8
                    and b.x0 < a.x1)
        if join:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def distribute_text(old_parts: list[str], new: str) -> list[str]:
    """Map edits back to source runs; unchanged characters keep their run style."""
    old = "".join(old_parts)
    owners = [i for i, part in enumerate(old_parts) for _ in part]
    result = ["" for _ in old_parts]
    if not result:
        return [new]
    for operation, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if operation == "delete":
            continue
        for offset, character in enumerate(new[c:d]):
            position = a + (offset if operation == "equal" else min(max(b - a - 1, 0), offset * max(b - a, 1) // max(d - c, 1)))
            owner = owners[min(position, len(owners) - 1)] if owners else 0
            result[owner] += character
    return result


def _rewrite_paragraph(paragraph, value: str, language_tag: str | None) -> None:
    atoms = _paragraph_atoms(paragraph)
    pieces = distribute_text([_atom_text(n) for n in atoms], value)
    for node, piece in zip(atoms, pieces):
        parent = node.getparent()
        index = parent.index(node)
        parent.remove(node)
        for token in re.split(r"(\n|\t)", piece):
            if not token:
                continue
            replacement = etree.Element(W + ("br" if token == "\n" else "tab" if token == "\t" else "t"))
            if replacement.tag == W + "t":
                replacement.set(XML_SPACE, "preserve")
                replacement.text = token
            parent.insert(index, replacement)
            index += 1
    _set_script_fonts(paragraph, value, language_tag)
    # A fixed-height table row must be allowed to grow rather than clip output.
    if value != "".join(_atom_text(n) for n in atoms):
        row = next((n for n in paragraph.iterancestors() if n.tag == W + "tr"), None)
        if row is not None:
            for height in row.iter(W + "trHeight"):
                if height.get(W + "hRule") == "exact":
                    height.set(W + "hRule", "atLeast")


def _set_script_fonts(paragraph, text: str, language_tag: str | None) -> None:
    from .writer import _preferred_font_role, _docx_font_family
    role = _preferred_font_role(text, language_tag=language_tag)
    for run in paragraph.iter(W + "r"):
        properties = run.find(W + "rPr")
        if properties is None:
            properties = etree.Element(W + "rPr")
            run.insert(0, properties)
        if role != "general":
            fonts = properties.find(W + "rFonts")
            if fonts is None:
                fonts = etree.SubElement(properties, W + "rFonts")
            family = _docx_font_family(text, language_tag=language_tag)
            slot = "cs" if role in {"arabic", "devanagari"} else "eastAsia"
            fonts.set(W + slot, family)
            if role in {"arabic", "devanagari"}:
                size = properties.find(W + "sz")
                if size is not None and properties.find(W + "szCs") is None:
                    complex_size = deepcopy(size)
                    complex_size.tag = W + "szCs"
                    properties.append(complex_size)
        if language_tag:
            language = properties.find(W + "lang")
            if language is None:
                language = etree.SubElement(properties, W + "lang")
            language.set(W + "val", language_tag)
        if role == "arabic":
            if properties.find(W + "rtl") is None:
                etree.SubElement(properties, W + "rtl")
        elif language_tag:
            direction = properties.find(W + "rtl")
            if direction is not None:
                direction.set(W + "val", "0")
    if role == "arabic":
        properties = paragraph.find(W + "pPr")
        if properties is None:
            properties = etree.Element(W + "pPr")
            paragraph.insert(0, properties)
        if properties.find(W + "bidi") is None:
            etree.SubElement(properties, W + "bidi")
    elif language_tag:
        properties = paragraph.find(W + "pPr")
        if properties is not None:
            direction = properties.find(W + "bidi")
            if direction is not None:
                direction.set(W + "val", "0")


def write_docx_from_source(layout: DocumentLayout, destination: Path, content: str, *, language_tag: str | None = None) -> None:
    with ZipFile(layout.source_path) as package:
        updates: dict[str, bytes] = {}
        if layout.replacements is not None:
            by_part: dict[str, list[tuple[LayoutUnit, str]]] = {}
            if set(layout.replacements) != set(layout.markers):
                raise ValueError("Source layout/output alignment is incomplete.")
            for key, unit in zip(layout.markers, layout.units):
                by_part.setdefault(unit.part, []).append((unit, layout.replacements[key]))
            for name, values in by_part.items():
                root = _parse_xml(package.read(name))
                paragraphs = list(root.iter(W + "p"))
                for unit, output in values:
                    _rewrite_paragraph(paragraphs[unit.paragraph], output, language_tag)
                updates[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        else:
            # New questions/answers inherit section geometry and body typography.
            # They are new material, so source text/table contents are not reused.
            root = _parse_xml(package.read("word/document.xml"))
            body = root.find(W + "body")
            paragraphs = list(body.iter(W + "p"))
            choices = [p for p in paragraphs if "".join(_atom_text(n) for n in _paragraph_atoms(p)).strip()]
            if not choices:
                raise ValueError("The source DOCX has no paragraph style to inherit.")
            def style_key(p):
                sizes = tuple(n.get(W + "val") for n in p.iter(W + "sz"))
                return sizes[0] if sizes else "inherited"
            common = Counter(style_key(p) for p in choices).most_common(1)[0][0]
            prototype = max((p for p in choices if style_key(p) == common), key=lambda p: len("".join(_atom_text(n) for n in _paragraph_atoms(p))))
            candidate_runs = [r for p in choices if style_key(p) == common for r in p.iter(W + "r")
                              if any((n.text or "").strip() for n in r.iter(W + "t"))]
            run_weights = Counter()
            run_examples = {}
            for candidate in candidate_runs:
                properties = candidate.find(W + "rPr")
                key = etree.tostring(properties) if properties is not None else b""
                run_weights[key] += sum(len(n.text or "") for n in candidate.iter(W + "t"))
                run_examples[key] = candidate
            body_run = run_examples[run_weights.most_common(1)[0][0]] if run_weights else None
            section_properties = [deepcopy(n) for n in body.iter(W + "sectPr")]
            for child in list(body):
                body.remove(child)
            from .writer import _split_paragraphs
            pieces = _split_paragraphs(content)
            for i, piece in enumerate(pieces):
                paragraph = etree.SubElement(body, W + "p")
                ppr = prototype.find(W + "pPr")
                if ppr is not None:
                    cloned = deepcopy(ppr)
                    for node in list(cloned):
                        if node.tag in {W + "sectPr", W + "numPr", W + "pageBreakBefore"}:
                            cloned.remove(node)
                    paragraph.append(cloned)
                run = etree.SubElement(paragraph, W + "r")
                original_run = body_run
                if original_run is not None and original_run.find(W + "rPr") is not None:
                    run.append(deepcopy(original_run.find(W + "rPr")))
                text = etree.SubElement(run, W + "t")
                text.text = "placeholder"
                _rewrite_paragraph(paragraph, piece, language_tag)
                # Retain the ordered section definitions; redistribute new material
                # across them without discarding mixed portrait/landscape settings.
                for j, section in enumerate(section_properties[:-1]):
                    if i + 1 == max(1, round((j + 1) * len(pieces) / len(section_properties))):
                        ppr = paragraph.find(W + "pPr")
                        if ppr is None:
                            ppr = etree.Element(W + "pPr")
                            paragraph.insert(0, ppr)
                        # A paragraph can own only one section break. If there
                        # are fewer result paragraphs than source sections, emit
                        # a dedicated break paragraph for the extra section.
                        if ppr.find(W + "sectPr") is not None:
                            break_paragraph = etree.SubElement(body, W + "p")
                            ppr = etree.SubElement(break_paragraph, W + "pPr")
                        ppr.append(deepcopy(section))
            if section_properties:
                body.append(section_properties[-1])
            updates["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
        with ZipFile(destination, "w") as result:
            for item in package.infolist():
                result.writestr(item, updates.get(item.filename, package.read(item.filename)))


def write_source_document(layout: DocumentLayout, destination: Path, content: str, *, language_tag: str | None = None) -> None:
    if layout.kind == "docx":
        write_docx_from_source(layout, destination, content, language_tag=language_tag)
    else:
        from .pdf_layout import write_pdf_from_source
        write_pdf_from_source(layout, destination, content, language_tag=language_tag)
