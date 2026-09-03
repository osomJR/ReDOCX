from __future__ import annotations

from enum import Enum
import re
import unicodedata
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, SecretStr, StringConstraints, field_validator, model_validator

# CONTRACT CONSTANTS (V1)

MAX_FILE_SIZE_MB = 25
MAX_WORD_COUNT = 5000
MAX_AUDIO_SIZE_MB = 25
MAX_AUDIO_DURATION_SECONDS = 6000  
MAX_VIDEO_SIZE_MB = 100
MAX_VIDEO_DURATION_SECONDS = 600  

MAX_PDF_TOOL_FILE_SIZE_MB = 100
MAX_COMBINE_PDF_FILES = 25
MAX_COMPLIANCE_DOCUMENT_SET_FILES = 20
MAX_ESIGN_DOCUMENTS = 20
MAX_ESIGN_RECIPIENTS = 25
MAX_ESIGN_FIELDS = 250
ESIGN_SIGNERS_PER_SIGNATURE_PAGE = 6
MAX_PDF_EDIT_OPERATIONS = 2000
MAX_PDF_DRAW_PATH_CHARACTERS = 1_000_000
MAX_PDF_EDIT_AGGREGATE_DRAW_PATH_CHARACTERS = 5_000_000
MAX_PDF_EDIT_AGGREGATE_TEXT_CHARACTERS = 2_000_000
MAX_PDF_EDIT_OPERATION_ID_CHARACTERS = 128
MAX_PDF_EDIT_OUTPUT_FILENAME_CHARACTERS = 180
MAX_STRUCTURED_EXTRACTION_SELECTED_FIELDS = 200
MAX_STRUCTURED_EXTRACTION_FIELD_NAME_CHARACTERS = 120

MAX_VAULT_LIST_ITEMS = 200
MAX_PDF_PASSWORD_LENGTH = 128

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PdfEditOperationId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_PDF_EDIT_OPERATION_ID_CHARACTERS,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]
PdfEditOutputFilename = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=5,
        max_length=MAX_PDF_EDIT_OUTPUT_FILENAME_CHARACTERS,
    ),
]
StructuredFieldName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_STRUCTURED_EXTRACTION_FIELD_NAME_CHARACTERS,
    ),
]
EmailLike = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]
HexColor = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=7,
        max_length=7,
        pattern=r"^#[0-9A-Fa-f]{6}$",
    ),
]
SHA256Hex = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    ),
]
NormalizedUnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]

# FORMATS (V1)


class DocumentInputFormat(str, Enum):
    # Contract: accepted document/image inputs across ReDOCX workflows.
    pdf = "pdf"
    docx = "docx"
    txt = "txt"
    jpg = "jpg"
    jpeg = "jpeg"
    png = "png"
    xlsx = "xlsx"
    html = "html"
    htm = "htm"
    pptx = "pptx"


class ConversionOutputFormat(str, Enum):
    # File Conversion targets. PDF/A is represented as "pdfa" at request level
    # while the produced artifact remains a standards-compliant .pdf file.
    pdf = "pdf"
    pdfa = "pdfa"
    docx = "docx"
    jpg = "jpg"
    jpeg = "jpeg"
    pptx = "pptx"
    xlsx = "xlsx"


class DocumentFileOutputFormat(str, Enum):
    # Contract: file outputs can be documents/images or a ZIP archive.
    pdf = "pdf"
    docx = "docx"
    jpg = "jpg"
    jpeg = "jpeg"
    png = "png"
    pptx = "pptx"
    xlsx = "xlsx"
    zip = "zip"


class StructuredDataOutputFormat(str, Enum):
    # Contract structured extraction outputs: .csv, .json, .xlsx
    csv = "csv"
    json = "json"
    xlsx = "xlsx"


class ComplianceOutputFormat(str, Enum):
    # Contract compliance outputs: human-readable report (.pdf),
    # machine-readable report (.json), annotated source output (.pdf),
    # and multi-document source-output packages (.zip).
    pdf = "pdf"
    json = "json"
    zip = "zip"


class InlineOutputFormat(str, Enum):
    # Contract: inline text output only
    txt = "txt"


class MediaType(str, Enum):
    audio = "audio"
    video = "video"


class AudioFormat(str, Enum):
    # Contract: uploaded/recorded audio accepted by Transcribe.
    mp3 = "mp3"
    wav = "wav"
    aac = "aac"
    flac = "flac"
    webm = "webm"
    m4a = "m4a"
    ogg = "ogg"


class SpeechAudioFormat(str, Enum):
    """Provider-neutral audio artifact formats supported by Text to Speech."""

    mp3 = "mp3"
    wav = "wav"
    opus = "opus"
    aac = "aac"
    flac = "flac"


class VideoFormat(str, Enum):
    # Contract: uploaded video accepted by Transcribe. WebM remains represented
    # by AudioFormat.webm because the same container is also used for browser
    # microphone recordings and the existing union resolves that value first.
    mp4 = "mp4"
    mov = "mov"
    avi = "avi"
    mkv = "mkv"
    wmv = "wmv"


class VaultOperation(str, Enum):
    store = "store"
    retrieve = "retrieve"
    list = "list"
    delete = "delete"


class PdfEncryptionAlgorithm(str, Enum):
    aes_256 = "aes_256"


# LANGUAGES


class SystemLanguage(str, Enum):
    """
    Backend processing language selection for supported product languages.
    Frontend UI language must stay synchronized with this value.
    Backend does not model UI language separately.
    """
    english = "english"
    french = "french"


BCP47Like = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=2,
        max_length=35,
        # examples: en, fr, yo, de, pt-BR, zh-Hans, es-419
        pattern=r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})*$",
    ),
]

SUPPORTED_TRANSLATION_TARGET_TAGS = (
    "en",
    "fr",
    "es",
    "de",
    "pt-PT",
    "pt-BR",
    "ar",
    "zh-Hans",
    "zh-Hant",
    "ja",
    "ko",
    "hi",
    "yo",
    "ha",
    "ig",
    "sw",
    "tr",
    "ru",
    "it",
    "nl",
)
_TRANSLATION_TARGET_ALIASES = {
    **{tag.casefold(): tag for tag in SUPPORTED_TRANSLATION_TARGET_TAGS},
    "pt": "pt-PT",
    "zh": "zh-Hans",
    "zh-cn": "zh-Hans",
    "zh-tw": "zh-Hant",
}


def _is_en_or_fr_tag(tag: str) -> bool:
    t = tag.lower().replace("_", "-")
    return t == "en" or t.startswith("en-") or t == "fr" or t.startswith("fr-")


def _system_language_to_tag(language: SystemLanguage) -> str:
    return "en" if language == SystemLanguage.english else "fr"


# FEATURE ENUM (V1)


class FeatureType(str, Enum):
    convert = "convert"
    summarize = "summarize"
    grammar_correct = "grammar_correct"
    translate = "translate"
    transcribe = "transcribe"
    text_to_speech = "text_to_speech"
    explain = "explain"
    redact = "redact"
    data_mask = "data_mask"
    structured_extract = "structured_extract"
    compliance = "compliance"
    generate_questions = "generate_questions"
    generate_answers = "generate_answers"

    # Secure storage
    vault = "vault"

    # PDF tools
    combine_pdf = "combine_pdf"
    split_pdf = "split_pdf"
    edit_pdf = "edit_pdf"
    compress_pdf = "compress_pdf"
    lock_pdf = "lock_pdf"

    # E-signature workflow
    e_signature = "e_signature"


# OUTPUT POLICY (V1)


class OutputPolicy(BaseModel):
    # Contract: tone preservation ON, professional neutrality ON (non-optional)
    tone_preservation: Literal[True] = True
    professional_neutrality: Literal[True] = True

    # Contract:
    # - True for transformed outputs:
    #   convert / summarize / grammar_correct / translate / transcribe / text_to_speech /
    #   redact / data_mask / vault / PDF tools / e-signature
    # - False for generated outputs:
    #   explain / structured_extract / compliance / generate_questions / generate_answers
    structure_preservation: bool = Field(
        ...,
        description="True for transformed outputs; False for generated outputs (per contract).",
    )


# INPUT ARTIFACTS


class DocumentMetadata(BaseModel):
    input_format: DocumentInputFormat
    file_size_mb: float = Field(..., ge=0, le=MAX_FILE_SIZE_MB)

    # Word-count post extraction.
    # The 5000-word cap is enforced only for features that allow inline text input:
    # summarize, grammar_correct, translate, explain, generate_questions, generate_answers.
    extracted_word_count: Optional[int] = Field(default=None, ge=0)

    # OCR enabled when needed. Must be False for docx/txt.
    ocr_used: bool = False

    # Server-side detected language (do not accept from client requests).
    detected_language: Optional[BCP47Like] = None

    @field_validator("ocr_used")
    @classmethod
    def validate_ocr_used(cls, v: bool, info):
        fmt = info.data.get("input_format")
        if fmt in (DocumentInputFormat.docx, DocumentInputFormat.txt) and v:
            raise ValueError("ocr_used must be False for docx/txt inputs.")
        return v


class DocumentPayload(BaseModel):
    # For text-based AI processing actions, extracted text is required.
    # For conversion, redaction, data masking, structured extraction, and compliance,
    # text can be omitted when the backend derives it from the uploaded file.
    text: Optional[NonEmptyStr] = None
    metadata: DocumentMetadata

    # Real persisted file reference (set by upload layer)
    filename: Optional[NonEmptyStr] = None
    mime_type: Optional[NonEmptyStr] = None


class DocumentSetPayload(BaseModel):
    """
    Used for workflows that may operate on a provided document set instead of a single document.
    Per contract this applies to Structured Extraction and Compliance.
    """
    documents: List[DocumentPayload] = Field(
        ...,
        min_length=1,
        max_length=MAX_COMPLIANCE_DOCUMENT_SET_FILES,
    )


# VAULT INPUT ARTIFACTS


class VaultFilePayload(BaseModel):
    """
    Binary-safe upload reference for a Vault store operation.

    The authenticated owner is intentionally absent. The backend must derive
    ownership from the verified account/session and must never trust a client-
    supplied owner id.
    """

    kind: Literal["vault_file"]
    filename: NonEmptyStr
    content_type: NonEmptyStr = "application/octet-stream"
    file_size_bytes: int = Field(..., ge=0)
    checksum_sha256: Optional[SHA256Hex] = None
    storage_key: Optional[NonEmptyStr] = None
    upload_id: Optional[NonEmptyStr] = None
    client_encrypted: bool = False

    @model_validator(mode="after")
    def validate_persisted_upload_reference(self):
        if not self.storage_key and not self.upload_id:
            raise ValueError("VaultFilePayload requires storage_key or upload_id.")
        return self


class VaultItemReferencePayload(BaseModel):
    """Owner-scoped item reference used for retrieve and delete operations."""

    kind: Literal["vault_item_reference"]
    item_id: NonEmptyStr


class VaultQueryPayload(BaseModel):
    """Pagination and optional metadata filters for an owner's Vault listing."""

    kind: Literal["vault_query"]
    limit: int = Field(default=50, ge=1, le=MAX_VAULT_LIST_ITEMS)
    cursor: Optional[NonEmptyStr] = None
    filename_contains: Optional[NonEmptyStr] = None
    content_type: Optional[NonEmptyStr] = None


# PDF TOOLS + E-SIGNATURE INPUT ARTIFACTS


class PdfDocumentMetadata(BaseModel):
    """
    PDF-specific metadata for ReDOCX PDF Tools and ReDOCX Sign.

    This is separate from DocumentMetadata because PDF tools are not AI text-processing
    actions and may use higher file-size limits than the strict text analyzer contract.
    """
    input_format: Literal[DocumentInputFormat.pdf] = DocumentInputFormat.pdf
    file_size_mb: float = Field(..., ge=0, le=MAX_PDF_TOOL_FILE_SIZE_MB)
    page_count: Optional[int] = Field(default=None, ge=1)
    encrypted: bool = False
    password_protected: bool = False
    checksum_sha256: Optional[SHA256Hex] = None


class PdfFilePayload(BaseModel):
    """
    A persisted PDF source file.

    `kind` intentionally distinguishes this payload from DocumentPayload in untagged
    unions. The upload layer should set storage_key/upload_id after receiving the file.
    """
    kind: Literal["pdf_file"]
    metadata: PdfDocumentMetadata
    filename: NonEmptyStr
    mime_type: NonEmptyStr = "application/pdf"
    storage_key: Optional[NonEmptyStr] = None
    upload_id: Optional[NonEmptyStr] = None

    @field_validator("mime_type")
    @classmethod
    def validate_pdf_mime_type(cls, v: str):
        if v.lower() not in {"application/pdf", "application/x-pdf"}:
            raise ValueError("PDF tools and e-signature inputs must be PDF files.")
        return v

    @field_validator("filename")
    @classmethod
    def validate_pdf_filename(cls, v: str):
        if not v.lower().endswith(".pdf"):
            raise ValueError("filename must end with .pdf.")
        return v


