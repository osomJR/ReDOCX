from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Union
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import TypeAdapter, ValidationError

from backend.auth0_dependencies import AuthenticatedUser, get_current_user
from backend.errors import to_http_exception
from backend.rate_limiter.dependencies import rate_limit_for_feature
from backend.upload import (
    UploadError,
    build_uploaded_document_payload,
    build_uploaded_media_payload,
    save_pdf_tool_upload,
)
from backend.batch_processing import (
    BATCH_UPLOAD_LIMITS_BY_PLAN,
    BatchUploadPolicy,
    BatchUploadPolicyError,
    require_batch_upload_entitlement,
)

from backend.src.extraction import build_inline_text_payload, build_pdf_input_artifact_for_action
from backend.src.processing.conversion.convert import convert_document
from backend.src.processing.data_protection.data_masking.data_mask import preview_data_mask_candidates
from backend.src.processing.data_protection.orchestration import ProtectedArtifactResult
from backend.src.processing.data_protection.redaction.redact import preview_redaction_candidates
from backend.src.processing.compliance.registry import RuleRegistryError

from backend.src.schema import (
    AddSignatureOperation,
    AnalyzerRequest,
    AnalyzerResponse,
    AnswerGenerationRequest,
    CombinePdfRequest,
    ComplianceJurisdiction,
    ComplianceRegulatoryDomain,
    ComplianceReportVariant,
    ComplianceRequest,
    ComplianceSectorPack,
    CompressPdfRequest,
    ConversionOutputFormat,
    ConversionRequest,
    DataMaskingRequest,
    ESignatureRequest,
    EditPdfRequest,
    ExplanationRequest,
    FeatureType,
    GrammarCorrectionRequest,
    MediaType,
    OutputPolicy,
    PdfCompressionLevel,
    PdfEditOperation,
    PdfPageRange,
    PdfSplitMode,
    QuestionGenerationRequest,
    RedactionMaskingDocumentType,
    RedactionRequest,
    SensitiveDataType,
    SplitPdfRequest,
    StructuredDataOutputFormat,
    StructuredExtractionDocumentClass,
    StructuredExtractionRequest,
    StructuredExtractionResultShape,
    SummarizationRequest,
    SystemLanguage,
    TranscriptionRequest,
    TranslationRequest,
)
from backend.src.workflow_router import WorkflowRouter
from backend.src.storage.artifacts import LocalArtifactStorage, guess_content_type


API_V1_ANALYZER_PREFIX = "/analyzer"

router = APIRouter(prefix=API_V1_ANALYZER_PREFIX, tags=["analyzer-v1"])


# -----------------------------------------------------------------------------
# Policy / error helpers
# -----------------------------------------------------------------------------

TRANSFORMED_ACTIONS = {
    FeatureType.convert,
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.translate,
    FeatureType.transcribe,
    FeatureType.redact,
    FeatureType.data_mask,
    FeatureType.combine_pdf,
    FeatureType.split_pdf,
    FeatureType.edit_pdf,
    FeatureType.compress_pdf,
    FeatureType.e_signature,
}

GENERATED_ACTIONS = {
    FeatureType.explain,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
    FeatureType.structured_extract,
    FeatureType.compliance,
}

PDF_UPLOAD_DIR = Path(os.getenv("PDF_UPLOAD_DIR", "uploads/pdf_tools"))
DEFAULT_GOOGLE_SDP_LOCATION = os.getenv("GOOGLE_SDP_LOCATION", "global")


def _policy_for_action(action: FeatureType) -> OutputPolicy:
    if action in TRANSFORMED_ACTIONS:
        return OutputPolicy(structure_preservation=True)
    if action in GENERATED_ACTIONS:
        return OutputPolicy(structure_preservation=False)
    raise ValueError(f"Unsupported action: {action}")


def _bad_request(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "error": "invalid_request",
            "message": message,
        },
    )


def _service_unavailable(message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "service_unavailable",
            "message": message,
        },
    )


def _google_sdp_project_id() -> str:
    project_id = os.getenv("GOOGLE_SDP_PROJECT_ID", "").strip()
    if not project_id:
        raise _service_unavailable(
            "Google Sensitive Data Protection is not configured. "
            "Set GOOGLE_SDP_PROJECT_ID for redaction and data masking."
        )
    return project_id


def _download_url_for_storage_key(storage_key: str | None) -> str | None:
    if not isinstance(storage_key, str) or not storage_key.strip():
        return None

    key = storage_key.strip().replace("\\", "/")
    key = key.removeprefix("/api/analyzer/artifacts/")
    key = key.removeprefix("/api/v1/analyzer/artifacts/")
    key = key.removeprefix("/artifacts/")
    key = key.removeprefix("artifacts/")
    return f"/api/v1/analyzer/artifacts/{key}"


workflow_router = WorkflowRouter(download_url_builder=_download_url_for_storage_key)


