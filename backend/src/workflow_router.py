from __future__ import annotations

"""
ReDOCX workflow router.

This module is the single top-level dispatcher for backend workflows. It keeps
FastAPI routes thin and prevents analyzer.py from becoming responsible for
PDF tools, e-signature, privacy, structured extraction, or compliance.

Routing ownership:
- Analyzer handles AI/document actions it already supports.
- PdfToolsService handles Combine/Split/Edit/Compress PDF.
- ESignatureService handles ReDOCX Sign workflows.
- process_privacy_action_and_persist handles Redaction/Data Masking.
- StructuredExtractionEngine handles Structured Extraction.
- ComplianceEngine handles Compliance.

The router is intentionally framework-agnostic:
- no FastAPI imports;
- no Auth0 imports;
- no database/session assumptions;
- no direct UploadFile handling.

API routes should:
1. build or receive an AnalyzerRequest;
2. pass it to WorkflowRouter.handle(...);
3. convert raised exceptions into HTTP responses.
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Union

try:  # Preferred in the deployed backend package.
    from backend.src.analyzer import Analyzer
    from backend.src.schema import (
        AddSignatureOperation,
        AnalyzerRequest,
        AnalyzerResponse,
        DocumentPayload,
        DocumentSetPayload,
        ESignatureAction,
        FeatureType,
    )
    from backend.src.validation import validate_analyzer_request, validate_analyzer_response

    from backend.src.pdf_tools_service import PdfToolsService
    from backend.src.esignature_service import ESignatureService

    from backend.src.processing.compliance.compliance import (
        ComplianceConfig,
        ComplianceEngine,
        ComplianceExecution,
        PreparedCompliance,
        preview_compliance,
        run_compliance,
    )
    from backend.src.processing.structured_extraction.structured_extraction import (
        StructuredExtractionConfig,
        StructuredExtractionEngine,
        run_structured_extraction,
        run_structured_extraction_with_preview,
    )
    from backend.src.processing.data_protection.orchestration import (
        ProtectedArtifactResult,
        process_privacy_action_and_persist,
    )
    from backend.src.processing.data_protection.client import (
        DEFAULT_DLP_LOCATION,
        DEFAULT_MIN_LIKELIHOOD,
        GoogleSDPClient,
    )
except ImportError:  # pragma: no cover - useful when placed under src/services.
    from .analyzer import Analyzer
    from .schema import (
        AddSignatureOperation,
        AnalyzerRequest,
        AnalyzerResponse,
        DocumentPayload,
        DocumentSetPayload,
        ESignatureAction,
        FeatureType,
    )
    from .validation import validate_analyzer_request, validate_analyzer_response

    from .pdf_tools_service import PdfToolsService
    from .esignature_service import ESignatureService

    from .processing.compliance.compliance import (
        ComplianceConfig,
        ComplianceEngine,
        ComplianceExecution,
        PreparedCompliance,
        preview_compliance,
        run_compliance,
    )
    from .processing.structured_extraction.structured_extraction import (
        StructuredExtractionConfig,
        StructuredExtractionEngine,
        run_structured_extraction,
        run_structured_extraction_with_preview,
    )
    from .processing.data_protection.orchestration import (
        ProtectedArtifactResult,
        process_privacy_action_and_persist,
    )
    from .processing.data_protection.client import (
        DEFAULT_DLP_LOCATION,
        DEFAULT_MIN_LIKELIHOOD,
        GoogleSDPClient,
    )


DocumentSourcePathResolver = Callable[[DocumentPayload], str | Path]
DownloadUrlBuilder = Callable[[str], str]


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


AI_ANALYZER_ACTIONS: set[FeatureType] = {
    FeatureType.convert,
    FeatureType.transcribe,
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.translate,
    FeatureType.explain,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
}

PDF_TOOL_ACTIONS: set[FeatureType] = {
    FeatureType.combine_pdf,
    FeatureType.split_pdf,
    FeatureType.edit_pdf,
    FeatureType.compress_pdf,
}

PRIVACY_ACTIONS: set[FeatureType] = {
    FeatureType.redact,
    FeatureType.data_mask,
}

STANDALONE_DOCUMENT_ACTIONS: set[FeatureType] = {
    FeatureType.structured_extract,
    FeatureType.compliance,
}

ALL_ROUTED_ACTIONS: set[FeatureType] = (
    AI_ANALYZER_ACTIONS
    | PDF_TOOL_ACTIONS
    | PRIVACY_ACTIONS
    | STANDALONE_DOCUMENT_ACTIONS
    | {FeatureType.e_signature}
)


@dataclass(frozen=True)
class WorkflowRouterConfig:
    """
    Configuration for workflow-level concerns.

    Privacy actions need Google Sensitive Data Protection configuration. PDF tools
    and e-signature are configured on their dedicated services.
    """

    privacy_output_dir: str = os.getenv("PRIVACY_OUTPUT_DIR", "outputs/privacy")
    google_sdp_project_id: Optional[str] = os.getenv("GOOGLE_SDP_PROJECT_ID") or None
    google_sdp_location: str = os.getenv("GOOGLE_SDP_LOCATION", DEFAULT_DLP_LOCATION)
    google_sdp_min_likelihood: str = os.getenv(
        "GOOGLE_SDP_MIN_LIKELIHOOD",
        DEFAULT_MIN_LIKELIHOOD,
    )
    attach_download_urls: bool = True


@dataclass(frozen=True)
class WorkflowExecution:
    """
    Optional richer execution result for routes that need side-channel artifacts.

    response:
        The schema-compliant AnalyzerResponse to return to the client.

    protected_artifact:
        Present only for redact/data_mask when the privacy orchestration persists
        a downloadable artifact.

    preview_payload / preview_rows:
        Present only when structured extraction preview mode is requested.
    """

    response: AnalyzerResponse
    protected_artifact: Optional[ProtectedArtifactResult] = None
    preview_payload: Optional[dict[str, Any]] = None
    preview_rows: Optional[list[dict[str, Any]]] = None
    preview_truncated: bool = False


class WorkflowRouter:
    """
    Top-level ReDOCX dispatcher.

    Use `handle(...)` for the common case where only AnalyzerResponse is needed.
    Use `execute(...)` if a route also needs privacy artifact metadata or
    structured-extraction preview payloads.
    """

    def __init__(
        self,
        *,
        config: Optional[WorkflowRouterConfig] = None,
        analyzer: Optional[Analyzer] = None,
        pdf_tools_service: Optional[PdfToolsService] = None,
        esignature_service: Optional[ESignatureService] = None,
        compliance_engine: Optional[ComplianceEngine] = None,
        structured_extraction_engine: Optional[StructuredExtractionEngine] = None,
        privacy_storage_backend: Optional[StorageBackend] = None,
        privacy_sdp_client: Optional[GoogleSDPClient] = None,
        document_source_path_resolver: Optional[DocumentSourcePathResolver] = None,
        download_url_builder: Optional[DownloadUrlBuilder] = None,
    ) -> None:
        self.config = config or WorkflowRouterConfig()
        self.analyzer = analyzer or Analyzer()
        self.pdf_tools_service = pdf_tools_service or PdfToolsService()
        self.esignature_service = esignature_service or ESignatureService()
        self.compliance_engine = compliance_engine or ComplianceEngine()
        self.structured_extraction_engine = (
            structured_extraction_engine or StructuredExtractionEngine()
        )
        self.privacy_storage_backend = privacy_storage_backend
        self.privacy_sdp_client = privacy_sdp_client
        self.document_source_path_resolver = document_source_path_resolver
        self.download_url_builder = download_url_builder or default_download_url_builder

    def handle(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        **context: Any,
    ) -> AnalyzerResponse:
        """
        Dispatch request and return only AnalyzerResponse.

        Context keyword arguments are passed to specialized handlers. This keeps
        the router framework-agnostic while still supporting signer metadata,
        privacy options, and preview mode.
        """
        return self.execute(request, **context).response

    def execute(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        *,
        # Privacy options
        privacy_source_path: Optional[str | Path] = None,
        custom_redactions: Optional[Sequence[str]] = None,
        ocr_languages: Optional[Sequence[str]] = None,
        google_sdp_project_id: Optional[str] = None,
        google_sdp_location: Optional[str] = None,
        google_sdp_min_likelihood: Optional[str] = None,
        google_sdp_client: Any | None = None,
        # E-signature options
        existing_state: Any | None = None,
        current_pdf_path: Optional[str | Path] = None,
        signer_email: Optional[str] = None,
        signer_signature: Optional[AddSignatureOperation] = None,
        field_values: Optional[Mapping[str, str]] = None,
        sender_email: Optional[str] = None,
        sender_name: Optional[str] = None,
        send_emails: bool = True,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        # Structured extraction options
        structured_preview: bool = False,
        structured_preview_rows_limit: int = 50,
        # Authenticated owner used to isolate asynchronous PDF compression jobs.
        pdf_job_owner_id: Optional[str] = None,
        # Authenticated owner metadata attached to persisted PDF edit artifacts.
        artifact_owner_user_id: Optional[str] = None,
        artifact_owner_organization_id: Optional[str] = None,
    ) -> WorkflowExecution:
        req = validate_analyzer_request(request)

        if req.action in AI_ANALYZER_ACTIONS:
            response = self._handle_analyzer_action(req)

        elif req.action in PDF_TOOL_ACTIONS:
            response = self._handle_pdf_tool_action(
                req,
                job_owner_id=pdf_job_owner_id,
                artifact_owner_user_id=artifact_owner_user_id,
                artifact_owner_organization_id=artifact_owner_organization_id,
            )

        elif req.action == FeatureType.e_signature:
            response = self._handle_esignature_action(
                req,
                existing_state=existing_state,
                current_pdf_path=current_pdf_path,
                signer_email=signer_email,
                signer_signature=signer_signature,
                field_values=field_values,
                sender_email=sender_email,
                sender_name=sender_name,
                send_emails=send_emails,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        elif req.action in PRIVACY_ACTIONS:
            privacy_execution = self._handle_privacy_action(
                req,
                source_path=privacy_source_path,
                custom_redactions=custom_redactions,
                ocr_languages=ocr_languages,
                project_id=google_sdp_project_id,
                location=google_sdp_location,
                min_likelihood=google_sdp_min_likelihood,
                client=google_sdp_client,
            )
            response = privacy_execution.analyzer_response
            response = self._finalize_response(response, request=req)
            return WorkflowExecution(
                response=response,
                protected_artifact=privacy_execution,
            )

        elif req.action == FeatureType.structured_extract:
            if structured_preview:
                execution = self.structured_extraction_engine.execute(req)
                response = self._finalize_response(execution.response, request=req)
                limit = max(1, int(structured_preview_rows_limit))
                preview_rows = execution.preview_rows[:limit]
                return WorkflowExecution(
                    response=response,
                    preview_payload=execution.preview_payload,
                    preview_rows=preview_rows,
                    preview_truncated=len(execution.preview_rows) > limit,
                )
            response = self.structured_extraction_engine.run(req)

        elif req.action == FeatureType.compliance:
            response = self.compliance_engine.run(req)

        else:  # pragma: no cover - guarded by schema enum + action sets
            raise ValueError(f"Unsupported workflow action: {req.action.value}")

        response = self._finalize_response(response, request=req)
        return WorkflowExecution(response=response)

    # ------------------------------------------------------------------
    # Handler dispatch
    # ------------------------------------------------------------------

    def _handle_analyzer_action(self, request: AnalyzerRequest) -> AnalyzerResponse:
        return self.analyzer.analyze(request)

    def _handle_pdf_tool_action(
        self,
        request: AnalyzerRequest,
        *,
        job_owner_id: Optional[str],
        artifact_owner_user_id: Optional[str],
        artifact_owner_organization_id: Optional[str],
    ) -> AnalyzerResponse:
        return self.pdf_tools_service.process(
            request,
            job_owner_id=job_owner_id,
            artifact_owner_user_id=artifact_owner_user_id,
            artifact_owner_organization_id=artifact_owner_organization_id,
        )

    def get_pdf_compression_job(self, *, job_id: str, owner_id: str):
        job = self.pdf_tools_service.get_compression_job(
            job_id=job_id,
            owner_id=owner_id,
        )
        if job.result is not None:
            self._attach_download_url_to_result(job.result)
        return job

    def _handle_esignature_action(
        self,
        request: AnalyzerRequest,
        *,
        existing_state: Any | None,
        current_pdf_path: Optional[str | Path],
        signer_email: Optional[str],
        signer_signature: Optional[AddSignatureOperation],
        field_values: Optional[Mapping[str, str]],
        sender_email: Optional[str],
        sender_name: Optional[str],
        send_emails: bool,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> AnalyzerResponse:
        return self.esignature_service.process(
            request,
            existing_state=existing_state,
            current_pdf_path=current_pdf_path,
            signer_email=signer_email,
            signer_signature=signer_signature,
            field_values=field_values,
            sender_email=sender_email,
            sender_name=sender_name,
            send_emails=send_emails,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _handle_privacy_action(
        self,
        request: AnalyzerRequest,
        *,
        source_path: Optional[str | Path],
        custom_redactions: Optional[Sequence[str]],
        ocr_languages: Optional[Sequence[str]],
        project_id: Optional[str],
        location: Optional[str],
        min_likelihood: Optional[str],
        client: Any | None,
    ) -> ProtectedArtifactResult:
        resolved_source_path = source_path or self._resolve_document_source_path(request)

        # If a fully configured SDP wrapper is injected, let the privacy
        # orchestration use it. Otherwise it will construct a Google client from
        # project/location/min_likelihood configuration.
        resolved_project_id = project_id or self.config.google_sdp_project_id
        resolved_location = location or self.config.google_sdp_location
        resolved_min_likelihood = min_likelihood or self.config.google_sdp_min_likelihood

        if self.privacy_sdp_client is None and not resolved_project_id:
            raise RuntimeError(
                "Google Sensitive Data Protection is not configured. "
                "Set GOOGLE_SDP_PROJECT_ID or inject privacy_sdp_client before "
                "using redact/data_mask workflows."
            )

        return process_privacy_action_and_persist(
            request,
            source_path=resolved_source_path,
            output_dir=self.config.privacy_output_dir,
            storage_backend=self.privacy_storage_backend,
            sdp=self.privacy_sdp_client,
            project_id=resolved_project_id,
            location=resolved_location,
            min_likelihood=resolved_min_likelihood,
            client=client,
            ocr_languages=ocr_languages,
            custom_redactions=custom_redactions,
        )

    # ------------------------------------------------------------------
    # Public preview helpers
    # ------------------------------------------------------------------

    def preview_structured_extraction(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        *,
        rows_limit: int = 50,
    ) -> dict[str, Any]:
        execution = self.execute(
            request,
            structured_preview=True,
            structured_preview_rows_limit=rows_limit,
        )
        return {
            "analyzer_response": execution.response.model_dump(mode="json"),
            "preview_payload": execution.preview_payload,
            "preview_rows": execution.preview_rows or [],
            "preview_truncated": execution.preview_truncated,
        }


    def execute_compliance(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
    ) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        if req.action != FeatureType.compliance:
            raise ValueError("execute_compliance only supports compliance requests.")
        execution = self.compliance_engine.execute(req)
        response = self._finalize_response(execution.response, request=req)
        return ComplianceExecution(
            report=execution.report,
            preview=execution.preview,
            response=response,
            artifact=execution.artifact,
            loaded_packs=execution.loaded_packs,
            evidence_documents=execution.evidence_documents,
        )

    def prepare_compliance(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
    ) -> PreparedCompliance:
        req = validate_analyzer_request(request)
        if req.action != FeatureType.compliance:
            raise ValueError("prepare_compliance only supports compliance requests.")
        return self.compliance_engine.prepare(req)

    def render_prepared_compliance(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        prepared: PreparedCompliance,
    ) -> ComplianceExecution:
        """Render the exact analysis shown in preview without evaluating twice."""
        req = validate_analyzer_request(request)
        if req.action != FeatureType.compliance:
            raise ValueError("render_prepared_compliance only supports compliance requests.")
        execution = self.compliance_engine.render_prepared(req, prepared)
        response = self._finalize_response(execution.response, request=req)
        return ComplianceExecution(
            report=execution.report,
            preview=execution.preview,
            response=response,
            artifact=execution.artifact,
            loaded_packs=execution.loaded_packs,
            evidence_documents=execution.evidence_documents,
        )

    def render_compliance_report(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        *,
        report: Any,
        evidence_documents: tuple[Any, ...],
        report_variant: Any,
    ) -> ComplianceExecution:
        req = validate_analyzer_request(request)
        if req.action != FeatureType.compliance:
            raise ValueError("render_compliance_report only supports compliance requests.")
        execution = self.compliance_engine.render_report(
            request=req,
            report=report,
            evidence_documents=evidence_documents,
            report_variant=report_variant,
        )
        response = self._finalize_response(execution.response, request=req)
        return ComplianceExecution(
            report=execution.report,
            preview=execution.preview,
            response=response,
            artifact=execution.artifact,
            loaded_packs=execution.loaded_packs,
            evidence_documents=execution.evidence_documents,
        )

    def preview_compliance(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
    ) -> Any:
        req = validate_analyzer_request(request)
        if req.action != FeatureType.compliance:
            raise ValueError("preview_compliance only supports compliance requests.")
        return self.compliance_engine.preview(req)

    # ------------------------------------------------------------------
    # Source/path helpers
    # ------------------------------------------------------------------

    def _resolve_document_source_path(self, request: AnalyzerRequest) -> str:
        if not isinstance(request.input, DocumentPayload):
            raise ValueError(
                f"{request.action.value} requires a single DocumentPayload source file "
                "for privacy processing."
            )

        if self.document_source_path_resolver is not None:
            path = Path(self.document_source_path_resolver(request.input)).expanduser()
            return str(self._require_existing_file(path))

        filename = (request.input.filename or "").strip()
        if not filename:
            raise ValueError(
                f"{request.action.value} requires DocumentPayload.filename to contain "
                "the persisted source file path, or document_source_path_resolver must "
                "be provided."
            )

        return str(self._require_existing_file(Path(filename).expanduser()))

    @staticmethod
    def _require_existing_file(path: str | Path) -> Path:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Source file not found: {resolved}")
        if not resolved.is_file():
            raise ValueError(f"Source path is not a file: {resolved}")
        return resolved

    # ------------------------------------------------------------------
    # Response finalization
    # ------------------------------------------------------------------

    def _finalize_response(
        self,
        response: AnalyzerResponse,
        *,
        request: AnalyzerRequest,
    ) -> AnalyzerResponse:
        if self.config.attach_download_urls:
            self._attach_download_urls(response)

        return validate_analyzer_response(response, request=request)

    def _attach_download_urls(self, response: AnalyzerResponse) -> None:
        """
        Best-effort download URL hydration for local artifact storage responses.

        This mirrors the route-layer behavior in the existing API while keeping
        this module independent of FastAPI.
        """
        self._attach_download_url_to_result(response.result)

        # TranscriptionResult carries a nested PDF artifact.
        pdf_artifact = getattr(response.result, "pdf_artifact", None)
        if pdf_artifact is not None:
            self._attach_download_url_to_result(pdf_artifact)

        # SplitPdfResult carries multiple output files and sometimes an archive.
        output_files = getattr(response.result, "output_files", None)
        if output_files:
            for item in output_files:
                self._attach_download_url_to_result(item)

        archive_file = getattr(response.result, "archive_file", None)
        if archive_file is not None:
            self._attach_download_url_to_result(archive_file)

        # Edit/e-signature results may carry previews.
        preview = getattr(response.result, "preview", None)
        if preview is not None:
            self._attach_preview_download_urls(preview)

        latest_preview = getattr(response.result, "latest_preview", None)
        if latest_preview is not None:
            self._attach_step_preview_download_urls(latest_preview)

        previews = getattr(response.result, "previews", None)
        if previews:
            for item in previews:
                self._attach_step_preview_download_urls(item)

        signed_pdf = getattr(response.result, "signed_pdf", None)
        if signed_pdf is not None:
            self._attach_download_url_to_result(signed_pdf)

        audit_certificate = getattr(response.result, "audit_certificate", None)
        if audit_certificate is not None:
            self._attach_download_url_to_result(audit_certificate)

    def _attach_preview_download_urls(self, preview: Any) -> None:
        self._attach_download_url_to_result(preview)

    def _attach_step_preview_download_urls(self, step_preview: Any) -> None:
        preview_pdf = getattr(step_preview, "preview_pdf", None)
        if preview_pdf is not None:
            self._attach_download_url_to_result(preview_pdf)

    def _attach_download_url_to_result(self, result: Any) -> None:
        storage_key = getattr(result, "storage_key", None)
        download_url = getattr(result, "download_url", None)

        if (
            isinstance(storage_key, str)
            and storage_key.strip()
            and not download_url
            and hasattr(result, "download_url")
        ):
            result.download_url = self.download_url_builder(storage_key)


def default_download_url_builder(storage_key: str) -> str:
    """
    Default route-compatible artifact URL builder.

    It preserves the existing analyzer route convention:
        /api/analyzer/artifacts/<storage-key>
    """
    key = storage_key.strip().replace("\\", "/")
    key = key.removeprefix("/api/analyzer/artifacts/")
    key = key.removeprefix("/api/v1/analyzer/artifacts/")
    key = key.removeprefix("/artifacts/")
    key = key.removeprefix("artifacts/")
    return f"/api/analyzer/artifacts/{key}"


__all__ = [
    "AI_ANALYZER_ACTIONS",
    "PDF_TOOL_ACTIONS",
    "PRIVACY_ACTIONS",
    "STANDALONE_DOCUMENT_ACTIONS",
    "ALL_ROUTED_ACTIONS",
    "WorkflowExecution",
    "WorkflowRouter",
    "WorkflowRouterConfig",
    "default_download_url_builder",
]
