"use client";

import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  FileType,
  Repeat,
  Download,
} from "lucide-react";
import {
  commonTranslations,
  convertPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import {
  postAnalyzerBatchFeature,
  postAnalyzerFeature,
} from "@/lib/api_client";
import BatchResultPanel from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import {
  FILE_SECURITY_POLICY,
  validateBrowserUpload,
  validateBrowserBatchUploads,
  getBatchUploadLimit,
} from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".jpg", ".jpeg", ".png"];
const MAX_FILE_SIZE_MB = 25;

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function getFileStem(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return filename || "converted-file";
  return filename.slice(0, lastDot) || "converted-file";
}

function getAllowedOutputExtensions(inputExtension) {
  switch (inputExtension) {
    case ".pdf":
      return [".docx"];
    case ".docx":
      return [".pdf"];
    case ".jpg":
    case ".jpeg":
      return [".pdf", ".docx"];
    case ".png":
      return [".jpg", ".jpeg"];
    default:
      return [];
  }
}

function getInputTypeLabel(ext, t) {
  if (ext === ".pdf") return t.pdfDocument;
  if (ext === ".docx") return t.wordDocument;
  if (ext === ".jpg" || ext === ".jpeg") return t.jpgImage;
  if (ext === ".png") return t.pngImage;
  return t.unknownFile;
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

function buildOutputFilename(filename = "", outputExtension = "") {
  const safeExtension = outputExtension
    ? outputExtension.startsWith(".")
      ? outputExtension
      : `.${outputExtension}`
    : "";

  return `${getFileStem(filename)}${safeExtension}`;
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

function extractArtifactMetadata(responseData) {
  const candidates = [
    responseData?.artifact,
    responseData?.output_artifact,
    responseData?.result?.artifact,
    responseData?.data?.artifact,
    responseData?.result,
    responseData?.data,
    responseData,
  ].filter(Boolean);

  let downloadUrl = "";
  let storageKey = "";
  let artifactName = "";
  let contentType = "";

  for (const candidate of candidates) {
    downloadUrl ||= pickFirstString([
      candidate?.download_url,
      candidate?.downloadUrl,
      candidate?.url,
      candidate?.href,
    ]);

    storageKey ||= pickFirstString([
      candidate?.storage_key,
      candidate?.storageKey,
      candidate?.key,
    ]);

    artifactName ||= pickFirstString([
      candidate?.original_artifact_name,
      candidate?.artifact_name,
      candidate?.artifactName,
      candidate?.filename,
      candidate?.name,
    ]);

    contentType ||= pickFirstString([
      candidate?.content_type,
      candidate?.contentType,
      candidate?.mime_type,
      candidate?.mimeType,
    ]);
  }

  return {
    downloadUrl,
    storageKey,
    artifactName,
    contentType,
  };
}

function extractResponseMessage(responseData, fallbackMessage = "") {
  return (
    pickFirstString([
      responseData?.detail?.message,
      responseData?.detail?.error,
      responseData?.message,
      responseData?.result?.message,
      responseData?.data?.message,
    ]) || fallbackMessage
  );
}

export default function ConvertPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);

  const common = commonTranslations[language] || commonTranslations.en;
  const t = convertPageTranslations[language] || convertPageTranslations.en;

  const downloadLabel = common.download || "Download output";
  const convertedFileLabel = t.convertedFile || "Converted file";
  const downloadReadyLabel = t.downloadReady || "Download ready";
  const outputReadyText =
    t.outputReadyText || "Your converted file is ready to download.";
  const missingDownloadUrlText =
    t.missingDownloadUrl ||
    "Conversion finished, but the backend did not return a download URL.";

  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [targetExtension, setTargetExtension] = useState("");
  const [conversionResult, setConversionResult] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);

  const inputExtension = useMemo(() => {
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [selectedFile]);

  const allowedOutputs = useMemo(() => {
    return getAllowedOutputExtensions(inputExtension);
  }, [inputExtension]);

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;

    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;

    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const isValidConversion =
    !!inputExtension &&
    !!targetExtension &&
    allowedOutputs.includes(targetExtension);

  const canSubmit =
    !isSubmitting && !!selectedFile && isValidFile && isValidConversion;

  function resetResultState() {
    setConversionResult("");
    setDownloadInfo(null);
    setBatchResult(null);
  }

  function rejectFile(message) {
    setSelectedFile(null);
    setSelectedFiles([]);
    setTargetExtension("");
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(
      file,
      FILE_SECURITY_POLICY.conversionDocument,
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

    const outputs = getAllowedOutputExtensions(ext);

    setError("");
    setSelectedFiles([file]);
    setSelectedFile(file);
    setTargetExtension(outputs[0] || "");
    resetResultState();
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
      FILE_SECURITY_POLICY.conversionDocument,
      {
        account: batchAccount,
        featureLabel: "conversion",
      },
    );

    if (batchValidation.message) {
      setError(batchValidation.message);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    const acceptedFiles = batchValidation.files;
    const ext = getFileExtension(acceptedFiles[0].name);
    const outputs = getAllowedOutputExtensions(ext);

    if (!outputs.length) {
      rejectFile(
        replaceVars(t.unsupportedFileType, {
          ext: ext || "unknown",
        }),
      );
      return;
    }

    setError(batchValidation.duplicateMessage || "");
    setSelectedFile(acceptedFiles[0]);
    setSelectedFiles(acceptedFiles);
    setTargetExtension(outputs[0] || "");
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

  function handleRemoveFile(_file, index) {
    const nextFiles = selectedFiles.filter(
      (_, fileIndex) => fileIndex !== index,
    );
    const nextFile = nextFiles[0] || null;
    const nextOutputs = getAllowedOutputExtensions(
      nextFile ? getFileExtension(nextFile.name) : "",
    );
    setSelectedFiles(nextFiles);
    setSelectedFile(nextFile);
    setTargetExtension(nextOutputs[0] || "");
    setError("");
    resetResultState();
  }

  function handleDownload() {
    if (!downloadInfo?.url) return;

    const link = document.createElement("a");
    link.href = downloadInfo.url;
    link.download = downloadInfo.filename || "converted-file";
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (!selectedFile) {
      setError(t.chooseFileToConvert);
      return;
    }

    if (!isValidConversion) {
      setError(t.invalidConversion);
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {
      if (selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append("output_format", targetExtension.replace(".", ""));
        formData.append(
          "system_language",
          language === "fr" ? "french" : "english",
        );

        const data = await postAnalyzerBatchFeature("convert", formData);
        setBatchResult(data);
        setConversionResult("Batch conversion completed.");
        setDownloadInfo(null);
        return;
      }

      const payload = {
        inputType: "file",
        filename: selectedFile.name,
        inputExtension,
        outputExtension: targetExtension,
      };

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("output_format", targetExtension.replace(".", ""));
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      const responseData = await postAnalyzerFeature("convert", formData);

      const artifact = extractArtifactMetadata(responseData);
      const resolvedArtifactName =
        downloadFilenameFromUrl(artifact.downloadUrl) ||
        artifact.artifactName ||
        buildOutputFilename(selectedFile.name, payload.outputExtension);
      const backendMessage = extractResponseMessage(responseData);

      if (artifact.downloadUrl) {
        setDownloadInfo({
          url: artifact.downloadUrl,
          filename: resolvedArtifactName,
          contentType: artifact.contentType,
          storageKey: artifact.storageKey,
        });
      }

      const previewLines = [
        t.conversionCompleted,
        "",
        `${t.inputFile}: ${selectedFile.name}`,
        `${t.inputExtension}: ${payload.inputExtension}`,
        `${t.outputExtension}: ${payload.outputExtension}`,
        `${convertedFileLabel}: ${resolvedArtifactName}`,
      ];

      if (artifact.downloadUrl) {
        previewLines.push("", outputReadyText);
      } else {
        previewLines.push("", missingDownloadUrlText);
      }

      if (backendMessage) {
        previewLines.push("", backendMessage);
      } else {
        previewLines.push("");
      }

      setConversionResult(previewLines.join("\n"));
    } catch (submitError) {
      setError(submitError?.message || t.conversionFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

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

                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept=".pdf,.docx,.jpg,.jpeg,.png"
                    onChange={handleFileChange}
                    className="hidden"
                  />

                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="mt-3 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                  >
                    {common.chooseFile}
                  </button>
                </div>

                {selectedFile && isValidFile && (
                  <SelectedFilesSummary
                    files={selectedFiles}
                    limit={batchLimit}
                    language={language}
                    className="mt-3"
                    onRemoveFile={handleRemoveFile}
                    disabled={isSubmitting}
                    renderDetails={(file) => (
                      <>
                        {t.detectedType}{" "}
                        {getInputTypeLabel(getFileExtension(file.name), t)}
                      </>
                    )}
                  />
                )}

                {selectedFile && isValidFile && (
                  <div className="mt-3 grid gap-3 sm:grid-cols-2">
                    <label className="block">
                      <span className="mb-2 block text-sm font-medium app-text-muted">
                        {t.from}
                      </span>
                      <div className="flex items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2.5 text-sm app-text-muted">
                        <FileType className="h-4 w-4 text-cyan-300" />
                        {inputExtension}
                      </div>
                    </label>

                    <label className="block">
                      <span className="mb-2 block text-sm font-medium app-text-muted">
                        {t.convertTo}
                      </span>
                      <select
                        value={targetExtension}
                        onChange={(e) => {
                          setTargetExtension(e.target.value);
                          setError("");
                          resetResultState();
                        }}
                        className="w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2.5 text-sm text-[var(--app-text)] outline-none transition focus:border-[var(--app-accent-border)] focus:bg-[var(--app-surface-strong)]"
                      >
                        {allowedOutputs.map((ext) => (
                          <option
                            key={ext}
                            value={ext}
                            className="bg-[var(--app-panel)] text-[var(--app-text)]"
                          >
                            {ext}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}

                {selectedFile && isValidFile && (
                  <div className="mt-3 rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] p-3">
                    <div className="flex items-start gap-3">
                      <Repeat className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" />
                      <div className="text-sm leading-6 text-[var(--app-accent-text)]">
                        {t.allowedOutputsFor}{" "}
                        <span className="font-semibold">{inputExtension}</span>:{" "}
                        {allowedOutputs.length > 0
                          ? allowedOutputs.join(", ")
                          : t.none}
                      </div>
                    </div>
                  </div>
                )}

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
                      {isSubmitting ? common.converting : common.convert}
                    </button>

                    {downloadInfo?.url && (
                      <button
                        type="button"
                        onClick={handleDownload}
                        className="inline-flex items-center gap-2 rounded-2xl border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-5 py-2.5 text-sm font-semibold text-[var(--app-accent-text)] transition hover:bg-[var(--app-accent-bg)]"
                      >
                        <Download className="h-4 w-4" />
                        {downloadLabel}
                      </button>
                    )}
                  </div>

                  <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-soft">
                    {t.conversionLabel}{" "}
                    <span className="font-medium app-text-muted">
                      {inputExtension || "—"} → {targetExtension || "—"}
                    </span>
                  </div>
                </div>
              </div>
            </form>

            <aside className="min-h-0">
              <div className="flex min-h-[270px] flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5 lg:max-h-[calc(100vh-11rem)]">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.conversionOutput || "Conversion result"}
                  </h2>
                  <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1 text-xs app-text-soft">
                    {inputExtension || "—"} → {targetExtension || "—"}
                  </span>
                </div>

                <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4 max-h-[420px] lg:max-h-[calc(100vh-16rem)]">
                  {batchResult ? (
                    <div>
                      <BatchResultPanel
                        result={batchResult}
                        title="Batch conversion results"
                        embedded
                      />
                      <ProcessedOutputActions
                        result={batchResult}
                        title="Batch conversion output"
                      />
                    </div>
                  ) : conversionResult ? (
                    <div className="flex h-full min-h-0 flex-col gap-3">
                      <pre className="whitespace-pre-wrap break-words pr-1 text-xs leading-6 app-text-muted md:text-sm">
                        {conversionResult}
                      </pre>

                      {downloadInfo?.url && (
                        <div className="shrink-0 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                          <div className="flex items-start gap-3">
                            <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                            <div className="min-w-0">
                              <p className="font-medium text-emerald-100">
                                {downloadReadyLabel}
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
                                {downloadLabel}
                              </button>
                              <ProcessedOutputActions
                                artifactUrl={downloadInfo.url}
                                filename={downloadInfo.filename}
                                title="Converted output"
                              />
                            </div>
                          </div>
                        </div>
                      )}
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