from __future__ import annotations

"""
ReDOCX PDF Tools - Edit / Annotate PDF processing.

Supported operation families aligned with schema.py:
- add_text / remove_text
- add_image / remove_image
- add_shape
- add_comment
- draw
- highlight
- whiteout
- add_signature / remove_signature

This module applies edits to a PDF and returns artifact metadata. It deliberately
uses normalized rectangles (0..1) so the frontend preview coordinate system maps
cleanly to backend PDF coordinates.
"""

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Callable, Optional, Protocol, Sequence
import hashlib
import io
import html
import json
import math
import mimetypes
import os
import re
import shutil
import unicodedata
import uuid

from .edit_fonts import installed_font, font_program
from .scan_text import erase_scan_words, recognize_lines, needs_ocr, ScanTextError

import fitz  # PyMuPDF
from PIL import Image
from pypdf import PdfReader
from pypdf.generic import (ArrayObject, ByteStringObject, ContentStream,
                           DictionaryObject, FloatObject, NameObject, NumberObject)

from .source_text import (
    SOURCE_OPERATIONS, prepare_source_operations, remove_source_text,
    insert_source_text, _fonts, _font_name,
)


AssetResolver = Callable[[str], str]
MAX_DRAW_PATH_CHARACTERS = 1_000_000
MAX_DRAW_PATH_TOKENS = 100_000
MAX_EDIT_OPERATIONS = 2_000
MAX_EDIT_ASSET_BYTES = 25 * 1024 * 1024
MAX_EDIT_IMAGE_PIXELS = 40_000_000
MAX_SVG_CHARACTERS = 1_000_000


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        feature: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class PdfPreviewArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    page_count: Optional[int] = None
    preview_stage: Optional[str] = None
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


