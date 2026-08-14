from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Type, Union

from pydantic import BaseModel

from .schema import (
    AnalyzerRequest,
    AnalyzerResponse,
    ArchiveFileResult,
    AnswerGenerationFileResult,
    AnswerGenerationInlineResult,
    AnswerGenerationRequest,
    BaseFileResult,
    CombinePdfRequest,
    CombinePdfResult,
    ComplianceFileResult,
    ComplianceOutputFormat,
    ComplianceReportVariant,
    ComplianceRequest,
    CompressPdfRequest,
    CompressPdfResult,
    ConversionRequest,
    DataMaskingRequest,
    DeterminismMetadata,
    DocumentFileOutputFormat,
    DocumentFileResult,
    DocumentInputFormat,
    DocumentPayload,
    DocumentSetPayload,
    ESignatureAction,
    ESignatureAuditEvent,
    ESignatureEnvelopeStatus,
    ESignatureFieldType,
    ESignatureRecipientResult,
    ESignatureRecipientStatus,
    ESignatureRequest,
    ESignatureResult,
    ESignatureStepPreview,
    ESignatureWorkflow,
    ESIGN_SIGNERS_PER_SIGNATURE_PAGE,
    EditPdfRequest,
    EditPdfResult,
    ExplanationRequest,
    FeatureType,
    GrammarCorrectionRequest,
    InlineTextResult,
    LockPdfRequest,
    LockPdfResult,
    MAX_PDF_PASSWORD_LENGTH,
    MediaPayload,
    MediaType,
    OutputPolicy,
    PdfFilePayload,
    PdfFileSetPayload,
    PdfJobResult,
    PdfJobStatus,
    PdfEncryptionAlgorithm,
    PdfPreviewResult,
    QuestionGenerationFileResult,
    QuestionGenerationInlineResult,
    QuestionGenerationRequest,
    QuestionScale,
    QuestionScaleMetadata,
    RedactionRequest,
    SplitPdfRequest,
    SplitPdfResult,
    SpeechAudioFormat,
    StructuredDataOutputFormat,
    StructuredExtractionFileResult,
    StructuredExtractionRequest,
    StructuredExtractionResultShape,
    SummarizationRequest,
    TranscriptionRequest,
    TranscriptionResult,
    TextToSpeechRequest,
    TextToSpeechResult,
    TranslationRequest,
    VaultDeleteResult,
    VaultFilePayload,
    VaultItemMetadata,
    VaultItemReferencePayload,
    VaultItemResult,
    VaultListResult,
    VaultOperation,
    VaultQueryPayload,
    VaultRequest,
    classify_word_count,
)
from .inline_text_security import (
    MAX_NUMBERED_QUESTIONS,
    validate_auxiliary_prompt_text,
    validate_inline_text,
    validate_question_item,
    validate_text_to_speech_inline_text,
)


# =========================
# Action groups
# =========================

TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT = {
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.translate,
    FeatureType.explain,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
}

PDF_DOCUMENT_ACTIONS = {
    FeatureType.combine_pdf,
    FeatureType.split_pdf,
    FeatureType.edit_pdf,
    FeatureType.compress_pdf,
    FeatureType.lock_pdf,
    FeatureType.e_signature,
}

PDF_SINGLE_FILE_ACTIONS = {
    FeatureType.split_pdf,
    FeatureType.edit_pdf,
    FeatureType.compress_pdf,
    FeatureType.lock_pdf,
    FeatureType.e_signature,
}

PDF_TRANSFORMED_ACTIONS = PDF_DOCUMENT_ACTIONS

SECURE_TRANSFORMED_ACTIONS = {
    FeatureType.text_to_speech,
    FeatureType.vault,
}

GENERATED_ACTIONS = {
    FeatureType.explain,
    FeatureType.structured_extract,
    FeatureType.compliance,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
}


_ACTION_PAYLOAD_MAP: dict[FeatureType, Type[BaseModel]] = {
    FeatureType.convert: ConversionRequest,
    FeatureType.summarize: SummarizationRequest,
    FeatureType.grammar_correct: GrammarCorrectionRequest,
    FeatureType.translate: TranslationRequest,
    FeatureType.transcribe: TranscriptionRequest,
    FeatureType.text_to_speech: TextToSpeechRequest,
    FeatureType.explain: ExplanationRequest,
    FeatureType.redact: RedactionRequest,
    FeatureType.data_mask: DataMaskingRequest,
    FeatureType.structured_extract: StructuredExtractionRequest,
    FeatureType.compliance: ComplianceRequest,
    FeatureType.generate_questions: QuestionGenerationRequest,
    FeatureType.generate_answers: AnswerGenerationRequest,
    FeatureType.vault: VaultRequest,
    FeatureType.combine_pdf: CombinePdfRequest,
    FeatureType.split_pdf: SplitPdfRequest,
    FeatureType.edit_pdf: EditPdfRequest,
    FeatureType.compress_pdf: CompressPdfRequest,
    FeatureType.lock_pdf: LockPdfRequest,
    FeatureType.e_signature: ESignatureRequest,
}


_ACTION_RESULT_TYPES: dict[FeatureType, tuple[type[BaseModel], ...]] = {
    FeatureType.convert: (DocumentFileResult,),
    FeatureType.summarize: (InlineTextResult, DocumentFileResult),
    FeatureType.grammar_correct: (InlineTextResult, DocumentFileResult),
    FeatureType.translate: (InlineTextResult, DocumentFileResult),
    FeatureType.transcribe: (TranscriptionResult,),
    FeatureType.text_to_speech: (TextToSpeechResult,),
    FeatureType.explain: (InlineTextResult, DocumentFileResult),
    FeatureType.redact: (DocumentFileResult,),
    FeatureType.data_mask: (DocumentFileResult,),
    FeatureType.structured_extract: (StructuredExtractionFileResult,),
    FeatureType.compliance: (ComplianceFileResult,),
    FeatureType.generate_questions: (QuestionGenerationInlineResult, QuestionGenerationFileResult),
    FeatureType.generate_answers: (AnswerGenerationInlineResult, AnswerGenerationFileResult),
    FeatureType.vault: (VaultItemResult, VaultListResult, VaultDeleteResult),
    FeatureType.combine_pdf: (CombinePdfResult,),
    FeatureType.split_pdf: (SplitPdfResult,),
    FeatureType.edit_pdf: (EditPdfResult,),
    FeatureType.compress_pdf: (CompressPdfResult, PdfJobResult),
    FeatureType.lock_pdf: (LockPdfResult,),
    FeatureType.e_signature: (ESignatureResult,),
}


_VAULT_INPUT_BY_OPERATION: dict[VaultOperation, type[BaseModel]] = {
    VaultOperation.store: VaultFilePayload,
    VaultOperation.retrieve: VaultItemReferencePayload,
    VaultOperation.list: VaultQueryPayload,
    VaultOperation.delete: VaultItemReferencePayload,
}

_VAULT_RESULT_BY_OPERATION: dict[VaultOperation, type[BaseModel]] = {
    VaultOperation.store: VaultItemResult,
    VaultOperation.retrieve: VaultItemResult,
    VaultOperation.list: VaultListResult,
    VaultOperation.delete: VaultDeleteResult,
}


# =========================
# Internal helpers
# =========================


