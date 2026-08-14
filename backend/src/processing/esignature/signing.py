from __future__ import annotations

"""
PDF signing renderer for ReDOCX Sign.

This module applies signer values to PDF fields using PyMuPDF. It supports:
- typed signatures
- uploaded image signatures
- drawn signatures stored as SVG/PNG/JPEG/WebP, with SVG rasterization when cairosvg is installed
- date/name/email/text/checkbox fields
- normalized frontend coordinates from schema.PdfRectangle

It does not decide envelope lifecycle. Use services/esignature_service.py or
processing/esignature/envelope.py for workflow transitions.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Mapping, Optional
import os
import shutil

import fitz  # PyMuPDF

try:
    from backend.src.schema import (
        AddSignatureOperation,
        ESignatureField,
        ESignatureFieldType,
        PdfRectangle,
        SignatureRepresentationType,
    )
except ImportError:
    from ...schema import (
        AddSignatureOperation,
        ESignatureField,
        ESignatureFieldType,
        PdfRectangle,
        SignatureRepresentationType,
    )

from .audit import sha256_file
from .fields import fields_for_signer, normalize_email


StorageKeyResolver = Callable[[str], str | Path]
DEFAULT_FONT_NAME = "helv"


@dataclass(frozen=True)
class SignedPdfArtifact:
    filename: str
    path: str
    file_size_mb: float
    page_count: int
    sha256: str
    signer_email: str
    signed_at_iso: str
    fields_applied: int


class PdfSigningError(RuntimeError):
    """Raised when signing cannot be safely applied to a PDF."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def file_size_mb(path: str | Path) -> float:
    return round(Path(path).stat().st_size / (1024 * 1024), 4)


def normalized_rect_to_fitz(page: fitz.Page, rectangle: PdfRectangle) -> fitz.Rect:
    page_rect = page.rect
    x0 = page_rect.x0 + rectangle.x * page_rect.width
    y0 = page_rect.y0 + rectangle.y * page_rect.height
    x1 = x0 + rectangle.width * page_rect.width
    y1 = y0 + rectangle.height * page_rect.height
    return fitz.Rect(x0, y0, x1, y1)