class PdfFileSetPayload(BaseModel):
    """
    Ordered PDF set used by Combine PDF and multi-document E-Signature envelopes.
    Action-specific validators enforce 25 for combine and 20 for E-Signature.
    """
    kind: Literal["pdf_file_set"]
    documents: List[PdfFilePayload] = Field(..., min_length=2, max_length=MAX_COMBINE_PDF_FILES)

    @model_validator(mode="after")
    def validate_all_sources_are_pdf_files(self):
        for document in self.documents:
            if document.metadata.input_format != DocumentInputFormat.pdf:
                raise ValueError("PDF file sets accept PDF files only.")
        return self


class PdfPageRange(BaseModel):
    start_page: int = Field(..., ge=1)
    end_page: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_page < self.start_page:
            raise ValueError("end_page must be greater than or equal to start_page.")
        return self


class PdfRectangle(BaseModel):
    """
    Normalized PDF page rectangle.

    Coordinates are expressed as ratios from 0.0 to 1.0 so frontend previews and backend
    PDF rendering can remain independent of actual page pixel dimensions.
    """
    x: NormalizedUnitFloat
    y: NormalizedUnitFloat
    width: NormalizedUnitFloat = Field(..., gt=0)
    height: NormalizedUnitFloat = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.x + self.width > 1:
            raise ValueError("x + width must be <= 1.0.")
        if self.y + self.height > 1:
            raise ValueError("y + height must be <= 1.0.")
        return self


class PdfCompressionLevel(str, Enum):
    small_file = "small_file"
    balanced = "balanced"
    high_quality = "high_quality"


class PdfSplitMode(str, Enum):
    every_page = "every_page"
    extract_selected_pages = "extract_selected_pages"
    page_ranges = "page_ranges"


class PdfEditOperationType(str, Enum):
    add_text = "add_text"
    remove_text = "remove_text"
    add_image = "add_image"
    remove_image = "remove_image"
    add_shape = "add_shape"
    add_comment = "add_comment"
    draw = "draw"
    highlight = "highlight"
    whiteout = "whiteout"
    add_signature = "add_signature"
    remove_signature = "remove_signature"


class PdfRemovalMode(str, Enum):
    whiteout_region = "whiteout_region"
    remove_redocx_annotation = "remove_redocx_annotation"


class SignatureRepresentationType(str, Enum):
    typed = "typed"
    drawn = "drawn"
    uploaded_image = "uploaded_image"


class PdfTextAlignment(str, Enum):
    left = "left"
    center = "center"
    right = "right"
    justify = "justify"


class PdfImageFit(str, Enum):
    contain = "contain"
    stretch = "stretch"


class PdfShapeType(str, Enum):
    rectangle = "rectangle"
    ellipse = "ellipse"
    line = "line"
    arrow = "arrow"


class PdfEditBaseOperation(BaseModel):
    operation_id: Optional[PdfEditOperationId] = None
    page_number: int = Field(..., ge=1)
    rectangle: PdfRectangle


class AddTextOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.add_text]
    text: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000),
    ]
    font_size: float = Field(default=12, ge=4, le=144)
    font_family: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ] = "Helvetica"
    color_hex: HexColor = "#111111"
    font_weight: Literal["normal", "bold"] = "normal"
    font_style: Literal["normal", "italic"] = "normal"
    underline: bool = False
    strikethrough: bool = False
    text_alignment: PdfTextAlignment = PdfTextAlignment.left
    line_height: float = Field(default=1.2, ge=0.8, le=3.0)
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)
    background_color_hex: Optional[HexColor] = None
    background_opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    border_color_hex: Optional[HexColor] = None
    border_width: float = Field(default=0.0, ge=0.0, le=12.0)
    padding: float = Field(default=1.5, ge=0.0, le=36.0)
    rotation: Literal[0, 90, 180, 270] = 0
    link_url: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
        ]
    ] = None
    auto_fit: bool = True
    minimum_font_size: float = Field(default=4.0, ge=4.0, le=144.0)

    @field_validator("link_url")
    @classmethod
    def validate_link_url(cls, v: Optional[str]):
        if v is None:
            return v
        if len(v) > 2048 or any(character.isspace() for character in v):
            raise ValueError("link_url must not contain whitespace and must be at most 2048 characters.")
        if not v.lower().startswith(("http://", "https://", "mailto:")):
            raise ValueError("link_url must use http, https, or mailto.")
        return v

    @model_validator(mode="after")
    def validate_text_sizing(self):
        if self.minimum_font_size > self.font_size:
            raise ValueError("minimum_font_size cannot exceed font_size.")
        if self.border_width > 0 and self.border_color_hex is None:
            raise ValueError("border_color_hex is required when border_width is greater than zero.")
        return self


class RemoveTextOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.remove_text]
    removal_mode: PdfRemovalMode = PdfRemovalMode.whiteout_region


class AddImageOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.add_image]
    image_storage_key: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
    ]
    image_mime_type: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ]
    alt_text: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
        ]
    ] = None
    fit_mode: PdfImageFit = PdfImageFit.contain
    rotation: Literal[0, 90, 180, 270] = 0
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)
    border_color_hex: Optional[HexColor] = None
    border_width: float = Field(default=0.0, ge=0.0, le=12.0)

    @field_validator("image_mime_type")
    @classmethod
    def validate_image_mime_type(cls, v: str):
        if v.lower() not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
            raise ValueError("Supported image types are png, jpeg, jpg, and webp.")
        return v

    @model_validator(mode="after")
    def validate_image_border(self):
        if self.border_width > 0 and self.border_color_hex is None:
            raise ValueError("border_color_hex is required when border_width is greater than zero.")
        return self


class RemoveImageOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.remove_image]
    removal_mode: PdfRemovalMode = PdfRemovalMode.whiteout_region


class AddShapeOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.add_shape]
    shape_type: PdfShapeType = PdfShapeType.rectangle
    stroke_color_hex: HexColor = "#111111"
    stroke_width: float = Field(default=1.5, ge=0.25, le=25.0)
    fill_color_hex: Optional[HexColor] = None
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)


class AddCommentOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.add_comment]
    comment: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=50_000),
    ]
    author: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        ]
    ] = None
    color_hex: HexColor = "#FACC15"
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)


class DrawOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.draw]
    path_svg: Optional[
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=MAX_PDF_DRAW_PATH_CHARACTERS,
            ),
        ]
    ] = None
    strokes_storage_key: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
        ]
    ] = None
    stroke_width: float = Field(default=2, ge=0.25, le=25)
    stroke_color_hex: HexColor = "#111111"

    @model_validator(mode="after")
    def validate_draw_source(self):
        if bool(self.path_svg) == bool(self.strokes_storage_key):
            raise ValueError(
                "Draw operation requires exactly one of path_svg or strokes_storage_key."
            )
        if self.path_svg and len(self.path_svg) > MAX_PDF_DRAW_PATH_CHARACTERS:
            raise ValueError("path_svg exceeds the supported size limit.")
        return self


class HighlightOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.highlight]
    color_hex: HexColor = "#FFF176"
    opacity: float = Field(default=0.35, ge=0.05, le=1.0)


class WhiteoutOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.whiteout]


class AddSignatureOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.add_signature]
    signature_type: SignatureRepresentationType
    typed_name: Optional[NonEmptyStr] = Field(default=None, max_length=200)
    signature_image_storage_key: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
        ]
    ] = None
    signature_svg_storage_key: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
        ]
    ] = None
    consent_accepted: Literal[True] = True

    @model_validator(mode="after")
    def validate_signature_source(self):
        if self.signature_type == SignatureRepresentationType.typed:
            if not self.typed_name:
                raise ValueError("typed signature requires typed_name.")
            if self.signature_svg_storage_key or self.signature_image_storage_key:
                raise ValueError("typed signature must not include an image or SVG source.")
        elif self.signature_type == SignatureRepresentationType.drawn:
            if bool(self.signature_svg_storage_key) == bool(
                self.signature_image_storage_key
            ):
                raise ValueError(
                    "drawn signature requires exactly one of "
                    "signature_svg_storage_key or signature_image_storage_key."
                )
            if self.typed_name:
                raise ValueError("drawn signature must not include typed_name.")
        elif self.signature_type == SignatureRepresentationType.uploaded_image:
            if not self.signature_image_storage_key:
                raise ValueError("uploaded_image signature requires signature_image_storage_key.")
            if self.signature_svg_storage_key or self.typed_name:
                raise ValueError(
                    "uploaded_image signature must not include typed_name or an SVG source."
                )
        return self


class RemoveSignatureOperation(PdfEditBaseOperation):
    operation: Literal[PdfEditOperationType.remove_signature]
    field_id: Optional[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        ]
    ] = None
    removal_mode: PdfRemovalMode = PdfRemovalMode.whiteout_region


PdfEditOperation = Annotated[
    Union[
        AddTextOperation,
        RemoveTextOperation,
        AddImageOperation,
        RemoveImageOperation,
        AddShapeOperation,
        AddCommentOperation,
        DrawOperation,
        HighlightOperation,
        WhiteoutOperation,
        AddSignatureOperation,
        RemoveSignatureOperation,
    ],
    Field(discriminator="operation"),
]


class ESignatureWorkflow(str, Enum):
    self_sign = "self_sign"
    send_to_single_recipient = "send_to_single_recipient"
    send_to_multiple_recipients = "send_to_multiple_recipients"
    self_sign_then_send = "self_sign_then_send"


class ESignatureRoutingMode(str, Enum):
    sequential = "sequential"
    parallel = "parallel"


class ESignatureRecipientRole(str, Enum):
    owner = "owner"
    external_signer = "external_signer"


class ESignatureRecipient(BaseModel):
    name: NonEmptyStr = Field(..., max_length=200)
    email: EmailLike
    role: ESignatureRecipientRole = ESignatureRecipientRole.external_signer
    signing_order: int = Field(default=1, ge=1)
    required: bool = True


class ESignatureDocument(BaseModel):
    """Stable document identity inside an envelope.

    The list order is the envelope order. ``document_id`` is deliberately
    independent of the uploaded filename so fields keep pointing at the same
    document when display names are changed.
    """

    document_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=96,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        ),
    ]
    title: Optional[NonEmptyStr] = Field(default=None, max_length=200)


class ESignatureFieldType(str, Enum):
    signature = "signature"
    initials = "initials"
    date_signed = "date_signed"
    name = "name"
    email = "email"
    text = "text"
    checkbox = "checkbox"


class ESignatureField(BaseModel):
    field_id: Optional[NonEmptyStr] = None
    # Required for multi-document envelopes. It remains optional for legacy
    # single-document requests, where the server resolves it to ``document_1``.
    document_id: Optional[NonEmptyStr] = Field(default=None, max_length=96)
    assigned_to_email: EmailLike
    field_type: ESignatureFieldType
    page_number: int = Field(..., ge=1)
    rectangle: PdfRectangle
    required: bool = True
    label: Optional[NonEmptyStr] = Field(default=None, max_length=256)
    default_value: Optional[str] = Field(default=None, max_length=10_000)
    # Native AcroForm widgets are recognized during sender layout analysis. The
    # renderer can fill compatible widgets directly instead of painting over them.
    native_widget_name: Optional[NonEmptyStr] = Field(default=None, max_length=256)
    placement_source: Literal[
        "manual",
        "signature_line_suggestion",
        "native_form_field",
        "signature_page",
    ] = "manual"


class ESignatureSelfSigner(BaseModel):
    name: NonEmptyStr = Field(..., max_length=200)
    email: EmailLike
    signature: Optional[AddSignatureOperation] = None


class ESignatureAction(str, Enum):
    create_draft = "create_draft"
    send = "send"
    sign = "sign"
    complete_signing = "complete_signing"
    void = "void"


