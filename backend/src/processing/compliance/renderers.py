from __future__ import annotations

"""
Renderers for compliance outputs.

Supported outputs:
- machine-readable JSON report
- human-readable PDF report
- annotated source output PDF (single PDF) or source-output ZIP package (document set)

Rendering note:
FPDF's ``multi_cell(0, ...)`` is sensitive to the current cursor X position.
If the cursor is left near the right margin after a previous write, width=0 can
resolve to zero usable width and raise:
    FPDFException: Not enough horizontal space to render a single character

All report text in this module is therefore written through ``_safe_multi_cell``.
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Sequence
import json
import os
import re
import zipfile

import fitz  # PyMuPDF
from fpdf import FPDF

try:
    from backend.src.schema import (
        ComplianceFileResult,
        ComplianceMachineReadableReport,
        ComplianceOutputFormat,
        ComplianceReportVariant,
        ComplianceRuleResult,
        DocumentPayload,
        DocumentSetPayload,
        HumanReviewRequirement,
    )
    from backend.src.validation import build_compliance_file_result
except ImportError:  # pragma: no cover
    from backend.src.schema import (
        ComplianceFileResult,
        ComplianceMachineReadableReport,
        ComplianceOutputFormat,
        ComplianceReportVariant,
        ComplianceRuleResult,
        DocumentPayload,
        DocumentSetPayload,
        HumanReviewRequirement,
    )
    from backend.src.validation import build_compliance_file_result

try:
    from backend.src.storage.artifacts import LocalArtifactStorage, guess_content_type
except ImportError:  # pragma: no cover
    from backend.src.storage.artifacts import LocalArtifactStorage, guess_content_type

try:
    from .evidence import EvidenceDocument, get_source_reference
except ImportError:  # pragma: no cover
    from backend.src.processing.compliance.evidence import EvidenceDocument, get_source_reference


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


def _friendly_rule_status(status: object) -> str:
    value = getattr(status, "value", status)
    return {
        "evidence_found": "Information found",
        "risk_detected": "Possible problem",
        "warning": "Check this",
        "evidence_missing": "Information not found",
        "requires_review": "Needs a person to review",
    }.get(str(value), "Needs review")


def _friendly_overall_status(status: object) -> str:
    value = getattr(status, "value", status)
    return {
        "ready_for_final_review": "Ready for final review",
        "changes_recommended": "Changes recommended",
        "manual_review_needed": "Manual review needed",
    }.get(str(value), "Manual review needed")


def _friendly_identifier(value: object) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "").strip()
    labels = {
        "us": "United States",
        "uk": "United Kingdom",
        "sa": "South Africa",
        "ngo": "NGO",
    }
    return labels.get(text, text.replace("_", " ").title()) if text else ""


def _safe_artifact_stem(value: object, *, fallback: str) -> str:
    filename = Path(str(value or "")).name
    stem = Path(filename).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("._-")
    return cleaned or fallback


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
            output_format = ComplianceOutputFormat.pdf
        elif report_variant == ComplianceReportVariant.annotated_source_output:
            artifact = self.render_annotated_source_output(
                base_name=base_name,
                request_input=request_input,
                report=report,
                documents=documents,
            )
            output_format = (
                ComplianceOutputFormat.zip
                if artifact.output_format == "zip"
                else ComplianceOutputFormat.pdf
            )
        else:
            raise ComplianceRenderError(f"Unsupported compliance report variant: {report_variant}")

        result = build_compliance_file_result(
            filename=artifact.filename,
            output_format=output_format,
            file_size_mb=artifact.file_size_mb,
            report_variant=report_variant,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=algorithm_version,
        )
        return artifact, result

    def build_preview(self, report: ComplianceMachineReadableReport) -> CompliancePreview:
        lines = [
            "# Your compliance check",
            f"Result: {_friendly_overall_status(report.overall_status)}",
            report.plain_language_summary
            or "A qualified person should review the document before it is relied on.",
            "",
            "## What to do next",
        ]
        for index, action in enumerate(report.recommended_next_steps, start=1):
            lines.append(f"{index}. {action}")
        lines.extend(
            [
                "",
                "## Summary",
                f"- Information found: {report.counts.evidence_found}",
                f"- Possible problems: {report.counts.risk_detected}",
                f"- Warnings: {report.counts.warning}",
                f"- Information not found: {report.counts.evidence_missing}",
                f"- Needs a person to review: {report.counts.requires_review}",
                "",
                "## Checks",
            ]
        )
        for item in report.rule_results:
            lines.append(f"- {_friendly_rule_status(item.status)}: {item.title}")
            if item.plain_language_summary:
                lines.append(f"  {item.plain_language_summary}")
            for action in item.recommended_actions:
                lines.append(f"  Next step: {action}")
        lines.extend(
            [
                "",
                "## Rules used",
                f"- Country/jurisdiction: {_friendly_identifier(report.jurisdiction)}",
                "- Rule packs: "
                + ", ".join(_friendly_identifier(pack) for pack in report.sector_packs),
            ]
        )
        for item in report.rule_pack_versions:
            lines.append(
                f"- {_friendly_identifier(item.sector_pack)} rules, version {item.version} "
                f"(checksum {item.checksum_sha256[:12]}…)"
            )
        if report.quality_warnings:
            lines.extend(["", "## Document quality notes"])
            lines.extend(f"- {warning}" for warning in report.quality_warnings)
        lines.extend(
            [
                "",
                f"Report reference: {report.run_metadata.report_id}",
                report.reliance_notice,
            ]
        )
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
        _safe_multi_cell(pdf, 8, "Compliance Check Report")

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(
            pdf,
            7,
            f"Overall result: {_friendly_overall_status(report.overall_status)}",
        )
        if report.plain_language_summary:
            _safe_multi_cell(pdf, 7, report.plain_language_summary)
        _safe_multi_cell(
            pdf,
            7,
            f"Country/jurisdiction: {_friendly_identifier(report.jurisdiction)}",
        )
        _safe_multi_cell(
            pdf,
            7,
            "Rules used: "
            + ", ".join(_friendly_identifier(pack) for pack in report.sector_packs),
        )
        _safe_multi_cell(
            pdf,
            7,
            (
                f"Summary - information found: {report.counts.evidence_found}; "
                f"possible problems: {report.counts.risk_detected}; "
                f"warnings: {report.counts.warning}; "
                f"information not found: {report.counts.evidence_missing}; "
                f"needs a person to review: {report.counts.requires_review}"
            ),
        )
        _safe_multi_cell(
            pdf,
            7,
            report.reliance_notice,
        )
        _safe_multi_cell(pdf, 6, f"Report reference: {report.run_metadata.report_id}")
        _safe_multi_cell(
            pdf,
            6,
            f"Generated: {report.run_metadata.generated_at_iso}; engine: "
            f"{report.run_metadata.algorithm_version}",
        )

        if report.quality_warnings:
            _safe_ln(pdf, 1)
            pdf.set_font(PDF_FONT_FAMILY, "B", 12)
            _safe_multi_cell(pdf, 7, "Document quality notes")
            pdf.set_font(PDF_FONT_FAMILY, size=10)
            for warning in report.quality_warnings:
                _safe_multi_cell(pdf, 6, f"- {warning}")

        if report.recommended_next_steps:
            _safe_ln(pdf, 1)
            pdf.set_font(PDF_FONT_FAMILY, "B", 12)
            _safe_multi_cell(pdf, 7, "What to do next")
            pdf.set_font(PDF_FONT_FAMILY, size=11)
            for index, action in enumerate(report.recommended_next_steps, start=1):
                _safe_multi_cell(pdf, 6, f"{index}. {action}")

        if report.rule_pack_versions:
            _safe_ln(pdf, 1)
            pdf.set_font(PDF_FONT_FAMILY, "B", 12)
            _safe_multi_cell(pdf, 7, "Rule versions used")
            pdf.set_font(PDF_FONT_FAMILY, size=11)

            for pack_version in report.rule_pack_versions:
                _safe_multi_cell(
                    pdf,
                    6,
                    f"- {_friendly_identifier(pack_version.sector_pack)}: "
                    f"{pack_version.version}; SHA-256 {pack_version.checksum_sha256}",
                )

        _safe_ln(pdf, 2)

        for item in report.rule_results:
            self._write_rule_result(pdf, item)

        pdf.output(str(target))
        return self._artifact_from_path(target, output_format="pdf")

    def render_annotated_source_output(
        self,
        *,
        base_name: str,
        request_input: DocumentPayload | DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        documents: Sequence[EvidenceDocument],
    ) -> RenderedArtifact:
        """
        Product behavior:
        - single PDF input => annotated source PDF;
        - single DOCX/image input => evidence overlay PDF;
        - document set => ZIP package containing annotated PDFs for PDF sources and
          an evidence overlay report for DOCX/image sources.
        """
        if isinstance(request_input, DocumentSetPayload):
            return self.render_source_output_package(
                base_name=base_name,
                request_input=request_input,
                report=report,
                documents=documents,
            )

        source_reference = request_input.filename
        source_path = Path(source_reference) if source_reference else None
        if source_path is not None and source_path.is_file() and source_path.suffix.lower() == ".pdf":
            target = self.artifacts_dir / f"{base_name}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            self._annotate_pdf_source(
                source_path=source_path,
                target_path=target,
                rule_results=report.rule_results,
                source_document_index=0,
            )
            return self._artifact_from_path(target, output_format="pdf")

        return self.render_evidence_overlay_pdf(
            base_name=base_name,
            request_input=request_input,
            report=report,
            documents=documents,
        )

    def render_source_output_package(
        self,
        *,
        base_name: str,
        request_input: DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        documents: Sequence[EvidenceDocument],
    ) -> RenderedArtifact:
        temporary_package = TemporaryDirectory(prefix="redocx-compliance-source-")
        package_dir = Path(temporary_package.name)

        manifest: list[dict[str, str]] = []
        document_by_index = {doc.source_document_index: doc for doc in documents}

        for index, source_document in enumerate(request_input.documents):
            source_name = Path(
                get_source_reference(request_input, source_document_index=index)
                or f"document-{index + 1}"
            ).name
            safe_stem = _safe_artifact_stem(source_name, fallback=f"document-{index + 1}")
            source_path = Path(source_document.filename) if source_document.filename else None
            evidence_document = document_by_index.get(index)

            if source_path is not None and source_path.is_file() and source_path.suffix.lower() == ".pdf":
                target = package_dir / f"{index + 1:02d}-{safe_stem}.annotated-source.pdf"
                self._annotate_pdf_source(
                    source_path=source_path,
                    target_path=target,
                    rule_results=report.rule_results,
                    source_document_index=index,
                )
                manifest.append({
                    "source": source_name,
                    "kind": "annotated_source_pdf",
                    "file": target.name,
                    "checksum_sha256": (
                        evidence_document.checksum_sha256 if evidence_document else "unavailable"
                    ),
                })
                continue

            overlay = self._render_evidence_overlay_pdf_to_path(
                target=package_dir / f"{index + 1:02d}-{safe_stem}.evidence-overlay.pdf",
                request_input=request_input,
                report=report,
                documents=[evidence_document] if evidence_document is not None else documents,
                title="Evidence Overlay Report",
                intro=(
                    "This source is not a PDF, so ReDOCX generated an evidence overlay report "
                    "instead of annotating the original file directly."
                ),
            )
            manifest.append({
                "source": source_name,
                "kind": "evidence_overlay_report",
                "file": overlay.name,
                "checksum_sha256": (
                    evidence_document.checksum_sha256 if evidence_document else "unavailable"
                ),
            })

        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

        target_zip = self.artifacts_dir / f"{base_name}.source-output-package.zip"
        with zipfile.ZipFile(target_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(package_dir.iterdir()):
                if item.is_file():
                    archive.write(item, arcname=item.name)

        temporary_package.cleanup()

        return self._artifact_from_path(target_zip, output_format="zip")

    def render_evidence_overlay_pdf(
        self,
        *,
        base_name: str,
        request_input: DocumentPayload | DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        documents: Sequence[EvidenceDocument],
    ) -> RenderedArtifact:
        target = self.artifacts_dir / f"{base_name}.evidence-overlay.pdf"
        self._render_evidence_overlay_pdf_to_path(
            target=target,
            request_input=request_input,
            report=report,
            documents=documents,
            title="Evidence Overlay Report",
            intro=(
                "This input is not a directly annotatable PDF source, so this PDF lists "
                "evidence-linked findings by source document and page."
            ),
        )
        return self._artifact_from_path(target, output_format="pdf")

    def _render_evidence_overlay_pdf_to_path(
        self,
        *,
        target: Path,
        request_input: DocumentPayload | DocumentSetPayload,
        report: ComplianceMachineReadableReport,
        documents: Sequence[EvidenceDocument],
        title: str,
        intro: str,
    ) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)

        pdf = _configure_report_pdf()

        pdf.set_font(PDF_FONT_FAMILY, "B", 16)
        _safe_multi_cell(pdf, 8, title)

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 7, intro)
        _safe_ln(pdf, 2)

        for evidence_document in documents:
            label = evidence_document.display_name

            pdf.set_font(PDF_FONT_FAMILY, "B", 11)
            _safe_multi_cell(pdf, 6, f"Source document {evidence_document.source_document_index}: {label}")

            pdf.set_font(PDF_FONT_FAMILY, size=10)
            _safe_multi_cell(pdf, 6, f"Input format: {evidence_document.input_format.value}")
            _safe_ln(pdf, 1)

        source_filter = {document.source_document_index for document in documents}
        for item in report.rule_results:
            self._write_rule_result(pdf, item, source_filter=source_filter)

        pdf.output(str(target))
        return target

    def _annotate_pdf_source(
        self,
        *,
        source_path: Path,
        target_path: Path,
        rule_results: Sequence[ComplianceRuleResult],
        source_document_index: int,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with fitz.open(source_path) as pdf:
            for rule in rule_results:
                for evidence in rule.evidence_references:
                    if evidence.source_document_index != source_document_index:
                        continue
                    if evidence.page_number is None:
                        continue

                    page_index = evidence.page_number - 1
                    if page_index < 0 or page_index >= len(pdf):
                        continue

                    page = pdf[page_index]
                    annotation_text = (
                        f"{_friendly_rule_status(rule.status)}: {rule.title}"
                    )
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

    def _write_rule_result(
        self,
        pdf: FPDF,
        item: ComplianceRuleResult,
        *,
        source_filter: Optional[set[int]] = None,
    ) -> None:
        pdf.set_font(PDF_FONT_FAMILY, "B", 12)
        _safe_multi_cell(
            pdf,
            7,
            f"{_friendly_rule_status(item.status)} - {item.title}",
        )

        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 6, item.plain_language_summary or item.summary)

        if item.recommended_actions:
            pdf.set_font(PDF_FONT_FAMILY, "B", 10)
            _safe_multi_cell(pdf, 6, "What to do")
            pdf.set_font(PDF_FONT_FAMILY, size=10)
            for index, action in enumerate(item.recommended_actions, start=1):
                _safe_multi_cell(pdf, 6, f"{index}. {action}")

        evidence_references = [
            evidence
            for evidence in item.evidence_references
            if source_filter is None or evidence.source_document_index in source_filter
        ]
        if evidence_references:
            for evidence in evidence_references:
                parts = [f"document {evidence.source_document_index + 1}"]

                if evidence.page_number is not None:
                    parts.append(f"page {evidence.page_number}")

                if evidence.section_label:
                    parts.append(f"section {evidence.section_label}")

                _safe_multi_cell(
                    pdf,
                    6,
                    "Where ReDOCX found it: " + ", ".join(parts),
                )

                if evidence.excerpt:
                    _safe_multi_cell(pdf, 6, f"Supporting text: {evidence.excerpt}")
        else:
            _safe_multi_cell(
                pdf,
                6,
                "Supporting text: ReDOCX did not find a matching passage in the relevant uploaded source.",
            )

        rule_details = [f"rule {item.rule_id}", f"version {item.rule_version}"]
        if item.sector_pack is not None:
            rule_details.append(f"pack {_friendly_identifier(item.sector_pack)}")
        _safe_multi_cell(pdf, 6, "Check reference: " + "; ".join(rule_details))

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