def _iter_documents(input_artifact: object) -> list[DocumentPayload]:
    if isinstance(input_artifact, DocumentPayload):
        return [input_artifact]
    if isinstance(input_artifact, DocumentSetPayload):
        return list(input_artifact.documents)
    return []


def _iter_pdf_files(input_artifact: object) -> list[PdfFilePayload]:
    if isinstance(input_artifact, PdfFilePayload):
        return [input_artifact]
    if isinstance(input_artifact, PdfFileSetPayload):
        return list(input_artifact.documents)
    return []


def _pdf_identity(pdf: PdfFilePayload) -> str:
    """
    Stable source identity used to detect accidental duplicate uploads.

    filename alone is intentionally not used because two separate PDFs can have the
    same display name. Prefer storage_key, then upload_id, then checksum.
    """
    if pdf.storage_key:
        return f"storage:{pdf.storage_key}"
    if pdf.upload_id:
        return f"upload:{pdf.upload_id}"
    if pdf.metadata.checksum_sha256:
        return f"sha256:{pdf.metadata.checksum_sha256.lower()}"
    return f"filename:{pdf.filename}"


def _require_pdf_file_result(result: DocumentFileResult, *, field_name: str = "result") -> None:
    if result.output_format != DocumentFileOutputFormat.pdf:
        raise ValueError(f"{field_name} must be a PDF document file result.")
    if not result.filename.lower().endswith(".pdf"):
        raise ValueError(f"{field_name}.filename must end with .pdf.")


def _require_storage_or_download(result: BaseFileResult, *, field_name: str = "result") -> None:
    if not result.storage_key and not result.download_url:
        raise ValueError(f"{field_name} must include storage_key or download_url.")


def _field_assignees_by_type(request: ESignatureRequest, field_type: ESignatureFieldType) -> set[str]:
    return {field.assigned_to_email.lower() for field in request.fields if field.field_type == field_type}


def _required_signer_emails(request: ESignatureRequest) -> set[str]:
    emails: set[str] = set()
    if request.self_signer:
        emails.add(request.self_signer.email.lower())
    for recipient in request.recipients:
        if recipient.required:
            emails.add(recipient.email.lower())
    return emails


# =========================
# Core request validation
# =========================


def validate_action_payload_consistency(request: AnalyzerRequest) -> None:
    """
    Explicit action-to-payload type guard aligned with schema.FeaturePayload.
    """
    expected_model = _ACTION_PAYLOAD_MAP.get(request.action)
    if expected_model is None:
        raise ValueError(f"Unsupported action: {request.action}")

    if not isinstance(request.payload, expected_model):
        raise ValueError(
            f"payload type mismatch for action '{request.action.value}': "
            f"expected {expected_model.__name__}, got {type(request.payload).__name__}"
        )


def validate_input_payload_consistency(request: AnalyzerRequest) -> None:
    """
    Explicit action-to-input type guard aligned with schema.AnalyzerRequest.

    This gives clearer service-layer errors than relying only on Pydantic's
    union validation when requests come from API JSON.
    """
    if request.action == FeatureType.vault:
        if not isinstance(request.payload, VaultRequest):
            raise ValueError("vault requires VaultRequest payload.")
        expected_input = _VAULT_INPUT_BY_OPERATION[request.payload.operation]
        if not isinstance(request.input, expected_input):
            raise ValueError(
                f"vault operation '{request.payload.operation.value}' requires "
                f"{expected_input.__name__} input."
            )
        return

    if request.action == FeatureType.transcribe:
        if not isinstance(request.input, MediaPayload):
            raise ValueError("transcribe requires MediaPayload input.")
        return

    if request.action == FeatureType.combine_pdf:
        if not isinstance(request.input, PdfFileSetPayload):
            raise ValueError("combine_pdf requires PdfFileSetPayload input.")
        return

    if request.action in PDF_SINGLE_FILE_ACTIONS:
        if not isinstance(request.input, PdfFilePayload):
            raise ValueError(f"{request.action.value} requires PdfFilePayload input.")
        return

    if request.action in {FeatureType.structured_extract, FeatureType.compliance}:
        if not isinstance(request.input, (DocumentPayload, DocumentSetPayload)):
            raise ValueError(f"{request.action.value} requires DocumentPayload or DocumentSetPayload input.")
        return

    if request.action == FeatureType.text_to_speech:
        if not isinstance(request.input, DocumentPayload):
            raise ValueError("text_to_speech requires DocumentPayload input.")
        return

    if not isinstance(request.input, DocumentPayload):
        raise ValueError(f"{request.action.value} requires DocumentPayload input.")


def validate_no_client_detected_language(request: AnalyzerRequest) -> None:
    """
    detected_language is a server-supplied carrier only.
    """
    for document in _iter_documents(request.input):
        if document.metadata.detected_language is not None:
            raise ValueError("detected_language must not be provided by client; server supplies detection.")

    if isinstance(request.input, MediaPayload) and request.input.detected_language is not None:
        raise ValueError("detected_language must not be provided by client; server supplies detection.")


def validate_output_policy(request: AnalyzerRequest) -> None:
    if not isinstance(request.policy, OutputPolicy):
        raise ValueError("AnalyzerRequest.policy must be OutputPolicy.")

    if request.action in PDF_TRANSFORMED_ACTIONS and request.policy.structure_preservation is not True:
        raise ValueError("PDF tools and e-signature require structure_preservation=True.")

    if request.action in SECURE_TRANSFORMED_ACTIONS and request.policy.structure_preservation is not True:
        raise ValueError(f"{request.action.value} requires structure_preservation=True.")

    if request.action in GENERATED_ACTIONS and request.policy.structure_preservation is not False:
        raise ValueError(f"{request.action.value} requires structure_preservation=False.")


def validate_inline_text_input_security(request: AnalyzerRequest) -> None:
    """Validate and canonicalize browser/API inline text before dispatch.

    Inline text is represented by a TXT ``DocumentPayload`` without a persisted
    filename. Persisted file inputs retain their existing upload/extraction path
    and are intentionally not reclassified here. Route-level construction remains
    the primary trust boundary; this validator protects direct WorkflowRouter or
    service-layer callers as a second authoritative gate.
    """
    document = request.input
    if not isinstance(document, DocumentPayload):
        return
    if document.metadata.input_format != DocumentInputFormat.txt:
        return
    if document.filename:
        return

    if request.action == FeatureType.text_to_speech:
        validator = validate_text_to_speech_inline_text
    elif request.action in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
        validator = validate_inline_text
    else:
        return

    normalized = validator(document.text or "")
    actual_word_count = len(normalized.split())

    if request.action in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
        declared_word_count = document.metadata.extracted_word_count
        if declared_word_count != actual_word_count:
            raise ValueError(
                "Inline text extracted_word_count does not match the submitted text."
            )

    # Canonical text is used consistently by validation, prompt construction,
    # response metadata, and any downstream writer. Unsafe characters are never
    # removed silently; only line endings, NFC, and surrounding whitespace are
    # normalized by the security module.
    document.text = normalized
    document.metadata.extracted_word_count = actual_word_count