class MediaPayload(BaseModel):
    media_type: MediaType
    media_format: Union[AudioFormat, VideoFormat]
    file_size_mb: float = Field(..., ge=0)
    duration_seconds: int = Field(..., ge=1)
    filename: Optional[NonEmptyStr] = None
    mime_type: Optional[NonEmptyStr] = None

    # Server-side detected language (do not accept from client requests).
    detected_language: Optional[BCP47Like] = None

    @model_validator(mode="after")
    def validate_media_limits_and_format(self):
        if self.media_type == MediaType.audio:
            if self.media_format not in {
                AudioFormat.mp3,
                AudioFormat.wav,
                AudioFormat.aac,
                AudioFormat.flac,
                AudioFormat.webm,
                AudioFormat.m4a,
                AudioFormat.ogg,
            }:
                raise ValueError(
                    "Audio media_format must be one of: mp3, wav, aac, flac, webm, m4a, ogg."
                )
            if self.file_size_mb > MAX_AUDIO_SIZE_MB:
                raise ValueError(f"Audio size must be <= {MAX_AUDIO_SIZE_MB} MB.")
            if self.duration_seconds > MAX_AUDIO_DURATION_SECONDS:
                raise ValueError(f"Audio duration must be <= {MAX_AUDIO_DURATION_SECONDS} seconds.")
        else:
            if self.media_format not in {
                VideoFormat.mp4,
                VideoFormat.mov,
                VideoFormat.avi,
                VideoFormat.mkv,
                VideoFormat.wmv,
                AudioFormat.webm,
            }:
                raise ValueError(
                    "Video media_format must be one of: mp4, mov, avi, mkv, wmv, webm."
                )
            if self.file_size_mb > MAX_VIDEO_SIZE_MB:
                raise ValueError(f"Video size must be <= {MAX_VIDEO_SIZE_MB} MB.")
            if self.duration_seconds > MAX_VIDEO_DURATION_SECONDS:
                raise ValueError(f"Video duration must be <= {MAX_VIDEO_DURATION_SECONDS} seconds.")
        return self


InputArtifact = Union[
    PdfFileSetPayload,
    PdfFilePayload,
    VaultFilePayload,
    VaultItemReferencePayload,
    VaultQueryPayload,
    DocumentPayload,
    DocumentSetPayload,
    MediaPayload,
]


# FEATURE PAYLOADS


class ConversionRequest(BaseModel):
    feature: Literal[FeatureType.convert]
    output_format: ConversionOutputFormat

    @staticmethod
    def _allowed_conversion_pairs() -> set[tuple[DocumentInputFormat, ConversionOutputFormat]]:
        # Central File Conversion contract. Keep this set synchronized with
        # processing/conversion/convert.py and the conversion page.
        return {
            (DocumentInputFormat.pdf, ConversionOutputFormat.docx),
            (DocumentInputFormat.pdf, ConversionOutputFormat.jpg),
            (DocumentInputFormat.pdf, ConversionOutputFormat.pptx),
            (DocumentInputFormat.pdf, ConversionOutputFormat.xlsx),
            (DocumentInputFormat.pdf, ConversionOutputFormat.pdfa),
            (DocumentInputFormat.docx, ConversionOutputFormat.pdf),
            (DocumentInputFormat.xlsx, ConversionOutputFormat.pdf),
            (DocumentInputFormat.html, ConversionOutputFormat.pdf),
            (DocumentInputFormat.htm, ConversionOutputFormat.pdf),
            (DocumentInputFormat.pptx, ConversionOutputFormat.pdf),
            (DocumentInputFormat.jpg, ConversionOutputFormat.pdf),
            (DocumentInputFormat.jpg, ConversionOutputFormat.docx),
            (DocumentInputFormat.jpeg, ConversionOutputFormat.pdf),
            (DocumentInputFormat.jpeg, ConversionOutputFormat.docx),
            (DocumentInputFormat.png, ConversionOutputFormat.jpg),
            (DocumentInputFormat.png, ConversionOutputFormat.jpeg),
        }

    def validate_pair(self, input_format: DocumentInputFormat) -> None:
        if (input_format, self.output_format) not in self._allowed_conversion_pairs():
            raise ValueError(
                f"Unsupported conversion pair: {input_format.value} -> {self.output_format.value} "
                f"(strict v1 contract)."
            )


class SummarizationRequest(BaseModel):
    feature: Literal[FeatureType.summarize]


class GrammarCorrectionRequest(BaseModel):
    feature: Literal[FeatureType.grammar_correct]


class TranslationRequest(BaseModel):
    """
    Product-wide supported system languages remain English/French.
    Source language may be auto-detected or supplied as a BCP-47-like tag.
    Target language is restricted to the explicit, UI-synchronized registry so
    the product never advertises an arbitrary model language as supported.
    """
    feature: Literal[FeatureType.translate]
    source_language: Literal["auto"] | BCP47Like = "auto"
    target_language: BCP47Like

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, v: str):
        if v.lower() == "auto":
            raise ValueError("target_language cannot be 'auto'.")
        normalized = v.replace("_", "-").casefold()
        canonical = _TRANSLATION_TARGET_ALIASES.get(normalized)
        if canonical is None:
            supported = ", ".join(SUPPORTED_TRANSLATION_TARGET_TAGS)
            raise ValueError(
                f"Unsupported target_language '{v}'. Supported tags: {supported}."
            )
        return canonical


class TranscriptionRequest(BaseModel):
    feature: Literal[FeatureType.transcribe]
    preserve_filler_words: bool = True
    remove_background_noise: bool = False
    diarize_speakers: bool = True


class TextToSpeechRequest(BaseModel):
    """Provider-neutral speech synthesis contract for PDF, DOCX, and TXT input."""

    feature: Literal[FeatureType.text_to_speech]
    voice_id: NonEmptyStr = "default"
    output_format: SpeechAudioFormat = SpeechAudioFormat.mp3
    output_filename: NonEmptyStr = "spoken-document.mp3"
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    preserve_text_exactly: Literal[True] = True

    @model_validator(mode="after")
    def validate_output_filename_extension(self):
        expected_suffix = f".{self.output_format.value}"
        if not self.output_filename.lower().endswith(expected_suffix):
            raise ValueError(
                f"output_filename must end with {expected_suffix} when output_format="
                f"'{self.output_format.value}'."
            )
        return self


class ExplanationRequest(BaseModel):
    feature: Literal[FeatureType.explain]
    allow_external_knowledge: bool = False


class SensitiveDataType(str, Enum):
    name = "name"
    email_address = "email_address"
    phone_number = "phone_number"
    account_number = "account_number"
    card_number = "card_number"
    national_id = "national_id"
    tax_id = "tax_id"
    passport_number = "passport_number"
    contact_address = "contact_address"
    date_of_birth = "date_of_birth"
    age = "age"
    signature = "signature"


class RedactionMaskingDocumentType(str, Enum):
    # Financial / commercial
    invoice = "invoice"
    receipt = "receipt"
    bank_statement = "bank_statement"
    financial_statement = "financial_statement"
    tax_document = "tax_document"
    insurance_document = "insurance_document"
    procurement_document = "procurement_document"
    utility_telecom_document = "utility_telecom_document"

    # Identity / onboarding / people records
    kyc_document = "kyc_document"
    id_document = "id_document"
    medical_record = "medical_record"
    employment_hr_document = "employment_hr_document"
    payroll_document = "payroll_document"
    resume_cv = "resume_cv"
    immigration_travel_document = "immigration_travel_document"

    # Education / research
    academic_record = "academic_record"
    academic_certificate = "academic_certificate"
    admission_enrollment_document = "admission_enrollment_document"
    research_technical_document = "research_technical_document"

    # Legal / institutional / organizational
    contract = "contract"
    legal_document = "legal_document"
    government_public_record = "government_public_record"
    property_real_estate_document = "property_real_estate_document"
    business_corporate_document = "business_corporate_document"
    audit_document = "audit_document"
    compliance_regulatory_document = "compliance_regulatory_document"
    policy_procedure_document = "policy_procedure_document"

    # General information artifacts
    historical_archival_document = "historical_archival_document"
    correspondence = "correspondence"
    application_form = "application_form"
    general_document = "general_document"


_ALL_SENSITIVE_DATA_TYPES = [item for item in SensitiveDataType]


class RedactionRequest(BaseModel):
    feature: Literal[FeatureType.redact]
    document_type: Optional[RedactionMaskingDocumentType] = None
    target_data: List[SensitiveDataType] = Field(default_factory=lambda: list(_ALL_SENSITIVE_DATA_TYPES))
    review_exclusions: List[NonEmptyStr] = Field(
        default_factory=list,
        description="Items the user reviewed and explicitly chose not to redact before final export.",
    )


class DataMaskingRequest(BaseModel):
    feature: Literal[FeatureType.data_mask]
    document_type: Optional[RedactionMaskingDocumentType] = None
    target_data: List[SensitiveDataType] = Field(default_factory=lambda: list(_ALL_SENSITIVE_DATA_TYPES))
    review_exclusions: List[NonEmptyStr] = Field(
        default_factory=list,
        description="Items the user reviewed and explicitly chose not to mask before final export.",
    )
    masking_mode: Literal["partial"] = "partial"


class StructuredExtractionDocumentClass(str, Enum):
    form = "form"
    memo = "memo"
    invoice = "invoice"
    receipt = "receipt"
    bank_statement = "bank_statement"
    kyc_document = "kyc_document"
    id_document = "id_document"
    contract = "contract"
    legal_record = "legal_record"
    medical_record = "medical_record"
    procurement_document = "procurement_document"
    technical_report = "technical_report"
    incident_report = "incident_report"
    insurance_document = "insurance_document"
    hr_record = "hr_record"
    onboarding_document = "onboarding_document"
    ticket = "ticket"


class StructuredExtractionResultShape(str, Enum):
    key_value_fields = "key_value_fields"
    tables = "tables"
    row_based_records = "row_based_records"
    machine_readable = "machine_readable"


class StructuredExtractionRequest(BaseModel):
    feature: Literal[FeatureType.structured_extract]
    document_classes: List[StructuredExtractionDocumentClass] = Field(
        default_factory=list,
        max_length=len(StructuredExtractionDocumentClass),
    )
    selected_fields: List[StructuredFieldName] = Field(
        default_factory=list,
        max_length=MAX_STRUCTURED_EXTRACTION_SELECTED_FIELDS,
        description="Predefined or user-selected fields to extract strictly from the provided document or document set.",
    )
    output_format: StructuredDataOutputFormat = StructuredDataOutputFormat.json
    result_shape: StructuredExtractionResultShape = StructuredExtractionResultShape.machine_readable
    allow_external_knowledge: Literal[False] = False
    require_human_review: Literal[True] = True

    @model_validator(mode="after")
    def validate_unique_extraction_options(self):
        if len(set(self.document_classes)) != len(self.document_classes):
            raise ValueError("document_classes must not contain duplicates.")

        normalized_fields = [
            re.sub(
                r"[^\w]+",
                "_",
                unicodedata.normalize("NFKC", item).casefold(),
                flags=re.UNICODE,
            ).strip("_")
            for item in self.selected_fields
        ]
        if any(not item for item in normalized_fields):
            raise ValueError("selected_fields must contain a readable field name.")
        if len(set(normalized_fields)) != len(normalized_fields):
            raise ValueError(
                "selected_fields must not contain duplicate or equivalent names."
            )
        return self


class ComplianceJurisdiction(str, Enum):
    nigeria = "nigeria"
    us = "us"
    uk = "uk"
    sa = "sa"
    canada = "canada"
    france = "france"
    togo = "togo"
    ghana = "ghana"


class ComplianceSectorPack(str, Enum):
    
    # Country-specific or legacy core packs
    
    nigeria_core_control_library = "nigeria_core_control_library"
    core_control_library = "core_control_library"

    # Shared sector packs
    
    accounting = "accounting"
    agriculture = "agriculture"
    aviation = "aviation"
    banking_and_fintech = "banking_and_fintech"
    payment_platforms_and_services = "payment_platforms_and_services"
    energy_and_power = "energy_and_power"
    health = "health"
    insurance = "insurance"

    # Keep both names for compatibility with existing and new folder conventions
    
    legal_and_law = "legal_and_law"
    law_and_legal = "law_and_legal"
    manufacturing = "manufacturing"
    maritime_and_shipping = "maritime_and_shipping"
    media = "media"
    mining = "mining"
    ngo = "ngo"
    oil_and_gas = "oil_and_gas"
    pharmaceuticals = "pharmaceuticals"
    sports = "sports"
    tech = "tech"
    telecom = "telecom"

def compliance_core_pack_for_jurisdiction(
    jurisdiction: ComplianceJurisdiction,
) -> ComplianceSectorPack:
    return ComplianceSectorPack.core_control_library

