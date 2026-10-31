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
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Sequence
import json
import os
import re
import time
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



_REPORT_LANGUAGE: ContextVar[str] = ContextVar("compliance_report_language", default="english")
_RENDER_DEADLINE: ContextVar[Optional[float]] = ContextVar("compliance_render_deadline", default=None)
_REPORT_REFERENCE: ContextVar[str] = ContextVar("compliance_report_reference", default="")

_REPORT_COPY_FR = {
    "Your compliance check": "Votre vérification de conformité",
    "Compliance Check Report": "Rapport de vérification de conformité",
    "Result": "Résultat", "Overall result": "Résultat global",
    "What to do next": "Prochaines étapes", "Summary": "Résumé",
    "Information found": "Information trouvée", "Possible problems": "Problèmes potentiels",
    "Warnings": "Avertissements", "Information not found": "Information introuvable",
    "Needs a person to review": "Vérification humaine nécessaire", "Checks": "Vérifications",
    "Rules used": "Règles utilisées", "Country/jurisdiction": "Pays/juridiction",
    "Rule packs": "Bibliothèques de règles", "Rule versions used": "Versions des règles utilisées",
    "Document quality notes": "Notes sur la qualité des documents",
    "Report reference": "Référence du rapport", "Generated": "Généré le", "Engine": "Moteur",
    "Sources": "Sources", "Source document": "Document source",
    "Input format": "Format d’entrée", "Requested focus areas": "Domaines demandés",
    "All available areas": "Tous les domaines disponibles",
    "Required core controls are always included": "Les contrôles communs obligatoires sont toujours inclus",
    "What to do": "Actions recommandées", "Where ReDOCX found it": "Origine de la preuve",
    "Supporting text": "Texte justificatif", "Check reference": "Référence de la vérification",
    "document": "document", "page": "page", "section": "section", "rule": "règle",
    "version": "version", "pack": "bibliothèque", "Next step": "Prochaine étape",
    "Legal sources supplied by the rule library": "Sources juridiques fournies par la bibliothèque de règles",
    "Coverage complete": "Couverture complète", "Yes": "Oui", "No": "Non",
    "Evaluated rules": "Règles évaluées", "Excluded by focus-area filter": "Exclues par le filtre",
    "Evidence Overlay Report": "Rapport de preuves",
    "Annotated working copy": "Copie de travail annotée",
    "Full findings, including missing evidence, are in": "Les résultats complets, y compris les preuves manquantes, figurent dans",
    "Review the original source to verify any digital signatures": "Utilisez le document original pour vérifier les signatures numériques",
    "No matching passage was found in the relevant uploaded source.": "Aucun passage correspondant n’a été trouvé dans la source concernée.",
    "This source is not a PDF; evidence-linked findings are listed below.": "Cette source n’est pas un PDF; les résultats liés aux preuves sont présentés ci-dessous.",
    "A qualified person must review the document before reliance.": "Une personne qualifiée doit vérifier le document avant son utilisation.",
}
_IDENTIFIER_FR = {
    "nigeria": "Nigéria", "us": "États-Unis", "uk": "Royaume-Uni", "sa": "Afrique du Sud",
    "canada": "Canada", "france": "France", "togo": "Togo", "ghana": "Ghana",
    "core_control_library": "Contrôles communs",
    "nigeria_core_control_library": "Contrôles communs",
    "accounting": "Comptabilité", "agriculture": "Agriculture", "aviation": "Aviation",
    "banking_and_fintech": "Banque et fintech", "energy_and_power": "Énergie",
    "health": "Santé", "insurance": "Assurance", "law_and_legal": "Droit et services juridiques",
    "legal_and_law": "Droit et services juridiques", "manufacturing": "Industrie manufacturière",
    "maritime_and_shipping": "Transport maritime", "media": "Médias", "mining": "Industrie minière",
    "ngo": "ONG", "oil_and_gas": "Pétrole et gaz",
    "payment_platforms_and_services": "Plateformes et services de paiement",
    "pharmaceuticals": "Industrie pharmaceutique", "sports": "Sport", "tech": "Technologie",
    "telecom": "Télécommunications", "privacy": "Protection des données personnelles",
    "cybersecurity": "Cybersécurité", "aml": "Lutte contre le blanchiment",
    "consumer_protection": "Protection des consommateurs",
    "public_sector_access_to_information": "Accès à l’information du secteur public",
    "licensing": "Licences", "registration": "Immatriculation",
    "sector_regulator_requirements": "Exigences du régulateur sectoriel",
}