def validate_answer_generation_prompt_inputs(request: AnalyzerRequest) -> None:
    """Validate generated questions before they can re-enter an LLM prompt."""
    if request.action != FeatureType.generate_answers:
        return
    if not isinstance(request.payload, AnswerGenerationRequest):
        raise ValueError("generate_answers requires AnswerGenerationRequest payload.")

    questions = list(request.payload.questions)
    if len(questions) > MAX_NUMBERED_QUESTIONS:
        raise ValueError(
            f"At most {MAX_NUMBERED_QUESTIONS} numbered questions are allowed."
        )

    validated_questions = [validate_question_item(question) for question in questions]
    validate_auxiliary_prompt_text(
        "\n".join(validated_questions),
        field_name="Generated questions",
    )
    request.payload.questions = validated_questions


def validate_word_count_contract_when_present(request: AnalyzerRequest) -> None:
    """
    The 1..1000 word-count contract applies only to text AI document actions.
    PDF tools and e-signature do not require extracted text or word count.
    """
    if request.action not in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
        return

    if not isinstance(request.input, DocumentPayload):
        raise ValueError(f"{request.action.value} requires DocumentPayload input.")

    wc = request.input.metadata.extracted_word_count
    if wc is None:
        raise ValueError(f"{request.action.value} requires extracted_word_count.")
    if wc < 1:
        raise ValueError("extracted_word_count must be >= 1 for text-based AI processing actions.")

    classify_word_count(wc)


def validate_text_to_speech_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.text_to_speech:
        return

    if not isinstance(request.input, DocumentPayload):
        raise ValueError("text_to_speech requires DocumentPayload input.")
    if not isinstance(request.payload, TextToSpeechRequest):
        raise ValueError("text_to_speech requires TextToSpeechRequest payload.")
    if request.input.metadata.input_format not in {
        DocumentInputFormat.pdf,
        DocumentInputFormat.docx,
        DocumentInputFormat.txt,
    }:
        raise ValueError("text_to_speech accepts only PDF, DOCX, and TXT input.")
    if not request.input.text or not request.input.text.strip():
        raise ValueError("text_to_speech requires extracted source text.")

    expected_suffix = f".{request.payload.output_format.value}"
    if not request.payload.output_filename.lower().endswith(expected_suffix):
        raise ValueError(
            f"text_to_speech output_filename must end with {expected_suffix}."
        )


def validate_vault_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.vault:
        return

    if not isinstance(request.payload, VaultRequest):
        raise ValueError("vault requires VaultRequest payload.")

    expected_input = _VAULT_INPUT_BY_OPERATION[request.payload.operation]
    if not isinstance(request.input, expected_input):
        raise ValueError(
            f"vault operation '{request.payload.operation.value}' requires "
            f"{expected_input.__name__} input."
        )

    if request.payload.operation != VaultOperation.delete and request.payload.confirm_delete:
        raise ValueError("confirm_delete is only valid for the Vault delete operation.")

    if isinstance(request.input, VaultFilePayload):
        if not request.input.storage_key and not request.input.upload_id:
            raise ValueError("Vault store input requires storage_key or upload_id.")


def validate_pdf_inputs_are_processable(request: AnalyzerRequest) -> None:
    """
    Service-layer guard for ReDOCX PDF operations.

    The schema accepts encrypted/password flags as metadata. The actual PDF tools
    cannot safely process password-protected PDFs unless the backend implements an
    unlock step, so this validator rejects them by default.
    """
    if request.action not in PDF_DOCUMENT_ACTIONS:
        return

    pdf_files = _iter_pdf_files(request.input)
    if not pdf_files:
        raise ValueError(f"{request.action.value} requires PDF input.")

    for pdf in pdf_files:
        if pdf.metadata.encrypted or pdf.metadata.password_protected:
            raise ValueError(
                "Password-protected or encrypted PDFs are not supported by this validation contract. "
                "Add a separate unlock/decrypt workflow before processing these files."
            )
        if pdf.mime_type.lower() not in {"application/pdf", "application/x-pdf"}:
            raise ValueError("PDF actions require application/pdf input.")
        if not pdf.filename.lower().endswith(".pdf"):
            raise ValueError("PDF input filename must end with .pdf.")


def validate_combine_pdf_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.combine_pdf:
        return

    if not isinstance(request.input, PdfFileSetPayload):
        raise ValueError("combine_pdf requires PdfFileSetPayload input.")
    if not isinstance(request.payload, CombinePdfRequest):
        raise ValueError("combine_pdf requires CombinePdfRequest payload.")

    identities = [_pdf_identity(pdf) for pdf in request.input.documents]
    if len(set(identities)) != len(identities):
        raise ValueError("combine_pdf input documents must not contain the same uploaded file more than once.")

    if not request.payload.output_filename.lower().endswith(".pdf"):
        raise ValueError("combine_pdf output_filename must end with .pdf.")


def validate_split_pdf_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.split_pdf:
        return

    if not isinstance(request.input, PdfFilePayload):
        raise ValueError("split_pdf requires PdfFilePayload input.")
    if not isinstance(request.payload, SplitPdfRequest):
        raise ValueError("split_pdf requires SplitPdfRequest payload.")

    page_count = request.input.metadata.page_count
    if page_count is None:
        return

    if request.payload.selected_pages and max(request.payload.selected_pages) > page_count:
        raise ValueError("selected_pages cannot exceed source PDF page_count.")

    for page_range in request.payload.page_ranges:
        if page_range.end_page > page_count:
            raise ValueError("page_ranges cannot exceed source PDF page_count.")


def validate_edit_pdf_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.edit_pdf:
        return

    if not isinstance(request.input, PdfFilePayload):
        raise ValueError("edit_pdf requires PdfFilePayload input.")
    if not isinstance(request.payload, EditPdfRequest):
        raise ValueError("edit_pdf requires EditPdfRequest payload.")

    page_count = request.input.metadata.page_count
    if page_count is not None:
        for operation in request.payload.operations:
            if operation.page_number > page_count:
                raise ValueError("edit operation page_number cannot exceed source PDF page_count.")

    operation_ids = [op.operation_id for op in request.payload.operations if op.operation_id]
    if len(set(operation_ids)) != len(operation_ids):
        raise ValueError("edit_pdf operation_id values must be unique when provided.")


def validate_compress_pdf_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.compress_pdf:
        return

    if not isinstance(request.input, PdfFilePayload):
        raise ValueError("compress_pdf requires PdfFilePayload input.")
    if not isinstance(request.payload, CompressPdfRequest):
        raise ValueError("compress_pdf requires CompressPdfRequest payload.")

    if not request.payload.output_filename.lower().endswith(".pdf"):
        raise ValueError("compress_pdf output_filename must end with .pdf.")


def validate_lock_pdf_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.lock_pdf:
        return

    if not isinstance(request.input, PdfFilePayload):
        raise ValueError("lock_pdf requires PdfFilePayload input.")
    if not isinstance(request.payload, LockPdfRequest):
        raise ValueError("lock_pdf requires LockPdfRequest payload.")
    if request.input.metadata.encrypted or request.input.metadata.password_protected:
        raise ValueError("lock_pdf requires an unlocked source PDF.")
    if request.payload.encryption != PdfEncryptionAlgorithm.aes_256:
        raise ValueError("lock_pdf requires AES-256 encryption.")
    if not request.payload.output_filename.lower().endswith(".pdf"):
        raise ValueError("lock_pdf output_filename must end with .pdf.")

    password = request.payload.password.get_secret_value()
    if not 8 <= len(password) <= MAX_PDF_PASSWORD_LENGTH:
        raise ValueError(
            f"lock_pdf password must contain 8 to {MAX_PDF_PASSWORD_LENGTH} characters."
        )