@dataclass(frozen=True)
class EditedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    operations_requested: int
    operations_applied: int
    source_checksum_sha256: str
    output_checksum_sha256: str
    page_count: int
    operation_results: tuple[dict[str, Any], ...] = ()
    preview: Optional[PdfPreviewArtifact] = None
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class PdfEditBackend(Protocol):
    def edit(
        self,
        *,
        source_path: str | Path,
        operations: Sequence[Any],
        output_filename: str = "edited-document.pdf",
        generate_preview: bool = True,
        asset_resolver: Optional[AssetResolver] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> EditedPdfArtifact:
        ...


class PyMuPDFEditBackend:
    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/edit",
        preview_artifacts_dir: str | Path = "artifacts/pdf_tools/preview",
        default_font_path: Optional[str | Path] = None,
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)
        self.preview_artifacts_dir = Path(preview_artifacts_dir)
        self.default_font_path = Path(default_font_path).expanduser().resolve() if default_font_path else _env_font_path()

    def edit(
        self,
        *,
        source_path: str | Path,
        operations: Sequence[Any],
        output_filename: str = "edited-document.pdf",
        generate_preview: bool = True,
        asset_resolver: Optional[AssetResolver] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> EditedPdfArtifact:
        source = _require_pdf_path(source_path)
        if not operations:
            raise ValueError("Edit PDF requires at least one operation.")
        if len(operations) > MAX_EDIT_OPERATIONS:
            raise ValueError(f"Edit PDF accepts at most {MAX_EDIT_OPERATIONS} operations.")

        output_name = _normalize_pdf_filename(output_filename, default="edited-document.pdf")
        source_checksum_sha256 = _sha256_file(source)
        operations_requested = len(operations)
        operations_applied = 0

        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            source_page_count = int(pdf.page_count)
            if source_page_count < 1:
                raise ValueError("The source PDF has no pages.")
            # A full rewrite invalidates certificate signatures. Refuse signed
            # inputs rather than silently producing a document with broken trust.
            if _has_certificate_signature(pdf):
                raise ValueError("Digitally signed PDFs must be edited from an unsigned copy; editing would invalidate their certificate signatures.")
            ids = [getattr(op, "operation_id", None) for op in operations if getattr(op, "operation_id", None)]
            if len(ids) != len(set(ids)):
                raise ValueError("Edit operation IDs must be unique.")
            source_geometry = [_page_geometry(page) for page in pdf]
            source_snapshots = [_page_snapshot(page) for page in pdf]
            # Resolve the original text and font programs once, before any
            # destructive operation. Every changed paragraph is preflighted.
            source_plans = prepare_source_operations(pdf, list(operations), source_checksum_sha256)
            evidence: dict[int, dict[str, Any]] = {}
            # Destructive edits target the uploaded document. Apply them first
            # so layer reordering cannot erase newly inserted content.
            for index, operation in enumerate(operations):
                if _operation_value(operation) == "edit_source_text":
                    page = pdf[int(operation.page_number) - 1]
                    rotation = page.rotation
                    try:
                        page.set_rotation(0)
                        remove_source_text(page, source_plans[index], _guard_destructive_region)
                    finally:
                        page.set_rotation(rotation)
                    continue
                if _operation_value(operation) in {"remove_text", "remove_image", "remove_signature", "whiteout", "replace_text"}:
                    evidence[index] = self._apply_operation(pdf, operation, asset_resolver=asset_resolver, removal_phase=True)
            # Source text is the document's content layer. Insert its verified
            # replacement before applying new images / shapes / annotations.
            for index, plan in source_plans.items():
                page = pdf[int(plan.operation.page_number) - 1]
                rotation = page.rotation
                try:
                    page.set_rotation(0)
                    evidence[index] = insert_source_text(page, plan)
                    if plan.block.links:
                        page = pdf.reload_page(page)
                finally:
                    page.set_rotation(rotation)
            for index, operation in enumerate(operations):
                if _operation_value(operation) in SOURCE_OPERATIONS:
                    continue
                if _operation_value(operation) in {"remove_text", "remove_image", "remove_signature", "whiteout"}:
                    continue
                result = self._apply_operation(pdf, operation, asset_resolver=asset_resolver)
                if index in evidence:
                    result["verification"] = evidence[index]["verification"] + "; " + result["verification"]
                evidence[index] = result
            operations_applied = len(evidence)
            operation_results = tuple(evidence[index] for index in range(len(operations)))
            expected_snapshots = [_page_snapshot(page) for page in pdf]
            touched_pages = {int(getattr(op, "page_number")) - 1 for op in operations}
            touched_pages.update(int(getattr(op, "source_page_number")) - 1 for op in operations if _operation_value(op) == "replace_text")
            if any(expected_snapshots[index] != source_snapshots[index] for index in range(source_page_count) if index not in touched_pages):
                raise RuntimeError("Edit verification failed: an operation changed an untouched page.")
            expected_renders = {index: _render_digest(pdf[index]) for index in touched_pages}

            with TemporaryDirectory(prefix="redocx-edit-") as workdir:
                output_path = Path(workdir) / output_name
                # Redactions require a full rewrite. Avoid clean=True because it
                # can normalize unrelated content streams more aggressively than
                # an edit operation requires.
                pdf.save(output_path, garbage=4, deflate=True, clean=False)
                output_checksum_sha256 = _verify_edited_pdf(
                    output_path,
                    expected_page_count=source_page_count,
                    expected_geometry=source_geometry,
                    expected_snapshots=expected_snapshots,
                    expected_renders=expected_renders,
                )

                preview_artifact: Optional[PdfPreviewArtifact] = None
                if generate_preview:
                    preview_path = Path(workdir) / f"{Path(output_name).stem}-preview.pdf"
                    _write_preview_pdf(
                        pdf,
                        preview_path=preview_path,
                        complete_document_path=output_path,
                    )
                    preview_artifact = _preview_artifact_from_path(
                        preview_path,
                        page_count=int(pdf.page_count),
                        preview_stage="edited",
                        storage_backend=self.storage_backend,
                        artifacts_dir=self.preview_artifacts_dir,
                        owner_user_id=owner_user_id,
                        organization_id=organization_id,
                    )

                persisted_path, storage_key, download_url = _persist_or_copy(
                    output_path,
                    output_name=output_name,
                    storage_backend=self.storage_backend,
                    artifacts_dir=self.artifacts_dir,
                    owner_user_id=owner_user_id,
                    organization_id=organization_id,
                    feature="edit_pdf",
                )

        return EditedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=_get_file_size_mb(persisted_path),
            file_path=str(persisted_path),
            operations_requested=operations_requested,
            operations_applied=operations_applied,
            source_checksum_sha256=source_checksum_sha256,
            output_checksum_sha256=output_checksum_sha256,
            page_count=source_page_count,
            operation_results=operation_results,
            preview=preview_artifact,
            storage_key=storage_key,
            download_url=download_url,
        )

    def _apply_operation(
        self,
        pdf: fitz.Document,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
        removal_phase: bool = False,
    ) -> dict[str, Any]:
        op = _operation_value(operation)
        page = _page_for_operation(pdf, operation)
        if op == "replace_text" and removal_phase:
            source_page_number = int(getattr(operation, "source_page_number", 0))
            if not 1 <= source_page_number <= pdf.page_count:
                raise ValueError("Text correction source page is outside the document.")
            page = pdf[source_page_number - 1]
        rect = _normalized_rect_to_page_rect(page, _operation_rectangle(operation))

        # PyMuPDF edit methods consume unrotated coordinates. Temporarily
        # clearing page rotation is also essential for cropped pages: on a
        # rotated page whose CropBox does not match the MediaBox, drawing while
        # rotation is active introduces a crop-origin offset. PDF.js reports
        # coordinates in the displayed, rotation-aware viewport, so calculate
        # the unrotated rectangle first, edit with rotation cleared, and always
        # restore the document's original page rotation.
        if op == "replace_text" and removal_phase:
            rect = _normalized_rect_to_page_rect(page, getattr(operation, "source_rectangle"))
        original_rotation = int(getattr(page, "rotation", 0) or 0) % 360
        try:
            if original_rotation:
                page.set_rotation(0)

            before = _page_snapshot(page)
            changed = 1
            details = {}
            if op == "replace_text" and removal_phase:
                changed = self._replace_source_text(page, rect, operation)
            elif op in {"add_text", "replace_text"}:
                details = self._add_text(page, rect, operation, page_rotation=original_rotation)
            elif op == "remove_text":
                changed = self._remove_content(
                    page,
                    rect,
                    operation,
                    remove_text=True,
                    remove_images=False,
                    remove_graphics=False,
                )
            elif op == "add_image":
                self._add_image(
                    page, rect, operation, asset_resolver=asset_resolver,
                    page_rotation=original_rotation,
                )
            elif op == "remove_image":
                changed = self._remove_content(
                    page,
                    rect,
                    operation,
                    remove_text=False,
                    remove_images=True,
                    remove_graphics=False,
                )
            elif op == "add_shape":
                self._add_shape(page, rect, operation)
            elif op == "add_comment":
                self._add_comment(page, rect, operation)
            elif op == "draw":
                self._draw(page, rect, operation, asset_resolver=asset_resolver)
            elif op == "highlight":
                self._highlight(page, rect, operation)
            elif op == "whiteout":
                _guard_destructive_region(page, rect, include_annotations=True)
                self._redact(page, rect, remove_text=True, remove_images=True,
                             remove_graphics=True, fill=(1, 1, 1), image_pixels=True)
            elif op == "add_signature":
                self._add_signature(page, rect, operation, asset_resolver=asset_resolver, page_rotation=original_rotation)
            elif op == "remove_signature":
                changed = self._remove_signature(page, rect, operation)
            else:
                raise ValueError(f"Unsupported edit operation: {op}")
            page = pdf.reload_page(page)
            after = _page_snapshot(page)
            if before == after:
                raise ValueError(f"{op}: no_target_found or no content changed in the selected region.")
            if op != "remove_signature" and before["widgets"] != after["widgets"]:
                raise RuntimeError(f"{op}: verification failed because unrelated form fields changed.")
            # _add_text verifies the copied shaped glyphs against an isolated
            # rendering of the exact submitted text. Extracted Unicode is not
            # a reliable substitute for glyphs in Arabic / Indic font shaping.
        finally:
            if original_rotation:
                page.set_rotation(original_rotation)

        return {
            "operation_id": getattr(operation, "operation_id", None),
            "operation": op,
            "page_number": int(getattr(operation, "page_number")),
            **({"source_page_number":int(operation.source_page_number)} if op == "replace_text" else {}),
            "status": "applied",
            "targets_changed": changed,
            **details,
            "verification": (
                "original text removed; surrounding text preserved" if op == "replace_text" and removal_phase
                else "target objects removed; non-target text/images preserved" if op in {"remove_text", "remove_image"}
                else "selected signature removed; unrelated form fields preserved" if op == "remove_signature"
                else "selected content redacted and white fill created" if op == "whiteout"
                else "inserted text and properties serialized" if op in {"add_text", "replace_text"}
                else "content change verified"
            ),
        }

    def _add_text(
        self, page: fitz.Page, rect: fitz.Rect, operation: Any, *,
        page_rotation: int = 0,
    ) -> dict[str, Any]:
        text = str(getattr(operation, "text", "") or "").strip()
        if not text:
            raise ValueError("add_text operation requires text.")
        font_size = float(getattr(operation, "font_size", 12) or 12)
        minimum_font_size = float(getattr(operation, "minimum_font_size", 4) or 4)
        color = _hex_to_rgb01(str(getattr(operation, "color_hex", "#111111") or "#111111"))
        font_family = str(getattr(operation, "font_family", "Helvetica") or "Helvetica")
        font_weight = _enum_value(getattr(operation, "font_weight", "normal")).strip().lower()
        font_style = _enum_value(getattr(operation, "font_style", "normal")).strip().lower()
        bold = font_weight == "bold"
        italic = font_style == "italic"
        alignment = _enum_value(getattr(operation, "text_alignment", "left")).strip().lower()
        line_height = float(getattr(operation, "line_height", 1.2) or 1.2)
        opacity = max(0.05, min(float(getattr(operation, "opacity", 1.0) or 1.0), 1.0))
        # Operation angles are counterclockwise in the displayed page. The
        # PDF's clockwise /Rotate must be cancelled in unrotated coordinates.
        rotation = (int(getattr(operation, "rotation", 0) or 0) + page_rotation) % 360
        auto_fit = bool(getattr(operation, "auto_fit", True))
        padding = max(0.0, float(getattr(operation, "padding", 1.5) or 0.0))
        background_hex = getattr(operation, "background_color_hex", None)
        border_hex = getattr(operation, "border_color_hex", None)
        border_width = max(0.0, float(getattr(operation, "border_width", 0.0) or 0.0))
        background_opacity = max(
            0.0,
            min(float(getattr(operation, "background_opacity", 1.0) or 0.0), 1.0),
        )
        link_url = _validated_link_url(
            str(getattr(operation, "link_url", "") or "").strip()
        )
        font_name, font_file = _resolve_font(
            font_family,
            self.default_font_path,
            bold=bold,
            italic=italic,
        )

        background = _optional_hex_to_rgb01(background_hex)
        border = _optional_hex_to_rgb01(border_hex)
        if background is not None or (border is not None and border_width > 0):
            draw_kwargs: dict[str, Any] = {
                "color": border if border_width > 0 else None,
                "fill": background,
                "width": max(border_width, 0.1),
                "overlay": True,
            }
            try:
                page.draw_rect(
                    rect,
                    stroke_opacity=opacity,
                    fill_opacity=background_opacity,
                    **draw_kwargs,
                )
            except TypeError:  # Compatibility with older PyMuPDF releases.
                page.draw_rect(rect, **draw_kwargs)

        content_rect = fitz.Rect(
            rect.x0 + padding,
            rect.y0 + padding,
            rect.x1 - padding,
            rect.y1 - padding,
        )
        if content_rect.is_empty or content_rect.width <= 0 or content_rect.height <= 0:
            raise ValueError("Text padding leaves no writable area inside the requested rectangle.")

        inserted = False
        rendered_fonts = []
        if not hasattr(page, "insert_htmlbox"):
            raise RuntimeError("Edit PDF requires a PyMuPDF release with insert_htmlbox support.")
        archive = None
        font_css = ""
        custom_family = None
        if font_file is not None:
            program = font_program(font_file.read_bytes(), font_file.suffix.lstrip(".").lower(), font_family)
            if program is None:
                raise ValueError("The configured font cannot be embedded safely.")
        elif font_name == "redocxfont":
            # Select an embedded document font by name before consulting the
            # operator's installed font library.
            requested = _font_name(font_family) + ("bold" if bold else "") + ("italic" if italic else "")
            program = _fonts(page).get(requested) or installed_font(font_family, bold=bold, italic=italic)
        else:
            program = None
        if program is not None:
            filename = f"editor-{program.key}.{program.extension}"
            archive = fitz.Archive(program.buffer, filename)
            custom_family = "ReDOCXEditorFont"
            font_css = (f'@font-face {{font-family:{custom_family};src:url("{filename}");'
                        f'font-weight:{"bold" if bold else "normal"};'
                        f'font-style:{"italic" if italic else "normal"};}} ')
        underline = bool(getattr(operation, "underline", False))
        strikethrough = bool(getattr(operation, "strikethrough", False))
        # The pinned Story renderer does not honour a decoration list.
        # Render one decoration natively and copy the other renderer's
        # exact paths below, preserving its font metrics and fitted size.
        decoration = "underline" if underline else "line-through" if strikethrough else "none"
        safe_family = custom_family or _css_font_family(font_family)
        safe_text = html.escape(text).replace("\n", "<br>")
        css = (
            font_css + "* { box-sizing: border-box; } "
            "body { margin: 0; padding: 0; } "
            ".redocx-text { "
            f"font-family: {safe_family}; "
            f"font-size: {font_size}pt; "
            f"font-weight: {'bold' if bold else 'normal'}; "
            f"font-style: {'italic' if italic else 'normal'}; "
            f"text-decoration: {decoration}; "
            f"text-align: {alignment}; "
            f"line-height: {line_height}; "
            f"color: {str(getattr(operation, 'color_hex', '#111111') or '#111111')}; "
            "white-space: pre-wrap; overflow-wrap: anywhere; "
            "}"
        )
        scale_low = (
            max(0.01, min(1.0, minimum_font_size / font_size))
            if auto_fit
            else 1.0
        )
        html_text = f'<div class="redocx-text">{safe_text}</div>'
        unicode_identity = uuid.uuid4().hex if _needs_unicode_shaping(text) else None
        story = _UnicodeStory(html_text, css, unicode_identity, archive=archive) if unicode_identity else html_text
        with fitz.open() as rendered:
            rendered_page = rendered.new_page(width=page.rect.width, height=page.rect.height)
            try:
                spare_height, _scale = rendered_page.insert_htmlbox(
                    content_rect, story, css=css, scale_low=scale_low,
                    rotate=rotation, opacity=opacity, overlay=True, archive=archive,
                )
            except TypeError as exc:
                raise RuntimeError("The installed PyMuPDF version cannot render the requested text properties.") from exc
            inserted = spare_height >= 0
            if inserted:
                if unicode_identity:
                    extracted = re.sub(r"\s+", "", rendered_page.get_text())
                    expected = re.sub(r"\s+", "", text)
                    directions = {unicodedata.bidirectional(char) for char in text}
                    mixed_direction = bool(directions & {"R", "AL"}) and bool(directions & {"L", "EN", "AN"})
                    # PDF readers may return bidi runs in visual order. Require
                    # every scalar and every word, while verifying the actual
                    # shaped glyphs below; do not reject correctly rendered RTL
                    # text merely because its extracted run order differs.
                    if extracted != expected and not (mixed_direction and Counter(extracted) == Counter(expected)):
                        raise ValueError("The shaped text cannot preserve its original Unicode in this layout. Change the text layout or font.")
                    search_units = text.split() if mixed_direction else [line for line in text.splitlines() if line.strip()]
                    if any(not rendered_page.search_for(unit) for unit in search_units):
                        raise ValueError("The shaped text cannot preserve searchable word boundaries in this layout. Change the text layout or font.")
                reference_glyphs = _text_glyphs(rendered_page, reject_missing=True)
                if not reference_glyphs:
                    raise RuntimeError("Text rendering produced no visible glyphs.")
                if underline and strikethrough:
                    _copy_strikethrough_paths(
                        rendered_page, html_text=html_text,
                        css=css.replace("text-decoration: underline;", "text-decoration: line-through;"),
                        content_rect=content_rect, rotation=rotation,
                        scale_low=scale_low, opacity=opacity,
                        expected_glyphs=reference_glyphs, unicode_identity=unicode_identity, archive=archive,
                    )
                before_glyphs = Counter(_text_glyphs(page))
                page.show_pdf_page(page.rect, rendered, 0, keep_proportion=False, overlay=True)
                copied_glyphs = Counter(_text_glyphs(page)) - before_glyphs
                if copied_glyphs != Counter(reference_glyphs):
                    raise RuntimeError("Inserted text verification failed: rendered glyphs were not copied faithfully.")
                rendered_fonts = sorted({entry[3] for entry in rendered_page.get_fonts(full=True)})
        if not inserted:
            raise ValueError("Text does not fit in this box at the allowed font size. Enlarge the box or enable shrink-to-fit.")

        if link_url:
            page.insert_link({"kind": fitz.LINK_URI, "from": rect, "uri": link_url})
        return {"requested_font":font_family, "rendered_fonts":rendered_fonts,
                "font_substituted":bool(font_file is not None or (program and program.substituted) or len(rendered_fonts) > 1)}

    def _add_image(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
        page_rotation: int = 0,
    ) -> None:
        key = str(getattr(operation, "image_storage_key", "") or "").strip()
        if not key:
            raise ValueError("add_image operation requires image_storage_key.")
        path = _resolve_asset_path(key, asset_resolver=asset_resolver)
        fit_mode = _enum_value(getattr(operation, "fit_mode", "contain")).strip().lower()
        rotation = (int(getattr(operation, "rotation", 0) or 0) + page_rotation) % 360
        opacity = max(0.05, min(float(getattr(operation, "opacity", 1.0) or 1.0), 1.0))
        _insert_image_or_svg(
            page,
            rect,
            path,
            keep_proportion=fit_mode != "stretch",
            rotation=rotation,
            opacity=opacity,
        )
        border_width = max(0.0, float(getattr(operation, "border_width", 0.0) or 0.0))
        border = _optional_hex_to_rgb01(getattr(operation, "border_color_hex", None))
        if border_width > 0 and border is not None:
            page.draw_rect(
                rect,
                color=border,
                fill=None,
                width=border_width,
                overlay=True,
                stroke_opacity=opacity,
            )

    def _add_shape(self, page: fitz.Page, rect: fitz.Rect, operation: Any) -> None:
        shape_type = _enum_value(getattr(operation, "shape_type", "rectangle")).strip().lower()
        stroke = _hex_to_rgb01(
            str(getattr(operation, "stroke_color_hex", "#111111") or "#111111")
        )
        fill = _optional_hex_to_rgb01(getattr(operation, "fill_color_hex", None))
        width = float(getattr(operation, "stroke_width", 1.5) or 1.5)
        opacity = max(0.05, min(float(getattr(operation, "opacity", 1.0) or 1.0), 1.0))
        common = {
            "color": stroke,
            "width": width,
            "overlay": True,
            "stroke_opacity": opacity,
        }

        if shape_type == "rectangle":
            page.draw_rect(rect, fill=fill, fill_opacity=opacity, **common)
            return
        if shape_type == "ellipse":
            page.draw_oval(rect, fill=fill, fill_opacity=opacity, **common)
            return

        start = fitz.Point(rect.x0, rect.y1)
        end = fitz.Point(rect.x1, rect.y0)
        if shape_type == "line":
            page.draw_line(start, end, **common)
            return
        if shape_type == "arrow":
            page.draw_line(start, end, **common)
            _draw_arrow_head(
                page,
                start=start,
                end=end,
                color=stroke,
                width=width,
                opacity=opacity,
            )
            return
        raise ValueError(f"Unsupported PDF shape type: {shape_type}")

    @staticmethod
    def _add_comment(page: fitz.Page, rect: fitz.Rect, operation: Any) -> None:
        comment = str(getattr(operation, "comment", "") or "").strip()
        if not comment:
            raise ValueError("add_comment operation requires comment.")
        author = str(getattr(operation, "author", "") or "").strip() or "ReDOCX user"
        color = _hex_to_rgb01(
            str(getattr(operation, "color_hex", "#FACC15") or "#FACC15")
        )
        opacity = max(0.05, min(float(getattr(operation, "opacity", 1.0) or 1.0), 1.0))
        annotation = page.add_text_annot(rect.tl, comment)
        annotation.set_info(
            title=author,
            subject="ReDOCX document comment",
            content=comment,
        )
        annotation.set_colors(stroke=color)
        annotation.set_opacity(opacity)
        annotation.update()

    def _draw(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
    ) -> None:
        stroke_width = float(getattr(operation, "stroke_width", 2) or 2)
        color = _hex_to_rgb01(str(getattr(operation, "stroke_color_hex", "#111111") or "#111111"))

        strokes_key = str(getattr(operation, "strokes_storage_key", "") or "").strip()
        path_svg = str(getattr(operation, "path_svg", "") or "").strip()

        if strokes_key:
            strokes_path = _resolve_asset_path(strokes_key, asset_resolver=asset_resolver)
            strokes = json.loads(strokes_path.read_text(encoding="utf-8"))
            _draw_strokes(page, rect, strokes, color=color, width=stroke_width)
            return

        if path_svg:
            if len(path_svg) > MAX_DRAW_PATH_CHARACTERS:
                raise ValueError("draw path_svg exceeds the supported size limit.")
            if not _draw_simple_svg_path(
                page,
                rect,
                path_svg,
                color=color,
                width=stroke_width,
            ):
                raise ValueError(
                    "draw path_svg must contain only normalized M/L path commands."
                )
            return

        raise ValueError("draw operation requires path_svg or strokes_storage_key.")

    def _highlight(self, page: fitz.Page, rect: fitz.Rect, operation: Any) -> None:
        color = _hex_to_rgb01(str(getattr(operation, "color_hex", "#FFF176") or "#FFF176"))
        opacity = float(getattr(operation, "opacity", 0.35) or 0.35)
        annotation = page.add_highlight_annot(rect)
        annotation.set_colors(stroke=color)
        annotation.set_opacity(max(0.05, min(opacity, 1.0)))
        operation_id = str(getattr(operation, "operation_id", "") or "").strip()
        annotation.set_info(
            title="ReDOCX PDF Editor",
            subject="ReDOCX edit annotation",
            content=f"operation_id={operation_id}" if operation_id else "ReDOCX highlight",
        )
        annotation.update()

    def _replace_source_text(self, page: fitz.Page, hint: fitz.Rect, operation: Any) -> int:
        original = str(getattr(operation, "original_text", "") or "").strip()
        if not original:
            raise ValueError("Text correction requires the original selectable text.")
        # PDF.js geometry is only a hint. Search the PDF's actual text objects.
        matches = page.search_for(original, flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES)
        candidates = [rect for rect in matches if rect.intersects(hint)]
        if not candidates:
            raise ValueError("Text correction: no_target_found. Select the original text again; scanned text requires OCR.")
        chars = _text_characters(page)
        selected = [item for item in chars if any(rect.contains(fitz.Rect(item[1]).tl + (fitz.Rect(item[1]).br - fitz.Rect(item[1]).tl) * 0.5) for rect in candidates)]
        if _canonical_text("".join(item[0] for item in selected)) != _canonical_text(original):
            raise ValueError("Text correction is ambiguous or overlaps adjacent text. Select a single complete text run.")
        if not selected:
            raise ValueError("Text correction: no_target_found.")
        target_counts = Counter(selected)
        others = list((Counter(chars) - target_counts).elements())
        # A tiny band through each selected glyph removes that glyph without
        # trusting estimated browser bounding boxes or erasing adjacent lines.
        bands = []
        for _, bounds in selected:
            box = fitz.Rect(bounds)
            mid_y = (box.y0 + box.y1) / 2
            band = fitz.Rect(box.x0 + min(0.02, box.width / 4), mid_y - 0.1,
                             box.x1 - min(0.02, box.width / 4), mid_y + 0.1)
            if band.is_empty or any(fitz.Rect(other[1]).intersects(band) for other in others):
                raise ValueError("Text correction cannot safely separate overlapping glyphs in this PDF.")
            _guard_destructive_region(page, band, include_annotations=True)
            bands.append(band)
        _strip_unicode_clusters(page, bands)
        for band in bands:
            page.add_redact_annot(band, cross_out=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
        if Counter(_text_characters(page)) != Counter(chars) - target_counts:
            raise RuntimeError("Text correction verification failed: original or adjacent text was not preserved as expected.")
        return len(selected)

    def _remove_content(self, page: fitz.Page, rect: fitz.Rect, operation: Any, *,
                        remove_text: bool, remove_images: bool, remove_graphics: bool) -> int:
        mode = _removal_mode(operation)
        if mode == "remove_redocx_annotation":
            removed = _remove_redocx_annotations(page, rect)
            if not removed:
                raise ValueError("no_target_found: no ReDOCX annotation in the selected region.")
            return removed
        _guard_destructive_region(page, rect, include_annotations=True)
        chars = _text_characters(page)
        images = _image_instances(page)
        targets = [item for item in chars if fitz.Rect(item[1]).intersects(rect)] if remove_text else []
        image_targets = [item for item in images if fitz.Rect(item[0]).intersects(rect)] if remove_images else []
        if remove_text and not remove_images and needs_ocr(page):
            native_rects = [fitz.Rect(trace["bbox"]) for trace in page.get_texttrace()
                            if trace.get("type") != 3 and float(trace.get("opacity", 1)) > 0]
            try:
                lines = recognize_lines(page, _render_digest(page), native_rects)
            except ScanTextError as exc:
                if not targets or not any(box.intersects(rect) for box in native_rects):
                    raise ValueError("Scanned text recognition is unavailable. Retry or use Whiteout.") from exc
                lines = []
            words = [fitz.Rect(word["rect"]) + (-.35, -.35, .35, .35)
                     for line in lines for word in line["words"] if fitz.Rect(word["rect"]).intersects(rect)]
            if words:
                if any(not rect.contains(word) for word in words):
                    raise ValueError("Remove text requires selecting each recognized word completely.")
                # Remove any original searchable OCR layer as well as the
                # visible scan pixels. The source image stays outside words.
                self._redact(page, rect, remove_text=True, remove_images=False, remove_graphics=False)
                erase_scan_words(page, words)
                if Counter(_text_characters(page)) != Counter(chars) - Counter(targets):
                    raise RuntimeError("Scanned removal changed text outside the selected region.")
                return len(targets) + len(words)
            if targets and not any(box.intersects(rect) for box in native_rects):
                raise ValueError("no_target_found: the selection contains hidden OCR but no recognized visible scan words. Use Whiteout to remove artwork.")
        if remove_images and any(not rect.contains(fitz.Rect(item[0])) for item in image_targets):
            raise ValueError("Remove image requires selecting the complete image; use Whiteout to redact only part of an image.")
        if not targets and not image_targets:
            raise ValueError("no_target_found: the selected region contains no removable text or image of the requested type.")
        self._redact(page, rect, remove_text=remove_text, remove_images=remove_images,
                     remove_graphics=remove_graphics)
        expected_chars = Counter(chars) - Counter(targets)
        expected_images = Counter(images) - Counter(image_targets)
        if Counter(_text_characters(page)) != expected_chars or Counter(_image_instances(page)) != expected_images:
            raise RuntimeError("Removal verification failed: target content remains or unrelated text/images changed.")
        return len(targets) + len(image_targets)

    @staticmethod
    def _redact(page: fitz.Page, rect: fitz.Rect, *, remove_text: bool,
                remove_images: bool, remove_graphics: bool,
                fill: Optional[tuple[float, float, float]] = None,
                image_pixels: bool = False) -> None:
        if remove_graphics:
            # MuPDF removes entire paths. Refuse partial paths instead of
            # destroying graphics that extend beyond the selected rectangle.
            for drawing in page.get_drawings():
                box = fitz.Rect(drawing["rect"])
                if box.intersects(rect) and not rect.contains(box):
                    raise ValueError("Whiteout crosses a vector graphic extending outside the selection. Select the entire graphic.")
        if remove_text:
            _strip_unicode_clusters(page, [rect])
        page.add_redact_annot(rect, fill=fill, cross_out=False)
        page.apply_redactions(
            images=(fitz.PDF_REDACT_IMAGE_PIXELS if image_pixels else fitz.PDF_REDACT_IMAGE_REMOVE) if remove_images else fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED if remove_graphics else fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE if remove_text else fitz.PDF_REDACT_TEXT_NONE,
        )

    def _remove_signature(self, page: fitz.Page, rect: fitz.Rect, operation: Any) -> int:
        field_id = str(getattr(operation, "field_id", "") or "").strip()
        if _removal_mode(operation) == "remove_redocx_annotation":
            removed = _remove_redocx_annotations(page, rect, signature_only=True)
            if not removed:
                raise ValueError("no_target_found: no ReDOCX signature annotation in this region.")
            return removed
        widgets = [widget for widget in (page.widgets() or [])
                   if widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE
                   and (not field_id or widget.field_name == field_id)
                   and widget.rect.intersects(rect)]
        annotations = [] if field_id else [annotation for annotation in (page.annots() or [])
                       if annotation.rect.intersects(rect) and _is_signature_annotation(annotation)]
        if not widgets and not annotations and not field_id:
            # A flattened visual signature has no signature object to delete.
            # The user's explicitly selected region is removed as page content;
            # certificate-signed documents were rejected before any mutation.
            _guard_destructive_region(page, rect, include_annotations=True)
            pix = page.get_pixmap(clip=rect, alpha=False, annots=False)
            pixels = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            from PIL import ImageStat
            if max(ImageStat.Stat(pixels).stddev) < 1:
                raise ValueError("no_target_found: the selected signature region is blank.")
            erase_scan_words(page, [rect])
            return 1
        if len(widgets) + len(annotations) != 1:
            raise ValueError("Select exactly one signature field or visual signature before removing it.")
        if any(not rect.contains(target.rect) for target in [*widgets, *annotations]):
            raise ValueError("Select the entire signature before removing it.")
        before = _page_snapshot(page)
        content_before = [page.parent.xref_stream(xref) for xref in page.get_contents()]
        names = {widget.field_name for widget in widgets}
        for widget in widgets:
            page.delete_widget(widget)
        for annotation in annotations:
            page.delete_annot(annotation)
        after = _page_snapshot(page)
        expected_widgets = [item for item in before["widgets"] if item[0] not in names or item[1] != fitz.PDF_WIDGET_TYPE_SIGNATURE]
        content_after = [page.parent.xref_stream(xref) for xref in page.get_contents()]
        if after["widgets"] != expected_widgets or content_before != content_after or before["links"] != after["links"]:
            raise RuntimeError("Signature removal verification failed: unrelated content changed.")
        return 1

    def _add_signature(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
        page_rotation: int = 0,
    ) -> None:
        if getattr(operation, "consent_accepted", None) is not True:
            raise ValueError("Explicit boolean signature consent is required.")
        # Import a self-contained appearance Form XObject into a stamp annotation.
        # Signature removal can then delete this exact object without redaction.
        with fitz.open() as appearance:
            signature_page = appearance.new_page(width=rect.width, height=rect.height)
            self._paint_signature(signature_page, signature_page.rect, operation, asset_resolver=asset_resolver, page_rotation=page_rotation)
            original_contents = page.get_contents()
            original_resources_kind, original_resources = page.parent.xref_get_key(page.xref, "Resources")
            resource_xref = int(original_resources.split()[0]) if original_resources_kind == "xref" else None
            original_resource_object = page.parent.xref_object(resource_xref) if resource_xref else None
            appearance_xref = page.show_pdf_page(rect, appearance, 0)
            # show_pdf_page imports the Form and adds a display stream. The
            # Form belongs only to the signature annotation, so remove that
            # added display stream while retaining all source content streams.
            page.parent.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{xref} 0 R" for xref in original_contents) + "]")
            # Do not leave the imported signature reachable through unused page
            # resources after its annotation is removed in a later request.
            page.parent.xref_set_key(page.xref, "Resources", original_resources)
            if resource_xref is not None:
                page.parent.update_object(resource_xref, original_resource_object)
            annotation = page.add_stamp_annot(rect)
            annotation.set_rect(rect)
            annotation.set_info(title="ReDOCX PDF Editor", subject="ReDOCX visual signature v1",
                                content=json.dumps({"operation_id": getattr(operation, "operation_id", None), "consent_accepted": True}))
            annotation.set_flags(fitz.PDF_ANNOT_IS_PRINT)
            page.parent.xref_set_key(annotation.xref, "AP", f"<< /N {appearance_xref} 0 R >>")

    def _paint_signature(self, page: fitz.Page, rect: fitz.Rect, operation: Any, *, asset_resolver: Optional[AssetResolver], page_rotation: int = 0) -> None:
        signature_type = _enum_value(getattr(operation, "signature_type", ""))
        if signature_type == "typed":
            typed_name = str(getattr(operation, "typed_name", "") or "").strip()
            if not typed_name:
                raise ValueError("typed signature requires typed_name.")
            self._add_text(page, rect, SimpleNamespace(
                text=typed_name, font_family="Helvetica", font_style="italic",
                font_size=min(144, max(8, rect.height * 0.45)),
                minimum_font_size=4, padding=0, auto_fit=True,
                text_alignment="center", color_hex="#111111",
            ), page_rotation=page_rotation)
            # _add_text already checks fitting, missing glyphs and faithful
            # glyph insertion, including signatures using complex scripts.
            return

        if signature_type == "uploaded_image":
            key = str(getattr(operation, "signature_image_storage_key", "") or "").strip()
            if not key:
                raise ValueError("uploaded_image signature requires signature_image_storage_key.")
            _insert_image_or_svg(page, rect, _resolve_asset_path(key, asset_resolver=asset_resolver), rotation=page_rotation)
            return

        if signature_type == "drawn":
            key = str(getattr(operation, "signature_svg_storage_key", "") or getattr(operation, "signature_image_storage_key", "") or "").strip()
            if not key:
                raise ValueError("drawn signature requires signature_svg_storage_key.")
            _insert_image_or_svg(page, rect, _resolve_asset_path(key, asset_resolver=asset_resolver), rotation=page_rotation)
            return

        raise ValueError(f"Unsupported signature_type: {signature_type}")


