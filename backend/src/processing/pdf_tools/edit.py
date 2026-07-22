from __future__ import annotations

"""
ReDOCX PDF Tools - Edit / Annotate PDF processing.

Supported operation families aligned with schema.py:
- add_text / remove_text
- add_image / remove_image
- draw
- highlight
- whiteout
- add_signature / remove_signature

This module applies edits to a PDF and returns artifact metadata. It deliberately
uses normalized rectangles (0..1) so the frontend preview coordinate system maps
cleanly to backend PDF coordinates.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Optional, Protocol, Sequence
import json
import mimetypes
import os
import re
import shutil

import fitz  # PyMuPDF
from PIL import Image


AssetResolver = Callable[[str], str]


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

        output_name = _normalize_pdf_filename(output_filename, default="edited-document.pdf")
        operations_requested = len(operations)
        operations_applied = 0

        with fitz.open(source) as pdf:
            _reject_encrypted_pdf(pdf, source)
            for operation in operations:
                operations_applied += self._apply_operation(
                    pdf,
                    operation,
                    asset_resolver=asset_resolver,
                )

            with TemporaryDirectory(prefix="redocx-edit-") as workdir:
                output_path = Path(workdir) / output_name
                pdf.save(output_path, garbage=4, deflate=True, clean=True)

                preview_artifact: Optional[PdfPreviewArtifact] = None
                if generate_preview:
                    preview_path = Path(workdir) / f"{Path(output_name).stem}-preview.pdf"
                    _write_preview_pdf(pdf, preview_path=preview_path)
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
    ) -> int:
        op = _operation_value(operation)
        page = _page_for_operation(pdf, operation)
        rect = _normalized_rect_to_page_rect(page, _operation_rectangle(operation))

        if op == "add_text":
            self._add_text(page, rect, operation)
        elif op == "remove_text":
            self._remove_content(
                page,
                rect,
                operation,
                remove_text=True,
                remove_images=False,
                remove_graphics=False,
            )
        elif op == "add_image":
            self._add_image(page, rect, operation, asset_resolver=asset_resolver)
        elif op == "remove_image":
            self._remove_content(
                page,
                rect,
                operation,
                remove_text=False,
                remove_images=True,
                remove_graphics=False,
            )
        elif op == "draw":
            self._draw(page, rect, operation, asset_resolver=asset_resolver)
        elif op == "highlight":
            self._highlight(page, rect, operation)
        elif op == "whiteout":
            self._redact(
                page,
                rect,
                remove_text=True,
                remove_images=True,
                remove_graphics=True,
                fill=(1, 1, 1),
            )
        elif op == "add_signature":
            self._add_signature(page, rect, operation, asset_resolver=asset_resolver)
        elif op == "remove_signature":
            self._remove_signature(page, rect, operation)
        else:
            raise ValueError(f"Unsupported edit operation: {op}")

        return 1

    def _add_text(self, page: fitz.Page, rect: fitz.Rect, operation: Any) -> None:
        text = str(getattr(operation, "text", "") or "").strip()
        if not text:
            raise ValueError("add_text operation requires text.")
        font_size = float(getattr(operation, "font_size", 12) or 12)
        color = _hex_to_rgb01(str(getattr(operation, "color_hex", "#111111") or "#111111"))
        font_family = str(getattr(operation, "font_family", "Helvetica") or "Helvetica")
        font_name, font_file = _resolve_font(font_family, self.default_font_path)

        kwargs: dict[str, Any] = {
            "fontsize": font_size,
            "color": color,
            "align": fitz.TEXT_ALIGN_LEFT,
        }
        if font_file:
            kwargs["fontname"] = "redocxfont"
            kwargs["fontfile"] = str(font_file)
        else:
            kwargs["fontname"] = font_name

        rc = -1.0
        candidate_size = font_size
        while candidate_size >= 4:
            kwargs["fontsize"] = candidate_size
            rc = page.insert_textbox(rect, text, **kwargs)
            if rc >= 0:
                break
            candidate_size = round(candidate_size * 0.9, 2)
        if rc < 0:
            raise RuntimeError("Text did not fit inside the requested PDF rectangle.")

    def _add_image(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
    ) -> None:
        key = str(getattr(operation, "image_storage_key", "") or "").strip()
        if not key:
            raise ValueError("add_image operation requires image_storage_key.")
        path = _resolve_asset_path(key, asset_resolver=asset_resolver)
        _insert_image_or_svg(page, rect, path)

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
            # Try to draw simple SVG path commands directly. If this is a full SVG
            # or complex path, render through cairosvg when available.
            if _draw_simple_svg_path(page, rect, path_svg, color=color, width=stroke_width):
                return
            temp_png = _render_svg_text_to_temp_png(path_svg)
            _insert_image_or_svg(page, rect, temp_png)
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

    def _remove_content(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        remove_text: bool,
        remove_images: bool,
        remove_graphics: bool,
    ) -> None:
        mode = _removal_mode(operation)
        if mode == "remove_redocx_annotation":
            removed = _remove_redocx_annotations(page, rect)
            if removed == 0:
                raise ValueError("No ReDOCX annotation was found in the selected region.")
            return

        self._redact(
            page,
            rect,
            remove_text=remove_text,
            remove_images=remove_images,
            remove_graphics=remove_graphics,
        )

    @staticmethod
    def _redact(
        page: fitz.Page,
        rect: fitz.Rect,
        *,
        remove_text: bool,
        remove_images: bool,
        remove_graphics: bool,
        fill: Optional[tuple[float, float, float]] = None,
    ) -> None:
        """Permanently remove the selected PDF content instead of covering it."""
        page.add_redact_annot(rect, fill=fill, cross_out=False)
        kwargs = {
            "images": (
                getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1)
                if remove_images
                else getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
            ),
            "graphics": (
                getattr(fitz, "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED", 2)
                if remove_graphics
                else getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0)
            ),
            "text": (
                getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0)
                if remove_text
                else getattr(fitz, "PDF_REDACT_TEXT_NONE", 1)
            ),
        }
        try:
            page.apply_redactions(**kwargs)
        except TypeError:  # Compatibility with older PyMuPDF releases.
            if not remove_text or not remove_graphics:
                raise RuntimeError(
                    "This PyMuPDF version cannot preserve non-target PDF content "
                    "during the requested removal operation. Upgrade PyMuPDF before "
                    "using selective text or image removal."
                )
            page.apply_redactions(images=kwargs["images"])

    def _remove_signature(self, page: fitz.Page, rect: fitz.Rect, operation: Any) -> None:
        mode = _removal_mode(operation)
        field_id = str(getattr(operation, "field_id", "") or "").strip()

        if mode == "remove_redocx_annotation":
            removed = _remove_redocx_annotations(page, rect)
            if removed == 0:
                raise ValueError("No ReDOCX signature annotation was found in the selected region.")
            return

        removed_widget = False
        for widget in list(page.widgets() or []):
            matches_field = not field_id or str(getattr(widget, "field_name", "") or "") == field_id
            if matches_field and widget.rect.intersects(rect):
                page.delete_widget(widget)
                removed_widget = True
        for annotation in list(page.annots() or []):
            if annotation.rect.intersects(rect) and _is_signature_annotation(annotation):
                page.delete_annot(annotation)

        if field_id and not removed_widget:
            raise ValueError(f"No signature field named '{field_id}' was found in the selected region.")

        self._redact(
            page,
            rect,
            remove_text=True,
            remove_images=True,
            remove_graphics=True,
        )

    def _add_signature(
        self,
        page: fitz.Page,
        rect: fitz.Rect,
        operation: Any,
        *,
        asset_resolver: Optional[AssetResolver],
    ) -> None:
        signature_type = _enum_value(getattr(operation, "signature_type", ""))
        if signature_type == "typed":
            typed_name = str(getattr(operation, "typed_name", "") or "").strip()
            if not typed_name:
                raise ValueError("typed signature requires typed_name.")
            font_name, font_file = _resolve_font("Helvetica-Oblique", self.default_font_path)
            kwargs: dict[str, Any] = {"fontsize": max(8, rect.height * 0.45), "color": (0, 0, 0)}
            if font_file:
                kwargs["fontname"] = "redocxfont"
                kwargs["fontfile"] = str(font_file)
            else:
                kwargs["fontname"] = font_name
            rc = -1.0
            candidate_size = float(kwargs["fontsize"])
            while candidate_size >= 6:
                kwargs["fontsize"] = candidate_size
                rc = page.insert_textbox(rect, typed_name, align=fitz.TEXT_ALIGN_CENTER, **kwargs)
                if rc >= 0:
                    break
                candidate_size = round(candidate_size * 0.9, 2)
            if rc < 0:
                raise RuntimeError("Typed signature did not fit inside the requested PDF rectangle.")
            return

        if signature_type == "uploaded_image":
            key = str(getattr(operation, "signature_image_storage_key", "") or "").strip()
            if not key:
                raise ValueError("uploaded_image signature requires signature_image_storage_key.")
            _insert_image_or_svg(page, rect, _resolve_asset_path(key, asset_resolver=asset_resolver))
            return

        if signature_type == "drawn":
            key = str(getattr(operation, "signature_svg_storage_key", "") or "").strip()
            if not key:
                raise ValueError("drawn signature requires signature_svg_storage_key.")
            _insert_image_or_svg(page, rect, _resolve_asset_path(key, asset_resolver=asset_resolver))
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


def _normalized_rect_to_page_rect(page: fitz.Page, rectangle: Any) -> fitz.Rect:
    x = float(getattr(rectangle, "x"))
    y = float(getattr(rectangle, "y"))
    width = float(getattr(rectangle, "width"))
    height = float(getattr(rectangle, "height"))
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
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


def _remove_redocx_annotations(page: fitz.Page, rect: fitz.Rect) -> int:
    removed = 0
    for annotation in list(page.annots() or []):
        if not annotation.rect.intersects(rect):
            continue
        info = annotation.info or {}
        marker = " ".join(
            str(info.get(key) or "")
            for key in ("title", "subject", "content")
        ).lower()
        if "redocx" not in marker:
            continue
        page.delete_annot(annotation)
        removed += 1
    return removed


def _is_signature_annotation(annotation: fitz.Annot) -> bool:
    annotation_type = getattr(annotation, "type", (None, ""))
    type_name = str(annotation_type[1] if isinstance(annotation_type, tuple) else annotation_type).lower()
    if type_name in {"ink", "stamp", "freetext"}:
        return True

    info = annotation.info or {}
    marker = " ".join(
        str(info.get(key) or "")
        for key in ("title", "subject", "content")
    ).lower()
    return "signature" in marker or "redocx" in marker


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
    if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
        raise ValueError(f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}")


def _hex_to_rgb01(value: str) -> tuple[float, float, float]:
    raw = value.strip()
    if not re.match(r"^#[0-9A-Fa-f]{6}$", raw):
        raise ValueError(f"Invalid hex color: {value}")
    return (
        int(raw[1:3], 16) / 255,
        int(raw[3:5], 16) / 255,
        int(raw[5:7], 16) / 255,
    )


def _env_font_path() -> Optional[Path]:
    configured = os.getenv("PDF_TOOLS_FONT_PATH", "").strip()
    if not configured:
        return None
    path = Path(configured).expanduser().resolve()
    return path if path.exists() else None


def _resolve_font(font_family: str, default_font_path: Optional[Path]) -> tuple[str, Optional[Path]]:
    # PyMuPDF built-in aliases.
    normalized = font_family.strip().lower()
    builtin = {
        "helvetica": "helv",
        "arial": "helv",
        "helvetica-oblique": "heit",
        "helvetica italic": "heit",
        "times": "tiro",
        "times-roman": "tiro",
        "courier": "cour",
    }
    if default_font_path is not None and default_font_path.exists():
        return "redocxfont", default_font_path
    return builtin.get(normalized, "helv"), None


def _resolve_asset_path(key: str, *, asset_resolver: Optional[AssetResolver]) -> Path:
    if asset_resolver is not None:
        resolved = Path(asset_resolver(key)).expanduser().resolve()
    else:
        resolved = Path(key).expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"PDF edit asset not found: {key}")
    return resolved


def _insert_image_or_svg(page: fitz.Page, rect: fitz.Rect, path: Path) -> None:
    temporary_path: Optional[Path] = None
    try:
        suffix = path.suffix.lower()
        if suffix == ".svg":
            temporary_path = _render_svg_file_to_temp_png(path)
            path = temporary_path
        elif suffix == ".webp":
            temporary_path = _convert_image_to_temp_png(path)
            path = temporary_path
        page.insert_image(rect, filename=str(path), keep_proportion=True, overlay=True)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _convert_image_to_temp_png(path: Path) -> Path:
    out = Path(os.getenv("TMPDIR", "/tmp")) / f"redocx-{os.urandom(4).hex()}.png"
    with Image.open(path) as image:
        image.convert("RGBA").save(out)
    return out


def _render_svg_file_to_temp_png(path: Path) -> Path:
    return _render_svg_text_to_temp_png(path.read_text(encoding="utf-8"))


def _render_svg_text_to_temp_png(svg_text: str) -> Path:
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

    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            continue
        points = [_point_in_rect(rect, item) for item in stroke]
        for start, end in zip(points, points[1:]):
            page.draw_line(start, end, color=color, width=width, overlay=True)


def _point_in_rect(rect: fitz.Rect, item: Any) -> fitz.Point:
    if isinstance(item, dict):
        x = float(item.get("x", 0))
        y = float(item.get("y", 0))
    elif isinstance(item, (list, tuple)) and len(item) >= 2:
        x = float(item[0])
        y = float(item[1])
    else:
        raise ValueError("Stroke point must be {'x','y'} or [x,y].")
    return fitz.Point(rect.x0 + x * rect.width, rect.y0 + y * rect.height)


def _draw_simple_svg_path(
    page: fitz.Page,
    rect: fitz.Rect,
    path_text: str,
    *,
    color: tuple[float, float, float],
    width: float,
) -> bool:
    # Accept either raw path data or a tiny SVG containing d="...".
    match = re.search(r'd=["\']([^"\']+)["\']', path_text)
    d = match.group(1) if match else path_text
    tokens = re.findall(r"[MLml]|-?(?:\d+(?:\.\d*)?|\.\d+)", d)
    if not tokens:
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
    normalized = all(0 <= x <= 1 and 0 <= y <= 1 for x, y in all_points)
    max_x = max(abs(x) for x, _ in all_points) or 1
    max_y = max(abs(y) for _, y in all_points) or 1
    for path in paths:
        points = [
            fitz.Point(
                rect.x0 + (x if normalized else x / max_x) * rect.width,
                rect.y0 + (y if normalized else y / max_y) * rect.height,
            )
            for x, y in path
        ]
        for start, end in zip(points, points[1:]):
            page.draw_line(start, end, color=color, width=width, overlay=True)
    return True


def _write_preview_pdf(pdf: fitz.Document, *, preview_path: Path, max_pages: Optional[int] = None) -> None:
    preview = fitz.open()
    try:
        page_limit = pdf.page_count if max_pages is None else min(max_pages, pdf.page_count)
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
    return cleaned or "document"


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