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
} from "lucide-react";
import {
  commonTranslations,
  structuredExtractionPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import { FILE_SECURITY_POLICY, validateBrowserUpload } from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 10;
const STRUCTURED_EXTRACTION_ENDPOINT = "/api/analyzer/structured-extraction";

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
  return `${getFileStem(filename)}.structured-extraction.${outputFormat}`;
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

  const downloadUrl = pickFirstString([
    artifact?.download_url,
    artifact?.downloadUrl,
    result?.download_url,
    result?.downloadUrl,
    responseData?.download_url,
    responseData?.downloadUrl,
    responseData?.url,
  ]);

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

  const [selectedFile, setSelectedFile] = useState(null);
  const [documentClasses, setDocumentClasses] = useState(["form"]);
  const [selectedFieldsText, setSelectedFieldsText] = useState("");
  const [outputFormat, setOutputFormat] = useState("json");
  const [resultShape, setResultShape] = useState("machine_readable");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [resultSummary, setResultSummary] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);
  const [previewPayload, setPreviewPayload] = useState(null);
  const [previewRows, setPreviewRows] = useState([]);
  const [previewTruncated, setPreviewTruncated] = useState(false);

  const inputExtension = useMemo(() => {
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [selectedFile]);

  const selectedFields = useMemo(
    () => parseSelectedFields(selectedFieldsText),
    [selectedFieldsText],
  );

  const activeSuggestedFields = useMemo(() => {
    const fields = documentClasses.flatMap(
      (documentClass) => SUGGESTED_FIELDS_BY_CLASS[documentClass] || [],
    );
    return uniqueStrings(fields).slice(0, 16);
  }, [documentClasses]);

  const resultShapeDescription = t.resultShapeDescriptions?.[resultShape] || "";

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;

    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;

    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const canSubmit =
    !isSubmitting &&
    !!selectedFile &&
    isValidFile &&
    documentClasses.length > 0 &&
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

  function rejectFile(message) {
    setSelectedFile(null);
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.documentWithImages);
    if (securityError) {
      rejectFile(securityError);
      return;
    }

    const ext = getFileExtension(file.name);

    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      rejectFile(
        replaceVars(t.unsupportedFileType, {
          ext: ext || "unknown",
        }),
      );
      return;
    }

    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      rejectFile(
        replaceVars(t.fileTooLarge, {
          maxSize: MAX_FILE_SIZE_MB,
        }),
      );
      return;
    }

    setError("");
    setSelectedFile(file);
    resetResultState();
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0];
    handlePickedFile(file);
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    if (isProcessing) return;

    const file = event.dataTransfer.files?.[0];
    handlePickedFile(file);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function toggleDocumentClass(value) {
    if (isProcessing) return;
    setDocumentClasses((current) => {
      if (current.includes(value)) {
        if (current.length === 1) return current;
        return current.filter((item) => item !== value);
      }
      return [...current, value];
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

    if (!selectedFile) {
      setError(t.chooseFileToExtract);
      return;
    }

    if (!documentClasses.length) {
      setError(t.documentClassRequired);
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {
      const fallbackFilename = buildFallbackFilename(
        selectedFile.name,
        outputFormat,
      );

      const formData = new FormData();
      formData.append("file", selectedFile);
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

      formData.append("allow_external_knowledge", "false");
      formData.append("require_human_review", "true");

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

      const classLabels = documentClasses
        .map((item) => t.documentClassLabels[item] || item)
        .join(", ");

      const resultShapeLabel = t.resultShapeLabels[resultShape] || resultShape;

      const outputFormatLabel =
        t.outputFormatLabels[outputFormat] || `.${outputFormat}`;

      const summaryLines = [
        t.extractionCompleted,
        "",
        `${t.inputFile}: ${selectedFile.name}`,
        `${t.inputExtension}: ${inputExtension}`,
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
                    {t.allowedFileInputs}
                  </p>

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.docx,.jpg,.jpeg,.png"
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

                {selectedFile && isValidFile && (
                  <div className="mt-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                      <div className="min-w-0">
                        <p className="font-medium text-emerald-100">
                          {common.fileAccepted}
                        </p>
                        <p className="mt-1 truncate text-sm text-emerald-100/80">
                          {selectedFile.name} • {formatBytes(selectedFile.size)}
                        </p>
                        <p className="mt-1 text-sm text-emerald-100/80">
                          {t.detectedType}{" "}
                          {getInputTypeLabel(inputExtension, t)}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                <div className="mt-3 grid gap-3 sm:grid-cols-2">
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
                          {t.outputFormatLabels[format] || `.${format}`}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.outputFormatHelp}
                    </p>
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.outputFormatExamples}
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
                          {t.resultShapeLabels[shape] || shape}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.resultShapeHelp}
                    </p>
                    {resultShapeDescription && (
                      <p className="mt-1 rounded-xl border border-cyan-300/20 bg-[var(--app-accent-bg)] px-3 py-2 text-xs leading-5 text-[var(--app-accent-text)]">
                        {resultShapeDescription}
                      </p>
                    )}
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.resultShapeExamples}
                    </p>
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
                        emptyText={t.documentClassesEmptyHelp}
                        examplesText={t.documentClassesExamples}
                        items={DOCUMENT_CLASSES}
                        selectedValues={documentClasses}
                        onToggle={toggleDocumentClass}
                        getLabel={(documentClass) =>
                          t.documentClassLabels[documentClass] || documentClass
                        }
                        searchPlaceholder={t.searchDocumentClassesPlaceholder}
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
                        onClick={() => !isProcessing && clearSelectedFields()}
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
                  <p className="mb-3 text-xs leading-5 app-text-soft">
                    {t.selectedFieldsExamples}
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
                  <p className="mb-2 text-xs leading-5 app-text-soft">
                    {t.suggestedFieldsExamples}
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
                                Generated preview
                              </p>
                              <p className="mt-1 text-xs text-[var(--app-accent-text)]">
                                Review the extracted data before downloading the
                                file.
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

                          <details className="rounded-xl border border-[var(--app-border)] bg-black/20 p-3">
                            <summary className="cursor-pointer text-xs font-medium text-[var(--app-accent-text)]">
                              View structured JSON
                            </summary>
                            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 app-text-muted">
                              {JSON.stringify(previewPayload, null, 2)}
                            </pre>
                          </details>

                          {previewTruncated && (
                            <p className="mt-2 text-xs text-amber-100/80">
                              Preview shortened. Download the full file to see
                              all rows.
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
                    <p className="font-medium app-text-muted">{t.reviewTitle}</p>
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