# Public convenience API -----------------------------------------------------


def edit_pdf(
    source_path: str | Path,
    *,
    operations: Sequence[Any],
    output_filename: str = "edited-document.pdf",
    generate_preview: bool = True,
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/edit",
    preview_artifacts_dir: str | Path = "artifacts/pdf_tools/preview",
    asset_resolver: Optional[AssetResolver] = None,
    default_font_path: Optional[str | Path] = None,
    owner_user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> EditedPdfArtifact:
    backend = PyMuPDFEditBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        preview_artifacts_dir=preview_artifacts_dir,
        default_font_path=default_font_path,
    )
    return backend.edit(
        source_path=source_path,
        operations=operations,
        output_filename=output_filename,
        generate_preview=generate_preview,
        asset_resolver=asset_resolver,
        owner_user_id=owner_user_id,
        organization_id=organization_id,
    )


# Internal helpers -----------------------------------------------------------


def _operation_value(operation: Any) -> str:
    return _enum_value(getattr(operation, "operation", "")).strip().lower()


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _removal_mode(operation: Any) -> str:
    return _enum_value(getattr(operation, "removal_mode", "whiteout_region")).strip().lower()


def _page_for_operation(pdf: fitz.Document, operation: Any) -> fitz.Page:
    page_number = int(getattr(operation, "page_number", 0))
    if page_number < 1 or page_number > pdf.page_count:
        raise ValueError(f"edit operation page_number {page_number} is outside source PDF page range.")
    return pdf[page_number - 1]


