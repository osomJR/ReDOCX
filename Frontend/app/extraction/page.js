"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft, ChevronDown, Download, Loader2, Printer, Share2, Upload, Users, X,
} from "lucide-react";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  commonTranslations,
  getPageRuntimeCopy,
  processedOutputActionTranslations,
  resolveErrorMessage,
  resolveErrorTranslationKey,
  structuredExtractionPageTranslations,
  structuredExtractionReviewTranslations,
} from "@/lib/translations";
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
const MAX_DOCUMENTS = 20;
const ENDPOINT = "/api/analyzer/structured-extraction";
const BUSINESS_VIEWS = ["fields", "source_tables", "records"];
const DOWNLOAD_FORMATS = ["xlsx", "csv", "json"];

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  return lastDot === -1 ? "" : filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  return (lastDot === -1 ? filename : filename.slice(0, lastDot)) || "structured-extraction";
}

function replaceVars(template, values = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
}

function parseSelectedFields(value) {
  return [...new Set(value.split(",").map((field) => field.trim()).filter(Boolean))];
}

async function requestExtraction(path, body, fallbackError, signal) {
  const response = await fetch(ENDPOINT + path, {
    method: "POST",
    body,
    credentials: "include",
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || (Array.isArray(detail) ? detail[0]?.msg : null);
    throw new Error(message || data.message || fallbackError);
  }
  return data;
}

function resultForm(reviewId, layout, tableId, columns, outputFormat) {
  const body = new FormData();
  body.append("review_id", reviewId);
  body.append("layout", layout);
  if (outputFormat) body.append("output_format", outputFormat);
  if (layout === "source_tables") {
    body.append("table_id", String(tableId));
    for (const column of columns || []) body.append("columns", column);
  }
  return body;
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


function ExtractionPreview({ preview, copy }) {
  if (preview.json) {
    return (
      <section className="space-y-2">
        <h3 className="font-medium">{copy.jsonPreview}</h3>
        {preview.shortened && <p className="text-sm app-text-muted">{copy.jsonShortened}</p>}
        <pre className="max-h-[30rem] overflow-auto rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3 text-xs">
          {JSON.stringify(preview.json, null, 2)}
        </pre>
      </section>
    );
  }

  const { columns, rows, row_count: rowCount, shortened } = preview;
  if (!columns?.length) return <p className="py-6 text-sm app-text-muted">{copy.noViewData}</p>;
  return (
    <div className="space-y-2">
      <p className="text-sm app-text-muted">
        {replaceVars(copy.rowCount, { shown: rows.length, total: rowCount })}
        {shortened && " " + copy.dataShortened}
      </p>
      <div className="max-h-[30rem] overflow-auto rounded-xl border border-[var(--app-border)]">
        <table className="w-full border-collapse text-left text-sm">
          <thead className="sticky top-0 bg-[var(--app-panel)]">
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col" className="border-b border-[var(--app-border)] px-3 py-2 font-medium">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index} className="border-b border-[var(--app-border)] last:border-b-0">
                {columns.map((column) => (
                  <td key={column} className="max-w-80 break-words px-3 py-2 align-top">
                    {Object.hasOwn(row, column) ? String(row[column] ?? "") : ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DocumentReview({ documents, quality, copy }) {
  const flagged = documents.filter((document) => document.fields.length || document.warnings.length);
  if (!flagged.length) return null;
  return (
    <section className="space-y-2 border-t border-[var(--app-border)] pt-4">
      <h3 className="text-sm font-semibold">{copy.documentReview}</h3>
      {quality.requested_field_count > 0 && (
        <p className="text-sm app-text-muted">
          {replaceVars(copy.coverage, {
            found: quality.populated_requested_field_count,
            requested: quality.requested_field_count,
          })}
        </p>
      )}
      {flagged.map((document) => (
        <details
          key={document.source_document_index}
          open={flagged.length === 1 ? true : undefined}
          className="rounded-xl border border-[var(--app-border)] p-3 text-sm"
        >
          <summary className="cursor-pointer break-words font-medium">{document.filename}</summary>
          {document.warnings.map((warning, index) => (
            <p key={index} className="mt-2 app-text-muted">{warning}</p>
          ))}
          {document.fields.length > 0 && (
            <dl className="mt-2 grid gap-2">
              {document.fields.map((field) => (
                <div key={field.name} className="break-words border-t border-[var(--app-border)] pt-2">
                  <dt className="font-medium">{field.name}</dt>
                  <dd className="app-text-muted">
                    {copy.fieldStatus[field.status]}
                    {field.value && " · " + field.value}
                    {field.line_number && " · " + replaceVars(copy.sourceLine, { line: field.line_number })}
                    {field.candidates.length > 0 && " · " + field.candidates.join(" / ")}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </details>
      ))}
    </section>
  );
}

export default function StructuredExtractionPage() {
  const router = useRouter();
  const inputRef = useRef(null);
  const checkingFiles = useRef(false);
  const { language } = useLanguage();
  const account = useAccount();
  const t = structuredExtractionPageTranslations[language] || structuredExtractionPageTranslations.en;
  const copy = structuredExtractionReviewTranslations[language] || structuredExtractionReviewTranslations.en;
  const common = commonTranslations[language] || commonTranslations.en;
  const [files, setFiles] = useState([]);
  const [fieldText, setFieldText] = useState("");
  const [checking, setChecking] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [inputError, setInputError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [reviewResult, setReviewResult] = useState(null);
  const [layout, setLayout] = useState("fields");
  const [tableId, setTableId] = useState(null);
  const [columns, setColumns] = useState(null);
  const [previewState, setPreviewState] = useState(null);
  const [downloadInfo, setDownloadInfo] = useState(null);

  const reviewId = reviewResult?.review_id;
  const review = reviewResult?.review;
  const table = review?.source_tables.find((item) => item.id === tableId);
  const sourceTableView = layout === "source_tables";
  const technicalView = layout === "technical_json";
  const canPreview = !!reviewId && (!sourceTableView || !!table);
  const viewKey = JSON.stringify([reviewId, layout, sourceTableView ? tableId : null, sourceTableView ? columns : null]);
  const result = previewState?.key === viewKey ? previewState : null;
  const busy = checking || extracting || downloading;
  const buttonClass = "rounded-xl border border-[var(--app-border)] px-3 py-2 text-sm font-medium hover:bg-[var(--app-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50";

  useEffect(() => {
    if (!canPreview) return;
    const controller = new AbortController();
    requestExtraction("/result", resultForm(reviewId, layout, tableId, columns), copy.previewError, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setPreviewState({ key: viewKey, preview: data.preview });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewState({ key: viewKey, error: error.message });
      });
    return () => controller.abort();
  }, [canPreview, reviewId, layout, tableId, columns, viewKey, copy.previewError]);

  function clearReview() {
    setReviewResult(null);
    setDownloadInfo(null);
    setDownloadError("");
  }

  function changeView(next) {
    setLayout(next);
    setDownloadInfo(null);
    setDownloadError("");
  }

  async function pickFiles(incoming) {
    const additions = Array.from(incoming || []);
    if (!additions.length || checkingFiles.current || extracting || downloading) return;
    checkingFiles.current = true;
    setChecking(true);
    setInputError("");
    try {
      const { acceptedFiles, duplicates } = await partitionDuplicateBrowserUploads([...files, ...additions]);
      if (acceptedFiles.length > MAX_DOCUMENTS) {
        throw new Error(replaceVars(getPageRuntimeCopy("structuredExtraction", language).maxFiles, { count: MAX_DOCUMENTS }));
      }
      for (const file of acceptedFiles) {
        const ext = getFileExtension(file.name);
        if (!ACCEPTED_EXTENSIONS.includes(ext)) {
          throw new Error(replaceVars(t.unsupportedFileType, { ext: ext || "—" }));
        }
        if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
          throw new Error(replaceVars(t.fileTooLarge, { maxSize: MAX_FILE_SIZE_MB }));
        }
        const error = await validateBrowserUpload(file, FILE_SECURITY_POLICY.documentWithImages);
        if (error) throw new Error(file.name + ": " + error);
      }
      if (duplicates.length) setInputError(copy.duplicateFiles);
      setFiles(acceptedFiles);
      clearReview();
    } catch (error) {
      setInputError(error.message);
    } finally {
      checkingFiles.current = false;
      setChecking(false);
    }
  }

  async function extract(event) {
    event.preventDefault();
    if (busy || checkingFiles.current || !files.length) return;
    clearReview();
    setInputError("");
    setExtracting(true);
    try {
      const body = new FormData();
      for (const file of files) body.append("files", file);
      const requested = parseSelectedFields(fieldText);
      for (const name of requested) body.append("selected_fields", name);
      body.append("system_language", language === "fr" ? "french" : "english");
      const result = await requestExtraction("/review", body, t.extractionFailed);
      if (!result.review_id || !result.review) throw new Error(t.extractionFailed);
      setColumns(null);
      setTableId(result.review.source_tables[0]?.id ?? null);
      setLayout(requested.length || !result.review.record_row_count ? "fields" : "records");
      setReviewResult(result);
    } catch (error) {
      setInputError(error.message);
    } finally {
      setExtracting(false);
    }
  }

  async function download(format, downloadLayout = layout) {
    if (!reviewId || downloading) return;
    setDownloading(true);
    setDownloadError("");
    setDownloadInfo(null);
    try {
      const body = resultForm(reviewId, downloadLayout, tableId, columns, format);
      const { artifact } = await requestExtraction("/result", body, copy.downloadError);
      const url = normalizeAnalyzerArtifactUrl(artifact?.download_url) || buildAnalyzerArtifactUrl(artifact?.storage_key);
      if (!url || !artifact?.filename) throw new Error(t.missingDownloadUrl);
      setDownloadInfo({ ...artifact, url });

      const link = document.createElement("a");
      link.href = url;
      link.download = artifact.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      setDownloadError(error.message);
    } finally {
      setDownloading(false);
    }
  }

  const activeColumns = columns || table?.columns || [];
  const preview = result?.preview;
  const canDownload = !!preview && (technicalView || preview.columns?.length > 0);

  return (
    <AppSidebarLayout>
      <main className="mx-auto min-h-screen max-w-7xl px-4 py-5 text-[var(--app-text)] md:px-6">
        <button type="button" onClick={() => router.push("/")} className={buttonClass + " inline-flex items-center gap-2"}>
          <ArrowLeft className="h-4 w-4" /> {common.back}
        </button>
        <h1 className="mt-4 text-2xl font-semibold sm:text-3xl">{t.title}</h1>
        <p className="mt-1 text-sm app-text-muted">{t.description}</p>

        <div className="mt-5 grid items-start gap-4 lg:grid-cols-[minmax(0,0.65fr)_minmax(0,1.35fr)]">
          <form onSubmit={extract} className="min-w-0 space-y-4 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
            <div
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault();
                if (!busy) void pickFiles(event.dataTransfer.files);
              }}
              className="rounded-xl border border-dashed border-[var(--app-border)] p-4 text-center"
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                multiple
                hidden
                onChange={(event) => {
                  void pickFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <button
                type="button"
                disabled={busy}
                onClick={() => inputRef.current?.click()}
                className={buttonClass + " inline-flex items-center gap-2"}
              >
                <Upload className="h-4 w-4" /> {copy.chooseFiles}
              </button>
              <p className="mt-2 text-xs app-text-muted">{replaceVars(t.allowedFileInputs, { maxFiles: MAX_DOCUMENTS })}</p>
            </div>
            {files.length > 0 && (
              <ul className="space-y-1">
                {files.map((file, index) => (
                  <li key={file.name + "-" + index} className="flex items-center justify-between gap-2 rounded-lg bg-[var(--app-panel)] px-3 py-2 text-sm">
                    <span className="min-w-0 break-all">{file.name}</span>
                    <button
                      type="button"
                      disabled={busy}
                      aria-label={replaceVars(copy.removeFile, { name: file.name })}
                      onClick={() => {
                        setFiles(files.filter((_, position) => position !== index));
                        setInputError("");
                        clearReview();
                      }}
                      className="shrink-0 rounded p-1 hover:bg-[var(--app-surface-strong)] disabled:opacity-50"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <label className="block space-y-2 text-sm">
              <span className="font-medium">{t.selectedFieldsLabel}</span>
              <span className="block app-text-muted">{copy.fieldHelp}</span>
              <input
                value={fieldText}
                onChange={(event) => {
                  setFieldText(event.target.value);
                  clearReview();
                }}
                disabled={busy}
                placeholder={copy.fieldExample}
                className="w-full rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-2"
              />
            </label>
            {inputError && <p role="alert" className="break-words text-sm text-amber-500">{inputError}</p>}
            <button
              type="submit"
              disabled={busy || !files.length}
              className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-50"
            >
              {(extracting || checking) && <Loader2 className="h-4 w-4 animate-spin" />}
              {extracting ? t.extracting : checking ? copy.checking : t.extractAction}
            </button>
          </form>

          <section className="min-w-0 space-y-4 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4" aria-label={t.extractionOutput}>
            <h2 className="text-lg font-semibold">{t.extractionOutput}</h2>
            {!review && <p aria-live="polite" className="text-sm app-text-muted">{extracting ? copy.extractingHelp : t.previewText}</p>}
            {review && (
              <>
                {!review.has_data && <p role="status" className="rounded-xl border border-[var(--app-border)] p-3 text-sm">{copy.noData}</p>}
                <div className="flex flex-wrap items-center gap-2" aria-label={copy.reviewViews}>
                  {BUSINESS_VIEWS.map((view) => (
                    <button
                      key={view}
                      type="button"
                      aria-pressed={layout === view}
                      disabled={downloading}
                      onClick={() => changeView(view)}
                      className={buttonClass + (layout === view ? " bg-[var(--app-accent-bg)] text-[var(--app-accent-text)]" : "")}
                    >
                      {copy.views[view]}
                    </button>
                  ))}
                  <button
                    type="button"
                    aria-pressed={technicalView}
                    disabled={downloading}
                    onClick={() => changeView("technical_json")}
                    className={buttonClass + (technicalView ? " bg-[var(--app-accent-bg)] text-[var(--app-accent-text)]" : "")}
                  >
                    {copy.views.technical_json}
                  </button>
                </div>

                {sourceTableView && table && (
                  <div className="flex flex-wrap items-start gap-3">
                    <label className="min-w-0 space-y-1 text-sm">
                      <span className="block app-text-muted">{copy.chooseTable}</span>
                      <select
                        value={tableId}
                        disabled={downloading}
                        onChange={(event) => {
                          setTableId(Number(event.target.value));
                          setColumns(null);
                          changeView("source_tables");
                        }}
                        className="max-w-full rounded-lg border border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-2"
                      >
                        {review.source_tables.map((item) => (
                          <option key={item.id} value={item.id}>
                            {replaceVars(copy.tableName, { name: item.filename, table: item.table_index })}
                          </option>
                        ))}
                      </select>
                    </label>
                    <details className="mt-6 rounded-lg border border-[var(--app-border)] px-3 py-2 text-sm">
                      <summary className="cursor-pointer">{copy.columns}</summary>
                      <div className="mt-2 max-h-56 space-y-1 overflow-auto">
                        {table.columns.map((column) => (
                          <label key={column} className="flex gap-2">
                            <input
                              type="checkbox"
                              checked={activeColumns.includes(column)}
                              disabled={downloading || (activeColumns.length === 1 && activeColumns.includes(column))}
                              onChange={(event) => {
                                const next = event.target.checked
                                  ? [...activeColumns, column]
                                  : activeColumns.filter((name) => name !== column);
                                setColumns(next.length === table.columns.length ? null : next);
                                changeView("source_tables");
                              }}
                            />
                            {column}
                          </label>
                        ))}
                      </div>
                    </details>
                  </div>
                )}
                {sourceTableView && !table && <p className="text-sm app-text-muted">{copy.noSourceTables}</p>}
                {canPreview && !result && <p role="status" className="text-sm app-text-muted">{copy.loadingPreview}</p>}
                {result?.error && <p role="alert" className="text-sm text-amber-500">{result.error}</p>}
                {preview && <ExtractionPreview preview={preview} copy={copy} />}

                <div className="flex flex-wrap items-start gap-2">
                  {canDownload && (technicalView ? (
                    <button type="button" disabled={downloading} onClick={() => download("json")} className={buttonClass}>
                      {copy.downloadTechnical}
                    </button>
                  ) : (
                    <details className="rounded-xl border border-[var(--app-border)] px-3 py-2 text-sm">
                      <summary className="cursor-pointer font-medium">{common.download}</summary>
                      <div className="mt-2 grid gap-1">
                        {DOWNLOAD_FORMATS.map((format) => (
                          <button
                            key={format}
                            type="button"
                            disabled={downloading}
                            onClick={() => download(format)}
                            className={buttonClass + " inline-flex items-center gap-2 text-left"}
                          >
                            <Download className="h-4 w-4" /> {copy.formats[format]}
                          </button>
                        ))}
                      </div>
                    </details>
                  ))}
                  {!technicalView && review.field_row_count > 0 && review.record_row_count > 0 && (
                    <button
                      type="button"
                      disabled={downloading}
                      onClick={() => download("xlsx", "complete_workbook")}
                      className={buttonClass}
                    >
                      {copy.completeWorkbook}
                    </button>
                  )}
                </div>
                {downloading && <p role="status" className="text-sm app-text-muted">{copy.preparingDownload}</p>}
                {downloadError && <p role="alert" className="text-sm text-amber-500">{downloadError}</p>}
                {downloadInfo && (
                  <div className="space-y-2 text-sm">
                    <a href={downloadInfo.url} download={downloadInfo.filename} className="break-all text-[var(--app-accent-text)] underline">
                      {downloadInfo.filename}
                    </a>
                    <ProductionOutputActions
                      artifactUrl={downloadInfo.url}
                      storageKey={downloadInfo.storage_key}
                      filename={downloadInfo.filename}
                      contentType={downloadInfo.content_type}
                      title={getPageRuntimeCopy("structuredExtraction", language).outputTitle}
                      language={language}
                      account={account}
                    />
                  </div>
                )}
                <DocumentReview documents={review.documents} quality={review.quality} copy={copy} />
              </>
            )}
          </section>
        </div>
      </main>
    </AppSidebarLayout>
  );
}
