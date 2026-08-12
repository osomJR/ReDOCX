"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  EyeClosed,
  Download,
} from "lucide-react";
import {
  commonTranslations,
  dataMaskPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import {
  FILE_SECURITY_POLICY,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 25;

const DOCUMENT_TYPES = [
  { value: "invoice", label: "Invoice" },
  { value: "kyc_document", label: "KYC document" },
  { value: "bank_statement", label: "Bank statement" },
  { value: "contract", label: "Contract" },
  { value: "id_document", label: "ID document" },
  { value: "legal_document", label: "Legal document" },
];

const SENSITIVE_LABELS = {
  name: "Name",
  email_address: "Email address",
  phone_number: "Phone number",
  account_number: "Account number",
  card_number: "Card number",
  national_id: "National / government ID (SSN, SIN, NIN)",
  tax_id: "Tax ID",
  passport_number: "Passport number",
  contact_address: "Contact address",
  date_of_birth: "Date of birth",
  age: "Age",
  signature: "Signature",
  custom_mask: "Custom mask",
};

const SUPPORTED_BY_DOCUMENT_TYPE = {
  invoice: [
    "name",
    "email_address",
    "phone_number",
    "account_number",
    "card_number",
    "tax_id",
    "contact_address",
    "signature",
  ],
  kyc_document: [
    "name",
    "email_address",
    "phone_number",
    "account_number",
    "national_id",
    "tax_id",
    "passport_number",
    "contact_address",
    "date_of_birth",
    "age",
    "signature",
  ],
  bank_statement: [
    "name",
    "email_address",
    "phone_number",
    "account_number",
    "card_number",
    "national_id",
    "tax_id",
    "contact_address",
    "date_of_birth",
    "signature",
  ],
  contract: [
    "name",
    "email_address",
    "phone_number",
    "account_number",
    "tax_id",
    "contact_address",
    "signature",
  ],
  id_document: [
    "name",
    "national_id",
    "tax_id",
    "passport_number",
    "contact_address",
    "date_of_birth",
    "age",
    "signature",
  ],
  legal_document: [
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
  ],
};

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

export default function DataMaskPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();

  const common = commonTranslations[language] || commonTranslations.en;
  const t = dataMaskPageTranslations[language] || dataMaskPageTranslations.en;

  const [selectedFile, setSelectedFile] = useState(null);
  const [documentType, setDocumentType] = useState("legal_document");
  const [targetData, setTargetData] = useState(
    SUPPORTED_BY_DOCUMENT_TYPE.legal_document,
  );
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

  const approvedCount = reviewCandidates.filter((candidate) =>
    approvedCandidateIds.has(candidateId(candidate)),
  ).length;

  const deselectedCount = reviewCandidates.length - approvedCount;
  const isBusy = isReviewing || isFinalizing || stage === "done";

  useEffect(() => {
    setTargetData(SUPPORTED_BY_DOCUMENT_TYPE[documentType] || []);
  }, [documentType]);

  function resetResultState() {
    setResultSummary("");
    setDownloadInfo(null);
    setProcessedPreviewUrl("");
    setReviewCandidates([]);
    setApprovedCandidateIds(new Set());
    setStage("edit");
  }

  function rejectFile(message) {
    setSelectedFile(null);
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (isBusy) return;
    if (!file) return;

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.documentWithImages,
    );
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
    if (isBusy) return;
    const file = event.dataTransfer.files?.[0];
    handlePickedFile(file);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function toggleTarget(value) {
    if (isBusy) return;
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

    if (stage === "done") return;

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
        `${t.documentTypeResult}: ${
          DOCUMENT_TYPES.find((item) => item.value === documentType)?.label ||
          documentType
        }`,
        `${t.reviewItemsLabel}: ${candidates.length}`,
        `${t.selectedTargetsLabel} ${targetData.length}`,
        "",
        t.reviewHint,
      ];

      setResultSummary(summaryLines.join("\n"));
    } catch (submitError) {
      setError(submitError?.message || t.maskingFailed);
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
        `${t.documentTypeResult}: ${
          DOCUMENT_TYPES.find((item) => item.value === documentType)?.label ||
          documentType
        }`,
        `${t.reviewItemsLabel}: ${reviewCandidates.length}`,
        `${t.approvedCountLabel}: ${approvedCount}`,
        `${t.deselectedCountLabel}: ${deselectedCount}`,
        `${t.exclusionsCount}: ${
          buildReviewExclusions(
            reviewCandidates,
            approvedCandidateIds,
          ).length
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
      setError(submitError?.message || t.maskingFailed);
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
                        if (isBusy) return;
                        setDocumentType(e.target.value);
                        setError("");
                        resetResultState();
                      }}
                      disabled={isBusy}
                      className="mt-2 w-full rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] px-3 py-2 text-sm text-[var(--app-text)] outline-none transition disabled:cursor-not-allowed disabled:opacity-50 focus:border-[var(--app-accent-border)]"
                    >
                      {DOCUMENT_TYPES.map((item) => (
                        <option
                          key={item.value}
                          value={item.value}
                          className="bg-[var(--app-panel)]"
                        >
                          {item.label}
                        </option>
                      ))}
                    </select>
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
                          if (isBusy) return;
                          setTargetData(supportedTargets);
                          setError("");
                          resetResultState();
                        }}
                        disabled={isBusy}
                        className="rounded-lg border border-[var(--app-border)] bg-[var(--app-surface)] px-2.5 py-1.5 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                      >
                        {t.selectAll}
                      </button>

                      <button
                        type="button"
                        onClick={() => {
                          if (isBusy) return;
                          setTargetData([]);
                          setError("");
                          resetResultState();
                        }}
                        disabled={isBusy}
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
                            disabled={isBusy}
                            className="h-4 w-4 rounded border-[var(--app-border)] bg-transparent disabled:cursor-not-allowed"
                          />
                          <span>{SENSITIVE_LABELS[item] || item}</span>
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
                      !selectedFile ||
                      !isValidFile ||
                      !documentType ||
                      targetData.length === 0
                    }
                    className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                      !isReviewing &&
                      !isFinalizing &&
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
                      <ProcessedOutputActions
                        artifactUrl={downloadInfo.downloadUrl}
                        filename={downloadInfo.filename}
                        title="Masked output"
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
                            title="Processed preview"
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
                          <img
                            src={processedPreviewUrl}
                            alt="Processed preview"
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
                                        {SENSITIVE_LABELS[candidate.label] ||
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