def _operation_rectangle(operation: Any) -> Any:
    rect = getattr(operation, "rectangle", None)
    if rect is None:
        raise ValueError("edit operation requires rectangle.")
    return rect


def _validated_link_url(value: str) -> str:
    """Allow only explicit web and email links in generated PDF annotations."""
    if not value:
        return ""
    if len(value) > 2048 or any(char.isspace() for char in value) or not re.match(r"^(?:https?://|mailto:)", value, re.IGNORECASE):
        raise ValueError("PDF text links must begin with http://, https://, or mailto:.")
    return value


def _normalized_rect_to_page_rect(page: fitz.Page, rectangle: Any) -> fitz.Rect:
    x = float(getattr(rectangle, "x"))
    y = float(getattr(rectangle, "y"))
    width = float(getattr(rectangle, "width"))
    height = float(getattr(rectangle, "height"))
    if not all(math.isfinite(value) for value in (x, y, width, height)) or x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise ValueError("PDF edit rectangle must be normalized and fit within the page.")

    rotated_page_rect = page.rect
    rotated_rect = fitz.Rect(
        rotated_page_rect.x0 + x * rotated_page_rect.width,
        rotated_page_rect.y0 + y * rotated_page_rect.height,
        rotated_page_rect.x0 + (x + width) * rotated_page_rect.width,
        rotated_page_rect.y0 + (y + height) * rotated_page_rect.height,
    )

    # PDF.js reports pointer coordinates in the displayed (rotation-aware)
    # viewport. PyMuPDF edit APIs consume unrotated page coordinates. Convert
    # the displayed rectangle back into the page's unrotated coordinate space.
    if int(getattr(page, "rotation", 0) or 0) % 360:
        return rotated_rect * page.derotation_matrix
    return rotated_rect


def _remove_redocx_annotations(
    page: fitz.Page,
    rect: fitz.Rect,
    *,
    signature_only: bool = False,
) -> int:
    removed = 0
    for annotation in list(page.annots() or []):
        if not annotation.rect.intersects(rect):
            continue
        info = annotation.info or {}
        marker = " ".join(
            str(info.get(key) or "")
            for key in ("title", "subject", "content")
        ).lower()
        if info.get("subject") not in {"ReDOCX edit annotation", "ReDOCX document comment", "ReDOCX visual signature v1"}:
            continue
        if signature_only and not _is_signature_annotation(annotation):
            continue
        page.delete_annot(annotation)
        removed += 1
    return removed


def _is_signature_annotation(annotation: fitz.Annot) -> bool:
    info = annotation.info or {}
    return info.get("title") == "ReDOCX PDF Editor" and info.get("subject") == "ReDOCX visual signature v1"


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")
    return path


def _reject_encrypted_pdf(document: fitz.Document, source_path: Path) -> None:
    # An owner-protected PDF with an empty user password is readable while
    # is_encrypted / needs_pass are false. Detect the original encryption too.
    if (
        bool(getattr(document, "needs_pass", False))
        or bool(getattr(document, "is_encrypted", False))
        or bool((document.metadata or {}).get("encryption"))
        or document.xref_get_key(-1, "Encrypt")[0] != "null"
    ):
        raise ValueError(f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_edited_pdf(path: Path, *, expected_page_count: int,
                       expected_geometry: Optional[list] = None,
                       expected_snapshots: Optional[list] = None,
                       expected_renders: Optional[dict] = None) -> str:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("The edited PDF was not created correctly.")
    try:
        with fitz.open(path) as verified:
            if bool(getattr(verified, "needs_pass", False)):
                raise RuntimeError("The edited PDF unexpectedly requires a password.")
            if int(verified.page_count) != expected_page_count:
                raise RuntimeError(
                    "Edited PDF verification failed because the page count changed."
                )
            for page_number in range(verified.page_count):
                page = verified.load_page(page_number)
                if page.rect.is_empty or page.rect.width <= 0 or page.rect.height <= 0:
                    raise RuntimeError(
                        f"Edited PDF verification failed on page {page_number + 1}."
                    )
                if expected_geometry is not None and _page_geometry(page) != expected_geometry[page_number]:
                    raise RuntimeError("Edited PDF verification failed: page boxes or rotation changed.")
                if expected_snapshots is not None and _page_snapshot(page) != expected_snapshots[page_number]:
                    raise RuntimeError("Edited PDF verification failed: saved content differs from verified edits.")
                if expected_renders is not None and page_number in expected_renders and _render_digest(page) != expected_renders[page_number]:
                    raise RuntimeError("Edited PDF verification failed: saved appearance differs from verified edits.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("The edited PDF could not be reopened for verification.") from exc
    return _sha256_file(path)


def _needs_unicode_shaping(text: str) -> bool:
    return any(
        0x0600 <= ord(ch) <= 0x08FF or 0x0900 <= ord(ch) <= 0x097F
        or 0xA8E0 <= ord(ch) <= 0xA8FF or 0xFB50 <= ord(ch) <= 0xFDFF
        or 0xFE70 <= ord(ch) <= 0xFEFF or 0x10E60 <= ord(ch) <= 0x10E7F
        or 0x1EE00 <= ord(ch) <= 0x1EEFF
        for ch in text
    )


def _pdf_number(value: float) -> str:
    if not math.isfinite(value):
        raise RuntimeError("Unicode text rendering produced an invalid coordinate.")
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _pdf_stream(document: fitz.Document, data: bytes) -> int:
    xref = document.get_new_xref()
    document.update_object(xref, "<< >>")
    document.update_stream(xref, data)
    return xref


def _unicode_cmap(entries: list[tuple[int, str]]) -> bytes:
    # A single original scalar per code avoids font-wide GID ambiguities and
    # the PDF.js multi-character diacritic/advance bug. No normalization.
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
             "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             "/CMapName /ReDOCX-UCS def", "/CMapType 2 def",
             "1 begincodespacerange", "<00> <FF>", "endcodespacerange"]
    for offset in range(0, len(entries), 100):
        batch = entries[offset:offset + 100]
        lines.append(f"{len(batch)} beginbfchar")
        for code, scalar in batch:
            if not 1 <= code <= 255 or len(scalar) != 1:
                raise RuntimeError("Invalid Unicode glyph mapping.")
            lines.append(f"<{code:02X}> <{scalar.encode('utf-16-be').hex().upper()}>")
        lines.append("endbfchar")
    return ("\n".join(lines + ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]) + "\n").encode("ascii")


