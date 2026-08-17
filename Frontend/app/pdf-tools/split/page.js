"use client";

import { useMemo, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, CheckCircle2, Download, FileStack, Loader2, UploadCloud } from "lucide-react";
import { ChevronDown, Printer, Share2, Users, X } from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { normalizeAnalyzerArtifactUrl, postAnalyzerFeature } from "@/lib/api_client";
import {
  getMyOrganizations,
  getOrganization,
  createConversation,
  sendConversationAttachment,
  forwardConversationMessage,
} from "@/lib/api_client";
import { buildAnalyzerArtifactUrl } from "@/lib/api_client";
import { splitPdfPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import { FILE_SECURITY_POLICY, validateBrowserUpload } from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/split";
const MAX_PDF_SIZE_MB = 100;

const copy = splitPdfPageTranslations;
function systemLanguageFor(language) { return language === "fr" ? "french" : "english"; }
function isPdf(file) { const type = String(file?.type || "").toLowerCase(); const name = String(file?.name || "").toLowerCase(); return type === "application/pdf" || type === "application/x-pdf" || name.endsWith(".pdf"); }
function fileSizeMb(file) { return file.size / (1024 * 1024); }
function getFileStem(filename = "") { const name = String(filename || ""); const lastDot = name.lastIndexOf("."); return (lastDot > 0 ? name.slice(0, lastDot) : name) || "document"; }
function normalizeArtifactUrl(url) { return normalizeAnalyzerArtifactUrl(url); }
function downloadFilenameFromUrl(url = "") {
  try {
    return new URL(
      String(url || ""),
      typeof window !== "undefined" ? window.location.origin : "http://local",
    ).searchParams.get("download_name") || "";
  } catch {
    return "";
  }
}
function hasValidSelectedPages(value) {
  const tokens = String(value || "").split(",").map((item) => item.trim());
  if (!tokens.length || tokens.some((item) => !/^[1-9][0-9]*$/.test(item))) return false;
  return new Set(tokens.map(Number)).size === tokens.length;
}
function hasValidPageRanges(value) {
  const tokens = String(value || "").split(",").map((item) => item.trim());
  if (!tokens.length || tokens.some((item) => !item)) return false;
  return tokens.every((item) => {
    const match = item.match(/^([1-9][0-9]*)(?:\s*-\s*([1-9][0-9]*))?$/);
    if (!match) return false;
    return Number(match[2] || match[1]) >= Number(match[1]);
  });
}
function actionFileExtension(filename = "") {
  const value = String(filename || "");
  const lastDot = value.lastIndexOf(".");
  if (lastDot === -1) return "";
  return value.slice(lastDot).toLowerCase();
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

const OUTPUT_ACTION_COPY = {
  en: {
    print: "Print",
    share: "Share",
    shareToApps: "Share to other apps",
    shareToMembers: "Share with ReDOCX members",
    preparingShare: "Preparing file for secure sharing...",
    nativeShareUnsupported:
      "This browser cannot share this file directly to other apps. Download the file and share it from your device instead.",
    printPopupBlocked:
      "The print window was blocked by your browser. Allow pop-ups for ReDOCX and try again.",
    printPreparing: "Preparing a secure print preview...",
    printFailed: "Could not prepare this output for printing.",
    printUnsupported:
      "This output format cannot be printed directly. Download the file and open it in an application that supports printing.",
    printArchiveUnsupported:
      "ZIP packages cannot be printed directly. Download and extract the package, then print the required document.",
    memberShareTitle: "Share securely with organization members",
    organization: "Organization",
    recipients: "Recipients",
    noOrganizations:
      "No active Business or Enterprise organization is available for member sharing.",
    noMembers: "No other active members are available in this organization.",
    selectRecipients: "Choose at least one organization member.",
    sharing: "Sharing securely...",
    shareSelected: "Share with selected members",
    cancel: "Cancel",
    close: "Close",
    memberLimit: `Choose up to ${TEAM_SHARE_MAX_RECIPIENTS} members.`,
    teamFileTooLarge:
      "This file exceeds the 20 MB secure team-attachment limit and cannot be shared to ReDOCX members.",
    teamFileTypeUnsupported:
      "This file type is not permitted by ReDOCX secure team attachments. Download it or share it through another app instead.",
    shareSuccess: "Shared securely with ReDOCX organization members.",
    sharePartial:
      "The file was shared with some members, but one or more deliveries failed.",
    signInRequired: "Sign in to share with ReDOCX organization members.",
    outputActions: "Output actions",
    fileShared: "File shared successfully.",
  },
  fr: {
    print: "Imprimer",
    share: "Partager",
    shareToApps: "Partager vers d’autres applications",
    shareToMembers: "Partager avec des membres ReDOCX",
    preparingShare: "Préparation du fichier pour un partage sécurisé...",
    nativeShareUnsupported:
      "Ce navigateur ne peut pas partager directement ce fichier vers d’autres applications. Téléchargez le fichier puis partagez-le depuis votre appareil.",
    printPopupBlocked:
      "La fenêtre d’impression a été bloquée. Autorisez les fenêtres contextuelles pour ReDOCX puis réessayez.",
    printPreparing: "Préparation d’un aperçu d’impression sécurisé...",
    printFailed: "Impossible de préparer cette sortie pour l’impression.",
    printUnsupported:
      "Ce format de sortie ne peut pas être imprimé directement. Téléchargez le fichier et ouvrez-le dans une application compatible avec l’impression.",
    printArchiveUnsupported:
      "Les archives ZIP ne peuvent pas être imprimées directement. Téléchargez et extrayez l’archive, puis imprimez le document requis.",
    memberShareTitle: "Partager de manière sécurisée avec les membres de l’organisation",
    organization: "Organisation",
    recipients: "Destinataires",
    noOrganizations:
      "Aucune organisation Business ou Enterprise active n’est disponible pour le partage entre membres.",
    noMembers: "Aucun autre membre actif n’est disponible dans cette organisation.",
    selectRecipients: "Choisissez au moins un membre de l’organisation.",
    sharing: "Partage sécurisé en cours...",
    shareSelected: "Partager avec les membres sélectionnés",
    cancel: "Annuler",
    close: "Fermer",
    memberLimit: `Choisissez jusqu’à ${TEAM_SHARE_MAX_RECIPIENTS} membres.`,
    teamFileTooLarge:
      "Ce fichier dépasse la limite de 20 Mo des pièces jointes d’équipe sécurisées et ne peut pas être partagé avec des membres ReDOCX.",
    teamFileTypeUnsupported:
      "Ce type de fichier n’est pas autorisé par les pièces jointes d’équipe sécurisées ReDOCX. Téléchargez-le ou partagez-le via une autre application.",
    shareSuccess: "Partage sécurisé effectué avec les membres de l’organisation ReDOCX.",
    sharePartial:
      "Le fichier a été partagé avec certains membres, mais une ou plusieurs livraisons ont échoué.",
    signInRequired: "Connectez-vous pour partager avec des membres de votre organisation ReDOCX.",
    outputActions: "Actions de sortie",
    fileShared: "Fichier partagé avec succès.",
  },
};

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
  switch (actionFileExtension(filename)) {
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

function actionDownloadFilenameFromUrl(url = "") {
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
    actionFirstString([filename, actionDownloadFilenameFromUrl(normalizedUrl)]) ||
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
  if (contentType.includes("application/json")) {
    const payload = await response.json().catch(() => null);
    return (
      actionFirstString([
        payload?.detail?.message,
        payload?.detail?.error,
        payload?.message,
        payload?.error,
      ]) || `Artifact request failed with HTTP ${response.status}.`
    );
  }

  const text = await response.text().catch(() => "");
  return text.trim() || `Artifact request failed with HTTP ${response.status}.`;
}

async function fetchArtifactAsFile(artifact, { signal } = {}) {
  if (artifact?.textContent != null) {
    if (typeof File === "undefined") {
      throw new Error("This browser cannot prepare files for sharing.");
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

  if (!artifact?.url) throw new Error("The output file is not available.");

  const response = await fetch(artifact.url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "*/*" },
    signal,
  });

  if (!response.ok) throw new Error(await readArtifactFetchError(response));

  const blob = await response.blob();
  if (!blob.size) throw new Error("The output file is empty.");
  if (typeof File === "undefined") {
    throw new Error("This browser cannot prepare files for sharing.");
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
  const safeTitle = escapeHtml(title || "ReDOCX print");
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
  const extension = actionFileExtension(file.name);
  const contentType = String(file.type || "").split(";", 1)[0].toLowerCase();
  const safeTitle = escapeHtml(title || file.name || "ReDOCX output");

  if (extension === ".zip") throw new Error(copy.printArchiveUnsupported);

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
  if (!isImage && !isPdf) throw new Error(copy.printUnsupported);

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
    <div class="screen-note">ReDOCX secure print preview</div>
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
  artifacts: artifactList = [],
  artifactUrl = "",
  storageKey = "",
  filename = "",
  contentType = "",
  textContent = "",
  textFilename = "",
  title = "ReDOCX output",
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
  const providedArtifacts = useMemo(() => {
    if (!Array.isArray(artifactList)) return [];
    const seen = new Set();
    return artifactList
      .map((item) =>
        normalizeOutputArtifact({
          artifactUrl: actionFirstString([
            item?.url,
            item?.download_url,
            item?.downloadUrl,
            item?.artifact_url,
            item?.artifactUrl,
          ]),
          storageKey: actionFirstString([item?.storage_key, item?.storageKey]),
          filename: actionFirstString([
            item?.filename,
            item?.file_name,
            item?.original_artifact_name,
            item?.artifact_name,
          ]),
          contentType: actionFirstString([
            item?.contentType,
            item?.mimeType,
            item?.content_type,
            item?.mime_type,
          ]),
        }),
      )
      .filter((artifact) => {
        if (!artifact || seen.has(artifact.key)) return false;
        seen.add(artifact.key);
        return true;
      });
  }, [artifactList]);
  const artifacts = useMemo(
    () =>
      directArtifact
        ? [directArtifact]
        : providedArtifacts.length
          ? providedArtifacts
          : collectDownloadableArtifacts(result),
    [directArtifact, providedArtifacts, result],
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
        text: shareError?.message || "Could not prepare the output for sharing.",
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
      text: `Shared from ReDOCX: ${file.name}`,
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
        text: shareError?.message || copy.nativeShareUnsupported,
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
            text: shareError?.message || copy.nativeShareUnsupported,
          });
        }
      })
      .finally(() => setBusyAction(""));
  }

  async function preparePrintableFile(artifact) {
    const sourceFile = await prepareArtifactFile(artifact);
    const extension = actionFileExtension(sourceFile.name);
    if (extension === ".zip") throw new Error(copy.printArchiveUnsupported);
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
          actionDownloadFilenameFromUrl(resultNode?.download_url || resultNode?.downloadUrl),
        ]) || `${getFileStem(sourceFile.name)}.pdf`,
      contentType: actionFirstString([
        resultNode?.content_type,
        resultNode?.contentType,
        "application/pdf",
      ]),
    });

    if (!preview) throw new Error(copy.printFailed);
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
        const message = printError?.message || copy.printFailed;
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
          organizationError?.message ||
          "Could not load organization members for sharing.",
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
    if (!TEAM_SHARE_ALLOWED_EXTENSIONS.has(actionFileExtension(preparedFile.name))) {
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
        text: organizationsError?.message || copy.noOrganizations,
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
    if (!TEAM_SHARE_ALLOWED_EXTENSIONS.has(actionFileExtension(file.name))) {
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
        throw new Error("Could not resolve the secure ReDOCX conversation.");
      }

      const uploadResult = await sendConversationAttachment(conversationId, file, {
        caption: `Shared from ReDOCX: ${file.name}`,
        clientMessageId: createShareClientMessageId("artifact-share"),
      });
      const sourceMessageId = uploadResult?.message?.id;
      if (!sourceMessageId) {
        throw new Error(
          "The secure attachment was sent but no message reference was returned.",
        );
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
          text: `The file was shared with 1 of ${recipientIds.length} selected members. ${
            forwardError?.message || "The remaining deliveries could not be confirmed."
          }`,
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
          text: `${copy.sharePartial} ${deliveredCount} of ${recipientIds.length} deliveries succeeded.`,
        });
        return;
      }

      setSelectedMemberIds([]);
      setMemberShareMessage({ type: "success", text: copy.shareSuccess });
    } catch (memberError) {
      setMemberShareMessage({
        type: "error",
        text: memberError?.message || "Could not share the output securely.",
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
                    actionFirstString([member.name, member.email]) ||
                    "Organization member";
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

function AuthRequired({ t }) { return <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6"><h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2><p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p><a href="/auth/login?returnTo=/pdf-tools/split" className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]">{t.signIn}</a></section>; }

export default function SplitPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("every_page");
  const [selectedPages, setSelectedPages] = useState("");
  const [pageRanges, setPageRanges] = useState("");
  const [outputBasename, setOutputBasename] = useState("split-document");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);
  function validate() {
    if (!file) return t.noFile;
    if (!isPdf(file)) return t.invalidFile;
    if (fileSizeMb(file) > MAX_PDF_SIZE_MB) return t.tooLarge;
    if (mode === "extract_selected_pages" && !hasValidSelectedPages(selectedPages)) return t.badSelected;
    if (mode === "page_ranges" && !hasValidPageRanges(pageRanges)) return t.badRanges;
    return "";
  }
  async function handlePickedPdfFile(file) {
    if (!file) {
      setFile(null);
      return;
    }

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.pdfTool);
    if (securityError) {
      setFile(null);
      setError(securityError);
      return;
    }

    setError("");
    setFile(file);
    setOutputBasename(`${getFileStem(file.name)}.split`);
  }
  async function handleSubmit(event) { event.preventDefault(); setError(""); setResponse(null); const validationError = validate(); if (validationError) return setError(validationError); const formData = new FormData(); formData.append("file", file); formData.append("mode", mode); if (mode === "extract_selected_pages") formData.append("selected_pages", selectedPages.trim()); if (mode === "page_ranges") formData.append("page_ranges", pageRanges.trim()); formData.append("output_basename", outputBasename.trim() || "split-document"); formData.append("system_language", systemLanguageFor(language)); setBusy(true); try { setResponse(await postAnalyzerFeature(FEATURE_PATH, formData, true)); } catch (caught) { setError(caught?.message || t.failed); } finally { setBusy(false); } }
  const result = response?.result || null;
  const archiveUrl = normalizeArtifactUrl(result?.archive_file?.download_url);
  const outputFiles = Array.isArray(result?.output_files) ? result.output_files : [];
  const archiveFilename =
    result?.archive_file?.filename ||
    downloadFilenameFromUrl(archiveUrl) ||
    result?.archive_file?.file_name ||
    `${outputBasename}.zip`;
  const processedArtifacts = [
    ...outputFiles
      .map((item) => ({
        url: normalizeArtifactUrl(item.download_url),
        filename:
          item.filename ||
          downloadFilenameFromUrl(normalizeArtifactUrl(item.download_url)) ||
          item.file_name,
        mimeType: "application/pdf",
      }))
      .filter((item) => item.url),
    ...(archiveUrl
      ? [{
          url: archiveUrl,
          filename: archiveFilename,
          mimeType: "application/zip",
        }]
      : []),
  ];
  if (!authChecked) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
    </AppSidebarLayout>
  );
  if (!user) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6"><AuthRequired t={t} /></main>
    </AppSidebarLayout>
  );
  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8"><button type="button" onClick={() => router.back()} className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"><ArrowLeft className="h-4 w-4" />{t.back}</button><section className="mb-8 rounded-3xl border app-surface-strong p-6"><p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">{t.badge}</p><h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">{t.title}</h1><p className="mt-3 max-w-3xl app-text-muted">{t.description}</p></section><form onSubmit={handleSubmit} className="grid gap-6 lg:grid-cols-[1fr_0.85fr]"><section className="rounded-3xl border app-surface-strong p-5"><h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2><p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p><label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center"><UploadCloud className="h-10 w-10 app-text-muted" /><span className="mt-3 text-sm font-semibold app-text">{file?.name || t.chooseFile}</span><input type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => handlePickedPdfFile(event.target.files?.[0] || null)} /></label></section><section className="space-y-6"><div className="rounded-3xl border app-surface-strong p-5"><label className="block text-sm font-medium app-text">{t.mode}<select value={mode} onChange={(event) => setMode(event.target.value)} className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"><option value="every_page">{t.everyPage}</option><option value="extract_selected_pages">{t.selectedPages}</option><option value="page_ranges">{t.pageRanges}</option></select></label>{mode === "extract_selected_pages" ? <input value={selectedPages} onChange={(event) => setSelectedPages(event.target.value)} placeholder={t.selectedPagesInput} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}{mode === "page_ranges" ? <input value={pageRanges} onChange={(event) => setPageRanges(event.target.value)} placeholder={t.pageRangesInput} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}<input value={outputBasename} readOnly placeholder={t.outputBasename} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /></div>{error ? <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"><AlertTriangle className="mr-2 inline h-4 w-4" />{error}</p> : null}<button type="submit" disabled={busy} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileStack className="h-4 w-4" />}{busy ? t.splitting : t.split}</button>{result ? <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5"><h2 className="flex items-center gap-2 text-lg font-semibold app-text"><CheckCircle2 className="h-5 w-5" />{t.resultTitle}</h2><div className="mt-4 flex flex-col gap-2">{archiveUrl ? <a href={archiveUrl} className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"><Download className="h-4 w-4" />{t.downloadArchive}</a> : null}{outputFiles.map((item, index) => { const url = normalizeArtifactUrl(item.download_url); return url ? <a key={item.file_name || index} href={url} className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text">{t.downloadFile} {index + 1}</a> : null; })}</div><ProductionOutputActions artifacts={processedArtifacts} title="Split PDF output" /></section> : null}</section></form></main>
    </AppSidebarLayout>
  );
}