class ComplianceRegulatoryDomain(str, Enum):
    privacy = "privacy"
    cybersecurity = "cybersecurity"
    aml = "aml"
    consumer_protection = "consumer_protection"
    public_sector_access_to_information = "public_sector_access_to_information"
    licensing = "licensing"
    registration = "registration"
    sector_regulator_requirements = "sector_regulator_requirements"


class ComplianceReportVariant(str, Enum):
    human_readable_report = "human_readable_report"
    machine_readable_report = "machine_readable_report"
    annotated_source_output = "annotated_source_output"


class ComplianceRequest(BaseModel):
    feature: Literal[FeatureType.compliance]
    jurisdiction: ComplianceJurisdiction = ComplianceJurisdiction.nigeria
    sector_packs: List[ComplianceSectorPack] = Field(
    default_factory=lambda: [ComplianceSectorPack.core_control_library],
    min_length=1,
    )
    regulatory_domains: List[ComplianceRegulatoryDomain] = Field(default_factory=list)
    report_variant: ComplianceReportVariant = ComplianceReportVariant.human_readable_report
    require_human_review: Literal[True] = True

    @model_validator(mode="after")
    def validate_sector_pack_selection(self):
        packs = set(self.sector_packs)
        if not packs:
            raise ValueError("At least one compliance sector pack must be supplied.")
        if len(packs) != len(self.sector_packs):
            raise ValueError("Compliance sector_packs must not contain duplicates.")
        if len(set(self.regulatory_domains)) != len(self.regulatory_domains):
            raise ValueError("Compliance regulatory_domains must not contain duplicates.")

        required_core_pack = compliance_core_pack_for_jurisdiction(self.jurisdiction)

        if required_core_pack not in packs:
            raise ValueError(
                f"When sector-specific packs are requested for {self.jurisdiction.value}, "
                f"{required_core_pack.value} must also be included."
            )

        return self


class QuestionGenerationRequest(BaseModel):
    """
    The source defines syllabus scope. Standard, stable subject knowledge may be
    used to turn a short topic into valid exam practice, but unrelated external
    facts or current/web knowledge remain forbidden.
    """
    feature: Literal[FeatureType.generate_questions]
    allow_external_knowledge: Literal[False] = False
    allow_standard_subject_knowledge: Literal[True] = True


class AnswerGenerationRequest(BaseModel):
    """Answer using source scope plus the standard methods needed to solve it."""
    feature: Literal[FeatureType.generate_answers]
    allow_external_knowledge: Literal[False] = False
    allow_standard_subject_knowledge: Literal[True] = True

    # Encodes the product rule that answer generation is only a follow-on action
    # after question generation. The frontend must still enforce button visibility.
    prerequisite_action: Literal[FeatureType.generate_questions] = FeatureType.generate_questions
    requires_generated_questions: Literal[True] = True
    questions: List[NonEmptyStr]

    @field_validator("questions")
    @classmethod
    def validate_numbered_questions(cls, v: List[str]):
        if not v:
            raise ValueError("Questions list cannot be empty.")
        for i, q in enumerate(v, start=1):
            if not q.lstrip().startswith(f"{i}."):
                raise ValueError("Questions must be sequentially numbered starting at 1 (e.g., '1. ...').")
        return v


# VAULT FEATURE PAYLOAD


class VaultRequest(BaseModel):
    feature: Literal[FeatureType.vault]
    operation: VaultOperation

    # These literals encode non-negotiable authorization boundaries. The route
    # still has to enforce them using the verified server-side account identity.
    owner_authentication_required: Literal[True] = True
    private_to_owner: Literal[True] = True
    encryption_at_rest_required: Literal[True] = True
    cross_account_access_allowed: Literal[False] = False
    confirm_delete: bool = False

    @model_validator(mode="after")
    def validate_delete_confirmation(self):
        if self.operation == VaultOperation.delete and self.confirm_delete is not True:
            raise ValueError("Vault delete operation requires confirm_delete=True.")
        return self


# PDF TOOLS + E-SIGNATURE FEATURE PAYLOADS


class CombinePdfRequest(BaseModel):
    feature: Literal[FeatureType.combine_pdf]
    output_filename: NonEmptyStr = "combined-document.pdf"
    preserve_bookmarks: bool = True
    preserve_metadata: bool = False

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: str):
        if not v.lower().endswith(".pdf"):
            raise ValueError("output_filename must end with .pdf.")
        return v


class SplitPdfRequest(BaseModel):
    feature: Literal[FeatureType.split_pdf]
    mode: PdfSplitMode
    selected_pages: List[int] = Field(default_factory=list)
    page_ranges: List[PdfPageRange] = Field(default_factory=list)
    output_basename: NonEmptyStr = "split-document"

    @field_validator("selected_pages")
    @classmethod
    def validate_selected_pages_are_unique(cls, v: List[int]):
        if any(page < 1 for page in v):
            raise ValueError("selected_pages must contain page numbers >= 1.")
        if len(set(v)) != len(v):
            raise ValueError("selected_pages cannot contain duplicates.")
        return v

    @model_validator(mode="after")
    def validate_mode_inputs(self):
        if self.mode == PdfSplitMode.every_page:
            if self.selected_pages or self.page_ranges:
                raise ValueError("every_page split mode must not include selected_pages or page_ranges.")
        elif self.mode == PdfSplitMode.extract_selected_pages:
            if not self.selected_pages:
                raise ValueError("extract_selected_pages requires selected_pages.")
            if self.page_ranges:
                raise ValueError("extract_selected_pages must not include page_ranges.")
        elif self.mode == PdfSplitMode.page_ranges:
            if not self.page_ranges:
                raise ValueError("page_ranges split mode requires page_ranges.")
            if self.selected_pages:
                raise ValueError("page_ranges split mode must not include selected_pages.")
        return self


class EditPdfRequest(BaseModel):
    feature: Literal[FeatureType.edit_pdf]
    operations: List[PdfEditOperation] = Field(..., min_length=1, max_length=MAX_PDF_EDIT_OPERATIONS)
    output_filename: PdfEditOutputFilename = "edited-document.pdf"
    generate_preview: bool = True

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: str):
        if not v.lower().endswith(".pdf"):
            raise ValueError("output_filename must end with .pdf.")
        if v in {".", ".."} or "/" in v or "\\" in v or any(
            ord(character) < 32 for character in v
        ):
            raise ValueError("output_filename must be a plain filename without a path.")
        return v

    @model_validator(mode="after")
    def validate_aggregate_operation_size(self):
        draw_characters = sum(
            len(str(getattr(operation, "path_svg", "") or ""))
            for operation in self.operations
        )
        text_characters = sum(
            len(str(getattr(operation, field, "") or ""))
            for operation in self.operations
            for field in ("text", "comment", "typed_name")
        )
        if draw_characters > MAX_PDF_EDIT_AGGREGATE_DRAW_PATH_CHARACTERS:
            raise ValueError(
                "Combined drawing data exceeds the PDF edit request safety limit."
            )
        if text_characters > MAX_PDF_EDIT_AGGREGATE_TEXT_CHARACTERS:
            raise ValueError(
                "Combined text data exceeds the PDF edit request safety limit."
            )
        return self


class CompressPdfRequest(BaseModel):
    feature: Literal[FeatureType.compress_pdf]
    compression_level: PdfCompressionLevel = PdfCompressionLevel.balanced
    output_filename: NonEmptyStr = "compressed-document.pdf"
    async_processing: bool = True

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, v: str):
        if not v.lower().endswith(".pdf"):
            raise ValueError("output_filename must end with .pdf.")
        return v


class PdfPermissionPolicy(BaseModel):
    """Permissions granted after a user successfully unlocks the PDF."""

    allow_printing: bool = False
    allow_copying: bool = False
    allow_modifying: bool = False
    allow_annotations: bool = False
    allow_form_filling: bool = False
    allow_accessibility: bool = True


class LockPdfRequest(BaseModel):
    feature: Literal[FeatureType.lock_pdf]
    password: SecretStr
    encryption: Literal[PdfEncryptionAlgorithm.aes_256] = PdfEncryptionAlgorithm.aes_256
    permissions: PdfPermissionPolicy = Field(default_factory=PdfPermissionPolicy)
    output_filename: NonEmptyStr = "locked-document.pdf"

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: SecretStr):
        password = value.get_secret_value()
        if len(password) < 8:
            raise ValueError("PDF password must contain at least 8 characters.")
        if len(password) > MAX_PDF_PASSWORD_LENGTH:
            raise ValueError(
                f"PDF password must contain at most {MAX_PDF_PASSWORD_LENGTH} characters."
            )
        return value

    @field_validator("output_filename")
    @classmethod
    def validate_output_filename(cls, value: str):
        if not value.lower().endswith(".pdf"):
            raise ValueError("output_filename must end with .pdf.")
        return value


class ESignatureRequest(BaseModel):
    feature: Literal[FeatureType.e_signature]
    action: ESignatureAction = ESignatureAction.create_draft
    workflow: ESignatureWorkflow
    routing_mode: ESignatureRoutingMode = ESignatureRoutingMode.sequential

    # For self_sign, this is the user signing their own uploaded document.
    # For send_to_* workflows, recipients are the external signers.
    # For self_sign_then_send, self_signer signs first, then recipients sign.
    self_signer: Optional[ESignatureSelfSigner] = None
    documents: List[ESignatureDocument] = Field(
        default_factory=list,
        max_length=MAX_ESIGN_DOCUMENTS,
    )
    recipients: List[ESignatureRecipient] = Field(default_factory=list, max_length=MAX_ESIGN_RECIPIENTS)
    fields: List[ESignatureField] = Field(default_factory=list, max_length=MAX_ESIGN_FIELDS)

    email_subject: Optional[NonEmptyStr] = Field(default=None, max_length=200)
    email_message: Optional[NonEmptyStr] = Field(default=None, max_length=5_000)
    expires_in_days: int = Field(default=30, ge=1, le=180)
    # When enabled, ReDOCX appends one or more dedicated signature pages before
    # envelope creation. Existing document pages are never reflowed or obscured.
    add_signature_page: bool = False

    # Product requirement: ReDOCX should produce a preview after each person signs.
    generate_preview_after_each_signature: Literal[True] = True

    @model_validator(mode="after")
    def validate_esignature_workflow(self):
        recipient_count = len(self.recipients)

        if self.workflow == ESignatureWorkflow.self_sign:
            if self.self_signer is None:
                raise ValueError("self_sign workflow requires self_signer.")
            if recipient_count != 0:
                raise ValueError("self_sign workflow must not include external recipients.")

        elif self.workflow == ESignatureWorkflow.send_to_single_recipient:
            if recipient_count != 1:
                raise ValueError("send_to_single_recipient requires exactly one external recipient.")

        elif self.workflow == ESignatureWorkflow.send_to_multiple_recipients:
            if recipient_count < 2:
                raise ValueError("send_to_multiple_recipients requires at least two external recipients.")

        elif self.workflow == ESignatureWorkflow.self_sign_then_send:
            if self.self_signer is None:
                raise ValueError("self_sign_then_send requires self_signer.")
            if recipient_count < 1:
                raise ValueError("self_sign_then_send requires at least one external recipient.")

        emails = [recipient.email.lower() for recipient in self.recipients]
        if self.self_signer:
            emails.append(self.self_signer.email.lower())

        if len(set(emails)) != len(emails):
            raise ValueError("Each signer email must be unique within an e-signature workflow.")

        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Each e-signature document_id must be unique within an envelope.")

        known_document_ids = set(document_ids)
        referenced_document_ids = {
            field.document_id for field in self.fields if field.document_id is not None
        }
        if known_document_ids and referenced_document_ids - known_document_ids:
            raise ValueError("Every field document_id must identify a document in the envelope.")

        field_assignees = {field.assigned_to_email.lower() for field in self.fields}
        known_signers = set(emails)
        unknown_assignees = field_assignees - known_signers
        if unknown_assignees:
            raise ValueError("All e-signature fields must be assigned to a known signer email.")

        if self.routing_mode == ESignatureRoutingMode.parallel:
            # Parallel workflows should not imply multi-step order.
            for recipient in self.recipients:
                if recipient.signing_order != 1:
                    raise ValueError("parallel routing requires all recipient signing_order values to be 1.")

        return self


FeaturePayload = Union[
    ConversionRequest,
    SummarizationRequest,
    GrammarCorrectionRequest,
    TranslationRequest,
    TranscriptionRequest,
    TextToSpeechRequest,
    ExplanationRequest,
    RedactionRequest,
    DataMaskingRequest,
    StructuredExtractionRequest,
    ComplianceRequest,
    QuestionGenerationRequest,
    AnswerGenerationRequest,
    VaultRequest,
    CombinePdfRequest,
    SplitPdfRequest,
    EditPdfRequest,
    CompressPdfRequest,
    LockPdfRequest,
    ESignatureRequest,
]


