from __future__ import annotations

"""
V1 orchestration layer for Redaction and Data Masking downloads.

Purpose:
- call the privacy-processing modules:
  - data_protection/redaction/redact.py
  - data_protection/data_masking/data_mask.py
- determine the generated local output file path
- persist the generated file into artifacts storage
- return both the analyzer response and downloadable artifact metadata

Design notes:
- keeps redact.py and data_mask.py focused on file processing only
- keeps artifacts.py focused on storage only
- does not perform upload saving or schema construction
- supports dependency injection for:
  - storage backend
  - Google SDP client
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from backend.src.processing.data_protection.client import (
    DEFAULT_DLP_LOCATION,
    DEFAULT_MIN_LIKELIHOOD,
    GoogleSDPClient,
)
from backend.src.processing.data_protection.data_masking.data_mask import apply_data_mask
from backend.src.processing.data_protection.redaction.redact import apply_redaction
from backend.src.schema import AnalyzerRequest, AnalyzerResponse, DocumentInputFormat, FeatureType
from backend.src.storage.artifacts import (
    LocalArtifactStorage,
    StorageBackend,
    StoredArtifact,
    guess_content_type,
)


@dataclass(frozen=True)
class ProtectedArtifactResult:
    """
    Final orchestration result for a privacy-processing request.

    analyzer_response:
        Schema-aligned response produced by redact.py / data_mask.py

    artifact:
        Persisted downloadable artifact metadata produced by artifacts.py

    generated_output_path:
        Local file path created by the processing module before artifact storage
    """

    analyzer_response: AnalyzerResponse
    artifact: StoredArtifact
    generated_output_path: str


_FILE_EXTENSION_MAP: dict[DocumentInputFormat, str] = {
    DocumentInputFormat.pdf: ".pdf",
    DocumentInputFormat.docx: ".docx",
    DocumentInputFormat.jpg: ".jpg",
    DocumentInputFormat.jpeg: ".jpeg",
    DocumentInputFormat.png: ".png",
}


def _normalize_request(request: AnalyzerRequest | Mapping[str, Any]) -> AnalyzerRequest:
    return request if isinstance(request, AnalyzerRequest) else AnalyzerRequest.model_validate(request)


def _resolve_source_path(source_path: str | Path) -> Path:
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")
    return path


def _expected_output_path(
    *,
    source_path: Path,
    output_dir: str | Path,
    suffix: str,
) -> Path:
    directory = Path(output_dir)
    return directory / f"{source_path.stem}{suffix}{source_path.suffix.lower()}"


def _artifact_name_for_output(path: Path) -> str:
    return path.name


def _storage_backend(storage_backend: Optional[StorageBackend]) -> StorageBackend:
    return storage_backend or LocalArtifactStorage()


def process_redaction_and_persist(
    request: AnalyzerRequest | Mapping[str, Any],
    *,
    source_path: str | Path,
    output_dir: str | Path,
    storage_backend: Optional[StorageBackend] = None,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
    ocr_languages: Optional[Sequence[str]] = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> ProtectedArtifactResult:
    """
    Run redaction, persist the generated file in artifact storage, and return
    both the analyzer response and the stored-artifact metadata.
    """
    req = _normalize_request(request)
    if req.action != FeatureType.redact:
        raise ValueError("process_redaction_and_persist requires action='redact'.")

    source = _resolve_source_path(source_path)
    expected_output = _expected_output_path(
        source_path=source,
        output_dir=output_dir,
        suffix="_redacted",
    )

    analyzer_response = apply_redaction(
        req,
        source_path=source,
        output_dir=output_dir,
        sdp=sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
        ocr_languages=ocr_languages,
        custom_redactions=custom_redactions,
    )

    if not expected_output.exists():
        raise RuntimeError(
            "Redaction completed but the expected output file was not found: "
            f"{expected_output}"
        )

    storage = _storage_backend(storage_backend)
    stored = storage.persist(
        source_file_path=str(expected_output),
        artifact_name=_artifact_name_for_output(expected_output),
        content_type=guess_content_type(str(expected_output)),
    )

    return ProtectedArtifactResult(
        analyzer_response=analyzer_response,
        artifact=stored,
        generated_output_path=str(expected_output),
    )


def process_data_mask_and_persist(
    request: AnalyzerRequest | Mapping[str, Any],
    *,
    source_path: str | Path,
    output_dir: str | Path,
    storage_backend: Optional[StorageBackend] = None,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
    ocr_languages: Optional[Sequence[str]] = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> ProtectedArtifactResult:
    """
    Run data masking, persist the generated file in artifact storage, and return
    both the analyzer response and the stored-artifact metadata.
    """
    req = _normalize_request(request)
    if req.action != FeatureType.data_mask:
        raise ValueError("process_data_mask_and_persist requires action='data_mask'.")

    source = _resolve_source_path(source_path)
    expected_output = _expected_output_path(
        source_path=source,
        output_dir=output_dir,
        suffix="_masked",
    )

    analyzer_response = apply_data_mask(
        req,
        source_path=source,
        output_dir=output_dir,
        sdp=sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
        ocr_languages=ocr_languages,
        custom_redactions=custom_redactions,
    )

    if not expected_output.exists():
        raise RuntimeError(
            "Data masking completed but the expected output file was not found: "
            f"{expected_output}"
        )

    storage = _storage_backend(storage_backend)
    stored = storage.persist(
        source_file_path=str(expected_output),
        artifact_name=_artifact_name_for_output(expected_output),
        content_type=guess_content_type(str(expected_output)),
    )

    return ProtectedArtifactResult(
        analyzer_response=analyzer_response,
        artifact=stored,
        generated_output_path=str(expected_output),
    )


def process_privacy_action_and_persist(
    request: AnalyzerRequest | Mapping[str, Any],
    *,
    source_path: str | Path,
    output_dir: str | Path,
    storage_backend: Optional[StorageBackend] = None,
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
    ocr_languages: Optional[Sequence[str]] = None,
    custom_redactions: Optional[Sequence[str]] = None,
) -> ProtectedArtifactResult:
    """
    Unified orchestration entrypoint for privacy actions.

    Supported actions:
    - redact
    - data_mask
    """
    req = _normalize_request(request)

    if req.action == FeatureType.redact:
        return process_redaction_and_persist(
            req,
            source_path=source_path,
            output_dir=output_dir,
            storage_backend=storage_backend,
            sdp=sdp,
            project_id=project_id,
            location=location,
            min_likelihood=min_likelihood,
            client=client,
            ocr_languages=ocr_languages,
            custom_redactions=custom_redactions,
        )

    if req.action == FeatureType.data_mask:
        return process_data_mask_and_persist(
            req,
            source_path=source_path,
            output_dir=output_dir,
            storage_backend=storage_backend,
            sdp=sdp,
            project_id=project_id,
            location=location,
            min_likelihood=min_likelihood,
            client=client,
            ocr_languages=ocr_languages,
            custom_redactions=custom_redactions,
        )

    raise ValueError(
        "process_privacy_action_and_persist only supports action='redact' "
        "or action='data_mask'."
    )


__all__ = [
    "ProtectedArtifactResult",
    "process_redaction_and_persist",
    "process_data_mask_and_persist",
    "process_privacy_action_and_persist",
]
