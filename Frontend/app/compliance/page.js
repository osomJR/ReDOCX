"use client";

import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  ShieldCheck,
  Download,
  FileText,
  FileJson,
  ClipboardCheck,
  PackageCheck,
  Printer,
  Share2,
  Users,
  Loader2,
  ChevronDown,
  X,
} from "lucide-react";
import {
  commonTranslations,
  compliancePageTranslations,
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
const MAX_COMPLIANCE_FILES = 20;
const COMPLIANCE_PREVIEW_ENDPOINT = "/api/analyzer/compliance/preview";
const COMPLIANCE_OPTIONS_ENDPOINT = "/api/analyzer/compliance/options";

const REPORT_VARIANTS = [
  "human_readable_report",
  "machine_readable_report",
  "annotated_source_output",
];

const REGULATORY_DOMAINS = [
  "privacy",
  "cybersecurity",
  "aml",
  "consumer_protection",
  "public_sector_access_to_information",
  "licensing",
  "registration",
  "sector_regulator_requirements",
];

const NIGERIA_SECTOR_PACKS = [
  "accounting",
  "agriculture",
  "aviation",
  "banking_and_fintech",
  "energy_and_power",
  "health",
  "insurance",
  "law_and_legal",
  "manufacturing",
  "maritime_and_shipping",
  "media",
  "mining",
  "ngo",
  "oil_and_gas",
  "payment_platforms_and_services",
  "pharmaceuticals",
  "sports",
  "tech",
  "telecom",
];

const EXPANDABLE_SECTOR_PACKS = NIGERIA_SECTOR_PACKS;

const COUNTRY_CONFIG = {
  nigeria: { labelKey: "nigeria", corePack: "core_control_library", sectorPacks: NIGERIA_SECTOR_PACKS },
  us: { labelKey: "unitedStates", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  uk: { labelKey: "unitedKingdom", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  sa: { labelKey: "southAfrica", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  canada: { labelKey: "canada", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  france: { labelKey: "france", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  togo: { labelKey: "togo", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
  ghana: { labelKey: "ghana", corePack: "core_control_library", sectorPacks: EXPANDABLE_SECTOR_PACKS },
};

const DEFAULT_JURISDICTION = "nigeria";

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return filename || "compliance-report";
  return filename.slice(0, lastDot) || "compliance-report";
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function replaceVars(template = "", vars = {}) {
  return String(template || "").replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
}

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function uniqueStrings(values = []) {
  return [...new Set(values.map((item) => String(item).trim()).filter(Boolean))];
}

function getFileTypeLabel(ext, t) {
  if (ext === ".pdf") return t.pdfDocument;
  if (ext === ".docx") return t.wordDocument;
  if (ext === ".jpg") return t.jpgImage;
  if (ext === ".jpeg") return t.jpegImage;
  if (ext === ".png") return t.pngImage;
  return t.unknownFile;
}

function getSourceOutputMode(files = []) {
  if (!files.length) return "none";
  const extensions = files.map((file) => getFileExtension(file.name));
  const pdfCount = extensions.filter((ext) => ext === ".pdf").length;
  const nonPdfCount = extensions.length - pdfCount;

  if (files.length === 1 && pdfCount === 1) return "single_pdf";
  if (files.length === 1 && nonPdfCount === 1) return "single_non_pdf";
  if (pdfCount > 0 && nonPdfCount > 0) return "mixed_document_set";
  if (pdfCount > 1) return "pdf_document_set";
  return "non_pdf_document_set";
}

function getReportOutputExtension(reportVariant, sourceOutputMode) {
  if (reportVariant === "machine_readable_report") return "json";
  if (
    reportVariant === "annotated_source_output" &&
    ["mixed_document_set", "pdf_document_set", "non_pdf_document_set"].includes(sourceOutputMode)
  ) {
    return "zip";
  }
  return "pdf";
}

function buildFallbackFilename(files = [], reportVariant = "human_readable_report", sourceOutputMode = "none") {
  const ext = getReportOutputExtension(reportVariant, sourceOutputMode);
  const stem = files.length === 1 ? getFileStem(files[0]?.name) : "compliance-document-set";
  return `${stem}_compliance_report.${ext}`;
}

function extractResponseMessage(responseData, fallbackMessage = "") {
  const detail = responseData?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail?.message === "string" && detail.message.trim()) return detail.message.trim();
  if (typeof detail?.error === "string" && detail.error.trim()) return detail.error.trim();
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
  const artifact = responseData?.artifact || responseData?.output_artifact || {};
  const result =
    responseData?.analyzer_response?.result ||
    responseData?.response?.result ||
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

  return {
    storageKey,
    downloadUrl,
    filename,
    outputFormat: pickFirstString([result?.output_format, result?.outputFormat, responseData?.output_format, responseData?.outputFormat]),
    reportVariant: pickFirstString([result?.report_variant, result?.reportVariant, responseData?.report_variant, responseData?.reportVariant]),
    fileSizeMb: result?.file_size_mb ?? result?.fileSizeMb ?? null,
    contentType: pickFirstString([artifact?.content_type, artifact?.contentType, result?.content_type, result?.contentType]),
  };
}

function extractComplianceCounts(responseData) {
  const counts = [
    responseData?.counts,
    responseData?.report?.counts,
    responseData?.preview?.report?.counts,
    responseData?.compliance_report?.counts,
    responseData?.data?.report?.counts,
    responseData?.analyzer_response?.report?.counts,
  ].find((item) => item && typeof item === "object");

  if (!counts) return null;

  return {
    evidence_found: counts.evidence_found ?? counts.passed ?? 0,
    risk_detected: counts.risk_detected ?? counts.failed ?? 0,
    warning: counts.warning ?? 0,
    evidence_missing: counts.evidence_missing ?? counts.missing ?? 0,
    requires_review: counts.requires_review ?? counts.review_required ?? 0,
  };
}

function extractComplianceReport(responseData) {
  return (
    [
      responseData?.report,
      responseData?.preview?.report,
      responseData?.compliance_report,
      responseData?.data?.report,
      responseData?.analyzer_response?.report,
    ].find((item) => item && typeof item === "object") || null
  );
}

function getRuleStatusLabel(status, t) {
  return t.ruleStatusLabels?.[status] || status;
}

function getOverallStatusLabel(status, t) {
  return t.overallStatusLabels?.[status] || status;
}

function getRuleStatusClasses(status) {
  if (status === "evidence_found") {
    return "border-emerald-400/20 bg-emerald-400/10 text-emerald-100";
  }
  if (status === "risk_detected") {
    return "border-red-400/20 bg-red-400/10 text-red-100";
  }
  if (status === "warning" || status === "evidence_missing") {
    return "border-amber-400/20 bg-amber-400/10 text-amber-100";
  }
  return "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-accent-text)]";
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
  clearLabel,
  onClear,
  lockedValues = [],
  lockedLabel = "required",
  disabled = false,
}) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const filteredItems = items.filter((item) => getLabel(item).toLowerCase().includes(normalizedQuery));

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-sm font-medium app-text-muted">{title}</p>
        {selectedValues.length > lockedValues.length && onClear && (
          <button
            type="button"
            disabled={disabled}
            onClick={disabled ? undefined : onClear}
            className={`text-xs font-medium transition ${disabled ? "cursor-not-allowed app-text-soft" : "text-[var(--app-accent-text)]"}`}
          >
            {clearLabel}
          </button>
        )}
      </div>

      {helpText ? <p className="text-xs leading-5 app-text-soft">{helpText}</p> : null}
      {examplesText ? <p className="mt-1 text-xs leading-5 app-text-soft">{examplesText}</p> : null}

      <div className="mt-3 flex min-h-10 flex-wrap gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2">
        {selectedValues.map((value) => {
          const isLocked = lockedValues.includes(value);
          return (
            <button
              key={value}
              type="button"
              disabled={disabled || isLocked}
              onClick={() => !disabled && !isLocked && onToggle(value)}
              className={`rounded-full border px-3 py-1 text-xs transition ${
                isLocked
                  ? "cursor-not-allowed border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-accent-text)]"
                  : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:border-[var(--app-accent-border)]"
              }`}
            >
              {getLabel(value)}{isLocked ? ` · ${lockedLabel}` : " ×"}
            </button>
          );
        })}
        {!selectedValues.length ? <span className="py-1 text-xs app-text-soft">{emptyText}</span> : null}
      </div>

      <input
        type="search"
        value={query}
        disabled={disabled}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={searchPlaceholder}
        className="mt-3 w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
      />

      <div className="mt-3 grid max-h-28 gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
        {filteredItems.map((item) => {
          const checked = selectedValues.includes(item);
          const isLocked = lockedValues.includes(item);
          return (
            <button
              key={item}
              type="button"
              disabled={disabled || isLocked}
              onClick={() => !disabled && onToggle(item)}
              className={`flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-left text-xs transition ${
                checked
                  ? "border-[var(--app-accent-border)] bg-cyan-300/15 text-[var(--app-accent-text)]"
                  : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:bg-[var(--app-surface-strong)]"
              } ${disabled || isLocked ? "cursor-not-allowed opacity-80" : ""}`}
            >
              <span>{getLabel(item)}</span>
              {checked ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0" /> : null}
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
  const extension = getFileExtension(file.name);
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
  artifactUrl = "",
  storageKey = "",
  filename = "",
  contentType = "",
  textContent = "",
  textFilename = "",
  title = "ReDOCX output",
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
    const extension = getFileExtension(sourceFile.name);
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
          downloadFilenameFromUrl(resultNode?.download_url || resultNode?.downloadUrl),
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


export default function CompliancePage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();

  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    compliancePageTranslations[language] || compliancePageTranslations.en;

  const [jurisdiction, setJurisdiction] = useState(DEFAULT_JURISDICTION);
  const selectedCountryConfig =
    COUNTRY_CONFIG[jurisdiction] || COUNTRY_CONFIG[DEFAULT_JURISDICTION];
  const countryLabels = t.countryLabels || {};
  const selectedCountryLabel =
    countryLabels[selectedCountryConfig.labelKey] || jurisdiction;

  const [selectedFiles, setSelectedFiles] = useState([]);
  const [sectorPacks, setSectorPacks] = useState([
    COUNTRY_CONFIG[DEFAULT_JURISDICTION].corePack,
  ]);
  const [regulatoryDomains, setRegulatoryDomains] = useState([]);
  const [reportVariant, setReportVariant] = useState("human_readable_report");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [previewMarkdown, setPreviewMarkdown] = useState("");
  const [resultSummary, setResultSummary] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);
  const [counts, setCounts] = useState(null);
  const [complianceReport, setComplianceReport] = useState(null);
  const [deployedOptions, setDeployedOptions] = useState(null);
  const [optionsLoading, setOptionsLoading] = useState(true);
  const [optionsError, setOptionsError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    async function loadOptions() {
      setOptionsLoading(true);
      setOptionsError("");
      try {
        const response = await fetch(COMPLIANCE_OPTIONS_ENDPOINT, {
          credentials: "include",
          cache: "no-store",
          signal: controller.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(extractResponseMessage(data, t.ruleOptionsUnavailable));
        }
        const jurisdictions = Array.isArray(data?.jurisdictions)
          ? data.jurisdictions.filter((item) => {
              const config = COUNTRY_CONFIG[item?.value];
              return (
                config &&
                Array.isArray(item?.sector_packs) &&
                item.sector_packs.some(
                  (pack) => pack?.value === config.corePack,
                )
              );
            })
          : [];
        if (!jurisdictions.length) throw new Error(t.ruleOptionsUnavailable);
        const normalized = { ...data, jurisdictions };
        setDeployedOptions(normalized);
        const active =
          jurisdictions.find(
            (item) => item.value === DEFAULT_JURISDICTION,
          ) ||
          jurisdictions[0];
        const activeConfig = COUNTRY_CONFIG[active.value];
        const deployedPacks = new Set(
          active.sector_packs.map((pack) => pack.value),
        );
        setJurisdiction(active.value);
        setSectorPacks((current) =>
          uniqueStrings([
            activeConfig.corePack,
            ...current.filter((pack) => deployedPacks.has(pack)),
          ]),
        );
      } catch (caught) {
        if (caught?.name !== "AbortError") {
          setOptionsError(caught?.message || t.ruleOptionsUnavailable);
        }
      } finally {
        if (!controller.signal.aborted) setOptionsLoading(false);
      }
    }
    loadOptions();
    return () => controller.abort();
  }, [t.ruleOptionsUnavailable]);

  const availableSectorPacks = useMemo(
    () => {
      const configured = [
        selectedCountryConfig.corePack,
        ...selectedCountryConfig.sectorPacks,
      ];
      if (!deployedOptions) return configured;
      const deployed = new Set(
        deployedOptions.jurisdictions
          .find((item) => item.value === jurisdiction)
          ?.sector_packs?.map((pack) => pack.value) || [],
      );
      return configured.filter((pack) => deployed.has(pack));
    },
    [deployedOptions, jurisdiction, selectedCountryConfig],
  );
  const availableRegulatoryDomains = useMemo(() => {
    if (!deployedOptions) return REGULATORY_DOMAINS;
    const packs =
      deployedOptions.jurisdictions.find((item) => item.value === jurisdiction)
        ?.sector_packs || [];
    const activePacks = packs.filter(
      (pack) => !sectorPacks.length || sectorPacks.includes(pack.value),
    );
    const deployed = new Set(
      activePacks.flatMap((pack) => pack.regulatory_domains || []),
    );
    return REGULATORY_DOMAINS.filter((domain) => deployed.has(domain));
  }, [deployedOptions, jurisdiction, sectorPacks]);
  const availableCountryEntries = useMemo(() => {
    const configured = Object.entries(COUNTRY_CONFIG);
    if (!deployedOptions) return configured;
    const deployed = new Set(
      deployedOptions.jurisdictions.map((item) => item.value),
    );
    return configured.filter(([value]) => deployed.has(value));
  }, [deployedOptions]);
  const sourceOutputMode = useMemo(
    () => getSourceOutputMode(selectedFiles),
    [selectedFiles],
  );
  const outputExtension = useMemo(
    () => getReportOutputExtension(reportVariant, sourceOutputMode),
    [reportVariant, sourceOutputMode],
  );
  const isProcessing = optionsLoading || isPreviewing || isSubmitting;
  const overallStatus =
    complianceReport?.overall_status ||
    complianceReport?.overallStatus ||
    "manual_review_needed";
  const recommendedNextSteps = Array.isArray(
    complianceReport?.recommended_next_steps,
  )
    ? complianceReport.recommended_next_steps
    : Array.isArray(complianceReport?.recommendedNextSteps)
      ? complianceReport.recommendedNextSteps
      : [];
  const complianceRuleResults = Array.isArray(complianceReport?.rule_results)
    ? complianceReport.rule_results
    : Array.isArray(complianceReport?.ruleResults)
      ? complianceReport.ruleResults
      : [];
  const qualityWarnings = Array.isArray(complianceReport?.quality_warnings)
    ? complianceReport.quality_warnings
    : Array.isArray(complianceReport?.qualityWarnings)
      ? complianceReport.qualityWarnings
      : [];

  const selectedSectorLabels = useMemo(
    () =>
      sectorPacks.map((pack) => t.sectorPackLabels?.[pack] || pack).join(", "),
    [sectorPacks, t],
  );
  const selectedDomainLabels = useMemo(() => {
    if (!regulatoryDomains.length) return t.allDomains;
    return regulatoryDomains
      .map((domain) => t.regulatoryDomainLabels?.[domain] || domain)
      .join(", ");
  }, [regulatoryDomains, t]);

  const sourceOutputLabel = useMemo(() => {
    if (reportVariant !== "annotated_source_output") return "";
    if (sourceOutputMode === "single_pdf") return t.annotatedSourcePdf;
    if (sourceOutputMode === "single_non_pdf") return t.evidenceOverlayReport;
    if (sourceOutputMode === "pdf_document_set")
      return t.annotatedSourcePdfPackage;
    if (sourceOutputMode === "mixed_document_set")
      return t.annotatedAndEvidencePackage;
    if (sourceOutputMode === "non_pdf_document_set")
      return t.evidenceOverlayReportPackage;
    return t.annotatedSourcePdf;
  }, [reportVariant, sourceOutputMode, t]);

  const reportVariantDescription =
    reportVariant === "annotated_source_output"
      ? replaceVars(t.annotatedSourceDynamicDescription, {
          output: sourceOutputLabel || t.annotatedSourcePdf,
        })
      : t.reportVariantDescriptions?.[reportVariant] || "";

  const canPreview =
    !isPreviewing &&
    !isSubmitting &&
    !optionsLoading &&
    !optionsError &&
    selectedFiles.length > 0 &&
    selectedFiles.length <= MAX_COMPLIANCE_FILES &&
    sectorPacks.includes(selectedCountryConfig.corePack) &&
    REPORT_VARIANTS.includes(reportVariant);

  const canGenerate = canPreview;

  function resetResultState() {
    setResultSummary("");
    setDownloadInfo(null);
    setCounts(null);
    setPreviewMarkdown("");
    setComplianceReport(null);
  }

  async function handlePickedFiles(fileList) {
    if (isProcessing) return;
    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const submittedFiles = [...selectedFiles, ...incomingFiles];
    const { acceptedFiles: files, duplicates } =
      await partitionDuplicateBrowserUploads(submittedFiles);

    if (files.length > MAX_COMPLIANCE_FILES) {
      setError(replaceVars(t.tooManyFiles, { maxFiles: MAX_COMPLIANCE_FILES }));
      return;
    }

    for (const file of files) {
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
        setError(replaceVars(t.unsupportedFileType, { ext: ext || "unknown" }));
        return;
      }

      if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
        setError(replaceVars(t.fileTooLarge, { maxSize: MAX_FILE_SIZE_MB }));
        return;
      }
    }

    setError(
      duplicates.length
        ? `${duplicates.length} duplicate file${duplicates.length === 1 ? " was" : "s were"} rejected. The remaining files are ready.`
        : "",
    );
    setSelectedFiles(files);
    resetResultState();
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

  function clearFiles() {
    if (isProcessing) return;
    setSelectedFiles([]);
    setError("");
    resetResultState();
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleRemoveFile(index) {
    if (isProcessing) return;
    setSelectedFiles((current) =>
      current.filter((_, fileIndex) => fileIndex !== index),
    );
    setError("");
    resetResultState();
  }

  function handleJurisdictionChange(nextJurisdiction) {
    if (isProcessing) return;
    const nextConfig =
      COUNTRY_CONFIG[nextJurisdiction] || COUNTRY_CONFIG[DEFAULT_JURISDICTION];
    setJurisdiction(nextJurisdiction);
    setSectorPacks([nextConfig.corePack]);
    setRegulatoryDomains([]);
    setError("");
    resetResultState();
  }

  function toggleSectorPack(value) {
    if (isProcessing) return;
    const corePack = selectedCountryConfig.corePack;
    setSectorPacks((current) => {
      if (value === corePack)
        return current.includes(corePack) ? current : [corePack, ...current];
      if (current.includes(value))
        return current.filter((item) => item !== value);
      return uniqueStrings([corePack, ...current, value]);
    });
    setRegulatoryDomains([]);
    setError("");
    resetResultState();
  }

  function toggleRegulatoryDomain(value) {
    if (isProcessing) return;
    setRegulatoryDomains((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
    setError("");
    resetResultState();
  }

  function clearRegulatoryDomains() {
    if (isProcessing) return;
    setRegulatoryDomains([]);
    setError("");
    resetResultState();
  }

  function getArtifactDownloadUrl(info) {
    if (!info) return "";
    return (
      normalizeAnalyzerArtifactUrl(info.downloadUrl) ||
      buildAnalyzerArtifactUrl(info.storageKey)
    );
  }

  function handleDownload() {
    const url = getArtifactDownloadUrl(downloadInfo);
    if (!url) {
      setError(t.missingDownloadUrl);
      return;
    }
    const link = document.createElement("a");
    link.href = url;
    link.download = downloadInfo.filename || "compliance-report";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  function buildComplianceFormData({ generateReport = false } = {}) {
    const formData = new FormData();
    const fileFieldName = selectedFiles.length > 1 ? "files" : "file";

    for (const selectedFile of selectedFiles) {
      formData.append(fileFieldName, selectedFile, selectedFile.name);
    }

    formData.append("jurisdiction", jurisdiction);
    formData.append("report_variant", reportVariant);
    formData.append("require_human_review", "true");
    formData.append(
      "system_language",
      language === "fr" ? "french" : "english",
    );

    for (const sectorPack of sectorPacks)
      formData.append("sector_packs", sectorPack);
    for (const regulatoryDomain of regulatoryDomains)
      formData.append("regulatory_domains", regulatoryDomain);
    if (generateReport) formData.append("generate_report", "true");

    return formData;
  }

  function validateBeforeRequest() {
    if (!selectedFiles.length) {
      setError(t.chooseFileToCheck);
      return false;
    }
    if (selectedFiles.length > MAX_COMPLIANCE_FILES) {
      setError(replaceVars(t.tooManyFiles, { maxFiles: MAX_COMPLIANCE_FILES }));
      return false;
    }
    if (!sectorPacks.includes(selectedCountryConfig.corePack)) {
      setError(
        replaceVars(t.corePackRequired, { country: selectedCountryLabel }),
      );
      return false;
    }
    return true;
  }

  async function handlePreview(event) {
    event?.preventDefault();
    if (!validateBeforeRequest()) return;

    setIsPreviewing(true);
    setError("");
    setResultSummary("");
    setDownloadInfo(null);
    setCounts(null);
    setPreviewMarkdown("");
    setComplianceReport(null);

    try {
      const response = await fetch(COMPLIANCE_PREVIEW_ENDPOINT, {
        method: "POST",
        body: buildComplianceFormData(),
        credentials: "include",
      });
      const responseData = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          extractResponseMessage(responseData, t.complianceFailed),
        );

      const previewText =
        responseData?.preview_markdown || responseData?.previewMarkdown || "";
      const report = extractComplianceReport(responseData);
      setPreviewMarkdown(previewText);
      setComplianceReport(report);
      setCounts(extractComplianceCounts(responseData));

      const inputLines = selectedFiles
        .map((file, index) => `${index + 1}. ${file.name}`)
        .join("\n");
      setResultSummary(
        [
          t.previewCompleted,
          report?.plain_language_summary || report?.plainLanguageSummary || "",
          "",
          `${t.inputFiles}:`,
          inputLines,
          `${t.jurisdictionResult}: ${selectedCountryLabel}`,
          `${t.sectorPacksResult}: ${selectedSectorLabels}`,
          "",
          t.humanReviewRequired,
        ]
          .filter(
            (line, index, items) => line !== "" || items[index - 1] !== "",
          )
          .join("\n"),
      );
    } catch (previewError) {
      setError(previewError?.message || t.complianceFailed);
    } finally {
      setIsPreviewing(false);
    }
  }

  async function handleSubmit(event) {
    event?.preventDefault();
    if (!validateBeforeRequest()) return;

    setIsSubmitting(true);
    setError("");
    setDownloadInfo(null);

    try {
      const fallbackFilename = buildFallbackFilename(
        selectedFiles,
        reportVariant,
        sourceOutputMode,
      );
      const response = await fetch(COMPLIANCE_PREVIEW_ENDPOINT, {
        method: "POST",
        body: buildComplianceFormData({ generateReport: true }),
        credentials: "include",
      });
      const responseData = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          extractResponseMessage(responseData, t.complianceFailed),
        );

      const resolvedDownload = extractDownloadInfo(
        responseData,
        fallbackFilename,
      );
      const generatedReport = extractComplianceReport(responseData);
      const resolvedCounts = extractComplianceCounts(responseData) || counts;
      setDownloadInfo(resolvedDownload);
      setCounts(resolvedCounts);
      if (generatedReport) setComplianceReport(generatedReport);
      setPreviewMarkdown(
        responseData?.preview_markdown || responseData?.previewMarkdown || "",
      );

      const reportVariantLabel =
        reportVariant === "annotated_source_output"
          ? sourceOutputLabel
          : t.reportVariantLabels?.[reportVariant] || reportVariant;
      const outputFormatLabel =
        t.outputFormatLabels?.[outputExtension] || `.${outputExtension}`;
      const inputLines = selectedFiles
        .map(
          (file, index) =>
            `${index + 1}. ${file.name} (${getFileTypeLabel(getFileExtension(file.name), t)})`,
        )
        .join("\n");

      const summaryLines = [
        t.complianceCompleted,
        generatedReport?.plain_language_summary ||
          generatedReport?.plainLanguageSummary ||
          complianceReport?.plain_language_summary ||
          complianceReport?.plainLanguageSummary ||
          "",
        "",
        `${t.inputFiles}:`,
        inputLines,
        `${t.jurisdictionResult}: ${selectedCountryLabel}`,
        `${t.sectorPacksResult}: ${selectedSectorLabels}`,
        `${t.regulatoryDomainsResult}: ${selectedDomainLabels}`,
        `${t.reportVariantResult}: ${reportVariantLabel}`,
        `${t.outputFormatResult}: ${outputFormatLabel}`,
        `${t.reportFile}: ${resolvedDownload.filename || fallbackFilename}`,
      ];

      if (resolvedCounts) {
        summaryLines.push(
          "",
          t.findingsSummary,
          `${t.evidenceFound}: ${resolvedCounts.evidence_found}`,
          `${t.riskDetected}: ${resolvedCounts.risk_detected}`,
          `${t.warning}: ${resolvedCounts.warning}`,
          `${t.evidenceMissing}: ${resolvedCounts.evidence_missing}`,
          `${t.reviewRequiredCount}: ${resolvedCounts.requires_review}`,
        );
      }

      summaryLines.push(
        "",
        resolvedDownload.downloadUrl ? t.outputReadyText : t.missingDownloadUrl,
        "",
        t.humanReviewRequired,
      );
      setResultSummary(summaryLines.join("\n"));
    } catch (submitError) {
      setError(submitError?.message || t.complianceFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  const outputIcon =
    reportVariant === "machine_readable_report"
      ? FileJson
      : reportVariant === "annotated_source_output" && outputExtension === "zip"
        ? PackageCheck
        : reportVariant === "annotated_source_output"
          ? ClipboardCheck
          : FileText;
  const OutputIcon = outputIcon;

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
              <h1 className="max-w-full text-2xl font-semibold tracking-tight text-[var(--app-text)] sm:text-3xl lg:text-[2.15rem] lg:leading-tight xl:text-[2.35rem]">
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
                      maxFiles: MAX_COMPLIANCE_FILES,
                    })}
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
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
                    className={`mt-3 rounded-2xl px-4 py-2.5 text-sm font-semibold transition ${isProcessing ? "cursor-not-allowed bg-[var(--app-surface)] app-text-soft" : "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"}`}
                  >
                    {t.chooseFiles || common.chooseFile}
                  </button>
                </div>

                {selectedFiles.length > 0 && (
                  <div className="mt-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-3">
                        <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                        <div className="min-w-0">
                          <p className="font-medium text-emerald-100">
                            {replaceVars(t.filesAccepted, {
                              count: selectedFiles.length,
                            })}
                          </p>
                          <div className="mt-2 space-y-1">
                            {selectedFiles.map((file, index) => {
                              const ext = getFileExtension(file.name);
                              return (
                                <div
                                  key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                                  className="flex items-center gap-2 text-sm text-emerald-100/80"
                                >
                                  <p className="min-w-0 flex-1 truncate">
                                    {file.name} • {formatBytes(file.size)} •{" "}
                                    {getFileTypeLabel(ext, t)}
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
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={clearFiles}
                        disabled={isProcessing}
                        className="rounded-full border border-emerald-300/20 px-3 py-1 text-xs text-emerald-100/80"
                      >
                        {t.clearFiles}
                      </button>
                    </div>
                  </div>
                )}

                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="block">
                    <span className="mb-2 block text-sm font-medium app-text-muted">
                      {t.jurisdictionLabel}
                    </span>
                    <select
                      value={jurisdiction}
                      disabled={isProcessing}
                      onChange={(event) =>
                        handleJurisdictionChange(event.target.value)
                      }
                      className="w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                    >
                      {availableCountryEntries.map(([value, config]) => (
                        <option
                          key={value}
                          value={value}
                          className="bg-[var(--app-panel)] text-[var(--app-text)]"
                        >
                          {countryLabels[config.labelKey] || value}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.jurisdictionHelp}
                    </p>
                  </label>

                  <label className="block">
                    <span className="mb-2 block text-sm font-medium app-text-muted">
                      {t.reportVariantLabel}
                    </span>
                    <select
                      value={reportVariant}
                      disabled={isProcessing}
                      onChange={(event) => {
                        setReportVariant(event.target.value);
                        setError("");
                        resetResultState();
                      }}
                      className="w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                    >
                      {REPORT_VARIANTS.map((variant) => (
                        <option
                          key={variant}
                          value={variant}
                          className="bg-[var(--app-panel)] text-[var(--app-text)]"
                        >
                          {variant === "annotated_source_output" &&
                          sourceOutputLabel
                            ? sourceOutputLabel
                            : t.reportVariantLabels?.[variant] || variant}
                        </option>
                      ))}
                    </select>
                    <p className="mt-1 text-xs leading-5 app-text-soft">
                      {t.reportVariantHelp}
                    </p>
                    {reportVariantDescription ? (
                      <p className="mt-1 rounded-xl border border-cyan-300/20 bg-[var(--app-accent-bg)] px-3 py-2 text-xs leading-5 text-[var(--app-accent-text)]">
                        {reportVariantDescription}
                      </p>
                    ) : null}
                  </label>
                </div>

                <div className="mt-3 rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-3">
                  <SearchableMultiSelect
                    title={t.sectorPacksLabel}
                    disabled={isProcessing}
                    helpText={replaceVars(t.corePackHelp, {
                      country: selectedCountryLabel,
                    })}
                    emptyText={t.sectorPacksEmptyHelp}
                    examplesText={t.sectorPacksExamples}
                    items={availableSectorPacks}
                    selectedValues={sectorPacks}
                    onToggle={toggleSectorPack}
                    getLabel={(pack) => t.sectorPackLabels?.[pack] || pack}
                    searchPlaceholder={t.searchSectorPacksPlaceholder}
                    clearLabel={t.clearSectorPacks}
                    lockedValues={[selectedCountryConfig.corePack]}
                    lockedLabel={t.requiredLabel}
                  />
                </div>

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                  <SearchableMultiSelect
                    title={t.regulatoryDomainsLabel}
                    disabled={isProcessing}
                    helpText={t.regulatoryDomainsHelp}
                    emptyText={t.regulatoryDomainsEmptyHelp}
                    examplesText={t.regulatoryDomainsExamples}
                    items={availableRegulatoryDomains}
                    selectedValues={regulatoryDomains}
                    onToggle={toggleRegulatoryDomain}
                    getLabel={(domain) =>
                      t.regulatoryDomainLabels?.[domain] || domain
                    }
                    searchPlaceholder={t.searchRegulatoryDomainsPlaceholder}
                    clearLabel={t.clearDomains}
                    onClear={clearRegulatoryDomains}
                  />
                </div>

                {error || optionsError ? (
                  <div className="mt-3 rounded-2xl border border-red-400/20 bg-red-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">
                        {error || optionsError}
                      </p>
                    </div>
                  </div>
                ) : null}

                <div className="mt-auto pt-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="submit"
                      disabled={!canGenerate}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${canGenerate ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl" : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"}`}
                    >
                      {isSubmitting ? t.checking : t.generateFileAction}
                    </button>
                    <button
                      type="button"
                      disabled={!canPreview}
                      onClick={handlePreview}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${canPreview ? "border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-accent-text)] hover:bg-[var(--app-accent-bg)]" : "cursor-not-allowed border border-[var(--app-border)] bg-[var(--app-surface)] app-text-soft"}`}
                    >
                      {isPreviewing ? t.previewing : t.previewAction}
                    </button>
                    {getArtifactDownloadUrl(downloadInfo) ? (
                      <button
                        type="button"
                        onClick={handleDownload}
                        className="inline-flex items-center gap-2 rounded-2xl border border-emerald-300/30 bg-emerald-400/10 px-5 py-2.5 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-400/15"
                      >
                        <Download className="h-4 w-4" />
                        {common.download}
                      </button>
                    ) : null}
                  </div>
                  <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-soft">
                    {t.complianceLabel}{" "}
                    <span className="font-medium app-text-muted">
                      {selectedCountryLabel}
                    </span>
                  </div>
                </div>
              </div>
            </form>

            <aside className="min-h-0 lg:h-full">
              <div className="flex min-h-[360px] flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5 lg:min-h-[calc(100vh-8.5rem)] lg:max-h-[calc(100vh-8.5rem)]">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.complianceOutput}
                  </h2>
                  <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1 text-xs app-text-soft">
                    {selectedFiles.length || 0}/{MAX_COMPLIANCE_FILES}{" "}
                    {t.filesLabel}
                  </span>
                </div>

                {counts ? (
                  <div className="mt-3 grid grid-cols-5 gap-2 text-center text-xs">
                    <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-2 text-emerald-100">
                      <p className="font-semibold">{counts.evidence_found}</p>
                      <p className="mt-1 text-[10px] opacity-80">
                        {t.evidenceFound}
                      </p>
                    </div>
                    <div className="rounded-2xl border border-red-400/20 bg-red-400/10 p-2 text-red-100">
                      <p className="font-semibold">{counts.risk_detected}</p>
                      <p className="mt-1 text-[10px] opacity-80">
                        {t.riskDetected}
                      </p>
                    </div>
                    <div className="rounded-2xl border border-amber-400/20 bg-amber-400/10 p-2 text-amber-100">
                      <p className="font-semibold">{counts.warning}</p>
                      <p className="mt-1 text-[10px] opacity-80">{t.warning}</p>
                    </div>
                    <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2 app-text-muted">
                      <p className="font-semibold">{counts.evidence_missing}</p>
                      <p className="mt-1 text-[10px] opacity-80">
                        {t.evidenceMissing}
                      </p>
                    </div>
                    <div className="rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-2 text-[var(--app-accent-text)]">
                      <p className="font-semibold">{counts.requires_review}</p>
                      <p className="mt-1 text-[10px] opacity-80">
                        {t.reviewRequiredShort}
                      </p>
                    </div>
                  </div>
                ) : null}

                <div className="mt-3 min-h-[320px] flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4 lg:max-h-none">
                  {resultSummary ? (
                    <div className="flex h-full min-h-0 flex-col gap-3">
                      <pre className="whitespace-pre-wrap break-words pr-1 text-xs leading-6 app-text-muted md:text-sm">
                        {resultSummary}
                      </pre>

                      {complianceReport ? (
                        <div className="grid gap-3">
                          <div
                            className={`rounded-2xl border p-4 ${
                              overallStatus === "ready_for_final_review"
                                ? "border-emerald-400/20 bg-emerald-400/10"
                                : overallStatus === "changes_recommended"
                                  ? "border-amber-400/20 bg-amber-400/10"
                                  : "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)]"
                            }`}
                          >
                            <p className="text-xs font-medium uppercase tracking-wide app-text-soft">
                              {t.overallResult}
                            </p>
                            <p className="mt-1 text-base font-semibold text-[var(--app-text)]">
                              {getOverallStatusLabel(overallStatus, t)}
                            </p>
                            <p className="mt-2 text-sm leading-6 app-text-muted">
                              {complianceReport?.plain_language_summary ||
                                complianceReport?.plainLanguageSummary}
                            </p>
                          </div>

                          {qualityWarnings.length > 0 ? (
                            <div className="rounded-2xl border border-amber-400/25 bg-amber-400/10 p-4">
                              <p className="text-sm font-semibold text-amber-100">
                                {t.documentQualityNotes}
                              </p>
                              <ul className="mt-2 grid list-disc gap-1.5 pl-5 text-sm leading-6 text-amber-100/85">
                                {qualityWarnings.map((warning, index) => (
                                  <li key={`${index}-${warning}`}>{warning}</li>
                                ))}
                              </ul>
                            </div>
                          ) : null}

                          {recommendedNextSteps.length > 0 ? (
                            <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                              <p className="text-sm font-semibold text-[var(--app-text)]">
                                {t.whatToDoNext}
                              </p>
                              <ol className="mt-3 grid list-decimal gap-2 pl-5 text-sm leading-6 app-text-muted">
                                {recommendedNextSteps.map((action, index) => (
                                  <li key={`${index}-${action}`}>{action}</li>
                                ))}
                              </ol>
                            </div>
                          ) : null}

                          {complianceRuleResults.length > 0 ? (
                            <div className="grid gap-3">
                              <p className="text-sm font-semibold text-[var(--app-text)]">
                                {t.checkResults}
                              </p>
                              {complianceRuleResults.map((rule, ruleIndex) => {
                                const status =
                                  rule?.status || "requires_review";
                                const actions = Array.isArray(
                                  rule?.recommended_actions,
                                )
                                  ? rule.recommended_actions
                                  : Array.isArray(rule?.recommendedActions)
                                    ? rule.recommendedActions
                                    : [];
                                const evidence = Array.isArray(
                                  rule?.evidence_references,
                                )
                                  ? rule.evidence_references
                                  : Array.isArray(rule?.evidenceReferences)
                                    ? rule.evidenceReferences
                                    : [];
                                const firstEvidence = evidence[0] || null;
                                const sourceDocumentIndex =
                                  firstEvidence?.source_document_index ??
                                  firstEvidence?.sourceDocumentIndex;
                                const pageNumber =
                                  firstEvidence?.page_number ??
                                  firstEvidence?.pageNumber;

                                return (
                                  <div
                                    key={`${rule?.rule_id || rule?.ruleId || ruleIndex}-${ruleIndex}`}
                                    className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4"
                                  >
                                    <div className="flex flex-wrap items-start justify-between gap-2">
                                      <p className="min-w-0 flex-1 text-sm font-semibold text-[var(--app-text)]">
                                        {rule?.title}
                                      </p>
                                      <span
                                        className={`rounded-full border px-2.5 py-1 text-[0.68rem] font-medium ${getRuleStatusClasses(status)}`}
                                      >
                                        {getRuleStatusLabel(status, t)}
                                      </span>
                                    </div>
                                    <p className="mt-2 text-sm leading-6 app-text-muted">
                                      {rule?.plain_language_summary ||
                                        rule?.plainLanguageSummary ||
                                        rule?.summary}
                                    </p>

                                    {actions.length > 0 ? (
                                      <div className="mt-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3">
                                        <p className="text-xs font-semibold text-[var(--app-text)]">
                                          {t.whatToDo}
                                        </p>
                                        <ol className="mt-2 grid list-decimal gap-1.5 pl-4 text-xs leading-5 app-text-muted">
                                          {actions.map((action, index) => (
                                            <li key={`${index}-${action}`}>
                                              {action}
                                            </li>
                                          ))}
                                        </ol>
                                      </div>
                                    ) : null}

                                    <div className="mt-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3 text-xs leading-5 app-text-soft">
                                      <p className="font-semibold app-text-muted">
                                        {t.supportingEvidence}
                                      </p>
                                      {firstEvidence ? (
                                        <>
                                          <p className="mt-1">
                                            {t.documentLabel}{" "}
                                            {Number(sourceDocumentIndex ?? 0) +
                                              1}
                                            {pageNumber
                                              ? ` · ${t.pageLabel} ${pageNumber}`
                                              : ""}
                                          </p>
                                          {firstEvidence?.excerpt ? (
                                            <p className="mt-1">
                                              “{firstEvidence.excerpt}”
                                            </p>
                                          ) : null}
                                          {evidence.length > 1 ? (
                                            <p className="mt-1">
                                              {replaceVars(
                                                t.moreEvidenceLocations,
                                                {
                                                  count: evidence.length - 1,
                                                },
                                              )}
                                            </p>
                                          ) : null}
                                        </>
                                      ) : (
                                        <p className="mt-1">
                                          {t.noSupportingEvidence}
                                        </p>
                                      )}
                                    </div>

                                    <details className="mt-3 text-xs app-text-soft">
                                      <summary className="cursor-pointer font-medium app-text-muted">
                                        {t.ruleDetails}
                                      </summary>
                                      <p className="mt-2">
                                        {t.ruleReferenceLabel}:{" "}
                                        {rule?.rule_id || rule?.ruleId}
                                      </p>
                                      <p>
                                        {t.ruleVersionLabel}:{" "}
                                        {rule?.rule_version ||
                                          rule?.ruleVersion}
                                      </p>
                                      {rule?.sector_pack || rule?.sectorPack ? (
                                        <p>
                                          {t.rulePackLabel}:{" "}
                                          {t.sectorPackLabels?.[
                                            rule?.sector_pack ||
                                              rule?.sectorPack
                                          ] ||
                                            rule?.sector_pack ||
                                            rule?.sectorPack}
                                        </p>
                                      ) : null}
                                    </details>
                                  </div>
                                );
                              })}
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {getArtifactDownloadUrl(downloadInfo) ? (
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
                                artifactUrl={getArtifactDownloadUrl(
                                  downloadInfo,
                                )}
                                storageKey={downloadInfo.storageKey}
                                filename={downloadInfo.filename}
                                contentType={downloadInfo.contentType}
                                title="Compliance report"
                                language={language}
                                account={account}
                              />
                            </div>
                          </div>
                        </div>
                      ) : null}
                    </div>
                  ) : (
                    <div className="flex h-full min-h-[180px] items-center justify-center rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center">
                      <div>
                        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                          <OutputIcon className="h-5 w-5 text-cyan-300" />
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
                      {t.outputTitle}
                    </p>
                    <p className="mt-1">.{outputExtension}</p>
                  </div>
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                    <p className="font-medium app-text-muted">
                      {t.reviewTitle}
                    </p>
                    <p className="mt-1">{t.reviewValue}</p>
                  </div>
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                    <p className="font-medium app-text-muted">{t.scopeTitle}</p>
                    <p className="mt-1">{t.scopeValue}</p>
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
