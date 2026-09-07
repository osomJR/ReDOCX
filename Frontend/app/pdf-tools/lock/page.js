"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  Share2,
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import {
  getAnalyzerResultDownloadUrl,
  postAnalyzerBatchFeature,
  postAnalyzerFeature,
} from "@/lib/api_client";
import {
  lockPdfPageTranslations,
  resolveErrorMessage,
} from "@/lib/translations";
import {
  FILE_SECURITY_POLICY,
  getBatchUploadLimit,
  validateBrowserBatchUploads,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/lock";
const BATCH_FEATURE_PATH = "pdf/lock";
const PASSWORD_MIN_CHARACTERS = 8;
const PASSWORD_MAX_CHARACTERS = 128;
const ENCRYPTION = "aes_256";

const DEFAULT_PERMISSIONS = Object.freeze({
  allow_printing: false,
  allow_copying: false,
  allow_modifying: false,
  allow_annotations: false,
  allow_form_filling: false,
  allow_accessibility: true,
});

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}

function getFileStem(filename = "") {
  const name = String(filename || "");
  const lastDot = name.lastIndexOf(".");
  return (lastDot > 0 ? name.slice(0, lastDot) : name) || "document";
}

function lockedOutputFilename(filename = "") {
  return `${getFileStem(filename)}.locked.pdf`;
}

function passwordCharacterCount(value = "") {
  // Python's len(str) counts Unicode code points. Array.from() mirrors that
  // contract more closely than JavaScript's UTF-16 code-unit String.length.
  return Array.from(String(value)).length;
}

function permissionFormFields(formData, permissions) {
  for (const [field, value] of Object.entries(permissions)) {
    formData.append(field, String(Boolean(value)));
  }
}

function createLockFormData({
  files,
  password,
  permissions,
  language,
}) {
  const formData = new FormData();
  const selectedFiles = Array.from(files || []).filter(Boolean);

  if (selectedFiles.length > 1) {
    selectedFiles.forEach((file) => formData.append("files", file));
    // The FastAPI batch route derives a source-specific output filename for
    // every item. This field remains present for contract compatibility only.
    formData.append("output_filename", "locked-document.pdf");
  } else if (selectedFiles.length === 1) {
    formData.append("file", selectedFiles[0]);
    formData.append("output_filename", lockedOutputFilename(selectedFiles[0].name));
  }

  // Passwords are intentionally never trimmed or normalized. The backend
  // treats the exact submitted string as the encryption secret.
  formData.append("password", password);
  formData.append("encryption", ENCRYPTION);
  permissionFormFields(formData, permissions);
  formData.append("system_language", systemLanguageFor(language));
  return formData;
}

function replacePlaceholders(value, replacements = {}) {
  return Object.entries(replacements).reduce(
    (text, [key, replacement]) =>
      String(text || "").replaceAll(`{${key}}`, String(replacement)),
    String(value || ""),
  );
}

