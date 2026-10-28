"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
  ShieldCheck,
  UploadCloud,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel, { getBatchResultSummary } from "@/components/batch_result_panel";
import SelectedFilesSummary from "@/components/selected_files_summary";
import {
  getAnalyzerResultDownloadUrl,
  postAnalyzerBatchFeature,
  postAnalyzerFeature,
} from "@/lib/api_client";
import {
  lockPdfPageTranslations,
  hidePdfUploadLimitDetails,
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
  const effective = {
    ...permissions,
    allow_form_filling: Boolean(permissions.allow_form_filling || permissions.allow_annotations),
    allow_accessibility: true,
  };
  for (const [field, value] of Object.entries(effective)) {
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
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);
  const [batchResult, setBatchResult] = useState(null);
  const resultsRef = useRef(null);
  const errorRef = useRef(null);

  const file = selectedFiles[0] || null;
  const isBatch = selectedFiles.length > 1;
  const outputFilename = file
    ? lockedOutputFilename(file.name)
    : "locked-document.pdf";
  const result = response?.result || null;
  const resultFilename = String(result?.filename || outputFilename).trim();
  const downloadUrl = getAnalyzerResultDownloadUrl(result);
  const passwordCount = passwordCharacterCount(password);
  const processingTitle = isBatch
    ? replacePlaceholders(t.processingTitle, { count: selectedFiles.length })
    : t.processingSingleTitle;
  const batchSummary = getBatchResultSummary(batchResult);
  const hasResults = Boolean(result || batchSummary.total);
  const completionTitle = batchSummary.total
    ? batchSummary.succeeded === 0
      ? t.batchFailedTitle
      : batchSummary.failed || batchSummary.missing
        ? t.batchIssuesTitle
        : replacePlaceholders(t.batchCompletedTitle, { count: batchSummary.succeeded })
    : downloadUrl ? t.singleCompletedTitle : t.outputUnavailableTitle;
  const completionDescription = batchSummary.total
    ? replacePlaceholders(t.batchCompletedHelp, {
        ready: batchSummary.ready,
        total: batchSummary.total,
      })
    : downloadUrl ? t.resultDescription : t.batchResultLabels.noOutput;

  useEffect(() => {
    if (busy || (!response && !batchResult)) return;
    const target = resultsRef.current;
    if (!target) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant" : "smooth",
      block: "start",
    });
  }, [busy, response, batchResult]);

  useEffect(() => {
    if (!error || busy) return;
    errorRef.current?.focus({ preventScroll: true });
    errorRef.current?.scrollIntoView({ block: "center", behavior: "instant" });
  }, [error, busy]);

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
        setError(hidePdfUploadLimitDetails(securityError, language));
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
      setError(hidePdfUploadLimitDetails(batchValidation.message, language));
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
      setError(hidePdfUploadLimitDetails(validationError, language));
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
      setError(hidePdfUploadLimitDetails(resolveErrorMessage(caught, language, "PROCESSING_FAILED"), language));
    } finally {
      setBusy(false);
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
            </div>
            <div className="inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-3 text-sm app-text-muted">
              <ShieldCheck className="h-4 w-4" />
              {t.aes256Only}
            </div>
          </div>
        </section>

        <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {busy ? processingTitle : hasResults ? `${completionTitle}. ${completionDescription}` : ""}
        </p>

        {busy ? (
          <section className="mb-6 flex items-start gap-3 rounded-3xl border app-surface-strong p-5" aria-label={t.processingLabel}>
            <Loader2 className="mt-1 h-6 w-6 shrink-0 animate-spin app-text-muted motion-reduce:animate-none" aria-hidden="true" />
            <div>
              <h2 className="text-lg font-semibold app-text">{processingTitle}</h2>
              <p className="mt-1 text-sm app-text-muted">{t.processingHelp}</p>
            </div>
          </section>
        ) : null}

        {hasResults ? (
          <section
            ref={resultsRef}
            tabIndex={-1}
            aria-labelledby="lock-results-title"
            className="mb-6 min-w-0 scroll-mt-6 rounded-3xl focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--app-accent-border)]"
          >
            {batchSummary.total > 0 ? (
              <div className="rounded-3xl border app-surface-strong p-5">
                <BatchResultPanel
                  result={batchResult}
                  title={completionTitle}
                  description={completionDescription}
                  headingId="lock-results-title"
                  labels={t.batchResultLabels}
                  embedded
                  compact
                  showBatchMetadata={false}
                />
                {batchSummary.ready > 0 ? (
                  <p className="mt-4 rounded-xl border app-surface p-3 text-xs app-text-muted">{t.passwordReminder}</p>
                ) : null}
              </div>
            ) : null}
            {!batchSummary.total && result ? (
              <div className="rounded-3xl border app-surface-strong p-5">
                <div className="flex items-start gap-3">
                  {downloadUrl ? <CheckCircle2 className="mt-1 h-6 w-6 shrink-0 text-emerald-500" aria-hidden="true" /> : <AlertTriangle className="mt-1 h-6 w-6 shrink-0 text-amber-500" aria-hidden="true" />}
                  <div className="min-w-0">
                    <h2 id="lock-results-title" className="text-xl font-semibold app-text">{completionTitle}</h2>
                    <p className="mt-1 text-sm app-text-muted">{completionDescription}</p>
                  </div>
                </div>

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
                      download={resultFilename}
                      className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--app-accent-border)] sm:w-auto"
                    >
                      <Download className="h-4 w-4" />
                      {t.download}
                    </a>
                  ) : null}
                </div>

                <p className="mt-4 rounded-xl border app-surface p-3 text-xs app-text-muted">
                  {t.passwordReminder}
                </p>
              </div>
            ) : null}
          </section>
        ) : null}

        {error ? (
          <div
            ref={errorRef}
            tabIndex={-1}
            className="mb-6 rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm app-text focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400"
            role="alert"
          >
            <AlertTriangle className="mr-2 inline h-4 w-4 text-red-500" aria-hidden="true" />
            {error}
          </div>
        ) : null}

        <form
          onSubmit={handleSubmit}
          className="grid min-w-0 gap-6 lg:grid-cols-[1fr_0.9fr]"
          aria-busy={busy}
        >
          <section className="min-w-0 space-y-6">
            <div className="rounded-3xl border app-surface-strong p-5">
              <h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2>
              <p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p>

              <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center transition hover:bg-[var(--app-hover)]">
                <UploadCloud className="h-10 w-10 app-text-muted" />
                <span className="mt-3 text-sm font-semibold app-text">
                  {t.chooseFiles}
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
                language={language}
                className="mt-4"
                onRemoveFile={handleRemoveFile}
                disabled={busy}
                collapsible
              />
            </div>

            <div className="rounded-3xl border app-surface-strong p-5">
              <div className="flex items-center gap-2">
                <KeyRound className="h-5 w-5 app-text-muted" />
                <h2 className="text-lg font-semibold app-text">
                  {t.passwordTitle}
                </h2>
              </div>

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
          </section>

          <section className="min-w-0 space-y-6">
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
                      checked={
                        option.key === "allow_accessibility" ||
                        (option.key === "allow_form_filling" && permissions.allow_annotations) ||
                        Boolean(permissions[option.key])
                      }
                      disabled={
                        busy || option.key === "allow_accessibility" ||
                        (option.key === "allow_form_filling" && permissions.allow_annotations)
                      }
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
                        {option.key === "allow_form_filling" && permissions.allow_annotations
                          ? t.formFillingRequiredByAnnotations
                          : option.description}
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
              {isBatch ? (
                <p className="mt-2 text-xs app-text-soft">{t.batchOutputHelp}</p>
              ) : null}
            </div>
          </section>
        </form>
      </main>
    </AppSidebarLayout>
  );
}
