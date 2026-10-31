from __future__ import annotations

"""
Compliance engine for ReDOCX v1.

Purpose:
- own request validation, deterministic compliance screening, preview generation,
  artifact rendering, and response construction outside analyzer.py
- stay aligned with schema.py, validation.py, extraction.py, and Product Contract v1
- support both single-document and document-set inputs

Design notes:
- stateless and deterministic at the engine layer
- scope is limited to configured jurisdiction and sector rule packs for v1
- findings are derived strictly from the provided documents only
- human review is always required before reliance or final export
- real regulatory content is loaded from versioned rule packs; this file is the engine,
  not the rule library itself
"""

from dataclasses import dataclass, replace
from contextvars import ContextVar
from functools import wraps
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Union
import hashlib
from pathlib import Path

try:
    from backend.src.schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        ComplianceMachineReadableReport,
        ComplianceRunMetadata,
        ComplianceSourceDocument,
        ComplianceReportVariant,
        ComplianceRequest,
        DocumentPayload,
        DocumentSetPayload,
        FeatureType,
        HumanReviewRequirement,
        OutputPolicy,
        RulePackVersion,
    )
    from backend.src.validation import validate_analyzer_request, validate_analyzer_response
except ImportError:  # pragma: no cover
    from backend.src.schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        ComplianceMachineReadableReport,
        ComplianceRunMetadata,
        ComplianceSourceDocument,
        ComplianceReportVariant,
        ComplianceRequest,
        DocumentPayload,
        DocumentSetPayload,
        FeatureType,
        HumanReviewRequirement,
        OutputPolicy,
        RulePackVersion,
    )
    from backend.src.validation import validate_analyzer_request, validate_analyzer_response

try:
    from .evidence import EvidenceDocument, EvidenceExtractionError, build_evidence_documents
    from .evaluators import build_counts, build_report_guidance, evaluate_rule_packs
    from .registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError, _validate_legal_review
    from .renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact
except ImportError:  # pragma: no cover
    from backend.src.processing.compliance.evidence import EvidenceDocument, EvidenceExtractionError, build_evidence_documents
    from backend.src.processing.compliance.evaluators import (
        build_counts,
        build_report_guidance,
        evaluate_rule_packs,
    )
    from backend.src.processing.compliance.registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError, _validate_legal_review
    from backend.src.processing.compliance.renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact


MAX_COMPLIANCE_SEARCHABLE_CHARACTERS_PER_REQUEST = 25_000_000
MAX_COMPLIANCE_RULES_PER_REQUEST = 5_000
MAX_COMPLIANCE_SIGNALS_PER_REQUEST = 50_000

_COMPLIANCE_WORK_GATE = threading.BoundedSemaphore(2)
_COMPLIANCE_WORK_ACTIVE: ContextVar[bool] = ContextVar("compliance_work_active", default=False)


