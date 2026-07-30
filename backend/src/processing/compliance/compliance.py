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

try:
    from backend.src.schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        ComplianceMachineReadableReport,
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


@dataclass(frozen=True)
class ComplianceConfig:
    algorithm_version: Optional[str] = None
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

        base_name = f"compliance_{_timestamp_slug()}"
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
        loaded_packs = tuple(self.registry.load_request_rule_packs(payload))
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

        report = ComplianceMachineReadableReport(
            jurisdiction=payload.jurisdiction,
            sector_packs=list(payload.sector_packs),
            rule_pack_versions=[
                RulePackVersion(sector_pack=pack.sector_pack, version=pack.version)
                for pack in loaded_packs
            ],
            counts=counts,
            rule_results=rule_results,
            overall_status=overall_status,
            plain_language_summary=plain_language_summary,
            recommended_next_steps=recommended_next_steps,
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


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
