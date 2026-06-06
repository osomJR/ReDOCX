from __future__ import annotations

"""
Renderers for compliance outputs.

Supported outputs:
- machine-readable JSON report
- human-readable PDF report
- annotated source output PDF (best-effort true annotation for single PDF input)

Rendering note:
FPDF's ``multi_cell(0, ...)`` is sensitive to the current cursor X position.
If the cursor is left near the right margin after a previous write, width=0 can
resolve to zero usable width and raise:
    FPDFException: Not enough horizontal space to render a single character

All report text in this module is therefore written through ``_safe_multi_cell``.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
import json
import os
import re

import fitz  # PyMuPDF
from fpdf import FPDF

try:
    from src.schema import (
        ComplianceFileResult,
        ComplianceMachineReadableReport,
        ComplianceOutputFormat,
        ComplianceReportVariant,
        ComplianceRuleResult,
        DocumentPayload,
        DocumentSetPayload,
        HumanReviewRequirement,
    )
    from src.validation import build_compliance_file_result
except ImportError:  # pragma: no cover
    from schema import (
        ComplianceFileResult,
        ComplianceMachineReadableReport,
        ComplianceOutputFormat,
        ComplianceReportVariant,
        ComplianceRuleResult,
        DocumentPayload,
        DocumentSetPayload,
        HumanReviewRequirement,
    )
    from validation import build_compliance_file_result

try:
    from src.storage.artifacts import LocalArtifactStorage, guess_content_type
except ImportError:  # pragma: no cover
    from storage.artifacts import LocalArtifactStorage, guess_content_type

try:
    from .evidence import EvidenceDocument, get_source_reference
except ImportError:  # pragma: no cover
    from evidence import EvidenceDocument, get_source_reference


DEFAULT_ARTIFACTS_DIR = Path("artifacts/compliance")

PDF_FONT_FAMILY = "NotoSans"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FONT_DIR = PROJECT_ROOT / "assets" / "fonts"

DEFAULT_PDF_FONT_REGULAR = DEFAULT_FONT_DIR / "NotoSans-Regular.ttf"
DEFAULT_PDF_FONT_BOLD = DEFAULT_FONT_DIR / "NotoSans-Bold.ttf"

# Long hashes, URLs, locator strings, and machine IDs can otherwise become one
# unbreakable token. This value is intentionally conservative for portrait A4.
MAX_UNBROKEN_TOKEN_CHARS = 72


def _resolve_font_path(env_name: str, default_path: Path) -> Path:
    configured = os.getenv(env_name, "").strip()
    return Path(configured).expanduser() if configured else default_path


def _configure_unicode_pdf(pdf: FPDF) -> None:
    regular_font = _resolve_font_path(
        "COMPLIANCE_PDF_FONT_REGULAR",
        DEFAULT_PDF_FONT_REGULAR,
    )
    bold_font = _resolve_font_path(
        "COMPLIANCE_PDF_FONT_BOLD",
        DEFAULT_PDF_FONT_BOLD,
    )

    missing = [str(path) for path in (regular_font, bold_font) if not path.exists()]
    if missing:
        raise ComplianceRenderError(
            "Unicode PDF font file(s) missing: "
            + ", ".join(missing)
            + ". Add Unicode .ttf fonts under assets/fonts or set "
              "COMPLIANCE_PDF_FONT_REGULAR and COMPLIANCE_PDF_FONT_BOLD."
        )

    pdf.add_font(PDF_FONT_FAMILY, "", str(regular_font), uni=True)
    pdf.add_font(PDF_FONT_FAMILY, "B", str(bold_font), uni=True)


def _configure_report_pdf() -> FPDF:
    """
    Create a report PDF with consistent margins, Unicode fonts, and automatic
    page breaking.
    """
    pdf = FPDF()
    _configure_unicode_pdf(pdf)
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.set_margins(left=12, top=12, right=12)
    pdf.add_page()
    return pdf


def _usable_page_width(pdf: FPDF) -> float:
    """
    Return the explicit usable page width.

    ``pdf.multi_cell(0, ...)`` means "remaining width from current X", so it can
    fail after a previous write moved X close to the right edge. Always use an
    explicit width from margins instead.
    """
    epw = getattr(pdf, "epw", None)
    if isinstance(epw, (int, float)) and epw > 1:
        return float(epw)

    page_width = float(getattr(pdf, "w", 210))
    left = float(getattr(pdf, "l_margin", 10))
    right = float(getattr(pdf, "r_margin", 10))
    width = page_width - left - right
    if width <= 1:
        raise ComplianceRenderError("PDF page width is too small for compliance rendering.")
    return width


def _normalize_pdf_text(value: object) -> str:
    """
    Normalize text before sending it to FPDF.

    Keeps Unicode characters, but removes control characters that can confuse
    line-breaking, normalizes whitespace, and creates break opportunities in very
    long tokens.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", "    ")
    text = text.replace("\u00a0", " ")
    text = text.replace("\u200b", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)

    normalized_lines = []
    for line in text.split("\n"):
        normalized_lines.append(_break_long_tokens(line.rstrip()))
    return "\n".join(normalized_lines)