def _run_request(
    request: Union[AnalyzerRequest, Mapping[str, Any]],
    **context: Any,
) -> AnalyzerResponse:
    try:
        return workflow_router.handle(request, **context)
    except HTTPException:
        raise
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValidationError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc
    except TypeError as exc:
        raise _bad_request(str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc


def _run_workflow_execution(
    request: AnalyzerRequest,
    **context: Any,
):
    try:
        return workflow_router.execute(request, **context)
    except HTTPException:
        raise
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except RuleRegistryError as exc:
        raise _bad_request(str(exc)) from exc
    except ValidationError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc
    except TypeError as exc:
        raise _bad_request(str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc


# -----------------------------------------------------------------------------
# Input builders
# -----------------------------------------------------------------------------


def _build_document_input(
    *,
    action: FeatureType,
    file: UploadFile | None,
    text: str | None,
):
    has_file = file is not None
    has_text = text is not None and text.strip() != ""

    if has_file and has_text:
        raise _bad_request("Provide either file or text, not both.")
    if not has_file and not has_text:
        raise _bad_request("Either file or text is required.")

    try:
        if has_file:
            return build_uploaded_document_payload(action=action, upload=file)  # type: ignore[arg-type]
        return build_inline_text_payload(text=text.strip())  # type: ignore[union-attr]
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc


def _save_upload_to_disk(upload: UploadFile, *, subdir: str, default_name: str) -> Path:
    try:
        return save_pdf_tool_upload(
            upload,
            subdir=subdir,
            default_name=default_name,
            base_dir=PDF_UPLOAD_DIR,
        )
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc


def _build_single_pdf_input(action: FeatureType, file: UploadFile):
    saved_path = _save_upload_to_disk(
        file,
        subdir=action.value,
        default_name="document.pdf",
    )
    try:
        return build_pdf_input_artifact_for_action(
            action=action,
            file_path=saved_path,
            storage_key=str(saved_path),
            mime_type=file.content_type or "application/pdf",
        )
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc


def _build_pdf_set_input(action: FeatureType, files: list[UploadFile]):
    saved_paths = [
        _save_upload_to_disk(
            file,
            subdir=action.value,
            default_name=f"document-{index}.pdf",
        )
        for index, file in enumerate(files, start=1)
    ]
    try:
        return build_pdf_input_artifact_for_action(
            action=action,
            file_paths=saved_paths,
            storage_keys=[str(path) for path in saved_paths],
            mime_types=[file.content_type or "application/pdf" for file in files],
        )
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc


def _loads_json(value: str | None, *, default: Any = None) -> Any:
    if value is None or not str(value).strip():
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise _bad_request(f"Invalid JSON: {exc.msg}") from exc


def _parse_int_list(value: str | None) -> list[int]:
    if value is None or not value.strip():
        return []
    raw = value.strip()
    loaded = _loads_json(raw, default=None) if raw.startswith("[") else None
    if loaded is not None:
        if not isinstance(loaded, list):
            raise _bad_request("selected_pages must be a JSON array or comma-separated integers.")
        return [int(item) for item in loaded]
    return [int(item.strip()) for item in raw.split(",") if item.strip()]


def _parse_page_ranges(value: str | None) -> list[PdfPageRange]:
    if value is None or not value.strip():
        return []

    raw = value.strip()
    if raw.startswith("["):
        loaded = _loads_json(raw, default=[])
        if not isinstance(loaded, list):
            raise _bad_request("page_ranges must be a JSON array or a comma-separated range string.")
        return [PdfPageRange.model_validate(item) for item in loaded]

    ranges: list[PdfPageRange] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if "-" in text:
            start, end = text.split("-", 1)
            ranges.append(PdfPageRange(start_page=int(start.strip()), end_page=int(end.strip())))
        else:
            page = int(text)
            ranges.append(PdfPageRange(start_page=page, end_page=page))
    return ranges


def _parse_edit_operations(operations_json: str) -> list[PdfEditOperation]:
    loaded = _loads_json(operations_json, default=[])
    return TypeAdapter(list[PdfEditOperation]).validate_python(loaded)


def _parse_esignature_request(payload_json: str) -> ESignatureRequest:
    loaded = _loads_json(payload_json, default={})
    if not isinstance(loaded, dict):
        raise _bad_request("payload_json must be a JSON object.")
    loaded.setdefault("feature", FeatureType.e_signature.value)
    return ESignatureRequest.model_validate(loaded)


def _parse_optional_signature(value: str | None) -> AddSignatureOperation | None:
    if value is None or not value.strip():
        return None
    loaded = _loads_json(value, default=None)
    return TypeAdapter(AddSignatureOperation).validate_python(loaded)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


def _user_email(user: AuthenticatedUser | None) -> str | None:
    if user is None:
        return None
    for attr in ("email", "user_email", "sub"):
        value = getattr(user, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# -----------------------------------------------------------------------------
# Download URL / artifact serialization helpers
# -----------------------------------------------------------------------------


def _ensure_download_url(response: AnalyzerResponse) -> AnalyzerResponse:
    result = response.result

    storage_key = getattr(result, "storage_key", None)
    download_url = getattr(result, "download_url", None)
    if storage_key and not download_url and hasattr(result, "download_url"):
        result.download_url = _download_url_for_storage_key(storage_key)

    pdf_artifact = getattr(result, "pdf_artifact", None)
    if pdf_artifact is not None:
        pdf_storage_key = getattr(pdf_artifact, "storage_key", None)
        pdf_download_url = getattr(pdf_artifact, "download_url", None)
        if pdf_storage_key and not pdf_download_url and hasattr(pdf_artifact, "download_url"):
            pdf_artifact.download_url = _download_url_for_storage_key(pdf_storage_key)

    return response


def _artifact_storage_download_candidates() -> list[LocalArtifactStorage]:
    candidate_base_dirs: list[str | None] = [None]

    configured_root = os.getenv("ARTIFACT_STORAGE_DIR", "").strip()
    if configured_root:
        candidate_base_dirs.append(configured_root)
        configured_path = Path(configured_root)
        if configured_path.name != "ai_documents":
            candidate_base_dirs.append(str(configured_path / "ai_documents"))

    candidate_base_dirs.append("artifacts/ai_documents")

    storages: list[LocalArtifactStorage] = []
    seen: set[str] = set()
    for base_dir in candidate_base_dirs:
        storage = LocalArtifactStorage(base_dir=base_dir)
        resolved_base_dir = str(storage.base_dir.resolve())
        if resolved_base_dir in seen:
            continue
        seen.add(resolved_base_dir)
        storages.append(storage)
    return storages


def _privacy_source_path(input_payload: Any) -> str:
    filename = getattr(input_payload, "filename", None)
    if not isinstance(filename, str) or not filename.strip():
        raise _bad_request("Uploaded privacy document is missing its persisted file path.")
    return filename.strip()


def _run_privacy_request(
    request: AnalyzerRequest,
    *,
    source_path: str,
    custom_redactions: list[str] | None = None,
) -> ProtectedArtifactResult:
    try:
        execution = workflow_router.execute(
            request,
            privacy_source_path=source_path,
            custom_redactions=custom_redactions,
        )
        if execution.protected_artifact is None:
            raise RuntimeError("Privacy workflow did not return a protected artifact.")
        return execution.protected_artifact
    except HTTPException as exc:
        raise to_http_exception(exc) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc


def _build_docx_preview_artifact(processed: ProtectedArtifactResult) -> dict[str, Any] | None:
    original_name = processed.artifact.original_artifact_name.lower()
    if not original_name.endswith(".docx"):
        return None

    preview = convert_document(
        input_format="docx",
        output_format="pdf",
        source_reference=processed.artifact.stored_path,
        source_name_hint=processed.artifact.original_artifact_name,
    )

    preview_storage_key = preview.storage_key
    preview_download_url = preview.download_url or _download_url_for_storage_key(preview_storage_key)

    return {
        "filename": preview.file_name,
        "storage_key": preview_storage_key,
        "download_url": preview_download_url,
        "content_type": "application/pdf",
    }


def _serialize_processed_result(processed: ProtectedArtifactResult) -> dict[str, Any]:
    return {
        "analyzer_response": _ensure_download_url(processed.analyzer_response).model_dump(mode="python"),
        "artifact": asdict(processed.artifact),
        "generated_output_path": processed.generated_output_path,
        "preview_artifact": _build_docx_preview_artifact(processed),
    }


def _run_structured_extraction_request_with_preview(request: AnalyzerRequest) -> dict[str, Any]:
    execution = _run_workflow_execution(
        request,
        structured_preview=True,
        structured_preview_rows_limit=50,
    )
    return {
        "analyzer_response": execution.response.model_dump(mode="json"),
        "preview_payload": execution.preview_payload,
        "preview_rows": execution.preview_rows or [],
        "preview_truncated": execution.preview_truncated,
    }


def _run_standalone_feature_request(request: AnalyzerRequest) -> AnalyzerResponse:
    return _run_request(request)


def _validate_numbered_questions(questions: list[str]) -> list[str]:
    cleaned = [str(item).strip() for item in questions if str(item).strip()]
    if not cleaned:
        raise _bad_request("At least one generated question is required.")

    for index, question in enumerate(cleaned, start=1):
        if not question.lstrip().startswith(f"{index}."):
            raise _bad_request("Questions must be sequentially numbered starting at 1.")

    return cleaned


def _numbered_questions_from_plain_text(raw: str) -> list[str]:
    matches = re.findall(r"(?:^|\n)\s*(\d+)\.\s+([\s\S]*?)(?=\n\s*\d+\.\s+|$)", raw)
    return [f"{index}. {body.strip()}" for index, (_, body) in enumerate(matches, start=1) if body.strip()]


def _coerce_numbered_questions(value: Any) -> list[str]:
    if isinstance(value, dict):
        value = value.get("questions")

    if isinstance(value, list):
        return _validate_numbered_questions([str(item).strip() for item in value if str(item).strip()])

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise _bad_request("At least one generated question is required.")
        if raw.startswith("[") or raw.startswith("{"):
            return _coerce_numbered_questions(_loads_json(raw, default=None))
        return _validate_numbered_questions(_numbered_questions_from_plain_text(raw))

    raise _bad_request("questions_json must be a JSON array, numbered text, or an object with a questions array.")


def _parse_numbered_questions(value: str | None) -> list[str]:
    """
    Parse generated questions supplied by the frontend for the follow-on
    generate_answers action.

    Accepted forms:
    - JSON array: ["1. ...", "2. ..."]
    - JSON object with a questions array: {"questions": [...]}
    - Plain numbered text: "1. ...\n2. ..."
    """
    if value is None or not str(value).strip():
        raise _bad_request("questions_json is required to generate answers.")
    return _coerce_numbered_questions(str(value).strip())


def _parse_batch_questions_by_index(value: str | None, *, file_count: int) -> dict[int, list[str]]:
    """Parse per-file questions for batch answer generation.

    Accepted forms:
    - shared JSON array / numbered text: applied to every file;
    - {"questions": [...]}: shared questions applied to every file;
    - {"questions_by_index": {"1": [...], "2": [...]}}: per-file questions;
    - {"1": [...], "2": [...]}: compact per-file shape.
    """
    if value is None or not str(value).strip():
        raise _bad_request("questions_json is required to generate answers.")

    raw = str(value).strip()
    parsed = _loads_json(raw, default=None) if raw.startswith("{") or raw.startswith("[") else None

    if isinstance(parsed, dict):
        per_file = parsed.get("questions_by_index") or parsed.get("questionsByIndex") or parsed.get("by_index")
        if per_file is None and all(str(key).isdigit() for key in parsed.keys()):
            per_file = parsed

        if isinstance(per_file, dict):
            questions_by_index: dict[int, list[str]] = {}
            for index in range(1, file_count + 1):
                raw_questions = per_file.get(str(index), per_file.get(index))
                if raw_questions is not None:
                    questions_by_index[index] = _coerce_numbered_questions(raw_questions)
            return questions_by_index

    shared_questions = _coerce_numbered_questions(parsed if parsed is not None else raw)
    return {index: shared_questions for index in range(1, file_count + 1)}

def _artifact_path_from_result(result: Any) -> Path | None:
    storage_key = getattr(result, "storage_key", None)
    filename = getattr(result, "filename", None)
    candidates: list[Path] = []

    if isinstance(storage_key, str) and storage_key.strip():
        raw_key = storage_key.strip().replace("\\", "/")
        candidates.append(Path(raw_key))

        key = raw_key
        key = key.removeprefix("/api/analyzer/artifacts/")
        key = key.removeprefix("/api/v1/analyzer/artifacts/")
        key = key.removeprefix("/artifacts/")
        key = key.removeprefix("artifacts/")

        for storage in _artifact_storage_download_candidates():
            candidates.append(storage.base_dir / key)

    if isinstance(filename, str) and filename.strip():
        for storage in _artifact_storage_download_candidates():
            candidates.append(storage.base_dir / Path(filename).name)

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        if resolved.exists() and resolved.is_file():
            return resolved

    return None


def _read_text_from_artifact(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").strip()

    if suffix == ".docx":
        try:
            import docx  # python-docx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("python-docx is required to read generated DOCX questions.") from exc
        document = docx.Document(str(path))
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()).strip()

    if suffix == ".pdf":
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyMuPDF is required to read generated PDF questions.") from exc
        with fitz.open(path) as pdf:
            return "\n".join(page.get_text("text") for page in pdf).strip()

    return ""


def _generated_questions_text_from_response(response: AnalyzerResponse) -> str | None:
    result = response.result
    inline_content = getattr(result, "content", None)
    if isinstance(inline_content, str) and inline_content.strip():
        return inline_content.strip()

    path = _artifact_path_from_result(result)
    if path is None:
        return None

    try:
        text = _read_text_from_artifact(path)
    except Exception:
        return None

    return text or None


def _clean_repeated_strings(values: list[str] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return cleaned


def _serialize_candidates(candidates: list[Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates):
        serialized.append(
            {
                "id": f"{candidate.label}|{candidate.source}|{candidate.quote}|{index}",
                "label": candidate.label,
                "quote": candidate.quote,
                "occurrences": candidate.occurrences,
                "source": candidate.source,
            }
        )
    return serialized


# -----------------------------------------------------------------------------
# Request builders for privacy/compliance
# -----------------------------------------------------------------------------


def _normalize_compliance_sector_packs(
    sector_packs: list[ComplianceSectorPack] | None,
) -> list[ComplianceSectorPack]:
    core_pack = ComplianceSectorPack.core_control_library
    legacy_core_pack = getattr(ComplianceSectorPack, "nigeria_core_control_library", None)

    resolved: list[ComplianceSectorPack] = []
    seen: set[ComplianceSectorPack] = set()
    for pack in list(sector_packs or []):
        if legacy_core_pack is not None and pack == legacy_core_pack:
            pack = core_pack
        if pack not in seen:
            resolved.append(pack)
            seen.add(pack)

    if core_pack not in seen:
        resolved.insert(0, core_pack)
    return resolved


def _default_compliance_sector_packs(
    jurisdiction: ComplianceJurisdiction,
) -> list[ComplianceSectorPack]:
    return [ComplianceSectorPack.core_control_library]


def _build_compliance_request(
    *,
    file: UploadFile,
    jurisdiction: ComplianceJurisdiction,
    sector_packs: list[ComplianceSectorPack] | None,
    regulatory_domains: list[ComplianceRegulatoryDomain] | None,
    report_variant: ComplianceReportVariant,
    system_language: SystemLanguage,
) -> AnalyzerRequest:
    try:
        input_payload = build_uploaded_document_payload(action=FeatureType.compliance, upload=file)
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except FileNotFoundError as exc:
        raise _bad_request(str(exc)) from exc

    payload = ComplianceRequest(
        feature=FeatureType.compliance,
        jurisdiction=jurisdiction,
        sector_packs=_normalize_compliance_sector_packs(
            sector_packs or _default_compliance_sector_packs(jurisdiction)
        ),
        regulatory_domains=regulatory_domains or [],
        report_variant=report_variant,
        require_human_review=True,
    )

    return AnalyzerRequest(
        action=FeatureType.compliance,
        input=input_payload,
        payload=payload,
        policy=_policy_for_action(FeatureType.compliance),
        system_language=system_language,
    )


def _privacy_payload_kwargs(
    *,
    feature: FeatureType,
    document_type: RedactionMaskingDocumentType | None,
    target_data: list[SensitiveDataType] | None,
    review_exclusions: list[str] | None,
) -> dict[str, Any]:
    payload_kwargs: dict[str, Any] = {"feature": feature}
    if document_type is not None:
        payload_kwargs["document_type"] = document_type
    if target_data:
        payload_kwargs["target_data"] = target_data
    if review_exclusions:
        payload_kwargs["review_exclusions"] = [item.strip() for item in review_exclusions if item and item.strip()]
    return payload_kwargs


def _build_privacy_request(
    *,
    action: FeatureType,
    file: UploadFile,
    document_type: RedactionMaskingDocumentType | None,
    target_data: list[SensitiveDataType] | None,
    review_exclusions: list[str] | None,
    system_language: SystemLanguage,
) -> tuple[Any, AnalyzerRequest]:
    try:
        input_payload = build_uploaded_document_payload(action=action, upload=file)
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    payload_kwargs = _privacy_payload_kwargs(
        feature=action,
        document_type=document_type,
        target_data=target_data,
        review_exclusions=review_exclusions,
    )
    payload = RedactionRequest(**payload_kwargs) if action == FeatureType.redact else DataMaskingRequest(**payload_kwargs)

    request = AnalyzerRequest(
        action=action,
        input=input_payload,
        payload=payload,
        policy=_policy_for_action(action),
        system_language=system_language,
    )
    return input_payload, request



# -----------------------------------------------------------------------------
# Paid-plan batch upload helpers
# -----------------------------------------------------------------------------


def _batch_policy_exception(exc: BatchUploadPolicyError) -> HTTPException:
    return HTTPException(
        status_code=getattr(exc, "status_code", 400),
        detail={
            "error": getattr(exc, "error_code", "invalid_batch_upload"),
            "message": str(exc),
            "limits": BATCH_UPLOAD_LIMITS_BY_PLAN,
        },
    )


def _require_batch_upload_policy(
    *,
    current_user: AuthenticatedUser,
    action: FeatureType,
    files: list[UploadFile],
) -> BatchUploadPolicy:
    try:
        return require_batch_upload_entitlement(
            current_user,
            feature=action.value,
            files=files,
        )
    except BatchUploadPolicyError as exc:
        raise _batch_policy_exception(exc) from exc


def _serialize_batch_result(value: Any) -> Any:
    if isinstance(value, AnalyzerResponse):
        return _ensure_download_url(value).model_dump(mode="json")
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _batch_error_payload(status_code: int, error: str, message: str) -> dict[str, Any]:
    return {
        "status_code": status_code,
        "error": error,
        "message": message,
    }


def _http_error_detail(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        return _batch_error_payload(
            int(exc.status_code),
            str(detail.get("error") or "request_failed"),
            str(detail.get("message") or detail.get("detail") or "Request failed."),
        )
    return _batch_error_payload(int(exc.status_code), "request_failed", str(detail or "Request failed."))


def _batch_item_from_upload(
    *,
    upload: UploadFile,
    index: int,
    policy: BatchUploadPolicy,
    operation: Callable[[UploadFile], Any],
) -> dict[str, Any]:
    original_filename = (upload.filename or f"upload-{index}{policy.extension}").strip()
    item_started = time.perf_counter()

    try:
        result = operation(upload)
        item = {
            "index": index,
            "filename": original_filename,
            "success": True,
            "response": _serialize_batch_result(result),
        }
    except HTTPException as exc:
        item = {
            "index": index,
            "filename": original_filename,
            "success": False,
            "error": _http_error_detail(exc),
        }
    except (UploadError, ValidationError, ValueError, FileNotFoundError, TypeError) as exc:
        item = {
            "index": index,
            "filename": original_filename,
            "success": False,
            "error": _batch_error_payload(400, "invalid_request", str(exc)),
        }
    except RuntimeError as exc:
        item = {
            "index": index,
            "filename": original_filename,
            "success": False,
            "error": _batch_error_payload(503, "service_unavailable", str(exc)),
        }
    except Exception as exc:  # pragma: no cover - defensive isolation per file.
        item = {
            "index": index,
            "filename": original_filename,
            "success": False,
            "error": _batch_error_payload(500, "batch_item_failed", str(exc)),
        }

    item["elapsed_ms"] = round((time.perf_counter() - item_started) * 1000)
    return item


def _run_batch_uploads(
    *,
    action: FeatureType,
    files: list[UploadFile],
    policy: BatchUploadPolicy,
    operation: Callable[[UploadFile], Any],
) -> dict[str, Any]:
    """Run a batch with bounded per-request concurrency.

    Previous behavior processed files one-by-one. That is safe but too slow for
    B2B workloads because total latency becomes the sum of every upload's LLM +
    extraction + writer time. This implementation keeps the same response shape
    while processing independent files concurrently up to the plan's configured
    worker count.
    """
    batch_started = time.perf_counter()
    indexed_uploads = list(enumerate(files, start=1))
    worker_count = max(1, min(int(getattr(policy, "max_concurrency", 1) or 1), len(indexed_uploads) or 1))
    items_by_index: dict[int, dict[str, Any]] = {}

    if worker_count == 1:
        for index, upload in indexed_uploads:
            items_by_index[index] = _batch_item_from_upload(
                upload=upload,
                index=index,
                policy=policy,
                operation=operation,
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix=f"redocx-{action.value}-batch",
        ) as executor:
            future_by_index = {
                executor.submit(
                    _batch_item_from_upload,
                    upload=upload,
                    index=index,
                    policy=policy,
                    operation=operation,
                ): index
                for index, upload in indexed_uploads
            }

            for future in concurrent.futures.as_completed(future_by_index):
                index = future_by_index[future]
                try:
                    items_by_index[index] = future.result()
                except Exception as exc:  # pragma: no cover - worker envelope fallback.
                    upload = files[index - 1]
                    items_by_index[index] = {
                        "index": index,
                        "filename": (upload.filename or f"upload-{index}{policy.extension}").strip(),
                        "success": False,
                        "error": _batch_error_payload(500, "batch_worker_failed", str(exc)),
                        "elapsed_ms": 0,
                    }

    items = [items_by_index[index] for index, _ in indexed_uploads if index in items_by_index]
    succeeded = sum(1 for item in items if item.get("success") is True)
    failed = len(items) - succeeded
    elapsed_ms = round((time.perf_counter() - batch_started) * 1000)

    return {
        "success": failed == 0,
        "feature": action.value,
        "batch": {
            "plan": policy.plan,
            "limit": policy.max_uploads,
            "extension": policy.extension,
            "file_count": policy.file_count,
            "succeeded": succeeded,
            "failed": failed,
            "concurrency": worker_count,
            "processing_mode": "concurrent" if worker_count > 1 else "sequential",
            "elapsed_ms": elapsed_ms,
        },
        "items": items,
    }

BATCH_CONVERSION_OUTPUTS_BY_INPUT_EXTENSION: dict[str, set[str]] = {
    ".pdf": {"docx"},
    ".docx": {"pdf"},
    ".jpg": {"pdf", "docx"},
    ".jpeg": {"pdf", "docx"},
    ".png": {"jpg", "jpeg"},
}

BATCH_TRANSCRIBE_MEDIA_TYPE_BY_EXTENSION: dict[str, str] = {
    ".mp3": "audio",
    ".mp4": "video",
    ".mkv": "video",
    ".mov": "video",
}


def _form_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _require_batch_extension(
    *,
    policy: BatchUploadPolicy,
    allowed_extensions: set[str],
    feature_label: str,
) -> None:
    normalized_allowed = {item if item.startswith(".") else f".{item}" for item in allowed_extensions}
    if policy.extension not in normalized_allowed:
        raise _bad_request(
            f"{feature_label} batch processing only accepts {', '.join(sorted(normalized_allowed))} files. "
            f"Received {policy.extension}."
        )


def _require_batch_conversion_action(
    *,
    policy: BatchUploadPolicy,
    output_format: ConversionOutputFormat,
) -> None:
    output = _form_value(output_format)
    allowed_outputs = BATCH_CONVERSION_OUTPUTS_BY_INPUT_EXTENSION.get(policy.extension, set())
    if not allowed_outputs:
        raise _bad_request(
            f"Batch conversion does not support {policy.extension} uploads."
        )
    if output not in allowed_outputs:
        raise _bad_request(
            "All files in a conversion batch must follow one valid conversion action. "
            f"For {policy.extension} input batches, allowed output formats are: "
            f"{', '.join(sorted(allowed_outputs))}. Received: {output or 'unknown'}."
        )


def _require_batch_transcription_action(
    *,
    policy: BatchUploadPolicy,
    media_type: MediaType,
) -> None:
    expected_media_type = BATCH_TRANSCRIBE_MEDIA_TYPE_BY_EXTENSION.get(policy.extension)
    requested_media_type = _form_value(media_type)
    if expected_media_type is None:
        raise _bad_request(
            f"Speech-to-text batch processing does not support {policy.extension} uploads."
        )
    if requested_media_type != expected_media_type:
        raise _bad_request(
            "All files in a speech-to-text batch must follow one valid media action. "
            f"{policy.extension} batches must be submitted as {expected_media_type}, "
            f"not {requested_media_type or 'unknown'}."
        )


# -----------------------------------------------------------------------------
# Paid-plan batch processing routes
# -----------------------------------------------------------------------------


@router.post("/batch/convert", dependencies=[Depends(rate_limit_for_feature(FeatureType.convert))])
def batch_convert_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    output_format: ConversionOutputFormat = Form(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.convert, files=files)
    _require_batch_conversion_action(policy=policy, output_format=output_format)

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = build_uploaded_document_payload(action=FeatureType.convert, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.convert,
            input=input_payload,
            payload=ConversionRequest(feature=FeatureType.convert, output_format=output_format),
            policy=_policy_for_action(FeatureType.convert),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.convert, files=files, policy=policy, operation=operation)


@router.post("/batch/summarize", dependencies=[Depends(rate_limit_for_feature(FeatureType.summarize))])
def batch_summarize_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.summarize, files=files)

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = build_uploaded_document_payload(action=FeatureType.summarize, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.summarize,
            input=input_payload,
            payload=SummarizationRequest(feature=FeatureType.summarize),
            policy=_policy_for_action(FeatureType.summarize),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.summarize, files=files, policy=policy, operation=operation)


@router.post("/batch/grammar-correct", dependencies=[Depends(rate_limit_for_feature(FeatureType.grammar_correct))])
def batch_grammar_correct_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.grammar_correct, files=files)

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = build_uploaded_document_payload(action=FeatureType.grammar_correct, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.grammar_correct,
            input=input_payload,
            payload=GrammarCorrectionRequest(feature=FeatureType.grammar_correct),
            policy=_policy_for_action(FeatureType.grammar_correct),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.grammar_correct, files=files, policy=policy, operation=operation)


@router.post("/batch/translate", dependencies=[Depends(rate_limit_for_feature(FeatureType.translate))])
def batch_translate_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    target_language: str = Form(...),
    source_language: str = Form("auto"),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.translate, files=files)

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = build_uploaded_document_payload(action=FeatureType.translate, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.translate,
            input=input_payload,
            payload=TranslationRequest(
                feature=FeatureType.translate,
                source_language=source_language,
                target_language=target_language,
            ),
            policy=_policy_for_action(FeatureType.translate),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.translate, files=files, policy=policy, operation=operation)


@router.post("/batch/explain", dependencies=[Depends(rate_limit_for_feature(FeatureType.explain))])
def batch_explain_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    allow_external_knowledge: bool = Form(False),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.explain, files=files)

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = build_uploaded_document_payload(action=FeatureType.explain, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.explain,
            input=input_payload,
            payload=ExplanationRequest(
                feature=FeatureType.explain,
                allow_external_knowledge=allow_external_knowledge,
            ),
            policy=_policy_for_action(FeatureType.explain),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.explain, files=files, policy=policy, operation=operation)


@router.post("/batch/generate-questions", dependencies=[Depends(rate_limit_for_feature(FeatureType.generate_questions))])
def batch_generate_questions_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.generate_questions, files=files)

    def operation(upload: UploadFile) -> dict[str, Any]:
        input_payload = build_uploaded_document_payload(action=FeatureType.generate_questions, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.generate_questions,
            input=input_payload,
            payload=QuestionGenerationRequest(feature=FeatureType.generate_questions),
            policy=_policy_for_action(FeatureType.generate_questions),
            system_language=system_language,
        )
        response = _ensure_download_url(_run_request(request))
        body = response.model_dump(mode="json")
        generated_questions_text = _generated_questions_text_from_response(response)
        if generated_questions_text:
            body["generated_questions_text"] = generated_questions_text
        return body

    return _run_batch_uploads(action=FeatureType.generate_questions, files=files, policy=policy, operation=operation)


@router.post("/batch/generate-answers", dependencies=[Depends(rate_limit_for_feature(FeatureType.generate_answers))])
def batch_generate_answers_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    questions_json: str = Form(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.generate_answers, files=files)
    questions_by_index = _parse_batch_questions_by_index(questions_json, file_count=len(files))
    index_by_upload_id = {id(upload): index for index, upload in enumerate(files, start=1)}

    def operation(upload: UploadFile) -> AnalyzerResponse:
        upload_index = index_by_upload_id[id(upload)]
        questions = questions_by_index.get(upload_index)
        if not questions:
            raise _bad_request("No generated questions were available for this file.")
        input_payload = build_uploaded_document_payload(action=FeatureType.generate_answers, upload=upload)
        request = AnalyzerRequest(
            action=FeatureType.generate_answers,
            input=input_payload,
            payload=AnswerGenerationRequest(
                feature=FeatureType.generate_answers,
                questions=questions,
            ),
            policy=_policy_for_action(FeatureType.generate_answers),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.generate_answers, files=files, policy=policy, operation=operation)


@router.post("/batch/pdf/compress", dependencies=[Depends(rate_limit_for_feature(FeatureType.compress_pdf))])
def batch_compress_pdf_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    compression_level: PdfCompressionLevel = Form(PdfCompressionLevel.balanced),
    output_filename: str = Form("compressed-document.pdf"),
    async_processing: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.compress_pdf, files=files)
    _require_batch_extension(policy=policy, allowed_extensions={".pdf"}, feature_label="PDF compression")

    def operation(upload: UploadFile) -> AnalyzerResponse:
        input_payload = _build_single_pdf_input(FeatureType.compress_pdf, upload)
        request = AnalyzerRequest(
            action=FeatureType.compress_pdf,
            input=input_payload,
            payload=CompressPdfRequest(
                feature=FeatureType.compress_pdf,
                compression_level=compression_level,
                output_filename=output_filename,
                async_processing=async_processing,
            ),
            policy=_policy_for_action(FeatureType.compress_pdf),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.compress_pdf, files=files, policy=policy, operation=operation)