class _StoryUnicodeCapture(fitz.mupdf.FzDevice2):
    """Capture original scalars before MuPDF's PDF writer loses their mapping."""

    def __init__(self) -> None:
        super().__init__()
        self.use_virtual_fill_text()
        self.rows: list[dict[str, Any]] = []
        self.error: Optional[Exception] = None

    def fill_text(self, ctx, text, ctm, colorspace, color, alpha, params):
        # Exceptions must not escape a SWIG device callback.
        try:
            if any(abs(a - b) > 1e-5 for a, b in zip(
                (ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f), (1, 0, 0, 1, 0, 0)
            )):
                raise RuntimeError("Unsupported Story text transform.")
            raw_span = text.head
            while raw_span:
                span = fitz.mupdf.FzTextSpan(raw_span)
                trm = span.trm()
                size = math.hypot(trm.a, trm.b)
                if size <= 0 or any(abs(a - b) > 1e-5 for a, b in zip(
                    (trm.a, trm.b, trm.c, trm.d), (size, 0, 0, -size)
                )):
                    raise RuntimeError("Unsupported shaped text matrix.")
                for index in range(raw_span.len):
                    item = span.items(index)
                    if item.gid >= 0:
                        scalar = chr(item.ucs) if item.ucs >= 0 else ""
                        if item.gid == 0 and scalar and not scalar.isspace() and unicodedata.category(scalar) != "Cf":
                            raise ValueError("Text contains a character for which no supported font glyph is available.")
                        box = fitz.mupdf.fz_bound_glyph(span.font(), item.gid, fitz.mupdf.FzMatrix(1000, 0, 0, 1000, 0, 0))
                        self.rows.append({"font": fitz.mupdf.fz_font_name(span.font()),
                                          "gid": item.gid, "text": scalar, "x": item.x, "y": item.y,
                                          "advance": item.adv, "size": size,
                                          "box": (box.x0, box.y0, box.x1, box.y1)})
                    elif item.ucs >= 0:
                        if not self.rows:
                            raise RuntimeError("Unicode continuation has no shaped glyph.")
                        self.rows[-1]["text"] += chr(item.ucs)
                raw_span = raw_span.next
        except Exception as exc:
            self.error = exc