def _break_long_tokens(text: str, *, max_chars: int = MAX_UNBROKEN_TOKEN_CHARS) -> str:
    if max_chars < 16:
        return text

    pieces: list[str] = []
    for token in re.split(r"(\s+)", text):
        if len(token) <= max_chars or token.isspace():
            pieces.append(token)
            continue

        chunks = [token[index:index + max_chars] for index in range(0, len(token), max_chars)]
        pieces.append(" ".join(chunks))

    return "".join(pieces)


def _safe_multi_cell(
    pdf: FPDF,
    height: float,
    text: object,
    *,
    align: str = "L",
    reset_x: bool = True,
) -> None:
    """
    FPDF-safe multi_cell wrapper.

    Fixes:
    - current X position being left at the right edge after previous writes;
    - width=0 resolving to no available horizontal space;
    - long unbroken IDs/URLs/hashes causing brittle line breaking.
    """
    if reset_x:
        pdf.set_x(pdf.l_margin)

    width = _usable_page_width(pdf)
    value = _normalize_pdf_text(text)

    try:
        # fpdf2 accepts these keyword arguments and keeps the next write aligned.
        pdf.multi_cell(width, height, value, align=align, new_x="LMARGIN", new_y="NEXT")
    except TypeError:
        # Compatibility fallback for older PyFPDF/fpdf versions.
        pdf.multi_cell(width, height, value, align=align)
        pdf.set_x(pdf.l_margin)

    if reset_x:
        pdf.set_x(pdf.l_margin)


def _safe_ln(pdf: FPDF, height: float = 2) -> None:
    pdf.set_x(pdf.l_margin)
    pdf.ln(height)
    pdf.set_x(pdf.l_margin)


@dataclass(frozen=True)
class RenderedArtifact:
    filename: str
    path: str
    file_size_mb: float
    output_format: str
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


@dataclass(frozen=True)
class CompliancePreview:
    report: ComplianceMachineReadableReport
    human_review: HumanReviewRequirement
    preview_markdown: str


class ComplianceRenderError(RuntimeError):
    """Raised when compliance report rendering fails."""


