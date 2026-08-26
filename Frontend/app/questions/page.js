"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileText,
  HelpCircle,
  ListChecks,
  Loader2,
  ShieldCheck,
  MessageCircleQuestion,
  RotateCcw,
  Sparkles,
  Upload,
  XCircle,
  Printer,
  Share2,
  Users,
  ChevronDown,
  X,
} from "lucide-react";
import {
  commonTranslations,
  generateQuestionsPageTranslations,
  processedOutputActionTranslations,
  resolveErrorMessage,
  resolveErrorTranslationKey,
  getPageRuntimeCopy,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import {
  getAnalyzerResultDownloadUrl,
  postAnalyzerFeature,
  postAnalyzerBatchFeature,
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
  getMyOrganizations,
  getOrganization,
  createConversation,
  sendConversationAttachment,
  forwardConversationMessage,
} from "@/lib/api_client";
import {
  FILE_SECURITY_POLICY,
  INLINE_TEXT_SECURITY_POLICY,
  validateBrowserInlineText,
  validateBrowserUpload,
  validateBrowserBatchUploads,
  getBatchUploadLimit,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];
const REJECTED_EXTENSIONS = [".png", ".jpg", ".jpeg"];
const MAX_FILE_SIZE_MB = 25;
const INLINE_TEXT_EXTENSION = ".txt";

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return filename || "document";
  return filename.slice(0, lastDot) || "document";
}

function generatedOutputFilename(snapshot, suffix, extension = ".txt") {
  const normalizedExtension = String(extension || ".txt").startsWith(".")
    ? String(extension || ".txt")
    : `.${extension}`;
  const sourceFile = snapshot?.file || snapshot?.files?.[0] || null;
  return sourceFile
    ? `${getFileStem(sourceFile.name)}_${suffix}${normalizedExtension}`
    : `${suffix}${normalizedExtension}`;
}

function replaceVars(template, vars = {}) {
  return String(template || "").replace(
    /\{(\w+)\}/g,
    (_, key) => vars[key] ?? "",
  );
}

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}

function normalizeAnalyzerPayload(data) {
  const analyzerResponse = data?.analyzer_response || data;
  return {
    analyzerResponse,
    result: analyzerResponse?.result || data?.result || null,
    sidecarQuestionsText:
      data?.generated_questions_text ||
      data?.questions_text ||
      analyzerResponse?.generated_questions_text ||
      analyzerResponse?.result?.generated_questions_text ||
      "",
  };
}

function resultDownloadInfo(result, fallbackName, fallbackFormat) {
  if (
    !result ||
    typeof result !== "object" ||
    typeof result.content === "string"
  ) {
    return null;
  }

  const url = getAnalyzerResultDownloadUrl(result);

  return {
    filename: result.filename || fallbackName,
    outputFormat: result.output_format || fallbackFormat,
    fileSizeMb: result.file_size_mb,
    url,
  };
}

function parseNumberedItems(text) {
  const normalized = String(text || "")
    .replace(/\r\n/g, "\n")
    .trim();
  if (!normalized) return [];

  const matches = [
    ...normalized.matchAll(
      /(?:^|\n)\s*(\d+)\.\s+([\s\S]*?)(?=\n\s*\d+\.\s+|$)/g,
    ),
  ];
  const items = matches
    .map((match) => ({
      number: Number.parseInt(match[1], 10),
      body: String(match[2] || "")
        .replace(/\s+/g, " ")
        .trim(),
      raw: `${match[1]}. ${String(match[2] || "")
        .replace(/\s+/g, " ")
        .trim()}`,
    }))
    .filter((item) => item.number >= 1 && item.body);

  if (!items.length) return [];

  for (let index = 0; index < items.length; index += 1) {
    if (items[index].number !== index + 1) return [];
  }

  return items.map((item, index) => `${index + 1}. ${item.body}`);
}

function countNumberedItems(text) {
  return parseNumberedItems(text).length;
}