def _unicode_clusters(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    clusters: list[dict[str, Any]] = []
    for row in rows:
        previous = clusters[-1] if clusters else None
        first = row["text"][:1]
        last = previous["text"].rstrip("\u200d\u200c")[-1:] if previous else ""
        continuation = (first and unicodedata.category(first).startswith("M")) or last == "\u094d" or (row["advance"] == 0 and row["text"] != " ")
        same_line = previous and abs(previous["rows"][0]["y"] - row["y"]) < row["size"] * 0.75
        if previous and same_line and continuation:
            previous["rows"].append(row)
            previous["text"] += row["text"]
        else:
            clusters.append({"rows": [row], "text": row["text"]})
    lines: list[list[dict[str, Any]]] = []
    for cluster in clusters:
        glyphs = cluster["rows"]
        cluster.update(x=min(row["x"] for row in glyphs), y=glyphs[0]["y"], size=glyphs[0]["size"])
        if any(abs(row["size"] - cluster["size"]) > 1e-5 for row in glyphs):
            raise RuntimeError("A shaped cluster contains inconsistent font sizes.")
        cluster["width"] = max(row["x"] + row["advance"] * row["size"] for row in glyphs) - cluster["x"]
        if not cluster["text"]:
            raise RuntimeError("A shaped glyph has no original Unicode mapping.")
        line = next((line for line in lines if abs(line[0]["y"] - cluster["y"]) < 0.05 * cluster["size"]), None)
        if line is None:
            lines.append([cluster])
        else:
            line.append(cluster)
    return [sorted(line, key=lambda cluster: cluster["x"]) for line in lines]


def _unicode_font(document: fitz.Document, clusters: list[dict[str, Any]], name: str) -> tuple[int, list[float], list[list[int]]]:
    procs, widths, entries, groups = [], [], [], []
    font_box = [0.0, 0.0, 0.0, 0.0]
    for cluster in clusters:
        # Store visual order within an Arabic ligature; reader bidi restores
        # logical order. Devanagari keeps original logical scalar order.
        text = cluster["text"][::-1] if "Arabic" in cluster["rows"][0]["font"] else cluster["text"]
        moving = [index for index, scalar in enumerate(text) if not unicodedata.category(scalar).startswith("M")]
        advance = cluster["width"] / cluster["size"] * 1000 / len(moving) if moving else 0.0
        group, pen = [], 0.0
        for index, scalar in enumerate(text):
            code = len(widths) + 1
            if code > 255:
                raise RuntimeError("A Unicode font exceeded its byte-code capacity.")
            group.append(code)
            entries.append((code, scalar))
            width = float(round(advance)) if index in moving else 0.0
            widths.append(width)
            box = [math.inf, math.inf, -math.inf, -math.inf]
            for row in cluster["rows"]:
                dx = (row["x"] - cluster["x"]) / cluster["size"] * 1000 - pen
                dy = -(row["y"] - cluster["y"]) / cluster["size"] * 1000
                box = [min(box[0], row["box"][0] + dx), min(box[1], row["box"][1] + dy),
                       max(box[2], row["box"][2] + dx), max(box[3], row["box"][3] + dy)]
            font_box = [min(font_box[0], box[0]), min(font_box[1], box[1]), max(font_box[2], box[2]), max(font_box[3], box[3])]
            operators = [f"{_pdf_number(width)} 0 {' '.join(_pdf_number(v) for v in box)} d1", "q"]
            if index == (moving[0] if moving else 0):
                operators.append("BT")
                for row in cluster["rows"]:
                    dx = (row["x"] - cluster["x"]) / cluster["size"] * 1000 - pen
                    dy = -(row["y"] - cluster["y"]) / cluster["size"] * 1000
                    operators.extend([f"/S{row['xref']} 1000 Tf",
                                      f"1 0 0 1 {_pdf_number(dx)} {_pdf_number(dy)} Tm <{row['gid']:04X}> Tj"])
                operators.append("ET")
            operators.append("Q")
            procs.append((code, _pdf_stream(document, ("\n".join(operators) + "\n").encode("ascii"))))
            pen += width
        groups.append(group)
    cmap = _pdf_stream(document, _unicode_cmap(entries))
    descriptor = document.get_new_xref()
    box_string = " ".join(_pdf_number(v) for v in font_box)
    document.update_object(descriptor, f"<< /Type /FontDescriptor /FontName /{name} /Flags 32 /FontBBox [{box_string}] /ItalicAngle 0 /Ascent 1000 /Descent -500 /CapHeight 1000 /StemV 80 >>")
    xref = document.get_new_xref()
    document.update_object(xref,
        f"<< /Type /Font /Subtype /Type3 /Name /{name} /FontDescriptor {descriptor} 0 R "
        f"/FontBBox [{box_string}] /FontMatrix [.001 0 0 .001 0 0] /FirstChar 1 /LastChar {len(widths)} "
        f"/Widths [{' '.join(_pdf_number(v) for v in widths)}] "
        "/CharProcs << " + " ".join(f"/c{code} {proc} 0 R" for code, proc in procs) + " >> "
        "/Encoding << /Type /Encoding /Differences [1 " + " ".join(f"/c{code}" for code, _ in procs) + "] >> "
        "/Resources << /Font << " + " ".join(f"/S{x} {x} 0 R" for x in sorted({row["xref"] for cluster in clusters for row in cluster["rows"]})) + " >> >> "
        f"/ToUnicode {cmap} 0 R /ReDOCXUnicode 2 /ReDOCXGroups [" + " ".join("[" + " ".join(map(str, group)) + "]" for group in groups) + "] >>")
    return xref, widths, groups


def _pdf_string_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return bytes(value)
    if isinstance(value, str):
        return value.original_bytes
    raise RuntimeError("Unsupported PDF text-string representation.")


def _unicode_direction_font(document: fitz.Document) -> int:
    """Non-content direction primer for MuPDF's initial-NSM bidi heuristic.

    This paints nothing. The caller marks it as an artifact with empty
    ActualText; PDF.js also discards the Arabic Letter Mark as a format mark.
    No user text is duplicated in an ActualText replacement.
    """
    proc = _pdf_stream(document, b"0 0 0 0 0 0 d1\n")
    cmap = _pdf_stream(document, _unicode_cmap([(1, "\u061c")]))
    xref = document.get_new_xref()
    document.update_object(xref,
        "<< /Type /Font /Subtype /Type3 /Name /ReDOCX-Bidi "
        "/FontBBox [0 0 0 0] /FontMatrix [.001 0 0 .001 0 0] "
        "/FirstChar 1 /LastChar 1 /Widths [0] "
        "/Encoding << /Type /Encoding /Differences [1 /dir] >> "
        f"/CharProcs << /dir {proc} 0 R >> /Resources << >> /ToUnicode {cmap} 0 R >>")
    return xref


def _repair_story_unicode(document: fitz.Document, rows: list[dict[str, Any]], identity: str) -> None:
    if document.page_count != 1:
        raise RuntimeError("Unicode insertion must render to one isolated page.")
    reader = PdfReader(io.BytesIO(document.tobytes()))
    source_page = reader.pages[0]
    content = ContentStream(source_page.get_contents(), reader)
    page = document[0]
    fonts = {"/" + font[4]: (font[0], font[3]) for font in page.get_fonts()}
    resources_kind, resources_value = document.xref_get_key(page.xref, "Resources")
    if resources_kind != "xref":
        raise RuntimeError("Story page resources are not indirect.")
    resources = int(resources_value.split()[0])
    operations, glyphs = [], []
    unicode_resources: dict[str, int] = {}
    cursor, ordinal = 0, 0
    direction_font = None
    current_font = None
    for operands, operator in content.operations:
        if operator == b"Tf":
            current_font = str(operands[0])
            continue
        if operator in {b"Tm", b"Td", b"TD", b"T*"}:
            continue
        if operator in {b"Tj", b"TJ"}:
            strings = [item for item in operands[0] if isinstance(item, (bytes, str))] if operator == b"TJ" else operands
            encoded = b"".join(_pdf_string_bytes(item) for item in strings)
            if len(encoded) % 2 or current_font not in fonts:
                raise RuntimeError("Unexpected native Story font encoding.")
            gids = [int.from_bytes(encoded[index:index + 2], "big") for index in range(0, len(encoded), 2)]
            selected = rows[cursor:cursor + len(gids)]
            cursor += len(gids)
            if len(selected) != len(gids) or any(row["font"] != fonts[current_font][1] or row["gid"] != gid for row, gid in zip(selected, gids)):
                raise RuntimeError("Original Unicode and native shaped glyphs do not match.")
            for row in selected:
                row["xref"] = fonts[current_font][0]
            glyphs.extend(selected)
            continue
        if operator == b"ET" and glyphs:
            for line in _unicode_clusters(glyphs):
                batches: list[list[dict[str, Any]]] = [[]]
                count = 0
                for cluster in line:
                    if len(cluster["text"]) > 255:
                        raise ValueError("A shaped character cluster is too large for safe PDF insertion.")
                    if count + len(cluster["text"]) > 255:
                        batches.append([])
                        count = 0
                    batches[-1].append(cluster)
                    count += len(cluster["text"])
                for batch in batches:
                    ordinal += 1
                    font_name = f"ReDOCX-U2-{identity[:16]}-{ordinal}"
                    if len(font_name) > 31:
                        raise ValueError("Unicode text exceeds the safe font-bucket limit.")
                    font, widths, codes = _unicode_font(document, batch, font_name)
                    resource = f"/RU{ordinal}"
                    unicode_resources[resource] = font
                    size = batch[0]["size"]
                    if any("Arabic" in c["rows"][0]["font"] for c in batch):
                        if direction_font is None:
                            direction_font = _unicode_direction_font(document)
                            unicode_resources["/UB"] = direction_font
                        # Place the non-painting artifact outside the text box
                        # so content removal cannot erase its direction state.
                        operations.extend([
                            ([NameObject("/Artifact"), DictionaryObject({NameObject("/ActualText"): ByteStringObject(b"")})], b"BDC"),
                            ([NameObject("/UB"), FloatObject(size)], b"Tf"),
                            ([FloatObject(v) for v in (1, 0, 0, -1, 0, -1000)], b"Tm"),
                            ([ByteStringObject(b"\x01")], b"Tj"), ([], b"EMC"),
                        ])
                    array = ArrayObject()
                    x = batch[0]["x"]
                    for cluster, group in zip(batch, codes):
                        if abs(cluster["size"] - size) > 1e-5:
                            raise RuntimeError("Inconsistent Unicode text size within a line.")
                        adjustment = (x - cluster["x"]) / size * 1000
                        if abs(adjustment) > 1e-7:
                            array.append(FloatObject(adjustment))
                        pen = 0.0
                        for code in group:
                            if widths[code - 1] == 0:
                                # Keep Devanagari marks just before the next
                                # base's cell, including Type3 metric rounding.
                                # Arabic uses visual scalar order and identical
                                # mark/base origins; nudging would add PDF.js gaps.
                                following_marks = sum(widths[other - 1] == 0 for other in group if other >= code)
                                offset = (0.0 if "Arabic" in cluster["rows"][0]["font"] else
                                          max(0, pen - cluster["width"] / size * 1000) + following_marks * 0.5)
                                array.extend([FloatObject(offset), ByteStringObject(bytes([code])), FloatObject(-offset)])
                            else:
                                array.append(ByteStringObject(bytes([code])))
                            pen += widths[code - 1]
                        x = cluster["x"] + pen * size / 1000
                    operations.extend([
                        ([NameObject(resource), FloatObject(size)], b"Tf"),
                        ([FloatObject(v) for v in (1, 0, 0, -1, batch[0]["x"], batch[0]["y"])], b"Tm"),
                        ([array], b"TJ"),
                    ])
            glyphs = []
        operations.append((operands, operator))
    if cursor != len(rows) or glyphs:
        raise RuntimeError("Not all native shaped glyphs received a Unicode mapping.")
    content.operations = operations
    xrefs = page.get_contents()
    if len(xrefs) != 1:
        raise RuntimeError("Unexpected Story page content structure.")
    document.update_stream(xrefs[0], content.get_data())
    # Native fonts are now reachable through the owned glyph procedures.
    # Do not retain unused parent references after all their glyphs are removed.
    # Remove unused entries rather than leaving null font references, which
    # strict readers diagnose as malformed font resources.
    document.xref_set_key(resources, "Font", "<< " + " ".join(
        f"{name} {xref} 0 R" for name, xref in unicode_resources.items()
    ) + " >>")


class _UnicodeStory(fitz.Story):
    def __init__(self, html_text: str, css: str, identity: str, archive=None) -> None:
        # Match the insert_htmlbox string-input body margin exactly.
        super().__init__(html_text, user_css="body {margin:1px;}" + css, archive=archive)
        self.identity = identity

    def write_with_links(self, rectfn, positionfn=None, pagefn=None):
        capture = _StoryUnicodeCapture()
        fitz.mupdf.fz_draw_story(self.this, capture, fitz.mupdf.FzMatrix(1, 0, 0, 1, 0, 0))
        if capture.error is not None:
            raise capture.error
        self.reset()
        document = super().write_with_links(rectfn, positionfn, pagefn)
        try:
            _repair_story_unicode(document, capture.rows, self.identity)
            return document
        except Exception:
            document.close()
            raise


def _unicode_fonts(page: fitz.Page) -> dict[str, dict[str, Any]]:
    fonts = {}
    for xref, _, subtype, name, *_ in page.get_fonts(full=True):
        if not name.startswith("ReDOCX-U2-") or subtype != "Type3":
            continue
        marker = page.parent.xref_get_key(xref, "ReDOCXUnicode")
        if marker != ("int", "2"):
            continue
        kind, value = page.parent.xref_get_key(xref, "ReDOCXGroups")
        if kind != "array" or not re.fullmatch(r"\[\s*(?:\[\s*(?:\d+\s*)+\]\s*)*\]", value):
            raise ValueError("The PDF contains invalid ReDOCX Unicode cluster metadata.")
        groups = [tuple(map(int, group.split())) for group in re.findall(r"\[([^\[\]]+)\]", value)]
        codes = [code for group in groups for code in group]
        if len(codes) != len(set(codes)) or any(not 1 <= code <= 255 for code in codes):
            raise ValueError("The PDF contains invalid ReDOCX Unicode character codes.")
        if name in fonts and fonts[name]["xref"] != xref:
            raise ValueError("The PDF contains ambiguous ReDOCX Unicode font identities.")
        fonts[name] = {"xref": xref, "groups": groups, "codes": {code: group for group in groups for code in group}}
    return fonts


def _unicode_cluster_geometry(page: fitz.Page, fonts: dict[str, dict[str, Any]]) -> tuple[dict, dict]:
    boxes, characters = {}, {}
    for span in page.get_texttrace():
        name = span["font"]
        if name not in fonts:
            continue
        for scalar, code, origin, bounds in span["chars"]:
            group = fonts[name]["codes"].get(code)
            if group is None:
                raise ValueError("Unicode text references an unmapped ReDOCX character.")
            key = (name, group)
            rect = fitz.Rect(bounds)
            boxes[key] = (boxes[key] | rect) if key in boxes else rect
            characters[(name, chr(scalar), tuple(round(float(v), 3) for v in origin))] = key
    raw_boxes = {}
    for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    key = characters.get((span["font"], char["c"], tuple(round(float(v), 3) for v in char["origin"])))
                    if key is not None:
                        rect = fitz.Rect(char["bbox"])
                        raw_boxes[key] = (raw_boxes[key] | rect) if key in raw_boxes else rect
    boxes.update(raw_boxes)
    return boxes, characters


def _strip_unicode_clusters(page: fitz.Page, regions: list[fitz.Rect]) -> None:
    """Remove a whole shaped cluster, including every auxiliary Unicode slot.

    Text positioning is retained as TJ advances. The deleted CharProcs and
    Unicode mappings are unlinked; garbage collection removes their streams.
    No whole-text ActualText or recoverable full-text metadata is written.
    """
    fonts = _unicode_fonts(page)
    if not fonts:
        return
    boxes, _ = _unicode_cluster_geometry(page, fonts)
    selected: dict[str, set[int]] = {}
    for (name, group), box in boxes.items():
        if any(box.intersects(region) for region in regions):
            selected.setdefault(name, set()).update(group)
    if not selected:
        return
    # Generated codes are unique to one placement. Refuse shared/repeated
    # placements rather than modifying text outside this user's selection.
    occurrences = Counter()
    for document_page in page.parent:
        for span in document_page.get_texttrace():
            if span["font"] in selected:
                for _, code, _, _ in span["chars"]:
                    if code in selected[span["font"]]:
                        occurrences[(span["font"], code)] += 1
    if any(occurrences[(name, code)] != 1 for name, codes in selected.items() for code in codes):
        raise ValueError("Cannot safely remove a shared Unicode text cluster. Select a PDF with independent text placements.")
    reader = PdfReader(io.BytesIO(page.parent.tobytes()))
    targets = {fonts[name]["xref"]: codes for name, codes in selected.items()}
    # Page text traces exclude annotation appearances. A reused owned font in
    # an appearance must not be pruned while deleting independent page text.
    appearance_resources_seen = set()

    def check_appearance(stream):
        stream = stream.get_object()
        reference = getattr(stream, "indirect_reference", None)
        if reference and reference.idnum in appearance_resources_seen:
            return
        if reference:
            appearance_resources_seen.add(reference.idnum)
        if not hasattr(stream, "get_data"):
            if isinstance(stream, DictionaryObject):
                for child in stream.values():
                    check_appearance(child)
            return
        resources = stream.get("/Resources", {})
        for font in resources.get("/Font", {}).values():
            reference = getattr(font, "indirect_reference", None)
            if reference and reference.idnum in targets:
                raise ValueError("Cannot safely remove Unicode text sharing its font with an annotation appearance.")
        for child in resources.get("/XObject", {}).values():
            child = child.get_object()
            if child.get("/Subtype") == "/Form":
                check_appearance(child)

    for document_page in reader.pages:
        for annotation in document_page.get("/Annots", []):
            for appearance in annotation.get_object().get("/AP", {}).values():
                check_appearance(appearance)
    visited, removed = set(), Counter()

    def rewrite(stream, resources, xref=None):
        if xref is not None and xref in visited:
            return
        if xref is not None:
            visited.add(xref)
        content = ContentStream(stream, reader)
        resource_fonts = resources.get("/Font", {})
        font_xref, stack, operations, changed = None, [], [], False
        spacing, word_spacing = 0.0, 0.0
        for operands, operator in content.operations:
            if operator == b"q":
                stack.append((font_xref, spacing, word_spacing))
            elif operator == b"Q" and stack:
                font_xref, spacing, word_spacing = stack.pop()
            elif operator == b"Tf":
                font = resource_fonts.get(operands[0])
                font_xref = font.indirect_reference.idnum if font is not None and font.indirect_reference else None
            elif operator == b"Tc":
                spacing = float(operands[0])
            elif operator == b"Tw":
                word_spacing = float(operands[0])
            elif operator == b"Do":
                form = resources.get("/XObject", {}).get(operands[0])
                if form is not None:
                    form = form.get_object()
                    if form.get("/Subtype") == "/Form":
                        rewrite(form, form.get("/Resources", resources), form.indirect_reference.idnum)
            if operator in {b"Tj", b"TJ"} and font_xref in targets:
                if spacing or word_spacing:
                    raise ValueError("Cannot safely remove Unicode text with modified character spacing.")
                font = reader.get_object(font_xref)
                first = int(font["/FirstChar"])
                widths = font["/Widths"]
                array = ArrayObject()
                original = operands[0] if operator == b"TJ" else [operands[0]]
                for item in original:
                    if not isinstance(item, (str, bytes)):
                        array.append(item)
                        continue
                    keep = bytearray()
                    for code in _pdf_string_bytes(item):
                        if code in targets[font_xref]:
                            if keep:
                                array.append(ByteStringObject(bytes(keep)))
                                keep.clear()
                            array.append(FloatObject(-float(widths[code - first])))
                            removed[(font_xref, code)] += 1
                            changed = True
                        else:
                            keep.append(code)
                    if keep:
                        array.append(ByteStringObject(bytes(keep)))
                operations.append(([array], b"TJ"))
            else:
                operations.append((operands, operator))
        if changed:
            content.operations = operations
            if xref is None:
                new_xref = _pdf_stream(page.parent, content.get_data())
                page.set_contents(new_xref)
            else:
                page.parent.update_stream(xref, content.get_data())

    root = reader.pages[page.number]
    rewrite(root.get_contents(), root["/Resources"])
    if any(removed[(xref, code)] != 1 for xref, codes in targets.items() for code in codes):
        raise RuntimeError("Unicode cluster removal did not remove exactly the selected characters.")
    for name, codes in selected.items():
        xref = fonts[name]["xref"]
        font = reader.get_object(xref)
        for code in codes:
            del font["/CharProcs"][NameObject(f"/c{code}")]
        remaining = [group for group in fonts[name]["groups"] if not codes.intersection(group)]
        font[NameObject("/ReDOCXGroups")] = ArrayObject([ArrayObject([NumberObject(code) for code in group]) for group in remaining])
        first, last = int(font["/FirstChar"]), int(font["/LastChar"])
        for code in codes:
            font["/Widths"][code - first] = NumberObject(0)
        font["/Encoding"][NameObject("/Differences")] = ArrayObject([NumberObject(first)] + [NameObject("/.notdef" if code in codes else f"/c{code}") for code in range(first, last + 1)])
        cmap = font["/ToUnicode"]
        mappings = [(int(code, 16), bytes.fromhex(scalar.decode("ascii")).decode("utf-16-be"))
                    for code, scalar in re.findall(rb"<([0-9A-Fa-f]{2})>\s*<([0-9A-Fa-f]{4,8})>", cmap.get_data())
                    if int(code, 16) not in codes]
        if len(mappings) != sum(map(len, remaining)):
            raise ValueError("Cannot safely prune malformed Unicode font mappings.")
        page.parent.update_stream(cmap.indirect_reference.idnum, _unicode_cmap(mappings))
        if not remaining:
            font[NameObject("/Resources")] = DictionaryObject()
        serialized = io.BytesIO()
        font.write_to_stream(serialized)
        page.parent.update_object(xref, serialized.getvalue().decode("ascii"))


def _canonical_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _text_glyphs(page: fitz.Page, *, reject_missing: bool = False) -> list[tuple]:
    """Fingerprint rendered glyphs without relying on a font's Unicode map.

    Complex-script glyphs can represent several characters or use reordered
    combining marks. A nonzero glyph id with an ambiguous Unicode mapping is
    valid; glyph zero (.notdef) must still fail closed for visible text.
    """
    glyphs = []
    for span in page.get_texttrace():
        style = (
            str(span["font"]), int(span["flags"]), int(span["type"]),
            round(float(span["size"]), 3), round(float(span["opacity"]), 3),
            tuple(round(float(v), 3) for v in span["dir"]),
            tuple(round(float(v), 3) for v in span["color"]),
        )
        for codepoint, glyph_id, origin, bounds in span["chars"]:
            if reject_missing and glyph_id == 0:
                character = chr(codepoint)
                if not character.isspace() and unicodedata.category(character) != "Cf":
                    raise ValueError("Text contains a character for which no supported font glyph is available.")
            glyphs.append((
                style, int(glyph_id), int(codepoint),
                tuple(round(float(v), 3) for v in origin),
                tuple(round(float(v), 3) for v in bounds),
            ))
    return glyphs


def _copy_strikethrough_paths(
    page: fitz.Page, *, html_text: str, css: str, content_rect: fitz.Rect,
    rotation: int, scale_low: float, opacity: float,
    expected_glyphs: list[tuple], unicode_identity: Optional[str] = None, archive=None,
) -> None:
    """Copy the renderer's strike paths without duplicating any text glyphs."""
    with fitz.open() as decoration:
        strike_page = decoration.new_page(width=page.rect.width, height=page.rect.height)
        story = _UnicodeStory(html_text, css, unicode_identity, archive=archive) if unicode_identity else html_text
        spare_height, _ = strike_page.insert_htmlbox(
            content_rect, story, css=css, scale_low=scale_low,
            rotate=rotation, opacity=opacity, overlay=True, archive=archive,
        )
        if spare_height < 0 or _text_glyphs(strike_page, reject_missing=True) != expected_glyphs:
            raise RuntimeError("Text decoration verification failed: strikethrough changed the fitted text layout.")
        paths_copied = 0
        for drawing in strike_page.get_drawings():
            if drawing["type"] != "s" or drawing.get("fill") is not None:
                continue
            for item in drawing["items"]:
                if item[0] != "l":
                    raise RuntimeError("Text decoration verification failed: unsupported strike path.")
                page.draw_line(
                    item[1], item[2], color=drawing["color"],
                    width=drawing["width"],
                    stroke_opacity=drawing.get("stroke_opacity", opacity),
                    overlay=True,
                )
                paths_copied += 1
        if paths_copied == 0:
            raise RuntimeError("Text decoration verification failed: no strikethrough was rendered.")


def _text_characters(page: fitz.Page) -> list[tuple[str, tuple]]:
    fonts = _unicode_fonts(page)
    boxes, positions = _unicode_cluster_geometry(page, fonts) if fonts else ({}, {})
    characters = []
    for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    origin = tuple(round(float(v), 3) for v in char["origin"])
                    key = positions.get((span["font"], char["c"], origin))
                    bounds = boxes[key] if key is not None else char["bbox"]
                    characters.append((char["c"], tuple(round(float(v), 3) for v in bounds)))
    return characters


def _image_instances(page: fitz.Page) -> list[tuple]:
    return [(tuple(round(float(value), 3) for value in image["bbox"]), bytes(image["digest"]).hex())
            for image in page.get_textpage(flags=fitz.TEXT_PRESERVE_IMAGES).extractIMGINFO(hashes=True)]


def _page_geometry(page: fitz.Page) -> tuple:
    return (tuple(page.mediabox), tuple(page.cropbox), int(page.rotation))


def _stable_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, (fitz.Rect, fitz.Point, fitz.Quad)):
        return [_stable_value(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {key: _stable_value(item) for key, item in value.items() if key not in {"xref", "id"}}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return value


def _page_snapshot(page: fitz.Page) -> dict:
    # Xrefs can be renumbered by garbage collection; compare semantic identity.
    widgets = sorted([(widget.field_name, widget.field_type, _stable_value(tuple(widget.rect)),
                       str(widget.field_value), widget.field_flags) for widget in (page.widgets() or [])], key=repr)
    annotations = [_stable_value({"type": annotation.type, "rect": tuple(annotation.rect),
                                 "info": annotation.info, "opacity": annotation.opacity,
                                 "colors": annotation.colors, "vertices": annotation.vertices})
                   for annotation in (page.annots() or [])]
    return {"text": _text_characters(page), "images": _image_instances(page),
            "drawings": _stable_value(page.get_drawings()), "widgets": widgets,
            "annotations": annotations, "links": _stable_value(page.get_links())}


def _render_digest(page: fitz.Page) -> str:
    # Bound memory for hostile / abnormally large page dimensions.
    if page.rect.width * page.rect.height > MAX_EDIT_IMAGE_PIXELS:
        raise ValueError("PDF page is too large to verify safely.")
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=True, annots=True)
    return hashlib.sha256(pixmap.samples).hexdigest()


def _guard_destructive_region(page: fitz.Page, rect: fitz.Rect, *, include_annotations: bool = False) -> None:
    if any(annotation.type[0] == fitz.PDF_ANNOT_REDACT for annotation in (page.annots() or [])):
        raise ValueError("Resolve existing pending redaction annotations before editing this page.")
    if any(widget.rect.intersects(rect) for widget in (page.widgets() or [])):
        raise ValueError("Content removal overlaps a form field. Move the selection to preserve the form.")
    if any(fitz.Rect(link["from"]).intersects(rect) for link in page.get_links()):
        raise ValueError("Content removal overlaps a link annotation. Select content without links.")
    if include_annotations and any(annotation.rect.intersects(rect) for annotation in (page.annots() or [])):
        raise ValueError("Whiteout overlaps an annotation. Remove the annotation explicitly first.")


def _has_certificate_signature(pdf: fitz.Document) -> bool:
    # Include signatures not reachable as visible widgets and catalog DocMDP.
    if pdf.xref_get_key(pdf.pdf_catalog(), "Perms")[0] != "null":
        return True
    for xref in range(1, pdf.xref_length()):
        kind, value = pdf.xref_get_key(xref, "ByteRange")
        if kind == "array" and value not in {"[]", "[0 0 0 0]"}:
            return True
    return False


def _hex_to_rgb01(value: str) -> tuple[float, float, float]:
    raw = value.strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", raw):
        raise ValueError(f"Invalid hex color: {value}")
    return (
        int(raw[1:3], 16) / 255,
        int(raw[3:5], 16) / 255,
        int(raw[5:7], 16) / 255,
    )


def _optional_hex_to_rgb01(value: Any) -> Optional[tuple[float, float, float]]:
    if value is None:
        return None
    raw = str(value).strip()
    return _hex_to_rgb01(raw) if raw else None


def _env_font_path() -> Optional[Path]:
    configured = os.getenv("PDF_TOOLS_FONT_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser().resolve()
    return path if path.exists() else None


def _resolve_font(
    font_family: str,
    default_font_path: Optional[Path],
    *,
    bold: bool = False,
    italic: bool = False,
) -> tuple[str, Optional[Path]]:
    # PyMuPDF built-in aliases.
    normalized = font_family.strip().lower()
    family = {
        "arial": "helvetica",
        "sans-serif": "helvetica",
        "times": "times-roman",
        "times new roman": "times-roman",
        "serif": "times-roman",
        "monospace": "courier",
        "courier new": "courier",
    }.get(normalized, normalized)
    builtin = {
        "helvetica": {
            (False, False): "helv",
            (True, False): "hebo",
            (False, True): "heit",
            (True, True): "hebi",
        },
        "helvetica-oblique": {
            (False, False): "heit",
            (True, False): "hebi",
            (False, True): "heit",
            (True, True): "hebi",
        },
        "times-roman": {
            (False, False): "tiro",
            (True, False): "tibo",
            (False, True): "tiit",
            (True, True): "tibi",
        },
        "courier": {
            (False, False): "cour",
            (True, False): "cobo",
            (False, True): "coit",
            (True, True): "cobi",
        },
    }
    if family not in builtin and default_font_path is not None and default_font_path.exists():
        return "redocxfont", default_font_path
    if family not in builtin:
        return "redocxfont", None
    return builtin[family][(bold, italic)], None


def _css_font_family(font_family: str) -> str:
    normalized = font_family.strip().lower()
    if normalized in {"times", "times-roman", "times new roman", "serif"}:
        return '"Times New Roman", Times, serif'
    if normalized in {"courier", "courier new", "monospace"}:
        return '"Courier New", Courier, monospace'
    return "Helvetica, Arial, sans-serif"


def _resolve_asset_path(key: str, *, asset_resolver: Optional[AssetResolver]) -> Path:
    if asset_resolver is not None:
        resolved = Path(asset_resolver(key)).expanduser().resolve()
    else:
        resolved = Path(key).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"PDF edit asset not found: {key}")
    return resolved


def _insert_image_or_svg(
    page: fitz.Page,
    rect: fitz.Rect,
    path: Path,
    *,
    keep_proportion: bool = True,
    rotation: int = 0,
    opacity: float = 1.0,
) -> None:
    temporary_paths: list[Path] = []
    try:
        if path.stat().st_size <= 0 or path.stat().st_size > MAX_EDIT_ASSET_BYTES:
            raise ValueError("PDF edit image is empty or exceeds the 25 MB safety limit.")
        suffix = path.suffix.lower()
        if suffix == ".svg":
            path = _render_svg_file_to_temp_png(path)
            temporary_paths.append(path)
        elif suffix == ".webp":
            _validate_raster_asset(path)
            path = _convert_image_to_temp_png(path)
            temporary_paths.append(path)
        elif suffix in {".png", ".jpg", ".jpeg"}:
            _validate_raster_asset(path)
        else:
            raise ValueError(
                "PDF edit images must be PNG, JPEG, WebP, or a safe SVG."
            )
        if opacity < 0.999:
            path = _apply_image_opacity_to_temp_png(path, opacity)
            temporary_paths.append(path)
        page.insert_image(
            rect,
            filename=str(path),
            keep_proportion=keep_proportion,
            rotate=rotation,
            overlay=True,
        )
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def _convert_image_to_temp_png(path: Path) -> Path:
    out = Path(os.getenv("TMPDIR", "/tmp")) / f"redocx-{os.urandom(4).hex()}.png"
    with Image.open(path) as image:
        image.convert("RGBA").save(out)
    return out


def _validate_raster_asset(path: Path) -> None:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_EDIT_IMAGE_PIXELS:
                raise ValueError(
                    "PDF edit image dimensions are invalid or exceed the pixel safety limit."
                )
            image.verify()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("PDF edit image is corrupt or uses an unsupported encoding.") from exc


def _apply_image_opacity_to_temp_png(path: Path, opacity: float) -> Path:
    out = Path(os.getenv("TMPDIR", "/tmp")) / f"redocx-opacity-{os.urandom(4).hex()}.png"
    with Image.open(path) as source:
        image = source.convert("RGBA")
        alpha = image.getchannel("A").point(
            lambda value: max(0, min(255, round(value * opacity)))
        )
        image.putalpha(alpha)
        image.save(out)
    return out


def _render_svg_file_to_temp_png(path: Path) -> Path:
    try:
        svg_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("PDF edit SVG must be valid UTF-8 text.") from exc
    return _render_svg_text_to_temp_png(svg_text)


def _render_svg_text_to_temp_png(svg_text: str) -> Path:
    if not svg_text or len(svg_text) > MAX_SVG_CHARACTERS:
        raise ValueError("PDF edit SVG is empty or exceeds the safety limit.")
    lowered = svg_text.lower()
    if any(
        fragment in lowered
        for fragment in ("<!doctype", "<!entity", "<script", "<foreignobject", "@import")
    ):
        raise ValueError(
            "PDF edit SVG contains an active or externally loadable construct."
        )
    if re.search(r"(?:href|xlink:href)\s*=\s*[\"'](?!#|data:image/)", svg_text, re.IGNORECASE):
        raise ValueError("PDF edit SVG must not reference external resources.")
    if re.search(r"url\(\s*[\"']?(?!#|data:image/)", svg_text, re.IGNORECASE):
        raise ValueError("PDF edit SVG must not load an external URL.")
    out = Path(os.getenv("TMPDIR", "/tmp")) / f"redocx-svg-{os.urandom(4).hex()}.png"
    try:
        import cairosvg  # type: ignore
    except Exception as exc:
        raise RuntimeError("SVG signatures/drawings require cairosvg or a pre-rendered PNG asset.") from exc
    cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), write_to=str(out))
    return out


