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
  EyeOff,
  Download,
} from "lucide-react";
import { commonTranslations, redactPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import {
  buildAnalyzerArtifactUrl,
  normalizeAnalyzerArtifactUrl,
} from "@/lib/api_client";
import { FILE_SECURITY_POLICY, validateBrowserUpload } from "@/lib/secure_upload_policy";

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
  custom_redaction: "Typed text",
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
  if (lastDot === -1) return filename || "redacted-file";
  return filename.slice(0, lastDot) || "redacted-file";
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

function parseCustomRedactions(value = "") {
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

  const downloadUrl =
    normalizeAnalyzerArtifactUrl(
      pickFirstString([
        artifact?.download_url,
        artifact?.downloadUrl,
        result?.download_url,
        result?.downloadUrl,
      ]),
    ) || buildAnalyzerArtifactUrl(storageKey);

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

function extractPreviewUrl(responseData) {
  const preview = responseData?.preview_artifact || {};
  const storageKey = pickFirstString([
    preview?.storage_key,
    preview?.storageKey,
  ]);

  return (
    normalizeAnalyzerArtifactUrl(
      pickFirstString([preview?.download_url, preview?.downloadUrl]),
    ) || buildAnalyzerArtifactUrl(storageKey)
  );
}

function canInlinePreview(ext) {
  return [".pdf", ".jpg", ".jpeg", ".png"].includes(ext);
}

function withInlineDisposition(url) {
  if (!url) return "";

  try {
    const parsed = new URL(url, window.location.origin);
    parsed.searchParams.set("disposition", "inline");

    if (url.startsWith("http://") || url.startsWith("https://")) {
      return parsed.toString();
    }

    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    const separator = url.includes("?") ? "&" : "?";
    return `${url}${separator}disposition=inline`;
  }
}

function resolveProcessedPreviewUrl({
  inputExtension,
  downloadUrl,
  previewUrl,
}) {
  const rawPreviewUrl =
    inputExtension === ".docx"
      ? previewUrl
      : downloadUrl && canInlinePreview(inputExtension)
        ? downloadUrl
        : "";

  return withInlineDisposition(rawPreviewUrl);
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

function parseReviewExclusionsFromDeselected(
  reviewCandidates,
  approvedCandidateIds,
) {
  const deselectedQuotes = reviewCandidates
    .filter((candidate) => !approvedCandidateIds.has(candidateId(candidate)))
    .map((candidate) => (candidate?.quote || "").trim())
    .filter(Boolean);

  return [...new Set(deselectedQuotes)];
}

export default function RedactPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();

  const common = commonTranslations[language] || commonTranslations.en;
  const t = redactPageTranslations[language] || redactPageTranslations.en;

  const [selectedFile, setSelectedFile] = useState(null);
  const [documentType, setDocumentType] = useState("legal_document");
  const [targetData, setTargetData] = useState(
    SUPPORTED_BY_DOCUMENT_TYPE.legal_document,
  );
  const [customRedactionsText, setCustomRedactionsText] = useState("");
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
  const isProcessing = isReviewing || isFinalizing || stage === "done";

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
    if (isProcessing || !file) return;

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

  function toggleTarget(value) {
    if (isProcessing) return;
    setTargetData((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
    setError("");
    resetResultState();
  }

  function toggleCandidate(candidate) {
    if (isProcessing || stage !== "review") return;
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
    link.download = downloadInfo.filename || "redacted-file";
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
      setError(t.chooseFileToRedact);
      return;
    }

    if (!targetData.length) {
      setError(t.redactionFailed);
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

      const customRedactions = parseCustomRedactions(customRedactionsText);
      for (const item of customRedactions) {
        formData.append("custom_redactions", item);
      }

      const response = await fetch("/api/analyzer/redact/review", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          extractResponseMessage(responseData, t.redactionFailed),
        );
      }

      const resolvedDownload = extractDownloadInfo(
        responseData,
        `${getFileStem(selectedFile.name)}_redacted${inputExtension}`,
      );

      const previewUrl = extractPreviewUrl(responseData);
      const candidates = Array.isArray(responseData?.candidates)
        ? responseData.candidates
        : [];

      setReviewCandidates(candidates);
      setApprovedCandidateIds(buildDefaultApprovedCandidateIds(candidates));

      // Do not expose provisional file as a downloadable output.
      // It is only used for inline review preview.
      setDownloadInfo(null);

      setProcessedPreviewUrl(
        resolveProcessedPreviewUrl({
          inputExtension,
          downloadUrl: resolvedDownload.downloadUrl,
          previewUrl,
        }),
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
      setError(submitError?.message || t.redactionFailed);
    } finally {
      setIsReviewing(false);
    }
  }

  async function handleFinalize() {
    if (stage !== "review" || isFinalizing) return;
    if (!selectedFile) {
      setError(t.chooseFileToRedact);
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

      const customRedactions = parseCustomRedactions(customRedactionsText);
      for (const item of customRedactions) {
        formData.append("custom_redactions", item);
      }

      const exclusions = parseReviewExclusionsFromDeselected(
        reviewCandidates,
        approvedCandidateIds,
      );

      for (const item of exclusions) {
        formData.append("review_exclusions", item);
      }

      const response = await fetch("/api/analyzer/redact", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          extractResponseMessage(responseData, t.redactionFailed),
        );
      }

      const resolvedDownload = extractDownloadInfo(
        responseData,
        `${getFileStem(selectedFile.name)}_redacted${inputExtension}`,
      );

      const previewUrl = extractPreviewUrl(responseData);

      setDownloadInfo(resolvedDownload);
      setProcessedPreviewUrl(
        resolveProcessedPreviewUrl({
          inputExtension,
          downloadUrl: resolvedDownload.downloadUrl,
          previewUrl,
        }),
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
          parseReviewExclusionsFromDeselected(
            reviewCandidates,
            approvedCandidateIds,
          ).length
        }`,
        `${t.processedFile}: ${
          resolvedDownload.filename ||
          `${getFileStem(selectedFile.name)}_redacted${inputExtension}`
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
      setError(submitError?.message || t.redactionFailed);
    } finally {
      setIsFinalizing(false);
    }
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto flex h-screen max-w-7xl flex-col px-4 py-4 md:px-5 md:py-5">
          <div className="mb-3 flex shrink-0 flex-wrap items-center justify-between gap-3">
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

          <section className="mb-4 shrink-0 max-w-3xl">
            <h1 className="text-2xl font-semibold tracking-tight text-[var(--app-text)] sm:text-3xl">
              {t.title}
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-5 app-text-muted">
              {t.description}
            </p>
          </section>

          <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[0.95fr_1.05fr]">
            <form
              onSubmit={handleProcessAndReview}
              className="relative min-h-0 overflow-hidden rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3 backdrop-blur-xl md:p-4"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative h-full overflow-y-auto pr-1">
                <div
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  className={`rounded-3xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center transition ${isProcessing ? "opacity-60" : "hover:border-[var(--app-border-strong)] hover:bg-[var(--app-surface-strong)]"}`}
                >
                  <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <Upload className="h-6 w-6 text-cyan-300" />
                  </div>

                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.uploadTitle}
                  </h2>
                  <p className="mt-1 text-sm leading-5 app-text-muted">
                    {t.allowedFileInputs}
                  </p>

                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    onChange={handleFileChange}
                    className="hidden"
                  />

                  <button
                    type="button"
                    disabled={isProcessing}
                    onClick={() =>
                      !isProcessing && fileInputRef.current?.click()
                    }
                    className={`mt-3 rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${isProcessing ? "cursor-not-allowed bg-[var(--app-surface-strong)] app-text-soft" : "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"}`}
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
                        <p className="mt-1 text-sm text-emerald-100/80">
                          {selectedFile.name} • {formatBytes(selectedFile.size)}
                        </p>
                        <p className="mt-1 text-sm text-emerald-100/80">
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
                      disabled={isProcessing}
                      onChange={(e) => {
                        setDocumentType(e.target.value);
                        setError("");
                        resetResultState();
                      }}
                      className={`mt-2 w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none focus:border-[var(--app-accent-border)] ${isProcessing ? "cursor-not-allowed opacity-60" : ""}`}
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
                      value={customRedactionsText}
                      disabled={isProcessing}
                      onChange={(e) => {
                        setCustomRedactionsText(e.target.value);
                        setError("");
                        resetResultState();
                      }}
                      placeholder={t.exclusionsPlaceholder}
                      rows={2}
                      className={`mt-2 w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] px-4 py-2.5 text-sm leading-5 text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] focus:border-[var(--app-accent-border)] ${isProcessing ? "cursor-not-allowed opacity-60" : ""}`}
                    />
                  </div>
                </div>

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-2.5">
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                    <p className="text-sm font-medium app-text-muted">
                      {t.sensitiveTargetsLabel}
                    </p>

                    <div className="flex gap-2">
                      <button
                        type="button"
                        disabled={isProcessing}
                        onClick={() => {
                          if (isProcessing) return;
                          setTargetData(supportedTargets);
                          setError("");
                          resetResultState();
                        }}
                        className={`rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1.5 text-xs app-text-muted transition ${isProcessing ? "cursor-not-allowed opacity-50" : "hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"}`}
                      >
                        {t.selectAll}
                      </button>

                      <button
                        type="button"
                        disabled={isProcessing}
                        onClick={() => {
                          if (isProcessing) return;
                          setTargetData([]);
                          setError("");
                          resetResultState();
                        }}
                        className={`rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1.5 text-xs app-text-muted transition ${isProcessing ? "cursor-not-allowed opacity-50" : "hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"}`}
                      >
                        {t.clearAll}
                      </button>
                    </div>
                  </div>

                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {supportedTargets.map((item) => {
                      const checked = targetData.includes(item);
                      return (
                        <label
                          key={item}
                          className={`flex items-center gap-3 rounded-2xl border px-3 py-1.5 text-sm transition ${isProcessing ? "cursor-not-allowed opacity-60" : "cursor-pointer"} ${
                            checked
                              ? "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-text)]"
                              : "border-[var(--app-border)] bg-[var(--app-surface)] app-text-muted hover:bg-[var(--app-surface-strong)]"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={isProcessing}
                            onChange={() => toggleTarget(item)}
                            className="h-4 w-4 rounded border-[var(--app-border)] bg-transparent"
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
                  <div className="mt-3 rounded-2xl border border-red-400/20 bg-red-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                )}

                <div className="mt-4 flex flex-wrap items-center gap-3">
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
                    className={`rounded-2xl px-5 py-3 text-sm font-semibold transition ${
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
                      className={`rounded-2xl px-5 py-3 text-sm font-semibold transition ${
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

            <div className="min-h-0 space-y-4 overflow-hidden">
              <div className="flex h-full min-h-0 flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3 backdrop-blur-xl md:p-4">
                <div className="mb-3 flex shrink-0 items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <EyeOff className="h-5 w-5 text-cyan-300" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-[var(--app-text)]">
                      {t.resultTitle}
                    </h2>
                  </div>
                </div>

                <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-3">
                  {stage === "done" && downloadInfo?.downloadUrl && (
                    <div className="mb-4 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                      <div className="flex items-start gap-3">
                        <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-300" />
                        <div className="flex-1">
                          <p className="font-medium text-emerald-100">
                            {stage === "done"
                              ? t.finalReady
                              : t.provisionalReady}
                          </p>
                          <p className="mt-1 text-sm text-emerald-100/80">
                            {downloadInfo.filename}
                          </p>
                        </div>

                        <button
                          type="button"
                          onClick={handleDownload}
                          className="inline-flex items-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
                        >
                          <Download className="h-4 w-4" />
                          {common.download}
                        </button>
                      </div>
                      <ProcessedOutputActions
                        artifactUrl={downloadInfo.downloadUrl}
                        filename={downloadInfo.filename}
                        title="Redacted output"
                      />
                    </div>
                  )}

                  {processedPreviewUrl &&
                    [".pdf", ".docx"].includes(inputExtension) && (
                      <div className="mb-4">
                        <p className="mb-2 text-sm font-medium app-text-muted">
                          {t.processedPreviewTitle}
                        </p>
                        <div className="overflow-hidden rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)]">
                          <iframe
                            src={processedPreviewUrl}
                            title="Processed preview"
                            className="h-[300px] w-full"
                          />
                        </div>
                      </div>
                    )}

                  {processedPreviewUrl &&
                    [".jpg", ".jpeg", ".png"].includes(inputExtension) && (
                      <div className="mb-4">
                        <p className="mb-2 text-sm font-medium app-text-muted">
                          {t.processedPreviewTitle}
                        </p>
                        <div className="overflow-hidden rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-2">
                          <img
                            src={processedPreviewUrl}
                            alt="Processed preview"
                            className="max-h-[300px] w-full rounded-xl object-contain"
                          />
                        </div>
                      </div>
                    )}

                  {!processedPreviewUrl &&
                    inputExtension === ".docx" &&
                    stage !== "edit" && (
                      <div className="mb-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-4 text-sm text-amber-100">
                        {t.docxPreviewNotice}
                      </div>
                    )}

                  {stage === "review" && (
                    <div className="mb-4 space-y-4">
                      <div className="rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-4">
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
                          disabled={isProcessing}
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
                          disabled={isProcessing}
                          onClick={() => setApprovedCandidateIds(new Set())}
                          className="rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-2 text-xs app-text-muted transition disabled:cursor-not-allowed disabled:opacity-50 hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                        >
                          {t.clearApproved}
                        </button>
                      </div>

                      <div className="grid gap-3 sm:grid-cols-2">
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

                      <div className="max-h-[220px] space-y-2 overflow-y-auto pr-1">
                        {reviewCandidates.length ? (
                          reviewCandidates.map((candidate) => {
                            const checked = approvedCandidateIds.has(
                              candidateId(candidate),
                            );
                            return (
                              <label
                                key={candidateId(candidate)}
                                className={`block rounded-2xl border p-3 transition ${
                                  checked
                                    ? "border-[var(--app-accent-border)] bg-[var(--app-accent-bg)]"
                                    : "border-[var(--app-border)] bg-[var(--app-surface)]"
                                }`}
                              >
                                <div className="flex items-start gap-3">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={isProcessing}
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

                                    <p className="mt-1 break-words text-sm leading-5 app-text-muted">
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
                    <pre className="whitespace-pre-wrap break-words text-sm leading-6 app-text-muted">
                      {resultSummary}
                    </pre>
                  ) : (
                    <p className="text-sm leading-6 app-text-soft">
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