export default function LockPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const account = useAccount();
  const { user, authChecked } = account;
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);
  const t = useMemo(
    () => lockPdfPageTranslations[language] || lockPdfPageTranslations.en,
    [language],
  );

  const [selectedFiles, setSelectedFiles] = useState([]);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [permissions, setPermissions] = useState(() => ({
    ...DEFAULT_PERMISSIONS,
  }));
  const [busy, setBusy] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);
  const [batchResult, setBatchResult] = useState(null);

  const file = selectedFiles[0] || null;
  const isBatch = selectedFiles.length > 1;
  const outputFilename = file
    ? lockedOutputFilename(file.name)
    : "locked-document.pdf";
  const result = response?.result || null;
  const resultFilename = String(result?.filename || outputFilename).trim();
  const downloadUrl = getAnalyzerResultDownloadUrl(result);
  const passwordCount = passwordCharacterCount(password);

  const permissionOptions = useMemo(
    () => [
      {
        key: "allow_printing",
        label: t.allowPrinting,
        description: t.allowPrintingHelp,
      },
      {
        key: "allow_copying",
        label: t.allowCopying,
        description: t.allowCopyingHelp,
      },
      {
        key: "allow_modifying",
        label: t.allowModifying,
        description: t.allowModifyingHelp,
      },
      {
        key: "allow_annotations",
        label: t.allowAnnotations,
        description: t.allowAnnotationsHelp,
      },
      {
        key: "allow_form_filling",
        label: t.allowFormFilling,
        description: t.allowFormFillingHelp,
      },
      {
        key: "allow_accessibility",
        label: t.allowAccessibility,
        description: t.allowAccessibilityHelp,
      },
    ],
    [t],
  );

  function resetResultState() {
    setError("");
    setResponse(null);
    setBatchResult(null);
  }

  async function handlePickedPdfFiles(fileList) {
    if (busy) return;

    const incomingFiles = Array.from(fileList || []).filter(Boolean);
    if (!incomingFiles.length) return;

    const combined = [...selectedFiles, ...incomingFiles];

    if (combined.length === 1) {
      const securityError = await validateBrowserUpload(
        combined[0],
        FILE_SECURITY_POLICY.pdfTool,
      );
      if (securityError) {
        setSelectedFiles([]);
        setError(securityError);
        setResponse(null);
        setBatchResult(null);
        return;
      }

      resetResultState();
      setSelectedFiles(combined);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(
      combined,
      FILE_SECURITY_POLICY.pdfTool,
      {
        account: batchAccount,
        featureLabel: t.batchFeatureLabel,
      },
    );

    if (batchValidation.message) {
      setError(batchValidation.message);
      setResponse(null);
      setBatchResult(null);
      return;
    }

    resetResultState();
    setSelectedFiles(batchValidation.files);
  }

  function handleRemoveFile(_removedFile, index) {
    if (busy) return;
    setSelectedFiles((current) =>
      current.filter((_, fileIndex) => fileIndex !== index),
    );
    resetResultState();
  }

  function updatePermission(key, checked) {
    setPermissions((current) => ({
      ...current,
      [key]: Boolean(checked),
    }));
    resetResultState();
  }

  async function validateBeforeSubmit() {
    if (!selectedFiles.length) return t.noFile;

    if (isBatch && batchLimit <= 0) {
      return t.batchPaidOnly;
    }

    if (isBatch) {
      const batchValidation = await validateBrowserBatchUploads(
        selectedFiles,
        FILE_SECURITY_POLICY.pdfTool,
        {
          account: batchAccount,
          featureLabel: t.batchFeatureLabel,
        },
      );
      if (batchValidation.message) return batchValidation.message;
    } else {
      const securityError = await validateBrowserUpload(
        selectedFiles[0],
        FILE_SECURITY_POLICY.pdfTool,
      );
      if (securityError) return securityError;
    }

    if (!password) return t.passwordRequired;
    if (passwordCount < PASSWORD_MIN_CHARACTERS) {
      return replacePlaceholders(t.passwordTooShort, {
        min: PASSWORD_MIN_CHARACTERS,
      });
    }
    if (passwordCount > PASSWORD_MAX_CHARACTERS) {
      return replacePlaceholders(t.passwordTooLong, {
        max: PASSWORD_MAX_CHARACTERS,
      });
    }
    if (password !== confirmPassword) return t.passwordMismatch;

    return "";
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (busy) return;

    setError("");
    setResponse(null);
    setBatchResult(null);

    const validationError = await validateBeforeSubmit();
    if (validationError) {
      setError(validationError);
      return;
    }

    const formData = createLockFormData({
      files: selectedFiles,
      password,
      permissions,
      language,
    });

    setBusy(true);
    try {
      if (isBatch) {
        const data = await postAnalyzerBatchFeature(BATCH_FEATURE_PATH, formData);
        setBatchResult(data);
        setResponse(null);
      } else {
        const data = await postAnalyzerFeature(
          FEATURE_PATH,
          formData,
          Boolean(user),
        );
        setResponse(data);
        setBatchResult(null);
      }

      // Minimize the lifetime of the encryption secret in React state after the
      // backend has completed the request successfully.
      setPassword("");
      setConfirmPassword("");
      setShowPassword(false);
    } catch (caught) {
      setError(resolveErrorMessage(caught, language, "PROCESSING_FAILED"));
    } finally {
      setBusy(false);
    }
  }

  async function handleShareLockedPdf() {
    if (!downloadUrl || sharing) return;

    if (typeof navigator === "undefined" || typeof navigator.share !== "function") {
      setError(t.shareUnavailable);
      return;
    }

    setError("");
    setSharing(true);
    try {
      const downloadResponse = await fetch(downloadUrl, {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/pdf,*/*" },
      });

      if (!downloadResponse.ok) {
        throw new Error("LOCKED_PDF_SHARE_DOWNLOAD_FAILED");
      }

      const blob = await downloadResponse.blob();
      if (!blob.size) throw new Error("LOCKED_PDF_SHARE_FILE_EMPTY");

      const shareFile = new File([blob], resultFilename || "locked-document.pdf", {
        type: blob.type || "application/pdf",
        lastModified: Date.now(),
      });
      const shareData = {
        title: resultFilename || t.resultTitle,
        text: t.shareText,
        files: [shareFile],
      };

      if (
        typeof navigator.canShare === "function" &&
        !navigator.canShare(shareData)
      ) {
        throw new Error("LOCKED_PDF_FILE_SHARE_UNSUPPORTED");
      }

      await navigator.share(shareData);
    } catch (caught) {
      if (caught?.name !== "AbortError") {
        setError(t.shareFailed);
      }
    } finally {
      setSharing(false);
    }
  }

  if (!authChecked) {
    return (
      <AppSidebarLayout>
        <main className="app-page flex min-h-screen items-center justify-center px-6 app-text">
          <div className="inline-flex items-center gap-3 rounded-3xl border app-surface-strong px-5 py-4 text-sm app-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t.loading}
          </div>
        </main>
      </AppSidebarLayout>
    );
  }

  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8">
        <button
          type="button"
          onClick={() => router.back()}
          className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted transition hover:app-text"
        >
          <ArrowLeft className="h-4 w-4" />
          {t.back}
        </button>

        <section className="mb-8 rounded-3xl border app-surface-strong p-6 shadow-2xl">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">
                {t.badge}
              </p>
              <h1 className="mt-3 text-3xl font-semibold tracking-tight app-text md:text-4xl">
                {t.title}
              </h1>
              <p className="mt-3 max-w-3xl app-text-muted">{t.description}</p>
            </div>
            <div className="inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm app-text-muted">
              <ShieldCheck className="h-4 w-4" />
              {t.aes256Only}
            </div>
          </div>
        </section>

        <form
          onSubmit={handleSubmit}
          className="grid gap-6 lg:grid-cols-[1fr_0.9fr]"
        >
          <section className="space-y-6">
            <div className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2>
              <p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>

              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center transition hover:bg-[var(--app-hover)]">
                <UploadCloud className="h-10 w-10 app-text-muted" />
                <span className="mt-3 text-sm font-semibold app-text">
                  {t.chooseFiles}
                </span>
                <span className="mt-1 text-xs app-text-soft">
                  {batchLimit > 0
                    ? replacePlaceholders(t.batchAvailable, { count: batchLimit })
                    : t.singleFileAccess}
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
            </div>

            <div className="rounded-3xl border app-surface-strong p-5">
              <div className="flex items-center gap-2">
                <KeyRound className="h-5 w-5 app-text-muted" />
                <h2 className="text-lg font-semibold app-text">
                  {t.passwordTitle}
                </h2>
              </div>
              <p className="mt-1 text-sm app-text-muted">{t.passwordHelp}</p>

              <div className="mt-4 grid gap-4">
                <label className="block text-sm font-medium app-text">
                  {t.passwordLabel}
                  <div className="relative mt-2">
                    <input
                      type={showPassword ? "text" : "password"}
                      value={password}
                      disabled={busy}
                      autoComplete="new-password"
                      spellCheck={false}
                      onChange={(event) => {
                        setPassword(event.target.value);
                        resetResultState();
                      }}
                      className="w-full rounded-2xl border app-surface px-4 py-3 pr-12 app-text"
                      aria-describedby="lock-password-help"
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((current) => !current)}
                      disabled={busy}
                      className="absolute inset-y-0 right-0 flex w-12 items-center justify-center app-text-muted transition hover:app-text disabled:opacity-50"
                      aria-label={showPassword ? t.hidePassword : t.showPassword}
                      title={showPassword ? t.hidePassword : t.showPassword}
                    >
                      {showPassword ? (
                        <EyeOff className="h-4 w-4" />
                      ) : (
                        <Eye className="h-4 w-4" />
                      )}
                    </button>
                  </div>
                </label>

                <label className="block text-sm font-medium app-text">
                  {t.confirmPasswordLabel}
                  <input
                    type={showPassword ? "text" : "password"}
                    value={confirmPassword}
                    disabled={busy}
                    autoComplete="new-password"
                    spellCheck={false}
                    onChange={(event) => {
                      setConfirmPassword(event.target.value);
                      resetResultState();
                    }}
                    className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                  />
                </label>
              </div>

              <div
                id="lock-password-help"
                className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs app-text-soft"
              >
                <span>
                  {replacePlaceholders(t.passwordLengthRule, {
                    min: PASSWORD_MIN_CHARACTERS,
                    max: PASSWORD_MAX_CHARACTERS,
                  })}
                </span>
                <span>
                  {replacePlaceholders(t.passwordCharacterCount, {
                    count: passwordCount,
                  })}
                </span>
              </div>
            </div>
          </section>

          <section className="space-y-6">
            <div className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">
                {t.securityOptionsTitle}
              </h2>
              <p className="mt-1 text-sm app-text-muted">
                {t.securityOptionsHelp}
              </p>

              <label className="mt-4 block text-sm font-medium app-text">
                {t.encryptionLabel}
                <input
                  value={t.encryptionValue}
                  readOnly
                  disabled
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                />
              </label>

              <div className="mt-5 space-y-3">
                {permissionOptions.map((option) => (
                  <label
                    key={option.key}
                    className="flex items-start gap-3 rounded-2xl border app-surface px-4 py-3"
                  >
                    <input
                      type="checkbox"
                      checked={Boolean(permissions[option.key])}
                      disabled={busy}
                      onChange={(event) =>
                        updatePermission(option.key, event.target.checked)
                      }
                      className="mt-1"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-medium app-text">
                        {option.label}
                      </span>
                      <span className="mt-0.5 block text-xs app-text-soft">
                        {option.description}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div className="rounded-3xl border app-surface-strong p-5">
              <label className="block text-sm font-medium app-text">
                {t.outputFilename}
                <input
                  value={isBatch ? t.batchOutputFilename : outputFilename}
                  readOnly
                  disabled
                  className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"
                />
              </label>
              <p className="mt-2 text-xs app-text-soft">
                {isBatch ? t.batchOutputHelp : t.outputFilenameHelp}
              </p>
            </div>

            {error ? (
              <div
                className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"
                role="alert"
              >
                <AlertTriangle className="mr-2 inline h-4 w-4" />
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={busy || !selectedFiles.length}
              className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] transition disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Lock className="h-4 w-4" />
              )}
              {busy ? t.locking : isBatch ? t.lockBatch : t.lock}
            </button>

            {result ? (
              <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5">
                <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
                  <CheckCircle2 className="h-5 w-5" />
                  {t.resultTitle}
                </h2>
                <p className="mt-2 text-sm app-text-muted">
                  {t.resultDescription}
                </p>

                <dl className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl border app-surface px-4 py-3">
                    <dt className="text-xs uppercase tracking-[0.12em] app-text-soft">
                      {t.resultFilename}
                    </dt>
                    <dd className="mt-1 break-all text-sm font-medium app-text">
                      {resultFilename}
                    </dd>
                  </div>
                  <div className="rounded-2xl border app-surface px-4 py-3">
                    <dt className="text-xs uppercase tracking-[0.12em] app-text-soft">
                      {t.resultEncryption}
                    </dt>
                    <dd className="mt-1 text-sm font-medium app-text">
                      {t.encryptionValue}
                    </dd>
                  </div>
                </dl>

                <div className="mt-4 flex flex-wrap gap-3">
                  {downloadUrl ? (
                    <a
                      href={downloadUrl}
                      className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                    >
                      <Download className="h-4 w-4" />
                      {t.download}
                    </a>
                  ) : null}
                  {downloadUrl ? (
                    <button
                      type="button"
                      onClick={handleShareLockedPdf}
                      disabled={sharing}
                      className="inline-flex items-center gap-2 rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text disabled:opacity-60"
                    >
                      {sharing ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Share2 className="h-4 w-4" />
                      )}
                      {sharing ? t.sharing : t.share}
                    </button>
                  ) : null}
                </div>

                <p className="mt-4 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-3 text-xs text-amber-200">
                  {t.passwordReminder}
                </p>
              </section>
            ) : null}
          </section>

          <div className="lg:col-span-2">
            <BatchResultPanel
              result={batchResult}
              title={t.batchResultsTitle}
              labels={t.batchResultLabels}
            />
            {batchResult ? (
              <p className="mt-3 rounded-2xl border border-amber-400/20 bg-amber-400/10 p-3 text-xs text-amber-200">
                {t.passwordReminder}
              </p>
            ) : null}
          </div>
        </form>
      </main>
    </AppSidebarLayout>
  );
}