def _bounded_compliance_work(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        if _COMPLIANCE_WORK_ACTIVE.get():
            return method(*args, **kwargs)
        if not _COMPLIANCE_WORK_GATE.acquire(timeout=5):
            raise EvidenceExtractionError("Compliance processing capacity limit reached. Please try again.")
        token = _COMPLIANCE_WORK_ACTIVE.set(True)
        try:
            return method(*args, **kwargs)
        finally:
            _COMPLIANCE_WORK_ACTIVE.reset(token)
            _COMPLIANCE_WORK_GATE.release()
    return wrapped


@dataclass(frozen=True)
class ComplianceConfig:
    algorithm_version: str = "compliance-engine-v1.2.0"
    rules_root: Optional[str] = None
    artifact_base_dir: str = "artifacts/compliance"


@dataclass(frozen=True)
class PreparedCompliance:
    payload: ComplianceRequest
    report: ComplianceMachineReadableReport
    preview: CompliancePreview
    loaded_packs: tuple[LoadedRulePack, ...]
    evidence_documents: tuple[EvidenceDocument, ...]


@dataclass(frozen=True)
class ComplianceExecution:
    report: ComplianceMachineReadableReport
    preview: CompliancePreview
    response: AnalyzerResponse
    artifact: RenderedArtifact
    loaded_packs: tuple[LoadedRulePack, ...]
    evidence_documents: tuple[EvidenceDocument, ...]


class ComplianceEngine:
    def __init__(self, config: Optional[ComplianceConfig] = None) -> None:
        self.config = config or ComplianceConfig()
        self.registry = ComplianceRuleRegistry(rules_root=self.config.rules_root)
        self.renderer = ComplianceRenderer(artifacts_dir=self.config.artifact_base_dir)

    @_bounded_compliance_work
    def prepare(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> PreparedCompliance:
        req = validate_analyzer_request(request)
        return self._prepare(req)

    def preview(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> CompliancePreview:
        return self.prepare(request).preview

    @_bounded_compliance_work
    def execute(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        prepared = self._prepare(req)
        return self.render_prepared(req, prepared)

    @_bounded_compliance_work
    def render_prepared(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        prepared: PreparedCompliance,
        *,
        report_variant: ComplianceReportVariant | None = None,
    ) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        self._validate_engine_scope(req)

        if req.payload.model_copy(update={"report_variant": prepared.payload.report_variant}) != prepared.payload:
            raise ValueError("The prepared Compliance analysis does not match this request.")
        _validate_report_sources(req, prepared.report, prepared.evidence_documents)

        variant = report_variant or prepared.payload.report_variant
        payload = prepared.payload
        if variant != payload.report_variant:
            payload = payload.model_copy(update={"report_variant": variant})
            req = req.model_copy(update={"payload": payload})
            prepared = replace(prepared, payload=payload)

        return self.render_report(
            request=req,
            report=prepared.report,
            evidence_documents=prepared.evidence_documents,
            report_variant=variant,
            loaded_packs=prepared.loaded_packs,
            preview=prepared.preview,
        )

    @_bounded_compliance_work
    def render_report(
        self,
        *,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        report: ComplianceMachineReadableReport,
        evidence_documents: tuple[EvidenceDocument, ...],
        report_variant: ComplianceReportVariant,
        loaded_packs: tuple[LoadedRulePack, ...] = (),
        preview: CompliancePreview | None = None,
    ) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        self._validate_engine_scope(req)

        payload = req.payload
        assert isinstance(payload, ComplianceRequest)
        _validate_report_sources(req, report, evidence_documents)
        if report_variant != payload.report_variant:
            payload = payload.model_copy(update={"report_variant": report_variant})
            req = req.model_copy(update={"payload": payload})

        base_name = f"compliance_{report.run_metadata.report_id}"
        artifact, result = self.renderer.render_variant(
            base_name=base_name,
            request_input=req.input,
            report=report,
            report_variant=report_variant,
            documents=evidence_documents,
            algorithm_version=self.config.algorithm_version,
        )

        response = AnalyzerResponse(
            action=FeatureType.compliance,
            input_format=_expected_response_input_format(req),
            policy=req.policy,
            system_language=req.system_language,
            detected_language=None,
            output_language=None,
            result=result,
            human_review=HumanReviewRequirement(),
        )
        validated = validate_analyzer_response(response, request=req)

        resolved_preview = preview or self.renderer.build_preview(report)
        return ComplianceExecution(
            report=report,
            preview=resolved_preview,
            response=validated,
            artifact=artifact,
            loaded_packs=loaded_packs,
            evidence_documents=evidence_documents,
        )

    def run(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> AnalyzerResponse:
        return self.execute(request).response

    def _prepare(self, request: AnalyzerRequest) -> PreparedCompliance:
        self._validate_engine_scope(request)
        payload = request.payload
        assert isinstance(payload, ComplianceRequest)

        # Validate deployed rules before spending time parsing/OCRing sources.
        loaded_packs = tuple(self.registry.load_request_rule_packs(payload))
        rule_count = sum(len(pack.rules) for pack in loaded_packs)
        if not rule_count:
            raise RuleRegistryError("The selected Compliance scope contains no evaluated rules.")
        signal_count = sum(
            len(rule.evaluation.get(field, []) or [])
            for pack in loaded_packs
            for rule in pack.rules
            for field in (
                "signals",
                "required_signals",
                "optional_signals",
                "prohibited_signals",
            )
        )
        if rule_count > MAX_COMPLIANCE_RULES_PER_REQUEST:
            raise RuleRegistryError(
                "The selected compliance packs exceed the per-run rule safety limit."
            )
        if signal_count > MAX_COMPLIANCE_SIGNALS_PER_REQUEST:
            raise RuleRegistryError(
                "The selected compliance packs exceed the per-run signal safety limit."
            )
        evidence_documents = tuple(build_evidence_documents(request.input))
        empty_documents = [
            document.source_document_index + 1
            for document in evidence_documents
            if not document.text.strip()
        ]
        if empty_documents:
            indexes = ", ".join(str(index) for index in empty_documents)
            raise ValueError(
                "Compliance screening stopped because reliable searchable text "
                f"could not be extracted from document(s): {indexes}. Upload a "
                "clearer file or a searchable PDF so missing evidence is not reported incorrectly."
            )
        total_searchable_characters = sum(
            len(document.text) for document in evidence_documents
        )
        if total_searchable_characters > MAX_COMPLIANCE_SEARCHABLE_CHARACTERS_PER_REQUEST:
            raise ValueError(
                "The document set contains too much searchable text for one reliable "
                "compliance run. Split it into smaller, logically related sets."
            )

        rule_results = evaluate_rule_packs(
            evidence_documents,
            loaded_packs,
            system_language=request.system_language,
        )
        counts = build_counts(rule_results)
        overall_status, plain_language_summary, recommended_next_steps = (
            build_report_guidance(
                counts,
                rule_results,
                system_language=request.system_language,
            )
        )

        generated_at = datetime.now(timezone.utc)
        report_id = _build_report_id(
            generated_at=generated_at,
            evidence_documents=evidence_documents,
            loaded_packs=loaded_packs,
        )
        quality_warnings = _build_quality_warnings(evidence_documents, system_language=request.system_language)
        french = getattr(request.system_language, "value", request.system_language) in {"fr", "french"}
        if any(not document.coverage_complete for document in evidence_documents):
            if getattr(overall_status, "value", overall_status) == "ready_for_final_review":
                overall_status = "manual_review_needed"
                plain_language_summary = (
                    "Certains contenus n’ont pas pu être évalués. Une personne qualifiée doit vérifier la couverture du document."
                    if french else
                    "Some source content could not be assessed. A qualified person must review the document coverage."
                )
        unapproved_rules = False
        for pack in loaded_packs:
            try:
                _validate_legal_review(pack.metadata)
            except RuleRegistryError:
                unapproved_rules = True
                quality_warnings.append(
                    f"{pack.sector_pack.value}: " + (
                        "la validation juridique actuelle de cette version des règles n’est pas confirmée; vérifiez son applicabilité."
                        if french else
                        "current legal approval of this rule-pack version is not verified; confirm its applicability."
                    )
                )
        if unapproved_rules and getattr(overall_status, "value", overall_status) == "ready_for_final_review":
            overall_status = "manual_review_needed"
            plain_language_summary = (
                "Les informations recherchées sont présentes, mais une personne qualifiée doit confirmer la validation et l’applicabilité des règles."
                if french else
                "The requested information was found, but a qualified person must confirm rule-library approval and applicability."
            )
        report = ComplianceMachineReadableReport(
            jurisdiction=payload.jurisdiction,
            sector_packs=list(payload.sector_packs),
            rule_pack_versions=[
                RulePackVersion(
                    sector_pack=pack.sector_pack,
                    version=pack.version,
                    checksum_sha256=pack.checksum_sha256,
                    evaluated_rule_ids=[rule.rule_id for rule in pack.rules],
                    excluded_rule_ids=list(pack.excluded_rule_ids),
                    total_rule_count=pack.total_rule_count or len(pack.rules),
                )
                for pack in loaded_packs
            ],
            counts=counts,
            rule_results=rule_results,
            overall_status=overall_status,
            plain_language_summary=plain_language_summary,
            recommended_next_steps=recommended_next_steps,
            run_metadata=ComplianceRunMetadata(
                report_id=report_id,
                generated_at_iso=generated_at.isoformat(),
                algorithm_version=self.config.algorithm_version,
                system_language=request.system_language,
                source_documents=[
                    ComplianceSourceDocument(
                        source_document_index=document.source_document_index,
                        filename=document.display_name,
                        input_format=document.input_format,
                        file_size_mb=document.file_size_mb,
                        checksum_sha256=document.checksum_sha256,
                        ocr_used=document.ocr_used,
                        extracted_character_count=len(document.text),
                        pages_with_text=len(document.pages),
                        page_count=document.page_count,
                        coverage_complete=document.coverage_complete,
                    )
                    for document in evidence_documents
                ],
            ),
            quality_warnings=quality_warnings,
            regulatory_domains=list(payload.regulatory_domains),
            localized_reliance_notice=(
                "Ce rapport est une vérification documentaire fondée sur des preuves, et non un avis juridique ou une certification juridique. "
                "Une personne qualifiée doit confirmer les obligations applicables et le document final."
                if french else None
            ),
        )
        preview = self.renderer.build_preview(report)
        return PreparedCompliance(
            payload=payload,
            report=report,
            preview=preview,
            loaded_packs=loaded_packs,
            evidence_documents=evidence_documents,
        )

    def _validate_engine_scope(self, request: AnalyzerRequest) -> None:
        if request.action != FeatureType.compliance:
            raise ValueError("ComplianceEngine only handles the compliance action.")
        if not isinstance(request.payload, ComplianceRequest):
            raise ValueError("compliance requires ComplianceRequest payload.")
        if not isinstance(request.policy, OutputPolicy):
            raise ValueError("compliance requires OutputPolicy.")


def run_compliance(request: Union[AnalyzerRequest, Mapping[str, Any]]) -> AnalyzerResponse:
    return ComplianceEngine().run(request)


def preview_compliance(request: Union[AnalyzerRequest, Mapping[str, Any]]) -> CompliancePreview:
    return ComplianceEngine().preview(request)


def _build_report_id(
    *,
    generated_at: datetime,
    evidence_documents: tuple[EvidenceDocument, ...],
    loaded_packs: tuple[LoadedRulePack, ...],
) -> str:
    identity = "|".join(
        [
            *(document.checksum_sha256 for document in evidence_documents),
            *(pack.checksum_sha256 for pack in loaded_packs),
            generated_at.isoformat(),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    timestamp = generated_at.strftime("%Y%m%dT%H%M%S%fZ")
    return f"cmp_{timestamp}_{digest}"


def _build_quality_warnings(
    evidence_documents: tuple[EvidenceDocument, ...],
    *, system_language: Any = "english",
) -> list[str]:
    french = getattr(system_language, "value", system_language) in {"fr", "french"}
    warnings: list[str] = []
    labels = {
        "image_text_unreadable": ("Image text could not be assessed.", "Le texte de l’image n’a pas pu être évalué."),
        "docx_image_text_unreadable": ("An embedded image could not be read.", "Une image intégrée n’a pas pu être lue."),
        "docx_image_format_unassessed": ("An embedded image format could not be assessed.", "Le format d’une image intégrée n’a pas pu être évalué."),
        "docx_chart_unassessed": ("Embedded chart content requires manual review.", "Le contenu d’un graphique intégré doit être vérifié manuellement."),
        "pdf_signature_original_required": (
            "A signature field is present. Verify digital signatures using the original source, not an annotated working copy.",
            "Un champ de signature est présent. Vérifiez les signatures numériques dans le document original, et non dans une copie de travail annotée.",
        ),
    }
    for document in evidence_documents:
        label = f"Document {document.source_document_index + 1} ({document.display_name})"
        if len(document.text) < 100:
            warnings.append(label + (
                " contient très peu de texte exploitable; vérifiez que le document est complet."
                if french else " contains very little searchable text; verify that the document is complete."
            ))
        for warning in document.warnings:
            if warning.startswith("pdf_image_text_unreadable:"):
                page = warning.split(":", 1)[1]
                message = f"le contenu image de la page {page} n’a pas pu être évalué." if french else f"image content on page {page} could not be assessed."
            else:
                pair = labels.get(warning)
                message = pair[1 if french else 0] if pair else warning
            warnings.append(f"{label}: {message}")
    if any(document.ocr_used for document in evidence_documents):
        warnings.append(
            "L’OCR a été utilisé. Vérifiez les noms, dates, montants et clauses dans le document original."
            if french else "OCR was used. Verify names, dates, amounts and clause wording against the original document."
        )
    return list(dict.fromkeys(warnings))


def _expected_response_input_format(request: AnalyzerRequest) -> str | Any:
    if isinstance(request.input, DocumentSetPayload):
        return "document_set"
    return request.input.metadata.input_format



def _validate_report_sources(request: AnalyzerRequest, report: ComplianceMachineReadableReport,
                             documents: tuple[EvidenceDocument, ...]) -> None:
    """Bind an internal prepared report to its scope and unchanged source bytes."""
    payload = request.payload
    if not isinstance(payload, ComplianceRequest):
        raise ValueError("Compliance report scope does not match the request.")
    if (report.jurisdiction != payload.jurisdiction
            or report.sector_packs != payload.sector_packs
            or report.regulatory_domains != payload.regulatory_domains
            or report.run_metadata.system_language != request.system_language):
        raise ValueError("Compliance report scope does not match the request. Run the check again.")
    inputs = list(request.input.documents) if isinstance(request.input, DocumentSetPayload) else [request.input]
    if len(inputs) != len(documents) or len(inputs) != len(report.run_metadata.source_documents):
        raise ValueError("Compliance report sources do not match the request.")
    for index, (source, evidence, recorded) in enumerate(zip(inputs, documents, report.run_metadata.source_documents)):
        reference = source.filename
        if evidence.source_reference and reference:
            if Path(evidence.source_reference).resolve() != Path(reference).resolve():
                raise ValueError("Compliance report sources do not match the request.")
        if reference and Path(reference).is_file():
            digest = hashlib.sha256()
            with Path(reference).open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            checksum = digest.hexdigest()
        else:
            checksum = hashlib.sha256((source.text or "").strip().encode("utf-8")).hexdigest()
        if (evidence.source_document_index != index or recorded.source_document_index != index
                or checksum != evidence.checksum_sha256 or checksum != recorded.checksum_sha256):
            raise ValueError("A source changed after the Compliance analysis. Run the check again.")


__all__ = [
    "ComplianceConfig",
    "ComplianceEngine",
    "ComplianceExecution",
    "PreparedCompliance",
    "RuleRegistryError",
    "preview_compliance",
    "run_compliance",
]