def _t(text: str) -> str:
    return _REPORT_COPY_FR.get(text, text) if _REPORT_LANGUAGE.get() in {"fr", "french"} else text


def _localized_output(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        report = kwargs.get("report")
        if report is None and args and hasattr(args[0], "run_metadata"):
            report = args[0]
        if report is None:
            return method(self, *args, **kwargs)
        language = getattr(report.run_metadata, "system_language", "english")
        token = _REPORT_LANGUAGE.set(str(getattr(language, "value", language)))
        reference_token = _REPORT_REFERENCE.set(report.run_metadata.report_id)
        parent = _RENDER_DEADLINE.get()
        deadline_token = _RENDER_DEADLINE.set(parent if parent is not None else time.monotonic() + 60)
        try:
            return method(self, *args, **kwargs)
        finally:
            _REPORT_LANGUAGE.reset(token)
            _RENDER_DEADLINE.reset(deadline_token)
            _REPORT_REFERENCE.reset(reference_token)
    return wrapped


def _reliance_notice(report) -> str:
    if getattr(report, "localized_reliance_notice", None):
        return report.localized_reliance_notice
    if _REPORT_LANGUAGE.get() in {"fr", "french"}:
        return ("Ce rapport est une vérification documentaire fondée sur des preuves, et non un avis juridique "
                "ou une certification juridique. Une personne qualifiée doit confirmer les obligations applicables et le document final.")
    return report.reliance_notice


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

    pdf.add_font(PDF_FONT_FAMILY, "", str(regular_font))
    pdf.add_font(PDF_FONT_FAMILY, "B", str(bold_font))


class _ComplianceReportPDF(FPDF):
    def footer(self) -> None:
        self.set_y(-9)
        self.set_x(self.l_margin)
        self.set_font(PDF_FONT_FAMILY, size=8)
        label = "Page du rapport" if _REPORT_LANGUAGE.get() in {"fr", "french"} else "Report page"
        self.cell(0, 4, f"{_REPORT_REFERENCE.get()} | {label} {self.page_no()}", align="R")


def _configure_report_pdf() -> FPDF:
    """
    Create a report PDF with consistent margins, Unicode fonts, and automatic
    page breaking.
    """
    pdf = _ComplianceReportPDF()
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

    deadline = _RENDER_DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise ComplianceRenderError("Compliance report rendering exceeded its processing time limit.")
    if pdf.page_no() > 500:
        raise ComplianceRenderError("Compliance report exceeds 500 pages. Narrow the selected scope.")

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
    value = str(getattr(status, "value", status))
    if _REPORT_LANGUAGE.get() in {"fr", "french"}:
        return {
            "evidence_found": "Information trouvée", "risk_detected": "Problème potentiel",
            "warning": "À vérifier", "evidence_missing": "Information introuvable",
            "requires_review": "Vérification humaine nécessaire",
        }.get(value, "À vérifier")
    return {
        "evidence_found": "Information found", "risk_detected": "Possible problem",
        "warning": "Check this", "evidence_missing": "Information not found",
        "requires_review": "Needs a person to review",
    }.get(value, "Needs review")


def _friendly_overall_status(status: object) -> str:
    value = str(getattr(status, "value", status))
    if _REPORT_LANGUAGE.get() in {"fr", "french"}:
        return {
            "ready_for_final_review": "Prêt pour la vérification finale",
            "changes_recommended": "Modifications recommandées",
            "manual_review_needed": "Vérification humaine nécessaire",
        }.get(value, "Vérification humaine nécessaire")
    return {
        "ready_for_final_review": "Ready for final review",
        "changes_recommended": "Changes recommended",
        "manual_review_needed": "Manual review needed",
    }.get(value, "Manual review needed")


def _friendly_identifier(value: object) -> str:
    text = str(getattr(value, "value", value) or "").strip()
    if _REPORT_LANGUAGE.get() in {"fr", "french"} and text in _IDENTIFIER_FR:
        return _IDENTIFIER_FR[text]
    return {"us": "United States", "uk": "United Kingdom", "sa": "South Africa", "ngo": "NGO"}.get(
        text, text.replace("_", " ").title(),
    )


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

    @_localized_output
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

    @_localized_output
    def build_preview(self, report: ComplianceMachineReadableReport) -> CompliancePreview:
        lines = [
            f"# {_t('Your compliance check')}",
            f"{_t('Result')}: {_friendly_overall_status(report.overall_status)}",
            report.plain_language_summary or _t("A qualified person must review the document before reliance."),
            "", f"## {_t('What to do next')}",
        ]
        lines.extend(f"{index}. {action}" for index, action in enumerate(report.recommended_next_steps, start=1))
        lines.extend(["", f"## {_t('Summary')}"])
        for label, value in (
            ("Information found", report.counts.evidence_found), ("Possible problems", report.counts.risk_detected),
            ("Warnings", report.counts.warning), ("Information not found", report.counts.evidence_missing),
            ("Needs a person to review", report.counts.requires_review),
        ):
            lines.append(f"- {_t(label)}: {value}")
        lines.extend(["", f"## {_t('Checks')}"])
        for item in report.rule_results:
            lines.append(f"- {_friendly_rule_status(item.status)}: {item.title}")
            lines.append(f"  {item.plain_language_summary or item.summary}")
            lines.extend(f"  {_t('Next step')}: {action}" for action in item.recommended_actions)
        domains = ", ".join(_friendly_identifier(item) for item in report.regulatory_domains) or _t("All available areas")
        lines.extend([
            "", f"## {_t('Rules used')}",
            f"- {_t('Country/jurisdiction')}: {_friendly_identifier(report.jurisdiction)}",
            f"- {_t('Rule packs')}: " + ", ".join(_friendly_identifier(pack) for pack in report.sector_packs),
            f"- {_t('Requested focus areas')}: {domains}",
            f"- {_t('Required core controls are always included')}",
        ])
        for item in report.rule_pack_versions:
            lines.append(f"- {_friendly_identifier(item.sector_pack)}: {item.version}; SHA-256 {item.checksum_sha256}")
        if report.quality_warnings:
            lines.extend(["", f"## {_t('Document quality notes')}"])
            lines.extend(f"- {warning}" for warning in report.quality_warnings)
        lines.extend(["", f"{_t('Report reference')}: {report.run_metadata.report_id}", _reliance_notice(report)])
        return CompliancePreview(report=report, human_review=HumanReviewRequirement(), preview_markdown="\n".join(lines))

    @_localized_output
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

    @_localized_output
    def render_human_readable_pdf(self, *, base_name: str, report: ComplianceMachineReadableReport) -> RenderedArtifact:
        target = self.artifacts_dir / f"{base_name}.pdf"
        self._write_human_report_to_path(target, report)
        return self._artifact_from_path(target, output_format="pdf")

    @_localized_output
    def render_annotated_source_output(self, *, base_name: str, request_input: DocumentPayload | DocumentSetPayload,
                                       report: ComplianceMachineReadableReport, documents: Sequence[EvidenceDocument]) -> RenderedArtifact:
        if isinstance(request_input, DocumentSetPayload):
            return self.render_source_output_package(base_name=base_name, request_input=request_input, report=report, documents=documents)
        source_path = Path(request_input.filename) if request_input.filename else None
        if source_path is not None and source_path.is_file() and source_path.suffix.lower() == ".pdf":
            target = self.artifacts_dir / f"{base_name}.pdf"
            target.parent.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="redocx-compliance-annotated-") as temporary:
                annotated = Path(temporary) / "annotated-source.pdf"
                summary = Path(temporary) / "compliance-report.pdf"
                self._annotate_pdf_source(source_path=source_path, target_path=annotated,
                                          rule_results=report.rule_results, source_document_index=0)
                self._write_human_report_to_path(summary, report)
                with fitz.open(annotated) as output, fitz.open(summary) as findings:
                    output.insert_pdf(findings)
                    output.save(target, garbage=4, deflate=True)
            return self._artifact_from_path(target, output_format="pdf")
        return self.render_evidence_overlay_pdf(base_name=base_name, request_input=request_input, report=report, documents=documents)

    @_localized_output
    def render_source_output_package(self, *, base_name: str, request_input: DocumentSetPayload,
                                     report: ComplianceMachineReadableReport, documents: Sequence[EvidenceDocument]) -> RenderedArtifact:
        document_by_index = {document.source_document_index: document for document in documents}
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        target_zip = self.artifacts_dir / f"{base_name}.source-output-package.zip"
        with TemporaryDirectory(prefix="redocx-compliance-source-") as temporary:
            package_dir = Path(temporary)
            manifest: list[dict[str, str]] = []
            for index, source_document in enumerate(request_input.documents):
                evidence_document = document_by_index.get(index)
                source_name = evidence_document.display_name if evidence_document is not None else Path(
                    get_source_reference(request_input, source_document_index=index) or f"document-{index + 1}"
                ).name
                safe_stem = _safe_artifact_stem(source_name, fallback=f"document-{index + 1}")
                source_path = Path(source_document.filename) if source_document.filename else None
                if source_path is not None and source_path.is_file() and source_path.suffix.lower() == ".pdf":
                    target = package_dir / f"{index + 1:02d}-{safe_stem}.annotated-source.pdf"
                    self._annotate_pdf_source(source_path=source_path, target_path=target,
                        rule_results=report.rule_results, source_document_index=index,
                        report=report, companion_report="compliance-report.pdf")
                    kind = "annotated_source_pdf"
                else:
                    target = self._render_evidence_overlay_pdf_to_path(
                        target=package_dir / f"{index + 1:02d}-{safe_stem}.evidence-overlay.pdf",
                        request_input=request_input, report=report,
                        documents=[evidence_document] if evidence_document is not None else documents,
                        title=_t("Evidence Overlay Report"),
                        intro=_t("This source is not a PDF; evidence-linked findings are listed below."),
                    )
                    kind = "evidence_overlay_report"
                manifest.append({
                    "source": source_name, "kind": kind, "file": target.name,
                    "checksum_sha256": evidence_document.checksum_sha256 if evidence_document else "unavailable",
                })
            (package_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
            (package_dir / "compliance-report.json").write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False), encoding="utf-8",
            )
            self._write_human_report_to_path(package_dir / "compliance-report.pdf", report)
            with zipfile.ZipFile(target_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for item in sorted(package_dir.iterdir()):
                    if item.is_file():
                        archive.write(item, arcname=item.name)
        return self._artifact_from_path(target_zip, output_format="zip")

    @_localized_output
    def render_evidence_overlay_pdf(self, *, base_name: str, request_input: DocumentPayload | DocumentSetPayload,
                                    report: ComplianceMachineReadableReport, documents: Sequence[EvidenceDocument]) -> RenderedArtifact:
        target = self.artifacts_dir / f"{base_name}.evidence-overlay.pdf"
        self._write_human_report_to_path(target, report, title=_t("Evidence Overlay Report"))
        return self._artifact_from_path(target, output_format="pdf")

    @_localized_output
    def _render_evidence_overlay_pdf_to_path(self, *, target: Path, request_input: DocumentPayload | DocumentSetPayload,
                                           report: ComplianceMachineReadableReport, documents: Sequence[EvidenceDocument],
                                           title: str, intro: str) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        pdf = _configure_report_pdf()
        pdf.set_font(PDF_FONT_FAMILY, "B", 16)
        _safe_multi_cell(pdf, 8, title)
        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 7, intro)
        _safe_multi_cell(pdf, 6, f"{_t('Report reference')}: {report.run_metadata.report_id}")
        _safe_multi_cell(pdf, 6, _reliance_notice(report))
        for document in documents:
            _safe_multi_cell(pdf, 6, f"{_t('Source document')} {document.source_document_index + 1}: {document.display_name}")
            _safe_multi_cell(pdf, 6, f"{_t('Input format')}: {document.input_format.value}; SHA-256 {document.checksum_sha256}")
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
        report: Optional[ComplianceMachineReadableReport] = None,
        companion_report: Optional[str] = None,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)

        with fitz.open(source_path) as pdf:
            if report is not None and companion_report and len(pdf):
                notice = pdf[0].add_text_annot(fitz.Point(24, 24), _t("Annotated working copy"))
                notice.set_info(content=(
                    f"{_t('Report reference')}: {report.run_metadata.report_id}\n"
                    f"{_t('Full findings, including missing evidence, are in')} {companion_report}.\n"
                    f"{_reliance_notice(report)}\n"
                    f"{_t('Review the original source to verify any digital signatures')}"
                ))
            for rule in rule_results:
                deadline = _RENDER_DEADLINE.get()
                if deadline is not None and time.monotonic() >= deadline:
                    raise ComplianceRenderError("Compliance annotation rendering exceeded its processing time limit.")
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

    def _write_rule_result(self, pdf: FPDF, item: ComplianceRuleResult, *, source_filter: Optional[set[int]] = None) -> None:
        # Keep the rule heading and its short plain-language explanation together.
        if pdf.get_y() + 40 > pdf.h - pdf.b_margin:
            pdf.add_page()
        pdf.set_font(PDF_FONT_FAMILY, "B", 12)
        _safe_multi_cell(pdf, 7, f"{_friendly_rule_status(item.status)} - {item.title}")
        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 6, item.plain_language_summary or item.summary)
        if item.recommended_actions:
            pdf.set_font(PDF_FONT_FAMILY, "B", 10)
            _safe_multi_cell(pdf, 6, _t("What to do"))
            pdf.set_font(PDF_FONT_FAMILY, size=10)
            for index, action in enumerate(item.recommended_actions, start=1):
                _safe_multi_cell(pdf, 6, f"{index}. {action}")
        references = [reference for reference in item.evidence_references
                      if source_filter is None or reference.source_document_index in source_filter]
        for reference in references:
            parts = [f"{_t('document')} {reference.source_document_index + 1}"]
            if reference.page_number is not None:
                parts.append(f"{_t('page')} {reference.page_number}")
            if reference.section_label:
                parts.append(f"{_t('section')} {reference.section_label}")
            _safe_multi_cell(pdf, 6, f"{_t('Where ReDOCX found it')}: " + ", ".join(parts))
            if reference.excerpt:
                _safe_multi_cell(pdf, 6, f"{_t('Supporting text')}: {reference.excerpt}")
        if not references:
            _safe_multi_cell(pdf, 6, _t("No matching passage was found in the relevant uploaded source."))
        details = [f"{_t('rule')} {item.rule_id}", f"{_t('version')} {item.rule_version}"]
        if item.sector_pack is not None:
            details.append(f"{_t('pack')} {_friendly_identifier(item.sector_pack)}")
        _safe_multi_cell(pdf, 6, f"{_t('Check reference')}: " + "; ".join(details))
        if item.legal_sources:
            _safe_multi_cell(pdf, 6, _t("Legal sources supplied by the rule library"))
            for source in item.legal_sources:
                _safe_multi_cell(pdf, 6, source)
        _safe_ln(pdf, 2)


    def _write_human_report_to_path(self, target: Path, report: ComplianceMachineReadableReport, *, title: Optional[str] = None) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        pdf = _configure_report_pdf()
        pdf.set_font(PDF_FONT_FAMILY, "B", 16)
        _safe_multi_cell(pdf, 8, title or _t("Compliance Check Report"))
        pdf.set_font(PDF_FONT_FAMILY, size=11)
        _safe_multi_cell(pdf, 7, f"{_t('Overall result')}: {_friendly_overall_status(report.overall_status)}")
        if report.plain_language_summary:
            _safe_multi_cell(pdf, 7, report.plain_language_summary)
        _safe_multi_cell(pdf, 6, _reliance_notice(report))
        _safe_multi_cell(pdf, 6, f"{_t('Report reference')}: {report.run_metadata.report_id}")
        _safe_multi_cell(pdf, 6, f"{_t('Generated')}: {report.run_metadata.generated_at_iso}; {_t('Engine')}: {report.run_metadata.algorithm_version}")
        _safe_multi_cell(pdf, 6, f"{_t('Country/jurisdiction')}: {_friendly_identifier(report.jurisdiction)}")
        domains = ", ".join(_friendly_identifier(item) for item in report.regulatory_domains) or _t("All available areas")
        _safe_multi_cell(pdf, 6, f"{_t('Requested focus areas')}: {domains}")
        _safe_multi_cell(pdf, 6, _t("Required core controls are always included"))
        for label, value in (
            ("Information found", report.counts.evidence_found), ("Possible problems", report.counts.risk_detected),
            ("Warnings", report.counts.warning), ("Information not found", report.counts.evidence_missing),
            ("Needs a person to review", report.counts.requires_review),
        ):
            _safe_multi_cell(pdf, 6, f"{_t(label)}: {value}")
        for title, entries in (
            ("Document quality notes", report.quality_warnings),
            ("What to do next", report.recommended_next_steps),
        ):
            if entries:
                pdf.set_font(PDF_FONT_FAMILY, "B", 12)
                _safe_multi_cell(pdf, 7, _t(title))
                pdf.set_font(PDF_FONT_FAMILY, size=10)
                for index, entry in enumerate(entries, start=1):
                    _safe_multi_cell(pdf, 6, f"{index}. {entry}")
        pdf.set_font(PDF_FONT_FAMILY, "B", 12)
        _safe_multi_cell(pdf, 7, _t("Sources"))
        pdf.set_font(PDF_FONT_FAMILY, size=10)
        for source in report.run_metadata.source_documents:
            _safe_multi_cell(pdf, 6, f"{_t('Source document')} {source.source_document_index + 1}: {source.filename}")
            _safe_multi_cell(pdf, 6, f"SHA-256 {source.checksum_sha256}")
            _safe_multi_cell(pdf, 6, f"{_t('Coverage complete')}: {_t('Yes') if source.coverage_complete else _t('No')}")
        pdf.set_font(PDF_FONT_FAMILY, "B", 12)
        _safe_multi_cell(pdf, 7, _t("Rule versions used"))
        pdf.set_font(PDF_FONT_FAMILY, size=10)
        for pack in report.rule_pack_versions:
            _safe_multi_cell(pdf, 6, f"{_friendly_identifier(pack.sector_pack)}: {pack.version}; SHA-256 {pack.checksum_sha256}")
            _safe_multi_cell(pdf, 6, f"{_t('Evaluated rules')}: {', '.join(pack.evaluated_rule_ids)}")
            if pack.excluded_rule_ids:
                _safe_multi_cell(pdf, 6, f"{_t('Excluded by focus-area filter')}: {', '.join(pack.excluded_rule_ids)}")
        for item in report.rule_results:
            self._write_rule_result(pdf, item)
        pdf.output(str(target))
        return target

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
