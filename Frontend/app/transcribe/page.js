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
  Mic,
  Square,
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
  transcribePageTranslations,
  resolveErrorMessage,
  resolveErrorTranslationKey,
  getPageRuntimeCopy,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
  postAnalyzerBatchFeature,
  getMyOrganizations,
  getOrganization,
  createConversation,
  sendConversationAttachment,
  forwardConversationMessage,
  postAnalyzerFeature,
} from "@/lib/api_client";
import BatchResultPanel from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import TranscriptionSubtitlePlayer, {
  extractTranscriptionSubtitlePayload,
} from "@/components/transcription_subtitle_player";
import {
  FILE_SECURITY_POLICY,
  validateBrowserUpload,
  validateBrowserBatchUploads,
  getBatchUploadLimit,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [
  ".mp3",
  ".wav",
  ".aac",
  ".flac",
  ".m4a",
  ".ogg",
  ".mp4",
  ".mov",
  ".avi",
  ".mkv",
  ".wmv",
  ".webm",
];
const AUDIO_EXTENSIONS = [".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".webm"];
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm"];
const SERVER_DURATION_FALLBACK_EXTENSIONS = new Set([
  ".wav",
  ".aac",
  ".flac",
  ".avi",
  ".wmv",
]);

const MICROPHONE_RECORDING_FORMATS = [
  { mimeType: "audio/webm;codecs=opus", extension: ".webm" },
  { mimeType: "audio/webm", extension: ".webm" },
  { mimeType: "audio/mp4;codecs=mp4a.40.2", extension: ".m4a" },
  { mimeType: "audio/mp4", extension: ".m4a" },
  { mimeType: "audio/ogg;codecs=opus", extension: ".ogg" },
  { mimeType: "audio/ogg", extension: ".ogg" },
];
const MICROPHONE_AUDIO_BITS_PER_SECOND = 32_000;

const OUTPUT_EXTENSION = ".txt";
const MAX_AUDIO_FILE_SIZE_MB = 25;
const MAX_VIDEO_FILE_SIZE_MB = 100;
const MAX_AUDIO_DURATION_SECONDS = 6000;
const MAX_VIDEO_DURATION_SECONDS = 600;
const MICROPHONE_AUTO_STOP_SECONDS = Math.max(1, MAX_AUDIO_DURATION_SECONDS - 5);
const MICROPHONE_MAX_RECORDING_BYTES = MAX_AUDIO_FILE_SIZE_MB * 1024 * 1024;
const MICROPHONE_SIZE_SAFETY_MARGIN_BYTES = 256 * 1024;

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const value = String(filename || "");
  const lastDot = value.lastIndexOf(".");
  return (lastDot > 0 ? value.slice(0, lastDot) : value) || "transcript";
}

function getRecordingFormatForMimeType(mimeType = "") {
  const normalized = String(mimeType || "").toLowerCase();
  if (normalized.includes("webm")) {
    return { mimeType: "audio/webm", extension: ".webm" };
  }
  if (normalized.includes("mp4") || normalized.includes("m4a")) {
    return { mimeType: "audio/mp4", extension: ".m4a" };
  }
  if (normalized.includes("ogg")) {
    return { mimeType: "audio/ogg", extension: ".ogg" };
  }
  return null;
}

function getSupportedMicrophoneRecordingFormat() {
  if (typeof MediaRecorder === "undefined") return null;
  if (typeof MediaRecorder.isTypeSupported !== "function") return null;
  return (
    MICROPHONE_RECORDING_FORMATS.find(({ mimeType }) =>
      MediaRecorder.isTypeSupported(mimeType),
    ) || null
  );
}

function buildMicrophoneRecordingFilename(extension) {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `microphone-recording-${timestamp}${extension}`;
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const wholeSeconds = Math.round(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  const remainingSeconds = wholeSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function replaceVars(template, vars = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
}

function getMediaType(extension = "", mimeType = "") {
  const normalizedMimeType = String(mimeType || "").trim().toLowerCase();

  // WebM is a container used by both browser-recorded audio and uploaded video.
  // Prefer the browser-reported media family when available while preserving the
  // existing audio default used by direct microphone recordings.
  if (extension === ".webm") {
    if (normalizedMimeType.startsWith("video/")) return "video";
    if (normalizedMimeType.startsWith("audio/")) return "audio";
  }

  if (AUDIO_EXTENSIONS.includes(extension)) return "audio";
  if (VIDEO_EXTENSIONS.includes(extension)) return "video";
  return "unknown";
}

function getMaxFileSizeMb(mediaType) {
  return mediaType === "audio"
    ? MAX_AUDIO_FILE_SIZE_MB
    : MAX_VIDEO_FILE_SIZE_MB;
}

function getMaxDurationSeconds(mediaType) {
  return mediaType === "audio"
    ? MAX_AUDIO_DURATION_SECONDS
    : MAX_VIDEO_DURATION_SECONDS;
}

function getMediaTypeLabel(extension, t, mimeType = "") {
  const mediaType = getMediaType(extension, mimeType);
  if (mediaType === "audio") return t.audioType;
  if (mediaType === "video") return t.videoType;
  return t.unknownType;
}

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
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
  if (
    typeof responseData?.message === "string" &&
    responseData.message.trim()
  ) {
    return responseData.message.trim();
  }
  if (typeof responseData?.error === "string" && responseData.error.trim()) {
    return responseData.error.trim();
  }

  try {
    return JSON.stringify(detail || responseData);
  } catch {
    return fallbackMessage;
  }
}

function extractTranscriptText(responseData) {
  const candidates = [
    responseData?.result,
    responseData?.data,
    responseData,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const directText = pickFirstString([
      candidate?.content,
      candidate?.transcript_text,
      candidate?.transcriptText,
      candidate?.transcript,
      candidate?.text,
    ]);

    if (directText) {
      return directText;
    }

    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  return "";
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

function normalizeArtifactDownloadUrl(url = "") {
  const raw = String(url || "").trim();
  if (!raw) return "";

  const normalized = normalizeAnalyzerArtifactUrl(raw);
  if (normalized !== raw || /^https?:\/\//i.test(raw)) {
    return normalized;
  }

  return buildAnalyzerArtifactUrl(raw);
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

function extractTranscriptPdfArtifact(responseData, sourceFilename = "") {
  const result = responseData?.result || responseData?.data?.result || null;
  const artifact = result?.pdf_artifact || result?.pdfArtifact || null;

  if (!artifact) return null;

  const downloadUrl =
    normalizeArtifactDownloadUrl(
      artifact.download_url || artifact.downloadUrl,
    ) || buildArtifactDownloadUrl(artifact.storage_key || artifact.storageKey);

  if (!downloadUrl) return null;

  return {
    filename:
      downloadFilenameFromUrl(downloadUrl) ||
      artifact.filename ||
      `${getFileStem(sourceFilename)}.pdf`,
    downloadUrl,
  };
}

function readMediaDuration(file, mediaType) {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const element =
      mediaType === "audio"
        ? document.createElement("audio")
        : document.createElement("video");

    let settled = false;

    const cleanup = () => {
      element.removeAttribute("src");
      element.load();
      URL.revokeObjectURL(objectUrl);
    };

    element.preload = "metadata";

    element.onloadedmetadata = () => {
      if (settled) return;
      settled = true;

      const duration = Number(element.duration);
      cleanup();

      if (!Number.isFinite(duration) || duration <= 0) {
        reject(Object.assign(new Error("MEDIA_DURATION_READ_FAILED"), { code: "MEDIA_DURATION_READ_FAILED" }));
        return;
      }

      resolve(duration);
    };

    element.onerror = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(Object.assign(new Error("MEDIA_DURATION_READ_FAILED"), { code: "MEDIA_DURATION_READ_FAILED" }));
    };

    element.src = objectUrl;
  });
}

async function validatePickedFile(file, t) {
  const extension = getFileExtension(file.name);

  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    throw new Error(
      replaceVars(t.unsupportedFileType, {
        ext: extension || "unknown",
      }),
    );
  }

  const mediaType = getMediaType(extension, file.type);
  if (mediaType === "unknown") {
    throw new Error(
      replaceVars(t.unsupportedFileType, {
        ext: extension || "unknown",
      }),
    );
  }

  const maxSizeMb = getMaxFileSizeMb(mediaType);
  if (file.size > maxSizeMb * 1024 * 1024) {
    throw new Error(
      replaceVars(t.fileTooLarge, {
        maxSize: maxSizeMb,
      }),
    );
  }

  let durationSeconds = 1;
  let durationVerifiedByBrowser = false;
  try {
    durationSeconds = await readMediaDuration(file, mediaType);
    durationVerifiedByBrowser = true;
  } catch {
    // Some valid legacy/container formats are not decodable by every browser.
    // The backend always runs authoritative ffprobe duration validation before
    // transcription, so these newly supported formats can safely continue with
    // the minimum positive compatibility value required by the request contract.
    if (!SERVER_DURATION_FALLBACK_EXTENSIONS.has(extension)) {
      throw new Error(t.couldNotReadDuration);
    }
  }

  const maxDurationSeconds = getMaxDurationSeconds(mediaType);
  if (durationVerifiedByBrowser && durationSeconds > maxDurationSeconds) {
    throw new Error(
      replaceVars(t.mediaTooLong, {
        maxDuration: formatDuration(maxDurationSeconds),
      }),
    );
  }

  return {
    mediaType,
    durationSeconds,
    maxSizeMb,
    maxDurationSeconds,
    durationVerifiedByBrowser,
  };
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


export default function TranscribePage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const mediaRecorderRef = useRef(null);
  const microphoneStreamRef = useRef(null);
  const recordingChunksRef = useRef([]);
  const recordingBytesRef = useRef(0);
  const recordingStartedAtRef = useRef(0);
  const recordingTimerRef = useRef(null);
  const recordingFormatRef = useRef(null);
  const recordingFailedRef = useRef(false);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);

  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    transcribePageTranslations[language] || transcribePageTranslations.en;

  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [selectedFileMeta, setSelectedFileMeta] = useState(null);
  const [selectedFileMetas, setSelectedFileMetas] = useState([]);
  const [error, setError] = useState("");
  const [isCheckingFile, setIsCheckingFile] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [transcriptResult, setTranscriptResult] = useState("");
  const [transcriptPdfArtifact, setTranscriptPdfArtifact] = useState(null);
  const [transcriptionResponse, setTranscriptionResponse] = useState(null);
  const [preserveFillerWords, setPreserveFillerWords] = useState(true);
  const [removeBackgroundNoise, setRemoveBackgroundNoise] = useState(false);
  const [diarizeSpeakers, setDiarizeSpeakers] = useState(false);
  const [isPreparingMicrophone, setIsPreparingMicrophone] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [inputSource, setInputSource] = useState("upload");

  const canSubmit =
    !isCheckingFile &&
    !isSubmitting &&
    !isPreparingMicrophone &&
    !isRecording &&
    !!selectedFile &&
    !!selectedFileMeta;

  function resetResultState() {
    setTranscriptResult("");
    setTranscriptPdfArtifact(null);
    setTranscriptionResponse(null);
    setBatchResult(null);
  }

  function resetFileState() {
    setSelectedFile(null);
    setSelectedFiles([]);
    setSelectedFileMeta(null);
    setSelectedFileMetas([]);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function clearRecordingTimer() {
    if (recordingTimerRef.current) {
      window.clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
  }

  function stopMicrophoneTracks() {
    const stream = microphoneStreamRef.current;
    microphoneStreamRef.current = null;
    if (!stream) return;
    stream.getTracks().forEach((track) => track.stop());
  }

  function microphoneErrorMessage(caught) {
    const name = String(caught?.name || "");
    if (name === "NotAllowedError" || name === "SecurityError") {
      return t.microphonePermissionDenied;
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return t.microphoneNotFound;
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return t.microphoneUnavailable;
    }
    return t.microphoneRecordingFailed;
  }

  async function acceptRecordedSpeech(file, durationSeconds) {
    setIsCheckingFile(true);
    setError("");
    resetResultState();

    try {
      const securityError = await validateBrowserUpload(
        file,
        FILE_SECURITY_POLICY.media,
      );
      if (securityError) {
        throw new Error(securityError);
      }

      const extension = getFileExtension(file.name);
      if (!AUDIO_EXTENSIONS.includes(extension)) {
        throw Object.assign(new Error("UNSUPPORTED_FILE_TYPE"), {
          code: "UNSUPPORTED_FILE_TYPE",
        });
      }

      const normalizedDuration = Math.max(
        1,
        Math.ceil(Number(durationSeconds) || 0),
      );
      if (normalizedDuration > MAX_AUDIO_DURATION_SECONDS) {
        throw new Error(
          replaceVars(t.mediaTooLong, {
            maxDuration: formatDuration(MAX_AUDIO_DURATION_SECONDS),
          }),
        );
      }

      const metadata = {
        mediaType: "audio",
        durationSeconds: normalizedDuration,
        maxSizeMb: MAX_AUDIO_FILE_SIZE_MB,
        maxDurationSeconds: MAX_AUDIO_DURATION_SECONDS,
      };

      setSelectedFiles([file]);
      setSelectedFile(file);
      setSelectedFileMetas([metadata]);
      setSelectedFileMeta(metadata);
      setInputSource("microphone");
    } catch (caught) {
      resetFileState();
      setInputSource("microphone");
      setError(
        caught?.message ||
          resolveErrorMessage(caught, language, "INVALID_REQUEST"),
      );
    } finally {
      setIsCheckingFile(false);
    }
  }

  async function startMicrophoneRecording() {
    if (isSubmitting || isCheckingFile || isPreparingMicrophone || isRecording) {
      return;
    }

    if (typeof window === "undefined" || window.isSecureContext === false) {
      setError(t.microphoneSecureContextRequired);
      return;
    }

    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setError(t.microphoneUnsupported);
      return;
    }

    setIsPreparingMicrophone(true);
    setError("");
    resetResultState();
    resetFileState();
    setInputSource("microphone");
    setRecordingSeconds(0);
    recordingChunksRef.current = [];
    recordingBytesRef.current = 0;
    recordingFailedRef.current = false;

    let stream = null;

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: { ideal: 1 },
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      microphoneStreamRef.current = stream;

      const preferredFormat = getSupportedMicrophoneRecordingFormat();
      let recorder;
      let requestedFormat = null;

      try {
        if (preferredFormat) {
          recorder = new MediaRecorder(stream, {
            mimeType: preferredFormat.mimeType,
            audioBitsPerSecond: MICROPHONE_AUDIO_BITS_PER_SECOND,
          });
          requestedFormat = preferredFormat;
        } else {
          recorder = new MediaRecorder(stream, {
            audioBitsPerSecond: MICROPHONE_AUDIO_BITS_PER_SECOND,
          });
        }
      } catch {
        recorder = new MediaRecorder(stream);
      }

      const recordingFormat =
        getRecordingFormatForMimeType(recorder.mimeType) || requestedFormat;
      if (!recordingFormat) {
        throw Object.assign(new Error("MICROPHONE_FORMAT_UNSUPPORTED"), {
          code: "MICROPHONE_FORMAT_UNSUPPORTED",
        });
      }

      recordingFormatRef.current = recordingFormat;
      mediaRecorderRef.current = recorder;
      recordingStartedAtRef.current = performance.now();

      recorder.ondataavailable = (event) => {
        if (event.data?.size > 0) {
          recordingChunksRef.current.push(event.data);
          recordingBytesRef.current += event.data.size;

          if (
            recordingBytesRef.current >=
              MICROPHONE_MAX_RECORDING_BYTES -
                MICROPHONE_SIZE_SAFETY_MARGIN_BYTES &&
            recorder.state !== "inactive"
          ) {
            try {
              recorder.stop();
            } catch {
              clearRecordingTimer();
              stopMicrophoneTracks();
            }
          }
        }
      };

      recorder.onerror = () => {
        recordingFailedRef.current = true;
        setError(t.microphoneRecordingFailed);
        if (recorder.state !== "inactive") {
          try {
            recorder.stop();
          } catch {
            stopMicrophoneTracks();
          }
        }
      };

      recorder.onstop = async () => {
        clearRecordingTimer();
        setIsRecording(false);
        setIsPreparingMicrophone(true);
        stopMicrophoneTracks();

        const durationSeconds = Math.max(
          1,
          (performance.now() - recordingStartedAtRef.current) / 1000,
        );
        const format = recordingFormatRef.current;
        const chunks = recordingChunksRef.current;

        mediaRecorderRef.current = null;
        recordingFormatRef.current = null;
        recordingChunksRef.current = [];
        recordingBytesRef.current = 0;

        try {
          if (recordingFailedRef.current) {
            throw Object.assign(new Error("MICROPHONE_RECORDING_FAILED"), {
              code: "MICROPHONE_RECORDING_FAILED",
            });
          }

          if (!format || !chunks.length) {
            throw Object.assign(new Error("MICROPHONE_RECORDING_EMPTY"), {
              code: "MICROPHONE_RECORDING_EMPTY",
            });
          }

          const blob = new Blob(chunks, { type: format.mimeType });
          if (!blob.size) {
            throw Object.assign(new Error("MICROPHONE_RECORDING_EMPTY"), {
              code: "MICROPHONE_RECORDING_EMPTY",
            });
          }

          const recordedFile = new File(
            [blob],
            buildMicrophoneRecordingFilename(format.extension),
            {
              type: format.mimeType,
              lastModified: Date.now(),
            },
          );

          await acceptRecordedSpeech(recordedFile, durationSeconds);
        } catch (caught) {
          resetFileState();
          setInputSource("microphone");
          setError(
            caught?.code === "MICROPHONE_FORMAT_UNSUPPORTED"
              ? t.microphoneFormatUnsupported
              : t.microphoneRecordingFailed,
          );
        } finally {
          recordingFailedRef.current = false;
          setIsPreparingMicrophone(false);
        }
      };

      recorder.start(1000);
      setIsRecording(true);

      recordingTimerRef.current = window.setInterval(() => {
        const elapsedSeconds = Math.max(
          0,
          (performance.now() - recordingStartedAtRef.current) / 1000,
        );
        setRecordingSeconds(elapsedSeconds);

        if (
          elapsedSeconds >= MICROPHONE_AUTO_STOP_SECONDS &&
          recorder.state !== "inactive"
        ) {
          try {
            recorder.stop();
          } catch {
            clearRecordingTimer();
            stopMicrophoneTracks();
          }
        }
      }, 250);
    } catch (caught) {
      clearRecordingTimer();
      stopMicrophoneTracks();
      mediaRecorderRef.current = null;
      recordingFormatRef.current = null;
      recordingChunksRef.current = [];
      recordingBytesRef.current = 0;
      setIsRecording(false);
      setError(
        caught?.code === "MICROPHONE_FORMAT_UNSUPPORTED"
          ? t.microphoneFormatUnsupported
          : microphoneErrorMessage(caught),
      );
    } finally {
      setIsPreparingMicrophone(false);
    }
  }

  function stopMicrophoneRecording() {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;

    setIsRecording(false);
    setIsPreparingMicrophone(true);
    clearRecordingTimer();

    try {
      recorder.stop();
    } catch {
      stopMicrophoneTracks();
      setIsPreparingMicrophone(false);
      setError(t.microphoneRecordingFailed);
    }
  }

  useEffect(() => {
    return () => {
      if (recordingTimerRef.current) {
        window.clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }

      const recorder = mediaRecorderRef.current;
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onerror = null;
        recorder.onstop = null;
        if (recorder.state !== "inactive") {
          try {
            recorder.stop();
          } catch {
            // Component teardown still stops the underlying media tracks below.
          }
        }
      }

      const stream = microphoneStreamRef.current;
      microphoneStreamRef.current = null;
      stream?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  function rejectFile(message) {
    resetFileState();
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.media,
    );
    if (securityError) {
      rejectFile(securityError);
      return;
    }

    setIsCheckingFile(true);
    setError("");
    resetResultState();

    try {
      const validated = await validatePickedFile(file, t);
      setSelectedFiles([file]);
      setSelectedFile(file);
      setSelectedFileMetas([validated]);
      setSelectedFileMeta(validated);
      setInputSource("upload");
    } catch (pickedFileError) {
      rejectFile(resolveErrorMessage(pickedFileError, language, "UNSUPPORTED_FILE_TYPE"));
    } finally {
      setIsCheckingFile(false);
    }
  }

  async function handlePickedFiles(fileList) {
    if (isRecording || isPreparingMicrophone) {
      setError(t.stopRecordingBeforeUpload);
      return;
    }

    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const existingFiles = inputSource === "microphone" ? [] : selectedFiles;
    const files = [...existingFiles, ...incomingFiles];

    if (files.length === 1) {
      await handlePickedFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(
      files,
      FILE_SECURITY_POLICY.media,
      {
        account: batchAccount,
        featureLabel: "speech to text",
      },
    );

    if (batchValidation.message) {
      setError(batchValidation.message);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    const acceptedFiles = batchValidation.files;

    setIsCheckingFile(true);
    setError(batchValidation.duplicateMessage || "");
    resetResultState();

    try {
      const validations = [];
      for (const file of acceptedFiles) {
        validations.push(await validatePickedFile(file, t));
      }

      const mediaTypes = [
        ...new Set(validations.map((item) => item.mediaType)),
      ];
      if (mediaTypes.length !== 1) {
        throw Object.assign(new Error("BATCH_MEDIA_TYPE_MISMATCH"), { code: "BATCH_MEDIA_TYPE_MISMATCH" });
      }

      setSelectedFile(acceptedFiles[0]);
      setSelectedFileMeta(validations[0]);
      setSelectedFiles(acceptedFiles);
      setSelectedFileMetas(validations);
      setInputSource("upload");
    } catch (pickedFileError) {
      rejectFile(resolveErrorMessage(pickedFileError, language, "INVALID_REQUEST"));
    } finally {
      setIsCheckingFile(false);
    }
  }

  function handleFileChange(event) {
    void handlePickedFiles(event.target.files);
    event.target.value = "";
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    void handlePickedFiles(event.dataTransfer.files);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function handleRemoveFile(_file, index) {
    const nextFiles = selectedFiles.filter(
      (_, fileIndex) => fileIndex !== index,
    );
    const nextMetas = selectedFileMetas.filter(
      (_, fileIndex) => fileIndex !== index,
    );
    setSelectedFiles(nextFiles);
    setSelectedFileMetas(nextMetas);
    setSelectedFile(nextFiles[0] || null);
    setSelectedFileMeta(nextMetas[0] || null);
    if (!nextFiles.length) setInputSource("upload");
    setError("");
    resetResultState();
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (!selectedFile || !selectedFileMeta) {
      setError(t.chooseFileToTranscribe);
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {
      if (selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append(
          "media_type",
          selectedFileMetas[0]?.mediaType || selectedFileMeta.mediaType,
        );
        selectedFileMetas.forEach((meta) => {
          formData.append(
            "duration_seconds",
            String(Math.round(meta.durationSeconds)),
          );
        });
        formData.append(
          "system_language",
          language === "fr" ? "french" : "english",
        );
        formData.append("preserve_filler_words", String(preserveFillerWords));
        formData.append(
          "remove_background_noise",
          String(removeBackgroundNoise),
        );
        formData.append("diarize_speakers", String(diarizeSpeakers));

        const data = await postAnalyzerBatchFeature("transcribe", formData);
        setBatchResult(data);
        setTranscriptResult("");
        setTranscriptPdfArtifact(null);
        setTranscriptionResponse(null);
        return;
      }

      const extension = getFileExtension(selectedFile.name);

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("media_type", selectedFileMeta.mediaType);
      formData.append("media_format", extension.replace(".", ""));
      formData.append(
        "duration_seconds",
        String(Math.round(selectedFileMeta.durationSeconds)),
      );
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );
      formData.append("preserve_filler_words", String(preserveFillerWords));
      formData.append("remove_background_noise", String(removeBackgroundNoise));
      formData.append("diarize_speakers", String(diarizeSpeakers));

      const response = await fetch("/api/analyzer/transcribe", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        const requestError = Object.assign(
          new Error(
            extractResponseMessage(responseData, t.transcriptionPotentialIssue),
          ),
          {
            payload: responseData,
            status: response.status,
            code: resolveErrorTranslationKey(responseData, "PROCESSING_FAILED"),
          },
        );
        throw requestError;
      }

      const transcriptText = extractTranscriptText(responseData);

      if (!transcriptText) {
        throw Object.assign(new Error("TRANSCRIPT_TEXT_MISSING"), { code: "TRANSCRIPT_TEXT_MISSING" });
      }

      if (!extractTranscriptionSubtitlePayload(responseData)) {
        throw Object.assign(new Error("TRANSCRIPT_SUBTITLES_MISSING"), {
          code: "TRANSCRIPT_SUBTITLES_MISSING",
        });
      }

      setTranscriptResult(transcriptText);
      const pdfArtifact = extractTranscriptPdfArtifact(responseData, selectedFile.name);
      if (!pdfArtifact) {
        throw Object.assign(new Error("TRANSCRIPT_ARTIFACT_MISSING"), { code: "TRANSCRIPT_ARTIFACT_MISSING" });
      }

      setTranscriptPdfArtifact(pdfArtifact);
      setTranscriptionResponse(responseData);
    } catch (submitError) {
      setError(resolveErrorMessage(submitError, language, "PROCESSING_FAILED"));
    } finally {
      setIsSubmitting(false);
    }
  }

  const actionLabel = isCheckingFile
    ? t.validatingMedia
    : isSubmitting
      ? common.transcribing || common.generating
      : common.transcribe;

  const optionsDisabled =
    isSubmitting || isPreparingMicrophone || isRecording;

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-x-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-5 md:px-6 lg:py-6">
          <header className="mb-4 shrink-0">
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

            <div className="mt-4">
              <h1 className="max-w-full text-3xl font-semibold tracking-tight text-[var(--app-text)] sm:text-4xl lg:whitespace-nowrap lg:text-[2.65rem] lg:leading-tight xl:text-5xl">
                {t.title}
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 app-text-muted md:text-base">
                {t.description}
              </p>
            </div>
          </header>

          <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
            <form
              onSubmit={handleSubmit}
              className="relative min-h-0 overflow-hidden rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative flex h-full min-h-0 flex-col">
                <div className="grid gap-3 md:grid-cols-2">
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

                    <p className="mt-2 text-sm leading-6 app-text-muted">
                      {t.allowedFileInputs}
                    </p>

                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      accept={ACCEPTED_EXTENSIONS.join(",")}
                      onChange={handleFileChange}
                      className="hidden"
                    />

                    <button
                      type="button"
                      disabled={
                        isSubmitting ||
                        isCheckingFile ||
                        isPreparingMicrophone ||
                        isRecording
                      }
                      onClick={() => fileInputRef.current?.click()}
                      className="mt-3 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {common.chooseFile}
                    </button>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center md:p-5">
                    <div
                      className={`mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border ${
                        isRecording
                          ? "border-red-400/40 bg-red-400/10"
                          : "border-[var(--app-border)] bg-[var(--app-surface)]"
                      }`}
                    >
                      <Mic
                        className={`h-5 w-5 ${
                          isRecording ? "text-red-300" : "text-cyan-300"
                        }`}
                      />
                    </div>

                    <h2 className="text-base font-semibold text-[var(--app-text)]">
                      {t.microphoneTitle}
                    </h2>
                    <p className="mt-2 text-sm leading-6 app-text-muted">
                      {t.microphoneHelp}
                    </p>

                    <div
                      className="mt-3 min-h-6 text-sm app-text-soft"
                      role="status"
                      aria-live="polite"
                    >
                      {isRecording ? (
                        <span className="inline-flex items-center gap-2 font-medium text-red-200">
                          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-red-400" />
                          {t.recordingNow} {formatDuration(recordingSeconds)}
                        </span>
                      ) : isPreparingMicrophone ? (
                        <span className="inline-flex items-center gap-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          {t.preparingMicrophone}
                        </span>
                      ) : inputSource === "microphone" && selectedFile ? (
                        <span className="text-emerald-200">
                          {t.microphoneRecordingReady}
                        </span>
                      ) : (
                        <span>{t.microphoneIdle}</span>
                      )}
                    </div>

                    {isRecording ? (
                      <button
                        type="button"
                        onClick={stopMicrophoneRecording}
                        className="mt-3 inline-flex items-center gap-2 rounded-2xl border border-red-400/30 bg-red-400/10 px-4 py-2.5 text-sm font-semibold text-red-100 transition hover:bg-red-400/20"
                      >
                        <Square className="h-4 w-4 fill-current" />
                        {t.stopRecording}
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={
                          isSubmitting ||
                          isCheckingFile ||
                          isPreparingMicrophone
                        }
                        onClick={() => void startMicrophoneRecording()}
                        className="mt-3 inline-flex items-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Mic className="h-4 w-4" />
                        {t.startRecording}
                      </button>
                    )}

                    <p className="mt-2 text-xs leading-5 app-text-soft">
                      {replaceVars(t.microphoneLimit, {
                        maxDuration: formatDuration(MAX_AUDIO_DURATION_SECONDS),
                        maxSize: MAX_AUDIO_FILE_SIZE_MB,
                      })}
                    </p>
                  </div>
                </div>

                {selectedFile && selectedFileMeta && (
                  <SelectedFilesSummary
                    files={selectedFiles}
                    limit={batchLimit}
                    language={language}
                    className="mt-3"
                    onRemoveFile={handleRemoveFile}
                    disabled={
                      isCheckingFile ||
                      isSubmitting ||
                      isPreparingMicrophone ||
                      isRecording
                    }
                    renderDetails={(file, index) => {
                      const metadata = selectedFileMetas[index];
                      if (!metadata) return null;

                      return (
                        <>
                          {inputSource === "microphone" && index === 0 ? (
                            <>
                              {t.sourceLabel} {t.microphoneSourceLabel}
                              {" • "}
                            </>
                          ) : null}
                          {t.detectedTypeLabel}{" "}
                          {getMediaTypeLabel(getFileExtension(file.name), t, file.type)}
                          {" • "}
                          {t.durationLabel}{" "}
                          {formatDuration(
                            metadata.durationVerifiedByBrowser === false
                              ? Number.NaN
                              : metadata.durationSeconds,
                          )}
                        </>
                      );
                    }}
                  />
                )}

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                  <div className="mb-3 flex items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                      <Mic className="h-5 w-5 text-cyan-300" />
                    </div>
                    <div>
                      <h2 className="text-base font-semibold text-[var(--app-text)]">
                        {t.transcriptOptionsTitle}
                      </h2>
                      <p className="text-sm app-text-soft">
                        {t.transcriptOptionsSubtitle}
                      </p>
                    </div>
                  </div>

                  <div className="grid gap-3 lg:grid-cols-3">
                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.preserveFillerWordsLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.preserveFillerWordsHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={preserveFillerWords}
                          disabled={optionsDisabled}
                          onChange={(e) =>
                            setPreserveFillerWords(e.target.checked)
                          }
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>

                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.removeBackgroundNoiseLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.removeBackgroundNoiseHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={removeBackgroundNoise}
                          disabled={optionsDisabled}
                          onChange={(e) =>
                            setRemoveBackgroundNoise(e.target.checked)
                          }
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>

                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.diarizeSpeakersLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.diarizeSpeakersHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={diarizeSpeakers}
                          disabled={optionsDisabled}
                          onChange={(e) => setDiarizeSpeakers(e.target.checked)}
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>
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
                      {actionLabel}
                    </button>
                  </div>

                  <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-soft">
                    {common.outputFormat}{" "}
                    <span className="font-medium app-text-muted">
                      {OUTPUT_EXTENSION}
                    </span>
                  </div>
                </div>
              </div>
            </form>

            <aside className="min-h-0">
              <div className="flex min-h-[270px] flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5 lg:max-h-[calc(100vh-11rem)]">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.transcriptOutput}
                  </h2>
                  <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1 text-xs app-text-soft">
                    {OUTPUT_EXTENSION}
                  </span>
                </div>

                <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4 max-h-[420px] lg:max-h-[calc(100vh-16rem)]">
                  {batchResult ? (
                    <div>
                      <BatchResultPanel
                        result={batchResult}
                        title={getPageRuntimeCopy("transcribe", language).batchResultsTitle}
                        transcriptionPlaybackTitle={t.synchronizedPlaybackTitle}
                        transcriptionSubtitleLabel={t.subtitlesLabel}
                        embedded
                      />
                      <ProductionOutputActions
                        result={batchResult}
                        title={getPageRuntimeCopy("transcribe", language).batchOutputTitle}
                      />
                    </div>
                  ) : transcriptResult ? (
                    <div className="space-y-3">
                      <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                        <div className="flex items-start gap-3">
                          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                          <div>
                            <p className="font-medium text-emerald-100">
                              {t.transcriptReady}
                            </p>
                            <p className="mt-1 text-sm text-emerald-100/80">
                              {t.transcriptReadyText}
                            </p>
                            <p className="mt-1 text-sm text-emerald-100/80">
                              {t.transcriptMetaLabel}: {t.transcriptMetaValue}
                            </p>
                          </div>
                        </div>
                      </div>

                      <TranscriptionSubtitlePlayer
                        responseData={transcriptionResponse}
                        title={t.synchronizedPlaybackTitle}
                        subtitleLabel={t.subtitlesLabel}
                      />

                      {transcriptPdfArtifact && (
                        <a
                          href={transcriptPdfArtifact.downloadUrl}
                          download={transcriptPdfArtifact.filename}
                          className="inline-flex items-center justify-center rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                        >
                          {t.downloadPdfTranscript || getPageRuntimeCopy("transcribe", language).downloadPdf}
                        </a>
                      )}

                      <pre className="whitespace-pre-wrap break-words pr-1 text-xs leading-6 app-text-muted md:text-sm">
                        {transcriptResult}
                      </pre>
                      <ProductionOutputActions
                        artifactUrl={transcriptPdfArtifact?.downloadUrl}
                        filename={transcriptPdfArtifact?.filename}
                        contentType="application/pdf"
                        textContent={transcriptResult}
                        textFilename="transcript.txt"
                        title={getPageRuntimeCopy("transcribe", language).outputTitle}
                      />
                    </div>
                  ) : (
                    <div className="flex h-full min-h-[180px] items-center justify-center rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center">
                      <p className="max-w-sm text-sm leading-6 app-text-soft">
                        {t.previewText}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </aside>
          </section>
        </div>
      </div>
    </AppSidebarLayout>
  );
}