def _rgb_from_hex(value: str) -> tuple[float, float, float]:
    raw = (value or "#111111").strip().lstrip("#")
    if len(raw) != 6:
        raw = "111111"
    return tuple(int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _require_source_pdf(source_pdf_path: str | Path) -> Path:
    path = Path(source_pdf_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Source PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError("source_pdf_path must point to a .pdf file.")
    return path


def _ensure_output_path(output_pdf_path: str | Path) -> Path:
    output = Path(output_pdf_path)
    if output.suffix.lower() != ".pdf":
        raise ValueError("output_pdf_path must end with .pdf.")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _resolve_storage_path(
    storage_key: Optional[str],
    *,
    resolver: Optional[StorageKeyResolver],
) -> Optional[Path]:
    if not storage_key:
        return None
    if resolver is None:
        candidate = Path(storage_key)
    else:
        candidate = Path(resolver(storage_key))
    if not candidate.exists():
        raise FileNotFoundError(f"Signature asset not found for storage key: {storage_key}")
    return candidate


def _rasterize_svg_to_png(svg_path: Path, output_png: Path) -> Optional[Path]:
    try:
        import cairosvg  # type: ignore
    except Exception:
        return None
    cairosvg.svg2png(url=str(svg_path), write_to=str(output_png))
    return output_png if output_png.exists() else None


def _signature_image_path(
    signature: AddSignatureOperation,
    *,
    resolver: Optional[StorageKeyResolver],
    workdir: Path,
) -> Optional[Path]:
    if signature.signature_type == SignatureRepresentationType.uploaded_image:
        return _resolve_storage_path(signature.signature_image_storage_key, resolver=resolver)

    if signature.signature_type == SignatureRepresentationType.drawn:
        if signature.signature_image_storage_key:
            return _resolve_storage_path(signature.signature_image_storage_key, resolver=resolver)
        svg_path = _resolve_storage_path(signature.signature_svg_storage_key, resolver=resolver)
        if svg_path is None:
            return None
        if svg_path.suffix.lower() == ".svg":
            rasterized = _rasterize_svg_to_png(
                svg_path,
                workdir / "drawn-signature.png",
            )
            if rasterized is None:
                raise PdfSigningError(
                    "CairoSVG is required to render an SVG signature."
                )
            return rasterized
        return svg_path

    return None


def _insert_textbox(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    fontsize: float = 11,
    color: tuple[float, float, float] = (0, 0, 0),
    align: int = 0,
) -> None:
    page.insert_textbox(
        rect,
        text,
        fontsize=fontsize,
        fontname=DEFAULT_FONT_NAME,
        color=color,
        align=align,
        overlay=True,
    )


def _apply_signature_to_rect(
    page: fitz.Page,
    rect: fitz.Rect,
    *,
    signature: AddSignatureOperation,
    signer_name: str,
    resolver: Optional[StorageKeyResolver],
    workdir: Path,
) -> None:
    # Never white-out the destination. Safe-placement validation guarantees that
    # signer fields do not obscure existing document content. PNG/SVG signatures
    # therefore retain their alpha channel and typed signatures remain transparent.
    image_path = _signature_image_path(signature, resolver=resolver, workdir=workdir)
    if image_path is not None:
        try:
            page.insert_image(rect, filename=str(image_path), keep_proportion=True, overlay=True)
            return
        except Exception as exc:
            raise PdfSigningError(f"Could not insert signature image: {image_path}") from exc

    # Typed signatures are rendered as text. Image-backed signatures must have
    # resolved successfully above and must never silently degrade to typed text.
    typed = signature.typed_name or signer_name
    fontsize = max(8, min(24, rect.height * 0.45))
    _insert_textbox(page, rect, typed, fontsize=fontsize, align=1)


def _apply_checkbox(page: fitz.Page, rect: fitz.Rect, checked: bool) -> None:
    page.draw_rect(rect, color=(0, 0, 0), width=0.8, overlay=True)
    if checked:
        page.draw_line(rect.bl + (rect.width * 0.18, -rect.height * 0.45), rect.tl + (rect.width * 0.45, rect.height * 0.72), color=(0, 0, 0), width=1.4)
        page.draw_line(rect.tl + (rect.width * 0.45, rect.height * 0.72), rect.tr + (-rect.width * 0.12, rect.height * 0.18), color=(0, 0, 0), width=1.4)


def _native_widget_for_field(page: fitz.Page, field: ESignatureField):
    name = str(getattr(field, "native_widget_name", None) or "").strip()
    if not name:
        return None
    try:
        widgets = list(page.widgets() or [])
    except Exception:
        return None
    for widget in widgets:
        if str(getattr(widget, "field_name", "") or "").strip() == name:
            return widget
    return None


def _field_rect(page: fitz.Page, field: ESignatureField) -> fitz.Rect:
    widget = _native_widget_for_field(page, field)
    if widget is not None:
        try:
            return fitz.Rect(widget.rect)
        except Exception:
            pass
    return normalized_rect_to_fitz(page, field.rectangle)


def _fill_native_text_widget(widget: object, value: str) -> bool:
    try:
        setattr(widget, "field_value", value)
        update = getattr(widget, "update", None)
        if callable(update):
            update()
        return True
    except Exception:
        return False


def _fill_native_checkbox_widget(widget: object, checked: bool) -> bool:
    try:
        value: object = "Off"
        if checked:
            on_state = getattr(widget, "on_state", None)
            if callable(on_state):
                value = on_state() or "Yes"
            else:
                value = "Yes"
        setattr(widget, "field_value", value)
        update = getattr(widget, "update", None)
        if callable(update):
            update()
        return True
    except Exception:
        return False


def _field_text_value(
    field: ESignatureField,
    *,
    signer_name: str,
    signer_email: str,
    signed_at_iso: str,
    values: Mapping[str, str],
) -> str:
    # Identity/date fields are server-owned. A signer may submit arbitrary JSON
    # through the public token route, so never let field_values override these
    # audit-sensitive values.
    if field.field_type == ESignatureFieldType.date_signed:
        return signed_at_iso[:10]
    if field.field_type == ESignatureFieldType.name:
        return signer_name
    if field.field_type == ESignatureFieldType.email:
        return signer_email

    key = field.field_id or field.label or f"{field.field_type.value}:{field.page_number}"
    if key in values:
        return str(values[key])
    if field.default_value is not None:
        return str(field.default_value)
    return ""


def _typed_signature_for_field(
    field: ESignatureField,
    *,
    signature: AddSignatureOperation,
    signer_name: str,
    values: Mapping[str, str],
) -> AddSignatureOperation:
    """Resolve a typed signature/initials value for one field.

    A single signing operation may cover both signature and initials fields.
    Signature fields use the legal typed signature by default; initials fields
    default to initials derived from the signer name. The recipient UI may
    explicitly provide a per-field typed value through field_values.
    """
    if signature.signature_type != SignatureRepresentationType.typed:
        return signature

    key = field.field_id or field.label or f"{field.field_type.value}:{field.page_number}"
    explicit = str(values.get(key, "")).strip()
    if explicit:
        if len(explicit) > 200:
            raise PdfSigningError(
                f"Typed value for '{field.label or field.field_id or field.field_type.value}' exceeds 200 characters."
            )
        return signature.model_copy(update={"typed_name": explicit})

    if field.field_type == ESignatureFieldType.initials:
        initials = "".join(
            part[0] for part in str(signer_name or "").strip().split() if part
        )
        if not initials:
            initials = str(signature.typed_name or "").strip()[:8]
        if not initials:
            raise PdfSigningError("Initials could not be derived for the signer.")
        return signature.model_copy(update={"typed_name": initials[:8]})

    return signature


def apply_signer_fields_to_pdf(
    *,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    fields: list[ESignatureField],
    signer_email: str,
    signer_name: str,
    signature: AddSignatureOperation,
    values: Optional[Mapping[str, str]] = None,
    signed_at_iso: Optional[str] = None,
    storage_key_resolver: Optional[StorageKeyResolver] = None,
) -> SignedPdfArtifact:
    """
    Apply the fields assigned to one signer to a new PDF.

    Parameters:
    - source_pdf_path: current envelope PDF version
    - output_pdf_path: destination PDF version after this signer
    - fields: all envelope fields; only signer-assigned fields are applied
    - signature: signature representation for signature/initials fields
    - values: optional field_id/label -> completed text values
    """
    source = _require_source_pdf(source_pdf_path)
    output = _ensure_output_path(output_pdf_path)
    normalized_email = normalize_email(signer_email)
    signed_at = signed_at_iso or utcnow_iso()
    value_map = dict(values or {})

    selected_fields = fields_for_signer(fields, normalized_email)
    if not selected_fields:
        raise PdfSigningError(f"No e-signature fields assigned to signer: {normalized_email}")

    with TemporaryDirectory(prefix="redocx-sign-") as tmp:
        workdir = Path(tmp)
        working = workdir / source.name
        shutil.copy2(source, working)

        with fitz.open(working) as pdf:
            if bool(getattr(pdf, "needs_pass", False)):
                raise PdfSigningError("Password-protected PDFs cannot be signed without an unlock workflow.")

            applied = 0
            for field in selected_fields:
                page_index = field.page_number - 1
                if page_index < 0 or page_index >= pdf.page_count:
                    raise PdfSigningError(f"Field page_number {field.page_number} exceeds PDF page_count {pdf.page_count}.")
                page = pdf[page_index]
                rect = _field_rect(page, field)
                native_widget = _native_widget_for_field(page, field)

                if field.field_type in {ESignatureFieldType.signature, ESignatureFieldType.initials}:
                    field_signature = _typed_signature_for_field(
                        field,
                        signature=signature,
                        signer_name=signer_name,
                        values=value_map,
                    )
                    _apply_signature_to_rect(
                        page,
                        rect,
                        signature=field_signature,
                        signer_name=signer_name,
                        resolver=storage_key_resolver,
                        workdir=workdir,
                    )
                    applied += 1
                    continue

                if field.field_type == ESignatureFieldType.checkbox:
                    raw = _field_text_value(
                        field,
                        signer_name=signer_name,
                        signer_email=normalized_email,
                        signed_at_iso=signed_at,
                        values=value_map,
                    )
                    checked = str(raw).strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "checked",
                        "on",
                    }
                    if field.required and not checked:
                        raise PdfSigningError(
                            f"Required checkbox '{field.label or field.field_id or 'checkbox'}' must be checked."
                        )
                    if native_widget is None or not _fill_native_checkbox_widget(native_widget, checked):
                        _apply_checkbox(page, rect, checked)
                    applied += 1
                    continue

                text = _field_text_value(field, signer_name=signer_name, signer_email=normalized_email, signed_at_iso=signed_at, values=value_map)
                if field.required and not str(text).strip():
                    raise PdfSigningError(f"Required field '{field.label or field.field_id or field.field_type.value}' is empty.")
                if native_widget is None or not _fill_native_text_widget(native_widget, str(text)):
                    _insert_textbox(page, rect, str(text), fontsize=max(7, min(14, rect.height * 0.42)))
                applied += 1

            pdf.save(output, garbage=4, deflate=True)

    with fitz.open(output) as completed_pdf:
        output_page_count = int(completed_pdf.page_count)

    return SignedPdfArtifact(
        filename=output.name,
        path=str(output),
        file_size_mb=file_size_mb(output),
        page_count=output_page_count,
        sha256=sha256_file(output),
        signer_email=normalized_email,
        signed_at_iso=signed_at,
        fields_applied=applied,
    )


__all__ = [
    "StorageKeyResolver",
    "SignedPdfArtifact",
    "PdfSigningError",
    "utcnow_iso",
    "file_size_mb",
    "normalized_rect_to_fitz",
    "apply_signer_fields_to_pdf",
]
