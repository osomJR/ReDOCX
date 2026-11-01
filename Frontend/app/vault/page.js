"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Download,
  Eye,
  File,
  FileText,
  Loader2,
  LockKeyhole,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import AppSidebarLayout from "@/components/app_sidebar";
import { postAnalyzerFeature } from "@/lib/api_client";
import { vaultPageTranslations as copy, resolveErrorMessage, resolveErrorTranslationKey } from "@/lib/translations";
import { vaultUploadPolicy, vaultUploadErrorCode } from "@/lib/vault_upload_policy";
import { uploadVaultFiles, downloadVaultFile } from "@/lib/vault_upload_client";

const MB = 1024 * 1024;
const MAX_VAULT_TEXT_BYTES = 128 * 1024;
const MAX_VAULT_TEXT_CHARACTERS = 50_000;
const PAGE_SIZE = 50;

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < MB) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / MB).toFixed(2)} MB`;
}

function formatDate(value, language) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === "fr" ? "fr-FR" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function errorMessage(error, fallback, t) {
  if (error?.message === "BACKEND_RESPONSE_INVALID") return fallback;
  const payload = error?.payload || {};
  const code = payload?.detail?.error || payload?.detail?.code || payload?.error?.code || payload?.error || error?.rawCode;
  if (code === "vault_storage_unavailable") return t.unavailable;
  if (code === "vault_integrity_error") return t.integrityFailed;
  if (code === "vault_item_not_found") return t.notFound;
  const key = resolveErrorTranslationKey(error);
  return key === "INTERNAL_ERROR" ? fallback : resolveErrorMessage(error, t.locale);
}

function safeVaultFilename(name) {
  const value = String(name || "");
  return Boolean(value) && !/[\\/\0]/u.test(value);
}

function itemTitle(item, t) {
  if (item?.item_kind === "unavailable") return t.itemUnavailable;
  return item?.item_kind === "file"
    ? item.filename || t.fileItem
    : t.textItem;
}

export default function VaultPage() {
  const { user, authChecked, entitlement } = useAccount();
  const owner = String(user?.id || user?.sub || "signed-out");
  return (
    <AppSidebarLayout>
      <VaultAccountPage key={owner} user={user} authChecked={authChecked} entitlement={entitlement} />
    </AppSidebarLayout>
  );
}

function VaultAccountPage({ user, authChecked, entitlement }) {
  const router = useRouter();
  const { language } = useLanguage();
  const t = copy[language] || copy.en;
  const ownerId = String(user?.id || user?.sub || "");
  const copyRef = useRef(t);
  useEffect(() => { copyRef.current = t; }, [t]);
  const uploadPolicy = useMemo(() => vaultUploadPolicy(entitlement), [entitlement?.plan, entitlement?.is_paid]);
  const fileInputRef = useRef(null);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [batchResults, setBatchResults] = useState(null);

  const [storeMode, setStoreMode] = useState("file");
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [privateText, setPrivateText] = useState("");
  const [items, setItems] = useState([]);
  const [unavailableItems, setUnavailableItems] = useState([]);
  const [listFailed, setListFailed] = useState(false);
  const [downloadingId, setDownloadingId] = useState("");
  const lifecycleRef = useRef({ active: true, requests: new Set() });
  const listSequenceRef = useRef(0);
  const listRequestRef = useRef(null);

  useEffect(() => {
    const lifecycle = lifecycleRef.current;
    lifecycle.active = true;
    return () => {
      lifecycle.active = false;
      for (const controller of lifecycle.requests) controller.abort();
      lifecycle.requests.clear();
    };
  }, []);

  const beginRequest = useCallback((externalSignal) => {
    const lifecycle = lifecycleRef.current;
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (externalSignal?.aborted) controller.abort();
    else externalSignal?.addEventListener("abort", abort, { once: true });
    lifecycle.requests.add(controller);
    return {
      controller,
      valid: () => lifecycle.active && !controller.signal.aborted,
      finish: () => {
        lifecycle.requests.delete(controller);
        externalSignal?.removeEventListener("abort", abort);
      },
    };
  }, []);
  const [nextCursor, setNextCursor] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [storing, setStoring] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const textByteLength = useMemo(() => {
    try {
      return new TextEncoder().encode(privateText).byteLength;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }, [privateText]);

  const loadItems = useCallback(
    async ({ append = false, cursor = "", signal } = {}) => {
      if (!ownerId) return;
      listRequestRef.current?.abort();
      const request = beginRequest(signal);
      listRequestRef.current = request.controller;
      const sequence = ++listSequenceRef.current;
      const current = () => request.valid() && sequence === listSequenceRef.current;
      setLoading(!append);
      setLoadingMore(append);
      setError("");
      try {
        const formData = new FormData();
        formData.set("operation", "list");
        formData.set("limit", String(PAGE_SIZE));
        if (cursor) formData.set("cursor", cursor);
        const data = await postAnalyzerFeature("vault", formData, true, { signal: request.controller.signal });
        if (!current()) return;
        const result = data?.result;
        const reportedUnavailable = result?.unavailable_items ?? [];
        const validReference = (item) => item && typeof item.item_id === "string" && item.item_id.trim().length > 0;
        if (!result || result.operation !== "list" || !Array.isArray(result.items)
            || !result.items.every(validReference) || !Array.isArray(reportedUnavailable)
            || !reportedUnavailable.every(validReference)
            || (result.next_cursor != null && typeof result.next_cursor !== "string")) {
          throw new Error("BACKEND_RESPONSE_INVALID");
        }
        const merge = (previous, incoming) => [...new Map([...previous, ...incoming].map((item) => [item.item_id, item])).values()];
        setListFailed(false);
        setItems((previous) => append ? merge(previous, result.items) : result.items);
        setUnavailableItems((previous) => append ? merge(previous, reportedUnavailable) : reportedUnavailable);
        setNextCursor(result.next_cursor || "");
      } catch (requestError) {
        if (current() && requestError?.name !== "AbortError") {
          setListFailed(true);
          setError(errorMessage(requestError, copyRef.current.requestFailed, copyRef.current));
        }
      } finally {
        if (current()) {
          setLoading(false);
          setLoadingMore(false);
          setHasLoaded(true);
        }
        request.finish();
      }
    },
    [beginRequest, ownerId],
  );

  useEffect(() => {
    if (!authChecked || !ownerId) return undefined;
    const controller = new AbortController();
    loadItems({ signal: controller.signal });
    return () => controller.abort();
  }, [authChecked, loadItems, ownerId]);

  async function handleStore(event) {
    event.preventDefault();
    if (storing) return;

    setError("");
    setMessage("");

    if (storeMode === "file") {
      const code = vaultUploadErrorCode(selectedFiles, uploadPolicy);
      if (code) {
        setError(resolveErrorMessage(code, language));
        return;
      }
      if (selectedFiles.some((file) => !safeVaultFilename(file.name))) {
        setError(t.unsafeFilename);
        return;
      }
    } else if (
      !privateText ||
      !privateText.trim() ||
      privateText.length > MAX_VAULT_TEXT_CHARACTERS ||
      textByteLength > MAX_VAULT_TEXT_BYTES
    ) {
      setError(t.invalidText);
      return;
    }

    setStoring(true);
    const request = beginRequest();
    try {
      setBatchResults(null);
      if (storeMode === "file") {
        const submitted = [...selectedFiles];
        const data = await uploadVaultFiles(submitted, { signal: request.controller.signal });
        if (!request.valid()) return;
        if (!Array.isArray(data?.items) || data.items.length !== submitted.length
            || !data.items.every((item, index) => item.index === index + 1
              && typeof item.success === "boolean"
              && (!item.success || (item.response?.result?.operation === "store" && item.response?.result?.item)))) {
          throw new Error("BACKEND_RESPONSE_INVALID");
        }
        const failed = data.items.filter((item) => !item.success);
        setBatchResults(data.items);
        setSelectedFiles(failed.map((item) => submitted[item.index - 1]));
        if (fileInputRef.current) fileInputRef.current.value = "";
        setMessage(t.batchSummary(submitted.length - failed.length, failed.length));
      } else {
        const formData = new FormData();
        formData.set("operation", "store");
        // Preserve the exact submitted text.
        formData.set("text", privateText);
        const data = await postAnalyzerFeature("vault", formData, true, { signal: request.controller.signal });
        if (!request.valid()) return;
        if (data?.result?.operation !== "store" || !data?.result?.item) {
          throw new Error("BACKEND_RESPONSE_INVALID");
        }
        setPrivateText("");
        setMessage(t.stored);
      }
      await loadItems();
    } catch (requestError) {
      if (request.valid() && requestError?.name !== "AbortError") setError(errorMessage(requestError, t.requestFailed, t));
    } finally {
      if (request.valid()) setStoring(false);
      request.finish();
    }
  }

  async function retrieveText(item) {
    if (!item?.item_id) return;
    setError("");
    setMessage("");

    const request = beginRequest();
    try {
      const formData = new FormData();
      formData.set("operation", "retrieve");
      formData.set("item_id", item.item_id);
      const data = await postAnalyzerFeature("vault", formData, true, { signal: request.controller.signal });
      if (!request.valid()) return;
      const result = data?.result;
      if (
        !result ||
        result.operation !== "retrieve" ||
        result.item?.item_kind !== "text" ||
        typeof result.text !== "string"
      ) {
        throw new Error("BACKEND_RESPONSE_INVALID");
      }
      setPreview(result);
    } catch (requestError) {
      if (request.valid() && requestError?.name !== "AbortError") setError(errorMessage(requestError, t.requestFailed, t));
    } finally {
      request.finish();
    }
  }

  async function confirmDelete() {
    const itemId = deleteTarget?.item_id;
    if (!itemId || deleting) return;

    setDeleting(true);
    setError("");
    setMessage("");

    const request = beginRequest();
    try {
      const formData = new FormData();
      formData.set("operation", "delete");
      formData.set("item_id", itemId);
      formData.set("confirm_delete", "true");
      const data = await postAnalyzerFeature("vault", formData, true, { signal: request.controller.signal });
      if (!request.valid()) return;
      if (
        data?.result?.operation !== "delete" ||
        data?.result?.deleted !== true
      ) {
        throw new Error("BACKEND_RESPONSE_INVALID");
      }

      setDeleteTarget(null);
      setPreview((current) =>
        current?.item?.item_id === itemId ? null : current,
      );
      setMessage(t.deleted);
      await loadItems();
    } catch (requestError) {
      if (request.valid() && requestError?.name !== "AbortError") setError(errorMessage(requestError, t.requestFailed, t));
    } finally {
      if (request.valid()) setDeleting(false);
      request.finish();
    }
  }

  async function downloadItem(item) {
    if (!item?.item_id || downloadingId) return;
    const request = beginRequest();
    setDownloadingId(item.item_id);
    setError("");
    try {
      const response = await downloadVaultFile(item.item_id, { signal: request.controller.signal });
      const blob = await response.blob();
      if (!request.valid()) return;
      if (Number.isFinite(item.file_size_bytes) && blob.size !== item.file_size_bytes) throw new Error(t.downloadFailed);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = item.filename || "vault-item";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (requestError) {
      if (request.valid() && requestError?.name !== "AbortError") setError(errorMessage(requestError, t.downloadFailed, t));
    } finally {
      if (request.valid()) setDownloadingId("");
      request.finish();
    }
  }

  if (!authChecked) {
    return (
      <div className="min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
        <div className="mx-auto flex min-h-[60vh] max-w-6xl items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin app-text-muted" />
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
        <section className="mx-auto flex min-h-[70vh] max-w-3xl items-center">
          <div className="w-full rounded-3xl border app-surface-strong p-8 text-center shadow-2xl md:p-10">
            <LockKeyhole className="mx-auto h-10 w-10 app-text-muted" />
            <h1 className="mt-5 text-3xl font-semibold app-text">{t.signInTitle}</h1>
            <p className="mx-auto mt-3 max-w-xl app-text-muted">{t.signInBody}</p>
            <a
              href="/auth/login?returnTo=/vault"
              className="mt-6 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]"
            >
              {t.signIn}
            </a>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--app-bg)] text-[var(--app-text)]">
      <div className="mx-auto max-w-7xl px-4 py-4 md:px-5">
        <header>
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => router.push("/")}
              className="inline-flex shrink-0 items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
            >
              <ArrowLeft className="h-4 w-4" />
              {t.back}
            </button>
            <div className="inline-flex min-w-0 items-center gap-1.5 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-2.5 py-1.5 text-[10px] font-medium text-[var(--app-accent-text)] sm:gap-2 sm:px-3 sm:text-sm">
              <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
              <span>{t.badge}</span>
            </div>
          </div>
          <h1 className="mt-3 max-w-4xl text-2xl font-semibold tracking-tight app-text sm:text-3xl">
            {t.title}
          </h1>
        </header>

        {(error || message) && (
          <div
            className={`mt-4 rounded-2xl border px-4 py-3 text-sm ${
              error
                ? "border-red-400/30 bg-red-400/10 text-red-200"
                : "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
            }`}
            role="status"
          >
            {error || message}
          </div>
        )}

        <div className="mt-5 grid items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)] xl:grid-cols-[340px_minmax(0,1fr)]">
          <section className="min-w-0 rounded-3xl border app-surface-strong p-4 shadow-xl lg:sticky lg:top-4">
            <form onSubmit={handleStore}>
              <div className="sticky top-4 z-10 space-y-3 rounded-2xl bg-[var(--app-surface-strong)] pb-3">
                <div className="grid grid-cols-2 rounded-2xl border app-surface p-1">
                  {[
                    ["file", t.fileMode, File],
                    ["text", t.textMode, FileText],
                  ].map(([mode, label, Icon]) => (
                    <button
                      key={mode}
                      type="button"
                      disabled={storing}
                      aria-pressed={storeMode === mode}
                      onClick={() => setStoreMode(mode)}
                      className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm font-semibold transition ${
                        storeMode === mode
                          ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                          : "app-text-muted hover:bg-[var(--app-hover)]"
                      }`}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {label}
                    </button>
                  ))}
                </div>
                <button
                  type="submit"
                  disabled={storing}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {storing ? <Loader2 className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
                  {storing ? t.storing : t.store}
                </button>
              </div>

              {storeMode === "file" ? (
                <div>
                  <label className="flex cursor-pointer items-center justify-center gap-3 rounded-2xl border border-dashed border-[var(--app-border-strong)] app-surface px-4 py-6 text-center transition hover:bg-[var(--app-hover)]">
                    <UploadCloud className="h-6 w-6 shrink-0 app-text-muted" />
                    <span className="text-sm font-semibold app-text">{t.chooseFile}</span>
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      disabled={storing}
                      className="sr-only"
                      onChange={(event) => {
                        const files = Array.from(event.target.files || []);
                        setSelectedFiles(files);
                        setBatchResults(null);
                        const code = vaultUploadErrorCode(files, uploadPolicy);
                        setError(code && files.length ? resolveErrorMessage(code, language) : "");
                        setMessage("");
                      }}
                    />
                  </label>
                  {selectedFiles.length > 0 && (
                    <div className="mt-3 rounded-xl border app-surface p-3">
                      <p className="text-xs app-text-muted">{t.selectedFiles(selectedFiles.length, formatBytes(selectedFiles.reduce((total, file) => total + file.size, 0)))}</p>
                      <ul className="mt-2 max-h-32 space-y-2 overflow-y-auto">
                        {selectedFiles.map((file, index) => (
                          <li key={`${index}-${file.name}`} className="flex items-center justify-between gap-3 text-sm">
                            <span className="min-w-0 break-all">{file.name} · {formatBytes(file.size)}</span>
                            <button type="button" disabled={storing} aria-label={`${t.removeFile}: ${file.name}`}
                              onClick={() => {
                                const remaining = selectedFiles.filter((_, current) => current !== index);
                                setSelectedFiles(remaining);
                                const code = remaining.length ? vaultUploadErrorCode(remaining, uploadPolicy) : "";
                                setError(code ? resolveErrorMessage(code, language) : "");
                                if (fileInputRef.current) fileInputRef.current.value = "";
                              }} className="shrink-0 rounded-lg border p-1"><X className="h-4 w-4" /></button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <label htmlFor="vault-private-text" className="text-sm font-medium app-text">
                    {t.textLabel}
                  </label>
                  <textarea
                    id="vault-private-text"
                    value={privateText}
                    onChange={(event) => {
                      setPrivateText(event.target.value);
                      setError("");
                      setMessage("");
                    }}
                    rows={6}
                    maxLength={MAX_VAULT_TEXT_CHARACTERS}
                    placeholder={t.textPlaceholder}
                    className="mt-2 max-h-56 min-h-32 w-full resize-y rounded-2xl border app-surface px-4 py-3 text-sm app-text outline-none transition focus:border-[var(--app-focus)]"
                  />
                </div>
              )}
            </form>
            {batchResults && (
              <section className="mt-4" aria-label={t.batchResults}>
                <h3 className="text-sm font-semibold app-text">{t.batchResults}</h3>
                <div className="mt-2 max-h-40 space-y-2 overflow-y-auto">
                  {batchResults.map((item) => (
                    <div key={item.index} className="rounded-xl border app-surface px-3 py-2 text-xs">
                      <p className="break-all font-medium app-text">{item.filename}</p>
                      <p className={item.success ? "text-emerald-500" : "text-red-500"}>
                        {item.success ? t.savedFile : errorMessage(item.error, t.requestFailed, t)}
                      </p>
                    </div>
                  ))}
                </div>
                {batchResults.some((item) => !item.success) && <p className="mt-2 text-xs app-text-muted">{t.failedFilesRetained}</p>}
              </section>
            )}
          </section>

          <section className="min-w-0 rounded-3xl border app-surface-strong p-4 shadow-xl" aria-busy={loading || loadingMore}>
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-xl font-semibold app-text">{t.itemsTitle}</h2>
              {loading && hasLoaded ? <Loader2 className="h-4 w-4 animate-spin app-text-muted" aria-label={t.loading} /> : null}
            </div>
            {unavailableItems.length > 0 ? (
              <p role="status" className="mt-3 rounded-xl border app-surface px-3 py-2 text-xs app-text-muted">{t.partialWarning}</p>
            ) : null}
            <div className="mt-3 space-y-2">
              {loading && !hasLoaded ? (
                <div className="flex items-center justify-center gap-2 rounded-2xl border app-surface px-4 py-10 text-sm app-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t.loading}
                </div>
              ) : listFailed && items.length === 0 ? (
                <div className="rounded-2xl border app-surface px-4 py-10 text-center text-sm app-text-muted">{t.listFailed}</div>
              ) : items.length === 0 && unavailableItems.length === 0 ? (
                <div className="rounded-2xl border app-surface px-4 py-10 text-center text-sm app-text-muted">{t.empty}</div>
              ) : (
                items.map((item) => {
                  const isFile = item.item_kind === "file";
                  const ItemIcon = isFile ? File : FileText;
                  return (
                    <article key={item.item_id} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border app-surface p-3">
                      <div className="flex min-w-0 flex-1 basis-40 items-center gap-2.5">
                        <div className="shrink-0 rounded-xl border app-surface-strong p-2">
                          <ItemIcon className="h-4 w-4 app-text-muted" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <h3 className="truncate text-sm font-semibold app-text" title={itemTitle(item, t)}>
                            {itemTitle(item, t)}
                          </h3>
                          <dl className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs app-text-muted">
                            <div className="flex flex-wrap gap-x-1">
                              <dt className="app-text-soft">{t.created}:</dt>
                              <dd>{formatDate(item.created_at_iso, language)}</dd>
                            </div>
                            <div className="flex gap-1">
                              <dt className="app-text-soft">{t.size}:</dt>
                              <dd>{formatBytes(item.file_size_bytes)}</dd>
                            </div>
                          </dl>
                        </div>
                      </div>
                      <div className="ml-auto flex shrink-0 flex-wrap gap-1.5">
                        {isFile ? (
                          <button
                            type="button"
                            onClick={() => downloadItem(item)}
                            disabled={Boolean(downloadingId)}
                            className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--app-button-bg)] px-2.5 py-2 text-xs font-semibold text-[var(--app-button-text)] disabled:opacity-50"
                          >
                            {downloadingId === item.item_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                            {t.download}
                          </button>
                        ) : (
                          <button
                            type="button"
                            onClick={() => retrieveText(item)}
                            className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--app-button-bg)] px-2.5 py-2 text-xs font-semibold text-[var(--app-button-text)]"
                          >
                            <Eye className="h-3.5 w-3.5" />
                            {t.retrieve}
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => setDeleteTarget(item)}
                          className="inline-flex items-center gap-1.5 rounded-xl border border-red-400/30 bg-red-400/10 px-2.5 py-2 text-xs font-semibold text-red-200"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          {t.delete}
                        </button>
                      </div>
                    </article>
                  );
                })
              )}
            </div>

            {unavailableItems.map((item) => (
              <article key={`unavailable-${item.item_id}`} className="mt-2 flex flex-wrap items-center justify-between gap-3 rounded-2xl border app-surface p-3">
                <p className="text-sm font-semibold app-text">{t.itemUnavailable}</p>
                <button type="button" onClick={() => setDeleteTarget({ ...item, item_kind: "unavailable" })} className="inline-flex items-center gap-1.5 rounded-xl border border-red-400/30 bg-red-400/10 px-2.5 py-2 text-xs font-semibold text-red-200">
                  <Trash2 className="h-3.5 w-3.5" />{t.delete}
                </button>
              </article>
            ))}

            {nextCursor ? (
              <button
                type="button"
                onClick={() => loadItems({ append: true, cursor: nextCursor })}
                disabled={loadingMore || loading}
                className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border app-surface px-4 py-2.5 text-sm font-semibold app-text disabled:opacity-50"
              >
                {loadingMore ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {loadingMore ? t.loadingMore : t.loadMore}
              </button>
            ) : null}
          </section>
        </div>
      </div>

      {preview ? (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            className="w-full max-w-3xl rounded-3xl border app-surface-strong p-6 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-border)] px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                  <LockKeyhole className="h-3.5 w-3.5" />
                  {t.previewTitle}
                </div>
              </div>
              <button
                type="button"
                aria-label={t.close}
                onClick={() => setPreview(null)}
                className="rounded-xl border app-surface p-2 app-text-muted"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <pre className="mt-5 max-h-[55vh] overflow-auto whitespace-pre-wrap break-words rounded-2xl border app-surface p-4 text-sm leading-6 app-text">
              {preview.text}
            </pre>
          </section>
        </div>
      ) : null}

      {deleteTarget ? (
        <div className="fixed inset-0 z-[130] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <section
            role="dialog"
            aria-modal="true"
            className="w-full max-w-md rounded-3xl border app-surface-strong p-6 shadow-2xl"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-red-400/30 bg-red-400/10 text-red-200">
              <Trash2 className="h-5 w-5" />
            </div>
            <h2 className="mt-5 text-xl font-semibold app-text">{t.deleteTitle}</h2>
            <p className="mt-2 text-sm leading-6 app-text-muted">{t.deleteBody}</p>
            <p className="mt-3 truncate rounded-xl border app-surface px-3 py-2 text-sm app-text">
              {itemTitle(deleteTarget, t)}
            </p>
            <div className="mt-6 grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                className="rounded-xl border app-surface px-4 py-2.5 text-sm font-semibold app-text disabled:opacity-50"
              >
                {t.cancel}
              </button>
              <button
                type="button"
                onClick={confirmDelete}
                disabled={deleting}
                className="inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
              >
                {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {deleting ? t.deleting : t.confirmDelete}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
