from __future__ import annotations

"""Full data masking using Redaction's detection, review and verification rules.

Detection and coordinate mapping are shared with the existing Redaction engine.
Only the format writers below differ: selected source data is removed before
asterisks are drawn. No Redaction functions or process-wide settings are patched.
"""

from copy import deepcopy
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Optional, Sequence
import uuid

import docx
import fitz
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.run import Run
from PIL import Image, ImageDraw, ImageFont, ImageOps
from lxml import etree

from backend.src.extraction import _redaction_docx_parts, redaction_pdf_surfaces, resolve_ocr_lang
from backend.src.redaction_analysis import redaction_analysis_session
from backend.src.processing.data_protection.client import (
    DEFAULT_DLP_LOCATION, DEFAULT_MIN_LIKELIHOOD, DetectionCandidate,
    GoogleSDPClient, TextFinding, merge_overlapping_findings,
)
from backend.src.processing.data_protection.redaction import redact as _redaction
from backend.src.schema import (
    AnalyzerRequest, AnalyzerResponse, DataMaskingRequest, DocumentInputFormat,
    DocumentPayload, FeatureType, HumanReviewRequirement, RedactionRequest,
)
from backend.src.validation import (
    build_document_file_result, validate_analyzer_request, validate_analyzer_response,
)

DEFAULT_PDF_RENDER_SCALE = _redaction.DEFAULT_PDF_RENDER_SCALE
MASK_BACKGROUND = (1, 1, 1)
_FILE_OUTPUT_MAP = _redaction._FILE_OUTPUT_MAP
_SUPPORTED_INPUTS = _redaction._SUPPORTED_INPUTS

# These helpers perform recognition, matching or verification only. Sharing
# their implementations keeps minority-language OCR, exact custom matching,
# signature coverage and retained-content checks identical to Redaction.
_docx_font_path = _redaction._docx_font_path
_docx_font_properties = _redaction._docx_font_properties
_docx_render_pdf = _redaction._docx_render_pdf
_docx_story_surfaces = _redaction._docx_story_surfaces
_display_boxes_to_raw = _redaction._display_boxes_to_raw
_image_findings = _redaction._image_findings
_inverse_exif_image = _redaction._inverse_exif_image
_manual_image_boxes = _redaction._manual_image_boxes
_normalize_manual_redactions = _redaction._normalize_manual_redactions
_ocr_redaction_boxes = _redaction._ocr_redaction_boxes
_page_has_rendered_content = _redaction._page_has_rendered_content
_paragraph_run_spans = _redaction._paragraph_run_spans
_pdf_character_records = _redaction._pdf_character_records
_pdf_metadata_findings = _redaction._pdf_metadata_findings
_pdf_rect_has_visible_content = _redaction._pdf_rect_has_visible_content
_redaction_boxes_for_image = _redaction._redaction_boxes_for_image
_redaction_findings = _redaction._redaction_findings
_save_pdf_checked = _redaction._save_pdf_checked
_signature_shares_source_graphics = _redaction._signature_shares_source_graphics
_signature_target_selected = _redaction._signature_target_selected
_verify_pdf_untouched_pixels = _redaction._verify_pdf_untouched_pixels


def _ensure_mask_request(request: AnalyzerRequest) -> tuple[DocumentPayload, DataMaskingRequest]:
    if request.action != FeatureType.data_mask:
        raise ValueError("data_mask.py only handles action='data_mask'.")
    if not isinstance(request.input, DocumentPayload) or not isinstance(request.payload, DataMaskingRequest):
        raise ValueError("data_mask requires DocumentPayload and DataMaskingRequest.")
    if request.input.metadata.input_format not in _SUPPORTED_INPUTS:
        raise ValueError("data_mask only supports pdf, docx, jpg, jpeg, png.")
    if request.policy.structure_preservation is not True:
        raise ValueError("structure_preservation must be True for data_mask.")
    return request.input, request.payload


def _detection_request(request: AnalyzerRequest) -> AnalyzerRequest:
    """Adapt the validated privacy policy, without altering the caller's request."""
    document, payload = _ensure_mask_request(request)
    return AnalyzerRequest(
        action=FeatureType.redact,
        input=document,
        payload=RedactionRequest(
            feature=FeatureType.redact,
            document_type=payload.document_type,
            target_data=list(payload.target_data),
            review_exclusions=list(payload.review_exclusions),
            custom_redaction_exclusions=list(payload.custom_redaction_exclusions),
        ),
        policy=request.policy,
        system_language=request.system_language,
    )


def mask_value_by_target(label: str, quote: str) -> str:
    """Mask every selected visible character; never retain initials or ID suffixes."""
    del label
    return "".join(character if character.isspace() else "*" for character in quote)


def mask_text_with_findings(text: str, findings: Sequence[TextFinding]) -> str:
    result = text
    for finding in sorted(merge_overlapping_findings(findings, original_text=text),
                          key=lambda item: item.start, reverse=True):
        result = result[:finding.start] + mask_value_by_target(
            finding.label, text[finding.start:finding.end],
        ) + result[finding.end:]
    return result


def _remove_metadata_findings(value: str, findings: Sequence[TextFinding]) -> str:
    return mask_text_with_findings(value, findings).strip()


def _mask_pdf_metadata(*, document, sdp, payload, custom_redactions=None) -> None:
    metadata = dict(document.metadata or {})
    changed = False
    for key, value, findings in _pdf_metadata_findings(
        document=document, sdp=sdp, payload=payload, custom_redactions=custom_redactions,
    ):
        metadata[key] = _remove_metadata_findings(value, findings)
        changed = True
    if changed:
        document.set_metadata(metadata)


def _star_segments(box):
    """Three crossing strokes form an asterisk, entirely inside the erased area."""
    x0, y0, x1, y1 = map(float, box)
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return
    count = max(1, min(4096, int(width / max(1.0, height * .6))))
    cell_width = width / count
    vertical = height * .32
    horizontal = min(vertical * .866, cell_width * .34)
    stroke = max(.05, min(vertical * .12, horizontal * .3))
    for index in range(count):
        cx, cy = x0 + cell_width * (index + .5), (y0 + y1) / 2
        yield ((cx, cy - vertical), (cx, cy + vertical), stroke)
        yield ((cx - horizontal, cy - vertical * .5),
               (cx + horizontal, cy + vertical * .5), stroke)
        yield ((cx - horizontal, cy + vertical * .5),
               (cx + horizontal, cy - vertical * .5), stroke)