# QUESTION COUNT SCALING (V1)


class QuestionScale(str, Enum):
    small = "small"
    medium = "medium"
    large = "large"


class QuestionScalingRule(BaseModel):
    classification: QuestionScale
    min_words: int
    max_words: int
    min_questions: int
    max_questions: int

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.max_words < self.min_words:
            raise ValueError("Invalid word range: max_words must be >= min_words.")
        if self.max_questions < self.min_questions:
            raise ValueError("Invalid question range: max_questions must be >= min_questions.")
        return self


QUESTION_SCALING_RULES: List[QuestionScalingRule] = [
    QuestionScalingRule(
        classification=QuestionScale.small,
        min_words=1,
        max_words=1500,
        min_questions=6,
        max_questions=10,
    ),
    QuestionScalingRule(
        classification=QuestionScale.medium,
        min_words=1501,
        max_words=3499,
        min_questions=11,
        max_questions=20,
    ),
    QuestionScalingRule(
        classification=QuestionScale.large,
        min_words=3500,
        max_words=5000,
        min_questions=21,
        max_questions=30,
    ),
]


def classify_word_count(word_count: int) -> QuestionScalingRule:
    for rule in QUESTION_SCALING_RULES:
        if rule.min_words <= word_count <= rule.max_words:
            return rule
    raise ValueError(f"Word count {word_count} is out of supported range (1-5000).")


# REQUEST ENVELOPE + CONTRACT ENFORCEMENT


TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT = {
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.translate,
    FeatureType.explain,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
}

_SINGLE_DOCUMENT_ACTIONS = {
    FeatureType.convert,
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.translate,
    FeatureType.explain,
    FeatureType.redact,
    FeatureType.data_mask,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
    FeatureType.text_to_speech,
}

_STRUCTURED_EXTRACTION_AND_COMPLIANCE_ACTIONS = {
    FeatureType.structured_extract,
    FeatureType.compliance,
}

_PDF_TOOL_ACTIONS = {
    FeatureType.combine_pdf,
    FeatureType.split_pdf,
    FeatureType.edit_pdf,
    FeatureType.compress_pdf,
    FeatureType.lock_pdf,
}

_PDF_DOCUMENT_ACTIONS = _PDF_TOOL_ACTIONS | {FeatureType.e_signature}

EN_FR_ONLY_NON_TRANSLATE_ACTIONS = {
    FeatureType.summarize,
    FeatureType.grammar_correct,
    FeatureType.explain,
    FeatureType.redact,
    FeatureType.data_mask,
    FeatureType.structured_extract,
    FeatureType.compliance,
    FeatureType.generate_questions,
    FeatureType.generate_answers,
}

_CONVERSION_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.jpg,
    DocumentInputFormat.jpeg,
    DocumentInputFormat.png,
    DocumentInputFormat.xlsx,
    DocumentInputFormat.html,
    DocumentInputFormat.htm,
    DocumentInputFormat.pptx,
}

_TEXT_AI_DOC_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.txt,
}

_REDACTION_MASKING_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.jpg,
    DocumentInputFormat.jpeg,
    DocumentInputFormat.png,
}

_STRUCTURED_EXTRACTION_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.jpg,
    DocumentInputFormat.jpeg,
    DocumentInputFormat.png,
}

_COMPLIANCE_INPUTS = {
    DocumentInputFormat.pdf,
    DocumentInputFormat.docx,
    DocumentInputFormat.jpg,
    DocumentInputFormat.jpeg,
    DocumentInputFormat.png,
}


def _iter_documents(input_artifact: InputArtifact) -> List[DocumentPayload]:
    if isinstance(input_artifact, DocumentPayload):
        return [input_artifact]
    if isinstance(input_artifact, DocumentSetPayload):
        return list(input_artifact.documents)
    return []


class AnalyzerRequest(BaseModel):
    """
    Backend request contract.

    - No ui_language field exists in the backend schema.
    - system_language is English/French only and should be kept synchronized by the frontend
      with the UI language selection.
    - Translation source/target selection is handled inside TranslationRequest.
    NOTE (server supplies detection):
    - detected_language fields are optional carriers and are not accepted from external clients.
    - server performs detection at runtime.
    """
    action: FeatureType
    input: InputArtifact
    payload: FeaturePayload
    policy: OutputPolicy
    system_language: SystemLanguage = SystemLanguage.english

    @model_validator(mode="after")
    def validate_contract_rules(self):
        # action must match payload.feature
        if self.payload.feature != self.action:
            raise ValueError("action must match payload.feature exactly.")

        # input type enforcement
        if self.action == FeatureType.vault:
            if not isinstance(
                self.input,
                (VaultFilePayload, VaultItemReferencePayload, VaultQueryPayload),
            ):
                raise ValueError(
                    "vault requires VaultFilePayload, VaultItemReferencePayload, or VaultQueryPayload."
                )
        elif self.action == FeatureType.transcribe:
            if not isinstance(self.input, MediaPayload):
                raise ValueError("transcribe requires MediaPayload as input.")
        elif self.action == FeatureType.combine_pdf:
            if not isinstance(self.input, PdfFileSetPayload):
                raise ValueError("combine_pdf requires PdfFileSetPayload as input.")
        elif self.action == FeatureType.e_signature:
            if not isinstance(self.input, (PdfFilePayload, PdfFileSetPayload)):
                raise ValueError(
                    "e_signature requires PdfFilePayload or PdfFileSetPayload as input."
                )
        elif self.action in {
            FeatureType.split_pdf,
            FeatureType.edit_pdf,
            FeatureType.compress_pdf,
            FeatureType.lock_pdf,
        }:
            if not isinstance(self.input, PdfFilePayload):
                raise ValueError(f"{self.action.value} requires PdfFilePayload as input.")
        elif self.action in _STRUCTURED_EXTRACTION_AND_COMPLIANCE_ACTIONS:
            if not isinstance(self.input, (DocumentPayload, DocumentSetPayload)):
                raise ValueError(f"{self.action.value} requires DocumentPayload or DocumentSetPayload as input.")
        else:
            if not isinstance(self.input, DocumentPayload):
                raise ValueError(f"{self.action.value} requires DocumentPayload as input.")

        # reject client-supplied detected_language (server supplies detection)
        for document in _iter_documents(self.input):
            if document.metadata.detected_language is not None:
                raise ValueError("detected_language must not be provided by client; server supplies detection.")
        if isinstance(self.input, MediaPayload) and self.input.detected_language is not None:
            raise ValueError("detected_language must not be provided by client; server supplies detection.")

        # per-feature input extension enforcement
        if self.action in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
            assert isinstance(self.input, DocumentPayload)
            if self.input.metadata.input_format not in _TEXT_AI_DOC_INPUTS:
                raise ValueError(
                    f"{self.action.value} only supports input formats: pdf, docx, txt (strict contract rule)."
                )

        if self.action == FeatureType.text_to_speech:
            assert isinstance(self.input, DocumentPayload)
            if self.input.metadata.input_format not in _TEXT_AI_DOC_INPUTS:
                raise ValueError("text_to_speech only supports input formats: pdf, docx, txt.")
            if not self.input.text:
                raise ValueError("text_to_speech requires extracted document text.")
            if not isinstance(self.payload, TextToSpeechRequest):
                raise ValueError("text_to_speech requires TextToSpeechRequest payload.")

        if self.action == FeatureType.vault:
            if not isinstance(self.payload, VaultRequest):
                raise ValueError("vault requires VaultRequest payload.")

            expected_input_by_operation = {
                VaultOperation.store: VaultFilePayload,
                VaultOperation.retrieve: VaultItemReferencePayload,
                VaultOperation.list: VaultQueryPayload,
                VaultOperation.delete: VaultItemReferencePayload,
            }
            expected_input = expected_input_by_operation[self.payload.operation]
            if not isinstance(self.input, expected_input):
                raise ValueError(
                    f"vault operation '{self.payload.operation.value}' requires "
                    f"{expected_input.__name__} input."
                )

        if self.action in {FeatureType.redact, FeatureType.data_mask}:
            assert isinstance(self.input, DocumentPayload)
            if self.input.metadata.input_format not in _REDACTION_MASKING_INPUTS:
                raise ValueError(
                    f"{self.action.value} only supports input formats: pdf, docx, jpg, jpeg, png "
                    f"(strict contract rule)."
                )

        if self.action == FeatureType.structured_extract:
            for document in _iter_documents(self.input):
                if document.metadata.input_format not in _STRUCTURED_EXTRACTION_INPUTS:
                    raise ValueError(
                        "structured_extract only supports input formats: pdf, docx, jpg, jpeg, png "
                        "(strict contract rule)."
                    )

        if self.action == FeatureType.compliance:
            for document in _iter_documents(self.input):
                if document.metadata.input_format not in _COMPLIANCE_INPUTS:
                    raise ValueError(
                        "compliance only supports input formats: pdf, docx, jpg, jpeg, png "
                        "(strict contract rule)."
                    )

        if self.action == FeatureType.convert:
            assert isinstance(self.input, DocumentPayload)
            if self.input.metadata.input_format not in _CONVERSION_INPUTS:
                raise ValueError(
                    "convert only supports: pdf, docx, jpg, jpeg, png, xlsx, html, htm, pptx "
                    "(strict contract rule)."
                )
            assert isinstance(self.payload, ConversionRequest)
            self.payload.validate_pair(self.input.metadata.input_format)

        # PDF tool + e-signature request validation
        if self.action == FeatureType.combine_pdf:
            assert isinstance(self.input, PdfFileSetPayload)
            if len(self.input.documents) > MAX_COMBINE_PDF_FILES:
                raise ValueError(f"combine_pdf supports at most {MAX_COMBINE_PDF_FILES} PDF files.")
            if not isinstance(self.payload, CombinePdfRequest):
                raise ValueError("combine_pdf requires CombinePdfRequest payload.")

        if self.action == FeatureType.split_pdf:
            assert isinstance(self.input, PdfFilePayload)
            if not isinstance(self.payload, SplitPdfRequest):
                raise ValueError("split_pdf requires SplitPdfRequest payload.")
            page_count = self.input.metadata.page_count
            if page_count is not None:
                if self.payload.selected_pages and max(self.payload.selected_pages) > page_count:
                    raise ValueError("selected_pages cannot exceed source PDF page_count.")
                for page_range in self.payload.page_ranges:
                    if page_range.end_page > page_count:
                        raise ValueError("page_ranges cannot exceed source PDF page_count.")

        if self.action == FeatureType.edit_pdf:
            assert isinstance(self.input, PdfFilePayload)
            if not isinstance(self.payload, EditPdfRequest):
                raise ValueError("edit_pdf requires EditPdfRequest payload.")
            page_count = self.input.metadata.page_count
            if page_count is not None:
                for operation in self.payload.operations:
                    if operation.page_number > page_count:
                        raise ValueError("edit operation page_number cannot exceed source PDF page_count.")

        if self.action == FeatureType.compress_pdf:
            assert isinstance(self.input, PdfFilePayload)
            if not isinstance(self.payload, CompressPdfRequest):
                raise ValueError("compress_pdf requires CompressPdfRequest payload.")

        if self.action == FeatureType.lock_pdf:
            assert isinstance(self.input, PdfFilePayload)
            if not isinstance(self.payload, LockPdfRequest):
                raise ValueError("lock_pdf requires LockPdfRequest payload.")
            if self.input.metadata.encrypted or self.input.metadata.password_protected:
                raise ValueError("lock_pdf requires an unlocked source PDF.")

        if self.action == FeatureType.e_signature:
            if not isinstance(self.payload, ESignatureRequest):
                raise ValueError("e_signature requires ESignatureRequest payload.")
            input_documents = (
                [self.input]
                if isinstance(self.input, PdfFilePayload)
                else list(self.input.documents)
            )
            if len(input_documents) > MAX_ESIGN_DOCUMENTS:
                raise ValueError(
                    f"e_signature supports at most {MAX_ESIGN_DOCUMENTS} documents per envelope."
                )
            if len(input_documents) > 1 and len(self.payload.documents) != len(input_documents):
                raise ValueError(
                    "A multi-document e-signature request requires one ordered documents entry "
                    "for every uploaded PDF."
                )
            if len(input_documents) == 1 and len(self.payload.documents) > 1:
                raise ValueError("A single-PDF envelope cannot declare several documents.")

            document_ids = (
                [item.document_id for item in self.payload.documents]
                if self.payload.documents
                else ["document_1"]
            )
            page_counts = {
                document_id: document.metadata.page_count
                for document_id, document in zip(document_ids, input_documents)
            }
            signature_page_count = 0
            if self.payload.add_signature_page:
                signer_count = len(self.payload.recipients) + (
                    1 if self.payload.self_signer is not None else 0
                )
                signature_page_count = (
                    max(1, signer_count) + ESIGN_SIGNERS_PER_SIGNATURE_PAGE - 1
                ) // ESIGN_SIGNERS_PER_SIGNATURE_PAGE

            for field in self.payload.fields:
                document_id = field.document_id or document_ids[0]
                if document_id not in page_counts:
                    raise ValueError(
                        f"Unknown e-signature field document_id: {document_id}."
                    )
                if len(input_documents) > 1 and field.document_id is None:
                    raise ValueError(
                        "Every field in a multi-document envelope requires document_id."
                    )
                page_count = page_counts[document_id]
                if (
                    page_count is not None
                    and field.page_number > page_count + signature_page_count
                ):
                    raise ValueError(
                        "e-signature field page_number cannot exceed the effective PDF page_count."
                    )

        # text + word count required for text-based AI document actions
        if self.action in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
            assert isinstance(self.input, DocumentPayload)
            if not self.input.text:
                raise ValueError(f"{self.action.value} requires extracted document text.")
            if self.input.metadata.extracted_word_count is None:
                raise ValueError(f"{self.action.value} requires extracted_word_count.")
            if self.input.metadata.extracted_word_count < 1:
                raise ValueError("extracted_word_count must be >= 1 for text-based AI processing actions.")
            if self.input.metadata.extracted_word_count > MAX_WORD_COUNT:
                raise ValueError(
                    f"extracted_word_count must be <= {MAX_WORD_COUNT} for text-based AI processing actions."
                )

        # structure preservation policy enforcement
        transformed = {
            FeatureType.convert,
            FeatureType.summarize,
            FeatureType.grammar_correct,
            FeatureType.translate,
            FeatureType.transcribe,
            FeatureType.text_to_speech,
            FeatureType.vault,
            FeatureType.redact,
            FeatureType.data_mask,
            FeatureType.combine_pdf,
            FeatureType.split_pdf,
            FeatureType.edit_pdf,
            FeatureType.compress_pdf,
            FeatureType.lock_pdf,
            FeatureType.e_signature,
        }
        generated = {
            FeatureType.explain,
            FeatureType.structured_extract,
            FeatureType.compliance,
            FeatureType.generate_questions,
            FeatureType.generate_answers,
        }

        if self.action in transformed and self.policy.structure_preservation is not True:
            raise ValueError("structure_preservation must be True for transformed outputs (strict contract rule).")
        if self.action in generated and self.policy.structure_preservation is not False:
            raise ValueError("structure_preservation must be False for generated outputs (strict contract rule).")
        return self


