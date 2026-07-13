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
  ShieldCheck,
  Download,
  FileText,
  FileJson,
  ClipboardCheck,
  PackageCheck,
  X,
} from "lucide-react";
import {
  commonTranslations,
  compliancePageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import {
  FILE_SECURITY_POLICY,
  getDuplicateBrowserUploadMessage,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 10;
const MAX_COMPLIANCE_FILES = 10;
const COMPLIANCE_PREVIEW_ENDPOINT = "/api/analyzer/compliance/preview";
const COMPLIANCE_SINGLE_ENDPOINT = "/api/analyzer/compliance";
const COMPLIANCE_SET_ENDPOINT = "/api/analyzer/compliance/set";

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
  return `${stem}.compliance.${ext}`;
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

export default function CompliancePage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();

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

  const availableSectorPacks = useMemo(
    () => [
      selectedCountryConfig.corePack,
      ...selectedCountryConfig.sectorPacks,
    ],
    [selectedCountryConfig],
  );
  const sourceOutputMode = useMemo(
    () => getSourceOutputMode(selectedFiles),
    [selectedFiles],
  );
  const outputExtension = useMemo(
    () => getReportOutputExtension(reportVariant, sourceOutputMode),
    [reportVariant, sourceOutputMode],
  );
  const isDocumentSet = selectedFiles.length > 1;
  const isProcessing = isPreviewing || isSubmitting;

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
    selectedFiles.length > 0 &&
    selectedFiles.length <= MAX_COMPLIANCE_FILES &&
    sectorPacks.includes(selectedCountryConfig.corePack) &&
    REPORT_VARIANTS.includes(reportVariant);

  const canGenerate = canPreview && Boolean(previewMarkdown);

  function resetResultState() {
    setResultSummary("");
    setDownloadInfo(null);
    setCounts(null);
    setPreviewMarkdown("");
  }

  async function handlePickedFiles(fileList) {
    if (isProcessing) return;
    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const files = [...selectedFiles, ...incomingFiles];

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

    const duplicateMessage = await getDuplicateBrowserUploadMessage(files);
    if (duplicateMessage) {
      setError(duplicateMessage);
      return;
    }

    setError("");
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
    if (info.downloadUrl)
      return info.downloadUrl.replace(
        /^\/api\/v1\/analyzer\/artifacts\//,
        "/api/analyzer/artifacts/",
      );
    if (info.storageKey) return `/api/analyzer/artifacts/${info.storageKey}`;
    return "";
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

  async function buildComplianceFormData() {
    const formData = new FormData();
    const fileFieldName = selectedFiles.length > 1 ? "files" : "file";

    for (const selectedFile of selectedFiles) {
      const buffer = await selectedFile.arrayBuffer();
      const fileBlob = new Blob([buffer], {
        type: selectedFile.type || "application/octet-stream",
      });
      formData.append(fileFieldName, fileBlob, selectedFile.name);
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

    try {
      const response = await fetch(COMPLIANCE_PREVIEW_ENDPOINT, {
        method: "POST",
        body: await buildComplianceFormData(),
        credentials: "include",
      });
      const responseData = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new Error(
          extractResponseMessage(responseData, t.complianceFailed),
        );

      const previewText =
        responseData?.preview_markdown || responseData?.previewMarkdown || "";
      setPreviewMarkdown(previewText);
      setCounts(extractComplianceCounts(responseData));

      const inputLines = selectedFiles
        .map((file, index) => `${index + 1}. ${file.name}`)
        .join("\n");
      setResultSummary(
        previewText ||
          [
            t.previewCompleted,
            "",
            `${t.inputFiles}:`,
            inputLines,
            `${t.jurisdictionResult}: ${selectedCountryLabel}`,
            `${t.sectorPacksResult}: ${selectedSectorLabels}`,
            "",
            t.humanReviewRequired,
          ].join("\n"),
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
      const endpoint = isDocumentSet
        ? COMPLIANCE_SET_ENDPOINT
        : COMPLIANCE_SINGLE_ENDPOINT;
      const response = await fetch(endpoint, {
        method: "POST",
        body: await buildComplianceFormData(),
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
      const resolvedCounts = extractComplianceCounts(responseData) || counts;
      setDownloadInfo(resolvedDownload);
      setCounts(resolvedCounts);

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
              onSubmit={handlePreview}
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
                      {Object.entries(COUNTRY_CONFIG).map(([value, config]) => (
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
                    items={REGULATORY_DOMAINS}
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

                {error ? (
                  <div className="mt-3 rounded-2xl border border-red-400/20 bg-red-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                ) : null}

                <div className="mt-auto pt-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="submit"
                      disabled={!canPreview}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${canPreview ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl" : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"}`}
                    >
                      {isPreviewing ? t.previewing : t.previewAction}
                    </button>
                    <button
                      type="button"
                      disabled={!canGenerate}
                      onClick={handleSubmit}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${canGenerate ? "border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] text-[var(--app-accent-text)] hover:bg-[var(--app-accent-bg)]" : "cursor-not-allowed border border-[var(--app-border)] bg-[var(--app-surface)] app-text-soft"}`}
                    >
                      {isSubmitting ? t.checking : t.generateFileAction}
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