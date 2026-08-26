"use client";

import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
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
  Printer,
  Share2,
  Users,
  Loader2,
  ChevronDown,
  X,
} from "lucide-react";
import {
  commonTranslations,
  processedOutputActionTranslations,
  structuredExtractionPageTranslations,
  resolveErrorMessage,
  resolveErrorTranslationKey,
  getPageRuntimeCopy,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
  getMyOrganizations,
  getOrganization,
  createConversation,
  sendConversationAttachment,
  forwardConversationMessage,
  postAnalyzerFeature,
} from "@/lib/api_client";
import {
  FILE_SECURITY_POLICY,
  partitionDuplicateBrowserUploads,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 25;
const MAX_STRUCTURED_EXTRACTION_FILES = 20;
const STRUCTURED_EXTRACTION_ENDPOINT = "/api/analyzer/structured-extraction";
const DEFAULT_OUTPUT_FORMAT = "xlsx";
const DEFAULT_RESULT_SHAPE = "row_based_records";
const TECHNICAL_PREVIEW_COLUMNS = new Set([
  "source_checksum_sha256",
  "extraction_quality_score",
]);
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
    ...ux,
    outputFormatLabels: ux.outputFormatLabels || {},
    resultShapeLabels: ux.resultShapeLabels || {},
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

    const tables = Array.isArray(documentItem?.tables)
      ? documentItem.tables
      : [];
    for (const table of tables) {
      const tableRows = Array.isArray(table?.rows) ? table.rows : [];
      for (const row of tableRows) {
        if (!row || typeof row !== "object") continue;
        for (const [name, value] of Object.entries(row)) {
          addFieldCandidate(candidates, name, value, {
            confidence: value ? 0.8 : 0,
          });
        }
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
      fieldName: raw,
      value: bestCandidate?.value || "",
      status: !found
        ? "not_found"
        : confidence < 0.6
          ? "low_confidence"
          : "found",
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

const TEAM_SHARE_MAX_FILE_BYTES = 20 * 1024 * 1024;
const TEAM_SHARE_MAX_RECIPIENTS = 50;
const OFFICE_PRINT_EXTENSIONS = new Set([".docx", ".xlsx", ".pptx"]);
const TEXT_PRINT_EXTENSIONS = new Set([".txt", ".csv", ".json", ".md"]);
const IMAGE_PRINT_EXTENSIONS = new Set([".jpg", ".jpeg", ".png"]);
const TEAM_SHARE_ALLOWED_EXTENSIONS = new Set([
  ".pdf",
  ".docx",
  ".xlsx",
  ".pptx",
  ".txt",
  ".csv",
  ".md",
  ".json",
  ".png",
  ".jpg",
  ".jpeg",
  ".mp3",
  ".mp4",
  ".mov",
  ".mkv",
]);
const OUTPUT_ACTION_COPY = processedOutputActionTranslations;

function actionFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function sanitizeSharedFilename(value = "") {
  const normalized = String(value || "")
    .normalize("NFKC")
    .replace(/[\\/\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/gu, "_")
    .trim()
    .slice(0, 180);

  return normalized || "redocx-output";
}

function mimeTypeForFilename(filename = "") {
  switch (getFileExtension(filename)) {
    case ".pdf":
      return "application/pdf";
    case ".docx":
      return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    case ".xlsx":
      return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
    case ".pptx":
      return "application/vnd.openxmlformats-officedocument.presentationml.presentation";
    case ".csv":
      return "text/csv";
    case ".json":
      return "application/json";
    case ".txt":
    case ".md":
      return "text/plain";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".png":
      return "image/png";
    case ".zip":
      return "application/zip";
    default:
      return "application/octet-stream";
  }
}

function downloadFilenameFromUrl(url = "") {
  try {
    return (
      new URL(
        String(url || ""),
        typeof window !== "undefined" ? window.location.origin : "http://local",
      ).searchParams.get("download_name") || ""
    );
  } catch {
    return "";
  }
}

function normalizeOutputArtifact({
  artifactUrl = "",
  storageKey = "",
  filename = "",
  contentType = "",
} = {}) {
  const normalizedStorageKey = actionFirstString([storageKey]);
  const normalizedUrl =
    normalizeAnalyzerArtifactUrl(actionFirstString([artifactUrl])) ||
    buildAnalyzerArtifactUrl(normalizedStorageKey);

  if (!normalizedUrl) return null;

  const resolvedFilename = sanitizeSharedFilename(
    actionFirstString([filename, downloadFilenameFromUrl(normalizedUrl)]) ||
      "redocx-output",
  );

  return {
    url: normalizedUrl,
    storageKey: normalizedStorageKey,
    filename: resolvedFilename,
    contentType: actionFirstString([
      contentType,
      mimeTypeForFilename(resolvedFilename),
    ]),
    key: `${normalizedUrl}|${resolvedFilename}`,
  };
}

function textOutputKey(text = "") {
  let hash = 2166136261;
  const value = String(text || "");
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function normalizeTextOutputArtifact(textContent = "", textFilename = "") {
  const text = String(textContent || "");
  if (!text.trim()) return null;
  const filename = sanitizeSharedFilename(textFilename || "redocx-output.txt");
  return {
    url: "",
    storageKey: "",
    filename,
    contentType: mimeTypeForFilename(filename) || "text/plain",
    textContent: text,
    key: `text:${filename}:${text.length}:${textOutputKey(text)}`,
  };
}

function collectDownloadableArtifacts(value) {
  const artifacts = [];
  const seenObjects = new WeakSet();
  const seenArtifacts = new Set();

  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (seenObjects.has(node)) return;
    seenObjects.add(node);

    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }

    const artifact = normalizeOutputArtifact({
      artifactUrl: actionFirstString([
        node.download_url,
        node.downloadUrl,
        node.artifact_url,
        node.artifactUrl,
      ]),
      storageKey: actionFirstString([node.storage_key, node.storageKey]),
      filename: actionFirstString([
        node.original_artifact_name,
        node.artifact_name,
        node.artifactName,
        node.output_filename,
        node.file_name,
        node.filename,
      ]),
      contentType: actionFirstString([
        node.content_type,
        node.contentType,
        node.mime_type,
        node.mimeType,
      ]),
    });

    if (artifact && !seenArtifacts.has(artifact.key)) {
      seenArtifacts.add(artifact.key);
      artifacts.push(artifact);
    }

    Object.values(node).forEach(visit);
  }

  visit(value);
  return artifacts;
}

async function readArtifactFetchError(response) {
  const contentType = String(response.headers.get("content-type") || "").toLowerCase();
  let payload = null;
  if (contentType.includes("application/json")) {
    payload = await response.json().catch(() => null);
  } else {
    await response.text().catch(() => "");
  }
  const error = new Error("OUTPUT_ARTIFACT_REQUEST_FAILED");
  error.code = resolveErrorTranslationKey(payload, "OUTPUT_ARTIFACT_REQUEST_FAILED");
  error.payload = payload;
  error.status = response.status;
  return error;
}

async function fetchArtifactAsFile(artifact, { signal } = {}) {
  if (artifact?.textContent != null) {
    if (typeof File === "undefined") {
      throw Object.assign(new Error("BROWSER_FILE_PREPARE_UNAVAILABLE"), { code: "BROWSER_FILE_PREPARE_UNAVAILABLE" });
    }
    return new File(
      [String(artifact.textContent)],
      sanitizeSharedFilename(artifact.filename),
      {
        type: artifact.contentType || "text/plain;charset=utf-8",
        lastModified: Date.now(),
      },
    );
  }

  if (!artifact?.url) throw Object.assign(new Error("OUTPUT_FILE_UNAVAILABLE"), { code: "OUTPUT_FILE_UNAVAILABLE" });

  const response = await fetch(artifact.url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "*/*" },
    signal,
  });

  if (!response.ok) throw await readArtifactFetchError(response);

  const blob = await response.blob();
  if (!blob.size) throw Object.assign(new Error("OUTPUT_FILE_EMPTY"), { code: "OUTPUT_FILE_EMPTY" });
  if (typeof File === "undefined") {
    throw Object.assign(new Error("BROWSER_FILE_PREPARE_UNAVAILABLE"), { code: "BROWSER_FILE_PREPARE_UNAVAILABLE" });
  }

  const responseType = String(blob.type || "").split(";", 1)[0].trim();
  const resolvedType =
    responseType && responseType !== "application/octet-stream"
      ? responseType
      : artifact.contentType || mimeTypeForFilename(artifact.filename);

  return new File([blob], sanitizeSharedFilename(artifact.filename), {
    type: resolvedType || "application/octet-stream",
    lastModified: Date.now(),
  });
}

function createShareClientMessageId(prefix = "share") {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}:${crypto.randomUUID()}`;
  }
  return `${prefix}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function escapeHtml(value = "") {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderPrintMessage(printWindow, title, message, { isError = false } = {}) {
  const safeTitle = escapeHtml(title || "ReDOCX");
  const safeMessage = escapeHtml(message || "");
  const toneClass = isError ? "error" : "status";

  printWindow.document.open();
  printWindow.document.write(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${safeTitle}</title>
    <style>
      body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0b1220; color: #e5eefc; }
      main { min-height: 100vh; display: grid; place-items: center; padding: 32px; box-sizing: border-box; }
      .card { width: min(560px, 100%); border: 1px solid rgba(148,163,184,.28); border-radius: 18px; background: rgba(15,23,42,.96); padding: 24px; box-sizing: border-box; }
      h1 { margin: 0 0 10px; font-size: 18px; }
      p { margin: 0; line-height: 1.6; color: #cbd5e1; }
      .error { color: #fecaca; }
      .status { color: #bae6fd; }
    </style>
  </head>
  <body><main><div class="card"><h1>${safeTitle}</h1><p class="${toneClass}">${safeMessage}</p></div></main></body>
</html>`);
  printWindow.document.close();
}

async function renderPrintableFile(printWindow, file, title, copy) {
  const extension = getFileExtension(file.name);
  const contentType = String(file.type || "").split(";", 1)[0].toLowerCase();
  const safeTitle = escapeHtml(title || file.name || copy.outputTitle);

  if (extension === ".zip") throw Object.assign(new Error("PRINT_ARCHIVE_UNSUPPORTED"), { code: "PRINT_ARCHIVE_UNSUPPORTED" });

  if (
    TEXT_PRINT_EXTENSIONS.has(extension) ||
    contentType.startsWith("text/") ||
    contentType === "application/json"
  ) {
    const text = await file.text();
    const safeText = escapeHtml(text);
    printWindow.document.open();
    printWindow.document.write(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${safeTitle}</title>
    <style>
      @page { margin: 16mm; }
      body { margin: 0; background: white; color: #111827; font: 11pt/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
      pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
    </style>
  </head>
  <body><pre>${safeText}</pre><script>window.addEventListener("load",()=>setTimeout(()=>{window.focus();window.print();},100),{once:true});</script></body>
</html>`);
    printWindow.document.close();
    return;
  }

  const isImage =
    contentType.startsWith("image/") || IMAGE_PRINT_EXTENSIONS.has(extension);
  const isPdf = contentType === "application/pdf" || extension === ".pdf";
  if (!isImage && !isPdf) throw Object.assign(new Error("PRINT_UNSUPPORTED"), { code: "PRINT_UNSUPPORTED" });

  const objectUrl = URL.createObjectURL(file);
  const safeUrl = escapeHtml(objectUrl);
  printWindow.document.open();
  printWindow.document.write(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${safeTitle}</title>
    <style>
      html, body { margin: 0; width: 100%; min-height: 100%; background: white; }
      .screen-note { position: fixed; z-index: 5; top: 12px; left: 50%; transform: translateX(-50%); padding: 8px 12px; border-radius: 999px; background: rgba(15,23,42,.92); color: white; font: 13px/1.4 ui-sans-serif, system-ui, sans-serif; }
      iframe { display: block; width: 100vw; height: 100vh; border: 0; }
      img { display: block; max-width: 100%; height: auto; margin: 0 auto; }
      @media print { .screen-note { display: none !important; } iframe { width: 100%; height: 100vh; } img { max-width: 100%; max-height: 100vh; object-fit: contain; } }
    </style>
  </head>
  <body>
    <div class="screen-note">${escapeHtml(copy.securePrintPreview)}</div>
    ${
      isImage
        ? `<img id="print-image" src="${safeUrl}" alt="${safeTitle}" />`
        : `<iframe id="print-document" src="${safeUrl}" title="${safeTitle}"></iframe>`
    }
    <script>
      (() => {
        let attempted = false;
        const trigger = () => {
          if (attempted) return;
          attempted = true;
          setTimeout(() => {
            try {
              const frame = document.getElementById("print-document");
              if (frame && frame.contentWindow) {
                frame.contentWindow.focus();
                frame.contentWindow.print();
                return;
              }
              window.focus();
              window.print();
            } catch (_) {
              window.focus();
              window.print();
            }
          }, 250);
        };
        const printable = document.getElementById("print-image") || document.getElementById("print-document");
        if (printable) printable.addEventListener("load", trigger, { once: true });
        setTimeout(trigger, 1800);
      })();
    </script>
  </body>
</html>`);
  printWindow.document.close();
  try {
    printWindow.focus();
  } catch {
    // The preview remains usable even when focus is denied by the browser.
  }

  const revoke = () => URL.revokeObjectURL(objectUrl);
  try {
    printWindow.addEventListener("beforeunload", revoke, { once: true });
  } catch {
    // Timed cleanup below remains authoritative.
  }
  setTimeout(revoke, 10 * 60 * 1000);
}

function ProductionOutputActions({
  result = null,
  artifactUrl = "",
  storageKey = "",
  filename = "",
  contentType = "",
  textContent = "",
  textFilename = "",
  title = "",
  language = "en",
  account = null,
}) {
  const copy = OUTPUT_ACTION_COPY[language] || OUTPUT_ACTION_COPY.en;
  const fileCacheRef = useRef(new Map());
  const filePromiseCacheRef = useRef(new Map());
  const directArtifact = useMemo(
    () =>
      normalizeOutputArtifact({ artifactUrl, storageKey, filename, contentType }) ||
      normalizeTextOutputArtifact(textContent, textFilename),
    [artifactUrl, storageKey, filename, contentType, textContent, textFilename],
  );
  const artifacts = useMemo(
    () => (directArtifact ? [directArtifact] : collectDownloadableArtifacts(result)),
    [directArtifact, result],
  );

  const [shareMenuKey, setShareMenuKey] = useState("");
  const [preparingShareKey, setPreparingShareKey] = useState("");
  const [preparedShareKey, setPreparedShareKey] = useState("");
  const [busyAction, setBusyAction] = useState("");
  const [actionMessage, setActionMessage] = useState(null);

  const [memberShareArtifact, setMemberShareArtifact] = useState(null);
  const [shareOrganizations, setShareOrganizations] = useState([]);
  const [shareOrganizationId, setShareOrganizationId] = useState("");
  const [shareMembers, setShareMembers] = useState([]);
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  const [shareUserId, setShareUserId] = useState("");
  const [memberShareLoading, setMemberShareLoading] = useState(false);
  const [memberShareBusy, setMemberShareBusy] = useState(false);
  const [memberShareMessage, setMemberShareMessage] = useState(null);

  const isSignedIn = Boolean(
    account?.isSignedIn || account?.user?.id || account?.account?.user?.id,
  );

  async function prepareArtifactFile(artifact) {
    const cached = fileCacheRef.current.get(artifact.key);
    if (cached) return cached;

    let pending = filePromiseCacheRef.current.get(artifact.key);
    if (!pending) {
      pending = fetchArtifactAsFile(artifact).then((file) => {
        fileCacheRef.current.set(artifact.key, file);
        return file;
      });
      filePromiseCacheRef.current.set(artifact.key, pending);
    }

    try {
      return await pending;
    } finally {
      filePromiseCacheRef.current.delete(artifact.key);
    }
  }

  async function openShareMenu(artifact) {
    if (shareMenuKey === artifact.key) {
      setShareMenuKey("");
      setActionMessage(null);
      return;
    }

    setShareMenuKey(artifact.key);
    setPreparedShareKey(fileCacheRef.current.has(artifact.key) ? artifact.key : "");
    setActionMessage(null);
    if (fileCacheRef.current.has(artifact.key)) return;

    setPreparingShareKey(artifact.key);
    try {
      await prepareArtifactFile(artifact);
      setPreparedShareKey(artifact.key);
    } catch (shareError) {
      setActionMessage({
        type: "error",
        text: resolveErrorMessage(shareError, language, "OUTPUT_SHARE_PREPARE_FAILED"),
      });
    } finally {
      setPreparingShareKey((current) => (current === artifact.key ? "" : current));
    }
  }

  function shareToThirdPartyApps(artifact) {
    const file = fileCacheRef.current.get(artifact.key);
    if (!file) {
      setActionMessage({ type: "error", text: copy.preparingShare });
      return;
    }

    if (typeof navigator === "undefined" || typeof navigator.share !== "function") {
      setActionMessage({ type: "error", text: copy.nativeShareUnsupported });
      return;
    }

    const shareData = {
      title,
      text: copy.sharedFrom.replace("{filename}", file.name),
      files: [file],
    };

    if (typeof navigator.canShare === "function" && !navigator.canShare(shareData)) {
      setActionMessage({ type: "error", text: copy.nativeShareUnsupported });
      return;
    }

    let sharePromise;
    try {
      // Invoke navigator.share synchronously from the click handler so transient
      // user activation is preserved for browsers that enforce it strictly.
      sharePromise = navigator.share(shareData);
    } catch (shareError) {
      setActionMessage({
        type: "error",
        text: copy.nativeShareUnsupported,
      });
      return;
    }

    setBusyAction(`external:${artifact.key}`);
    setActionMessage(null);
    Promise.resolve(sharePromise)
      .then(() => {
        setActionMessage({ type: "success", text: copy.fileShared });
        setShareMenuKey("");
      })
      .catch((shareError) => {
        if (shareError?.name !== "AbortError") {
          setActionMessage({
            type: "error",
            text: copy.nativeShareUnsupported,
          });
        }
      })
      .finally(() => setBusyAction(""));
  }

  async function preparePrintableFile(artifact) {
    const sourceFile = await prepareArtifactFile(artifact);
    const extension = getFileExtension(sourceFile.name);
    if (extension === ".zip") throw Object.assign(new Error("PRINT_ARCHIVE_UNSUPPORTED"), { code: "PRINT_ARCHIVE_UNSUPPORTED" });
    if (!OFFICE_PRINT_EXTENSIONS.has(extension)) return sourceFile;

    const formData = new FormData();
    formData.append("file", sourceFile, sourceFile.name);
    formData.append("output_format", "pdf");
    formData.append("system_language", language === "fr" ? "french" : "english");

    const responseData = await postAnalyzerFeature("convert", formData);
    const resultNode =
      responseData?.analyzer_response?.result ||
      responseData?.response?.result ||
      responseData?.result ||
      responseData?.data ||
      responseData ||
      {};
    const preview = normalizeOutputArtifact({
      artifactUrl: actionFirstString([
        resultNode?.download_url,
        resultNode?.downloadUrl,
        responseData?.download_url,
        responseData?.downloadUrl,
      ]),
      storageKey: actionFirstString([
        resultNode?.storage_key,
        resultNode?.storageKey,
        responseData?.storage_key,
        responseData?.storageKey,
      ]),
      filename:
        actionFirstString([
          resultNode?.filename,
          resultNode?.file_name,
          resultNode?.name,
          downloadFilenameFromUrl(resultNode?.download_url || resultNode?.downloadUrl),
        ]) || `${getFileStem(sourceFile.name)}.pdf`,
      contentType: actionFirstString([
        resultNode?.content_type,
        resultNode?.contentType,
        "application/pdf",
      ]),
    });

    if (!preview) throw Object.assign(new Error("PRINT_FAILED"), { code: "PRINT_FAILED" });
    return fetchArtifactAsFile(preview);
  }

  function handlePrint(artifact) {
    if (typeof window === "undefined") return;

    const printWindow = window.open("", "_blank", "popup=yes,width=1100,height=800");
    if (!printWindow) {
      setActionMessage({ type: "error", text: copy.printPopupBlocked });
      return;
    }
    try {
      printWindow.opener = null;
    } catch {
      // Some browsers expose opener as read-only.
    }

    renderPrintMessage(printWindow, title, copy.printPreparing);
    setBusyAction(`print:${artifact.key}`);
    setActionMessage(null);

    preparePrintableFile(artifact)
      .then((file) => renderPrintableFile(printWindow, file, title, copy))
      .catch((printError) => {
        const message = resolveErrorMessage(printError, language, "PRINT_FAILED");
        renderPrintMessage(printWindow, title, message, { isError: true });
        setActionMessage({ type: "error", text: message });
      })
      .finally(() => setBusyAction(""));
  }

  async function loadMembersForOrganization(organizationId, currentUserId) {
    setMemberShareLoading(true);
    setMemberShareMessage(null);
    setSelectedMemberIds([]);
    try {
      const organizationData = await getOrganization(organizationId);
      const members = Array.isArray(organizationData?.members)
        ? organizationData.members
        : [];
      setShareMembers(
        members.filter(
          (member) =>
            member?.status === "active" &&
            !member?.is_email_invitation &&
            String(member?.user_id || "") !== String(currentUserId || ""),
        ),
      );
    } catch (organizationError) {
      setShareMembers([]);
      setMemberShareMessage({
        type: "error",
        text:
          resolveErrorMessage(organizationError, language, "ORGANIZATION_MEMBERS_LOAD_FAILED"),
      });
    } finally {
      setMemberShareLoading(false);
    }
  }

  async function openMemberShare(artifact) {
    if (!isSignedIn) {
      setActionMessage({ type: "error", text: copy.signInRequired });
      return;
    }

    const preparedFile = fileCacheRef.current.get(artifact.key);
    if (!preparedFile) {
      setActionMessage({ type: "error", text: copy.preparingShare });
      return;
    }
    if (preparedFile.size > TEAM_SHARE_MAX_FILE_BYTES) {
      setActionMessage({ type: "error", text: copy.teamFileTooLarge });
      return;
    }
    if (!TEAM_SHARE_ALLOWED_EXTENSIONS.has(getFileExtension(preparedFile.name))) {
      setActionMessage({ type: "error", text: copy.teamFileTypeUnsupported });
      return;
    }

    setMemberShareArtifact(artifact);
    setShareMenuKey("");
    setShareOrganizations([]);
    setShareOrganizationId("");
    setShareMembers([]);
    setSelectedMemberIds([]);
    setMemberShareMessage(null);
    setMemberShareLoading(true);

    try {
      const organizationsData = await getMyOrganizations();
      const currentUserId = actionFirstString([
        organizationsData?.user?.id,
        account?.user?.id,
        account?.account?.user?.id,
      ]);
      const organizations = (Array.isArray(organizationsData?.organizations)
        ? organizationsData.organizations
        : []
      ).filter((organization) =>
        ["business", "enterprise"].includes(
          String(organization?.subscription?.plan || "").toLowerCase(),
        ),
      );

      setShareUserId(currentUserId);
      setShareOrganizations(organizations);

      if (!organizations.length) {
        setMemberShareMessage({ type: "error", text: copy.noOrganizations });
        return;
      }

      const firstOrganizationId = String(organizations[0].id);
      setShareOrganizationId(firstOrganizationId);
      await loadMembersForOrganization(firstOrganizationId, currentUserId);
    } catch (organizationsError) {
      setMemberShareMessage({
        type: "error",
        text: resolveErrorMessage(organizationsError, language, "ORGANIZATION_MEMBERS_LOAD_FAILED"),
      });
    } finally {
      setMemberShareLoading(false);
    }
  }

  async function handleOrganizationChange(event) {
    const nextOrganizationId = event.target.value;
    setShareOrganizationId(nextOrganizationId);
    await loadMembersForOrganization(nextOrganizationId, shareUserId);
  }

  function toggleShareMember(userId) {
    const normalizedUserId = String(userId || "");
    if (!normalizedUserId || memberShareBusy) return;

    setSelectedMemberIds((current) => {
      if (current.includes(normalizedUserId)) {
        return current.filter((item) => item !== normalizedUserId);
      }
      if (current.length >= TEAM_SHARE_MAX_RECIPIENTS) {
        setMemberShareMessage({ type: "error", text: copy.memberLimit });
        return current;
      }
      setMemberShareMessage(null);
      return [...current, normalizedUserId];
    });
  }

  async function shareWithSelectedMembers() {
    if (!memberShareArtifact || !shareOrganizationId) return;
    if (!selectedMemberIds.length) {
      setMemberShareMessage({ type: "error", text: copy.selectRecipients });
      return;
    }

    const file = fileCacheRef.current.get(memberShareArtifact.key);
    if (!file) {
      setMemberShareMessage({ type: "error", text: copy.preparingShare });
      return;
    }
    if (file.size > TEAM_SHARE_MAX_FILE_BYTES) {
      setMemberShareMessage({ type: "error", text: copy.teamFileTooLarge });
      return;
    }
    if (!TEAM_SHARE_ALLOWED_EXTENSIONS.has(getFileExtension(file.name))) {
      setMemberShareMessage({ type: "error", text: copy.teamFileTypeUnsupported });
      return;
    }

    const recipientIds = [...selectedMemberIds];
    const [firstRecipientId, ...remainingRecipientIds] = recipientIds;
    setMemberShareBusy(true);
    setMemberShareMessage(null);

    try {
      const conversationData = await createConversation(shareOrganizationId, {
        type: "dm",
        member_user_ids: [firstRecipientId],
      });
      const conversationId = conversationData?.conversation?.id;
      if (!conversationId) {
        throw Object.assign(new Error("SECURE_CONVERSATION_RESOLVE_FAILED"), { code: "SECURE_CONVERSATION_RESOLVE_FAILED" });
      }

      const uploadResult = await sendConversationAttachment(conversationId, file, {
        caption: copy.sharedFrom.replace("{filename}", file.name),
        clientMessageId: createShareClientMessageId("artifact-share"),
      });
      const sourceMessageId = uploadResult?.message?.id;
      if (!sourceMessageId) {
        throw Object.assign(new Error("SECURE_ATTACHMENT_REFERENCE_MISSING"), { code: "SECURE_ATTACHMENT_REFERENCE_MISSING" });
      }

      if (!remainingRecipientIds.length) {
        setSelectedMemberIds([]);
        setMemberShareMessage({ type: "success", text: copy.shareSuccess });
        return;
      }

      let forwardResult;
      try {
        forwardResult = await forwardConversationMessage(
          shareOrganizationId,
          sourceMessageId,
          remainingRecipientIds,
          { clientMessageId: createShareClientMessageId("artifact-forward") },
        );
      } catch (forwardError) {
        setSelectedMemberIds(remainingRecipientIds);
        setMemberShareMessage({
          type: "warning",
          text: copy.shareOneOfMany
            .replace("{total}", String(recipientIds.length))
            .replace("{error}", resolveErrorMessage(forwardError, language, "DELIVERY_CONFIRMATION_FAILED")),
        });
        return;
      }

      const failures = Array.isArray(forwardResult?.failures)
        ? forwardResult.failures
        : [];
      if (failures.length) {
        const failedIds = failures
          .map((failure) => String(failure?.recipient_user_id || ""))
          .filter(Boolean);
        const deliveredCount = Math.max(
          1,
          1 + Number(forwardResult?.delivered_count || 0),
        );
        setSelectedMemberIds(failedIds);
        setMemberShareMessage({
          type: "warning",
          text: copy.shareManyOfMany
            .replace("{delivered}", String(deliveredCount))
            .replace("{total}", String(recipientIds.length)),
        });
        return;
      }

      setSelectedMemberIds([]);
      setMemberShareMessage({ type: "success", text: copy.shareSuccess });
    } catch (memberError) {
      setMemberShareMessage({
        type: "error",
        text: resolveErrorMessage(memberError, language, "SECURE_SHARE_FAILED"),
      });
    } finally {
      setMemberShareBusy(false);
    }
  }

  if (!artifacts.length) return null;

  return (
    <div className="mt-3 space-y-2" aria-label={copy.outputActions}>
      {artifacts.map((artifact, index) => {
        const isPreparingShare = preparingShareKey === artifact.key;
        const isPrepared =
          preparedShareKey === artifact.key || fileCacheRef.current.has(artifact.key);
        const isPrinting = busyAction === `print:${artifact.key}`;
        const isExternalSharing = busyAction === `external:${artifact.key}`;

        return (
          <div
            key={artifact.key}
            className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3"
          >
            {artifacts.length > 1 ? (
              <p
                className="mb-2 truncate text-xs font-medium app-text-muted"
                title={artifact.filename}
              >
                {index + 1}. {artifact.filename}
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => handlePrint(artifact)}
                disabled={Boolean(busyAction)}
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] px-3 py-2 text-xs font-semibold app-text-muted transition hover:border-[var(--app-accent-border)] hover:text-[var(--app-text)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isPrinting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Printer className="h-4 w-4" />
                )}
                {copy.print}
              </button>

              <button
                type="button"
                onClick={() => openShareMenu(artifact)}
                disabled={Boolean(busyAction)}
                aria-expanded={shareMenuKey === artifact.key}
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] px-3 py-2 text-xs font-semibold app-text-muted transition hover:border-[var(--app-accent-border)] hover:text-[var(--app-text)] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isPreparingShare ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Share2 className="h-4 w-4" />
                )}
                {copy.share}
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>

            {shareMenuKey === artifact.key ? (
              <div className="mt-2 grid gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-2 sm:grid-cols-2">
                <button
                  type="button"
                  onClick={() => shareToThirdPartyApps(artifact)}
                  disabled={!isPrepared || isExternalSharing}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs font-medium app-text-muted transition hover:border-[var(--app-accent-border)] hover:text-[var(--app-text)] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isExternalSharing ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Share2 className="h-4 w-4" />
                  )}
                  {isPreparingShare ? copy.preparingShare : copy.shareToApps}
                </button>
                <button
                  type="button"
                  onClick={() => openMemberShare(artifact)}
                  disabled={!isPrepared || !isSignedIn}
                  title={!isSignedIn ? copy.signInRequired : undefined}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs font-medium app-text-muted transition hover:border-[var(--app-accent-border)] hover:text-[var(--app-text)] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Users className="h-4 w-4" />
                  {copy.shareToMembers}
                </button>
              </div>
            ) : null}
          </div>
        );
      })}

      {actionMessage ? (
        <p
          className={`rounded-xl border px-3 py-2 text-xs leading-5 ${
            actionMessage.type === "error"
              ? "border-red-400/20 bg-red-400/10 text-red-100"
              : "border-emerald-400/20 bg-emerald-400/10 text-emerald-100"
          }`}
          role={actionMessage.type === "error" ? "alert" : "status"}
        >
          {actionMessage.text}
        </p>
      ) : null}

      {memberShareArtifact ? (
        <div className="rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-panel)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-[var(--app-text)]">
                {copy.memberShareTitle}
              </h3>
              <p
                className="mt-1 truncate text-xs app-text-soft"
                title={memberShareArtifact.filename}
              >
                {memberShareArtifact.filename}
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                if (memberShareBusy) return;
                setMemberShareArtifact(null);
                setMemberShareMessage(null);
                setSelectedMemberIds([]);
              }}
              disabled={memberShareBusy}
              aria-label={copy.close}
              className="rounded-lg p-1.5 app-text-soft transition hover:bg-[var(--app-surface)] hover:text-[var(--app-text)] disabled:opacity-60"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {shareOrganizations.length ? (
            <label className="mt-3 block">
              <span className="mb-1 block text-xs font-medium app-text-muted">
                {copy.organization}
              </span>
              <select
                value={shareOrganizationId}
                onChange={handleOrganizationChange}
                disabled={memberShareBusy || memberShareLoading}
                className="w-full rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-sm text-[var(--app-text)] outline-none focus:border-[var(--app-accent-border)] disabled:opacity-60"
              >
                {shareOrganizations.map((organization) => (
                  <option key={organization.id} value={String(organization.id)}>
                    {organization.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <div className="mt-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-xs font-medium app-text-muted">{copy.recipients}</span>
              <span className="text-[11px] app-text-soft">
                {selectedMemberIds.length}/{TEAM_SHARE_MAX_RECIPIENTS}
              </span>
            </div>

            {memberShareLoading ? (
              <div className="flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-3 text-xs app-text-soft">
                <Loader2 className="h-4 w-4 animate-spin" />
                {copy.preparingShare}
              </div>
            ) : shareMembers.length ? (
              <div className="max-h-52 space-y-2 overflow-y-auto pr-1">
                {shareMembers.map((member) => {
                  const memberUserId = String(member.user_id || "");
                  const checked = selectedMemberIds.includes(memberUserId);
                  const label =
                    actionFirstString([member.name, member.email]) || copy.organizationMember;
                  return (
                    <label
                      key={memberUserId}
                      className="flex cursor-pointer items-center gap-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2.5 text-sm transition hover:border-[var(--app-accent-border)]"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleShareMember(memberUserId)}
                        disabled={memberShareBusy}
                        className="h-4 w-4"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium text-[var(--app-text)]">
                          {label}
                        </span>
                        {member.email && member.email !== label ? (
                          <span className="block truncate text-xs app-text-soft">
                            {member.email}
                          </span>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </div>
            ) : shareOrganizations.length && !memberShareMessage ? (
              <p className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-3 text-xs app-text-soft">
                {copy.noMembers}
              </p>
            ) : null}
          </div>

          {memberShareMessage ? (
            <p
              className={`mt-3 rounded-xl border px-3 py-2 text-xs leading-5 ${
                memberShareMessage.type === "success"
                  ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-100"
                  : memberShareMessage.type === "warning"
                    ? "border-amber-400/20 bg-amber-400/10 text-amber-100"
                    : "border-red-400/20 bg-red-400/10 text-red-100"
              }`}
              role={memberShareMessage.type === "error" ? "alert" : "status"}
            >
              {memberShareMessage.text}
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={shareWithSelectedMembers}
              disabled={
                memberShareBusy ||
                memberShareLoading ||
                !selectedMemberIds.length ||
                !shareOrganizationId
              }
              className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-xs font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {memberShareBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Users className="h-4 w-4" />
              )}
              {memberShareBusy ? copy.sharing : copy.shareSelected}
            </button>
            <button
              type="button"
              onClick={() => {
                if (memberShareBusy) return;
                setMemberShareArtifact(null);
                setMemberShareMessage(null);
                setSelectedMemberIds([]);
              }}
              disabled={memberShareBusy}
              className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs font-medium app-text-muted transition hover:text-[var(--app-text)] disabled:opacity-60"
            >
              {copy.cancel}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}


export default function StructuredExtractionPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();

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
  const qualitySummary = previewPayload?.quality_summary || null;
  const documentWarnings = Array.isArray(previewPayload?.document_warnings)
    ? previewPayload.document_warnings
    : [];
  const previewColumns = useMemo(() => {
    const columns = [];
    for (const row of previewRows) {
      if (!row || typeof row !== "object") continue;
      for (const key of Object.keys(row)) {
        if (TECHNICAL_PREVIEW_COLUMNS.has(key)) continue;
        if (!columns.includes(key)) columns.push(key);
      }
    }
    return columns;
  }, [previewRows]);

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
        getPageRuntimeCopy("structuredExtraction", language).maxFiles.replace("{count}", String(MAX_STRUCTURED_EXTRACTION_FILES)),
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
      setError(resolveErrorMessage(submitError, language, "PROCESSING_FAILED"));
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
                            <div className="mb-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)]">
                              <p className="border-b border-[var(--app-border)] px-3 py-2 text-xs app-text-soft">
                                {replaceVars(ux.previewCoverage, {
                                  rowCount: previewRows.length,
                                  columnCount: previewColumns.length,
                                })}
                              </p>
                              <div className="max-h-96 overflow-auto">
                                <table className="min-w-full text-left text-xs app-text-muted">
                                  <thead className="border-b border-[var(--app-border)] text-[var(--app-text)]">
                                    <tr>
                                      {previewColumns.map((key) => (
                                        <th
                                          key={key}
                                          className="sticky top-0 bg-[var(--app-panel)] px-3 py-2 font-medium"
                                        >
                                          {key}
                                        </th>
                                      ))}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {previewRows.map((row, rowIndex) => (
                                      <tr
                                        key={rowIndex}
                                        className="border-b border-[var(--app-border)]"
                                      >
                                        {previewColumns.map((key) => (
                                          <td
                                            key={key}
                                            className="max-w-[320px] whitespace-pre-wrap break-words px-3 py-2 align-top"
                                          >
                                            {String(row?.[key] ?? "")}
                                          </td>
                                        ))}
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          )}

                          {selectedFieldStatusRows.length > 0 && (
                            <div className="mb-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3">
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

                          {qualitySummary ? (
                            <div className="mb-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3">
                              <div className="flex items-center justify-between gap-3">
                                <p className="text-xs font-semibold text-[var(--app-accent-text)]">
                                  {ux.extractionQuality}
                                </p>
                                <span className="rounded-full border border-[var(--app-border)] px-2 py-0.5 text-xs app-text-muted">
                                  {qualitySummary.score == null
                                    ? ux.notApplicable
                                    : `${Math.round(Number(qualitySummary.score) * 100)}%`}
                                </span>
                              </div>
                              <p className="mt-1 text-xs app-text-soft">
                                {ux.qualityHelp}
                              </p>
                            </div>
                          ) : null}

                          {documentWarnings.length > 0 ? (
                            <div className="mb-3 rounded-xl border border-amber-400/25 bg-amber-400/10 p-3">
                              <p className="text-xs font-semibold text-amber-100">
                                {ux.reviewNotes}
                              </p>
                              <ul className="mt-2 grid list-disc gap-1 pl-5 text-xs leading-5 text-amber-100/85">
                                {documentWarnings.flatMap((document) =>
                                  (document.warnings || []).map((warning, index) => (
                                    <li key={`${document.source_document_index}-${index}-${warning}`}>
                                      {document.filename || getPageRuntimeCopy("structuredExtraction", language).documentLabel.replace("{number}", String(Number(document.source_document_index || 0) + 1))}: {warning}
                                    </li>
                                  )),
                                )}
                              </ul>
                            </div>
                          ) : null}

                          <details className="rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3">
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
                              <ProductionOutputActions
                                artifactUrl={downloadInfo.downloadUrl}
                                storageKey={downloadInfo.storageKey}
                                filename={downloadInfo.filename}
                                contentType={downloadInfo.contentType}
                                title={getPageRuntimeCopy("structuredExtraction", language).outputTitle}
                                language={language}
                                account={account}
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