def _draw_strokes(
    page: fitz.Page,
    rect: fitz.Rect,
    strokes: Any,
    *,
    color: tuple[float, float, float],
    width: float,
) -> None:
    """Draw strokes shaped like [[{'x':0,'y':0}, ...], ...] inside rect."""
    if isinstance(strokes, dict):
        strokes = strokes.get("strokes", [])
    if not isinstance(strokes, list):
        raise ValueError("strokes JSON must be a list or {'strokes': list}.")

    segments_drawn = 0
    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        points = [_point_in_rect(rect, item) for item in stroke]
        for start, end in zip(points, points[1:]):
            page.draw_line(start, end, color=color, width=width, overlay=True)
            segments_drawn += 1

    if segments_drawn == 0:
        raise ValueError("strokes JSON must contain at least one drawable stroke.")


def _draw_arrow_head(
    page: fitz.Page,
    *,
    start: fitz.Point,
    end: fitz.Point,
    color: tuple[float, float, float],
    width: float,
    opacity: float,
) -> None:
    delta_x = end.x - start.x
    delta_y = end.y - start.y
    line_length = math.hypot(delta_x, delta_y)
    if line_length <= 0:
        return
    angle = math.atan2(delta_y, delta_x)
    head_length = min(max(width * 4.0, 6.0), line_length * 0.35)
    spread = math.radians(28)
    left = fitz.Point(
        end.x - head_length * math.cos(angle - spread),
        end.y - head_length * math.sin(angle - spread),
    )
    right = fitz.Point(
        end.x - head_length * math.cos(angle + spread),
        end.y - head_length * math.sin(angle + spread),
    )
    kwargs = {
        "color": color,
        "width": width,
        "overlay": True,
    }
    try:
        page.draw_line(end, left, stroke_opacity=opacity, **kwargs)
        page.draw_line(end, right, stroke_opacity=opacity, **kwargs)
    except TypeError:  # Compatibility with older PyMuPDF releases.
        page.draw_line(end, left, **kwargs)
        page.draw_line(end, right, **kwargs)