# RESPONSE MODELS (V1)


class DeterminismMetadata(BaseModel):
    deterministic: Literal[True] = True
    contract_version: Literal["v1"] = "v1"
    algorithm_version: Optional[NonEmptyStr] = None


class HumanReviewRequirement(BaseModel):
    required: Literal[True] = True
    note: Literal["User or authorized reviewer verification is required before reliance or final export."] = (
        "User or authorized reviewer verification is required before reliance or final export."
    )


class InlineTextResult(BaseModel):
    output_format: Literal[InlineOutputFormat.txt] = InlineOutputFormat.txt
    content: NonEmptyStr
    meta: DeterminismMetadata


class BaseFileResult(BaseModel):
    filename: NonEmptyStr
    file_size_mb: float = Field(..., ge=0)
    storage_key: Optional[str] = None
    download_url: Optional[str] = None
    meta: DeterminismMetadata


class DocumentFileResult(BaseFileResult):
    output_format: DocumentFileOutputFormat


class ArchiveFileResult(BaseFileResult):
    output_format: Literal[DocumentFileOutputFormat.zip] = DocumentFileOutputFormat.zip

    @model_validator(mode="after")
    def validate_filename_extension(self):
        if not self.filename.lower().endswith(".zip"):
            raise ValueError("Archive filename must end with .zip.")
        return self


# VAULT + TEXT TO SPEECH RESPONSE MODELS


class TextToSpeechResult(BaseFileResult):
    output_format: SpeechAudioFormat
    voice_id: NonEmptyStr
    source_character_count: int = Field(..., ge=1)
    duration_seconds: Optional[float] = Field(default=None, gt=0)
    synthetic_voice: Literal[True] = True

    @model_validator(mode="after")
    def validate_filename_extension(self):
        expected_suffix = f".{self.output_format.value}"
        if not self.filename.lower().endswith(expected_suffix):
            raise ValueError(
                f"Text to Speech filename must end with {expected_suffix}."
            )
        return self


class VaultItemMetadata(BaseModel):
    item_id: NonEmptyStr
    filename: NonEmptyStr
    content_type: NonEmptyStr
    file_size_bytes: int = Field(..., ge=0)
    checksum_sha256: Optional[SHA256Hex] = None
    client_encrypted: bool = False
    created_at_iso: NonEmptyStr
    updated_at_iso: Optional[NonEmptyStr] = None


class VaultItemResult(BaseModel):
    operation: Literal[VaultOperation.store, VaultOperation.retrieve]
    item: VaultItemMetadata
    download_url: Optional[NonEmptyStr] = None
    meta: DeterminismMetadata


class VaultListResult(BaseModel):
    operation: Literal[VaultOperation.list] = VaultOperation.list
    items: List[VaultItemMetadata] = Field(default_factory=list)
    next_cursor: Optional[NonEmptyStr] = None
    meta: DeterminismMetadata


class VaultDeleteResult(BaseModel):
    operation: Literal[VaultOperation.delete] = VaultOperation.delete
    item_id: NonEmptyStr
    deleted: Literal[True] = True
    meta: DeterminismMetadata


# PDF TOOLS + E-SIGNATURE RESPONSE MODELS


class PdfJobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class PdfJobResult(BaseModel):
    job_id: NonEmptyStr
    status: PdfJobStatus
    message: Optional[NonEmptyStr] = None
    result: Optional[DocumentFileResult] = None
    meta: DeterminismMetadata


class PdfPreviewResult(BaseFileResult):
    output_format: Literal[DocumentFileOutputFormat.pdf] = DocumentFileOutputFormat.pdf
    page_count: Optional[int] = Field(default=None, ge=1)
    preview_stage: Optional[NonEmptyStr] = None


class CombinePdfResult(DocumentFileResult):
    output_format: Literal[DocumentFileOutputFormat.pdf] = DocumentFileOutputFormat.pdf
    source_file_count: int = Field(..., ge=2, le=MAX_COMBINE_PDF_FILES)
    source_filenames: List[NonEmptyStr] = Field(default_factory=list)
    combined_page_count: Optional[int] = Field(default=None, ge=1)


class SplitPdfResult(BaseModel):
    mode: PdfSplitMode
    output_files: List[DocumentFileResult] = Field(..., min_length=1)
    archive_file: Optional[ArchiveFileResult] = None
    meta: DeterminismMetadata

    @model_validator(mode="after")
    def validate_split_outputs_are_pdf_or_zip_archive(self):
        for item in self.output_files:
            if item.output_format != DocumentFileOutputFormat.pdf:
                raise ValueError("Split PDF output_files must be PDF document results.")
        return self


class EditPdfResult(DocumentFileResult):
    output_format: Literal[DocumentFileOutputFormat.pdf] = DocumentFileOutputFormat.pdf
    operations_requested: int = Field(..., ge=1)
    operations_applied: int = Field(..., ge=0)
    preview: Optional[PdfPreviewResult] = None
    source_checksum_sha256: SHA256Hex
    output_checksum_sha256: SHA256Hex
    page_count: int = Field(..., ge=1)

    @model_validator(mode="after")
    def validate_operations(self):
        if self.operations_applied != self.operations_requested:
            raise ValueError(
                "A successful edit_pdf result must apply every requested operation."
            )
        return self


class CompressPdfResult(DocumentFileResult):
    output_format: Literal[DocumentFileOutputFormat.pdf] = DocumentFileOutputFormat.pdf
    compression_level: PdfCompressionLevel
    original_file_size_mb: float = Field(..., ge=0)
    compressed_file_size_mb: float = Field(..., ge=0)
    estimated_output_file_size_mb: Optional[float] = Field(default=None, ge=0)
    compression_ratio: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_compression_ratio(self):
        if self.original_file_size_mb > 0:
            computed = self.compressed_file_size_mb / self.original_file_size_mb
            if self.compression_ratio is not None and abs(self.compression_ratio - computed) > 0.05:
                raise ValueError("compression_ratio must approximately equal compressed/original file size.")
        return self


class LockPdfResult(DocumentFileResult):
    output_format: Literal[DocumentFileOutputFormat.pdf] = DocumentFileOutputFormat.pdf
    encryption: Literal[PdfEncryptionAlgorithm.aes_256] = PdfEncryptionAlgorithm.aes_256
    password_protected: Literal[True] = True


class ESignatureEnvelopeStatus(str, Enum):
    draft = "draft"
    sent = "sent"
    viewed = "viewed"
    partially_signed = "partially_signed"
    completed = "completed"
    voided = "voided"
    expired = "expired"


class ESignatureRecipientStatus(str, Enum):
    pending = "pending"
    sent = "sent"
    viewed = "viewed"
    signed = "signed"
    declined = "declined"


class ESignatureRecipientResult(BaseModel):
    name: NonEmptyStr
    email: EmailLike
    role: ESignatureRecipientRole
    signing_order: int = Field(..., ge=1)
    status: ESignatureRecipientStatus


class ESignatureAuditEventType(str, Enum):
    envelope_created = "envelope_created"
    document_uploaded = "document_uploaded"
    field_added = "field_added"
    envelope_sent = "envelope_sent"
    email_sent = "email_sent"
    signer_viewed = "signer_viewed"
    signer_consented = "signer_consented"
    signer_signed = "signer_signed"
    preview_generated = "preview_generated"
    pades_sealed = "pades_sealed"
    bundle_created = "bundle_created"
    envelope_completed = "envelope_completed"
    envelope_voided = "envelope_voided"


class ESignatureAuditEvent(BaseModel):
    event_id: NonEmptyStr
    event_type: ESignatureAuditEventType
    actor_email: Optional[EmailLike] = None
    ip_address: Optional[NonEmptyStr] = None
    user_agent: Optional[NonEmptyStr] = None
    document_id: Optional[NonEmptyStr] = None
    document_sha256: Optional[SHA256Hex] = None
    created_at_iso: NonEmptyStr


class ESignatureStepPreview(BaseModel):
    """
    A PDF preview generated immediately after a signer completes their signing step.
    """
    signer_email: EmailLike
    signer_name: NonEmptyStr
    signing_order: int = Field(..., ge=1)
    document_id: NonEmptyStr = "document_1"
    preview_pdf: PdfPreviewResult
    created_at_iso: NonEmptyStr


class PAdESProfile(str, Enum):
    baseline_b = "B-B"
    baseline_t = "B-T"
    baseline_lt = "B-LT"
    baseline_lta = "B-LTA"


class PAdESSignatureInfo(BaseModel):
    """Cryptographic verification facts for one final PDF revision."""

    profile: PAdESProfile
    subfilter: Literal["ETSI.CAdES.detached"] = "ETSI.CAdES.detached"
    field_name: NonEmptyStr
    signer_subject: NonEmptyStr
    signer_issuer: NonEmptyStr
    certificate_serial_number: NonEmptyStr
    certificate_sha256: SHA256Hex
    signing_time_iso: NonEmptyStr
    timestamped: bool
    validation_info_embedded: bool = False
    document_timestamped: bool = False
    integrity_ok: bool
    signature_valid: bool
    certificate_trusted: bool

    @model_validator(mode="after")
    def validate_profile_evidence(self):
        if self.profile != PAdESProfile.baseline_b and not self.timestamped:
            raise ValueError("PAdES B-T/B-LT/B-LTA signatures require a trusted signature timestamp.")
        if self.profile in {PAdESProfile.baseline_lt, PAdESProfile.baseline_lta}:
            if not self.validation_info_embedded:
                raise ValueError("PAdES B-LT/B-LTA signatures require embedded validation information.")
        if self.profile == PAdESProfile.baseline_lta and not self.document_timestamped:
            raise ValueError("PAdES B-LTA signatures require a validated document timestamp.")
        return self