def validate_esignature_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.e_signature:
        return

    if not isinstance(request.input, PdfFilePayload):
        raise ValueError("e_signature requires PdfFilePayload input.")
    if not isinstance(request.payload, ESignatureRequest):
        raise ValueError("e_signature requires ESignatureRequest payload.")

    payload = request.payload
    page_count = request.input.metadata.page_count

    if page_count is not None:
        effective_page_count = page_count
        if payload.add_signature_page:
            signer_count = len(payload.recipients) + (
                1 if payload.self_signer is not None else 0
            )
            signature_page_count = (
                max(1, signer_count) + ESIGN_SIGNERS_PER_SIGNATURE_PAGE - 1
            ) // ESIGN_SIGNERS_PER_SIGNATURE_PAGE
            effective_page_count += signature_page_count
        for field in payload.fields:
            if field.page_number > effective_page_count:
                raise ValueError(
                    "e-signature field page_number cannot exceed the effective PDF page_count "
                    "after optional signature-page insertion."
                )

    field_ids = [field.field_id for field in payload.fields if field.field_id]
    if len(set(field_ids)) != len(field_ids):
        raise ValueError("e-signature field_id values must be unique when provided.")

    if payload.action in {ESignatureAction.send, ESignatureAction.sign, ESignatureAction.complete_signing}:
        if not payload.fields and payload.workflow != ESignatureWorkflow.self_sign:
            raise ValueError("Sending or completing an e-signature envelope requires at least one field.")

    if payload.workflow in {ESignatureWorkflow.self_sign, ESignatureWorkflow.self_sign_then_send}:
        if payload.self_signer is None:
            raise ValueError(f"{payload.workflow.value} requires self_signer.")

        if payload.action in {ESignatureAction.sign, ESignatureAction.complete_signing}:
            if payload.self_signer.signature is None:
                raise ValueError(f"{payload.action.value} requires self_signer.signature.")

    if (
        payload.workflow == ESignatureWorkflow.self_sign_then_send
        and payload.action == ESignatureAction.send
        and (payload.self_signer is None or payload.self_signer.signature is None)
    ):
        raise ValueError(
            "self_sign_then_send with action=send requires self_signer.signature "
            "before recipient invitations are delivered."
        )

    if payload.action == ESignatureAction.send:
        required_signers = _required_signer_emails(payload)
        sign_field_assignees = _field_assignees_by_type(payload, ESignatureFieldType.signature)
        initials_assignees = _field_assignees_by_type(payload, ESignatureFieldType.initials)
        signable_assignees = sign_field_assignees | initials_assignees

        # A signer can also be represented by a self_signer.signature for the self-sign step.
        if payload.self_signer and payload.self_signer.signature:
            signable_assignees.add(payload.self_signer.email.lower())

        missing = required_signers - signable_assignees
        if missing:
            raise ValueError(
                "Each required signer must have at least one signature/initials field "
                f"or a self_signer.signature. Missing: {', '.join(sorted(missing))}."
            )


def validate_generate_questions_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.generate_questions:
        return

    if not isinstance(request.input, DocumentPayload):
        raise ValueError("generate_questions requires DocumentPayload input.")

    wc = request.input.metadata.extracted_word_count
    if wc is None:
        raise ValueError("generate_questions requires extracted_word_count.")

    classify_word_count(wc)


def validate_generate_answers_request(request: AnalyzerRequest) -> None:
    if request.action != FeatureType.generate_answers:
        return

    if not isinstance(request.input, DocumentPayload):
        raise ValueError("generate_answers requires DocumentPayload input.")
    if not isinstance(request.payload, AnswerGenerationRequest):
        raise ValueError("generate_answers requires AnswerGenerationRequest payload.")

    wc = request.input.metadata.extracted_word_count
    if wc is None:
        raise ValueError("generate_answers requires extracted_word_count.")

    rule = classify_word_count(wc)
    question_count = len(request.payload.questions)
    if not (rule.min_questions <= question_count <= rule.max_questions):
        raise ValueError(
            "Supplied questions count is inconsistent with document-size scaling for generate_answers: "
            f"expected {rule.min_questions}–{rule.max_questions}, got {question_count}."
        )


def validate_question_scale(word_count: int) -> QuestionScale:
    return classify_word_count(word_count).classification


def get_question_range(word_count: int) -> tuple[int, int]:
    rule = classify_word_count(word_count)
    return rule.min_questions, rule.max_questions


def validate_analyzer_request(request: Union[AnalyzerRequest, Mapping[str, Any]]) -> AnalyzerRequest:
    """
    Full deterministic validation pipeline for incoming API requests.

    schema.py remains the primary Pydantic contract. This function adds
    service-layer guards, clearer error messages, and cross-field checks that are
    useful before dispatching work to PDF engines, e-signature services, queues,
    or AI processors.
    """
    req = request if isinstance(request, AnalyzerRequest) else AnalyzerRequest.model_validate(request)

    validate_action_payload_consistency(req)
    validate_input_payload_consistency(req)
    validate_no_client_detected_language(req)
    validate_inline_text_input_security(req)
    validate_answer_generation_prompt_inputs(req)
    validate_output_policy(req)
    validate_word_count_contract_when_present(req)
    validate_text_to_speech_request(req)
    validate_vault_request(req)

    validate_pdf_inputs_are_processable(req)
    validate_combine_pdf_request(req)
    validate_split_pdf_request(req)
    validate_edit_pdf_request(req)
    validate_compress_pdf_request(req)
    validate_lock_pdf_request(req)
    validate_esignature_request(req)

    validate_generate_questions_request(req)
    validate_generate_answers_request(req)

    return req


# =========================
# Result construction helpers
# =========================


def _meta(*, algorithm_version: Optional[str] = None) -> DeterminismMetadata:
    return DeterminismMetadata(algorithm_version=algorithm_version)


def build_inline_txt_result(
    *,
    content: str,
    algorithm_version: Optional[str] = None,
) -> InlineTextResult:
    return InlineTextResult(content=content, meta=_meta(algorithm_version=algorithm_version))


