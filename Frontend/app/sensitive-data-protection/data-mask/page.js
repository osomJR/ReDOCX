"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  EyeClosed,
  Download,
  Printer,
  Share2,
  Users,
  Loader2,
  ChevronDown,
  X,
} from "lucide-react";
import {
  commonTranslations,
  dataMaskPageTranslations,
  dataProtectionDocumentTypeOptions,
  dataProtectionSensitiveLabelTranslations,
  processedOutputActionTranslations,
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
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 25;
const DOCUMENT_TYPES = dataProtectionDocumentTypeOptions;

const DOCUMENT_TYPE_VALUES = new Set(DOCUMENT_TYPES.map((item) => item.value));

function getDocumentTypeLabel(value, language = "en") {
  const item = DOCUMENT_TYPES.find((candidate) => candidate.value === value);
  return item?.labels?.[language] || item?.labels?.en || value || "";
}
const SENSITIVE_LABELS = dataProtectionSensitiveLabelTranslations;

const ALL_SENSITIVE_TARGETS = Object.freeze([
  "name",
  "email_address",
  "phone_number",
  "account_number",
  "card_number",
  "national_id",
  "tax_id",
  "passport_number",
  "contact_address",
  "date_of_birth",
  "age",
  "signature",
]);

const BILLING_SENSITIVE_TARGETS = Object.freeze([
  "name",
  "email_address",
  "phone_number",
  "account_number",
  "card_number",
  "tax_id",
  "contact_address",
  "signature",
]);

const IDENTITY_SENSITIVE_TARGETS = Object.freeze([
  "name",
  "national_id",
  "tax_id",
  "passport_number",
  "contact_address",
  "date_of_birth",
  "age",
  "signature",
]);

const SUPPORTED_BY_DOCUMENT_TYPE = Object.freeze({
  ...Object.fromEntries(
    DOCUMENT_TYPES.map(({ value }) => [value, ALL_SENSITIVE_TARGETS]),
  ),
  invoice: BILLING_SENSITIVE_TARGETS,
  receipt: BILLING_SENSITIVE_TARGETS,
  utility_telecom_document: BILLING_SENSITIVE_TARGETS,
  id_document: IDENTITY_SENSITIVE_TARGETS,
});

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return filename || "masked-file";
  return filename.slice(0, lastDot) || "masked-file";
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

function getInputTypeLabel(ext) {
  if (ext === ".pdf") return "PDF";
  if (ext === ".docx") return "DOCX";
  if (ext === ".jpg") return "JPG";
  if (ext === ".jpeg") return "JPEG";
  if (ext === ".png") return "PNG";
  return "Unknown";
}

function parseDelimitedItems(value = "") {
  return value
    .split(/[\n,]/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function extractResponseMessage(responseData, fallbackMessage = "") {
  return (
    pickFirstString([
      responseData?.detail?.message,
      responseData?.detail?.error,
      responseData?.detail,
      responseData?.message,
      responseData?.error,
      responseData?.result?.message,
      responseData?.data?.message,
      responseData?.analyzer_response?.result?.message,
    ]) || fallbackMessage
  );
}

function cleanArtifactStorageKey(value) {
  if (typeof value !== "string" || !value.trim()) return "";

  let key = value.trim().replaceAll("\\", "/");

  try {
    const parsedUrl = new URL(
      key,
      typeof window !== "undefined" ? window.location.origin : "http://local",
    );

    key = parsedUrl.pathname;
  } catch {
    // Keep key as-is when it is not URL-shaped.
  }

  const prefixes = [
    "/api/analyzer/artifacts/",
    "api/analyzer/artifacts/",
    "/api/v1/analyzer/artifacts/",
    "api/v1/analyzer/artifacts/",
    "/artifacts/",
    "artifacts/",
  ];

  let changed = true;

  while (changed) {
    changed = false;

    for (const prefix of prefixes) {
      if (key.startsWith(prefix)) {
        key = key.slice(prefix.length);
        changed = true;
      }
    }
  }

  return key.replace(/^\/+/, "");
}

function buildArtifactDownloadUrl(storageKey) {
  const cleanStorageKey = cleanArtifactStorageKey(storageKey);
  if (!cleanStorageKey) return "";

  return `/api/analyzer/artifacts/${cleanStorageKey}`;
}

function isKnownArtifactPath(value = "") {
  return (
    value.startsWith("/api/analyzer/artifacts/") ||
    value.startsWith("api/analyzer/artifacts/") ||
    value.startsWith("/api/v1/analyzer/artifacts/") ||
    value.startsWith("api/v1/analyzer/artifacts/") ||
    value.startsWith("/artifacts/") ||
    value.startsWith("artifacts/")
  );
}

function normalizeArtifactDownloadUrl(url = "", storageKey = "") {
  const raw = String(url || "").trim();

  if (raw) {
    try {
      const parsedUrl = new URL(
        raw,
        typeof window !== "undefined" ? window.location.origin : "http://local",
      );

      if (isKnownArtifactPath(parsedUrl.pathname)) {
        const normalizedPath = buildArtifactDownloadUrl(parsedUrl.pathname);
        return normalizedPath
          ? `${normalizedPath}${parsedUrl.search}${parsedUrl.hash}`
          : "";
      }

      if (/^https?:\/\//i.test(raw)) {
        return raw;
      }
    } catch {
      // Fall through to plain path normalization.
    }
  }

  if (isKnownArtifactPath(raw)) {
    return buildArtifactDownloadUrl(raw);
  }

  if (raw) {
    return raw;
  }

  return storageKey ? buildArtifactDownloadUrl(storageKey) : "";
}

function downloadFilenameFromUrl(url = "") {
  try {
    const value = new URL(
      String(url || ""),
      typeof window !== "undefined" ? window.location.origin : "http://local",
    ).searchParams.get("download_name");

    return value || "";
  } catch {
    return "";
  }
}

function extractDownloadInfo(responseData, fallbackFilename = "") {
  const artifact = responseData?.artifact || {};
  const result =
    responseData?.analyzer_response?.result || responseData?.result || {};

  const storageKey = pickFirstString([
    artifact?.storage_key,
    artifact?.storageKey,
    result?.storage_key,
    result?.storageKey,
  ]);

  const downloadUrl = normalizeArtifactDownloadUrl(
    pickFirstString([
      artifact?.download_url,
      artifact?.downloadUrl,
      result?.download_url,
      result?.downloadUrl,
    ]),
    storageKey,
  );

  const filename = pickFirstString([
    downloadFilenameFromUrl(downloadUrl),
    artifact?.original_artifact_name,
    artifact?.artifact_name,
    result?.filename,
    fallbackFilename,
  ]);

  const outputFormat = pickFirstString([
    result?.output_format,
    result?.outputFormat,
  ]);

  return {
    storageKey,
    downloadUrl,
    filename,
    outputFormat,
    fileSizeMb: result?.file_size_mb ?? result?.fileSizeMb ?? null,
    contentType: pickFirstString([
      artifact?.content_type,
      artifact?.contentType,
    ]),
  };
}

function withInlineDisposition(url) {
  if (!url) return "";

  try {
    const parsed = new URL(
      url,
      typeof window !== "undefined" ? window.location.origin : "http://local",
    );
    parsed.searchParams.set("disposition", "inline");

    if (/^https?:\/\//i.test(url)) {
      return parsed.toString();
    }

    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}disposition=inline`;
  }
}

function resolveProcessedPreviewUrl(
  responseData,
  resolvedDownload,
  inputExtension,
) {
  if (!canInlinePreview(inputExtension) && inputExtension !== ".docx") {
    return "";
  }

  const previewUrl = extractPreviewUrl(responseData);

  if (inputExtension === ".docx") {
    return previewUrl ? withInlineDisposition(previewUrl) : "";
  }

  return resolvedDownload?.downloadUrl
    ? withInlineDisposition(resolvedDownload.downloadUrl)
    : "";
}

function extractPreviewUrl(responseData) {
  const preview = responseData?.preview_artifact || {};
  const storageKey = pickFirstString([
    preview?.storage_key,
    preview?.storageKey,
  ]);

  return normalizeArtifactDownloadUrl(
    pickFirstString([preview?.download_url, preview?.downloadUrl]),
    storageKey,
  );
}

function canInlinePreview(ext) {
  return [".pdf", ".jpg", ".jpeg", ".png"].includes(ext);
}

function candidateId(candidate) {
  return (
    candidate?.id ||
    `${candidate?.label || ""}::${candidate?.quote || ""}::${candidate?.source || ""}`
  );
}

function candidateQuote(candidate) {
  return String(candidate?.quote || "").trim();
}

function buildDefaultApprovedCandidateIds(candidates = []) {
  // Detection and format validation are owned by the backend. Revalidating
  // findings in the browser caused valid international alphanumeric IDs and
  // uncommon card formats to be silently excluded from the final operation.
  // Default every non-empty server finding to protected; the reviewer can
  // explicitly deselect a false positive.
  return new Set(
    candidates
      .filter((candidate) => candidateQuote(candidate))
      .map(candidateId),
  );
}

function buildReviewExclusions(reviewCandidates, approvedCandidateIds) {
  const deselectedQuotes = reviewCandidates
    .filter((candidate) => !approvedCandidateIds.has(candidateId(candidate)))
    .map((candidate) => (candidate?.quote || "").trim())
    .filter(Boolean);

  return [...new Set(deselectedQuotes)];
}

function appendCustomMaskItems(formData, customMaskText) {
  for (const item of parseDelimitedItems(customMaskText)) {
    formData.append("custom_redactions", item);
  }
}

function customMaskItemCount(customMaskText) {
  return parseDelimitedItems(customMaskText).length;
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
    .replace(
      /[\\/\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/gu,
      "_",
    )
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

  const responseType = String(blob.type || "")
    .split(";", 1)[0]
    .trim();
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
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
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

function renderPrintMessage(
  printWindow,
  title,
  message,
  { isError = false } = {},
) {
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
  const contentType = String(file.type || "")
    .split(";", 1)[0]
    .toLowerCase();
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
  language: languageOverride = "",
  account: accountOverride = null,
}) {
  const { language: contextLanguage } = useLanguage();
  const accountContext = useAccount();
  const language = languageOverride || contextLanguage || "en";
  const account = accountOverride || accountContext;
  const copy = OUTPUT_ACTION_COPY[language] || OUTPUT_ACTION_COPY.en;
  const fileCacheRef = useRef(new Map());
  const filePromiseCacheRef = useRef(new Map());
  const directArtifact = useMemo(
    () =>
      normalizeOutputArtifact({
        artifactUrl,
        storageKey,
        filename,
        contentType,
      }) || normalizeTextOutputArtifact(textContent, textFilename),
    [artifactUrl, storageKey, filename, contentType, textContent, textFilename],
  );
  const artifacts = useMemo(
    () =>
      directArtifact ? [directArtifact] : collectDownloadableArtifacts(result),
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
    setPreparedShareKey(
      fileCacheRef.current.has(artifact.key) ? artifact.key : "",
    );
    setActionMessage(null);
    if (fileCacheRef.current.has(artifact.key)) return;

    setPreparingShareKey(artifact.key);
    try {
      await prepareArtifactFile(artifact);
      setPreparedShareKey(artifact.key);
    } catch (shareError) {
      setActionMessage({
        type: "error",
        text:
          resolveErrorMessage(shareError, language, "OUTPUT_SHARE_PREPARE_FAILED"),
      });
    } finally {
      setPreparingShareKey((current) =>
        current === artifact.key ? "" : current,
      );
    }
  }

  function shareToThirdPartyApps(artifact) {
    const file = fileCacheRef.current.get(artifact.key);
    if (!file) {
      setActionMessage({ type: "error", text: copy.preparingShare });
      return;
    }

    if (
      typeof navigator === "undefined" ||
      typeof navigator.share !== "function"
    ) {
      setActionMessage({ type: "error", text: copy.nativeShareUnsupported });
      return;
    }

    const shareData = {
      title,
      text: copy.sharedFrom.replace("{filename}", file.name),
      files: [file],
    };

    if (
      typeof navigator.canShare === "function" &&
      !navigator.canShare(shareData)
    ) {
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
    formData.append(
      "system_language",
      language === "fr" ? "french" : "english",
    );

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
          downloadFilenameFromUrl(
            resultNode?.download_url || resultNode?.downloadUrl,
          ),
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

    const printWindow = window.open(
      "",
      "_blank",
      "popup=yes,width=1100,height=800",
    );
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
    if (
      !TEAM_SHARE_ALLOWED_EXTENSIONS.has(getFileExtension(preparedFile.name))
    ) {
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
      const organizations = (
        Array.isArray(organizationsData?.organizations)
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
      setMemberShareMessage({
        type: "error",
        text: copy.teamFileTypeUnsupported,
      });
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

      const uploadResult = await sendConversationAttachment(
        conversationId,
        file,
        {
          caption: copy.sharedFrom.replace("{filename}", file.name),
          clientMessageId: createShareClientMessageId("artifact-share"),
        },
      );
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
          preparedShareKey === artifact.key ||
          fileCacheRef.current.has(artifact.key);
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
              <span className="text-xs font-medium app-text-muted">
                {copy.recipients}
              </span>
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

export default function DataMaskPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const documentTypeDetectionAbortRef = useRef(null);
  const fileSelectionSequenceRef = useRef(0);
  const { language } = useLanguage();
  const sensitiveLabels = SENSITIVE_LABELS[language] || SENSITIVE_LABELS.en;

  const common = commonTranslations[language] || commonTranslations.en;
  const t = dataMaskPageTranslations[language] || dataMaskPageTranslations.en;

  const [selectedFile, setSelectedFile] = useState(null);
  const [documentType, setDocumentType] = useState("general_document");
  const [targetData, setTargetData] = useState(
    SUPPORTED_BY_DOCUMENT_TYPE.general_document,
  );
  const [isDetectingDocumentType, setIsDetectingDocumentType] = useState(false);
  const [documentTypeDetection, setDocumentTypeDetection] = useState(null);
  const [customMaskText, setCustomMaskText] = useState("");
  const [error, setError] = useState("");
  const [isReviewing, setIsReviewing] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [resultSummary, setResultSummary] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);
  const [stage, setStage] = useState("edit");
  const [reviewCandidates, setReviewCandidates] = useState([]);
  const [approvedCandidateIds, setApprovedCandidateIds] = useState(new Set());
  const [processedPreviewUrl, setProcessedPreviewUrl] = useState("");

  const inputExtension = useMemo(() => {
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [selectedFile]);

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;
    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;
    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const supportedTargets = useMemo(() => {
    return SUPPORTED_BY_DOCUMENT_TYPE[documentType] || [];
  }, [documentType]);

  const documentTypeConfidencePercent = useMemo(() => {
    const value = Number(documentTypeDetection?.confidence);
    return Number.isFinite(value) ? Math.round(value * 100) : null;
  }, [documentTypeDetection]);

  const approvedCount = reviewCandidates.filter((candidate) =>
    approvedCandidateIds.has(candidateId(candidate)),
  ).length;

  const deselectedCount = reviewCandidates.length - approvedCount;
  const isBusy = isReviewing || isFinalizing || stage === "done";

  useEffect(() => {
    setTargetData(SUPPORTED_BY_DOCUMENT_TYPE[documentType] || []);
  }, [documentType]);

  useEffect(() => {
    return () => documentTypeDetectionAbortRef.current?.abort();
  }, []);

  function resetResultState() {
    setResultSummary("");
    setDownloadInfo(null);
    setProcessedPreviewUrl("");
    setReviewCandidates([]);
    setApprovedCandidateIds(new Set());
    setStage("edit");
  }

  function rejectFile(message) {
    fileSelectionSequenceRef.current += 1;
    documentTypeDetectionAbortRef.current?.abort();
    documentTypeDetectionAbortRef.current = null;
    setIsDetectingDocumentType(false);
    setDocumentTypeDetection(null);
    setDocumentType("general_document");
    setSelectedFile(null);
    setError(message);
    resetResultState();
  }

  async function detectDocumentType(file) {
    documentTypeDetectionAbortRef.current?.abort();
    const controller = new AbortController();
    documentTypeDetectionAbortRef.current = controller;
    setIsDetectingDocumentType(true);
    setDocumentTypeDetection(null);

    try {
      const formData = new FormData();
      formData.append("file", file);

      const response = await fetch(
        "/api/analyzer/data-mask/detect-document-type",
        {
          method: "POST",
          credentials: "include",
          body: formData,
          signal: controller.signal,
        },
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          "PROCESSING_FAILED",
        );
      }

      const detectedType = String(data?.document_type || "");
      if (!DOCUMENT_TYPE_VALUES.has(detectedType)) {
        throw Object.assign(new Error("DOCUMENT_TYPE_UNSUPPORTED"), { code: "DOCUMENT_TYPE_UNSUPPORTED" });
      }

      if (documentTypeDetectionAbortRef.current !== controller) return;

      setDocumentType(detectedType);
      setDocumentTypeDetection({
        status: data?.is_fallback ? "fallback" : "detected",
        confidence: Number(data?.confidence),
      });
    } catch (detectionError) {
      if (detectionError?.name === "AbortError") return;
      if (documentTypeDetectionAbortRef.current !== controller) return;

      setDocumentType("general_document");
      setDocumentTypeDetection({ status: "fallback", confidence: null });
    } finally {
      if (documentTypeDetectionAbortRef.current === controller) {
        documentTypeDetectionAbortRef.current = null;
        setIsDetectingDocumentType(false);
      }
    }
  }

  async function handlePickedFile(file) {
    if (isBusy) return;
    if (!file) return;

    const selectionId = ++fileSelectionSequenceRef.current;
    documentTypeDetectionAbortRef.current?.abort();

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.documentWithImages,
    );
    if (selectionId !== fileSelectionSequenceRef.current) return;
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
    setDocumentType("general_document");
    setDocumentTypeDetection(null);
    resetResultState();
    void detectDocumentType(file);
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0];
    handlePickedFile(file);
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    if (isBusy) return;
    const file = event.dataTransfer.files?.[0];
    handlePickedFile(file);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function toggleTarget(value) {
    if (isBusy || isDetectingDocumentType) return;
    setTargetData((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
    setError("");
    resetResultState();
  }

  function toggleCandidate(candidate) {
    if (isBusy || stage !== "review") return;
    const id = candidateId(candidate);
    setApprovedCandidateIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function handleDownload() {
    if (!downloadInfo?.downloadUrl) return;

    const link = document.createElement("a");
    link.href = downloadInfo.downloadUrl;
    link.download = downloadInfo.filename || "masked-file";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  async function handleProcessAndReview(event) {
    event.preventDefault();

    if (stage === "done" || isDetectingDocumentType) return;

    if (!selectedFile) {
      setError(t.chooseFileToMask);
      return;
    }

    if (!targetData.length) {
      setError(t.maskingFailed);
      return;
    }

    setIsReviewing(true);
    setError("");
    setResultSummary("");
    setDownloadInfo(null);
    setProcessedPreviewUrl("");

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("document_type", documentType);
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      for (const item of targetData) {
        formData.append("target_data", item);
      }

      appendCustomMaskItems(formData, customMaskText);

      const response = await fetch("/api/analyzer/data-mask/review", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(extractResponseMessage(responseData, t.maskingFailed));
      }

      const resolvedDownload = extractDownloadInfo(
        responseData,
        `${getFileStem(selectedFile.name)}_masked${inputExtension}`,
      );

      const candidates = Array.isArray(responseData?.candidates)
        ? responseData.candidates
        : [];

      setReviewCandidates(candidates);
      setApprovedCandidateIds(buildDefaultApprovedCandidateIds(candidates));
      setDownloadInfo(resolvedDownload);
      setProcessedPreviewUrl(
        resolveProcessedPreviewUrl(
          responseData,
          resolvedDownload,
          inputExtension,
        ),
      );
      setStage("review");

      const summaryLines = [
        t.provisionalReady,
        "",
        `${t.inputFile}: ${selectedFile.name}`,
        `${t.inputExtension}: ${inputExtension}`,
        `${t.outputExtension}: ${inputExtension}`,
        `${t.documentTypeResult}: ${getDocumentTypeLabel(documentType, language)}`,
        `${t.reviewItemsLabel}: ${candidates.length}`,
        `${t.selectedTargetsLabel} ${targetData.length}`,
        "",
        t.reviewHint,
      ];

      setResultSummary(summaryLines.join("\n"));
    } catch (submitError) {
      setError(resolveErrorMessage(submitError, language, "PROCESSING_FAILED"));
    } finally {
      setIsReviewing(false);
    }
  }

  async function handleFinalize() {
    if (stage !== "review" || isFinalizing) return;
    if (!selectedFile) {
      setError(t.chooseFileToMask);
      return;
    }

    setIsFinalizing(true);
    setError("");

    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("document_type", documentType);
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      for (const item of targetData) {
        formData.append("target_data", item);
      }

      const exclusions = buildReviewExclusions(
        reviewCandidates,
        approvedCandidateIds,
      );

      for (const item of exclusions) {
        formData.append("review_exclusions", item);
      }

      appendCustomMaskItems(formData, customMaskText);

      const response = await fetch("/api/analyzer/data-mask", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(extractResponseMessage(responseData, t.maskingFailed));
      }

      const resolvedDownload = extractDownloadInfo(
        responseData,
        `${getFileStem(selectedFile.name)}_masked${inputExtension}`,
      );

      setDownloadInfo(resolvedDownload);
      setProcessedPreviewUrl(
        resolveProcessedPreviewUrl(
          responseData,
          resolvedDownload,
          inputExtension,
        ),
      );
      setStage("done");

      const backendMessage = extractResponseMessage(responseData);
      const summaryLines = [
        t.finalReady,
        "",
        `${t.inputFile}: ${selectedFile.name}`,
        `${t.inputExtension}: ${inputExtension}`,
        `${t.outputExtension}: ${inputExtension}`,
        `${t.documentTypeResult}: ${getDocumentTypeLabel(documentType, language)}`,
        `${t.reviewItemsLabel}: ${reviewCandidates.length}`,
        `${t.approvedCountLabel}: ${approvedCount}`,
        `${t.deselectedCountLabel}: ${deselectedCount}`,
        `${t.exclusionsCount}: ${
          buildReviewExclusions(reviewCandidates, approvedCandidateIds).length
        }`,
        `${t.customMaskCount}: ${customMaskItemCount(customMaskText)}`,
        `${t.processedFile}: ${
          resolvedDownload.filename ||
          `${getFileStem(selectedFile.name)}_masked${inputExtension}`
        }`,
      ];

      if (
        resolvedDownload.fileSizeMb !== null &&
        resolvedDownload.fileSizeMb !== undefined
      ) {
        summaryLines.push(`File size: ${resolvedDownload.fileSizeMb} MB`);
      }

      summaryLines.push("");
      summaryLines.push(
        resolvedDownload.downloadUrl ? t.outputReadyText : t.missingDownloadUrl,
      );
      summaryLines.push("");
      summaryLines.push(backendMessage || t.rulesApplied);

      setResultSummary(summaryLines.join("\n"));
    } catch (submitError) {
      setError(resolveErrorMessage(submitError, language, "PROCESSING_FAILED"));
    } finally {
      setIsFinalizing(false);
    }
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto max-w-[1600px] px-3 py-3 md:px-5 md:py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => router.push("/")}
              className="inline-flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
            >
              <ArrowLeft className="h-4 w-4" />
              {common.back}
            </button>

            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-3 py-1.5 text-xs font-medium text-[var(--app-accent-text)] backdrop-blur sm:text-sm">
              <Sparkles className="h-4 w-4" />
              {t.badge}
            </div>
          </div>

          <section className="mb-4 flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
            <h1 className="text-2xl font-semibold tracking-tight text-[var(--app-text)] sm:text-3xl">
              {t.title}
            </h1>
            <p className="mt-2 max-w-2xl text-xs leading-5 app-text-muted md:text-sm">
              {t.description}
            </p>
          </section>

          <section className="grid gap-4 lg:grid-cols-[minmax(380px,0.9fr)_minmax(520px,1.1fr)]">
            <form
              onSubmit={handleProcessAndReview}
              className="relative rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3 backdrop-blur-xl md:p-4 lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative">
                <div
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  className={`rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-3 text-center transition ${
                    isBusy
                      ? "cursor-not-allowed opacity-60"
                      : "hover:border-[var(--app-border-strong)] hover:bg-[var(--app-surface-strong)]"
                  }`}
                >
                  <div className="mx-auto mb-2 flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <Upload className="h-5 w-5 text-cyan-300" />
                  </div>

                  <h2 className="text-base font-semibold text-[var(--app-text)]">
                    {t.uploadTitle}
                  </h2>
                  <p className="mt-1 text-xs leading-5 app-text-muted">
                    {t.allowedFileInputs}
                  </p>

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    onChange={handleFileChange}
                    disabled={isBusy}
                    className="hidden"
                  />

                  <button
                    type="button"
                    onClick={() => {
                      if (!isBusy) fileInputRef.current?.click();
                    }}
                    disabled={isBusy}
                    className={`mt-3 rounded-xl px-4 py-2 text-sm font-semibold transition ${
                      isBusy
                        ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                        : "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                    }`}
                  >
                    {common.chooseFile}
                  </button>
                </div>

                {selectedFile && isValidFile && (
                  <div className="mt-2 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-2.5">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-300" />
                      <div>
                        <p className="font-medium text-emerald-100">
                          {t.fileAcceptedLabel}
                        </p>
                        <p className="mt-0.5 text-xs text-emerald-100/80">
                          {selectedFile.name} • {formatBytes(selectedFile.size)}
                        </p>
                        <p className="mt-0.5 text-xs text-emerald-100/80">
                          {t.fileTypeLabel} {getInputTypeLabel(inputExtension)}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                <div className="mt-3 grid gap-3 md:grid-cols-2">
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                    <label className="block text-sm font-medium app-text-muted">
                      {t.docTypeLabel}
                    </label>
                    <select
                      value={documentType}
                      onChange={(e) => {
                        if (isBusy || isDetectingDocumentType) return;
                        setDocumentType(e.target.value);
                        setDocumentTypeDetection(null);
                        setError("");
                        resetResultState();
                      }}
                      disabled={isBusy || isDetectingDocumentType}
                      className="mt-2 w-full rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-2 text-sm text-[var(--app-text)] outline-none transition disabled:cursor-not-allowed disabled:opacity-50 focus:border-[var(--app-accent-border)]"
                    >
                      {DOCUMENT_TYPES.map((item) => (
                        <option
                          key={item.value}
                          value={item.value}
                          className="bg-[var(--app-panel)]"
                        >
                          {item.labels?.[language] || item.labels.en}
                        </option>
                      ))}
                    </select>
                    {selectedFile && (
                      <p
                        aria-live="polite"
                        className="mt-2 text-xs app-text-soft"
                      >
                        {isDetectingDocumentType
                          ? t.detectingDocumentType
                          : documentTypeDetection?.status === "detected" &&
                              documentTypeConfidencePercent !== null
                            ? replaceVars(t.documentTypeAutoDetected, {
                                confidence: documentTypeConfidencePercent,
                              })
                            : documentTypeDetection?.status === "fallback"
                              ? t.documentTypeDetectionFallback
                              : null}
                      </p>
                    )}
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                    <label className="block text-sm font-medium app-text-muted">
                      {t.exclusionsLabel}
                    </label>
                    <textarea
                      value={customMaskText}
                      onChange={(e) => {
                        if (isBusy) return;
                        setCustomMaskText(e.target.value);
                        setError("");
                        resetResultState();
                      }}
                      placeholder={t.exclusionsPlaceholder}
                      rows={2}
                      disabled={isBusy}
                      className="mt-2 w-full rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-2 text-sm leading-5 text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] disabled:cursor-not-allowed disabled:opacity-50 focus:border-[var(--app-accent-border)]"
                    />
                  </div>
                </div>

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                  <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium app-text-muted">
                      {t.sensitiveTargetsLabel}
                    </p>

                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => {
                          if (isBusy || isDetectingDocumentType) return;
                          setTargetData(supportedTargets);
                          setError("");
                          resetResultState();
                        }}
                        disabled={isBusy || isDetectingDocumentType}
                        className="rounded-lg border border-[var(--app-border)] bg-[var(--app-surface)] px-2.5 py-1.5 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                      >
                        {t.selectAll}
                      </button>

                      <button
                        type="button"
                        onClick={() => {
                          if (isBusy || isDetectingDocumentType) return;
                          setTargetData([]);
                          setError("");
                          resetResultState();
                        }}
                        disabled={isBusy || isDetectingDocumentType}
                        className="rounded-lg border border-[var(--app-border)] bg-[var(--app-surface)] px-2.5 py-1.5 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                      >
                        {t.clearAll}
                      </button>
                    </div>
                  </div>

                  <div className="grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
                    {supportedTargets.map((item) => {
                      const checked = targetData.includes(item);
                      return (
                        <label
                          key={item}
                          className={`flex items-center gap-2 rounded-xl border px-2.5 py-1.5 text-xs transition ${
                            isBusy
                              ? "cursor-not-allowed opacity-60"
                              : "cursor-pointer"
                          } ${
                            checked
                              ? "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-text)]"
                              : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:bg-[var(--app-surface-strong)]"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleTarget(item)}
                            disabled={isBusy || isDetectingDocumentType}
                            className="h-4 w-4 rounded border-[var(--app-border)] bg-transparent disabled:cursor-not-allowed"
                          />
                          <span>{sensitiveLabels[item] || item}</span>
                        </label>
                      );
                    })}
                  </div>
                  <p className="mt-2 text-xs leading-5 app-text-muted">
                    {t.coverageNote}
                  </p>
                </div>

                {error && (
                  <div className="mt-2 rounded-2xl border border-red-400/20 bg-red-400/10 p-2.5">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                )}

                <div className="sticky bottom-0 z-10 -mx-3 mt-3 flex flex-wrap items-center gap-3 border-t border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-3 backdrop-blur md:-mx-4 md:px-4">
                  <button
                    type="submit"
                    disabled={
                      isReviewing ||
                      isFinalizing ||
                      isDetectingDocumentType ||
                      !selectedFile ||
                      !isValidFile ||
                      !documentType ||
                      targetData.length === 0
                    }
                    className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                      !isReviewing &&
                      !isFinalizing &&
                      !isDetectingDocumentType &&
                      selectedFile &&
                      isValidFile &&
                      documentType &&
                      targetData.length > 0
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                        : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                    }`}
                  >
                    {isReviewing ? t.reviewing : t.processAndReview}
                  </button>

                  {stage === "review" && (
                    <button
                      type="button"
                      onClick={handleFinalize}
                      disabled={isFinalizing}
                      className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                        !isFinalizing
                          ? "bg-cyan-300 text-[var(--app-button-text)] hover:scale-[1.02]"
                          : "cursor-not-allowed bg-cyan-300/30 app-text-soft"
                      }`}
                    >
                      {isFinalizing ? t.finalizing : t.finalizeAction}
                    </button>
                  )}
                </div>
              </div>
            </form>

            <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)]">
              <div className="rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3 backdrop-blur-xl md:p-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
                <div className="mb-3 flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <EyeClosed className="h-5 w-5 text-cyan-300" />
                  </div>
                  <div>
                    <h2 className="text-base font-semibold text-[var(--app-text)]">
                      {t.resultTitle}
                    </h2>
                  </div>
                </div>

                <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-2.5">
                  {downloadInfo?.downloadUrl && stage === "done" && (
                    <div className="mb-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-2.5">
                      <div className="flex items-start gap-3">
                        <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-300" />
                        <div className="flex-1">
                          <p className="font-medium text-emerald-100">
                            {stage === "done"
                              ? t.finalReady
                              : t.provisionalReady}
                          </p>
                          <p className="mt-0.5 text-xs text-emerald-100/80">
                            {downloadInfo.filename}
                          </p>
                        </div>

                        <button
                          type="button"
                          onClick={handleDownload}
                          className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
                        >
                          <Download className="h-4 w-4" />
                          {common.download}
                        </button>
                      </div>
                      <ProductionOutputActions
                        artifactUrl={downloadInfo.downloadUrl}
                        filename={downloadInfo.filename}
                        title={getPageRuntimeCopy("dataMask", language).outputTitle}
                      />
                    </div>
                  )}

                  {processedPreviewUrl &&
                    [".pdf", ".docx"].includes(inputExtension) && (
                      <div className="mb-3">
                        <p className="mb-2 text-sm font-medium app-text-muted">
                          {t.processedPreviewTitle}
                        </p>
                        <div className="overflow-hidden rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)]">
                          <iframe
                            src={processedPreviewUrl}
                            title={getPageRuntimeCopy("dataMask", language).previewTitle}
                            className="h-[38vh] min-h-[250px] w-full"
                          />
                        </div>
                      </div>
                    )}

                  {processedPreviewUrl &&
                    [".jpg", ".jpeg", ".png"].includes(inputExtension) && (
                      <div className="mb-3">
                        <p className="mb-2 text-sm font-medium app-text-muted">
                          {t.processedPreviewTitle}
                        </p>
                        <div className="overflow-hidden rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-2">
                          <image
                            src={processedPreviewUrl}
                            alt={getPageRuntimeCopy("dataMask", language).previewTitle}
                            className="max-h-[38vh] w-full rounded-xl object-contain"
                          />
                        </div>
                      </div>
                    )}

                  {!processedPreviewUrl &&
                    inputExtension === ".docx" &&
                    stage !== "edit" && (
                      <div className="mb-3 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-3 text-sm text-amber-100">
                        {t.docxPreviewNotice}
                      </div>
                    )}

                  {stage === "review" && (
                    <div className="mb-3 space-y-3">
                      <div className="rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-3">
                        <p className="font-medium text-[var(--app-accent-text)]">
                          {t.reviewTitle}
                        </p>
                        <p className="mt-1 text-sm text-[var(--app-accent-text)]">
                          {t.reviewHint}
                        </p>
                      </div>

                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() =>
                            setApprovedCandidateIds(
                              buildDefaultApprovedCandidateIds(
                                reviewCandidates,
                              ),
                            )
                          }
                          className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                        >
                          {t.approveAll}
                        </button>

                        <button
                          type="button"
                          disabled={isBusy}
                          onClick={() => setApprovedCandidateIds(new Set())}
                          className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                        >
                          {t.clearApproved}
                        </button>
                      </div>

                      <div className="grid gap-2 sm:grid-cols-2">
                        <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                          <p className="text-sm font-medium app-text-muted">
                            {t.approvedCountLabel}
                          </p>
                          <p className="mt-1 text-sm app-text-muted">
                            {approvedCount}
                          </p>
                        </div>
                        <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                          <p className="text-sm font-medium app-text-muted">
                            {t.deselectedCountLabel}
                          </p>
                          <p className="mt-1 text-sm app-text-muted">
                            {deselectedCount}
                          </p>
                        </div>
                      </div>

                      <div className="max-h-[min(28vh,240px)] space-y-2 overflow-y-auto pr-1">
                        {reviewCandidates.length ? (
                          reviewCandidates.map((candidate) => {
                            const checked = approvedCandidateIds.has(
                              candidateId(candidate),
                            );
                            return (
                              <label
                                key={candidateId(candidate)}
                                className={`block rounded-xl border p-3 transition ${
                                  checked
                                    ? "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)]"
                                    : "border-[var(--app-border)] bg-[var(--app-surface)]"
                                }`}
                              >
                                <div className="flex items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={isBusy}
                                    onChange={() => toggleCandidate(candidate)}
                                    className="mt-1 h-4 w-4"
                                  />

                                  <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                      <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-2 py-1 text-xs app-text-muted">
                                        {sensitiveLabels[candidate.label] ||
                                          candidate.label}
                                      </span>
                                      <span className="text-xs app-text-soft">
                                        {t.occurrencesLabel}:{" "}
                                        {candidate.occurrences ?? 1}
                                      </span>
                                    </div>

                                    <p className="mt-1.5 break-words text-sm leading-5 app-text-muted">
                                      {candidate.quote}
                                    </p>
                                  </div>
                                </div>
                              </label>
                            );
                          })
                        ) : (
                          <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-4 text-sm text-emerald-100">
                            {t.noCandidates}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {resultSummary ? (
                    <pre className="max-h-[180px] overflow-y-auto rounded-xl bg-[var(--app-surface)] p-3 whitespace-pre-wrap break-words text-xs leading-5 app-text-muted">
                      {resultSummary}
                    </pre>
                  ) : (
                    <p className="text-sm leading-5 app-text-soft">
                      {t.previewEmpty}
                    </p>
                  )}
                </div>
              </div>
            </div>
          </section>
        </div>
      </div>
    </AppSidebarLayout>
  );
}
