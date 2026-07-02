"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import {
  ArrowLeft,
  Upload,
  Sparkles,
  XCircle,
  CheckCircle2,
  FileText,
  AlignLeft,
  ShieldCheck,
  PenTool,
} from "lucide-react";
import { commonTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel from "@/components/batch_result_panel";
import { getAnalyzerResultDownloadUrl, postAnalyzerFeature, postAnalyzerBatchFeature } from "@/lib/api_client";
import { FILE_SECURITY_POLICY, validateBrowserUpload, validateBrowserBatchUploads, getBatchUploadLimit } from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];
const REJECTED_EXTENSIONS = [".png", ".jpg", ".jpeg"];
const MAX_FILE_SIZE_MB = 10;
const INLINE_TEXT_EXTENSION = ".txt";

function getFileExtension(filename = "") {
  const lastDot = filename.lastIndexOf(".");
  if (lastDot === -1) return "";
  return filename.slice(lastDot).toLowerCase();
}

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
async function getDuplicateBatchFileMessage(files = []) {
  const fileList = Array.from(files || []).filter(Boolean);
  if (fileList.length < 2 || !globalThis.crypto?.subtle) return "";

  const filesBySize = new Map();
  for (const file of fileList) {
    const sizeKey = Number.isFinite(file?.size) ? file.size : "unknown";
    const bucket = filesBySize.get(sizeKey) || [];
    bucket.push(file);
    filesBySize.set(sizeKey, bucket);
  }

  for (const bucket of filesBySize.values()) {
    if (bucket.length < 2) continue;

    const seen = new Map();
    for (const file of bucket) {
      const buffer = await file.arrayBuffer();
      const digest = await crypto.subtle.digest("SHA-256", buffer);
      const hash = Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");

      const original = seen.get(hash);
      if (original) {
        return `Duplicate file rejected: "${file.name}" has the same content as "${original.name}". Remove one copy before starting the batch.`;
      }

      seen.set(hash, file);
    }
  }

  return "";
}


function replaceVars(template, vars = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
}

const t = {
  badge: "Grammar correction",
  title: "Correct grammar while preserving the original meaning",
  description:
    "Upload a PDF or Word document, or paste inline text. The correction keeps the original tone, structure, and intent while fixing grammar and syntax.",
  fileMode: "Upload file",
  textMode: "Inline text",
  uploadTitle: "Upload content to correct",
  allowedFileInputs:
    "Allowed: .pdf and .docx. Rejected automatically: .png, .jpg, .jpeg, and unsupported formats.",
  outputExtensionWillBe: "Output extension will be",
  pasteTextLabel: "Paste text to correct",
  pasteTextPlaceholder: "Paste or type the text you want to correct...",
  inlineTextTreatedAs:
    "Inline text is treated as .txt, so the output extension will also be .txt.",
  unsupportedFileType:
    "Unsupported file type: {ext}. Only .pdf and .docx uploads are allowed. PNG, JPG, JPEG and other image formats are rejected.",
  fileTooLarge: "File is too large. Maximum allowed size is {maxSize} MB.",
  correctionFailed: "Something went wrong while correcting the grammar.",
  correctingGrammar: "Correcting grammar...",
  grammarCorrect: "Correct grammar",
  formatPolicy: "Format policy",
  policySubtitle: "Grammar fixes without rewriting intent",
  allowedUploadsLabel: "Allowed uploads:",
  inlineInputLabel: "Inline input:",
  rejectedAutomaticallyLabel: "Rejected automatically:",
  outputRuleLabel: "Output rule:",
  inlineInputValue: "treated as .txt",
  rejectedAutomaticallyValue: ".png, .jpg, .jpeg, and unsupported file types",
  outputRuleValue: "output extension mirrors the input extension",
  correctionOutputTitle: "Corrected output",
  previewEmpty:
    "Your corrected text or downloadable file details will appear here after processing.",
  outputExtensionLabel: "Output extension:",
  downloadCorrectedFile: "Download corrected file",
};

