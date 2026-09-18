from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Union

from backend.src.storage.artifacts import LocalArtifactStorage

from .processing.conversion.convert import convert_document
from .processing.llm.explain import explain_text
from .processing.llm.generate_answers import generate_answers_text
from .processing.llm.generate_questions import generate_questions_text
from .processing.llm.grammar_correct import grammar_correct_text
from .schema import (
    AnalyzerRequest,
    AnalyzerResponse,
    AudioFormat,
    AnswerGenerationFileResult,
    AnswerGenerationInlineResult,
    AnswerGenerationRequest,
    ConversionRequest,
    DeterminismMetadata,
    DocumentFileOutputFormat,
    DocumentFileResult,
    DocumentInputFormat,
    DocumentPayload,
    DocumentSetPayload,
    ExplanationRequest,
    FeatureType,
    GrammarCorrectionRequest,
    InlineTextResult,
    MediaPayload,
    MediaType,
    QuestionGenerationFileResult,
    QuestionGenerationInlineResult,
    QuestionGenerationRequest,
    SummarizationRequest,
    SystemLanguage,
    TextToSpeechRequest,
    TextToSpeechResult,
    TranscriptionPlaybackArtifact,
    TranscriptionRequest,
    TranscriptionResult,
    TranslationRequest,
    VaultDeleteResult,
    VaultFilePayload,
    VaultItemReferencePayload,
    VaultItemResult,
    VaultListResult,
    VaultQueryPayload,
    VaultRequest,
    VaultTextPayload,
    VideoFormat,
)
from .inline_text_security import (
    validate_text_to_speech_inline_text,
    validate_vault_inline_text,
)
from .processing.llm.summarize import summarize_text
from .processing.asr.transcribe import build_browser_playback_rendition, transcribe_media
from .processing.llm.translate import translate_text
from .validation import (
    build_answer_generation_file_result,
    build_answer_generation_inline_result,
    build_document_file_result,
    build_inline_txt_result,
    build_transcription_result,
    build_question_generation_file_result,
    build_question_generation_inline_result,
    get_question_range,
    validate_analyzer_request,
    validate_analyzer_response,
)
from .processing.llm.writer import (
    write_document,
    write_document_preserving_source_layout,
    write_summary_preserving_source_layout,
)


TextResult = Union[InlineTextResult, DocumentFileResult]
QuestionResult = Union[QuestionGenerationInlineResult, QuestionGenerationFileResult]
AnswerResult = Union[AnswerGenerationInlineResult, AnswerGenerationFileResult]
VaultResult = Union[VaultItemResult, VaultListResult, VaultDeleteResult]


class TextToSpeechEngine(Protocol):
    """Engine boundary for provider-specific speech synthesis implementation."""

    def process(self, request: AnalyzerRequest) -> TextToSpeechResult:
        ...


class VaultEngine(Protocol):
    """Engine boundary for owner-scoped Vault persistence/retrieval implementation."""

    def process(
        self,
        request: AnalyzerRequest,
        *,
        text_validator: Callable[[str], str],
    ) -> VaultResult:
        ...

_UNIMPLEMENTED_ACTIONS = {
    FeatureType.redact,
    FeatureType.data_mask,
    FeatureType.structured_extract,
    FeatureType.compliance,
}


@dataclass(frozen=True)
class AnalyzerConfig:
    """
    Deterministic analyzer configuration.

    Notes:
    - algorithm_version is echoed into response metadata builders.
    - include_language_fields controls whether optional response language fields
      are emitted when the analyzer can do so accurately.
    - For non-translate actions, this analyzer intentionally omits optional
      language fields because no detection module is attached here.
    """

    algorithm_version: Optional[str] = None
    include_language_fields: bool = True


