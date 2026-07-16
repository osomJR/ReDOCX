"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileArchive,
  Loader2,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  getAccessToken,
  postAnalyzerFeature,
  postAnalyzerBatchFeature,
} from "@/lib/api_client";
import { compressPdfPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import {
  FILE_SECURITY_POLICY,
  validateBrowserUpload,
  validateBrowserBatchUploads,
  getBatchUploadLimit,
} from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/compress";
const MAX_PDF_SIZE_MB = 50;
const JOB_POLL_INTERVAL_MS = 1_500;
const JOB_PROCESSING_TIMEOUT_MS = 30 * 60 * 1000;
const copy = compressPdfPageTranslations;

const COMPRESSION_LEVEL_HELP = {
  small_file: "Maximum compression · approximately 96 DPI for images",
  balanced: "Balanced quality and size · approximately 150 DPI for images",
  high_quality: "Higher visual quality · approximately 300 DPI for images",
};

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function responseErrorMessage(data, fallback) {
  return (
    data?.detail?.message ||
    data?.detail?.error ||
    data?.message ||
    data?.error ||
    fallback
  );
}

async function getCompressionJob(jobId) {
  const url = `/api/analyzer/pdf/compress/jobs/${encodeURIComponent(jobId)}`;
  async function requestJob(forceRefresh = false) {
    const token = await getAccessToken({ forceRefresh });
    return fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
    });
  }

  let response = await requestJob();
  if (response.status === 401) {
    response = await requestJob(true);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      responseErrorMessage(data, "Could not read compression job status."),
    );
  }
  return data;
}

async function waitForCompressionJob(jobId, onStatus) {
  let processingStartedAt = null;
  let processingElapsedMs = 0;

  while (true) {
    const job = await getCompressionJob(jobId);
    onStatus?.(job);

    if (job.status === "processing") {
      processingStartedAt ??= Date.now();
      if (
        processingElapsedMs + (Date.now() - processingStartedAt) >=
        JOB_PROCESSING_TIMEOUT_MS
      ) {
        throw new Error(
          "Compression has been actively processing for 30 minutes. Keep the job ID and try the status request again.",
        );
      }
    } else if (processingStartedAt !== null) {
      processingElapsedMs += Date.now() - processingStartedAt;
      processingStartedAt = null;
    }

    if (job.status === "completed") {
      if (!job.result) {
        throw new Error("Compression completed without a downloadable result.");
      }
      return job;
    }
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.message || "PDF compression failed.");
    }

    await delay(JOB_POLL_INTERVAL_MS);
  }
}