def _point_in_rect(rect: fitz.Rect, item: Any) -> fitz.Point:
    if isinstance(item, dict):
        x = float(item.get("x", 0))
        y = float(item.get("y", 0))
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        x = float(item[0])
        y = float(item[1])
    else:
        raise ValueError("Stroke point must be {'x','y'} or [x,y].")
    if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError("Stroke point coordinates must be finite values from 0 to 1.")
    return fitz.Point(rect.x0 + x * rect.width, rect.y0 + y * rect.height)


def _draw_simple_svg_path(
    page: fitz.Page,
    rect: fitz.Rect,
    path_text: str,
    *,
    color: tuple[float, float, float],
    width: float,
) -> bool:
    if len(path_text) > MAX_DRAW_PATH_CHARACTERS:
        return False
    # Accept either raw path data or a tiny SVG containing d="...".
    match = re.search(r'd=["\']([^"\']+)["\']', path_text)
    d = match.group(1) if match else path_text
    tokens = re.findall(r"[MLml]|-?(?:\d+(?:\.\d*)?|\.\d+)", d)
    if not tokens or len(tokens) > MAX_DRAW_PATH_TOKENS:
        return False

    # Reject unsupported SVG commands instead of silently drawing a different
    # shape. The browser drawing pad emits only absolute M/L commands.
    remainder = re.sub(r"[MLml]|-?(?:\d+(?:\.\d*)?|\.\d+)|[\s,]+", "", d)
    if remainder:
        return False

    paths: list[list[tuple[float, float]]] = []
    current_path: list[tuple[float, float]] = []
    command: Optional[str] = None
    cursor = (0.0, 0.0)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"M", "L", "m", "l"}:
            command = token
            i += 1
            continue
        if command not in {"M", "L", "m", "l"} or i + 1 >= len(tokens):
            return False
        try:
            x = float(tokens[i])
            y = float(tokens[i + 1])
        except ValueError:
            return False
        if command in {"m", "l"}:
            x += cursor[0]
            y += cursor[1]
        cursor = (x, y)
        if command in {"M", "m"}:
            if len(current_path) >= 2:
                paths.append(current_path)
            current_path = [(x, y)]
            command = "L" if command == "M" else "l"
        else:
            current_path.append((x, y))
        i += 2

    if len(current_path) >= 2:
        paths.append(current_path)
    if not paths:
        return False

    all_points = [point for path in paths for point in path]
    if not all(
        math.isfinite(x)
        and math.isfinite(y)
        and 0 <= x <= 1
        and 0 <= y <= 1
        for x, y in all_points
    ):
        return False
    for path in paths:
        points = [
            fitz.Point(
                rect.x0 + x * rect.width,
                rect.y0 + y * rect.height,
            )
            for x, y in path
        ]
        for start, end in zip(points, points[1:]):
            page.draw_line(start, end, color=color, width=width, overlay=True)
    return True


def _write_preview_pdf(
    pdf: fitz.Document,
    *,
    preview_path: Path,
    max_pages: Optional[int] = None,
    complete_document_path: Optional[Path] = None,
) -> None:
    page_limit = pdf.page_count if max_pages is None else min(max_pages, pdf.page_count)
    if page_limit == pdf.page_count and complete_document_path is not None:
        # The Edit preview is a second delivery reference to the complete edited
        # document. Copying the already-saved result preserves every document-
        # level feature (outlines, destinations, forms, page labels, metadata,
        # attachments, optional-content layers, structure tags, and page boxes)
        # instead of rebuilding only its pages with insert_pdf().
        source = Path(complete_document_path)
        if not source.is_file():
            raise FileNotFoundError(f"Complete edited PDF not found: {source}")
        shutil.copyfile(source, preview_path)
        return

    preview = fitz.open()
    try:
        for index in range(page_limit):
            preview.insert_pdf(pdf, from_page=index, to_page=index)
        preview.save(preview_path, garbage=4, deflate=True, clean=True)
    finally:
        preview.close()


def _preview_artifact_from_path(
    path: Path,
    *,
    page_count: Optional[int],
    preview_stage: str,
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
    owner_user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> PdfPreviewArtifact:
    persisted_path, storage_key, download_url = _persist_or_copy(
        path,
        output_name=path.name,
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        owner_user_id=owner_user_id,
        organization_id=organization_id,
        feature="edit_pdf_preview",
    )
    return PdfPreviewArtifact(
        file_name=path.name,
        file_extension="pdf",
        file_size_mb=_get_file_size_mb(persisted_path),
        file_path=str(persisted_path),
        page_count=page_count,
        preview_stage=preview_stage,
        storage_key=storage_key,
        download_url=download_url,
    )


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    stem = _safe_stem(Path(raw).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return (cleaned[:175].rstrip("-._") or "document")


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated PDF is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/pdf"


def _persist_or_copy(
    source_path: Path,
    *,
    output_name: str,
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
    owner_user_id: Optional[str] = None,
    organization_id: Optional[str] = None,
    feature: Optional[str] = None,
) -> tuple[Path, Optional[str], Optional[str]]:
    if storage_backend is None:
        storage_backend = _try_default_storage(base_dir=str(artifacts_dir))

    if storage_backend is not None:
        persist_kwargs: dict[str, Any] = {
            "source_file_path": str(source_path),
            "artifact_name": output_name,
            "content_type": _guess_content_type(source_path),
        }
        if owner_user_id:
            persist_kwargs.update(
                owner_user_id=owner_user_id,
                organization_id=organization_id,
                feature=feature,
            )
        try:
            stored = storage_backend.persist(**persist_kwargs)
        except TypeError as exc:
            if owner_user_id:
                raise RuntimeError(
                    "The configured PDF artifact storage backend must accept "
                    "owner_user_id, organization_id, and feature metadata."
                ) from exc
            raise
        stored_path = Path(getattr(stored, "stored_path", source_path)).resolve()
        return stored_path, getattr(stored, "storage_key", None), getattr(stored, "download_url", None)

    artifacts = Path(artifacts_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / output_name
    if destination.exists():
        destination = artifacts / f"{Path(output_name).stem}-{os.urandom(4).hex()}{Path(output_name).suffix}"
    shutil.copy2(source_path, destination)
    return destination, None, None


def _try_default_storage(*, base_dir: str) -> Optional[StorageBackend]:
    try:
        from backend.src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


__all__ = [
    "EditedPdfArtifact",
    "PdfPreviewArtifact",
    "PyMuPDFEditBackend",
    "edit_pdf",
]