@router.post("/batch/transcribe", dependencies=[Depends(rate_limit_for_feature(FeatureType.transcribe))])
def batch_transcribe_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    media_type: MediaType = Form(...),
    duration_seconds: list[int] = Form(...),
    preserve_filler_words: bool = Form(True),
    remove_background_noise: bool = Form(False),
    diarize_speakers: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    policy = _require_batch_upload_policy(current_user=current_user, action=FeatureType.transcribe, files=files)
    _require_batch_transcription_action(policy=policy, media_type=media_type)
    durations = [int(item) for item in duration_seconds]
    if len(durations) == 1 and len(files) > 1:
        durations = durations * len(files)
    if len(durations) != len(files):
        raise _bad_request("duration_seconds must be supplied once per uploaded media file.")
    duration_by_filename = {id(upload): durations[index] for index, upload in enumerate(files)}

    def operation(upload: UploadFile) -> AnalyzerResponse:
        try:
            input_payload = build_uploaded_media_payload(
                upload=upload,
                media_type=media_type,
                duration_seconds=duration_by_filename[id(upload)],
            )
        except UploadError as exc:
            raise _bad_request(str(exc)) from exc
        except ValueError as exc:
            raise _bad_request(str(exc)) from exc

        request = AnalyzerRequest(
            action=FeatureType.transcribe,
            input=input_payload,
            payload=TranscriptionRequest(
                feature=FeatureType.transcribe,
                preserve_filler_words=preserve_filler_words,
                remove_background_noise=remove_background_noise,
                diarize_speakers=diarize_speakers,
            ),
            policy=_policy_for_action(FeatureType.transcribe),
            system_language=system_language,
        )
        return _run_request(request)

    return _run_batch_uploads(action=FeatureType.transcribe, files=files, policy=policy, operation=operation)