async function resolveBatchCompressionJobs(batchData, onProgress) {
  const items = Array.isArray(batchData?.items) ? batchData.items : [];
  const queuedItems = items.filter(
    (item) => item?.success && item?.response?.result?.job_id,
  );
  if (!queuedItems.length) return batchData;

  let completed = 0;
  const resolvedItems = await Promise.all(
    items.map(async (item) => {
      const jobId = item?.response?.result?.job_id;
      if (!item?.success || !jobId) return item;

      try {
        const job = await waitForCompressionJob(jobId, (status) => {
          onProgress?.(
            `${completed}/${queuedItems.length} complete · ${status.message || status.status}`,
          );
        });
        completed += 1;
        onProgress?.(
          `${completed}/${queuedItems.length} compression jobs complete`,
        );
        return {
          ...item,
          response: { ...item.response, result: job.result },
        };
      } catch (caught) {
        completed += 1;
        return {
          ...item,
          success: false,
          error: {
            error: "compression_job_failed",
            message: caught?.message || "PDF compression failed.",
          },
        };
      }
    }),
  );

  const succeeded = resolvedItems.filter((item) => item.success).length;
  const failed = resolvedItems.length - succeeded;
  return {
    ...batchData,
    success: failed === 0,
    batch: {
      ...(batchData?.batch || {}),
      succeeded,
      failed,
      processing_mode: "background queue",
    },
    items: resolvedItems,
  };
}
function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}
function isPdf(file) {
  const type = String(file?.type || "").toLowerCase();
  const name = String(file?.name || "").toLowerCase();
  return (
    type === "application/pdf" ||
    type === "application/x-pdf" ||
    name.endsWith(".pdf")
  );
}
function fileSizeMb(file) {
  return file.size / (1024 * 1024);
}
function normalizePdfFilename(value, fallback) {
  const raw = String(value || "").trim() || fallback;
  return raw.toLowerCase().endsWith(".pdf") ? raw : `${raw}.pdf`;
}
function normalizeArtifactUrl(url) {
  if (!url) return "";
  const raw = String(url);
  if (/^https?:\/\//i.test(raw)) return raw;
  return raw.replace(
    /^\/api\/v1\/analyzer\/artifacts\//,
    "/api/analyzer/artifacts/",
  );
}
function AuthRequired({ t }) {
  return (
    <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6">
      <h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2>
      <p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p>
      <a
        href="/auth/login?returnTo=/pdf-tools/compress"
        className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]"
      >
        {t.signIn}
      </a>
    </section>
  );
}
export default function CompressPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const account = useAccount();
  const { user, authChecked } = account;
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const [file, setFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [compressionLevel, setCompressionLevel] = useState("balanced");
  const [outputFilename, setOutputFilename] = useState(
    "compressed-document.pdf",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);
  const [batchResult, setBatchResult] = useState(null);
  const [jobStatus, setJobStatus] = useState("");
  const [activeJobId, setActiveJobId] = useState("");
  async function handlePickedPdfFile(file) {
    if (busy) return;
    if (!file) {
      setFile(null);
      setSelectedFiles([]);
      return;
    }

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.pdfTool,
    );
    if (securityError) {
      setFile(null);
      setSelectedFiles([]);
      setError(securityError);
      return;
    }

    setError("");
    setSelectedFiles([file]);
    setBatchResult(null);
    setResponse(null);
    setJobStatus("");
    setActiveJobId("");
    setFile(file);
  }

  async function handlePickedPdfFiles(fileList) {
    if (busy) return;
    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;
    const files = [...selectedFiles, ...incomingFiles];

    if (files.length === 1) {
      await handlePickedPdfFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(
      files,
      FILE_SECURITY_POLICY.pdfTool,
      {
        account: batchAccount,
        featureLabel: "PDF compression",
      },
    );

    if (batchValidation.message) {
      setError(batchValidation.message);
      return;
    }

    setError("");
    setResponse(null);
    setBatchResult(null);
    setJobStatus("");
    setActiveJobId("");
    setFile(files[0]);
    setSelectedFiles(files);
  }

  function handleRemoveFile(_file, index) {
    const nextFiles = selectedFiles.filter(
      (_, fileIndex) => fileIndex !== index,
    );
    setSelectedFiles(nextFiles);
    setFile(nextFiles[0] || null);
    setError("");
    setResponse(null);
    setBatchResult(null);
    setJobStatus("");
    setActiveJobId("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setResponse(null);
    setBatchResult(null);
    setJobStatus("");
    setActiveJobId("");
    if (!file) return setError(t.noFile);
    if (!isPdf(file)) return setError(t.invalidFile);
    if (fileSizeMb(file) > MAX_PDF_SIZE_MB) return setError(t.tooLarge);
    if (selectedFiles.length > 1) {
      const formData = new FormData();
      selectedFiles.forEach((item) => formData.append("files", item));
      formData.append("compression_level", compressionLevel);
      formData.append(
        "output_filename",
        normalizePdfFilename(outputFilename, "compressed-document.pdf"),
      );
      formData.append("async_processing", "true");
      formData.append("system_language", systemLanguageFor(language));
      setBusy(true);
      try {
        const data = await postAnalyzerBatchFeature("pdf/compress", formData);
        setBatchResult(await resolveBatchCompressionJobs(data, setJobStatus));
        setResponse(null);
      } catch (caught) {
        setError(caught?.message || t.failed);
      } finally {
        setBusy(false);
      }
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("compression_level", compressionLevel);
    formData.append(
      "output_filename",
      normalizePdfFilename(outputFilename, "compressed-document.pdf"),
    );
    formData.append("async_processing", "true");
    formData.append("system_language", systemLanguageFor(language));
    setBusy(true);
    try {
      const data = await postAnalyzerFeature(FEATURE_PATH, formData, true);
      const queuedJob = data?.result;
      if (queuedJob?.job_id) {
        setActiveJobId(queuedJob.job_id);
        setJobStatus(queuedJob.message || "Compression job queued.");
        const completedJob = await waitForCompressionJob(
          queuedJob.job_id,
          (job) => setJobStatus(job.message || job.status),
        );
        setResponse({ ...data, result: completedJob.result });
        setJobStatus(completedJob.message || "Compression completed.");
      } else {
        setResponse(data);
      }
    } catch (caught) {
      setError(caught?.message || t.failed);
    } finally {
      setBusy(false);
    }
  }
  const result = response?.result || null;
  const downloadUrl = normalizeArtifactUrl(
    result?.download_url ||
      result?.pdf_artifact?.download_url ||
      (result?.storage_key
        ? `/api/analyzer/artifacts/${String(result.storage_key).replace(/^\/+/, "")}`
        : ""),
  );
  if (!authChecked)
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
      </AppSidebarLayout>
    );
  if (!user)
    return (
      <AppSidebarLayout>
        <main className="app-page min-h-screen p-6">
          <AuthRequired t={t} />
        </main>
      </AppSidebarLayout>
    );
  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
        <button
          type="button"
          onClick={() => router.back()}
          className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"
        >
          <ArrowLeft className="h-4 w-4" />
          {t.back}
        </button>
        <section className="mb-8 rounded-3xl border app-surface-strong p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
            {t.badge}
          </p>
          <h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">
            {t.title}
          </h1>
          <p className="mt-3 max-w-3xl app-text-muted">{t.description}</p>
        </section>
        <form
          onSubmit={handleSubmit}
          className="grid gap-6 lg:grid-cols-[1fr_0.85fr]"
        >
          <section className="rounded-3xl border app-surface-strong p-5">
            <h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2>
            <p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>
            <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center">
              <UploadCloud className="h-10 w-10 app-text-muted" />
              <span className="mt-3 text-sm font-semibold app-text">
                {t.chooseFile}
              </span>
              <input
                type="file"
                multiple
                accept="application/pdf,.pdf"
                disabled={busy}
                className="hidden"
                onChange={(event) => {
                  handlePickedPdfFiles(event.target.files);
                  event.target.value = "";
                }}
              />
            </label>
            <SelectedFilesSummary
              files={selectedFiles}
              limit={batchLimit}
              language={language}
              className="mt-4"
              onRemoveFile={handleRemoveFile}
              disabled={busy}
            />
          </section>
          <section className="space-y-6">
            <div className="rounded-3xl border app-surface-strong p-5">
              <label className="block text-sm font-medium app-text">
                {t.compressionLevel}
                <select
                  value={compressionLevel}
                  disabled={busy}
                  onChange={(event) => setCompressionLevel(event.target.value)}
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                >
                  <option value="small_file">{t.smallFile}</option>
                  <option value="balanced">{t.balanced}</option>
                  <option value="high_quality">{t.highQuality}</option>
                </select>
              </label>
              <p className="mt-2 text-xs app-text-muted">
                {COMPRESSION_LEVEL_HELP[compressionLevel]}
              </p>
              <input
                value={outputFilename}
                disabled={busy}
                onChange={(event) => setOutputFilename(event.target.value)}
                placeholder={t.outputFilename}
                className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text"
              />
            </div>
            {jobStatus ? (
              <div
                className="rounded-2xl border border-cyan-400/30 bg-cyan-400/10 p-4 text-sm text-cyan-100"
                role="status"
                aria-live="polite"
              >
                <p>{jobStatus}</p>
                {activeJobId ? (
                  <p className="mt-1 break-all text-xs text-cyan-100/70">
                    Job ID: {activeJobId}
                  </p>
                ) : null}
              </div>
            ) : null}
            {error ? (
              <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200">
                <AlertTriangle className="mr-2 inline h-4 w-4" />
                {error}
              </p>
            ) : null}
            <button
              type="submit"
              disabled={busy}
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <FileArchive className="h-4 w-4" />
              )}
              {busy ? t.compressing : t.compress}
            </button>
            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                {downloadUrl ? (
                  <a
                    href={downloadUrl}
                    className="mt-4 inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                  >
                    <Download className="h-4 w-4" />
                    {t.download}
                  </a>
                ) : null}
              </section>
            ) : null}
          </section>
          <BatchResultPanel
            result={batchResult}
            title="Batch PDF compression results"
          />
        </form>
      </main>
    </AppSidebarLayout>
  );
}