def _image_boxes(image: Image.Image, boxes):
    result = []
    for box in boxes:
        x0, y0, x1, y1 = box
        clipped = (max(0, math.floor(x0)), max(0, math.floor(y0)),
                   min(image.width, math.ceil(x1)), min(image.height, math.ceil(y1)))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            result.append(clipped)
    return list(dict.fromkeys(result))


def _erase_image_boxes(image: Image.Image, boxes) -> Image.Image:
    out = (image.convert("RGBA") if "transparency" in image.info else
           image.copy() if image.mode in {"RGB", "RGBA", "L", "LA", "I;16", "I"}
           else image.convert("RGB"))
    fill = ((255, 255, 255, 255) if out.mode == "RGBA" else
            (255, 255) if out.mode == "LA" else
            65535 if out.mode == "I;16" else
            255 if out.mode in {"L", "I"} else (255, 255, 255))
    draw = ImageDraw.Draw(out)
    for x0, y0, x1, y1 in _image_boxes(out, boxes):
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)
    return out


def draw_masking_asterisks(image: Image.Image, boxes) -> Image.Image:
    out = _erase_image_boxes(image, boxes)
    draw = ImageDraw.Draw(out)
    ink = ((0, 0, 0, 255) if out.mode == "RGBA" else
           (0, 255) if out.mode == "LA" else
           0 if out.mode in {"L", "I;16", "I"} else (0, 0, 0))
    # Erase every region before drawing any symbol, including overlapping boxes.
    for box in _image_boxes(out, boxes):
        for start, end, stroke in _star_segments(box):
            draw.line((start, end), fill=ink, width=max(1, round(stroke)))
    return out


def pixelate_boxes(image: Image.Image, boxes, *, blur_radius: int = 3) -> Image.Image:
    """Compatibility entry point: permanent removal followed by asterisks."""
    del blur_radius
    return draw_masking_asterisks(image, boxes)


def _draw_pdf_asterisks(page, boxes) -> None:
    rotation = page.rotation
    try:
        page.set_rotation(0)
        for box in dict.fromkeys(tuple(rect) for rect in boxes):
            for start, end, stroke in _star_segments(box):
                page.draw_line(start, end, color=(0, 0, 0), width=stroke, overlay=True)
    finally:
        page.set_rotation(rotation)


def _docx_mask_drawing(run: Any, text: str) -> Any:
    family, points, bold, italic = _docx_font_properties(run)
    font = ImageFont.truetype(_docx_font_path(family, bold, italic), size=max(1, round(points * 64)))
    width = font.getlength(text, features=["-kern", "-liga"]) / 64
    rpr = run._r.rPr
    if rpr is not None:
        spacing, scale = rpr.find(qn("w:spacing")), rpr.find(qn("w:w"))
        if spacing is not None:
            width += len(text) * float(spacing.get(qn("w:val"), "0")) / 20
        if scale is not None:
            width *= float(scale.get(qn("w:val"), "100")) / 100
    width, height = max(.05, width), max(.5, points * .65)
    scale = min(4.0, math.sqrt(4_000_000 / (width * height)))
    tile = Image.new("RGBA", (max(1, math.ceil(width * scale)), max(1, math.ceil(height * scale))), (255, 255, 255, 0))
    draw = ImageDraw.Draw(tile)
    for start, end, stroke in _star_segments((0, 0, tile.width, tile.height)):
        draw.line((start, end), fill=(0, 0, 0, 255), width=max(1, round(stroke)))
    mask = BytesIO()
    tile.save(mask, format="PNG")
    mask.seek(0)
    shape = run.add_picture(mask, width=Pt(width), height=Pt(height))
    for edge in ("distL", "distR", "distT", "distB"):
        shape._inline.set(edge, "0")
    owner_part = getattr(run._parent, "_redaction_owner_part", run.part)
    if owner_part is not run.part:
        from docx.opc.constants import RELATIONSHIP_TYPE
        blip = shape._inline.xpath(".//a:blip")[0]
        image_part = run.part.related_parts[blip.get(qn("r:embed"))]
        blip.set(qn("r:embed"), owner_part.relate_to(image_part, RELATIONSHIP_TYPE.IMAGE))
    drawing = shape._inline.getparent()
    run._r.remove(drawing)
    return drawing


def _layout_match_positions(text: str, values):
    """Map known Word matches through whitespace introduced by PDF layout.

    This is used only for verification of matches already selected in the
    original Word XML. It never broadens the user's exact matching policy.
    """
    seen = set()
    for finding in values:
        value = finding.quote if isinstance(finding, TextFinding) else str(finding)
        manual = isinstance(finding, TextFinding) and finding.label == "custom_redaction"
        key = value, manual
        if key in seen or not value.strip():
            continue
        seen.add(key)
        pieces = re.findall(r"\s+|\S", value)
        pattern = []
        previous_was_text = False
        for piece in pieces:
            if piece.isspace():
                # Preserve inline spacing. A line break inserted by the Word
                # renderer may replace a source space or split a long word.
                alternatives = [re.escape(piece), r"[ \t]*(?:\r?\n[ \t]*)+"]
                if any(character not in " \r\n" for character in piece):
                    alternatives.append(r"[^\S\r\n]+")
                pattern.append("(?:" + "|".join(alternatives) + ")")
                previous_was_text = False
            else:
                if previous_was_text:
                    pattern.append(r"(?:[ \t]*\r?\n[ \t]*)*")
                pattern.append(re.escape(piece))
                previous_was_text = True
        bounded, unbounded = [], []
        for match in re.finditer("(?=(" + "".join(pattern) + "))", text):
            start, end = match.span(1)
            positions = [index for index in range(start, end) if not text[index].isspace()]
            if _redaction._has_exact_custom_word_boundaries(text, start, end):
                bounded.append(positions)
            elif not manual:
                unbounded.append(positions)
        # Automatic detectors can legitimately identify a value embedded in a
        # label (e.g. CJK names). Prefer full-word occurrences when available.
        occurrences = bounded if bounded or manual else unbounded
        yield value, occurrences