# -----------------------------------------------------------------------------
# Existing AI/document routes
# -----------------------------------------------------------------------------


@router.post("/convert", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.convert))])
def convert_route(
    file: UploadFile = File(...),
    output_format: ConversionOutputFormat = Form(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    try:
        input_payload = build_uploaded_document_payload(action=FeatureType.convert, upload=file)
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    request = AnalyzerRequest(
        action=FeatureType.convert,
        input=input_payload,
        payload=ConversionRequest(feature=FeatureType.convert, output_format=output_format),
        policy=_policy_for_action(FeatureType.convert),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/summarize", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.summarize))])
def summarize_route(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_document_input(action=FeatureType.summarize, file=file, text=text)
    request = AnalyzerRequest(
        action=FeatureType.summarize,
        input=input_payload,
        payload=SummarizationRequest(feature=FeatureType.summarize),
        policy=_policy_for_action(FeatureType.summarize),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/grammar-correct", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.grammar_correct))])
def grammar_correct_route(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_document_input(action=FeatureType.grammar_correct, file=file, text=text)
    request = AnalyzerRequest(
        action=FeatureType.grammar_correct,
        input=input_payload,
        payload=GrammarCorrectionRequest(feature=FeatureType.grammar_correct),
        policy=_policy_for_action(FeatureType.grammar_correct),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/translate", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.translate))])
def translate_route(
    target_language: str = Form(...),
    source_language: str = Form("auto"),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_document_input(action=FeatureType.translate, file=file, text=text)
    request = AnalyzerRequest(
        action=FeatureType.translate,
        input=input_payload,
        payload=TranslationRequest(
            feature=FeatureType.translate,
            source_language=source_language,
            target_language=target_language,
        ),
        policy=_policy_for_action(FeatureType.translate),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/transcribe", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.transcribe))])
def transcribe_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    media_type: MediaType = Form(...),
    duration_seconds: int = Form(...),
    preserve_filler_words: bool = Form(True),
    remove_background_noise: bool = Form(False),
    diarize_speakers: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    del current_user
    try:
        input_payload = build_uploaded_media_payload(upload=file, media_type=media_type, duration_seconds=duration_seconds)
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    request = AnalyzerRequest(
        action=FeatureType.transcribe,
        input=input_payload,
        payload=TranscriptionRequest(
            feature=FeatureType.transcribe,
            preserve_filler_words=preserve_filler_words,
            remove_background_noise=remove_background_noise,
            diarize_speakers=diarize_speakers,
        ),
        policy=_policy_for_action(FeatureType.transcribe),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/explain", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.explain))])
def explain_route(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    allow_external_knowledge: bool = Form(False),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_document_input(action=FeatureType.explain, file=file, text=text)
    request = AnalyzerRequest(
        action=FeatureType.explain,
        input=input_payload,
        payload=ExplanationRequest(
            feature=FeatureType.explain,
            allow_external_knowledge=allow_external_knowledge,
        ),
        policy=_policy_for_action(FeatureType.explain),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/generate-questions", dependencies=[Depends(rate_limit_for_feature(FeatureType.generate_questions))])
def generate_questions_route(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    input_payload = _build_document_input(action=FeatureType.generate_questions, file=file, text=text)
    request = AnalyzerRequest(
        action=FeatureType.generate_questions,
        input=input_payload,
        payload=QuestionGenerationRequest(feature=FeatureType.generate_questions),
        policy=_policy_for_action(FeatureType.generate_questions),
        system_language=system_language,
    )
    response = _ensure_download_url(_run_request(request))
    body = response.model_dump(mode="json")
    generated_questions_text = _generated_questions_text_from_response(response)
    if generated_questions_text:
        body["generated_questions_text"] = generated_questions_text
    return body


@router.post("/generate-answers", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.generate_answers))])
def generate_answers_route(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    questions_json: str = Form(...),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_document_input(action=FeatureType.generate_answers, file=file, text=text)
    questions = _parse_numbered_questions(questions_json)
    request = AnalyzerRequest(
        action=FeatureType.generate_answers,
        input=input_payload,
        payload=AnswerGenerationRequest(
            feature=FeatureType.generate_answers,
            questions=questions,
        ),
        policy=_policy_for_action(FeatureType.generate_answers),
        system_language=system_language,
    )
    return _run_request(request)


# -----------------------------------------------------------------------------
# Privacy routes
# -----------------------------------------------------------------------------


@router.post("/redact/review", dependencies=[Depends(rate_limit_for_feature(FeatureType.redact))])
def redact_review_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    document_type: RedactionMaskingDocumentType | None = Form(default=None),
    target_data: list[SensitiveDataType] | None = Form(default=None),
    review_exclusions: list[str] | None = Form(default=None),
    custom_redactions: list[str] | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    input_payload, request = _build_privacy_request(
        action=FeatureType.redact,
        file=file,
        document_type=document_type,
        target_data=target_data,
        review_exclusions=review_exclusions,
        system_language=system_language,
    )
    cleaned_custom_redactions = _clean_repeated_strings(custom_redactions)
    processed = _run_privacy_request(
        request,
        source_path=_privacy_source_path(input_payload),
        custom_redactions=cleaned_custom_redactions,
    )
    candidates = preview_redaction_candidates(
        request,
        project_id=_google_sdp_project_id(),
        location=DEFAULT_GOOGLE_SDP_LOCATION,
        custom_redactions=cleaned_custom_redactions,
    )
    return {**_serialize_processed_result(processed), "candidates": _serialize_candidates(candidates)}


@router.post("/data-mask/review", dependencies=[Depends(rate_limit_for_feature(FeatureType.data_mask))])
def data_mask_review_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    document_type: RedactionMaskingDocumentType | None = Form(default=None),
    target_data: list[SensitiveDataType] | None = Form(default=None),
    review_exclusions: list[str] | None = Form(default=None),
    custom_redactions: list[str] | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    input_payload, request = _build_privacy_request(
        action=FeatureType.data_mask,
        file=file,
        document_type=document_type,
        target_data=target_data,
        review_exclusions=review_exclusions,
        system_language=system_language,
    )
    cleaned_custom_redactions = _clean_repeated_strings(custom_redactions)
    processed = _run_privacy_request(
        request,
        source_path=_privacy_source_path(input_payload),
        custom_redactions=cleaned_custom_redactions,
    )
    candidates = preview_data_mask_candidates(
        request,
        project_id=_google_sdp_project_id(),
        location=DEFAULT_GOOGLE_SDP_LOCATION,
        custom_redactions=cleaned_custom_redactions,
    )
    return {**_serialize_processed_result(processed), "candidates": _serialize_candidates(candidates)}


@router.post("/redact", dependencies=[Depends(rate_limit_for_feature(FeatureType.redact))])
def redact_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    document_type: RedactionMaskingDocumentType | None = Form(default=None),
    target_data: list[SensitiveDataType] | None = Form(default=None),
    review_exclusions: list[str] | None = Form(default=None),
    custom_redactions: list[str] | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    input_payload, request = _build_privacy_request(
        action=FeatureType.redact,
        file=file,
        document_type=document_type,
        target_data=target_data,
        review_exclusions=review_exclusions,
        system_language=system_language,
    )
    processed = _run_privacy_request(
        request,
        source_path=_privacy_source_path(input_payload),
        custom_redactions=_clean_repeated_strings(custom_redactions),
    )
    return _serialize_processed_result(processed)


@router.post("/data-mask", dependencies=[Depends(rate_limit_for_feature(FeatureType.data_mask))])
def data_mask_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    document_type: RedactionMaskingDocumentType | None = Form(default=None),
    target_data: list[SensitiveDataType] | None = Form(default=None),
    review_exclusions: list[str] | None = Form(default=None),
    custom_redactions: list[str] | None = Form(default=None),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    input_payload, request = _build_privacy_request(
        action=FeatureType.data_mask,
        file=file,
        document_type=document_type,
        target_data=target_data,
        review_exclusions=review_exclusions,
        system_language=system_language,
    )
    processed = _run_privacy_request(
        request,
        source_path=_privacy_source_path(input_payload),
        custom_redactions=_clean_repeated_strings(custom_redactions),
    )
    return _serialize_processed_result(processed)


# -----------------------------------------------------------------------------
# Structured extraction / compliance routes
# -----------------------------------------------------------------------------


@router.post("/structured-extraction", dependencies=[Depends(rate_limit_for_feature(FeatureType.structured_extract))])
def structured_extraction_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    document_classes: list[StructuredExtractionDocumentClass] | None = Form(default=None),
    selected_fields: list[str] | None = Form(default=None),
    output_format: StructuredDataOutputFormat = Form(StructuredDataOutputFormat.json),
    result_shape: StructuredExtractionResultShape = Form(StructuredExtractionResultShape.machine_readable),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    try:
        input_payload = build_uploaded_document_payload(action=FeatureType.structured_extract, upload=file)
    except UploadError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc

    payload = StructuredExtractionRequest(
        feature=FeatureType.structured_extract,
        document_classes=document_classes or [],
        selected_fields=_clean_repeated_strings(selected_fields),
        output_format=output_format,
        result_shape=result_shape,
        allow_external_knowledge=False,
        require_human_review=True,
    )
    request = AnalyzerRequest(
        action=FeatureType.structured_extract,
        input=input_payload,
        payload=payload,
        policy=_policy_for_action(FeatureType.structured_extract),
        system_language=system_language,
    )
    return _run_structured_extraction_request_with_preview(request)


@router.post("/compliance", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.compliance))])
def compliance_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    jurisdiction: ComplianceJurisdiction = Form(ComplianceJurisdiction.nigeria),
    sector_packs: list[ComplianceSectorPack] | None = Form(default=None),
    regulatory_domains: list[ComplianceRegulatoryDomain] | None = Form(default=None),
    report_variant: ComplianceReportVariant = Form(ComplianceReportVariant.human_readable_report),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    del current_user
    request = _build_compliance_request(
        file=file,
        jurisdiction=jurisdiction,
        sector_packs=sector_packs,
        regulatory_domains=regulatory_domains,
        report_variant=report_variant,
        system_language=system_language,
    )
    return _run_standalone_feature_request(request)


@router.post("/compliance/preview", dependencies=[Depends(rate_limit_for_feature(FeatureType.compliance))])
def compliance_preview_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    jurisdiction: ComplianceJurisdiction = Form(ComplianceJurisdiction.nigeria),
    sector_packs: list[ComplianceSectorPack] | None = Form(default=None),
    regulatory_domains: list[ComplianceRegulatoryDomain] | None = Form(default=None),
    report_variant: ComplianceReportVariant = Form(ComplianceReportVariant.human_readable_report),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> dict[str, Any]:
    del current_user
    request = _build_compliance_request(
        file=file,
        jurisdiction=jurisdiction,
        sector_packs=sector_packs,
        regulatory_domains=regulatory_domains,
        report_variant=report_variant,
        system_language=system_language,
    )
    try:
        preview = workflow_router.preview_compliance(request)
        report = preview.report.model_dump(mode="json")
        return {
            "preview_markdown": preview.preview_markdown,
            "report": report,
            "counts": report.get("counts"),
            "rule_results": report.get("rule_results", []),
            "human_review": preview.human_review.model_dump(mode="json"),
        }
    except HTTPException:
        raise
    except RuleRegistryError as exc:
        raise _bad_request(str(exc)) from exc
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    except RuntimeError as exc:
        raise _service_unavailable(str(exc)) from exc


# -----------------------------------------------------------------------------
# PDF tools routes
# -----------------------------------------------------------------------------


@router.post("/pdf/combine", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.combine_pdf))])
def combine_pdf_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    files: list[UploadFile] = File(...),
    output_filename: str = Form("combined-document.pdf"),
    preserve_bookmarks: bool = Form(True),
    preserve_metadata: bool = Form(False),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    _require_batch_upload_policy(current_user=current_user, action=FeatureType.combine_pdf, files=files)
    input_payload = _build_pdf_set_input(FeatureType.combine_pdf, files)
    request = AnalyzerRequest(
        action=FeatureType.combine_pdf,
        input=input_payload,
        payload=CombinePdfRequest(
            feature=FeatureType.combine_pdf,
            output_filename=output_filename,
            preserve_bookmarks=preserve_bookmarks,
            preserve_metadata=preserve_metadata,
        ),
        policy=_policy_for_action(FeatureType.combine_pdf),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/pdf/split", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.split_pdf))])
def split_pdf_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    mode: PdfSplitMode = Form(...),
    selected_pages: str | None = Form(default=None),
    page_ranges: str | None = Form(default=None),
    output_basename: str = Form("split-document"),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    del current_user
    input_payload = _build_single_pdf_input(FeatureType.split_pdf, file)
    request = AnalyzerRequest(
        action=FeatureType.split_pdf,
        input=input_payload,
        payload=SplitPdfRequest(
            feature=FeatureType.split_pdf,
            mode=mode,
            selected_pages=_parse_int_list(selected_pages),
            page_ranges=_parse_page_ranges(page_ranges),
            output_basename=output_basename,
        ),
        policy=_policy_for_action(FeatureType.split_pdf),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/pdf/edit", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.edit_pdf))])
def edit_pdf_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    operations_json: str = Form(...),
    output_filename: str = Form("edited-document.pdf"),
    generate_preview: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    del current_user
    input_payload = _build_single_pdf_input(FeatureType.edit_pdf, file)
    request = AnalyzerRequest(
        action=FeatureType.edit_pdf,
        input=input_payload,
        payload=EditPdfRequest(
            feature=FeatureType.edit_pdf,
            operations=_parse_edit_operations(operations_json),
            output_filename=output_filename,
            generate_preview=generate_preview,
        ),
        policy=_policy_for_action(FeatureType.edit_pdf),
        system_language=system_language,
    )
    return _run_request(request)


@router.post("/pdf/compress", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.compress_pdf))])
def compress_pdf_route(
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    compression_level: PdfCompressionLevel = Form(PdfCompressionLevel.balanced),
    output_filename: str = Form("compressed-document.pdf"),
    async_processing: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    del current_user
    input_payload = _build_single_pdf_input(FeatureType.compress_pdf, file)
    request = AnalyzerRequest(
        action=FeatureType.compress_pdf,
        input=input_payload,
        payload=CompressPdfRequest(
            feature=FeatureType.compress_pdf,
            compression_level=compression_level,
            output_filename=output_filename,
            async_processing=async_processing,
        ),
        policy=_policy_for_action(FeatureType.compress_pdf),
        system_language=system_language,
    )
    return _run_request(request)


# -----------------------------------------------------------------------------
# E-signature route
# -----------------------------------------------------------------------------


@router.post("/e-signature", response_model=AnalyzerResponse, dependencies=[Depends(rate_limit_for_feature(FeatureType.e_signature))])
def esignature_route(
    http_request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    file: UploadFile = File(...),
    payload_json: str = Form(...),
    signer_email: str | None = Form(default=None),
    signer_signature_json: str | None = Form(default=None),
    current_pdf_path: str | None = Form(default=None),
    send_emails: bool = Form(True),
    system_language: SystemLanguage = Form(SystemLanguage.english),
) -> AnalyzerResponse:
    input_payload = _build_single_pdf_input(FeatureType.e_signature, file)
    payload = _parse_esignature_request(payload_json)
    signer_signature = _parse_optional_signature(signer_signature_json)

    request = AnalyzerRequest(
        action=FeatureType.e_signature,
        input=input_payload,
        payload=payload,
        policy=_policy_for_action(FeatureType.e_signature),
        system_language=system_language,
    )
    return _run_request(
        request,
        current_pdf_path=current_pdf_path,
        signer_email=signer_email,
        signer_signature=signer_signature,
        sender_email=_user_email(current_user),
        sender_name=getattr(current_user, "name", None),
        send_emails=send_emails,
        ip_address=_client_ip(http_request),
        user_agent=_user_agent(http_request),
    )


# -----------------------------------------------------------------------------
# Artifact download route
# -----------------------------------------------------------------------------


@router.api_route("/artifacts/{storage_key:path}", methods=["GET", "HEAD"])
def download_artifact(
    storage_key: str,
):
    content_disposition_type = "attachment"

    def _file_response(path: Path):
        response = FileResponse(path=str(path), media_type=guess_content_type(str(path)))
        filename = path.name.replace('"', "")
        encoded_filename = quote(filename)
        response.headers["Content-Disposition"] = (
            f'{content_disposition_type}; filename="{filename}"; '
            f"filename*=UTF-8''{encoded_filename}"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "sandbox"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    last_checked_path: Path | None = None
    for storage in _artifact_storage_download_candidates():
        try:
            path = storage.resolve_storage_key(storage_key)
            last_checked_path = path
            if path.exists() and path.is_file():
                return _file_response(path)
        except ValueError:
            continue

    normalized_key = storage_key.strip().replace("\\", "/")
    candidate = Path(normalized_key)
    if candidate.is_absolute():
        raise HTTPException(status_code=400, detail="Artifact path must be relative.")
    if any(part == ".." for part in candidate.parts):
        raise HTTPException(status_code=400, detail="Artifact path must not contain parent-directory traversal.")

    resolved = candidate.resolve()
    configured_root = os.getenv("ARTIFACT_STORAGE_DIR", "").strip()
    allowed_roots = {
        Path("artifacts").resolve(),
        Path("artifacts/ai_documents").resolve(),
        Path("outputs").resolve(),
    }
    if configured_root:
        configured_path = Path(configured_root).expanduser().resolve()
        allowed_roots.add(configured_path)
        if configured_path.name != "ai_documents":
            allowed_roots.add((configured_path / "ai_documents").resolve())

    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        raise HTTPException(status_code=400, detail="Artifact path is outside the allowed artifact directories.")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "artifact_not_found",
                "message": "Artifact not found.",
                "last_checked_path": str(last_checked_path) if last_checked_path else None,
            },
        )

    return _file_response(resolved)


__all__ = ["router", "API_V1_ANALYZER_PREFIX"]