class ComplianceRenderer:
    def __init__(self, artifacts_dir: Optional[str | Path] = None) -> None:
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else DEFAULT_ARTIFACTS_DIR
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.storage = LocalArtifactStorage()

    def render_variant(
        self,
        *,
        base_name: str,
        request_input: DocumentPayload | DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        report_variant: ComplianceReportVariant,
        documents: Sequence[EvidenceDocument],
        algorithm_version: Optional[str] = None,
    ) -> tuple[RenderedArtifact, ComplianceFileResult]:
        if report_variant == ComplianceReportVariant.machine_readable_report:
            artifact = self.render_machine_readable_json(base_name=base_name, report=report)
            result = build_compliance_file_result(
                filename=artifact.filename,
                output_format=ComplianceOutputFormat.json,
                file_size_mb=artifact.file_size_mb,
                report_variant=report_variant,
                storage_key=artifact.storage_key,
                download_url=artifact.download_url,
                algorithm_version=algorithm_version,
            )
            return artifact, result

        if report_variant == ComplianceReportVariant.human_readable_report:
            artifact = self.render_human_readable_pdf(base_name=base_name, report=report)
        elif report_variant == ComplianceReportVariant.annotated_source_output:
            artifact = self.render_annotated_source_pdf(
                base_name=base_name,
                request_input=request_input,
                report=report,
                documents=documents,
            )
        else:
            raise ComplianceRenderError(f"Unsupported compliance report variant: {report_variant}")

        result = build_compliance_file_result(
            filename=artifact.filename,
            output_format=ComplianceOutputFormat.pdf,
            file_size_mb=artifact.file_size_mb,
            report_variant=report_variant,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=algorithm_version,
        )
        return artifact, result

    def build_preview(self, report: ComplianceMachineReadableReport) -> CompliancePreview:
        lines = [
            "# Compliance Preview",
            f"- Jurisdiction: {report.jurisdiction.value}",
            f"- Sector packs: {', '.join(pack.value for pack in report.sector_packs)}",
            f"- Passed: {report.counts.passed}",
            f"- Failed: {report.counts.failed}",
            f"- Warning: {report.counts.warning}",
            f"- Missing: {report.counts.missing}",
            f"- Review required: {report.counts.review_required}",
            "",
            "## Rule Pack Versions",
        ]
        for item in report.rule_pack_versions:
            lines.append(f"- {item.sector_pack.value}: {item.version}")
        lines.append("")
        lines.append("## Findings")
        for item in report.rule_results:
            lines.append(f"- [{item.status.value}] {item.rule_id} — {item.title}")
        return CompliancePreview(
            report=report,
            human_review=HumanReviewRequirement(),
            preview_markdown="\n".join(lines),
        )

    def render_machine_readable_json(
        self,
        *,
        base_name: str,
        report: ComplianceMachineReadableReport,
    ) -> RenderedArtifact:
        target = self.artifacts_dir / f"{base_name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self._artifact_from_path(target, output_format="json")

    def render_human_readable_pdf(
        self,
        *,
        base_name: str,
        report: ComplianceMachineReadableReport,
    ) -> RenderedArtifact:
        target = self.artifacts_dir / f"{base_name}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)

        pdf = _configure_report_pdf()

        pdf.set_font(PDF_FONT_FAMILY, "B", 16)
        _safe_multi_cell(pdf, 8, "Compliance Report")

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 7, f"Jurisdiction: {report.jurisdiction.value}")
        _safe_multi_cell(pdf, 7, f"Sector packs: {', '.join(pack.value for pack in report.sector_packs)}")
        _safe_multi_cell(
            pdf,
            7,
            (
                f"Counts - passed: {report.counts.passed}, failed: {report.counts.failed}, "
                f"warning: {report.counts.warning}, missing: {report.counts.missing}, "
                f"review_required: {report.counts.review_required}"
            ),
        )
        _safe_multi_cell(pdf, 7, "Human review is required before reliance or final export.")

        if report.rule_pack_versions:
            _safe_ln(pdf, 1)
            pdf.set_font(PDF_FONT_FAMILY, "B", 12)
            _safe_multi_cell(pdf, 7, "Rule Pack Versions")
            pdf.set_font(PDF_FONT_FAMILY, size=11)

            for pack_version in report.rule_pack_versions:
                _safe_multi_cell(pdf, 6, f"- {pack_version.sector_pack.value}: {pack_version.version}")

        _safe_ln(pdf, 2)

        for item in report.rule_results:
            self._write_rule_result(pdf, item)

        pdf.output(str(target))
        return self._artifact_from_path(target, output_format="pdf")

    def render_annotated_source_pdf(
        self,
        *,
        base_name: str,
        request_input: DocumentPayload | DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        documents: Sequence[EvidenceDocument],
    ) -> RenderedArtifact:
        if isinstance(request_input, DocumentPayload):
            source_reference = request_input.filename
            source_path = Path(source_reference) if source_reference else None
            if source_path is not None and source_path.exists() and source_path.suffix.lower() == ".pdf":
                target = self.artifacts_dir / f"{base_name}.pdf"
                target.parent.mkdir(parents=True, exist_ok=True)
                self._annotate_pdf_source(source_path=source_path, target_path=target, rule_results=report.rule_results)
                return self._artifact_from_path(target, output_format="pdf")

        target = self.artifacts_dir / f"{base_name}.pdf"
        target.parent.mkdir(parents=True, exist_ok=True)

        pdf = _configure_report_pdf()

        pdf.set_font(PDF_FONT_FAMILY, "B", 16)
        _safe_multi_cell(pdf, 8, "Annotated Source Output (Evidence Overlay Report)")

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(
            pdf,
            7,
            (
                "A direct source-document annotation was not possible for this input shape, "
                "so this PDF lists evidence-linked findings by source document and page."
            ),
        )
        _safe_ln(pdf, 2)

        for evidence_document in documents:
            source_name = get_source_reference(
                request_input,
                source_document_index=evidence_document.source_document_index,
            )
            label = source_name or evidence_document.source_reference or f"doc[{evidence_document.source_document_index}]"

            pdf.set_font(PDF_FONT_FAMILY, "B", 11)
            _safe_multi_cell(pdf, 6, f"Source document {evidence_document.source_document_index}: {label}")

            pdf.set_font(PDF_FONT_FAMILY, size=10)
            _safe_multi_cell(pdf, 6, f"Input format: {evidence_document.input_format.value}")
            _safe_ln(pdf, 1)

        for item in report.rule_results:
            self._write_rule_result(pdf, item)

        pdf.output(str(target))
        return self._artifact_from_path(target, output_format="pdf")

    def _annotate_pdf_source(
        self,
        *,
        source_path: Path,
        target_path: Path,
        rule_results: Sequence[ComplianceRuleResult],
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with fitz.open(source_path) as pdf:
            for rule in rule_results:
                for evidence in rule.evidence_references:
                    if evidence.page_number is None:
                        continue

                    page_index = evidence.page_number - 1
                    if page_index < 0 or page_index >= len(pdf):
                        continue

                    page = pdf[page_index]
                    annotation_text = f"{rule.status.value.upper()}: {rule.rule_id} - {rule.title}"
                    target_phrase = (evidence.locator_text or "").strip()
                    added = False

                    if target_phrase:
                        try:
                            rectangles = page.search_for(target_phrase)
                        except Exception:
                            rectangles = []

                        for rect in rectangles[:2]:
                            page.add_highlight_annot(rect)
                            note = page.add_text_annot(rect.tl, annotation_text)
                            note.set_info(content=evidence.excerpt or annotation_text)
                            added = True

                    if not added:
                        note = page.add_text_annot(fitz.Point(36, 36), annotation_text)
                        note.set_info(content=evidence.excerpt or annotation_text)

            pdf.save(target_path, garbage=4, deflate=True)

    def _write_rule_result(self, pdf: FPDF, item: ComplianceRuleResult) -> None:
        pdf.set_font(PDF_FONT_FAMILY, "B", 12)
        _safe_multi_cell(pdf, 7, f"{item.rule_id} [{item.status.value}] - {item.title}")

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 6, item.summary)

        if item.evidence_references:
            for evidence in item.evidence_references:
                parts = [f"doc={evidence.source_document_index}"]

                if evidence.page_number is not None:
                    parts.append(f"page={evidence.page_number}")

                if evidence.section_label:
                    parts.append(f"section={evidence.section_label}")

                parts.append(f"locator={evidence.locator_text or '-'}")

                _safe_multi_cell(pdf, 6, "Evidence: " + "; ".join(parts))

                if evidence.excerpt:
                    _safe_multi_cell(pdf, 6, f"Excerpt: {evidence.excerpt}")
        else:
            _safe_multi_cell(pdf, 6, "Evidence: none captured")

        _safe_ln(pdf, 2)

    def _artifact_from_path(self, path: Path, *, output_format: str) -> RenderedArtifact:
        if not path.exists() or not path.is_file():
            raise ComplianceRenderError(f"Compliance artifact was not created: {path}")

        stored = self.storage.persist(
            source_file_path=str(path),
            artifact_name=path.name,
            content_type=guess_content_type(str(path)),
        )

        stored_path = Path(stored.stored_path)
        file_size_mb = round(stored_path.stat().st_size / (1024 * 1024), 4)

        return RenderedArtifact(
            filename=stored.original_artifact_name,
            path=stored.stored_path,
            file_size_mb=file_size_mb,
            output_format=output_format,
            storage_key=stored.storage_key,
            download_url=stored.download_url,
        )


__all__ = [
    "CompliancePreview",
    "ComplianceRenderError",
    "ComplianceRenderer",
    "DEFAULT_ARTIFACTS_DIR",
    "RenderedArtifact",
]