class ESignatureDocumentResult(BaseModel):
    document_id: NonEmptyStr
    filename: NonEmptyStr
    source_sha256: SHA256Hex
    final_sha256: Optional[SHA256Hex] = None
    signed_pdf: Optional[DocumentFileResult] = None
    pades_signature: Optional[PAdESSignatureInfo] = None

    @model_validator(mode="after")
    def validate_document_output(self):
        if self.signed_pdf and self.signed_pdf.output_format != DocumentFileOutputFormat.pdf:
            raise ValueError("An e-signature document signed_pdf must be a PDF.")
        return self


class ESignatureResult(BaseModel):
    envelope_id: NonEmptyStr
    workflow: ESignatureWorkflow
    status: ESignatureEnvelopeStatus
    recipients: List[ESignatureRecipientResult] = Field(default_factory=list)
    latest_preview: Optional[ESignatureStepPreview] = None
    previews: List[ESignatureStepPreview] = Field(default_factory=list)
    documents: List[ESignatureDocumentResult] = Field(
        default_factory=list,
        min_length=1,
        max_length=MAX_ESIGN_DOCUMENTS,
    )
    signed_bundle: Optional[ArchiveFileResult] = None
    # Backward-compatible alias for single-document clients. For an envelope
    # with several documents this is the first document in envelope order.
    signed_pdf: Optional[DocumentFileResult] = None
    audit_certificate: Optional[DocumentFileResult] = None
    audit_events: List[ESignatureAuditEvent] = Field(default_factory=list)
    meta: DeterminismMetadata

    @model_validator(mode="after")
    def validate_completed_envelope_outputs(self):
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("e-signature result document_id values must be unique.")
        if self.status == ESignatureEnvelopeStatus.completed:
            if self.audit_certificate is None:
                raise ValueError("completed e-signature envelope requires audit_certificate.")
            for document in self.documents:
                if document.signed_pdf is None or document.final_sha256 is None:
                    raise ValueError("Every completed envelope document requires a final signed PDF.")
                signature = document.pades_signature
                if signature is None:
                    raise ValueError("Every completed envelope document requires a PAdES signature.")
                if not (
                    signature.integrity_ok
                    and signature.signature_valid
                    and signature.certificate_trusted
                ):
                    raise ValueError("Every completed envelope PAdES signature must validate and be trusted.")
            if len(self.documents) > 1 and self.signed_bundle is None:
                raise ValueError("A completed multi-document envelope requires signed_bundle.")
        if self.signed_pdf and self.signed_pdf.output_format != DocumentFileOutputFormat.pdf:
            raise ValueError("signed_pdf must be a PDF file result.")
        if self.signed_bundle and self.signed_bundle.output_format != DocumentFileOutputFormat.zip:
            raise ValueError("signed_bundle must be a ZIP archive result.")
        if self.audit_certificate and self.audit_certificate.output_format != DocumentFileOutputFormat.pdf:
            raise ValueError("audit_certificate must be a PDF file result.")
        return self

class TranscriptionResult(InlineTextResult):
    pdf_artifact: DocumentFileResult

    @model_validator(mode="after")
    def validate_pdf_artifact(self):
        if self.output_format != InlineOutputFormat.txt:
            raise ValueError("Transcription inline output must remain txt.")

        if self.pdf_artifact.output_format != DocumentFileOutputFormat.pdf:
            raise ValueError("Transcription downloadable artifact must be pdf only.")

        return self


class StructuredExtractionFileResult(BaseFileResult):
    output_format: StructuredDataOutputFormat
    result_shape: StructuredExtractionResultShape
    selected_fields: List[NonEmptyStr] = Field(default_factory=list)


class ComplianceFileResult(BaseFileResult):
    output_format: ComplianceOutputFormat
    report_variant: ComplianceReportVariant


class QuestionScaleMetadata(BaseModel):
    classification: QuestionScale
    extracted_word_count: int = Field(..., ge=1, le=MAX_WORD_COUNT)


_TOP_LEVEL_NUMBERED_OUTPUT_RE = re.compile(r"^\s*(\d+)\.\s+\S")


def _top_level_numbered_output_items(content: str) -> list[int]:
    numbers: list[int] = []
    for line in content.splitlines():
        match = _TOP_LEVEL_NUMBERED_OUTPUT_RE.match(line)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


class QuestionGenerationInlineResult(InlineTextResult):
    scale: QuestionScaleMetadata

    @model_validator(mode="after")
    def enforce_question_scaling(self):
        rule = classify_word_count(self.scale.extracted_word_count)
        if self.scale.classification != rule.classification:
            raise ValueError("classification mismatch for extracted_word_count.")
        numbered = _top_level_numbered_output_items(self.content)
        n = len(numbered)
        if not (rule.min_questions <= n <= rule.max_questions):
            raise ValueError(
                f"Question count out of range for {rule.classification.value}: "
                f"expected {rule.min_questions}–{rule.max_questions}."
            )
        if numbered != list(range(1, n + 1)):
            raise ValueError("Questions must be sequentially numbered starting at 1.")
        return self


class QuestionGenerationFileResult(DocumentFileResult):
    scale: QuestionScaleMetadata
    generated_questions_text: NonEmptyStr

    @model_validator(mode="after")
    def enforce_question_scaling(self):
        rule = classify_word_count(self.scale.extracted_word_count)
        if self.scale.classification != rule.classification:
            raise ValueError("classification mismatch for extracted_word_count.")
        numbered = _top_level_numbered_output_items(
            self.generated_questions_text
        )
        question_count = len(numbered)
        if not (rule.min_questions <= question_count <= rule.max_questions):
            raise ValueError(
                f"Question count out of range for {rule.classification.value}: "
                f"expected {rule.min_questions}–{rule.max_questions}."
            )
        if numbered != list(range(1, question_count + 1)):
            raise ValueError("Questions must be sequentially numbered starting at 1.")
        return self


class AnswerGenerationInlineResult(InlineTextResult):
    expected_question_count: int = Field(..., ge=1)

    @model_validator(mode="after")
    def enforce_answer_alignment(self):
        numbered = _top_level_numbered_output_items(self.content)
        n = len(numbered)
        if n != self.expected_question_count:
            raise ValueError("Answer count must exactly match the number of questions.")
        if numbered != list(range(1, n + 1)):
            raise ValueError("Answers must be sequentially numbered starting at 1.")
        return self


class AnswerGenerationFileResult(DocumentFileResult):
    expected_question_count: int = Field(..., ge=1)


class EvidenceReference(BaseModel):
    source_document_index: int = Field(..., ge=0)
    page_number: Optional[int] = Field(default=None, ge=1)
    section_label: Optional[NonEmptyStr] = None
    locator_text: Optional[NonEmptyStr] = None
    excerpt: Optional[NonEmptyStr] = None


class ComplianceCheckStatus(str, Enum):
    # Compliance screening statuses deliberately avoid authoritative legal
    # pass/fail semantics. Evidence presence is not the same as legal approval.
    evidence_found = "evidence_found"
    risk_detected = "risk_detected"
    warning = "warning"
    evidence_missing = "evidence_missing"
    requires_review = "requires_review"


class ComplianceOverallStatus(str, Enum):
    # These labels describe a preliminary document screening, not a legal
    # certification or authoritative pass/fail decision.
    ready_for_final_review = "ready_for_final_review"
    changes_recommended = "changes_recommended"
    manual_review_needed = "manual_review_needed"


class RulePackVersion(BaseModel):
    sector_pack: ComplianceSectorPack
    version: NonEmptyStr
    checksum_sha256: SHA256Hex


class ComplianceSourceDocument(BaseModel):
    source_document_index: int = Field(..., ge=0)
    filename: NonEmptyStr
    input_format: DocumentInputFormat
    file_size_mb: float = Field(..., ge=0, le=MAX_FILE_SIZE_MB)
    checksum_sha256: SHA256Hex
    ocr_used: bool = False
    extracted_character_count: int = Field(..., ge=1)
    pages_with_text: int = Field(default=0, ge=0)
    page_count: Optional[int] = Field(default=None, ge=1)


class ComplianceRunMetadata(BaseModel):
    report_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=16,
            max_length=96,
            pattern=r"^[A-Za-z0-9_-]+$",
        ),
    ]
    generated_at_iso: NonEmptyStr
    algorithm_version: NonEmptyStr
    evaluation_mode: Literal["deterministic_evidence_screening"] = (
        "deterministic_evidence_screening"
    )
    source_documents: List[ComplianceSourceDocument] = Field(..., min_length=1)


class ComplianceRuleResult(BaseModel):
    rule_id: NonEmptyStr
    rule_version: NonEmptyStr
    title: NonEmptyStr
    status: ComplianceCheckStatus
    summary: NonEmptyStr
    sector_pack: Optional[ComplianceSectorPack] = None
    regulatory_domain: Optional[ComplianceRegulatoryDomain] = None
    plain_language_summary: Optional[NonEmptyStr] = None
    recommended_actions: List[NonEmptyStr] = Field(default_factory=list)
    matched_signals: List[NonEmptyStr] = Field(default_factory=list)
    missing_signals: List[NonEmptyStr] = Field(default_factory=list)
    evidence_references: List[EvidenceReference] = Field(default_factory=list)


class ComplianceCounts(BaseModel):
    evidence_found: int = Field(default=0, ge=0)
    risk_detected: int = Field(default=0, ge=0)
    warning: int = Field(default=0, ge=0)
    evidence_missing: int = Field(default=0, ge=0)
    requires_review: int = Field(default=0, ge=0)