def _verify_docx_layout(source: Path, output: Path, removed) -> None:
    """Verify retained glyphs, including when a selected phrase wraps lines."""
    with tempfile.TemporaryDirectory(prefix="redocx-mask-layout-") as directory:
        root = Path(directory)
        before = _docx_render_pdf(source, root / "before")
        after = _docx_render_pdf(output, root / "after")
        with fitz.open(before) as left, fitz.open(after) as right:
            if len(left) != len(right):
                raise RuntimeError("DOCX masking would change pagination; no output was released.")
            pages = [_pdf_character_records(page) for page in left]
            offsets = []
            text = ""
            for page_text, _characters in pages:
                offsets.append(len(text))
                text += page_text + "\n"
            selected = {index for _value, occurrences in _layout_match_positions(text, removed)
                        for occurrence in occurrences for index in occurrence}
            for old, new, (_page_text, characters), offset in zip(left, right, pages, offsets):
                if old.rect != new.rect:
                    raise RuntimeError("DOCX masking would change page geometry.")
                expected = [character for index, character in enumerate(characters)
                            if not character["c"].isspace() and index + offset not in selected]
                _new_text, new_characters = _pdf_character_records(new)
                actual = [character for character in new_characters if not character["c"].isspace()]
                if len(expected) != len(actual):
                    raise RuntimeError("DOCX masking could not verify retained text; no output was released.")
                for original, retained in zip(expected, actual):
                    if (original["c"] != retained["c"] or original["font"] != retained["font"]
                            or abs(original["size"] - retained["size"]) > .05
                            or original["color"] != retained["color"]
                            or max(abs(a - b) for a, b in zip(original["origin"], retained["origin"])) > .8):
                        raise RuntimeError("DOCX masking would move or restyle retained text; no output was released.")


def _verify_word_recovery_coverage(source_pdf: Path, *, sdp, payload,
                                   known_source_matches, custom_redactions) -> None:
    """Do not release a recovery that has lost a match selected in Word.

    A converted PDF can introduce line/page breaks or reorder glyphs. The old
    outer manual-match state proved only that Word contained the value; it did
    not prove that the recovery PDF would remove it. Check every known visible
    occurrence against the actual PDF findings before any output is published.
    Ambiguous/unmappable recovery fails closed instead of exposing source text.
    """
    if not known_source_matches:
        return
    text = ""
    covered = set()
    with fitz.open(source_pdf) as document:
        document.bake(annots=True, widgets=True)
        for page in document:
            page_text, _characters = _pdf_character_records(page)
            offset = len(text)
            for finding in _redaction_findings(sdp=sdp, text=page_text, payload=payload,
                                                custom_redactions=custom_redactions):
                covered.update(range(offset + finding.start, offset + finding.end))
            text += page_text + "\n"
    for _value, occurrences in _layout_match_positions(text, known_source_matches):
        if not occurrences or any(not set(occurrence).issubset(covered) for occurrence in occurrences):
            raise RuntimeError(
                "Word layout recovery could not verify every selected match; no masked output was released."
            )


def _stable_image_boxes(image, boxes, *, sdp, payload, custom_redactions, ocr_lang):
    result = list(boxes)
    if not boxes or not _normalize_manual_redactions(custom_redactions):
        return result
    for _attempt in range(3):
        remaining = _manual_image_boxes(
            _erase_image_boxes(image, result), sdp=sdp, payload=payload,
            custom_redactions=custom_redactions, ocr_lang=ocr_lang,
        )
        if not remaining:
            return result
        result.extend(remaining)
    raise RuntimeError("Requested text remained readable after masking; no output was released.")


def _lossless_jpeg_mask(source: Path, output: Path, boxes, *, replacement: Image.Image) -> None:
    worker = Path(__file__).with_name("_jpeg_masks.py")
    try:
        with tempfile.TemporaryDirectory(prefix="redocx-mask-jpeg-") as directory:
            pixels = Path(directory) / "removed.png"
            replacement.save(pixels, format="PNG")
            subprocess.run(
                [sys.executable, str(worker)],
                input=json.dumps({"source": str(source), "output": str(output),
                                  "replacement": str(pixels), "boxes": boxes}),
                text=True, capture_output=True, check=True, timeout=90,
            )
        _redaction._copy_jpeg_presentation_metadata(source, output)
        with Image.open(output) as verified, Image.open(source) as original:
            verified.load()
            if verified.size != original.size:
                raise RuntimeError("JPEG masking changed image dimensions.")
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise RuntimeError("The JPEG could not be masked losslessly; no output was released.") from exc


def preview_data_mask_candidates(
    request: AnalyzerRequest | Mapping[str, Any], *, sdp: GoogleSDPClient | None = None,
    project_id: str | None = None, location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD, client: Any | None = None,
    custom_redactions: Optional[Sequence[str]] = None,
    ocr_languages: Optional[Sequence[str]] = None,
) -> list[DetectionCandidate]:
    req = validate_analyzer_request(request)
    with redaction_analysis_session():
        return _redaction.preview_redaction_candidates(
            _detection_request(req), sdp=sdp, project_id=project_id, location=location,
            min_likelihood=min_likelihood, client=client, custom_redactions=custom_redactions,
            ocr_languages=ocr_languages,
        )


