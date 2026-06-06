from __future__ import annotations

"""
Compliance engine for jupitAIx v1.

Purpose:
- own request validation, deterministic compliance evaluation, preview generation,
  artifact rendering, and response construction for the compliance feature outside analyzer.py
- stay aligned with schema.py, validation.py, extraction.py, and Product Contract v1
- support both single-document and document-set inputs

Design notes:
- stateless and deterministic
- scope is limited to configured jurisdiction and sector rule packs for v1
- findings are derived strictly from the provided documents only
- human review is always required before reliance or final export
- real regulatory content is loaded from versioned rule packs; this file is the engine,
  not the rule library itself
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

try:
    from backend.src.schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        ComplianceMachineReadableReport,
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
    from .evaluators import build_counts, evaluate_rule_packs
    from .registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError
    from .renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact
except ImportError:  # pragma: no cover
    from backend.src.processing.compliance.evidence import EvidenceDocument, build_evidence_documents
    from backend.src.processing.compliance.evaluators import build_counts, evaluate_rule_packs
    from backend.src.processing.compliance.registry import ComplianceRuleRegistry, LoadedRulePack, RuleRegistryError
    from backend.src.processing.compliance.renderers import CompliancePreview, ComplianceRenderer, RenderedArtifact


@dataclass(frozen=True)
class ComplianceConfig:
    algorithm_version: Optional[str] = None
    rules_root: Optional[str] = None
    artifact_base_dir: str = "artifacts/compliance"


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

    def preview(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> CompliancePreview:
        req = validate_analyzer_request(request)
        execution = self._prepare(req)
        return execution.preview

    def execute(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        prepared = self._prepare(req)

        base_name = f"compliance_{_timestamp_slug()}"
        artifact, result = self.renderer.render_variant(
            base_name=base_name,
            request_input=req.input,
            report=prepared.report,
            report_variant=prepared.payload.report_variant,
            documents=prepared.evidence_documents,
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

        return ComplianceExecution(
            report=prepared.report,
            preview=prepared.preview,
            response=validated,
            artifact=artifact,
            loaded_packs=prepared.loaded_packs,
            evidence_documents=prepared.evidence_documents,
        )

    def run(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> AnalyzerResponse:
        return self.execute(request).response

    def _prepare(self, request: AnalyzerRequest) -> "_PreparedCompliance":
        self._validate_engine_scope(request)
        payload = request.payload
        assert isinstance(payload, ComplianceRequest)

        evidence_documents = tuple(build_evidence_documents(request.input))
        loaded_packs = tuple(self.registry.load_request_rule_packs(payload))
        rule_results = evaluate_rule_packs(evidence_documents, loaded_packs)
        counts = build_counts(rule_results)

        report = ComplianceMachineReadableReport(
            jurisdiction=payload.jurisdiction,
            sector_packs=list(payload.sector_packs),
            rule_pack_versions=[
                RulePackVersion(sector_pack=pack.sector_pack, version=pack.version)
                for pack in loaded_packs
            ],
            counts=counts,
            rule_results=rule_results,
        )
        preview = self.renderer.build_preview(report)
        return _PreparedCompliance(
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


@dataclass(frozen=True)
class _PreparedCompliance:
    payload: ComplianceRequest
    report: ComplianceMachineReadableReport
    preview: CompliancePreview
    loaded_packs: tuple[LoadedRulePack, ...]
    evidence_documents: tuple[EvidenceDocument, ...]


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
    "RuleRegistryError",
    "preview_compliance",
    "run_compliance",
]