class Analyzer:
    """
    Contract-first orchestration layer.

    Responsibilities:
    - validate requests against schema.py and validation.py
    - route supported actions to the attached processing modules
    - construct schema-compliant response/result models
    - validate the final response before returning it

    Important scope note:
    - This analyzer only orchestrates actions for which processing modules are
      actually attached in this file set.
    - redact, data_mask, structured_extract, and compliance are present in the
      schema, but corresponding processor modules were not attached here, so
      this analyzer raises NotImplementedError for those actions rather than
      inventing unsupported behavior.
    """

    def __init__(
        self,
        config: Optional[AnalyzerConfig] = None,
        *,
        text_to_speech_engine: Optional[TextToSpeechEngine] = None,
        vault_engine: Optional[VaultEngine] = None,
    ) -> None:
        self.config = config or AnalyzerConfig()
        self.text_to_speech_engine = text_to_speech_engine
        self.vault_engine = vault_engine

    def analyze(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
    ) -> AnalyzerResponse:
        req = validate_analyzer_request(request)

        if req.action in _UNIMPLEMENTED_ACTIONS:
            raise NotImplementedError(
                f"{req.action.value} is handled by a dedicated workflow outside Analyzer."
            )

        if req.action == FeatureType.convert:
            result = self._handle_convert(req)
            detected_language, output_language = None, None
        elif req.action == FeatureType.transcribe:
            result = self._handle_transcribe(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.text_to_speech:
            result = self._handle_text_to_speech(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.summarize:
            result = self._handle_summarize(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.grammar_correct:
            result = self._handle_grammar(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.translate:
            result = self._handle_translate(req)
            detected_language, output_language = self._language_fields_for_translate(req)
        elif req.action == FeatureType.explain:
            result = self._handle_explain(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.generate_questions:
            result = self._handle_generate_questions(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.generate_answers:
            result = self._handle_generate_answers(req)
            detected_language, output_language = self._language_fields_for_non_translate()
        elif req.action == FeatureType.vault:
            result = self._handle_vault(req)
            detected_language, output_language = None, None
        else:
            raise ValueError(f"Unsupported action: {req.action.value}")

        response = self._build_response(
            req,
            result=result,
            detected_language=detected_language,
            output_language=output_language,
        )
        return validate_analyzer_response(response, request=req)

    def _handle_convert(self, req: AnalyzerRequest) -> DocumentFileResult:
        if not isinstance(req.payload, ConversionRequest):
            raise ValueError("convert requires ConversionRequest payload.")
        document = self._require_document_input(req, action="convert")
        source_reference = self._require_document_source_reference(document, action="convert")

        artifact = convert_document(
            input_format=document.metadata.input_format.value,
            output_format=req.payload.output_format.value,
            source_reference=source_reference,
            source_name_hint=Path(source_reference).name,
        )

        return build_document_file_result(
            filename=artifact.file_name,
            output_format=_document_file_output_from_string(artifact.file_extension),
            file_size_mb=artifact.file_size_mb,
            storage_key=artifact.storage_key,
            download_url=artifact.download_url,
            algorithm_version=self.config.algorithm_version,
        )

    def _handle_transcribe(self, req: AnalyzerRequest) -> TranscriptionResult:
        if not isinstance(req.payload, TranscriptionRequest):
            raise ValueError("transcribe requires TranscriptionRequest payload.")
        if not isinstance(req.input, MediaPayload):
            raise ValueError("transcribe requires MediaPayload input.")

        file_reference = self._require_media_source_reference(req.input)

        transcription = transcribe_media(
            media_type=req.input.media_type.value,
            media_format=req.input.media_format.value,
            file_reference=file_reference,
            preserve_filler_words=req.payload.preserve_filler_words,
            remove_background_noise=req.payload.remove_background_noise,
            diarize_speakers=req.payload.diarize_speakers,
        )
        content = transcription.content

        output_name = self._planned_transcript_output_name(req.input)

        written = write_document(
            content=content,
            output_format=DocumentFileOutputFormat.pdf.value,
            output_name=output_name,
        )
    
        pdf_artifact = build_document_file_result(
            filename=written.file_name,
            output_format=DocumentFileOutputFormat.pdf,
            file_size_mb=written.file_size_mb,
            storage_key=written.storage_key,
            download_url=written.download_url,
            algorithm_version=self.config.algorithm_version,
        )

        with build_browser_playback_rendition(
            media_type=req.input.media_type.value,
            file_reference=file_reference,
        ) as rendition:
            stored_playback = LocalArtifactStorage().persist(
                source_file_path=rendition.file_path,
                artifact_name=rendition.filename,
                content_type=rendition.mime_type,
                feature="transcribe_playback",
            )

        playback_path = Path(stored_playback.stored_path)
        playback_artifact = TranscriptionPlaybackArtifact(
            filename=stored_playback.original_artifact_name,
            file_size_mb=round(playback_path.stat().st_size / (1024 * 1024), 6),
            storage_key=stored_playback.storage_key,
            download_url=stored_playback.download_url,
            media_type=req.input.media_type,
            media_format=(
                AudioFormat.m4a
                if req.input.media_type == MediaType.audio
                else VideoFormat.mp4
            ),
            mime_type=rendition.mime_type,
            meta=DeterminismMetadata(algorithm_version=self.config.algorithm_version),
        )

        return build_transcription_result(
            content=content,
            pdf_artifact=pdf_artifact,
            playback_artifact=playback_artifact,
            subtitle_cues=[
                {
                    "start_seconds": cue.start_seconds,
                    "end_seconds": cue.end_seconds,
                    "text": cue.text,
                    "speaker_label": cue.speaker_label,
                }
                for cue in transcription.subtitle_cues
            ],
            algorithm_version=self.config.algorithm_version,
        )

    def _handle_text_to_speech(self, req: AnalyzerRequest) -> TextToSpeechResult:
        if not isinstance(req.payload, TextToSpeechRequest):
            raise ValueError("text_to_speech requires TextToSpeechRequest payload.")

        document = self._require_document_input(req, action="text_to_speech")
        source_text = self._require_document_text(document, action="text_to_speech")

        # Inline text has a deliberately tighter browser/API resource policy than
        # uploaded documents. Apply that policy only to a true inline TXT payload
        # (no persisted filename). Uploaded PDF/DOCX/TXT inputs have already passed
        # upload security and extraction validation and may legitimately exceed the
        # inline 20,000-character ceiling; the TTS engine handles them in provider-
        # sized chunks without changing the extracted source text.
        if document.metadata.input_format == DocumentInputFormat.txt and not document.filename:
            secured_text = validate_text_to_speech_inline_text(source_text)
            if secured_text != source_text:
                document.text = secured_text
                document.metadata.extracted_word_count = len(secured_text.split())

        if self.text_to_speech_engine is None:
            raise NotImplementedError(
                "Text-to-Speech engine is not attached to Analyzer."
            )

        result = self.text_to_speech_engine.process(req)
        if not isinstance(result, TextToSpeechResult):
            raise TypeError("Text-to-Speech engine must return TextToSpeechResult.")
        return result

    def _handle_vault(self, req: AnalyzerRequest) -> VaultResult:
        if not isinstance(req.payload, VaultRequest):
            raise ValueError("vault requires VaultRequest payload.")
        if not isinstance(
            req.input,
            (
                VaultFilePayload,
                VaultTextPayload,
                VaultItemReferencePayload,
                VaultQueryPayload,
            ),
        ):
            raise ValueError(
                "vault requires VaultFilePayload, VaultTextPayload, "
                "VaultItemReferencePayload, or VaultQueryPayload input."
            )

        # VaultTextPayload must retain byte-for-byte user content. The validator
        # therefore rejects unsafe/resource-abusive text without normalizing it.
        # For persisted .txt VaultFilePayload inputs, the future Vault engine must
        # call the same validator after resolving and decoding the stored file.
        if isinstance(req.input, VaultTextPayload):
            validate_vault_inline_text(req.input.text)

        if self.vault_engine is None:
            raise NotImplementedError("Vault engine is not attached to Analyzer.")

        result = self.vault_engine.process(
            req,
            text_validator=validate_vault_inline_text,
        )
        if not isinstance(result, (VaultItemResult, VaultListResult, VaultDeleteResult)):
            raise TypeError(
                "Vault engine must return VaultItemResult, VaultListResult, or VaultDeleteResult."
            )
        return result

    def _handle_summarize(self, req: AnalyzerRequest) -> TextResult:
        if not isinstance(req.payload, SummarizationRequest):
            raise ValueError("summarize requires SummarizationRequest payload.")

        document = self._require_document_input(req, action="summarize")
        source_text = self._require_document_text(document, action="summarize")
        content = summarize_text(source_text)
        input_format = document.metadata.input_format

        if input_format == DocumentInputFormat.txt:
            return build_inline_txt_result(
                content=content,
                algorithm_version=self.config.algorithm_version,
            )

        if input_format in {DocumentInputFormat.pdf, DocumentInputFormat.docx}:
            output_format = _matching_document_output_format(input_format)
            output_name = self._planned_output_name(
                document,
                suffix="summary",
                output_format=output_format,
            )

            # OCR-only / hybrid PDF text has no reliable source font metadata for
            # every summarized token. Retain the existing generated-document path
            # for those PDFs rather than pretending typography can be preserved.
            # Native-text PDF and DOCX inputs use the source-aware writer below.
            if input_format == DocumentInputFormat.pdf and document.metadata.ocr_used:
                written = write_document(
                    content=content,
                    output_format=output_format.value,
                    output_name=output_name,
                )
            else:
                written = write_summary_preserving_source_layout(
                    content=content,
                    source_file_path=self._require_document_source_reference(
                        document,
                        action="summarize",
                    ),
                    output_format=output_format.value,
                    output_name=output_name,
                )

            return build_document_file_result(
                filename=written.file_name,
                output_format=output_format,
                file_size_mb=written.file_size_mb,
                storage_key=written.storage_key,
                download_url=written.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        raise ValueError("summarize only supports input formats: pdf, docx, txt.")

    def _handle_grammar(self, req: AnalyzerRequest) -> TextResult:
        if not isinstance(req.payload, GrammarCorrectionRequest):
            raise ValueError("grammar_correct requires GrammarCorrectionRequest payload.")

        document = self._require_document_input(req, action="grammar_correct")
        source_text = self._require_document_text(document, action="grammar_correct")
        content = grammar_correct_text(source_text)
        input_format = document.metadata.input_format

        if input_format == DocumentInputFormat.txt:
            return build_inline_txt_result(
                content=content,
                algorithm_version=self.config.algorithm_version,
            )

        if input_format in {DocumentInputFormat.pdf, DocumentInputFormat.docx}:
            output_format = _matching_document_output_format(input_format)
            output_name = self._planned_output_name(
                document,
                suffix="grammar-corrected",
                output_format=output_format,
            )
            written = write_document_preserving_source_layout(
                content=content,
                source_file_path=self._require_document_source_reference(
                    document,
                    action="grammar_correct",
                ),
                output_format=output_format.value,
                output_name=output_name,
            )
            return build_document_file_result(
                filename=written.file_name,
                output_format=output_format,
                file_size_mb=written.file_size_mb,
                storage_key=written.storage_key,
                download_url=written.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        raise ValueError("grammar_correct only supports input formats: pdf, docx, txt.")

    def _handle_translate(self, req: AnalyzerRequest) -> TextResult:
        if not isinstance(req.payload, TranslationRequest):
            raise ValueError("translate requires TranslationRequest payload.")
        return self._handle_text_ai_document_action(
            req,
            transform=lambda text: translate_text(
                text,
                source_language=req.payload.source_language,
                target_language=req.payload.target_language,
            ),
            output_suffix="translated",
            output_language_tag=req.payload.target_language,
        )

    def _handle_explain(self, req: AnalyzerRequest) -> TextResult:
        if not isinstance(req.payload, ExplanationRequest):
            raise ValueError("explain requires ExplanationRequest payload.")
        return self._handle_text_ai_document_action(
            req,
            transform=explain_text,
            output_suffix="explained",
        )

    def _handle_generate_questions(self, req: AnalyzerRequest) -> QuestionResult:
        if not isinstance(req.payload, QuestionGenerationRequest):
            raise ValueError("generate_questions requires QuestionGenerationRequest payload.")

        document = self._require_document_input(req, action="generate_questions")
        source_text = self._require_document_text(document, action="generate_questions")
        word_count = self._require_extracted_word_count(document, action="generate_questions")
        min_questions, max_questions = get_question_range(word_count)

        content = generate_questions_text(
            source_text,
            min_questions=min_questions,
            max_questions=max_questions,
        )

        input_format = document.metadata.input_format
        if input_format == DocumentInputFormat.txt:
            return build_question_generation_inline_result(
                content=content,
                extracted_word_count=word_count,
                algorithm_version=self.config.algorithm_version,
            )

        if input_format in {DocumentInputFormat.pdf, DocumentInputFormat.docx}:
            output_format = _matching_document_output_format(input_format)
            output_name = self._planned_output_name(document, suffix="questions", output_format=output_format)
            written = write_document(
                content=content,
                output_format=output_format.value,
                output_name=output_name,
            )
            return build_question_generation_file_result(
                filename=written.file_name,
                output_format=output_format,
                file_size_mb=written.file_size_mb,
                extracted_word_count=word_count,
                generated_questions_text=content,
                storage_key=written.storage_key,
                download_url=written.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        raise ValueError("generate_questions only supports input formats: pdf, docx, txt.")

    def _handle_generate_answers(self, req: AnalyzerRequest) -> AnswerResult:
        if not isinstance(req.payload, AnswerGenerationRequest):
            raise ValueError("generate_answers requires AnswerGenerationRequest payload.")

        document = self._require_document_input(req, action="generate_answers")
        source_text = self._require_document_text(document, action="generate_answers")
        expected_question_count = len(req.payload.questions)
        if expected_question_count < 1:
            raise ValueError("questions cannot be empty.")

        questions_text = "\n".join(req.payload.questions)
        content = generate_answers_text(
            source_text,
            questions_text=questions_text,
            expected_question_count=expected_question_count,
        )

        input_format = document.metadata.input_format
        if input_format == DocumentInputFormat.txt:
            return build_answer_generation_inline_result(
                content=content,
                expected_question_count=expected_question_count,
                algorithm_version=self.config.algorithm_version,
            )

        if input_format in {DocumentInputFormat.pdf, DocumentInputFormat.docx}:
            output_format = _matching_document_output_format(input_format)
            output_name = self._planned_output_name(document, suffix="answers", output_format=output_format)
            written = write_document(
                content=content,
                output_format=output_format.value,
                output_name=output_name,
            )
            return build_answer_generation_file_result(
                filename=written.file_name,
                output_format=output_format,
                file_size_mb=written.file_size_mb,
                expected_question_count=expected_question_count,
                storage_key=written.storage_key,
                download_url=written.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        raise ValueError("generate_answers only supports input formats: pdf, docx, txt.")

    def _handle_text_ai_document_action(
        self,
        req: AnalyzerRequest,
        *,
        transform: Callable[[str], str],
        output_suffix: str,
        output_language_tag: Optional[str] = None,
    ) -> TextResult:
        document = self._require_document_input(req, action=req.action.value)
        source_text = self._require_document_text(document, action=req.action.value)
        input_format = document.metadata.input_format
        content = transform(source_text)

        if input_format == DocumentInputFormat.txt:
            return build_inline_txt_result(
                content=content,
                algorithm_version=self.config.algorithm_version,
            )

        if input_format in {DocumentInputFormat.pdf, DocumentInputFormat.docx}:
            output_format = _matching_document_output_format(input_format)
            output_name = self._planned_output_name(document, suffix=output_suffix, output_format=output_format)
            written = write_document(
                content=content,
                output_format=output_format.value,
                output_name=output_name,
                language_tag=output_language_tag,
            )
            return build_document_file_result(
                filename=written.file_name,
                output_format=output_format,
                file_size_mb=written.file_size_mb,
                storage_key=written.storage_key,
                download_url=written.download_url,
                algorithm_version=self.config.algorithm_version,
            )

        raise ValueError(f"{req.action.value} only supports input formats: pdf, docx, txt.")

    def _build_response(
        self,
        req: AnalyzerRequest,
        *,
        result: Any,
        detected_language: Optional[str],
        output_language: Optional[str],
    ) -> AnalyzerResponse:
        input_format: Union[DocumentInputFormat, str]
        if isinstance(req.input, MediaPayload):
            input_format = "audio" if req.input.media_type == MediaType.audio else "video"
        elif isinstance(req.input, DocumentSetPayload):
            input_format = "document_set"
        elif isinstance(
            req.input,
            (VaultFilePayload, VaultTextPayload, VaultItemReferencePayload, VaultQueryPayload),
        ):
            input_format = req.input.kind
        else:
            input_format = req.input.metadata.input_format

        return AnalyzerResponse(
            action=req.action,
            input_format=input_format,
            policy=req.policy,
            system_language=req.system_language,
            detected_language=detected_language if self.config.include_language_fields else None,
            output_language=output_language if self.config.include_language_fields else None,
            result=result,
        )

    def _language_fields_for_non_translate(self) -> tuple[Optional[str], Optional[str]]:
        """
        No detector is attached in this file set for non-translate actions.

        The schema treats these response fields as optional, so the analyzer omits
        them rather than inventing potentially incorrect language metadata.
        """
        return None, None

    def _language_fields_for_translate(
        self,
        req: AnalyzerRequest,
    ) -> tuple[Optional[str], Optional[str]]:
        if not isinstance(req.payload, TranslationRequest):
            return None, None

        detected_language = None
        if req.payload.source_language != "auto":
            detected_language = req.payload.source_language

        return detected_language, req.payload.target_language

    @staticmethod
    def _require_document_input(req: AnalyzerRequest, *, action: str) -> DocumentPayload:
        if not isinstance(req.input, DocumentPayload):
            raise ValueError(f"{action} requires DocumentPayload input.")
        return req.input

    @staticmethod
    def _require_document_text(document: DocumentPayload, *, action: str) -> str:
        if not document.text or not document.text.strip():
            raise ValueError(f"{action} requires extracted document text.")
        return document.text

    @staticmethod
    def _require_extracted_word_count(document: DocumentPayload, *, action: str) -> int:
        word_count = document.metadata.extracted_word_count
        if word_count is None:
            raise ValueError(f"{action} requires extracted_word_count.")
        return int(word_count)

    @staticmethod
    def _require_document_source_reference(document: DocumentPayload, *, action: str) -> str:
        if not document.filename or not str(document.filename).strip():
            raise ValueError(
                f"{action} requires DocumentPayload.filename to contain a real persisted source reference."
            )
        return str(document.filename)

    @staticmethod
    def _require_media_source_reference(media: MediaPayload) -> str:
        if not media.filename or not str(media.filename).strip():
            raise ValueError(
                "transcribe requires MediaPayload.filename to contain a real persisted source reference."
            )
        return str(media.filename)

    @staticmethod
    def _planned_output_name(
        document: DocumentPayload,
        *,
        suffix: str,
        output_format: DocumentFileOutputFormat,
    ) -> str:
        base_source = document.filename or f"document.{document.metadata.input_format.value}"
        stem = _safe_basename(Path(str(base_source)).stem)
        normalized_suffix = _safe_basename(suffix)
        return f"{stem}.{normalized_suffix}.{output_format.value}"
    
    @staticmethod
    def _planned_transcript_output_name(media: MediaPayload) -> str:
        base_source = media.filename or f"media.{media.media_format.value}"
        stem = _safe_basename(Path(str(base_source)).stem)
        return f"{stem}.transcript.pdf"

def _document_file_output_from_string(value: str) -> DocumentFileOutputFormat:
    normalized = value.strip().lower()
    try:
        return DocumentFileOutputFormat(normalized)
    except ValueError as exc:
        raise ValueError(f"Unsupported document file output format: {value}") from exc



def _matching_document_output_format(input_format: DocumentInputFormat) -> DocumentFileOutputFormat:
    if input_format == DocumentInputFormat.pdf:
        return DocumentFileOutputFormat.pdf
    if input_format == DocumentInputFormat.docx:
        return DocumentFileOutputFormat.docx
    raise ValueError("Only pdf and docx inputs map to downloadable text-AI document outputs.")



def _safe_basename(value: str) -> str:
    raw = str(value).strip().lower()
    if not raw:
        return "document"
    filtered = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    compact = "-".join(part for part in filtered.split("-") if part)
    return compact or "document"


__all__ = ["Analyzer", "AnalyzerConfig"]
