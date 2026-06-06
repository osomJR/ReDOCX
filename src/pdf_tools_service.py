
from __future__ import annotations

"""
ReDOCX PDF Tools service layer.

This module is the orchestration bridge between:
- core/schema.py request and response models
- core/validation.py validation/result builders
- processing/pdf/* low-level PDF engines

It intentionally does not know about FastAPI, Auth0, Vercel, billing, or database
tables. API routes/workers should instantiate PdfToolsService with resolver hooks
that map PdfFilePayload.storage_key/upload_id to readable local file paths.

Supported actions:
- combine_pdf
- split_pdf
- edit_pdf
- compress_pdf
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Union
from uuid import uuid4

try:
    from src.schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        CombinePdfRequest,
        CompressPdfRequest,
        DocumentFileOutputFormat,
        DocumentFileResult,
        EditPdfRequest,
        FeatureType,
        PdfFilePayload,
        PdfFileSetPayload,
        PdfJobStatus,
        PdfPreviewResult,
        SplitPdfRequest,
    )
    from src.validation import (
        build_combine_pdf_result,
        build_compress_pdf_result,
        build_document_file_result,
        build_edit_pdf_result,
        build_pdf_job_result,
        build_pdf_preview_result,
        build_split_pdf_result,
        validate_analyzer_request,
        validate_analyzer_response,
    )
    from src.processing.pdf_tools.combine import PdfSource, combine_pdfs
    from src.processing.pdf_tools.compress import compress_pdf, estimate_compressed_size_mb
    from src.processing.pdf_tools.edit import edit_pdf
    from src.processing.pdf_tools.split import split_pdf
except ImportError:  # pragma: no cover - useful when this file is placed inside src/services
    from .schema import (
        AnalyzerRequest,
        AnalyzerResponse,
        CombinePdfRequest,
        CompressPdfRequest,
        DocumentFileOutputFormat,
        DocumentFileResult,
        EditPdfRequest,
        FeatureType,
        PdfFilePayload,
        PdfFileSetPayload,
        PdfJobStatus,
        PdfPreviewResult,
        SplitPdfRequest,
    )
    from .validation import (
        build_combine_pdf_result,
        build_compress_pdf_result,
        build_document_file_result,
        build_edit_pdf_result,
        build_pdf_job_result,
        build_pdf_preview_result,
        build_split_pdf_result,
        validate_analyzer_request,
        validate_analyzer_response,
    )
    from .processing.pdf_tools.combine import PdfSource, combine_pdfs
    from .processing.pdf_tools.compress import compress_pdf, estimate_compressed_size_mb
    from .processing.pdf_tools.edit import edit_pdf
    from .processing.pdf_tools.split import split_pdf


SourcePathResolver = Callable[[PdfFilePayload], str | Path]
AssetPathResolver = Callable[[str], str | Path]


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


class CompressionJobQueue(Protocol):
    """
    Optional queue adapter for async compression.

    Implement this against BullMQ, Celery, RQ, Dramatiq, Trigger.dev, Inngest,
    or your own DB-backed job table. The service only needs a returned job id.
    """

    def enqueue_compress_pdf(
        self,
        *,
        request: AnalyzerRequest,
        source_path: str,
        output_filename: str,
        compression_level: str,
    ) -> str:
        ...


@dataclass(frozen=True)
class PdfToolsServiceConfig:
    algorithm_version: Optional[str] = "pdf-tools-service-v1.0.0"
    combine_artifacts_dir: str = "artifacts/pdf_tools/combine"
    split_artifacts_dir: str = "artifacts/pdf_tools/split"
    edit_artifacts_dir: str = "artifacts/pdf_tools/edit"
    edit_preview_artifacts_dir: str = "artifacts/pdf_tools/preview"
    compress_artifacts_dir: str = "artifacts/pdf_tools/compress"
    default_font_path: Optional[str] = None
    archive_split_outputs: bool = True
    allow_larger_compressed_output: bool = False

    # If True, an async compression request is processed immediately when no
    # queue adapter is configured. If False, the service returns a queued job id.
    process_async_compression_inline_without_queue: bool = True


class PdfToolsService:
    """
    Contract-first orchestration service for ReDOCX PDF Tools.

    API route usage:
        service = PdfToolsService(source_path_resolver=...)
        response = service.process(analyzer_request)

    The source_path_resolver is important in production because PdfFilePayload
    may contain storage_key/upload_id rather than a direct filesystem path.
    """

    PDF_ACTIONS = {
        FeatureType.combine_pdf,
        FeatureType.split_pdf,
        FeatureType.edit_pdf,
        FeatureType.compress_pdf,
    }

    def __init__(
        self,
        *,
        config: Optional[PdfToolsServiceConfig] = None,
        storage_backend: Optional[StorageBackend] = None,
        source_path_resolver: Optional[SourcePathResolver] = None,
        asset_path_resolver: Optional[AssetPathResolver] = None,
        compression_queue: Optional[CompressionJobQueue] = None,
    ) -> None:
        self.config = config or PdfToolsServiceConfig()
        self.storage_backend = storage_backend
        self.source_path_resolver = source_path_resolver
        self.asset_path_resolver = asset_path_resolver
        self.compression_queue = compression_queue

    def process(self, request: Union[AnalyzerRequest, Mapping[str, Any]]) -> AnalyzerResponse:
        req = validate_analyzer_request(request)

        if req.action not in self.PDF_ACTIONS:
            raise ValueError(f"PdfToolsService cannot handle action: {req.action.value}")

        if req.action == FeatureType.combine_pdf:
            response = self._handle_combine_pdf(req)
        elif req.action == FeatureType.split_pdf:
            response = self._handle_split_pdf(req)
        elif req.action == FeatureType.edit_pdf:
            response = self._handle_edit_pdf(req)
        elif req.action == FeatureType.compress_pdf:
            response = self._handle_compress_pdf(req)
        else:  # pragma: no cover - guarded above
            raise ValueError(f"Unsupported PDF tool action: {req.action.value}")

        return validate_analyzer_response(response, request=req)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_combine_pdf(self, request: AnalyzerRequest) -> AnalyzerResponse:
        if not isinstance(request.input, PdfFileSetPayload):
            raise ValueError("combine_pdf requires PdfFileSetPayload input.")
        if not isinstance(request.payload, CombinePdfRequest):
            raise ValueError("combine_pdf requires CombinePdfRequest payload.")

        sources = [
            PdfSource(
                path=str(self._resolve_pdf_path(pdf)),
                display_name=pdf.filename,
            )
            for pdf in request.input.documents
        ]

        artifact = combine_pdfs(
            sources,
            output_filename=request.payload.output_filename,
            preserve_bookmarks=request.payload.preserve_bookmarks,
            preserve_metadata=request.payload.preserve_metadata,
            storage_backend=self.storage_backend,
            artifacts_dir=self.config.combine_artifacts_dir,
        )

        result = build_combine_pdf_result(
            filename=artifact.file_name,
            file_size_mb=artifact.file_size_mb,
            source_filenames=artifact.source_filenames,
            combined_page_count=artifact.combined_page_count,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=self.config.algorithm_version,
        )

        return self._response(request, result=result, input_format="pdf_file_set")

    def _handle_split_pdf(self, request: AnalyzerRequest) -> AnalyzerResponse:
        if not isinstance(request.input, PdfFilePayload):
            raise ValueError("split_pdf requires PdfFilePayload input.")
        if not isinstance(request.payload, SplitPdfRequest):
            raise ValueError("split_pdf requires SplitPdfRequest payload.")

        source_path = self._resolve_pdf_path(request.input)
        artifact = split_pdf(
            source_path,
            mode=request.payload.mode,
            selected_pages=request.payload.selected_pages,
            page_ranges=request.payload.page_ranges,
            output_basename=request.payload.output_basename,
            storage_backend=self.storage_backend,
            artifacts_dir=self.config.split_artifacts_dir,
            archive_multiple_outputs=self.config.archive_split_outputs,
        )

        output_files = [
            self._pdf_artifact_to_document_file_result(item)
            for item in artifact.output_files
        ]

        archive_file = None
        if artifact.archive_file is not None:
            archive_file = self._archive_artifact_to_document_file_result(artifact.archive_file)

        result = build_split_pdf_result(
            mode=request.payload.mode,
            output_files=output_files,
            archive_file=archive_file,
            algorithm_version=self.config.algorithm_version,
        )

        return self._response(request, result=result, input_format="pdf_file")

    def _handle_edit_pdf(self, request: AnalyzerRequest) -> AnalyzerResponse:
        if not isinstance(request.input, PdfFilePayload):
            raise ValueError("edit_pdf requires PdfFilePayload input.")
        if not isinstance(request.payload, EditPdfRequest):
            raise ValueError("edit_pdf requires EditPdfRequest payload.")

        source_path = self._resolve_pdf_path(request.input)
        artifact = edit_pdf(
            source_path,
            operations=request.payload.operations,
            output_filename=request.payload.output_filename,
            generate_preview=request.payload.generate_preview,
            storage_backend=self.storage_backend,
            artifacts_dir=self.config.edit_artifacts_dir,
            preview_artifacts_dir=self.config.edit_preview_artifacts_dir,
            asset_resolver=self._resolve_asset_path if self.asset_path_resolver is not None else None,
            default_font_path=self.config.default_font_path,
        )

        preview = None
        if artifact.preview is not None:
            preview = build_pdf_preview_result(
                filename=artifact.preview.file_name,
                file_size_mb=artifact.preview.file_size_mb,
                page_count=artifact.preview.page_count,
                preview_stage=artifact.preview.preview_stage,
                storage_key=artifact.preview.storage_key,
                download_url=artifact.preview.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        result = build_edit_pdf_result(
            filename=artifact.file_name,
            file_size_mb=artifact.file_size_mb,
            operations_requested=artifact.operations_requested,
            operations_applied=artifact.operations_applied,
            preview=preview,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=self.config.algorithm_version,
        )

        return self._response(request, result=result, input_format="pdf_file")

    def _handle_compress_pdf(self, request: AnalyzerRequest) -> AnalyzerResponse:
        if not isinstance(request.input, PdfFilePayload):
            raise ValueError("compress_pdf requires PdfFilePayload input.")
        if not isinstance(request.payload, CompressPdfRequest):
            raise ValueError("compress_pdf requires CompressPdfRequest payload.")

        source_path = self._resolve_pdf_path(request.input)
        level = getattr(request.payload.compression_level, "value", request.payload.compression_level)

        if request.payload.async_processing and self.compression_queue is not None:
            job_id = self.compression_queue.enqueue_compress_pdf(
                request=request,
                source_path=str(source_path),
                output_filename=request.payload.output_filename,
                compression_level=str(level),
            )
            job_result = build_pdf_job_result(
                job_id=job_id,
                status=PdfJobStatus.queued,
                message="Compression job queued.",
                result=None,
                algorithm_version=self.config.algorithm_version,
            )
            return self._response(request, result=job_result, input_format="pdf_file")

        if (
            request.payload.async_processing
            and self.compression_queue is None
            and not self.config.process_async_compression_inline_without_queue
        ):
            job_result = build_pdf_job_result(
                job_id=f"pdfjob_{uuid4().hex}",
                status=PdfJobStatus.queued,
                message=(
                    "Compression was requested asynchronously, but no queue adapter "
                    "is configured. Persist this job id in your API layer before processing."
                ),
                result=None,
                algorithm_version=self.config.algorithm_version,
            )
            return self._response(request, result=job_result, input_format="pdf_file")

        artifact = compress_pdf(
            source_path,
            compression_level=request.payload.compression_level,
            output_filename=request.payload.output_filename,
            storage_backend=self.storage_backend,
            artifacts_dir=self.config.compress_artifacts_dir,
            allow_larger_output=self.config.allow_larger_compressed_output,
        )

        # The response contract compares original_file_size_mb to request.input.metadata.file_size_mb.
        # Use the validated metadata value as the canonical API value.
        original_file_size_mb = request.input.metadata.file_size_mb
        compressed_file_size_mb = min(artifact.compressed_file_size_mb, original_file_size_mb)
        compression_ratio = (
            round(compressed_file_size_mb / original_file_size_mb, 4)
            if original_file_size_mb > 0
            else None
        )

        result = build_compress_pdf_result(
            filename=artifact.file_name,
            file_size_mb=artifact.file_size_mb,
            compression_level=request.payload.compression_level,
            original_file_size_mb=original_file_size_mb,
            compressed_file_size_mb=compressed_file_size_mb,
            estimated_output_file_size_mb=(
                artifact.estimated_output_file_size_mb
                if artifact.estimated_output_file_size_mb is not None
                else estimate_compressed_size_mb(original_file_size_mb, request.payload.compression_level)
            ),
            compression_ratio=compression_ratio,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=self.config.algorithm_version,
        )

        return self._response(request, result=result, input_format="pdf_file")

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def _resolve_pdf_path(self, payload: PdfFilePayload) -> Path:
        if self.source_path_resolver is not None:
            resolved = Path(self.source_path_resolver(payload)).expanduser().resolve()
            return self._require_existing_pdf(resolved)

        candidates = [
            getattr(payload, "local_path", None),
            getattr(payload, "file_path", None),
            getattr(payload, "path", None),
            payload.storage_key,
            payload.upload_id,
            payload.filename,
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                path = Path(candidate.strip()).expanduser()
                if path.exists():
                    return self._require_existing_pdf(path.resolve())

        raise ValueError(
            "Could not resolve PdfFilePayload to a readable local PDF path. "
            "Pass source_path_resolver to PdfToolsService so storage_key/upload_id "
            "can be mapped to a backend file path."
        )

    def _resolve_asset_path(self, storage_key: str) -> str:
        if self.asset_path_resolver is None:
            path = Path(storage_key).expanduser().resolve()
        else:
            path = Path(self.asset_path_resolver(storage_key)).expanduser().resolve()

        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"PDF edit asset not found: {storage_key}")
        return str(path)

    @staticmethod
    def _require_existing_pdf(path: Path) -> Path:
        if not path.exists():
            raise FileNotFoundError(f"PDF source not found: {path}")
        if not path.is_file():
            raise ValueError(f"PDF source is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF source must end with .pdf: {path.name}")
        return path

    def _pdf_artifact_to_document_file_result(self, artifact: Any) -> DocumentFileResult:
        return build_document_file_result(
            filename=artifact.file_name,
            output_format=DocumentFileOutputFormat.pdf,
            file_size_mb=artifact.file_size_mb,
            storage_key=getattr(artifact, "storage_key", None),
            download_url=getattr(artifact, "download_url", None),
            algorithm_version=self.config.algorithm_version,
        )

    def _archive_artifact_to_document_file_result(self, artifact: Any) -> DocumentFileResult:
        # Current schema models archive_file as DocumentFileResult even though
        # DocumentFileOutputFormat has no zip value. Use pdf as a carrier until
        # schema.py adds a generic ArchiveFileResult or zip output format.
        return build_document_file_result(
            filename=artifact.file_name,
            output_format=DocumentFileOutputFormat.pdf,
            file_size_mb=artifact.file_size_mb,
            storage_key=getattr(artifact, "storage_key", None),
            download_url=getattr(artifact, "download_url", None),
            algorithm_version=self.config.algorithm_version,
        )

    def _response(self, request: AnalyzerRequest, *, result: Any, input_format: str) -> AnalyzerResponse:
        return AnalyzerResponse(
            action=request.action,
            input_format=input_format,
            policy=request.policy,
            system_language=request.system_language,
            result=result,
        )


__all__ = [
    "PdfToolsService",
    "PdfToolsServiceConfig",
    "SourcePathResolver",
    "AssetPathResolver",
    "CompressionJobQueue",
]
