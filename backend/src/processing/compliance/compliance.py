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
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Union
import hashlib

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
    from .evidence import EvidenceDocument, build_evidence_documents
    from .evaluators import build_counts, build_report_guidance, evaluate_rule_packs
    from .registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError
    from .renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact
except ImportError:  # pragma: no cover
    from backend.src.processing.compliance.evidence import EvidenceDocument, build_evidence_documents
    from backend.src.processing.compliance.evaluators import (
        build_counts,
        build_report_guidance,
        evaluate_rule_packs,
    )
    from backend.src.processing.compliance.registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError
    from backend.src.processing.compliance.renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact


MAX_COMPLIANCE_SEARCHABLE_CHARACTERS_PER_REQUEST = 25_000_000
MAX_COMPLIANCE_RULES_PER_REQUEST = 5_000
MAX_COMPLIANCE_SIGNALS_PER_REQUEST = 50_000


@dataclass(frozen=True)
class ComplianceConfig:
    algorithm_version: str = "compliance-engine-v1.1.0"
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

    def prepare(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> PreparedCompliance:
        req = validate_analyzer_request(request)
        return self._prepare(req)

    def preview(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> CompliancePreview:
        return self.prepare(request).preview

    def execute(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        prepared = self._prepare(req)
        return self.render_prepared(req, prepared)

    def render_prepared(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        prepared: PreparedCompliance,
        *,
        report_variant: ComplianceReportVariant | None = None,
    ) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        self._validate_engine_scope(req)

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

        loaded_packs = tuple(self.registry.load_request_rule_packs(payload))
        rule_count = sum(len(pack.rules) for pack in loaded_packs)
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
        quality_warnings = _build_quality_warnings(evidence_documents)
        report = ComplianceMachineReadableReport(
            jurisdiction=payload.jurisdiction,
            sector_packs=list(payload.sector_packs),
            rule_pack_versions=[
                RulePackVersion(
                    sector_pack=pack.sector_pack,
                    version=pack.version,
                    checksum_sha256=pack.checksum_sha256,
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
                    )
                    for document in evidence_documents
                ],
            ),
            quality_warnings=quality_warnings,
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
) -> list[str]:
    warnings: list[str] = []
    for document in evidence_documents:
        label = f"Document {document.source_document_index + 1} ({document.display_name})"
        if len(document.text) < 100:
            warnings.append(
                f"{label} contains very little searchable text; confirm that the upload is complete."
            )
        warnings.extend(f"{label}: {warning}" for warning in document.warnings)
    if any(document.ocr_used for document in evidence_documents):
        warnings.append(
            "OCR was used. Confirm names, dates, amounts, and clause wording against the original document."
        )
    return list(dict.fromkeys(warnings))


def _expected_response_input_format(request: AnalyzerRequest) -> str | Any:
    if isinstance(request.input, DocumentSetPayload):
        return "document_set"
    return request.input.metadata.input_format


__all__ = [
    "ComplianceConfig",
    "ComplianceEngine",
    "ComplianceExecution",
    "PreparedCompliance",
    "RuleRegistryError",
    "preview_compliance",
    "run_compliance",
]