function UploadDropzone({
  t,
  selectedFiles = [],
  batchLimit,
  language,
  fileInputRef,
  onDrop,
  onDragOver,
  onFileChange,
  onPick,
  onRemoveFile,
  disabled = false,
}) {
  return (
    <div
      onDrop={onDrop}
      onDragOver={onDragOver}
      className="rounded-3xl border border-dashed border-[var(--app-border-strong)] app-surface p-6 text-center transition hover:border-[var(--app-border)]"
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS.join(",")}
        onChange={onFileChange}
        className="hidden"
      />
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border app-surface-strong">
        <Upload className="h-6 w-6 app-text-muted" />
      </div>
      <h2 className="mt-4 text-lg font-semibold app-text">{t.uploadTitle}</h2>
      <p className="mt-2 text-sm app-text-muted">{t.allowedFileInputs}</p>
      <p className="mt-1 text-xs app-text-soft">{t.wordsLimit}</p>
      <button
        type="button"
        onClick={onPick}
        className="mt-5 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
      >
        {t.uploadTitle}
      </button>
      <SelectedFilesSummary
        files={selectedFiles}
        limit={batchLimit}
        language={language}
        onRemoveFile={onRemoveFile}
        disabled={disabled}
      />
    </div>
  );
}

function DownloadCard({ info, label, title }) {
  if (!info) return null;

  return (
    <div className="mt-4 rounded-2xl border border-[var(--app-border)] app-surface p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold app-text">
            {info.filename}
          </p>
          <p className="text-xs app-text-muted">
            {info.outputFormat || "file"}
            {info.fileSizeMb ? ` · ${info.fileSizeMb} MB` : ""}
          </p>
        </div>
        {info.url ? (
          <a
            href={info.url}
            download
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01]"
          >
            <Download className="h-4 w-4" />
            {label}
          </a>
        ) : null}
      </div>
      <ProductionOutputActions
        artifactUrl={info.url}
        filename={info.filename}
        title={title || label}
      />
    </div>
  );
}