export default function GrammarPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);
  const common = commonTranslations[language] || commonTranslations.en;

  const [mode, setMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [inlineText, setInlineText] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [correctionResult, setCorrectionResult] = useState("");
  const [downloadInfo, setDownloadInfo] = useState(null);

  const inputExtension = useMemo(() => {
    if (mode === "text") return INLINE_TEXT_EXTENSION;
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [mode, selectedFile]);

  const outputExtension =
    inputExtension || (mode === "text" ? INLINE_TEXT_EXTENSION : "");

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;
    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;
    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const canSubmit =
    !isSubmitting &&
    ((mode === "file" && selectedFile && isValidFile) ||
      (mode === "text" && inlineText.trim().length > 0));

  function resetResultState() {
    setCorrectionResult("");
    setDownloadInfo(null);
    setBatchResult(null);
  }

  function rejectFile(message) {
    setSelectedFile(null);
    setSelectedFiles([]);
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.aiTextDocument);
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
    setSelectedFiles([]);
    setSelectedFile(file);
    resetResultState();
  }

  async function handlePickedFiles(fileList) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;

    if (files.length === 1) {
      await handlePickedFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(files, FILE_SECURITY_POLICY.aiTextDocument, {
      account: batchAccount,
      featureLabel: "grammar correction",
    });

    if (batchValidation.message) {
      rejectFile(batchValidation.message);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }


    const duplicateMessage = await getDuplicateBatchFileMessage(files);

    if (duplicateMessage) {

      rejectFile(duplicateMessage);

      if (fileInputRef.current) fileInputRef.current.value = "";

      return;

    }

    setError("");
    setSelectedFile(files[0]);
    setSelectedFiles(files);
    resetResultState();
  }

  function handleFileChange(event) {
    handlePickedFiles(event.target.files);
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

  async function handleSubmit(event) {
    event.preventDefault();

    if (mode === "file" && !selectedFile) {
      setError("Please choose a file.");
      return;
    }

    if (mode === "text" && !inlineText.trim()) {
      setError("Please enter text to correct.");
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {

      if (mode === "file" && selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append(
          "system_language",
          language === "fr" ? "french" : "english",
        );

        const data = await postAnalyzerBatchFeature("grammar-correct", formData);
        setBatchResult(data);
        return;
      }

      const formData = new FormData();

      if (mode === "file") {
        formData.append("file", selectedFile);
      } else {
        formData.append("text", inlineText.trim());
      }

      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      const data = await postAnalyzerFeature("grammar-correct", formData, false);
      const result = data?.result;

      if (!result) {
        throw new Error("Backend returned no result.");
      }

      if (typeof result.content === "string") {
        setCorrectionResult(result.content);
        return;
      }

      const downloadUrl = getAnalyzerResultDownloadUrl(result);

      if (!downloadUrl && !result.filename) {
        throw new Error("Unexpected response shape from backend.");
      }

      setDownloadInfo({
        filename: result.filename || "corrected-output",
        outputFormat: result.output_format || outputExtension,
        fileSizeMb: result.file_size_mb,
        url: downloadUrl,
      });
    } catch (err) {
      setError(err.message || t.correctionFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto max-w-5xl px-6 py-12 md:px-8 md:py-16">
          <button
            type="button"
            onClick={() => router.push("/")}
            className="mb-8 inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
          >
            <ArrowLeft className="h-4 w-4" />
            {common.back}
          </button>

          <section className="mb-10">
            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-4 py-2 text-sm text-[var(--app-accent-text)] backdrop-blur">
              <Sparkles className="h-4 w-4" />
              {t.badge}
            </div>

            <div className="mt-6 max-w-3xl">
              <h1 className="text-4xl font-semibold tracking-tight text-[var(--app-text)] sm:text-5xl">
                {t.title}
              </h1>
              <p className="mt-4 max-w-2xl text-base leading-7 app-text-muted md:text-lg">
                {t.description}
              </p>
            </div>
          </section>

          <section className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
            <form
              onSubmit={handleSubmit}
              className="relative overflow-hidden rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-6 backdrop-blur-xl md:p-8"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative">
                <div className="mb-6 inline-flex rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-1">
                  <button
                    type="button"
                    onClick={() => {
                      setMode("file");
                      setError("");
                      resetResultState();
                    }}
                    className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
                      mode === "file"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] shadow-sm"
                        : "app-text-muted hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                    }`}
                  >
                    {t.fileMode}
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setMode("text");
                      setSelectedFile(null);
                      setError("");
                      resetResultState();
                    }}
                    className={`rounded-xl px-4 py-2 text-sm font-medium transition ${
                      mode === "text"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] shadow-sm"
                        : "app-text-muted hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                    }`}
                  >
                    {t.textMode}
                  </button>
                </div>

                {mode === "file" ? (
                  <>
                    <div
                      onDrop={handleDrop}
                      onDragOver={handleDragOver}
                      className="rounded-3xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-8 text-center transition hover:border-[var(--app-border-strong)] hover:bg-[var(--app-surface-strong)]"
                    >
                      <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                        <Upload className="h-7 w-7 text-cyan-300" />
                      </div>

                      <h2 className="text-lg font-semibold text-[var(--app-text)]">
                        {t.uploadTitle}
                      </h2>
                      <p className="mt-2 text-sm leading-6 app-text-muted">
                        {t.allowedFileInputs}
                      </p>
                      <p className="mt-2 text-sm leading-6 app-text-soft">
                        {t.outputExtensionWillBe}{" "}
                        <span className="font-medium text-[var(--app-text)]">
                          {inputExtension || ".pdf / .docx"}
                        </span>
                      </p>

                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".pdf,.docx"
                        onChange={handleFileChange}
                        className="hidden"
                      />

                      <button
                        type="button"
                        onClick={() => fileInputRef.current?.click()}
                        className="mt-5 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                      >
                        {common.chooseFile}
                      </button>
                    </div>

                    {selectedFile && isValidFile && (
                      <div className="mt-5 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-4">
                        <div className="flex items-start gap-3">
                          <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-300" />
                          <div>
                            <p className="font-medium text-emerald-100">
                              {common.fileAccepted}
                            </p>
                            <p className="mt-1 text-sm text-emerald-100/80">
                              {selectedFile.name} •{" "}
                              {formatBytes(selectedFile.size)}
                            </p>
                          </div>
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface)] p-5">
                    <label className="block">
                      <span className="mb-3 block text-sm font-medium app-text-muted">
                        {t.pasteTextLabel}
                      </span>
                      <textarea
                        value={inlineText}
                        onChange={(e) => {
                          setInlineText(e.target.value);
                          setError("");
                          resetResultState();
                        }}
                        placeholder={t.pasteTextPlaceholder}
                        rows={10}
                        className="w-full rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] px-4 py-3 text-sm leading-6 text-[var(--app-text)] outline-none transition placeholder:text-[var(--app-text-soft)] focus:border-[var(--app-accent-border)]"
                      />
                    </label>

                    <p className="mt-3 text-sm leading-6 app-text-soft">
                      {t.inlineTextTreatedAs}
                    </p>
                  </div>
                )}

                {error && (
                  <div className="mt-5 rounded-2xl border border-red-400/20 bg-red-400/10 p-4">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                )}

                <div className="mt-6 flex flex-wrap items-center gap-3">
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className={`rounded-2xl px-5 py-3 text-sm font-semibold transition ${
                      canSubmit
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                        : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                    }`}
                  >
                    {isSubmitting ? t.correctingGrammar : t.grammarCorrect}
                  </button>

                  <div className="text-sm app-text-soft">
                    {common.outputFormat}{" "}
                    <span className="font-medium app-text-muted">
                      {outputExtension || "—"}
                    </span>
                  </div>
                </div>
              </div>
            <BatchResultPanel result={batchResult} title="Batch grammar correction results" />
            </form>

            <aside className="space-y-6">
              <div className="rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-6 backdrop-blur-xl">
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <ShieldCheck className="h-5 w-5 text-cyan-300" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-[var(--app-text)]">
                      {t.formatPolicy}
                    </h2>
                    <p className="text-sm app-text-soft">{t.policySubtitle}</p>
                  </div>
                </div>

                <div className="space-y-3 text-sm leading-6 app-text-muted">
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <p className="font-semibold text-[var(--app-text)]">
                      {t.allowedUploadsLabel}
                    </p>
                    <p className="mt-1 app-text-muted">.pdf, .docx</p>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <p className="font-semibold text-[var(--app-text)]">
                      {t.inlineInputLabel}
                    </p>
                    <p className="mt-1 app-text-muted">{t.inlineInputValue}</p>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <p className="font-semibold text-[var(--app-text)]">
                      {t.rejectedAutomaticallyLabel}
                    </p>
                    <p className="mt-1 app-text-muted">
                      {t.rejectedAutomaticallyValue}
                    </p>
                  </div>

                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <p className="font-semibold text-[var(--app-text)]">
                      {t.outputRuleLabel}
                    </p>
                    <p className="mt-1 app-text-muted">{t.outputRuleValue}</p>
                  </div>
                </div>
              </div>

              <div className="rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-6 backdrop-blur-xl">
                <div className="mb-4 flex items-center gap-3">
                  <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <PenTool className="h-5 w-5 text-cyan-300" />
                  </div>
                  <div>
                    <h2 className="text-lg font-semibold text-[var(--app-text)]">
                      {t.correctionOutputTitle}
                    </h2>
                    <p className="text-sm app-text-soft">
                      {common.previewArea}
                    </p>
                  </div>
                </div>

                {correctionResult ? (
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4">
                    <pre className="whitespace-pre-wrap break-words text-sm leading-7 app-text-muted">
                      {correctionResult}
                    </pre>
                  </div>
                ) : downloadInfo ? (
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4">
                    <div className="space-y-2 text-sm app-text-muted">
                      <p>
                        <span className="font-medium text-[var(--app-text)]">File:</span>{" "}
                        {downloadInfo.filename}
                      </p>
                      <p>
                        <span className="font-medium text-[var(--app-text)]">Format:</span>{" "}
                        {downloadInfo.outputFormat}
                      </p>
                      <p>
                        <span className="font-medium text-[var(--app-text)]">Size:</span>{" "}
                        {downloadInfo.fileSizeMb ?? "unknown"} MB
                      </p>
                    </div>

                    {downloadInfo.url && (
                      <a
                        href={downloadInfo.url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                      >
                        {t.downloadCorrectedFile}
                      </a>
                    )}
                  </div>
                ) : (
                  <div className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4">
                    <p className="text-sm leading-6 app-text-soft">
                      {t.previewEmpty}
                    </p>
                  </div>
                )}

                <div className="mt-4 inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-muted">
                  <FileText className="h-4 w-4 text-cyan-300" />
                  {t.outputExtensionLabel}{" "}
                  <span className="font-medium text-[var(--app-text)]">
                    {downloadInfo?.outputFormat || outputExtension || "—"}
                  </span>
                </div>
              </div>

              <div className="rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-6 backdrop-blur-xl">
                <h2 className="text-lg font-semibold text-[var(--app-text)]">
                  {common.formatPolicy}
                </h2>
                <p className="mt-1 text-sm app-text-soft">{t.policySubtitle}</p>

                <div className="mt-4 space-y-3 text-sm leading-6 app-text-muted">
                  <div className="flex items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <FileText className="h-4 w-4 text-cyan-300" />
                    <span>.pdf / .docx</span>
                  </div>
                  <div className="flex items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <AlignLeft className="h-4 w-4 text-cyan-300" />
                    <span>{t.inlineInputValue}</span>
                  </div>
                  <div className="flex items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                    <XCircle className="h-4 w-4 text-cyan-300" />
                    <span>{REJECTED_EXTENSIONS.join(", ")}</span>
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