def build_transcription_result(
    *,
    content: str,
    pdf_artifact: DocumentFileResult,
    algorithm_version: Optional[str] = None,
) -> TranscriptionResult:
    _require_pdf_file_result(pdf_artifact, field_name="pdf_artifact")
    return TranscriptionResult(
        content=content,
        pdf_artifact=pdf_artifact,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_document_file_result(
    *,
    filename: str,
    output_format: DocumentFileOutputFormat,
    file_size_mb: float,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> DocumentFileResult:
    return DocumentFileResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_archive_file_result(
    *,
    filename: str,
    file_size_mb: float,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> ArchiveFileResult:
    return ArchiveFileResult(
        filename=filename,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_text_to_speech_result(
    *,
    filename: str,
    output_format: SpeechAudioFormat,
    file_size_mb: float,
    voice_id: str,
    source_character_count: int,
    duration_seconds: Optional[float] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> TextToSpeechResult:
    return TextToSpeechResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        voice_id=voice_id,
        source_character_count=source_character_count,
        duration_seconds=duration_seconds,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_vault_item_metadata(
    *,
    item_id: str,
    filename: str,
    content_type: str,
    file_size_bytes: int,
    created_at_iso: str,
    checksum_sha256: Optional[str] = None,
    client_encrypted: bool = False,
    updated_at_iso: Optional[str] = None,
) -> VaultItemMetadata:
    return VaultItemMetadata(
        item_id=item_id,
        filename=filename,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        checksum_sha256=checksum_sha256,
        client_encrypted=client_encrypted,
        created_at_iso=created_at_iso,
        updated_at_iso=updated_at_iso,
    )


def build_vault_item_result(
    *,
    operation: VaultOperation,
    item: VaultItemMetadata,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> VaultItemResult:
    if operation not in {VaultOperation.store, VaultOperation.retrieve}:
        raise ValueError("VaultItemResult operation must be store or retrieve.")
    return VaultItemResult(
        operation=operation,
        item=item,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_vault_list_result(
    *,
    items: Optional[list[VaultItemMetadata]] = None,
    next_cursor: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> VaultListResult:
    return VaultListResult(
        items=items or [],
        next_cursor=next_cursor,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_vault_delete_result(
    *,
    item_id: str,
    algorithm_version: Optional[str] = None,
) -> VaultDeleteResult:
    return VaultDeleteResult(
        item_id=item_id,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_pdf_document_file_result(
    *,
    filename: str,
    file_size_mb: float,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> DocumentFileResult:
    result = build_document_file_result(
        filename=filename,
        output_format=DocumentFileOutputFormat.pdf,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        algorithm_version=algorithm_version,
    )
    _require_pdf_file_result(result)
    return result


def build_pdf_preview_result(
    *,
    filename: str,
    file_size_mb: float,
    page_count: Optional[int] = None,
    preview_stage: Optional[str] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> PdfPreviewResult:
    return PdfPreviewResult(
        filename=filename,
        file_size_mb=file_size_mb,
        page_count=page_count,
        preview_stage=preview_stage,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_pdf_job_result(
    *,
    job_id: str,
    status: PdfJobStatus,
    message: Optional[str] = None,
    result: Optional[DocumentFileResult] = None,
    algorithm_version: Optional[str] = None,
) -> PdfJobResult:
    if status == PdfJobStatus.completed and result is None:
        raise ValueError("completed PdfJobResult requires result.")
    if status == PdfJobStatus.failed and not message:
        raise ValueError("failed PdfJobResult requires message.")
    if result is not None:
        _require_pdf_file_result(result, field_name="result")
    return PdfJobResult(
        job_id=job_id,
        status=status,
        message=message,
        result=result,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_combine_pdf_result(
    *,
    filename: str,
    file_size_mb: float,
    source_filenames: list[str],
    combined_page_count: Optional[int] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> CombinePdfResult:
    return CombinePdfResult(
        filename=filename,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
        source_file_count=len(source_filenames),
        source_filenames=source_filenames,
        combined_page_count=combined_page_count,
    )


def build_split_pdf_result(
    *,
    mode,
    output_files: list[DocumentFileResult],
    archive_file: Optional[ArchiveFileResult] = None,
    algorithm_version: Optional[str] = None,
) -> SplitPdfResult:
    for index, item in enumerate(output_files):
        _require_pdf_file_result(item, field_name=f"output_files[{index}]")
    return SplitPdfResult(
        mode=mode,
        output_files=output_files,
        archive_file=archive_file,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_edit_pdf_result(
    *,
    filename: str,
    file_size_mb: float,
    operations_requested: int,
    operations_applied: int,
    preview: Optional[PdfPreviewResult] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> EditPdfResult:
    return EditPdfResult(
        filename=filename,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
        operations_requested=operations_requested,
        operations_applied=operations_applied,
        preview=preview,
    )


def build_compress_pdf_result(
    *,
    filename: str,
    file_size_mb: float,
    compression_level,
    original_file_size_mb: float,
    compressed_file_size_mb: float,
    estimated_output_file_size_mb: Optional[float] = None,
    compression_ratio: Optional[float] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> CompressPdfResult:
    return CompressPdfResult(
        filename=filename,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
        compression_level=compression_level,
        original_file_size_mb=original_file_size_mb,
        compressed_file_size_mb=compressed_file_size_mb,
        estimated_output_file_size_mb=estimated_output_file_size_mb,
        compression_ratio=compression_ratio,
    )


def build_lock_pdf_result(
    *,
    filename: str,
    file_size_mb: float,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> LockPdfResult:
    return LockPdfResult(
        filename=filename,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_esignature_result(
    *,
    envelope_id: str,
    workflow,
    status,
    recipients: Optional[list[ESignatureRecipientResult]] = None,
    latest_preview: Optional[ESignatureStepPreview] = None,
    previews: Optional[list[ESignatureStepPreview]] = None,
    signed_pdf: Optional[DocumentFileResult] = None,
    audit_certificate: Optional[DocumentFileResult] = None,
    audit_events: Optional[list[ESignatureAuditEvent]] = None,
    algorithm_version: Optional[str] = None,
) -> ESignatureResult:
    if signed_pdf is not None:
        _require_pdf_file_result(signed_pdf, field_name="signed_pdf")
    if audit_certificate is not None:
        _require_pdf_file_result(audit_certificate, field_name="audit_certificate")

    return ESignatureResult(
        envelope_id=envelope_id,
        workflow=workflow,
        status=status,
        recipients=recipients or [],
        latest_preview=latest_preview,
        previews=previews or [],
        signed_pdf=signed_pdf,
        audit_certificate=audit_certificate,
        audit_events=audit_events or [],
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_structured_extraction_file_result(
    *,
    filename: str,
    output_format: StructuredDataOutputFormat,
    file_size_mb: float,
    result_shape: StructuredExtractionResultShape,
    selected_fields: Optional[list[str]] = None,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> StructuredExtractionFileResult:
    return StructuredExtractionFileResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        result_shape=result_shape,
        selected_fields=selected_fields or [],
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_compliance_file_result(
    *,
    filename: str,
    output_format: ComplianceOutputFormat,
    file_size_mb: float,
    report_variant: ComplianceReportVariant,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> ComplianceFileResult:
    return ComplianceFileResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        report_variant=report_variant,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
    )


def build_question_generation_inline_result(
    *,
    content: str,
    extracted_word_count: int,
    algorithm_version: Optional[str] = None,
) -> QuestionGenerationInlineResult:
    classification = classify_word_count(extracted_word_count).classification
    return QuestionGenerationInlineResult(
        content=content,
        meta=_meta(algorithm_version=algorithm_version),
        scale=QuestionScaleMetadata(
            classification=classification,
            extracted_word_count=extracted_word_count,
        ),
    )


def build_question_generation_file_result(
    *,
    filename: str,
    output_format: DocumentFileOutputFormat,
    file_size_mb: float,
    extracted_word_count: int,
    generated_questions_text: str,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> QuestionGenerationFileResult:
    classification = classify_word_count(extracted_word_count).classification
    return QuestionGenerationFileResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
        generated_questions_text=generated_questions_text,
        scale=QuestionScaleMetadata(
            classification=classification,
            extracted_word_count=extracted_word_count,
        ),
    )


def build_answer_generation_inline_result(
    *,
    content: str,
    expected_question_count: int,
    algorithm_version: Optional[str] = None,
) -> AnswerGenerationInlineResult:
    return AnswerGenerationInlineResult(
        content=content,
        meta=_meta(algorithm_version=algorithm_version),
        expected_question_count=expected_question_count,
    )


def build_answer_generation_file_result(
    *,
    filename: str,
    output_format: DocumentFileOutputFormat,
    file_size_mb: float,
    expected_question_count: int,
    storage_key: Optional[str] = None,
    download_url: Optional[str] = None,
    algorithm_version: Optional[str] = None,
) -> AnswerGenerationFileResult:
    return AnswerGenerationFileResult(
        filename=filename,
        output_format=output_format,
        file_size_mb=file_size_mb,
        storage_key=storage_key,
        download_url=download_url,
        meta=_meta(algorithm_version=algorithm_version),
        expected_question_count=expected_question_count,
    )


# =========================
# Response validation
# =========================


def _expected_response_input_format(request: AnalyzerRequest):
    """
    AnalyzerResponse echoes input_format as:
      - DocumentInputFormat for regular single-document requests
      - "document_set" for DocumentSetPayload requests
      - "audio" or "video" for transcription requests
      - "pdf_file" for single PDF tool/e-signature requests
      - "pdf_file_set" for Combine PDF requests
      - Vault-specific discriminator strings for Vault requests
    """
    if isinstance(request.input, MediaPayload):
        return "audio" if request.input.media_type == MediaType.audio else "video"
    if isinstance(request.input, DocumentSetPayload):
        return "document_set"
    if isinstance(request.input, PdfFileSetPayload):
        return "pdf_file_set"
    if isinstance(request.input, PdfFilePayload):
        return "pdf_file"
    if isinstance(request.input, VaultFilePayload):
        return "vault_file"
    if isinstance(request.input, VaultItemReferencePayload):
        return "vault_item_reference"
    if isinstance(request.input, VaultQueryPayload):
        return "vault_query"
    return request.input.metadata.input_format


def validate_result_type_for_action(response: AnalyzerResponse) -> None:
    expected_types = _ACTION_RESULT_TYPES.get(response.action)
    if expected_types is None:
        raise ValueError(f"Unsupported response action: {response.action}")
    if not isinstance(response.result, expected_types):
        expected = " or ".join(model.__name__ for model in expected_types)
        raise ValueError(
            f"result type mismatch for action '{response.action.value}': "
            f"expected {expected}, got {type(response.result).__name__}"
        )


def validate_pdf_job_result(result: PdfJobResult) -> None:
    if result.status == PdfJobStatus.completed and result.result is None:
        raise ValueError("completed PdfJobResult requires result.")
    if result.status == PdfJobStatus.failed and not result.message:
        raise ValueError("failed PdfJobResult requires message.")
    if result.result is not None:
        _require_pdf_file_result(result.result, field_name="PdfJobResult.result")


def validate_text_to_speech_response(
    response: AnalyzerResponse,
    request: Optional[AnalyzerRequest],
) -> None:
    if response.action != FeatureType.text_to_speech:
        return
    if not isinstance(response.result, TextToSpeechResult):
        raise ValueError("text_to_speech response must contain TextToSpeechResult.")

    if request is None:
        return
    if not isinstance(request.payload, TextToSpeechRequest):
        raise ValueError("text_to_speech request must use TextToSpeechRequest.")
    if not isinstance(request.input, DocumentPayload) or not request.input.text:
        raise ValueError("text_to_speech request must contain extracted document text.")
    if response.result.output_format != request.payload.output_format:
        raise ValueError("TextToSpeechResult.output_format must match the request.")
    if response.result.voice_id != request.payload.voice_id:
        raise ValueError("TextToSpeechResult.voice_id must match the request.")
    if response.result.filename != request.payload.output_filename:
        raise ValueError("TextToSpeechResult.filename must match request.output_filename.")
    if response.result.source_character_count != len(request.input.text):
        raise ValueError(
            "TextToSpeechResult.source_character_count must match the exact extracted source text."
        )


def validate_vault_response(
    response: AnalyzerResponse,
    request: Optional[AnalyzerRequest],
) -> None:
    if response.action != FeatureType.vault:
        return
    if not isinstance(response.result, (VaultItemResult, VaultListResult, VaultDeleteResult)):
        raise ValueError("vault response must contain a Vault result model.")

    if request is None:
        return
    if not isinstance(request.payload, VaultRequest):
        raise ValueError("vault request must use VaultRequest.")

    operation = request.payload.operation
    expected_result = _VAULT_RESULT_BY_OPERATION[operation]
    if not isinstance(response.result, expected_result):
        raise ValueError(
            f"vault operation '{operation.value}' must return {expected_result.__name__}."
        )
    if response.result.operation != operation:
        raise ValueError("Vault result operation must match the request operation.")

    if operation == VaultOperation.store:
        if not isinstance(request.input, VaultFilePayload):
            raise ValueError("Vault store request must use VaultFilePayload.")
        assert isinstance(response.result, VaultItemResult)
        item = response.result.item
        if item.filename != request.input.filename:
            raise ValueError("Stored Vault filename must match the uploaded filename.")
        if item.content_type != request.input.content_type:
            raise ValueError("Stored Vault content_type must match the uploaded content_type.")
        if item.file_size_bytes != request.input.file_size_bytes:
            raise ValueError("Stored Vault file_size_bytes must match the uploaded file size.")
        if item.client_encrypted != request.input.client_encrypted:
            raise ValueError("Stored Vault client_encrypted flag must match the upload.")
        if request.input.checksum_sha256 and item.checksum_sha256 != request.input.checksum_sha256:
            raise ValueError("Stored Vault checksum must match the uploaded checksum.")

    elif operation == VaultOperation.retrieve:
        if not isinstance(request.input, VaultItemReferencePayload):
            raise ValueError("Vault retrieve request must use VaultItemReferencePayload.")
        assert isinstance(response.result, VaultItemResult)
        if response.result.item.item_id != request.input.item_id:
            raise ValueError("Retrieved Vault item_id must match the request.")

    elif operation == VaultOperation.list:
        if not isinstance(request.input, VaultQueryPayload):
            raise ValueError("Vault list request must use VaultQueryPayload.")
        assert isinstance(response.result, VaultListResult)
        if len(response.result.items) > request.input.limit:
            raise ValueError("VaultListResult cannot exceed the requested limit.")
        item_ids = [item.item_id for item in response.result.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("VaultListResult must not contain duplicate item_id values.")

    elif operation == VaultOperation.delete:
        if not isinstance(request.input, VaultItemReferencePayload):
            raise ValueError("Vault delete request must use VaultItemReferencePayload.")
        assert isinstance(response.result, VaultDeleteResult)
        if response.result.item_id != request.input.item_id:
            raise ValueError("Deleted Vault item_id must match the request.")


def validate_combine_pdf_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.combine_pdf:
        return
    if not isinstance(response.result, CombinePdfResult):
        raise ValueError("combine_pdf response must contain CombinePdfResult.")

    _require_pdf_file_result(response.result)
    if response.input_format != "pdf_file_set":
        raise ValueError("combine_pdf response input_format must be 'pdf_file_set'.")

    if request is not None:
        if not isinstance(request.input, PdfFileSetPayload):
            raise ValueError("combine_pdf request must use PdfFileSetPayload.")
        source_filenames = [pdf.filename for pdf in request.input.documents]
        if response.result.source_file_count != len(source_filenames):
            raise ValueError("CombinePdfResult.source_file_count must match request input document count.")
        if response.result.source_filenames and response.result.source_filenames != source_filenames:
            raise ValueError("CombinePdfResult.source_filenames must preserve request input order.")


def validate_split_pdf_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.split_pdf:
        return
    if not isinstance(response.result, SplitPdfResult):
        raise ValueError("split_pdf response must contain SplitPdfResult.")

    if request is not None:
        if not isinstance(request.payload, SplitPdfRequest):
            raise ValueError("split_pdf request must use SplitPdfRequest.")
        if response.result.mode != request.payload.mode:
            raise ValueError("SplitPdfResult.mode must match request payload mode.")

        if isinstance(request.input, PdfFilePayload):
            page_count = request.input.metadata.page_count
            if page_count is not None and request.payload.mode.value == "every_page":
                if response.result.output_files and len(response.result.output_files) != page_count:
                    raise ValueError("every_page split should return one output PDF per source page.")


def validate_edit_pdf_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.edit_pdf:
        return
    if not isinstance(response.result, EditPdfResult):
        raise ValueError("edit_pdf response must contain EditPdfResult.")

    _require_pdf_file_result(response.result)

    if request is not None:
        if not isinstance(request.payload, EditPdfRequest):
            raise ValueError("edit_pdf request must use EditPdfRequest.")
        if response.result.operations_requested != len(request.payload.operations):
            raise ValueError("EditPdfResult.operations_requested must match request operation count.")
        if request.payload.generate_preview and response.result.preview is None:
            raise ValueError("EditPdfResult.preview is required when request.generate_preview=True.")


def validate_compress_pdf_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.compress_pdf:
        return

    if not isinstance(response.result, (CompressPdfResult, PdfJobResult)):
        raise ValueError("compress_pdf response must contain CompressPdfResult or PdfJobResult.")

    if isinstance(response.result, PdfJobResult):
        validate_pdf_job_result(response.result)
    else:
        _require_pdf_file_result(response.result)
        if abs(response.result.compressed_file_size_mb - response.result.file_size_mb) > 0.01:
            raise ValueError(
                "CompressPdfResult.compressed_file_size_mb must match the actual output file_size_mb."
            )

    if request is not None:
        if not isinstance(request.payload, CompressPdfRequest):
            raise ValueError("compress_pdf request must use CompressPdfRequest.")

        if request.payload.async_processing is False and isinstance(response.result, PdfJobResult):
            raise ValueError("Synchronous compress_pdf requests must not return PdfJobResult.")

        if isinstance(response.result, CompressPdfResult):
            if response.result.compression_level != request.payload.compression_level:
                raise ValueError("CompressPdfResult.compression_level must match request.")
            if isinstance(request.input, PdfFilePayload):
                original = request.input.metadata.file_size_mb
                if abs(response.result.original_file_size_mb - original) > 0.01:
                    raise ValueError("CompressPdfResult.original_file_size_mb must match source PDF size.")


def validate_lock_pdf_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.lock_pdf:
        return
    if not isinstance(response.result, LockPdfResult):
        raise ValueError("lock_pdf response must contain LockPdfResult.")

    _require_pdf_file_result(response.result)
    if response.result.encryption != PdfEncryptionAlgorithm.aes_256:
        raise ValueError("LockPdfResult must declare AES-256 encryption.")
    if response.result.password_protected is not True:
        raise ValueError("LockPdfResult must declare password_protected=True.")

    if request is not None:
        if not isinstance(request.payload, LockPdfRequest):
            raise ValueError("lock_pdf request must use LockPdfRequest.")
        if response.result.encryption != request.payload.encryption:
            raise ValueError("LockPdfResult.encryption must match the request.")
        if response.result.filename != request.payload.output_filename:
            raise ValueError("LockPdfResult.filename must match request.output_filename.")


def validate_esignature_response(response: AnalyzerResponse, request: Optional[AnalyzerRequest]) -> None:
    if response.action != FeatureType.e_signature:
        return
    if not isinstance(response.result, ESignatureResult):
        raise ValueError("e_signature response must contain ESignatureResult.")

    result = response.result

    if result.latest_preview and result.previews:
        latest = result.previews[-1]
        if result.latest_preview.signer_email.lower() != latest.signer_email.lower():
            raise ValueError("latest_preview must match the last item in previews by signer_email.")
        if result.latest_preview.created_at_iso != latest.created_at_iso:
            raise ValueError("latest_preview must match the last item in previews by created_at_iso.")

    if result.status == ESignatureEnvelopeStatus.completed:
        if result.signed_pdf is None:
            raise ValueError("completed e-signature response requires signed_pdf.")
        if result.audit_certificate is None:
            raise ValueError("completed e-signature response requires audit_certificate.")
        _require_pdf_file_result(result.signed_pdf, field_name="signed_pdf")
        _require_pdf_file_result(result.audit_certificate, field_name="audit_certificate")

        unsigned = [
            recipient.email
            for recipient in result.recipients
            if recipient.status != ESignatureRecipientStatus.signed
        ]
        if unsigned:
            raise ValueError(f"completed e-signature response has unsigned recipients: {', '.join(unsigned)}")

    signed_recipient_emails = {
        recipient.email.lower()
        for recipient in result.recipients
        if recipient.status == ESignatureRecipientStatus.signed
    }
    preview_emails = {preview.signer_email.lower() for preview in result.previews}
    missing_previews = signed_recipient_emails - preview_emails
    if missing_previews:
        raise ValueError(
            "A preview must be generated after each recipient signs. "
            f"Missing previews for: {', '.join(sorted(missing_previews))}."
        )

    if request is not None:
        if not isinstance(request.payload, ESignatureRequest):
            raise ValueError("e_signature request must use ESignatureRequest.")
        if result.workflow != request.payload.workflow:
            raise ValueError("ESignatureResult.workflow must match request workflow.")


def validate_regular_response_against_request(response: AnalyzerResponse, request: AnalyzerRequest) -> None:
    """
    Existing non-PDF response consistency checks retained from the previous validator.
    """
    if response.action == FeatureType.translate:
        if not isinstance(request.payload, TranslationRequest):
            raise ValueError("translate request must use TranslationRequest.")
        if response.output_language is not None and response.output_language != request.payload.target_language:
            raise ValueError("For translate, output_language must match payload.target_language.")

    if response.action == FeatureType.structured_extract:
        if not isinstance(request.payload, StructuredExtractionRequest):
            raise ValueError("structured_extract request must use StructuredExtractionRequest.")
        if not isinstance(response.result, StructuredExtractionFileResult):
            raise ValueError("structured_extract response must contain StructuredExtractionFileResult.")
        if response.result.output_format != request.payload.output_format:
            raise ValueError("structured_extract response output_format must match request payload.output_format.")
        if response.result.result_shape != request.payload.result_shape:
            raise ValueError("structured_extract response result_shape must match request payload.result_shape.")
        if response.result.selected_fields != request.payload.selected_fields:
            raise ValueError("structured_extract response selected_fields must match request payload.selected_fields.")

    if response.action == FeatureType.compliance:
        if not isinstance(request.payload, ComplianceRequest):
            raise ValueError("compliance request must use ComplianceRequest.")
        if not isinstance(response.result, ComplianceFileResult):
            raise ValueError("compliance response must contain ComplianceFileResult.")
        if response.result.report_variant != request.payload.report_variant:
            raise ValueError("compliance response report_variant must match request payload.report_variant.")
        expected_output_format = (
            ComplianceOutputFormat.json
            if request.payload.report_variant == ComplianceReportVariant.machine_readable_report
            else None
        )
        if (
            expected_output_format is not None
            and response.result.output_format != expected_output_format
        ):
            raise ValueError("compliance response output_format is inconsistent with report_variant.")
        if (
            request.payload.report_variant == ComplianceReportVariant.human_readable_report
            and response.result.output_format != ComplianceOutputFormat.pdf
        ):
            raise ValueError("human_readable_report must use pdf output.")
        if (
            request.payload.report_variant == ComplianceReportVariant.annotated_source_output
            and response.result.output_format
            not in {ComplianceOutputFormat.pdf, ComplianceOutputFormat.zip}
        ):
            raise ValueError(
                "annotated_source_output must use pdf for one source or zip for a document set."
            )

    if response.action == FeatureType.generate_questions:
        if not isinstance(response.result, (QuestionGenerationInlineResult, QuestionGenerationFileResult)):
            raise ValueError("generate_questions response must contain a question-generation result.")
        if isinstance(request.input, DocumentPayload):
            wc = request.input.metadata.extracted_word_count
            if wc is None:
                raise ValueError("generate_questions requires extracted_word_count in request input.")
            if response.result.scale.extracted_word_count != wc:
                raise ValueError(
                    "Question-generation response scale.extracted_word_count must match request extracted_word_count."
                )
            expected_classification = classify_word_count(wc).classification
            if response.result.scale.classification != expected_classification:
                raise ValueError(
                    "Question-generation response scale.classification is inconsistent with request extracted_word_count."
                )

    if response.action == FeatureType.generate_answers:
        if not isinstance(request.payload, AnswerGenerationRequest):
            raise ValueError("generate_answers request must use AnswerGenerationRequest.")
        if not isinstance(response.result, (AnswerGenerationInlineResult, AnswerGenerationFileResult)):
            raise ValueError("generate_answers response must contain an answer-generation result.")
        expected_count = len(request.payload.questions)
        if response.result.expected_question_count != expected_count:
            raise ValueError("expected_question_count must match the supplied questions count.")


def validate_analyzer_response(
    response: Union[AnalyzerResponse, Mapping[str, Any]],
    *,
    request: Optional[AnalyzerRequest] = None,
    require_file_location: bool = False,
) -> AnalyzerResponse:
    """
    Validates responses after they have been constructed by route handlers/workers.

    When `request` is supplied, this also enforces request/response alignment
    such as source file count, split mode, edit operation count, compression
    level, e-signature workflow, and expected response input_format.

    Set `require_file_location=True` in production handlers when every returned
    file must contain either storage_key or download_url before being sent to
    the frontend.
    """
    resp = response if isinstance(response, AnalyzerResponse) else AnalyzerResponse.model_validate(response)

    validate_result_type_for_action(resp)

    if request is not None:
        if resp.action != request.action:
            raise ValueError("Response action must match request action.")
        if resp.policy != request.policy:
            raise ValueError("Response policy must match request policy.")
        if resp.system_language != request.system_language:
            raise ValueError("Response system_language must match request system_language.")

        expected_in_fmt = _expected_response_input_format(request)
        if resp.input_format != expected_in_fmt:
            raise ValueError("Response input_format must match request input format.")

        validate_regular_response_against_request(resp, request)

    validate_combine_pdf_response(resp, request)
    validate_split_pdf_response(resp, request)
    validate_edit_pdf_response(resp, request)
    validate_compress_pdf_response(resp, request)
    validate_lock_pdf_response(resp, request)
    validate_esignature_response(resp, request)
    validate_text_to_speech_response(resp, request)
    validate_vault_response(resp, request)

    if require_file_location:
        _validate_response_file_locations(resp)

    return resp


def _iter_file_results_from_response(response: AnalyzerResponse) -> Iterable[tuple[str, BaseFileResult]]:
    result = response.result

    if isinstance(result, DocumentFileResult):
        yield "result", result

    if isinstance(result, TextToSpeechResult):
        yield "result", result

    if isinstance(result, TranscriptionResult):
        yield "pdf_artifact", result.pdf_artifact

    if isinstance(result, PdfJobResult) and result.result is not None:
        yield "job.result", result.result

    if isinstance(result, SplitPdfResult):
        for index, item in enumerate(result.output_files):
            yield f"output_files[{index}]", item
        if result.archive_file is not None:
            yield "archive_file", result.archive_file

    if isinstance(result, EditPdfResult) and result.preview is not None:
        yield "preview", result.preview

    if isinstance(result, ESignatureResult):
        if result.signed_pdf is not None:
            yield "signed_pdf", result.signed_pdf
        if result.audit_certificate is not None:
            yield "audit_certificate", result.audit_certificate
        for index, preview in enumerate(result.previews):
            yield f"previews[{index}].preview_pdf", preview.preview_pdf
        if result.latest_preview is not None:
            yield "latest_preview.preview_pdf", result.latest_preview.preview_pdf


def _validate_response_file_locations(response: AnalyzerResponse) -> None:
    for field_name, file_result in _iter_file_results_from_response(response):
        _require_storage_or_download(file_result, field_name=field_name)


__all__ = [
    "TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT",
    "PDF_DOCUMENT_ACTIONS",
    "PDF_SINGLE_FILE_ACTIONS",
    "SECURE_TRANSFORMED_ACTIONS",
    "validate_action_payload_consistency",
    "validate_input_payload_consistency",
    "validate_no_client_detected_language",
    "validate_output_policy",
    "validate_inline_text_input_security",
    "validate_answer_generation_prompt_inputs",
    "validate_word_count_contract_when_present",
    "validate_text_to_speech_request",
    "validate_vault_request",
    "validate_pdf_inputs_are_processable",
    "validate_combine_pdf_request",
    "validate_split_pdf_request",
    "validate_edit_pdf_request",
    "validate_compress_pdf_request",
    "validate_lock_pdf_request",
    "validate_esignature_request",
    "validate_generate_questions_request",
    "validate_generate_answers_request",
    "validate_question_scale",
    "get_question_range",
    "validate_analyzer_request",
    "build_inline_txt_result",
    "build_transcription_result",
    "build_archive_file_result",
    "build_document_file_result",
    "build_text_to_speech_result",
    "build_vault_item_metadata",
    "build_vault_item_result",
    "build_vault_list_result",
    "build_vault_delete_result",
    "build_pdf_document_file_result",
    "build_pdf_preview_result",
    "build_pdf_job_result",
    "build_combine_pdf_result",
    "build_split_pdf_result",
    "build_edit_pdf_result",
    "build_compress_pdf_result",
    "build_lock_pdf_result",
    "build_esignature_result",
    "build_structured_extraction_file_result",
    "build_compliance_file_result",
    "build_question_generation_inline_result",
    "build_question_generation_file_result",
    "build_answer_generation_inline_result",
    "build_answer_generation_file_result",
    "validate_result_type_for_action",
    "validate_pdf_job_result",
    "validate_text_to_speech_response",
    "validate_vault_response",
    "validate_combine_pdf_response",
    "validate_split_pdf_response",
    "validate_edit_pdf_response",
    "validate_compress_pdf_response",
    "validate_lock_pdf_response",
    "validate_esignature_response",
    "validate_regular_response_against_request",
    "validate_analyzer_response",
]
