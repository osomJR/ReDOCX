"use client";

import { useLanguage } from "@/components/language_provider";
import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  FileType,
  Database,
  Download,
  SlidersHorizontal,
  TableProperties,
  FileJson,
  X,
} from "lucide-react";
import {
  commonTranslations,
  structuredExtractionPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import {
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
} from "@/lib/api_client";
import {
  FILE_SECURITY_POLICY,
  partitionDuplicateBrowserUploads,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 10;
const MAX_STRUCTURED_EXTRACTION_FILES = 10;
const STRUCTURED_EXTRACTION_ENDPOINT = "/api/analyzer/structured-extraction";
const DEFAULT_OUTPUT_FORMAT = "xlsx";
const DEFAULT_RESULT_SHAPE = "row_based_records";
const AUTO_DOCUMENT_CLASS_VALUE = "auto";

const OUTPUT_FORMATS = ["json", "csv", "xlsx"];

const RESULT_SHAPES = [
  "machine_readable",
  "key_value_fields",
  "tables",
  "row_based_records",
];

const DOCUMENT_CLASSES = [
  "form",
  "memo",
  "invoice",
  "receipt",
  "bank_statement",
  "kyc_document",
  "id_document",
  "contract",
  "legal_record",
  "medical_record",
  "procurement_document",
  "technical_report",
  "incident_report",
  "insurance_document",
  "hr_record",
  "onboarding_document",
  "ticket",
];

const SIMPLE_DOCUMENT_TYPE_OPTIONS = [
  AUTO_DOCUMENT_CLASS_VALUE,
  "invoice",
  "receipt",
  "bank_statement",
  "kyc_document",
  "id_document",
  "contract",
  "form",
  "ticket",
];

const DEFAULT_STRUCTURED_EXTRACTION_COPY = {
  documentTypeLabel: "Document type",
  autoDetectDocumentType: "Auto-detect document type",
  autoDetectDocumentTypeHelp:
    "Recommended. ReDOCX will inspect the file and use the best matching extraction strategy.",
  advancedOptions: "Advanced options",
  advancedOptionsHelp:
    "Use these only when you need a specific output format, result shape, document class, or exact fields.",
  simpleFlowHelp: "Upload a document, let ReDOCX detect the type, then download an Excel-ready extraction.",
  fullTechnicalJson: "Full technical JSON",
  simpleFields: "Simple fields",
  tablesOnly: "Tables only",
  spreadsheetRows: "Spreadsheet rows",
  outputFormatLabels: {
    json: "Developer JSON",
    csv: "CSV spreadsheet",
    xlsx: "Excel workbook",
  },
  previewGeneratedTitle: "Generated preview",
  previewGeneratedBody: "Review the extracted data before downloading the file.",
  viewStructuredJson: "View structured JSON",
  previewShortened: "Preview shortened. Download the full file to see all rows.",
  selectedFieldStatusTitle: "Selected field status",
  selectedFieldStatusHelp:
    "Requested fields are marked as found, not found, or low confidence with evidence when available.",
  fieldStatusFound: "Found",
  fieldStatusNotFound: "Not found",
  fieldStatusLowConfidence: "Low confidence",
  fieldStatusEvidence: "Evidence",
  fieldStatusNoEvidence: "No evidence excerpt available",
  fieldStatusValue: "Value",
};

const FRIENDLY_RESULT_SHAPE_LABELS = {
  machine_readable: "Full technical JSON",
  key_value_fields: "Simple fields",
  tables: "Tables only",
  row_based_records: "Spreadsheet rows",
};

const SUGGESTED_FIELDS_BY_CLASS = {
  form: ["name", "date", "email", "phone_number", "address"],
  memo: ["to", "from", "date", "subject"],
  invoice: [
    "invoice_number",
    "invoice_date",
    "due_date",
    "subtotal",
    "tax",
    "total",
  ],
  receipt: [
    "receipt_number",
    "transaction_date",
    "merchant",
    "subtotal",
    "tax",
    "total",
  ],
  bank_statement: [
    "account_name",
    "account_number",
    "statement_period",
    "opening_balance",
    "closing_balance",
  ],
  kyc_document: [
    "full_name",
    "date_of_birth",
    "national_id",
    "phone_number",
    "email_address",
    "address",
  ],
  id_document: [
    "full_name",
    "date_of_birth",
    "national_id",
    "phone_number",
    "email_address",
    "address",
  ],
  contract: [
    "effective_date",
    "termination_date",
    "governing_law",
    "party_a",
    "party_b",
  ],
  legal_record: [
    "effective_date",
    "termination_date",
    "governing_law",
    "party_a",
    "party_b",
  ],
  medical_record: [
    "patient_name",
    "patient_id",
    "date_of_birth",
    "diagnosis",
    "provider",
  ],
  procurement_document: [
    "purchase_order_number",
    "vendor",
    "delivery_date",
    "total",
  ],
  technical_report: ["report_title", "report_date", "author", "summary"],
  incident_report: [
    "report_title",
    "report_date",
    "author",
    "summary",
    "incident_date",
    "incident_location",
    "severity",
  ],
  insurance_document: [
    "policy_number",
    "insured_name",
    "premium",
    "coverage_period",
    "claim_number",
  ],
  hr_record: [
    "employee_name",
    "employee_id",
    "department",
    "job_title",
    "start_date",
  ],
  onboarding_document: [
    "employee_name",
    "employee_id",
    "department",
    "job_title",
    "start_date",
  ],
  ticket: ["ticket_id", "status", "priority", "assignee", "created_date"],
};

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return filename || "structured-extraction";
  return filename.slice(0, lastDot) || "structured-extraction";
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function replaceVars(template, vars = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
}

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function uniqueStrings(values = []) {
  return [...new Set(values.map((item) => item.trim()).filter(Boolean))];
}

function parseSelectedFields(value = "") {
  return uniqueStrings(value.split(/[\n,]/g));
}

function getInputTypeLabel(ext, t) {
  if (ext === ".pdf") return t.pdfDocument;
  if (ext === ".docx") return t.wordDocument;
  if (ext === ".jpg") return t.jpgImage;
  if (ext === ".jpeg") return t.jpegImage;
  if (ext === ".png") return t.pngImage;
  return t.unknownFile;
}

function buildFallbackFilename(filename = "", outputFormat = "json") {
  return `${getFileStem(filename)}_structured_extraction.${outputFormat}`;
}

function buildDocumentSetFallbackFilename(files = [], outputFormat = "json") {
  if (files.length === 1) {
    return buildFallbackFilename(files[0]?.name, outputFormat);
  }

  return `structured-extraction-document-set.${outputFormat}`;
}

function extractResponseMessage(responseData, fallbackMessage = "") {
  const detail = responseData?.detail;

  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail?.message === "string" && detail.message.trim()) {
    return detail.message.trim();
  }
  if (typeof detail?.error === "string" && detail.error.trim()) {
    return detail.error.trim();
  }

  return (
    pickFirstString([
      responseData?.message,
      responseData?.error,
      responseData?.result?.message,
      responseData?.data?.message,
      responseData?.analyzer_response?.result?.message,
    ]) || fallbackMessage
  );
}

function extractDownloadInfo(responseData, fallbackFilename = "") {
  const artifact =
    responseData?.artifact || responseData?.output_artifact || {};
  const result =
    responseData?.analyzer_response?.result ||
    responseData?.result ||
    responseData?.data ||
    {};

  const storageKey = pickFirstString([
    artifact?.storage_key,
    artifact?.storageKey,
    result?.storage_key,
    result?.storageKey,
    responseData?.storage_key,
    responseData?.storageKey,
  ]);

  const downloadUrl =
    normalizeAnalyzerArtifactUrl(
      pickFirstString([
        artifact?.download_url,
        artifact?.downloadUrl,
        result?.download_url,
        result?.downloadUrl,
        responseData?.download_url,
        responseData?.downloadUrl,
        responseData?.url,
      ]),
    ) || buildAnalyzerArtifactUrl(storageKey);

  const filename = pickFirstString([
    artifact?.original_artifact_name,
    artifact?.artifact_name,
    artifact?.artifactName,
    result?.filename,
    result?.name,
    responseData?.filename,
    fallbackFilename,
  ]);

  const outputFormat = pickFirstString([
    result?.output_format,
    result?.outputFormat,
    responseData?.output_format,
    responseData?.outputFormat,
  ]);

  const resultShape = pickFirstString([
    result?.result_shape,
    result?.resultShape,
    responseData?.result_shape,
    responseData?.resultShape,
  ]);

  const selectedFields = Array.isArray(result?.selected_fields)
    ? result.selected_fields
    : Array.isArray(result?.selectedFields)
      ? result.selectedFields
      : [];

  return {
    storageKey,
    downloadUrl,
    filename,
    outputFormat,
    resultShape,
    selectedFields,
    fileSizeMb: result?.file_size_mb ?? result?.fileSizeMb ?? null,
    contentType: pickFirstString([
      artifact?.content_type,
      artifact?.contentType,
      result?.content_type,
      result?.contentType,
    ]),
  };
}

function extractStructuredPreview(responseData) {
  const previewPayload =
    responseData?.preview_payload ||
    responseData?.previewPayload ||
    responseData?.preview ||
    null;

  const previewRows = Array.isArray(responseData?.preview_rows)
    ? responseData.preview_rows
    : Array.isArray(responseData?.previewRows)
      ? responseData.previewRows
      : [];

  return {
    previewPayload,
    previewRows,
    previewTruncated: Boolean(
      responseData?.preview_truncated || responseData?.previewTruncated,
    ),
  };
}


function normalizeFieldName(value = "") {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function getStructuredExtractionCopy(t = {}) {
  const ux = t.structuredExtractionUx || {};

  return {
    ...DEFAULT_STRUCTURED_EXTRACTION_COPY,
    ...ux,
    outputFormatLabels: {
      ...DEFAULT_STRUCTURED_EXTRACTION_COPY.outputFormatLabels,
      ...(ux.outputFormatLabels || {}),
    },
    resultShapeLabels: {
      ...FRIENDLY_RESULT_SHAPE_LABELS,
      ...(ux.resultShapeLabels || {}),
    },
  };
}

function extractEvidenceForField(evidenceItems = [], normalizedFieldName = "") {
  if (!Array.isArray(evidenceItems)) return null;

  return (
    evidenceItems.find((item) => {
      const fieldName = item?.field_name || item?.fieldName || item?.name;
      return normalizeFieldName(fieldName) === normalizedFieldName;
    }) || null
  );
}

function addFieldCandidate(candidates, rawName, rawValue, options = {}) {
  const normalized = normalizeFieldName(rawName);
  if (!normalized) return;

  const value = rawValue == null ? "" : String(rawValue).trim();
  const confidenceValue = Number(options.confidence);
  const confidence = Number.isFinite(confidenceValue) ? confidenceValue : value ? 0.85 : 0;

  candidates.push({
    normalized,
    name: String(rawName || normalized),
    value,
    confidence,
    evidence: options.evidence || null,
  });
}

function collectFieldCandidatesFromPayload(previewPayload) {
  const candidates = [];

  if (!previewPayload || typeof previewPayload !== "object") {
    return candidates;
  }

  const documents = Array.isArray(previewPayload.documents)
    ? previewPayload.documents
    : [];

  for (const documentItem of documents) {
    const fields = documentItem?.fields;
    const documentEvidence = Array.isArray(documentItem?.evidence)
      ? documentItem.evidence
      : [];

    if (Array.isArray(fields)) {
      for (const field of fields) {
        const name = field?.name || field?.field_name || field?.fieldName;
        const evidence = Array.isArray(field?.evidence) && field.evidence.length
          ? field.evidence[0]
          : extractEvidenceForField(documentEvidence, normalizeFieldName(name));
        addFieldCandidate(candidates, name, field?.value, {
          confidence: field?.confidence,
          evidence,
        });
      }
    } else if (fields && typeof fields === "object") {
      for (const [name, value] of Object.entries(fields)) {
        addFieldCandidate(candidates, name, value, {
          evidence: extractEvidenceForField(documentEvidence, normalizeFieldName(name)),
        });
      }
    }
  }

  const rows = Array.isArray(previewPayload.rows) ? previewPayload.rows : [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    for (const [name, value] of Object.entries(row)) {
      if (["source_document_index", "filename", "record_type", "table_index"].includes(name)) {
        continue;
      }
      addFieldCandidate(candidates, name, value, { confidence: value ? 0.75 : 0 });
    }
  }

  return candidates;
}

function buildSelectedFieldStatusRows(previewPayload, selectedFields = []) {
  const requestedFields = uniqueStrings(selectedFields).map((field) => ({
    raw: field,
    normalized: normalizeFieldName(field),
  })).filter((field) => field.normalized);

  if (!requestedFields.length) {
    return [];
  }

  const candidates = collectFieldCandidatesFromPayload(previewPayload);

  return requestedFields.map(({ raw, normalized }) => {
    const matchingCandidates = candidates.filter((candidate) => candidate.normalized === normalized);
    const bestCandidate = matchingCandidates.sort((left, right) => {
      const leftHasValue = left.value ? 1 : 0;
      const rightHasValue = right.value ? 1 : 0;
      if (leftHasValue !== rightHasValue) return rightHasValue - leftHasValue;
      return right.confidence - left.confidence;
    })[0];

    const found = Boolean(bestCandidate?.value);
    const confidence = found ? bestCandidate.confidence : 0;

    return {
      field: raw,
      value: bestCandidate?.value || "",
      found,
      lowConfidence: found && confidence < 0.6,
      confidence,
      evidence: bestCandidate?.evidence || null,
    };
  });
}
function SearchableMultiSelect({
  title,
  helpText,
  emptyText,
  examplesText,
  items,
  selectedValues,
  onToggle,
  getLabel,
  searchPlaceholder,
  disabled = false,
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const filteredItems = items.filter((item) =>
    getLabel(item).toLowerCase().includes(normalizedQuery),
  );

  return (
    <div>
      <p className="text-sm font-medium text-[var(--app-accent-text)]">{title}</p>
      {helpText && (
        <p className="mt-1 text-xs leading-5 text-[var(--app-accent-text)]">{helpText}</p>
      )}
      {emptyText && (
        <p className="mt-1 text-xs leading-5 app-text-soft">{emptyText}</p>
      )}
      {examplesText && (
        <p className="mt-1 text-xs leading-5 app-text-soft">{examplesText}</p>
      )}

      <div className="mt-3 flex min-h-10 flex-wrap gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2">
        {selectedValues.map((value) => (
          <button
            key={value}
            type="button"
            disabled={disabled}
            onClick={() => !disabled && onToggle(value)}
            className={`rounded-full border px-3 py-1 text-xs transition ${
              disabled
                ? "cursor-not-allowed border-[var(--app-border)] bg-[var(--app-surface)] app-text-soft"
                : "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-accent-text)] hover:bg-cyan-400/20"
            }`}
          >
            {getLabel(value)} ×
          </button>
        ))}
      </div>

      <input
        type="search"
        value={query}
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={searchPlaceholder}
        className={`mt-3 w-full rounded-2xl border border-[var(--app-border)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] ${
          disabled
            ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
            : "bg-[var(--app-surface)] focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
        }`}
      />

      <div className="mt-3 grid max-h-24 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
        {filteredItems.map((item) => {
          const checked = selectedValues.includes(item);

          return (
            <button
              key={item}
              type="button"
              disabled={disabled}
              onClick={() => !disabled && onToggle(item)}
              className={`flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left text-xs transition ${
                disabled
                  ? "cursor-not-allowed border-[var(--app-border)] bg-[var(--app-surface)] app-text-soft opacity-80"
                  : checked
                    ? "border-[var(--app-accent-border)] bg-cyan-300/15 text-[var(--app-accent-text)]"
                    : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:bg-[var(--app-surface-strong)]"
              }`}
            >
              <span>{getLabel(item)}</span>
              {checked && <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function StructuredExtractionPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();

  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    structuredExtractionPageTranslations[language] ||
    structuredExtractionPageTranslations.en;
  const ux = useMemo(() => getStructuredExtractionCopy(t), [t]);

  const [selectedFiles, setSelectedFiles] = useState([]);
  const [selectedDocumentType, setSelectedDocumentType] = useState(
    AUTO_DOCUMENT_CLASS_VALUE,
  );
  const [documentClasses, setDocumentClasses] = useState([]);
  const [selectedFieldsText, setSelectedFieldsText] = useState("");
  const [outputFormat, setOutputFormat] = useState(DEFAULT_OUTPUT_FORMAT);
  const [resultShape, setResultShape] = useState(DEFAULT_RESULT_SHAPE);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [resultSummary, setResultSummary] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);
  const [previewPayload, setPreviewPayload] = useState(null);
  const [previewRows, setPreviewRows] = useState([]);
  const [previewTruncated, setPreviewTruncated] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const inputExtensions = useMemo(() => {
    return uniqueStrings(
      selectedFiles.map((file) => getFileExtension(file.name)),
    ).sort();
  }, [selectedFiles]);

  const inputExtensionSummary = inputExtensions.join(", ");

  const selectedFields = useMemo(
    () => parseSelectedFields(selectedFieldsText),
    [selectedFieldsText],
  );

  const activeSuggestedFields = useMemo(() => {
    const sourceClasses = documentClasses.length
      ? documentClasses
      : [
          "invoice",
          "receipt",
          "bank_statement",
          "kyc_document",
          "contract",
          "form",
        ];
    const fields = sourceClasses.flatMap(
      (documentClass) => SUGGESTED_FIELDS_BY_CLASS[documentClass] || [],
    );
    return uniqueStrings(fields).slice(0, 16);
  }, [documentClasses]);

  const resultShapeDescription = t.resultShapeDescriptions?.[resultShape] || "";
  const selectedFieldStatusRows = useMemo(
    () => buildSelectedFieldStatusRows(previewPayload, selectedFields),
    [previewPayload, selectedFields],
  );

  const isValidFileSelection = useMemo(() => {
    if (
      selectedFiles.length < 1 ||
      selectedFiles.length > MAX_STRUCTURED_EXTRACTION_FILES
    ) {
      return false;
    }

    return selectedFiles.every((file) => {
      const ext = getFileExtension(file.name);
      return (
        ACCEPTED_EXTENSIONS.includes(ext) &&
        file.size <= MAX_FILE_SIZE_MB * 1024 * 1024
      );
    });
  }, [selectedFiles]);

  const canSubmit =
    !isSubmitting &&
    isValidFileSelection &&
    OUTPUT_FORMATS.includes(outputFormat) &&
    RESULT_SHAPES.includes(resultShape);
  const isProcessing = isSubmitting;

  function resetResultState() {
    setResultSummary("");
    setDownloadInfo(null);
    setPreviewPayload(null);
    setPreviewRows([]);
    setPreviewTruncated(false);
  }

  async function handlePickedFiles(files) {
    const incomingFiles = Array.from(files || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const submittedFiles = [...selectedFiles, ...incomingFiles];
    const { acceptedFiles: fileList, duplicates } =
      await partitionDuplicateBrowserUploads(submittedFiles);

    if (fileList.length > MAX_STRUCTURED_EXTRACTION_FILES) {
      setError(
        `Structured extraction accepts a maximum of ${MAX_STRUCTURED_EXTRACTION_FILES} files.`,
      );
      return;
    }

    for (const file of fileList) {
      const securityError = await validateBrowserUpload(
        file,
        FILE_SECURITY_POLICY.documentWithImages,
      );
      if (securityError) {
        setError(`${file.name}: ${securityError}`);
        return;
      }

      const ext = getFileExtension(file.name);

      if (!ACCEPTED_EXTENSIONS.includes(ext)) {
        setError(
          replaceVars(t.unsupportedFileType, {
            ext: ext || "unknown",
          }),
        );
        return;
      }

      if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
        setError(
          replaceVars(t.fileTooLarge, {
            maxSize: MAX_FILE_SIZE_MB,
          }),
        );
        return;
      }
    }

    setError(
      duplicates.length
        ? `${duplicates.length} duplicate file${duplicates.length === 1 ? " was" : "s were"} rejected. The remaining files are ready.`
        : "",
    );
    setSelectedFiles(fileList);
    resetResultState();
  }

  function handleFileChange(event) {
    handlePickedFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    if (isProcessing) return;

    handlePickedFiles(event.dataTransfer.files);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function handleRemoveFile(index) {
    if (isProcessing) return;
    setSelectedFiles((current) =>
      current.filter((_, fileIndex) => fileIndex !== index),
    );
    setError("");
    resetResultState();
  }

  function handleDocumentTypeChange(value) {
    if (isProcessing) return;

    setSelectedDocumentType(value);
    setDocumentClasses(value === AUTO_DOCUMENT_CLASS_VALUE ? [] : [value]);
    setError("");
    resetResultState();
  }

  function toggleDocumentClass(value) {
    if (isProcessing) return;
    setDocumentClasses((current) => {
      let next;
      if (current.includes(value)) {
        next = current.filter((item) => item !== value);
      } else {
        next = [...current, value];
      }
      setSelectedDocumentType(
        next.length === 1 ? next[0] : AUTO_DOCUMENT_CLASS_VALUE,
      );
      return next;
    });
    setError("");
    resetResultState();
  }

  function addSuggestedField(field) {
    if (isProcessing) return;
    setSelectedFieldsText((current) => {
      const next = uniqueStrings([...parseSelectedFields(current), field]);
      return next.join("\n");
    });
    setError("");
    resetResultState();
  }

  function clearSelectedFields() {
    if (isProcessing) return;
    setSelectedFieldsText("");
    setError("");
    resetResultState();
  }

  function handleDownload() {
    if (!downloadInfo?.downloadUrl) return;

    const link = document.createElement("a");
    link.href = downloadInfo.downloadUrl;
    link.download = downloadInfo.filename || "structured-extraction";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (!selectedFiles.length) {
      setError(t.chooseFileToExtract);
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {
      const fallbackFilename = buildDocumentSetFallbackFilename(
        selectedFiles,
        outputFormat,
      );

      const formData = new FormData();
      for (const file of selectedFiles) {
        formData.append("files", file);
      }
      formData.append("output_format", outputFormat);
      formData.append("result_shape", resultShape);
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      for (const documentClass of documentClasses) {
        formData.append("document_classes", documentClass);
      }

      for (const field of selectedFields) {
        formData.append("selected_fields", field);
      }

      const response = await fetch(STRUCTURED_EXTRACTION_ENDPOINT, {
        method: "POST",
        body: formData,
        credentials: "include",
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          extractResponseMessage(responseData, t.extractionFailed),
        );
      }

      const resolvedDownload = extractDownloadInfo(
        responseData,
        fallbackFilename,
      );

      setDownloadInfo(resolvedDownload);
      const structuredPreview = extractStructuredPreview(responseData);

      setPreviewPayload(structuredPreview.previewPayload);
      setPreviewRows(structuredPreview.previewRows);
      setPreviewTruncated(structuredPreview.previewTruncated);

      const classLabels = documentClasses.length
        ? documentClasses
            .map((item) => t.documentClassLabels[item] || item)
            .join(", ")
        : ux.autoDetectDocumentType;

      const resultShapeLabel = ux.resultShapeLabels[resultShape] || resultShape;

      const outputFormatLabel =
        ux.outputFormatLabels[outputFormat] || `.${outputFormat}`;

      const summaryLines = [
        t.extractionCompleted,
        "",
        `${t.inputFile}: ${selectedFiles.map((file) => file.name).join(", ")}`,
        `${t.inputExtension}: ${inputExtensionSummary}`,
        `${t.documentClassesResult}: ${classLabels}`,
        `${t.resultShapeResult}: ${resultShapeLabel}`,
        `${t.outputFormatResult}: ${outputFormatLabel}`,
        `${t.selectedFieldsResult}: ${
          selectedFields.length > 0
            ? selectedFields.join(", ")
            : t.allDetectedFields
        }`,
        `${t.extractedFile}: ${resolvedDownload.filename || fallbackFilename}`,
        "",
        resolvedDownload.downloadUrl ? t.outputReadyText : t.missingDownloadUrl,
        "",
        t.humanReviewRequired,
      ];

      setResultSummary(summaryLines.join("\n"));
    } catch (submitError) {
      setError(submitError?.message || t.extractionFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-x-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto flex min-h-screen max-w-7xl flex-col px-4 py-4 md:px-5 lg:py-4">
          <header className="mb-3 shrink-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => router.push("/")}
                className="inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
              >
                <ArrowLeft className="h-4 w-4" />
                {common.back}
              </button>

              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-4 py-2 text-sm text-[var(--app-accent-text)] backdrop-blur">
                <Sparkles className="h-4 w-4" />
                {t.badge}
              </div>
            </div>

            <div className="mt-3">
              <h1 className="max-w-full text-2xl font-semibold tracking-tight text-[var(--app-text)] sm:text-3xl lg:whitespace-nowrap lg:text-[2.15rem] lg:leading-tight xl:text-[2.35rem]">
                {t.title}
              </h1>
              <p className="mt-1 max-w-4xl text-sm leading-5 app-text-muted md:text-base">
                {t.description}
              </p>
            </div>
          </header>

          <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,0.92fr)_minmax(420px,1.08fr)] lg:items-stretch">
            <form
              onSubmit={handleSubmit}
              className="relative min-h-0 overflow-y-auto rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3 backdrop-blur-xl md:p-4 lg:max-h-[calc(100vh-8.5rem)]"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative flex h-full min-h-0 flex-col">
                <div
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  className="rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center transition hover:border-[var(--app-border-strong)] hover:bg-[var(--app-surface-strong)] md:p-5"
                >
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <Upload className="h-5 w-5 text-cyan-300" />
                  </div>

                  <h2 className="text-base font-semibold text-[var(--app-text)]">
                    {t.uploadTitle}
                  </h2>

                  <p className="mt-1 text-xs leading-5 app-text-soft">
                    {replaceVars(t.allowedFileInputs, {
                      maxFiles: MAX_STRUCTURED_EXTRACTION_FILES,
                    })}
                  </p>

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.docx,.jpg,.jpeg,.png"
                    multiple
                    disabled={isProcessing}
                    onChange={handleFileChange}
                    className="hidden"
                  />

                  <button
                    type="button"
                    disabled={isProcessing}
                    onClick={() =>
                      !isProcessing && fileInputRef.current?.click()
                    }
                    className={`mt-3 rounded-2xl px-4 py-2.5 text-sm font-semibold transition ${
                      isProcessing
                        ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                        : "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                    }`}
                  >
                    {common.chooseFile}
                  </button>
                </div>

                {selectedFiles.length > 0 && isValidFileSelection && (
                  <div className="mt-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                      <div className="min-w-0">
                        <p className="font-medium text-emerald-100">
                          {common.fileAccepted}
                        </p>
                        <div className="mt-1 max-h-24 space-y-1 overflow-y-auto pr-1">
                          {selectedFiles.map((file, index) => {
                            const ext = getFileExtension(file.name);

                            return (
                              <div
                                key={`${file.name}-${file.size}-${index}`}
                                className="flex items-center gap-2 text-sm text-emerald-100/80"
                              >
                                <p className="min-w-0 flex-1 truncate">
                                  {file.name} • {formatBytes(file.size)} •{" "}
                                  {getInputTypeLabel(ext, t)}
                                </p>
                                <button
                                  type="button"
                                  disabled={isProcessing}
                                  onClick={() => handleRemoveFile(index)}
                                  className="rounded-lg p-1 transition hover:bg-emerald-300/10 disabled:opacity-50"
                                  aria-label={`Remove ${file.name}`}
                                  title={`Remove ${file.name}`}
                                >
                                  <X className="h-4 w-4" />
                                </button>
                              </div>
                            );
                          })}
                        </div>
                        <p className="mt-1 text-sm text-emerald-100/80">
                          {t.detectedType} {inputExtensionSummary}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                  <label className="block">
                    <span className="mb-2 block text-sm font-medium app-text-muted">
                      {ux.documentTypeLabel || t.documentClassesLabel}
                    </span>
                    <select
                      value={selectedDocumentType}
                      disabled={isProcessing}
                      onChange={(event) =>
                        handleDocumentTypeChange(event.target.value)
                      }
                      className={`w-full rounded-2xl border border-[var(--app-border)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition ${
                        isProcessing
                          ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                          : "bg-[var(--app-surface-strong)] focus:border-[var(--app-accent-border)]"
                      }`}
                    >
                      {SIMPLE_DOCUMENT_TYPE_OPTIONS.map((documentClass) => (
                        <option
                          key={documentClass}
                          value={documentClass}
                          className="bg-[var(--app-panel)] text-[var(--app-text)]"
                        >
                          {documentClass === AUTO_DOCUMENT_CLASS_VALUE
                            ? ux.autoDetectDocumentType
                            : t.documentClassLabels[documentClass] ||
                              documentClass}
                        </option>
                      ))}
                    </select>
                    <p className="mt-2 text-xs leading-5 app-text-soft">
                      {selectedDocumentType === AUTO_DOCUMENT_CLASS_VALUE
                        ? ux.autoDetectDocumentTypeHelp
                        : ux.simpleFlowHelp}
                    </p>
                  </label>
                </div>

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                  <button
                    type="button"
                    disabled={isProcessing}
                    onClick={() =>
                      !isProcessing && setAdvancedOpen((value) => !value)
                    }
                    className={`flex w-full items-center justify-between gap-3 text-left ${
                      isProcessing
                        ? "cursor-not-allowed app-text-soft"
                        : "app-text"
                    }`}
                  >
                    <span>
                      <span className="block text-sm font-semibold">
                        {ux.advancedOptions}
                      </span>
                      <span className="mt-1 block text-xs leading-5 app-text-soft">
                        {ux.advancedOptionsHelp}
                      </span>
                    </span>
                    <span className="rounded-full border border-[var(--app-border)] px-3 py-1 text-xs app-text-soft">
                      {advancedOpen ? "−" : "+"}
                    </span>
                  </button>

                  {advancedOpen && (
                    <div className="mt-4 border-t border-[var(--app-border)] pt-4">
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="block">
                          <span className="mb-2 block text-sm font-medium app-text-muted">
                            {t.outputFormatLabel}
                          </span>
                          <select
                            value={outputFormat}
                            disabled={isProcessing}
                            onChange={(event) => {
                              if (isProcessing) return;
                              setOutputFormat(event.target.value);
                              setError("");
                              resetResultState();
                            }}
                            className={`w-full rounded-2xl border border-[var(--app-border)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition ${
                              isProcessing
                                ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                                : "bg-[var(--app-surface)] focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                            }`}
                          >
                            {OUTPUT_FORMATS.map((format) => (
                              <option
                                key={format}
                                value={format}
                                className="bg-[var(--app-panel)] text-[var(--app-text)]"
                              >
                                {ux.outputFormatLabels[format] || `.${format}`}
                              </option>
                            ))}
                          </select>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.outputFormatHelp}
                          </p>
                        </label>

                        <label className="block">
                          <span className="mb-2 block text-sm font-medium app-text-muted">
                            {t.resultShapeLabel}
                          </span>
                          <select
                            value={resultShape}
                            disabled={isProcessing}
                            onChange={(event) => {
                              if (isProcessing) return;
                              setResultShape(event.target.value);
                              setError("");
                              resetResultState();
                            }}
                            className={`w-full rounded-2xl border border-[var(--app-border)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition ${
                              isProcessing
                                ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                                : "bg-[var(--app-surface)] focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                            }`}
                          >
                            {RESULT_SHAPES.map((shape) => (
                              <option
                                key={shape}
                                value={shape}
                                className="bg-[var(--app-panel)] text-[var(--app-text)]"
                              >
                                {ux.resultShapeLabels[shape] || shape}
                              </option>
                            ))}
                          </select>
                          {resultShapeDescription && (
                            <p className="mt-1 rounded-xl border border-cyan-300/20 bg-[var(--app-accent-bg)] px-3 py-2 text-xs leading-5 text-[var(--app-accent-text)]">
                              {resultShapeDescription}
                            </p>
                          )}
                        </label>
                      </div>

                      <div className="mt-3 rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-3">
                        <div className="flex items-start gap-3">
                          <SlidersHorizontal className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" />
                          <div className="min-w-0 flex-1">
                            <SearchableMultiSelect
                              title={t.documentClassesLabel}
                              disabled={isProcessing}
                              helpText={t.documentClassesHelp}
                              emptyText={ux.autoDetectDocumentTypeHelp}
                              examplesText={t.documentClassesExamples}
                              items={DOCUMENT_CLASSES}
                              selectedValues={documentClasses}
                              onToggle={toggleDocumentClass}
                              getLabel={(documentClass) =>
                                t.documentClassLabels[documentClass] ||
                                documentClass
                              }
                              searchPlaceholder={
                                t.searchDocumentClassesPlaceholder
                              }
                            />
                          </div>
                        </div>
                      </div>

                      <label className="mt-3 block">
                        <div className="mb-2 flex items-center justify-between gap-3">
                          <span className="text-sm font-medium app-text-muted">
                            {t.selectedFieldsLabel}
                          </span>

                          {selectedFields.length > 0 && (
                            <button
                              type="button"
                              disabled={isProcessing}
                              onClick={() =>
                                !isProcessing && clearSelectedFields()
                              }
                              className={`text-xs font-medium transition ${
                                isProcessing
                                  ? "cursor-not-allowed app-text-soft"
                                  : "text-[var(--app-accent-text)] hover:text-[var(--app-accent-text)]"
                              }`}
                            >
                              {t.clearFields}
                            </button>
                          )}
                        </div>
                        <p className="mb-1 text-xs leading-5 app-text-soft">
                          {t.selectedFieldsHelp}
                        </p>
                        <textarea
                          value={selectedFieldsText}
                          disabled={isProcessing}
                          onChange={(event) => {
                            if (isProcessing) return;
                            setSelectedFieldsText(event.target.value);
                            setError("");
                            resetResultState();
                          }}
                          placeholder={t.selectedFieldsPlaceholder}
                          rows={3}
                          className={`w-full resize-none rounded-2xl border border-[var(--app-border)] px-4 py-3 text-sm leading-6 text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] ${
                            isProcessing
                              ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                              : "bg-[var(--app-surface)] focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                          }`}
                        />
                      </label>

                      <div className="mt-3">
                        <p className="mb-1 text-xs font-medium app-text-soft">
                          {t.suggestedFieldsLabel}
                        </p>
                        <p className="mb-1 text-xs leading-5 app-text-soft">
                          {t.suggestedFieldsHelp}
                        </p>
                        <div className="flex max-h-20 flex-wrap gap-2 overflow-y-auto pr-1">
                          {activeSuggestedFields.map((field) => (
                            <button
                              key={field}
                              type="button"
                              disabled={isProcessing}
                              onClick={() =>
                                !isProcessing && addSuggestedField(field)
                              }
                              className={`rounded-full border px-3 py-1 text-xs transition ${
                                isProcessing
                                  ? "cursor-not-allowed border-[var(--app-border)] bg-[var(--app-surface)] app-text-soft"
                                  : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:border-[var(--app-accent-border)] hover:bg-[var(--app-accent-bg)] hover:text-[var(--app-accent-text)]"
                              }`}
                            >
                              {field}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {error && (
                  <div className="mt-3 rounded-2xl border border-red-400/20 bg-red-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                )}

                <div className="mt-auto pt-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="submit"
                      disabled={!canSubmit}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${
                        canSubmit
                          ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                          : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                      }`}
                    >
                      {isSubmitting ? t.extracting : t.extractAction}
                    </button>

                    {downloadInfo?.downloadUrl && (
                      <button
                        type="button"
                        onClick={handleDownload}
                        className="inline-flex items-center gap-2 rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-5 py-2.5 text-sm font-semibold text-[var(--app-accent-text)] transition hover:bg-[var(--app-accent-bg)]"
                      >
                        <Download className="h-4 w-4" />
                        {common.download}
                      </button>
                    )}
                  </div>

                  <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-soft">
                    {t.extractionLabel}{" "}
                    <span className="font-medium app-text-muted">
                      .{outputFormat}
                    </span>
                  </div>
                </div>
              </div>
            </form>

            <aside className="min-h-0 lg:h-full">
              <div className="flex min-h-[360px] flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5 lg:min-h-[calc(100vh-8.5rem)] lg:max-h-[calc(100vh-8.5rem)]">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.extractionOutput}
                  </h2>
                  <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1 text-xs app-text-soft">
                    .{outputFormat}
                  </span>
                </div>

                <div className="mt-3 min-h-[320px] flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4 lg:max-h-none">
                  {resultSummary ? (
                    <div className="flex h-full min-h-0 flex-col gap-3">
                      <pre className="whitespace-pre-wrap break-words pr-1 text-xs leading-6 app-text-muted md:text-sm">
                        {resultSummary}
                      </pre>

                      {previewPayload && (
                        <div className="rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-3">
                          <div className="mb-3 flex items-center justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-[var(--app-accent-text)]">
                                {ux.previewGeneratedTitle}
                              </p>
                              <p className="mt-1 text-xs text-[var(--app-accent-text)]">
                                {ux.previewGeneratedBody}
                              </p>
                            </div>
                            <FileJson className="h-5 w-5 shrink-0 text-[var(--app-accent-text)]" />
                          </div>

                          {previewRows.length > 0 && (
                            <div className="mb-3 overflow-x-auto rounded-xl border border-[var(--app-border)] bg-black/20">
                              <table className="min-w-full text-left text-xs app-text-muted">
                                <thead className="border-b border-[var(--app-border)] text-[var(--app-text)]">
                                  <tr>
                                    {Object.keys(previewRows[0])
                                      .slice(0, 8)
                                      .map((key) => (
                                        <th
                                          key={key}
                                          className="px-3 py-2 font-medium"
                                        >
                                          {key}
                                        </th>
                                      ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {previewRows
                                    .slice(0, 10)
                                    .map((row, rowIndex) => (
                                      <tr
                                        key={rowIndex}
                                        className="border-b border-[var(--app-border)]"
                                      >
                                        {Object.keys(previewRows[0])
                                          .slice(0, 8)
                                          .map((key) => (
                                            <td
                                              key={key}
                                              className="max-w-[220px] truncate px-3 py-2"
                                            >
                                              {String(row?.[key] ?? "")}
                                            </td>
                                          ))}
                                      </tr>
                                    ))}
                                </tbody>
                              </table>
                            </div>
                          )}

                          {selectedFieldStatusRows.length > 0 && (
                            <div className="mb-3 rounded-xl border border-[var(--app-border)] bg-black/20 p-3">
                              <p className="text-xs font-semibold text-[var(--app-accent-text)]">
                                {ux.selectedFieldStatusTitle}
                              </p>
                              <p className="mt-1 text-xs app-text-soft">
                                {ux.selectedFieldStatusHelp}
                              </p>
                              <div className="mt-3 grid gap-2">
                                {selectedFieldStatusRows.map((row) => {
                                  const statusLabel =
                                    row.status === "found"
                                      ? ux.fieldStatusFound
                                      : row.status === "low_confidence"
                                        ? ux.fieldStatusLowConfidence
                                        : ux.fieldStatusNotFound;
                                  const evidenceText =
                                    row.evidence?.excerpt ||
                                    row.evidence?.value ||
                                    ux.fieldStatusNoEvidence;

                                  return (
                                    <div
                                      key={row.fieldName}
                                      className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3"
                                    >
                                      <div className="flex flex-wrap items-center justify-between gap-2">
                                        <p className="text-xs font-semibold app-text-muted">
                                          {row.fieldName}
                                        </p>
                                        <span className="rounded-full border border-[var(--app-border)] px-2 py-0.5 text-[0.68rem] app-text-soft">
                                          {statusLabel}
                                        </span>
                                      </div>
                                      {row.value ? (
                                        <p className="mt-2 text-xs app-text-soft">
                                          {ux.fieldStatusValue}: {row.value}
                                        </p>
                                      ) : null}
                                      <p className="mt-2 text-xs app-text-soft">
                                        {ux.fieldStatusEvidence}: {evidenceText}
                                      </p>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          <details className="rounded-xl border border-[var(--app-border)] bg-black/20 p-3">
                            <summary className="cursor-pointer text-xs font-medium text-[var(--app-accent-text)]">
                              {ux.viewStructuredJson}
                            </summary>
                            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 app-text-muted">
                              {JSON.stringify(previewPayload, null, 2)}
                            </pre>
                          </details>

                          {previewTruncated && (
                            <p className="mt-2 text-xs text-amber-100/80">
                              {ux.previewShortened}
                            </p>
                          )}
                        </div>
                      )}

                      {downloadInfo?.downloadUrl && (
                        <div className="shrink-0 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                          <div className="flex items-start gap-3">
                            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                            <div className="min-w-0">
                              <p className="font-medium text-emerald-100">
                                {t.downloadReady}
                              </p>
                              <p className="mt-1 truncate text-sm text-emerald-100/80">
                                {downloadInfo.filename}
                              </p>
                              <button
                                type="button"
                                onClick={handleDownload}
                                className="mt-3 inline-flex items-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                              >
                                <Download className="h-4 w-4" />
                                {common.download}
                              </button>
                              <ProcessedOutputActions
                                artifactUrl={downloadInfo.downloadUrl}
                                filename={downloadInfo.filename}
                                title="Structured extraction output"
                              />
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="flex h-full min-h-[180px] items-center justify-center rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center">
                      <div>
                        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                          {outputFormat === "json" ? (
                            <FileJson className="h-5 w-5 text-cyan-300" />
                          ) : outputFormat === "csv" ? (
                            <TableProperties className="h-5 w-5 text-cyan-300" />
                          ) : (
                            <Database className="h-5 w-5 text-cyan-300" />
                          )}
                        </div>
                        <p className="max-w-sm text-sm leading-6 app-text-soft">
                          {t.previewText}
                        </p>
                      </div>
                    </div>
                  )}
                </div>

                <div className="mt-3 grid gap-2 text-xs app-text-soft sm:grid-cols-3">
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                    <p className="font-medium app-text-muted">
                      {t.outputFormatsTitle}
                    </p>
                    <p className="mt-1">.json · .csv · .xlsx</p>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                    <p className="font-medium app-text-muted">
                      {t.reviewTitle}
                    </p>
                    <p className="mt-1">{t.reviewValue}</p>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                    <p className="font-medium app-text-muted">
                      {t.knowledgeTitle}
                    </p>
                    <p className="mt-1">{t.knowledgeValue}</p>
                  </div>
                </div>
              </div>
            </aside>
          </section>
        </div>
      </div>
    </AppSidebarLayout>
  );
}