class ComplianceMachineReadableReport(BaseModel):
    jurisdiction: ComplianceJurisdiction = ComplianceJurisdiction.nigeria
    sector_packs: List[ComplianceSectorPack] = Field(..., min_length=1)
    rule_pack_versions: List[RulePackVersion] = Field(default_factory=list)
    counts: ComplianceCounts
    rule_results: List[ComplianceRuleResult] = Field(default_factory=list)
    overall_status: ComplianceOverallStatus = ComplianceOverallStatus.manual_review_needed
    plain_language_summary: Optional[NonEmptyStr] = None
    recommended_next_steps: List[NonEmptyStr] = Field(default_factory=list)
    run_metadata: ComplianceRunMetadata
    quality_warnings: List[NonEmptyStr] = Field(default_factory=list)
    reliance_notice: Literal[
        "This report is an evidence-based document screening result, not legal advice or a legal certification. A qualified reviewer must confirm the applicable obligations and the final document."
    ] = (
        "This report is an evidence-based document screening result, not legal advice or a legal certification. A qualified reviewer must confirm the applicable obligations and the final document."
    )

    @model_validator(mode="after")
    def validate_counts(self):
        if not self.rule_results:
            raise ValueError("Compliance reports must contain at least one evaluated rule.")

        expected_packs = set(self.sector_packs)
        versioned_packs = {item.sector_pack for item in self.rule_pack_versions}
        if expected_packs != versioned_packs:
            raise ValueError(
                "rule_pack_versions must identify every selected sector pack exactly once."
            )
        if len(versioned_packs) != len(self.rule_pack_versions):
            raise ValueError("rule_pack_versions must not contain duplicate sector packs.")

        identities = [
            (item.sector_pack, item.rule_id, item.rule_version)
            for item in self.rule_results
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("Compliance rule results must not contain duplicate identities.")

        source_indexes = [
            item.source_document_index for item in self.run_metadata.source_documents
        ]
        if source_indexes != list(range(len(source_indexes))):
            raise ValueError(
                "run_metadata.source_documents must use contiguous zero-based indexes."
            )

        source_documents = {
            item.source_document_index: item
            for item in self.run_metadata.source_documents
        }
        for rule_result in self.rule_results:
            for evidence in rule_result.evidence_references:
                source_document = source_documents.get(evidence.source_document_index)
                if source_document is None:
                    raise ValueError(
                        "Evidence references must identify a report source document."
                    )
                if (
                    evidence.page_number is not None
                    and source_document.page_count is not None
                    and evidence.page_number > source_document.page_count
                ):
                    raise ValueError(
                        "Evidence page_number is outside the source document page range."
                    )

        actual = {
            ComplianceCheckStatus.evidence_found: 0,
            ComplianceCheckStatus.risk_detected: 0,
            ComplianceCheckStatus.warning: 0,
            ComplianceCheckStatus.evidence_missing: 0,
            ComplianceCheckStatus.requires_review: 0,
        }
        for item in self.rule_results:
            actual[item.status] += 1

        expected = {
            ComplianceCheckStatus.evidence_found: self.counts.evidence_found,
            ComplianceCheckStatus.risk_detected: self.counts.risk_detected,
            ComplianceCheckStatus.warning: self.counts.warning,
            ComplianceCheckStatus.evidence_missing: self.counts.evidence_missing,
            ComplianceCheckStatus.requires_review: self.counts.requires_review,
        }
        if actual != expected:
            raise ValueError("ComplianceCounts must exactly match the statuses present in rule_results.")
        return self


AnalyzerResult = Union[
    TranscriptionResult,
    TextToSpeechResult,
    VaultItemResult,
    VaultListResult,
    VaultDeleteResult,
    InlineTextResult,
    DocumentFileResult,
    StructuredExtractionFileResult,
    ComplianceFileResult,
    QuestionGenerationInlineResult,
    QuestionGenerationFileResult,
    AnswerGenerationInlineResult,
    AnswerGenerationFileResult,
    PdfJobResult,
    CombinePdfResult,
    SplitPdfResult,
    EditPdfResult,
    CompressPdfResult,
    LockPdfResult,
    ESignatureResult,
]


class AnalyzerResponse(BaseModel):
    action: FeatureType
    input_format: Union[
        DocumentInputFormat,
        Literal["audio"],
        Literal["video"],
        Literal["document_set"],
        Literal["pdf_file"],
        Literal["pdf_file_set"],
        Literal["vault_file"],
        Literal["vault_item_reference"],
        Literal["vault_query"],
    ]
    policy: OutputPolicy

    # Backend-only processing language. Frontend UI language should stay synchronized with this value.
    system_language: SystemLanguage = SystemLanguage.english

    # Detected input language (optional echo).
    # - For non-translate features: en*/fr* when provided.
    # - For translate: any supported source tag when provided.
    detected_language: Optional[BCP47Like] = None

    # Explicit output language marker.
    # - For non-translate features: en*/fr* when provided and should match detected_language.
    # - For translate: any supported target tag when provided.
    output_language: Optional[BCP47Like] = None

    result: AnalyzerResult
    human_review: Optional[HumanReviewRequirement] = None

    @model_validator(mode="after")
    def validate_action_result_mapping_and_output_rules(self):
        # 1) Action-result family constraints
        if self.action == FeatureType.convert:
            if not isinstance(self.result, DocumentFileResult):
                raise ValueError("convert must return a document file result.")
        elif self.action == FeatureType.combine_pdf:
            if not isinstance(self.result, CombinePdfResult):
                raise ValueError("combine_pdf must return CombinePdfResult.")
        elif self.action == FeatureType.split_pdf:
            if not isinstance(self.result, SplitPdfResult):
                raise ValueError("split_pdf must return SplitPdfResult.")
        elif self.action == FeatureType.edit_pdf:
            if not isinstance(self.result, EditPdfResult):
                raise ValueError("edit_pdf must return EditPdfResult.")
        elif self.action == FeatureType.compress_pdf:
            if not isinstance(self.result, (CompressPdfResult, PdfJobResult)):
                raise ValueError("compress_pdf must return CompressPdfResult or PdfJobResult for async jobs.")
        elif self.action == FeatureType.lock_pdf:
            if not isinstance(self.result, LockPdfResult):
                raise ValueError("lock_pdf must return LockPdfResult.")
        elif self.action == FeatureType.e_signature:
            if not isinstance(self.result, ESignatureResult):
                raise ValueError("e_signature must return ESignatureResult.")
        elif self.action == FeatureType.transcribe:
            if not isinstance(self.result, TranscriptionResult):
                raise ValueError("transcribe must return inline txt plus a downloadable pdf transcript.")
        elif self.action == FeatureType.text_to_speech:
            if not isinstance(self.result, TextToSpeechResult):
                raise ValueError("text_to_speech must return TextToSpeechResult.")
        elif self.action == FeatureType.vault:
            if not isinstance(self.result, (VaultItemResult, VaultListResult, VaultDeleteResult)):
                raise ValueError("vault must return a Vault result model.")
        elif self.action in {
            FeatureType.summarize,
            FeatureType.grammar_correct,
            FeatureType.translate,
            FeatureType.explain,
        }:
            if not isinstance(self.result, (InlineTextResult, DocumentFileResult)):
                raise ValueError(f"{self.action.value} must return inline txt or a document file result.")
        elif self.action in {FeatureType.redact, FeatureType.data_mask}:
            if not isinstance(self.result, DocumentFileResult):
                raise ValueError(f"{self.action.value} must return a document file result.")
        elif self.action == FeatureType.structured_extract:
            if not isinstance(self.result, StructuredExtractionFileResult):
                raise ValueError("structured_extract must return a structured-data file result.")
        elif self.action == FeatureType.compliance:
            if not isinstance(self.result, ComplianceFileResult):
                raise ValueError("compliance must return a compliance report file result.")
        elif self.action == FeatureType.generate_questions:
            if not isinstance(self.result, (QuestionGenerationInlineResult, QuestionGenerationFileResult)):
                raise ValueError("generate_questions must return question-generation result.")
        elif self.action == FeatureType.generate_answers:
            if not isinstance(self.result, (AnswerGenerationInlineResult, AnswerGenerationFileResult)):
                raise ValueError("generate_answers must return answer-generation result.")

        # 2) Output-extension rules
        # - text AI document actions: txt input -> inline txt; pdf/docx input -> same extension file
        # - redaction/data masking: pdf/docx/jpg/jpeg/png input -> same extension file
        # - PDF tools and e-signature always operate on PDF files/results
        if self.action in _PDF_DOCUMENT_ACTIONS:
            if self.action == FeatureType.combine_pdf:
                if self.input_format != "pdf_file_set":
                    raise ValueError("combine_pdf response input_format must be 'pdf_file_set'.")
            elif self.action == FeatureType.e_signature:
                if self.input_format not in (
                    DocumentInputFormat.pdf,
                    "pdf_file",
                    "pdf_file_set",
                ):
                    raise ValueError(
                        "e_signature response input_format must be pdf, 'pdf_file', or 'pdf_file_set'."
                    )
            else:
                if self.input_format not in (DocumentInputFormat.pdf, "pdf_file"):
                    raise ValueError(f"{self.action.value} response input_format must be pdf or 'pdf_file'.")

        if self.action in TEXT_AI_DOC_ACTIONS_REQUIRING_TEXT_AND_WORDCOUNT:
            if isinstance(self.input_format, DocumentInputFormat):
                if self.input_format == DocumentInputFormat.txt:
                    if not isinstance(self.result, InlineTextResult):
                        raise ValueError("For txt input, output must be inline txt (strict contract rule).")
                    if self.result.output_format != InlineOutputFormat.txt:
                        raise ValueError("Inline output must be txt.")
                elif self.input_format in (DocumentInputFormat.pdf, DocumentInputFormat.docx):
                    if not isinstance(self.result, DocumentFileResult):
                        raise ValueError(
                            "For pdf/docx input, output must be a downloadable file (strict contract rule)."
                        )
                    expected = (
                        DocumentFileOutputFormat.pdf
                        if self.input_format == DocumentInputFormat.pdf
                        else DocumentFileOutputFormat.docx
                    )
                    if self.result.output_format != expected:
                        raise ValueError("Output file extension must match input extension (strict contract rule).")
                else:
                    raise ValueError("Text AI document actions only support input formats: pdf, docx, txt.")
            else:
                raise ValueError("Text AI document actions require a document input_format.")

        if self.action == FeatureType.text_to_speech:
            if self.input_format not in {
                DocumentInputFormat.pdf,
                DocumentInputFormat.docx,
                DocumentInputFormat.txt,
            }:
                raise ValueError("text_to_speech requires pdf, docx, or txt input_format.")
            if not isinstance(self.result, TextToSpeechResult):
                raise ValueError("text_to_speech output must be a TextToSpeechResult.")

        if self.action == FeatureType.vault:
            expected_input_format = {
                VaultOperation.store: "vault_file",
                VaultOperation.retrieve: "vault_item_reference",
                VaultOperation.list: "vault_query",
                VaultOperation.delete: "vault_item_reference",
            }[self.result.operation]
            if self.input_format != expected_input_format:
                raise ValueError(
                    f"vault {self.result.operation.value} response input_format must be "
                    f"'{expected_input_format}'."
                )

        if self.action in {FeatureType.redact, FeatureType.data_mask}:
            if not isinstance(self.input_format, DocumentInputFormat):
                raise ValueError(f"{self.action.value} requires a document input_format.")
            if self.input_format == DocumentInputFormat.txt:
                raise ValueError(f"{self.action.value} does not support txt input.")
            if not isinstance(self.result, DocumentFileResult):
                raise ValueError(f"{self.action.value} output must be a downloadable file.")
            expected_map = {
                DocumentInputFormat.pdf: DocumentFileOutputFormat.pdf,
                DocumentInputFormat.docx: DocumentFileOutputFormat.docx,
                DocumentInputFormat.jpg: DocumentFileOutputFormat.jpg,
                DocumentInputFormat.jpeg: DocumentFileOutputFormat.jpeg,
                DocumentInputFormat.png: DocumentFileOutputFormat.png,
            }
            expected = expected_map[self.input_format]
            if self.result.output_format != expected:
                raise ValueError("Output file extension must match input extension (strict contract rule).")

        if self.action == FeatureType.structured_extract:
            if not isinstance(self.result, StructuredExtractionFileResult):
                raise ValueError("structured_extract output must be a structured-data file result.")

        if self.action == FeatureType.compliance:
            if not isinstance(self.result, ComplianceFileResult):
                raise ValueError("compliance output must be a compliance file result.")
            if self.result.report_variant == ComplianceReportVariant.machine_readable_report:
                if self.result.output_format != ComplianceOutputFormat.json:
                    raise ValueError("machine_readable_report must use json output.")
            elif self.result.report_variant == ComplianceReportVariant.human_readable_report:
                if self.result.output_format != ComplianceOutputFormat.pdf:
                    raise ValueError("human_readable_report must use pdf output.")
            elif self.result.output_format not in {
                ComplianceOutputFormat.pdf,
                ComplianceOutputFormat.zip,
            }:
                raise ValueError(
                    "annotated_source_output must use pdf for one source or zip for a document set."
                )

        # 3) Transcription output rule: inline txt + downloadable pdf
        if self.action == FeatureType.transcribe:
            if not isinstance(self.result, TranscriptionResult):
                raise ValueError("transcribe output must include inline txt and a pdf artifact.")
            if self.result.output_format != InlineOutputFormat.txt:
                raise ValueError("transcribe inline output must be txt.")
            if self.result.pdf_artifact.output_format != DocumentFileOutputFormat.pdf:
                raise ValueError("transcribe downloadable output must be pdf only.")
        # 4) Language boundary validation for non-translate features (when language fields are provided)
        if self.action in EN_FR_ONLY_NON_TRANSLATE_ACTIONS or self.action == FeatureType.transcribe:
            if self.detected_language is not None and not _is_en_or_fr_tag(self.detected_language):
                raise ValueError("Non-translate responses must have detected_language en*/fr* when provided.")
            if self.output_language is not None and not _is_en_or_fr_tag(self.output_language):
                raise ValueError("Non-translate responses must have output_language en*/fr* when provided.")
            if self.detected_language and self.output_language:
                d = self.detected_language.lower().replace("_", "-")
                o = self.output_language.lower().replace("_", "-")
                if (d.startswith("en") and not o.startswith("en")) or (d.startswith("fr") and not o.startswith("fr")):
                    raise ValueError(
                        "For non-translate features, output_language must match detected_language "
                        "(keep same language)."
                    )

        # 5) Optional consistency check between selected system_language and response language
        if self.action in EN_FR_ONLY_NON_TRANSLATE_ACTIONS or self.action == FeatureType.transcribe:
            forced_tag = _system_language_to_tag(self.system_language)
            if self.output_language is not None:
                o = self.output_language.lower().replace("_", "-")
                if forced_tag == "en" and not (o == "en" or o.startswith("en-")):
                    raise ValueError("system_language='english' but output_language is not en*.")
                if forced_tag == "fr" and not (o == "fr" or o.startswith("fr-")):
                    raise ValueError("system_language='french' but output_language is not fr*.")

        # 6) Human review enforcement
        if self.action in {
            FeatureType.redact,
            FeatureType.data_mask,
            FeatureType.structured_extract,
            FeatureType.compliance,
        }:
            if self.human_review is None or self.human_review.required is not True:
                raise ValueError(
                    f"{self.action.value} responses must explicitly declare human review as required."
                )

        return self