def apply_data_mask(
    request: AnalyzerRequest | Mapping[str, Any], *, source_path: str | Path,
    output_dir: str | Path, sdp: GoogleSDPClient | None = None,
    project_id: str | None = None, location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD, client: Any | None = None,
    ocr_languages: Optional[Sequence[str]] = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> AnalyzerResponse:
    req = validate_analyzer_request(request)
    document, _payload = _ensure_mask_request(req)
    payload = _detection_request(req).payload
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError("Data-masking source file was not found.")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{source.stem}_masked{source.suffix.lower()}"
    resolved = _redaction._resolve_sdp(
        sdp=sdp, project_id=project_id, location=location,
        min_likelihood=min_likelihood, client=client,
    )
    with redaction_analysis_session(), _redaction._manual_redaction_scope(
        custom_redactions, payload.custom_redaction_exclusions, output, exact=True,
    ):
        fmt = document.metadata.input_format
        processor = (_mask_docx if fmt == DocumentInputFormat.docx else
                     _mask_pdf if fmt == DocumentInputFormat.pdf else _mask_image_file)
        processor(source_path=source, output_path=output, sdp=resolved, payload=payload,
                  custom_redactions=custom_redactions, ocr_languages=ocr_languages)
        result = build_document_file_result(
            filename=output.name, output_format=_FILE_OUTPUT_MAP[fmt],
            file_size_mb=round(output.stat().st_size / (1024 * 1024), 4),
            algorithm_version="google-sdp-data-mask-v3",
        )
        response = AnalyzerResponse(
            action=FeatureType.data_mask, input_format=fmt, policy=req.policy,
            system_language=req.system_language, result=result,
            human_review=HumanReviewRequirement(),
        )
        return validate_analyzer_response(response, request=req)


# The format writers follow the existing Redaction writers. Their detection,
# content removal, geometry checks, safe fallbacks and temporary-file cleanup
# are retained; their presentation is changed to asterisks only.


def mask_paragraph_runs(paragraph: Any, findings: Sequence[TextFinding]) -> None:
    full_text, _spans = _paragraph_run_spans(paragraph)
    if not full_text or not findings:
        return
    selected = merge_overlapping_findings(findings, original_text=full_text)
    cursor = 0
    for run_element in list(paragraph._p.iter(qn("w:r"))):
        owner = run_element.getparent()
        while owner is not None and owner.tag != qn("w:p"):
            owner = owner.getparent()
        if owner is not paragraph._p:
            continue
        run = Run(run_element, paragraph)
        for node in list(run_element):
            if node.tag in {qn("w:tab"), qn("w:br"), qn("w:cr")}:
                cursor += 1
                continue
            if node.tag not in {qn("w:t"), qn("w:delText"), qn("w:instrText")}:
                continue
            text = node.text or ""
            cuts = {0, len(text)}
            for finding in selected:
                if finding.start < cursor + len(text) and finding.end > cursor:
                    cuts.update((max(0, finding.start - cursor), min(len(text), finding.end - cursor)))
            cuts = sorted(cuts)
            if len(cuts) == 2 and not any(f.start <= cursor and f.end >= cursor + len(text) for f in selected):
                cursor += len(text)
                continue
            if node.tag != qn("w:t"):
                # Hidden field instructions / deleted revisions must not retain PII.
                node.text = _remove_metadata_findings(text, [TextFinding(
                    start=max(0, f.start-cursor), end=min(len(text), f.end-cursor),
                    quote="", label=f.label, source=f.source,
                ) for f in selected if f.start < cursor+len(text) and f.end > cursor])
                cursor += len(text)
                continue
            index = run_element.index(node)
            # Split only this text node. Existing images, breaks, field objects,
            # hyperlinks and their relative ordering are retained untouched.
            for a, b in zip(cuts, cuts[1:]):
                fragment = text[a:b]
                covered = any(f.start <= cursor+a and f.end >= cursor+b for f in selected)
                if covered:
                    for piece in re.findall(r"\s+|\S+", fragment):
                        if piece.isspace():
                            child = OxmlElement("w:t"); child.text = piece
                            child.set(qn("xml:space"), "preserve")
                        else:
                            child = _docx_mask_drawing(run, piece)
                        run_element.insert(index, child); index += 1
                else:
                    child = OxmlElement("w:t"); child.text = fragment
                    child.set(qn("xml:space"), "preserve")
                    run_element.insert(index, child); index += 1
            run_element.remove(node)
            cursor += len(text)


def _mask_docx(*, source_path: Path, output_path: Path, sdp: GoogleSDPClient,
                 payload: RedactionRequest, custom_redactions: Optional[Sequence[str]] = None,
                 ocr_languages: Optional[Sequence[str]] = None) -> None:
    document = docx.Document(source_path)
    for part in document.part.package.parts:
        if str(part.partname).startswith(("/word/embeddings/", "/word/charts/", "/word/diagrams/")):
            raise ValueError("This Word document contains embedded objects, charts or SmartArt that cannot be inspected safely. "
                             "Replace those objects with ordinary text or PNG/JPEG images before masking.")
        for relationship in part.rels.values():
            if relationship.is_external and (relationship.reltype.endswith("/image") or relationship.reltype.endswith("/oleObject")):
                raise ValueError("Linked external Word objects cannot be inspected safely; embed their content before masking.")
    removed = []
    original_images = [part for part in document.part.package.parts if part.content_type.startswith("image/")]
    stories = list(_redaction_docx_parts(document))
    if any(next(root.iter(qn("w:dataBinding")), None) is not None for _part,root in stories):
        raise ValueError("Data-bound Word controls can restore source text; remove their data binding before masking.")
    vector_visuals = False
    if _signature_target_selected(payload) and not _redaction._visual_signature_is_excluded(payload):
        # Word/VML vector drawings cannot be permanently masked by changing an
        # image relationship. Inspect their rendered appearance, then rebuild
        # verified masked pages so the original vector objects cannot survive.
        vector_visuals = any(
            element.tag.rsplit("}", 1)[-1] in {"drawing", "pict", "object"}
            and not any(child.tag.rsplit("}", 1)[-1] in {"blip", "imagedata"}
                        for child in element.iter())
            for _part, root in stories for element in root.iter()
        )
    for paragraphs,offsets,parts,text in _docx_story_surfaces(document,stories=stories):
        findings = _redaction_findings(sdp=sdp,text=text,payload=payload,
            custom_redactions=custom_redactions) if text.strip() else []
        for paragraph,start,part_text in zip(paragraphs,offsets,parts):
            local=[]
            for finding in findings:
                left=max(0,finding.start-start);right=min(len(part_text),finding.end-start)
                if right>left:
                    quote=part_text[left:right]
                    local.append(TextFinding(start=left,end=right,quote=quote,label=finding.label,source=finding.source))
                    removed.append(TextFinding(start=left, end=right, quote=quote,
                                               label=finding.label, source=finding.source))
            if local and not vector_visuals:
                mask_paragraph_runs(paragraph,local)
    if vector_visuals:
        _mask_docx_as_page_images(
            source_path=source_path, output_path=output_path, sdp=sdp, payload=payload,
            custom_redactions=custom_redactions, ocr_languages=ocr_languages,
            known_source_matches=removed,
        )
        return
    # Notes and comments may use generic OPC Parts; serialize their changed roots.
    # Relationship URLs and alternative text are content too, not formatting.
    for part, root in stories:
        for element in root.iter():
            for key, value in list(element.attrib.items()):
                if key.rsplit("}",1)[-1] in {"descr", "title", "author", "initials"}:
                    findings = _redaction_findings(sdp=sdp, text=value, payload=payload, custom_redactions=custom_redactions)
                    if findings:
                        element.set(key, _remove_metadata_findings(value, findings))
        if getattr(part, "_element", None) is None:
            part._blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    for part in document.part.package.parts:
        for relationship in list(part.rels.values()):
            if relationship.is_external and "hyperlink" in relationship.reltype:
                value = str(relationship.target_ref)
                if _redaction_findings(sdp=sdp, text=value, payload=payload, custom_redactions=custom_redactions):
                    relationship._target = "about:blank"
        if part not in original_images:
            continue
        if part.content_type in {"image/png", "image/jpeg"}:
            with tempfile.TemporaryDirectory(prefix="redocx-redact-image-") as temp:
                suffix = ".png" if part.content_type == "image/png" else ".jpg"
                source = Path(temp)/("source"+suffix); target = Path(temp)/("redacted"+suffix)
                source.write_bytes(part.blob)
                _mask_image_file(source_path=source, output_path=target, sdp=sdp,
                                   payload=payload, custom_redactions=custom_redactions, ocr_languages=ocr_languages)
                part._blob = target.read_bytes()
        elif part.content_type.startswith("image/"):
            try:
                with Image.open(BytesIO(part.blob)) as raw:
                    image = ImageOps.exif_transpose(raw)
                    boxes = _redaction_boxes_for_image(image, sdp=sdp, payload=payload,
                        custom_redactions=custom_redactions, ocr_lang=resolve_ocr_lang(ocr_languages))
                    encoded = BytesIO()
                    options = {key: raw.info[key] for key in ("dpi", "icc_profile") if key in raw.info}
                    draw_masking_asterisks(image, boxes).save(encoded,format="PNG",**options)
                    from docx.opc.packuri import PackURI
                    part._blob = encoded.getvalue()
                    part._content_type = "image/png"
                    part._partname = PackURI("/word/media/redacted_"+uuid.uuid4().hex+".png")
            except Exception as exc:
                raise RuntimeError("An embedded Word image cannot be inspected safely; convert that image to PNG or JPEG before masking.") from exc
    for key in ("author", "last_modified_by", "title", "subject", "keywords", "comments", "identifier", "category"):
        value = getattr(document.core_properties, key, "") or ""
        findings = _redaction_findings(sdp=sdp, text=value, payload=payload, custom_redactions=custom_redactions) if value else []
        if findings:
            setattr(document.core_properties, key, _remove_metadata_findings(value, findings))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        document.save(output_path)
        if removed:
            try:
                _verify_docx_layout(source_path, output_path, removed)
            except RuntimeError:
                # Inline replacement drawings can move a keep-with-next group
                # across a page boundary. Recover from the original appearance
                # instead of relaxing layout verification or publishing drift.
                output_path.unlink(missing_ok=True)
                _mask_docx_as_page_images(
                    source_path=source_path, output_path=output_path, sdp=sdp,
                    payload=payload, custom_redactions=custom_redactions,
                    ocr_languages=ocr_languages,
                    known_source_matches=removed,
                )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _mask_docx_as_page_images(*, source_path, output_path, sdp, payload,
                               custom_redactions=None, ocr_languages=None,
                               known_source_matches=None) -> None:
    """Layout fallback: a fresh DOCX containing only verified redacted pages.

    Original document XML, revisions, fields, hyperlinks, attachments and source
    images are not copied. Affected documents retain their appearance, page sizes
    and DOCX container; page content becomes non-editable raster images.
    """
    from docx.enum.section import WD_SECTION_START
    from zipfile import ZipFile

    with tempfile.TemporaryDirectory(prefix="redocx-redact-word-pages-") as directory:
        root = Path(directory)
        source_pdf = _docx_render_pdf(source_path, root / "source")
        _verify_word_recovery_coverage(
            source_pdf, sdp=sdp, payload=payload, known_source_matches=known_source_matches,
            custom_redactions=custom_redactions,
        )
        safe_pdf = root / "redacted.pdf"
        _mask_pdf(source_path=source_pdf, output_path=safe_pdf, sdp=sdp,
                    payload=payload, custom_redactions=custom_redactions,
                    ocr_languages=ocr_languages)
        fresh = docx.Document()
        for key in ("author", "last_modified_by", "title", "subject", "keywords", "comments"):
            setattr(fresh.core_properties, key, "")
        expected_images = []
        geometry = []
        with fitz.open(safe_pdf) as pages:
            for index, page in enumerate(pages):
                section = fresh.sections[0] if index == 0 else fresh.add_section(WD_SECTION_START.NEW_PAGE)
                section.page_width, section.page_height = Pt(page.rect.width), Pt(page.rect.height)
                section.top_margin = section.bottom_margin = Pt(0)
                section.left_margin = section.right_margin = Pt(0)
                section.header_distance = section.footer_distance = Pt(0)
                geometry.append((page.rect.width, page.rect.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), colorspace=fitz.csRGB, alpha=False)
                image = pix.tobytes("png")
                expected_images.append(hashlib.sha256(image).hexdigest())
                paragraph = fresh.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = Pt(1)
                run = paragraph.add_run()
                run.font.size = Pt(1)
                picture = run.add_picture(BytesIO(image), width=Pt(page.rect.width), height=Pt(page.rect.height))
                inline = picture._inline
                anchor = OxmlElement("wp:anchor")
                for key, value in {"distT":"0", "distB":"0", "distL":"0", "distR":"0",
                                   "simplePos":"0", "relativeHeight":"0", "behindDoc":"1",
                                   "locked":"0", "layoutInCell":"1", "allowOverlap":"1"}.items():
                    anchor.set(key, value)
                position = OxmlElement("wp:simplePos")
                position.set("x", "0"); position.set("y", "0"); anchor.append(position)
                for axis in ("H", "V"):
                    position = OxmlElement("wp:position" + axis)
                    position.set("relativeFrom", "page")
                    offset = OxmlElement("wp:posOffset"); offset.text = "0"
                    position.append(offset); anchor.append(position)
                for name in ("wp:extent", "wp:effectExtent"):
                    element = inline.find(qn(name))
                    if element is not None:
                        anchor.append(deepcopy(element))
                anchor.append(OxmlElement("wp:wrapNone"))
                for name in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
                    element = inline.find(qn(name))
                    if element is not None:
                        anchor.append(deepcopy(element))
                inline.getparent().replace(inline, anchor)
        fresh.save(output_path)
        # Check stored source data as well as the rendered Word pagination.
        with ZipFile(output_path) as package:
            actual_images = [hashlib.sha256(package.read(name)).hexdigest()
                             for name in package.namelist() if name.startswith("word/media/")]
            if set(actual_images) != set(expected_images):
                raise RuntimeError("Word fallback could not verify stored page images.")
            xml = etree.fromstring(package.read("word/document.xml"))
            if any((node.text or "").strip() for node in xml.iter(qn("w:t"))):
                raise RuntimeError("Word fallback retained unexpected editable text.")
        verified_pdf = _docx_render_pdf(output_path, root / "verified")
        with fitz.open(verified_pdf) as verified:
            if len(verified) != len(geometry) or any(
                    abs(page.rect.width-width) > .1 or abs(page.rect.height-height) > .1
                    for page, (width, height) in zip(verified, geometry)):
                raise RuntimeError("Word fallback could not preserve page geometry.")
            if any(not _page_has_rendered_content(page) for page in verified):
                raise RuntimeError("Word fallback lost a rendered document page.")


def _mask_image_file(*, source_path: Path, output_path: Path, sdp: GoogleSDPClient,
                       payload: RedactionRequest, custom_redactions: Optional[Sequence[str]] = None,
                       ocr_languages: Optional[Sequence[str]] = None) -> None:
    try:
        with Image.open(source_path) as raw:
            raw.load()
            info = dict(raw.info)
            orientation = int(raw.getexif().get(274, 1))
            image = ImageOps.exif_transpose(raw)
            boxes = _redaction_boxes_for_image(image, sdp=sdp, payload=payload,
                                              custom_redactions=custom_redactions,
                                              ocr_lang=resolve_ocr_lang(ocr_languages))
            boxes = _stable_image_boxes(image,boxes,sdp=sdp,payload=payload,
                custom_redactions=custom_redactions,ocr_lang=resolve_ocr_lang(ocr_languages))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if raw.format == "JPEG":
                raw_boxes = _display_boxes_to_raw(boxes, raw.size, orientation) if boxes else []
                try:
                    _lossless_jpeg_mask(source_path, output_path, raw_boxes,
                        replacement=_inverse_exif_image(draw_masking_asterisks(image, boxes), orientation))
                except RuntimeError:
                    if raw.mode != "CMYK":
                        raise
                    # Some jpeglib builds cannot verify the fourth component.
                    # Rebuild from decoded, erased RGB pixels rather than
                    # returning a visually black file that still contains K.
                    display = image.convert("RGB")
                    profile = None
                    if info.get("icc_profile"):
                        from PIL import ImageCms
                        source_profile = ImageCms.ImageCmsProfile(BytesIO(info["icc_profile"]))
                        target_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                        display = ImageCms.profileToProfile(image, source_profile, target_profile, outputMode="RGB")
                        profile = target_profile.tobytes()
                    result = _inverse_exif_image(draw_masking_asterisks(display, boxes), orientation)
                    exif = Image.Exif()
                    for key in (274, 282, 283, 296):
                        if key in raw.getexif():
                            exif[key] = raw.getexif()[key]
                    options = {"exif": exif.tobytes(), "quality": 100, "subsampling": 0}
                    if info.get("dpi"):
                        options["dpi"] = info["dpi"]
                    if profile:
                        options["icc_profile"] = profile
                    result.save(output_path, format="JPEG", **options)
                    with Image.open(output_path) as checked:
                        checked.load()
                        if checked.mode != "RGB" or checked.size != raw.size:
                            raise RuntimeError("CMYK recovery could not verify the JPEG geometry.")
            else:
                result = _inverse_exif_image(draw_masking_asterisks(image, boxes), orientation)
                # Only presentation metadata is copied; textual metadata and EXIF
                # thumbnails can contain the original sensitive data.
                options = {key: info[key] for key in ("dpi", "icc_profile") if key in info}
                exif = Image.Exif()
                for key in (274, 282, 283, 296):
                    if key in raw.getexif():
                        exif[key] = raw.getexif()[key]
                if len(exif):
                    options["exif"] = exif.tobytes()
                from PIL.PngImagePlugin import PngInfo
                import struct
                presentation = PngInfo()
                if "gamma" in info:
                    presentation.add(b"gAMA", struct.pack(">I", round(float(info["gamma"])*100000)))
                if "srgb" in info:
                    presentation.add(b"sRGB", bytes([int(info["srgb"])]))
                if "chromaticity" in info:
                    presentation.add(b"cHRM", struct.pack(">8I", *(round(float(value)*100000) for value in info["chromaticity"])))
                result.save(output_path, format="PNG", pnginfo=presentation, **options)
            if boxes and _normalize_manual_redactions(custom_redactions):
                with Image.open(output_path) as encoded:
                    if _manual_image_boxes(_erase_image_boxes(ImageOps.exif_transpose(encoded), boxes),sdp=sdp,payload=payload,
                            custom_redactions=custom_redactions,ocr_lang=resolve_ocr_lang(ocr_languages)):
                        raise RuntimeError("Requested text remained readable in the encoded image; no output was released.")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise


def _rasterize_masked_pdf_page(page, source_display_list, masks) -> None:
    """Replace a difficult page with verified pixels from its original appearance.

    The source display list is captured before any removal. All selected areas
    are erased in the pixels before embedding. Original content streams and
    resources are detached, so no editable text, vector path or original image
    survives behind an opaque overlay. Final garbage collection removes orphan
    objects. This fallback is local to the affected page; its boxes and rotation
    remain unchanged. Other pages keep their native representation.
    """
    area = max(1.0, source_display_list.rect.get_area())
    scale = min(4.0, math.sqrt(24_000_000 / area))
    if scale < 1:
        raise ValueError("This PDF page is too large to redact at a readable resolution.")
    pix = source_display_list.get_pixmap(
        matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False,
    )
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    boxes = []
    for mask in masks:
        rect = fitz.Rect(mask)
        boxes.append((math.floor(rect.x0*scale-pix.x), math.floor(rect.y0*scale-pix.y),
                      math.ceil(rect.x1*scale-pix.x), math.ceil(rect.y1*scale-pix.y)))
    image = draw_masking_asterisks(image, boxes)
    encoded = BytesIO()
    image.save(encoded, format="PNG")

    rotation = page.rotation
    try:
        page.set_rotation(0)
        document = page.parent
        empty_stream = document.get_new_xref()
        document.update_object(empty_stream, "<<>>")
        document.update_stream(empty_stream, b"")
        page.set_contents(empty_stream)
        document.xref_set_key(page.xref, "Resources", "<<>>")
        # A cached thumbnail may carry an unmasked miniature of this page.
        document.xref_set_key(page.xref, "Thumb", "null")
        image_xref = page.insert_image(page.rect, stream=encoded.getvalue(), keep_proportion=False)
        recovered = fitz.Pixmap(document, image_xref)
        if ((recovered.width, recovered.height, recovered.n) != (image.width, image.height, 3)
                or recovered.samples != image.tobytes()
                or page.get_text("text").strip() or page.get_drawings()
                or len(page.get_image_info()) != 1):
            raise RuntimeError("A PDF page could not be flattened and verified safely.")
    finally:
        page.set_rotation(rotation)


def _mask_pdf(*, source_path: Path, output_path: Path, sdp: GoogleSDPClient,
                payload: RedactionRequest, custom_redactions: Optional[Sequence[str]] = None,
                ocr_languages: Optional[Sequence[str]] = None,
                render_scale: float = DEFAULT_PDF_RENDER_SCALE) -> None:
    temporary_paths: list[Path] = []
    rasterized_pages: set[int] = set()
    ocr_lang = resolve_ocr_lang(ocr_languages)
    try:
        with fitz.open(source_path) as document:
            expected_geometry = [(tuple(p.mediabox), tuple(p.cropbox), p.rotation) for p in document]
            # Bake form/annotation appearances so their visible and editable
            # values pass through the same permanent removal path.
            document.bake(annots=True, widgets=True)
            document.del_xml_metadata()
            _mask_pdf_metadata(document=document, sdp=sdp, payload=payload, custom_redactions=custom_redactions)
            for name in list(document.embfile_names()):
                document.embfile_del(name)
            for page in document:
                # Apply redactions can invalidate MuPDF's cached link chain.
                # Inspect/remove sensitive actions before modifying page streams.
                try:
                    links = list(page.get_links())
                except (IndexError, RuntimeError):
                    # Annotation/widget appearances were baked above. Dropping
                    # an unreadable action array preserves their visible content
                    # without retaining an uninspectable hidden destination.
                    document.xref_set_key(page.xref, "Annots", "[]")
                    links = []
                for link in links:
                    value = " ".join(str(link.get(key) or "") for key in ("uri", "file", "name")).strip()
                    if value and _redaction_findings(sdp=sdp, text=value, payload=payload, custom_redactions=custom_redactions):
                        page.delete_link(link)
                page = document.reload_page(page)
                visual_before = page.get_pixmap(alpha=False, annots=False)
                original_rotation = page.rotation
                page.set_rotation(0)
                original_scene = page.get_displaylist(annots=False)
                source_drawings = page.get_drawings()
                page.set_rotation(original_rotation)
                document.xref_set_key(page.xref, "Thumb", "null")
                bounds = fitz.Rect(page.rect) * page.derotation_matrix
                text, chars = _pdf_character_records(page)
                findings = _redaction_findings(sdp=sdp, text=text, payload=payload, custom_redactions=custom_redactions)
                selected = {i for f in findings for i in range(f.start, min(len(chars), f.end)) if "bbox" in chars[i]}
                masks = []
                signature_masks = []
                for i in selected:
                    rect = fitz.Rect(chars[i]["bbox"]) & bounds
                    if not rect.is_empty and _pdf_rect_has_visible_content(page, rect):
                        masks.append(rect)

                # OCR is additive on mixed pages. Never choose a native-text OR
                # raster branch: a single native heading must not hide a scan.
                if page.get_image_info() or not text.strip() or _signature_target_selected(payload):
                    surfaces = list(redaction_pdf_surfaces(page, original_source=True)) if page.get_image_info() or not text.strip() else []
                    if _signature_target_selected(payload):
                        pix = page.get_pixmap(matrix=fitz.Matrix(render_scale, render_scale),
                                              alpha=False, colorspace=fitz.csRGB, annots=False)
                        surfaces.append((Image.frombytes("RGB", (pix.width, pix.height), pix.samples), fitz.Rect(page.rect)))
                    for surface_index, (image, surface_rect) in enumerate(surfaces):
                        raster_boxes = []
                        # The full-page signature surface is not a third OCR pass.
                        is_signature_surface = bool(_signature_target_selected(payload) and surface_rect == page.rect and surface_index == len(surfaces)-1)
                        if not is_signature_surface:
                            raster_boxes.extend(_ocr_redaction_boxes(image, sdp=sdp, payload=payload,
                                custom_redactions=custom_redactions, ocr_lang=ocr_lang))
                        visual_payload = payload.model_copy(update={"target_data": [target for target in payload.target_data
                            if not is_signature_surface or str(getattr(target,"value",target)) == "signature"]})
                        visual_findings = _image_findings(image, sdp=sdp, payload=visual_payload,
                            custom_redactions=custom_redactions if not is_signature_surface else None)
                        raster_boxes.extend(f.bbox for f in visual_findings)
                        signature_boxes = {f.bbox for f in visual_findings if f.label == "signature"}
                        for box in raster_boxes:
                            rotated = fitz.Rect(surface_rect.x0 + box[0]/image.width*surface_rect.width,
                                                surface_rect.y0 + box[1]/image.height*surface_rect.height,
                                                surface_rect.x0 + box[2]/image.width*surface_rect.width,
                                                surface_rect.y0 + box[3]/image.height*surface_rect.height)
                            rect = (rotated * page.derotation_matrix) & bounds
                            if rect.is_empty:
                                raise RuntimeError("A detected PDF field could not be mapped to page coordinates.")
                            hit = {i for i,c in enumerate(chars) if "bbox" in c and
                                   not (fitz.Rect(c["bbox"]) & rect).is_empty and not c["c"].isspace()}
                            if box in signature_boxes:
                                signature_masks.append(rect)
                            if hit and hit <= selected and box not in signature_boxes:
                                continue
                            selected.update(hit)
                            masks.append(rect)

                saved_rotation = page.rotation
                page.set_rotation(0)
                # Remove only chosen glyph objects. Keep safe neighbouring text
                # in its original font, size, colour and position, even within a word.
                for i in selected:
                    rect = fitz.Rect(chars[i]["bbox"]) & bounds
                    if rect.is_empty:
                        continue
                    centre = (rect.tl + rect.br) / 2
                    probe = fitz.Rect(centre.x-.01, centre.y-.01, centre.x+.01, centre.y+.01)
                    page.add_redact_annot(probe, fill=None, cross_out=False)
                if selected:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                          graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                          text=fitz.PDF_REDACT_TEXT_REMOVE)
                # Pixel removal and visible bars run separately so a scan's
                # bounding rectangle cannot erase/restyle neighbouring PDF text.
                for rect in masks:
                    page.add_redact_annot(rect, fill=MASK_BACKGROUND, cross_out=False)
                if masks:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                          text=fitz.PDF_REDACT_TEXT_NONE)
                # Confirmed visual signatures may be vector paths. Remove their
                # source graphics, then verify that unrelated artwork survived.
                flatten_page = _signature_shares_source_graphics(source_drawings, signature_masks)
                for rect in signature_masks:
                    page.add_redact_annot(rect, fill=MASK_BACKGROUND, cross_out=False)
                if signature_masks:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=(fitz.PDF_REDACT_LINE_ART_NONE if flatten_page else
                                                    fitz.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED),
                                          text=fitz.PDF_REDACT_TEXT_NONE)
                page.set_rotation(saved_rotation)
                if masks and page.get_image_info() and _normalize_manual_redactions(custom_redactions):
                    for _attempt in range(3):
                        extra_masks=[]
                        for image, surface_rect in redaction_pdf_surfaces(page):
                            boxes = _manual_image_boxes(image,sdp=sdp,payload=payload,
                                custom_redactions=custom_redactions,ocr_lang=ocr_lang)
                            for box in boxes:
                                rotated = fitz.Rect(surface_rect.x0+box[0]/image.width*surface_rect.width,
                                    surface_rect.y0+box[1]/image.height*surface_rect.height,
                                    surface_rect.x0+box[2]/image.width*surface_rect.width,
                                    surface_rect.y0+box[3]/image.height*surface_rect.height)
                                extra_masks.append((rotated*page.derotation_matrix)&bounds)
                        if not extra_masks:
                            break
                        page.set_rotation(0)
                        for rect in extra_masks:
                            if rect.is_empty:
                                raise RuntimeError("A remaining manual redaction could not be mapped safely.")
                            hit={i for i,c in enumerate(chars) if "bbox" in c and not(fitz.Rect(c["bbox"])&rect).is_empty}
                            selected.update(hit)
                            page.add_redact_annot(rect,fill=MASK_BACKGROUND,cross_out=False)
                        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                            graphics=fitz.PDF_REDACT_LINE_ART_NONE,text=fitz.PDF_REDACT_TEXT_REMOVE)
                        masks.extend(extra_masks)
                        page.set_rotation(saved_rotation)
                    else:
                        raise RuntimeError("Requested text remained readable after PDF redaction; no output was released.")
                # Source text, images and signature paths have been removed.
                # Draw asterisks only now, so replacement symbols cannot be
                # mistaken for a requested literal such as "*" during removal checks.
                _draw_pdf_asterisks(page, masks)
                # A nearby/overlapping glyph bbox must not cause collateral loss.
                expected = [c for i,c in enumerate(chars) if i not in selected and not c["c"].isspace()]
                _new_text, new_chars = _pdf_character_records(page)
                actual = [c for c in new_chars if not c["c"].isspace()]
                native_text_changed = len(expected) != len(actual) or any(
                    a["c"] != b["c"] or a["font"] != b["font"] or a["color"] != b["color"]
                    or abs(a["size"]-b["size"]) > .01
                    or max(abs(x-y) for x,y in zip(a["origin"], b["origin"])) > .05
                    for a,b in zip(expected,actual)
                )
                if not flatten_page and not native_text_changed:
                    try:
                        _verify_pdf_untouched_pixels(page, visual_before, masks)
                    except RuntimeError:
                        # Recover from collateral edits using the original
                        # appearance, not the already modified page.
                        flatten_page = True
                if flatten_page or native_text_changed:
                    _rasterize_masked_pdf_page(page, original_scene, masks)
                    rasterized_pages.add(page.number)
                    # The fallback verifies every embedded pixel at 288 dpi
                    # against the original scene with only the masks applied.
                    # Comparing its 72-dpi downsample to native font hinting is
                    # not an equivalent test and can reject identical content.
            if expected_geometry != [(tuple(p.mediabox), tuple(p.cropbox), p.rotation) for p in document]:
                raise RuntimeError("PDF redaction changed page geometry; no output was released.")
            _save_pdf_checked(document, output_path, temporary_paths=temporary_paths,
                              rasterized_pages=rasterized_pages)
            with fitz.open(output_path) as verified:
                if expected_geometry != [(tuple(p.mediabox), tuple(p.cropbox), p.rotation) for p in verified]:
                    raise RuntimeError("PDF writer changed page geometry; no output was released.")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary_paths:
            path.unlink(missing_ok=True)


__all__ = ["preview_data_mask_candidates", "mask_value_by_target",
           "mask_text_with_findings", "pixelate_boxes", "mask_paragraph_runs", "apply_data_mask"]