function TextOutput({ title, empty, content, icon: Icon = FileText }) {
  return (
    <section className="rounded-3xl border app-surface-strong p-6">
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl border app-surface">
          <Icon className="h-5 w-5 app-text-muted" />
        </div>
        <h2 className="text-lg font-semibold app-text">{title}</h2>
      </div>
      {content ? (
        <>
          <pre className="max-h-[32rem] whitespace-pre-wrap rounded-2xl border border-[var(--app-border)] app-surface p-4 text-sm leading-6 app-text overflow-auto">
            {content}
          </pre>
          <ProductionOutputActions
            textContent={content}
            textFilename={`${title}.txt`}
            title={title}
          />
        </>
      ) : (
        <p className="rounded-2xl border border-[var(--app-border)] app-surface p-4 text-sm app-text-muted">
          {empty}
        </p>
      )}
    </section>
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


export default function QuestionsPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);
  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    generateQuestionsPageTranslations[language] ||
    generateQuestionsPageTranslations.en;

  const [mode, setMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [inlineText, setInlineText] = useState("");
  const [error, setError] = useState("");
  const [isGeneratingQuestions, setIsGeneratingQuestions] = useState(false);
  const [isGeneratingAnswers, setIsGeneratingAnswers] = useState(false);
  const [questionsText, setQuestionsText] = useState("");
  const [questionItems, setQuestionItems] = useState([]);
  const [questionDownloadInfo, setQuestionDownloadInfo] = useState(null);
  const [answersText, setAnswersText] = useState("");
  const [answerDownloadInfo, setAnswerDownloadInfo] = useState(null);
  const [batchQuestionResult, setBatchQuestionResult] = useState(null);
  const [batchAnswerResult, setBatchAnswerResult] = useState(null);
  const [batchQuestionItemsByIndex, setBatchQuestionItemsByIndex] = useState(
    {},
  );
  const [answerDecision, setAnswerDecision] = useState("pending");
  const [sourceSnapshot, setSourceSnapshot] = useState(null);

  const inputExtension = useMemo(() => {
    if (mode === "text") return INLINE_TEXT_EXTENSION;
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [mode, selectedFile]);

  const outputExtension =
    mode === "text" ? INLINE_TEXT_EXTENSION : inputExtension || "";

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;
    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;
    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const canGenerateQuestions =
    !isGeneratingQuestions &&
    !isGeneratingAnswers &&
    ((mode === "file" && selectedFile && isValidFile) ||
      (mode === "text" && inlineText.trim().length > 0));

  const batchQuestionEntries = useMemo(
    () =>
      Object.entries(batchQuestionItemsByIndex)
        .map(([index, questions]) => ({
          index: Number(index),
          questions: Array.isArray(questions) ? questions : [],
        }))
        .filter(
          ({ index, questions }) =>
            Number.isInteger(index) && index > 0 && questions.length > 0,
        )
        .sort((left, right) => left.index - right.index),
    [batchQuestionItemsByIndex],
  );

  const isBatchAnswerFlow =
    sourceSnapshot?.mode === "file" &&
    Array.isArray(sourceSnapshot.files) &&
    sourceSnapshot.files.length > 1;

  const hasBatchQuestionItems = batchQuestionEntries.length > 0;

  const batchQuestionCount = useMemo(
    () =>
      batchQuestionEntries.reduce(
        (total, entry) => total + entry.questions.length,
        0,
      ),
    [batchQuestionEntries],
  );

  const canGenerateAnswers =
    !isGeneratingAnswers &&
    !isGeneratingQuestions &&
    Boolean(sourceSnapshot) &&
    ((isBatchAnswerFlow && hasBatchQuestionItems) ||
      (!isBatchAnswerFlow && questionItems.length > 0));

  const shouldShowAnswerPrompt =
    Boolean(sourceSnapshot) &&
    (Boolean(questionsText) ||
      questionItems.length > 0 ||
      hasBatchQuestionItems);

  const displayedQuestionCount = isBatchAnswerFlow
    ? batchQuestionCount
    : questionItems.length || countNumberedItems(questionsText);

  function clearGeneratedState() {
    setQuestionsText("");
    setQuestionItems([]);
    setQuestionDownloadInfo(null);
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setBatchQuestionResult(null);
    setBatchAnswerResult(null);
    setBatchQuestionItemsByIndex({});
    setAnswerDecision("pending");
    setSourceSnapshot(null);
  }

  function rejectFile(message) {
    setSelectedFile(null);
    setSelectedFiles([]);
    setError(message);
    clearGeneratedState();
  }

  function handleModeChange(nextMode) {
    setMode(nextMode);
    if (nextMode === "text") {
      setSelectedFile(null);
      setSelectedFiles([]);
    }
    setError("");
    clearGeneratedState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.aiTextDocument,
    );
    if (securityError) {
      rejectFile(securityError);
      return;
    }

    const ext = getFileExtension(file.name);

    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      rejectFile(replaceVars(t.unsupportedFileType, { ext: ext || "unknown" }));
      return;
    }

    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      rejectFile(replaceVars(t.fileTooLarge, { maxSize: MAX_FILE_SIZE_MB }));
      return;
    }

    setError("");
    setSelectedFiles([file]);
    setSelectedFile(file);
    clearGeneratedState();
  }

  async function handlePickedFiles(fileList) {
    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const files = [...selectedFiles, ...incomingFiles];

    if (files.length === 1) {
      await handlePickedFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(
      files,
      FILE_SECURITY_POLICY.aiTextDocument,
      {
        account: batchAccount,
        featureLabel: "question generation",
      },
    );

    if (batchValidation.message) {
      setError(batchValidation.message);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setError("");
    setSelectedFile(files[0]);
    setSelectedFiles(files);
    clearGeneratedState();
  }

  function handleFileChange(event) {
    handlePickedFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    handlePickedFiles(event.dataTransfer.files);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function handleRemoveFile(_file, index) {
    const nextFiles = selectedFiles.filter(
      (_, fileIndex) => fileIndex !== index,
    );
    setSelectedFiles(nextFiles);
    setSelectedFile(nextFiles[0] || null);
    setError("");
    clearGeneratedState();
  }

  function buildSourceFormData(snapshot = null) {
    const activeSnapshot = snapshot || sourceSnapshot;
    const formData = new FormData();

    if (activeSnapshot?.mode === "file") {
      if (!activeSnapshot.file) {
        throw new Error(t.sourceRequired);
      }
      formData.append("file", activeSnapshot.file);
    } else {
      const text = activeSnapshot?.text || inlineText.trim();
      if (!text) {
        throw new Error(t.sourceRequired);
      }
      const inlineTextError = validateBrowserInlineText(text);
      if (inlineTextError) {
        throw new Error(inlineTextError);
      }
      formData.append("text", text);
    }

    formData.append("system_language", systemLanguageFor(language));
    return formData;
  }

  async function handleGenerateQuestions(event) {
    event.preventDefault();

    if (mode === "file" && !selectedFile) {
      setError(t.sourceRequired);
      return;
    }

    if (mode === "text" && !inlineText.trim()) {
      setError(t.sourceRequired);
      return;
    }

    if (mode === "text") {
      const inlineTextError = validateBrowserInlineText(inlineText);
      if (inlineTextError) {
        setError(inlineTextError);
        return;
      }
    }

    const snapshot =
      mode === "file"
        ? {
            mode,
            file: selectedFile,
            files: selectedFiles.length > 1 ? selectedFiles : [],
            sourceLabel:
              selectedFiles.length > 1
                ? `${selectedFiles.length} files`
                : selectedFile?.name || t.inputFile,
          }
        : {
            mode,
            text: inlineText.trim(),
            sourceLabel: t.inputText,
          };

    setIsGeneratingQuestions(true);
    setError("");
    clearGeneratedState();
    setSourceSnapshot(snapshot);

    try {
      if (mode === "file" && selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append("system_language", systemLanguageFor(language));

        const data = await postAnalyzerBatchFeature(
          "generate-questions",
          formData,
        );
        const parsedByIndex = {};
        for (const item of data?.items || []) {
          if (!item?.success) continue;
          const { result, sidecarQuestionsText } = normalizeAnalyzerPayload(
            item.response,
          );
          const content =
            typeof result?.content === "string" && result.content.trim()
              ? result.content.trim()
              : String(sidecarQuestionsText || "").trim();
          const parsed = parseNumberedItems(content);
          if (parsed.length) parsedByIndex[item.index] = parsed;
        }

        setBatchQuestionResult(data);
        setBatchQuestionItemsByIndex(parsedByIndex);
        setAnswerDecision(
          Object.keys(parsedByIndex).length ? "pending" : "declined",
        );
        return;
      }

      const formData = buildSourceFormData(snapshot);
      const data = await postAnalyzerFeature(
        "generate-questions",
        formData,
        true,
      );
      const { result, sidecarQuestionsText } = normalizeAnalyzerPayload(data);

      if (!result) {
        throw Object.assign(new Error("BACKEND_RESULT_MISSING"), { code: "BACKEND_RESULT_MISSING" });
      }

      const content =
        typeof result.content === "string" && result.content.trim()
          ? result.content.trim()
          : String(sidecarQuestionsText || "").trim();
      const parsedQuestions = parseNumberedItems(content);

      setQuestionsText(content);
      setQuestionItems(parsedQuestions);
      setQuestionDownloadInfo(
        resultDownloadInfo(
          result,
          generatedOutputFilename(
            snapshot,
            "generated_questions",
            outputExtension || "txt",
          ),
          outputExtension || "txt",
        ),
      );
      setAnswerDecision("pending");
    } catch (err) {
      setError(resolveErrorMessage(err, language, "PROCESSING_FAILED"));
      setSourceSnapshot(null);
    } finally {
      setIsGeneratingQuestions(false);
    }
  }

  async function handleGenerateAnswers() {
    if (!sourceSnapshot) {
      setError(t.sourceRequired);
      return;
    }

    if (isBatchAnswerFlow && !hasBatchQuestionItems) {
      setError(t.badQuestionList);
      return;
    }

    if (!isBatchAnswerFlow && !questionItems.length) {
      setError(t.badQuestionList);
      return;
    }

    setIsGeneratingAnswers(true);
    setError("");
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setBatchAnswerResult(null);
    setAnswerDecision("accepted");

    try {
      if (isBatchAnswerFlow) {
        const formData = new FormData();
        sourceSnapshot.files.forEach((file) => formData.append("files", file));

        const questionsByIndex = {};
        for (let index = 0; index < sourceSnapshot.files.length; index += 1) {
          const questionsForFile = batchQuestionItemsByIndex[index + 1];
          if (questionsForFile?.length) {
            questionsByIndex[String(index + 1)] = questionsForFile;
          }
        }

        formData.append(
          "questions_json",
          JSON.stringify({ questions_by_index: questionsByIndex }),
        );
        formData.append("system_language", systemLanguageFor(language));

        const data = await postAnalyzerBatchFeature(
          "generate-answers",
          formData,
        );
        setBatchAnswerResult(data);
        return;
      }

      const formData = buildSourceFormData(sourceSnapshot);
      formData.append("questions_json", JSON.stringify(questionItems));

      const data = await postAnalyzerFeature(
        "generate-answers",
        formData,
        true,
      );
      const { result } = normalizeAnalyzerPayload(data);

      if (!result) {
        throw Object.assign(new Error("BACKEND_RESULT_MISSING"), { code: "BACKEND_RESULT_MISSING" });
      }

      if (typeof result.content === "string") {
        setAnswersText(result.content.trim());
      } else {
        setAnswerDownloadInfo(
          resultDownloadInfo(
            result,
            generatedOutputFilename(
              sourceSnapshot,
              "generated_answers",
              outputExtension || "txt",
            ),
            outputExtension || "txt",
          ),
        );
      }
    } catch (err) {
      setError(resolveErrorMessage(err, language, "PROCESSING_FAILED"));
    } finally {
      setIsGeneratingAnswers(false);
    }
  }

  function handleSkipAnswers() {
    setAnswerDecision("declined");
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setError("");
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto max-w-6xl px-6 py-12 md:px-8 md:py-16">
          <button
            type="button"
            onClick={() => router.push("/")}
            className="mb-8 inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
          >
            <ArrowLeft className="h-4 w-4" />
            {common.back}
          </button>

          <section className="mb-10">
            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-4 py-2 text-sm text-[var(--app-accent-text)]">
              <Sparkles className="h-4 w-4" />
              {t.badge}
            </div>
            <h1 className="mt-5 max-w-4xl text-4xl font-semibold tracking-tight app-text md:text-5xl">
              {t.title}
            </h1>
            <p className="mt-4 max-w-3xl text-base leading-7 app-text-muted">
              {t.description}
            </p>
          </section>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <section className="rounded-3xl border app-surface-strong p-6">
              <div className="mb-5 grid grid-cols-2 gap-2 rounded-2xl border border-[var(--app-border)] app-surface p-1">
                {[
                  ["file", t.fileMode],
                  ["text", t.textMode],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => handleModeChange(key)}
                    className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                      mode === key
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : "app-text-muted hover:bg-neutral-100 dark:hover:bg-[#2d2d33]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <form onSubmit={handleGenerateQuestions} className="space-y-5">
                {mode === "file" ? (
                  <UploadDropzone
                    t={t}
                    selectedFiles={selectedFiles}
                    batchLimit={batchLimit}
                    language={language}
                    fileInputRef={fileInputRef}
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onFileChange={handleFileChange}
                    onPick={() => fileInputRef.current?.click()}
                    onRemoveFile={handleRemoveFile}
                    disabled={isGeneratingQuestions || isGeneratingAnswers}
                  />
                ) : (
                  <div className="rounded-3xl border app-surface p-5">
                    <label
                      className="text-sm font-semibold app-text"
                      htmlFor="inlineText"
                    >
                      {t.pasteTextLabel}
                    </label>
                    <textarea
                      id="inlineText"
                      value={inlineText}
                      onChange={(event) => {
                        setInlineText(event.target.value);
                        clearGeneratedState();
                      }}
                      placeholder={t.pasteTextPlaceholder}
                      maxLength={INLINE_TEXT_SECURITY_POLICY.maxChars}
                      rows={12}
                      className="mt-3 w-full resize-y rounded-2xl border border-[var(--app-border)] app-surface-strong px-4 py-3 text-sm app-text outline-none transition placeholder:text-[var(--app-text-soft)] focus:border-[var(--app-border-strong)]"
                    />
                    <p className="mt-2 text-xs app-text-muted">
                      {t.inlineTextTreatedAs}
                    </p>
                  </div>
                )}

                {error ? (
                  <div className="flex items-start gap-3 rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-100">
                    <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
                    <p>{error}</p>
                  </div>
                ) : null}

                <button
                  type="submit"
                  disabled={!canGenerateQuestions}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isGeneratingQuestions ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <HelpCircle className="h-4 w-4" />
                  )}
                  {isGeneratingQuestions
                    ? t.generatingQuestions
                    : t.generateQuestions}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSelectedFile(null);
                    setSelectedFiles([]);
                    setInlineText("");
                    setError("");
                    clearGeneratedState();
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border app-surface px-5 py-3 text-sm font-semibold app-text transition hover:bg-neutral-100 dark:hover:bg-[#2d2d33]"
                >
                  <RotateCcw className="h-4 w-4" />
                  {t.resetFlow}
                </button>
              </form>

              <div className="mt-6 rounded-3xl border app-surface p-5">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-5 w-5 app-text-muted" />
                  <div>
                    <h3 className="text-sm font-semibold app-text">
                      {t.formatPolicy}
                    </h3>
                    <p className="mt-1 text-sm app-text-muted">
                      {t.policySubtitle}
                    </p>
                  </div>
                </div>
                <dl className="mt-4 grid gap-3 text-sm">
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.allowedUploadsLabel}</dt>
                    <dd className="text-right app-text-muted">.pdf, .docx</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.inlineInputLabel}</dt>
                    <dd className="text-right app-text-muted">
                      {t.inlineInputValue}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">
                      {t.rejectedAutomaticallyLabel}
                    </dt>
                    <dd className="text-right app-text-muted">
                      {REJECTED_EXTENSIONS.join(", ")}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.outputRuleLabel}</dt>
                    <dd className="max-w-xs text-right app-text-muted">
                      {t.outputRuleValue}
                    </dd>
                  </div>
                </dl>
              </div>
            </section>

            <div className="space-y-6">
              <BatchResultPanel
                result={batchQuestionResult}
                title={getPageRuntimeCopy("generateQuestions", language).batchQuestionsTitle}
              />
              <ProductionOutputActions
                result={batchQuestionResult}
                title={getPageRuntimeCopy("generateQuestions", language).batchQuestionsTitle}
              />

              <BatchResultPanel
                result={batchAnswerResult}
                title={getPageRuntimeCopy("generateQuestions", language).batchAnswersTitle}
              />
              <ProductionOutputActions
                result={batchAnswerResult}
                title={getPageRuntimeCopy("generateQuestions", language).batchAnswersTitle}
              />

              {hasBatchQuestionItems ? (
                <section className="rounded-3xl border app-surface p-4">
                  <p className="text-sm font-semibold app-text">
                    {getPageRuntimeCopy("generateQuestions", language).batchReady}
                  </p>
                  <p className="mt-1 text-sm app-text-muted">
                    {(batchQuestionEntries.length === 1
                      ? getPageRuntimeCopy("generateQuestions", language).parsedSummaryOne
                      : getPageRuntimeCopy("generateQuestions", language).parsedSummaryMany)
                      .replace("{questions}", String(batchQuestionCount))
                      .replace("{files}", String(batchQuestionEntries.length))}
                  </p>
                </section>
              ) : null}

              <TextOutput
                title={t.questionsOutputTitle}
                empty={t.previewEmpty}
                content={questionsText}
                icon={ListChecks}
              />

              {questionDownloadInfo ? (
                <DownloadCard
                  info={questionDownloadInfo}
                  label={t.downloadQuestionsFile}
                  title={t.questionsOutputTitle}
                />
              ) : null}

              {shouldShowAnswerPrompt ? (
                <section className="rounded-3xl border app-surface-strong p-6">
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border app-surface">
                      <MessageCircleQuestion className="h-5 w-5 app-text-muted" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h2 className="text-lg font-semibold app-text">
                        {t.answerPromptTitle}
                      </h2>
                      <p className="mt-1 text-sm app-text-muted">
                        {t.answerPromptDescription}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs app-text-soft">
                        <span className="rounded-full border border-[var(--app-border)] app-surface px-3 py-1">
                          {t.detectedSource}:{" "}
                          {sourceSnapshot?.sourceLabel || "—"}
                        </span>
                        <span className="rounded-full border border-[var(--app-border)] app-surface px-3 py-1">
                          {t.questionCount}: {displayedQuestionCount}
                        </span>
                      </div>
                    </div>
                  </div>

                  {!isBatchAnswerFlow &&
                  questionsText &&
                  !questionItems.length ? (
                    <div className="mt-5 flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-100">
                      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                      <div>
                        <p className="font-semibold">
                          {t.cannotGenerateAnswersTitle}
                        </p>
                        <p className="mt-1 text-amber-100/80">
                          {t.cannotGenerateAnswersDescription}
                        </p>
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-5 grid gap-2 md:grid-cols-2">
                    <button
                      type="button"
                      onClick={handleSkipAnswers}
                      disabled={isGeneratingAnswers}
                      className="rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#2d2d33]"
                    >
                      {t.skipAnswers}
                    </button>
                    <button
                      type="button"
                      onClick={handleGenerateAnswers}
                      disabled={!canGenerateAnswers}
                      className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isGeneratingAnswers ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Sparkles className="h-4 w-4" />
                      )}
                      {isGeneratingAnswers ? t.generatingAnswers : t.yes}
                    </button>
                  </div>

                  {answerDecision === "declined" ? (
                    <div className="mt-5 rounded-2xl border border-[var(--app-border)] app-surface p-4">
                      <p className="text-sm font-semibold app-text">
                        {t.declinedTitle}
                      </p>
                      <p className="mt-1 text-sm app-text-muted">
                        {t.declinedDescription}
                      </p>
                    </div>
                  ) : null}
                </section>
              ) : null}

              <TextOutput
                title={t.answersOutputTitle}
                empty={t.answersPreviewEmpty}
                content={answersText}
                icon={CheckCircle2}
              />

              {answerDownloadInfo ? (
                <DownloadCard
                  info={answerDownloadInfo}
                  label={t.downloadAnswersFile}
                  title={t.answersOutputTitle}
                />
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </AppSidebarLayout>
  );
}
