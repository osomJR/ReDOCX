"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Download,
  Eye,
  File,
  FileText,
  FolderLock,
  Loader2,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { postAnalyzerFeature } from "@/lib/api_client";

const MB = 1024 * 1024;
const MAX_VAULT_FILE_BYTES = 100 * MB;
const MAX_VAULT_TEXT_BYTES = 128 * 1024;
const MAX_VAULT_TEXT_CHARACTERS = 50_000;
const PAGE_SIZE = 50;

const copy = {
  en: {
    back: "Back to dashboard",
    badge: "Encrypted private storage",
    title: "Vault",
    description:
      "Store private files and notes in your owner-scoped ReDOCX Vault. Vault content is encrypted at rest and remains private to the authenticated account.",
    securityNote:
      "Ownership is resolved by the server. Cross-account Vault access is not permitted.",
    storeTitle: "Store in Vault",
    fileMode: "File",
    textMode: "Private text",
    chooseFile: "Choose file",
    fileHelp: "Any file type is accepted up to 100 MB. ReDOCX validates and scans the upload on the server before storage.",
    textLabel: "Private text",
    textPlaceholder: "Enter the private text you want to store...",
    textHelp: "Up to 50,000 characters / 128 KB. The submitted text is stored without client-side rewriting.",
    store: "Store securely",
    storing: "Storing...",
    stored: "Stored securely in Vault.",
    itemsTitle: "Your Vault",
    searchPlaceholder: "Filter by filename",
    contentTypePlaceholder: "Filter by content type",
    search: "Apply filters",
    clear: "Clear",
    refresh: "Refresh",
    loading: "Loading Vault...",
    empty: "Your Vault is empty.",
    emptyFiltered: "No Vault items match these filters.",
    fileItem: "File",
    textItem: "Private text",
    retrieve: "Open",
    download: "Download",
    delete: "Delete",
    loadMore: "Load more",
    loadingMore: "Loading...",
    previewTitle: "Private text",
    close: "Close",
    deleteTitle: "Delete Vault item?",
    deleteBody:
      "This permanently removes the encrypted Vault item. This action cannot be undone.",
    cancel: "Cancel",
    confirmDelete: "Delete permanently",
    deleting: "Deleting...",
    deleted: "Vault item deleted.",
    signInTitle: "Sign in required",
    signInBody: "Vault is available only to authenticated ReDOCX users.",
    signIn: "Sign in",
    invalidFile: "Select a non-empty file up to 100 MB.",
    unsafeFilename: "The selected filename is not safe. Rename the file and try again.",
    invalidText: "Enter non-empty private text within the Vault size limit.",
    requestFailed: "The Vault request could not be completed.",
    itemId: "Item ID",
    created: "Created",
    size: "Size",
    contentType: "Content type",
  },
  fr: {
    back: "Retour au tableau de bord",
    badge: "Stockage privé chiffré",
    title: "Coffre-fort",
    description:
      "Stockez des fichiers et notes privés dans votre coffre-fort ReDOCX associé à votre compte. Le contenu est chiffré au repos et reste privé pour le compte authentifié.",
    securityNote:
      "Le propriétaire est déterminé par le serveur. L’accès entre comptes au coffre-fort n’est pas autorisé.",
    storeTitle: "Stocker dans le coffre-fort",
    fileMode: "Fichier",
    textMode: "Texte privé",
    chooseFile: "Choisir un fichier",
    fileHelp: "Tous les types de fichiers sont acceptés jusqu’à 100 Mo. ReDOCX valide et analyse le fichier côté serveur avant stockage.",
    textLabel: "Texte privé",
    textPlaceholder: "Saisissez le texte privé à stocker...",
    textHelp: "Jusqu’à 50 000 caractères / 128 Ko. Le texte envoyé est stocké sans réécriture côté client.",
    store: "Stocker en sécurité",
    storing: "Stockage...",
    stored: "Stocké en sécurité dans le coffre-fort.",
    itemsTitle: "Votre coffre-fort",
    searchPlaceholder: "Filtrer par nom de fichier",
    contentTypePlaceholder: "Filtrer par type de contenu",
    search: "Appliquer les filtres",
    clear: "Effacer",
    refresh: "Actualiser",
    loading: "Chargement du coffre-fort...",
    empty: "Votre coffre-fort est vide.",
    emptyFiltered: "Aucun élément ne correspond à ces filtres.",
    fileItem: "Fichier",
    textItem: "Texte privé",
    retrieve: "Ouvrir",
    download: "Télécharger",
    delete: "Supprimer",
    loadMore: "Charger plus",
    loadingMore: "Chargement...",
    previewTitle: "Texte privé",
    close: "Fermer",
    deleteTitle: "Supprimer cet élément ?",
    deleteBody:
      "Cette action supprime définitivement l’élément chiffré du coffre-fort et ne peut pas être annulée.",
    cancel: "Annuler",
    confirmDelete: "Supprimer définitivement",
    deleting: "Suppression...",
    deleted: "Élément supprimé du coffre-fort.",
    signInTitle: "Connexion requise",
    signInBody: "Le coffre-fort est réservé aux utilisateurs ReDOCX authentifiés.",
    signIn: "Se connecter",
    invalidFile: "Sélectionnez un fichier non vide de 100 Mo maximum.",
    unsafeFilename: "Le nom du fichier n’est pas sûr. Renommez-le puis réessayez.",
    invalidText: "Saisissez un texte privé non vide dans la limite autorisée.",
    requestFailed: "La requête du coffre-fort n’a pas pu être exécutée.",
    itemId: "ID de l’élément",
    created: "Créé",
    size: "Taille",
    contentType: "Type de contenu",
  },
};

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

function errorMessage(error, fallback) {
  return (
    error?.payload?.detail?.message ||
    error?.payload?.error?.message ||
    error?.message ||
    fallback
  );
}

function safeVaultFilename(name) {
  const value = String(name || "");
  return Boolean(value) && !/[\\/\0]/u.test(value);
}

function buildVaultDownloadUrl(itemId) {
  const cleanId = String(itemId || "").trim();
  return cleanId
    ? `/api/analyzer/vault/items/${encodeURIComponent(cleanId)}/download`
    : "";
}

function itemTitle(item, t) {
  return item?.item_kind === "file"
    ? item.filename || t.fileItem
    : t.textItem;
}

export default function VaultPage() {
  const { user, authChecked } = useAccount();
  const { language } = useLanguage();
  const t = copy[language] || copy.en;

  const [storeMode, setStoreMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [privateText, setPrivateText] = useState("");
  const [items, setItems] = useState([]);
  const [nextCursor, setNextCursor] = useState("");
  const [filenameFilter, setFilenameFilter] = useState("");
  const [contentTypeFilter, setContentTypeFilter] = useState("");
  const [appliedFilters, setAppliedFilters] = useState({
    filename: "",
    contentType: "",
  });
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [storing, setStoring] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);

  const hasFilters = Boolean(
    appliedFilters.filename || appliedFilters.contentType,
  );

  const textByteLength = useMemo(() => {
    try {
      return new TextEncoder().encode(privateText).byteLength;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }, [privateText]);

  const loadItems = useCallback(
    async ({ append = false, cursor = "", signal } = {}) => {
      if (!user) return;

      append ? setLoadingMore(true) : setLoading(true);
      setError("");

      try {
        const formData = new FormData();
        formData.set("operation", "list");
        formData.set("limit", String(PAGE_SIZE));
        if (cursor) formData.set("cursor", cursor);
        if (appliedFilters.filename) {
          formData.set("filename_contains", appliedFilters.filename);
        }
        if (appliedFilters.contentType) {
          formData.set("content_type", appliedFilters.contentType);
        }

        const data = await postAnalyzerFeature(
          "vault",
          formData,
          true,
          { signal },
        );
        const result = data?.result;
        if (!result || result.operation !== "list" || !Array.isArray(result.items)) {
          throw new Error("BACKEND_RESPONSE_INVALID");
        }

        setItems((current) =>
          append ? [...current, ...result.items] : result.items,
        );
        setNextCursor(result.next_cursor || "");
      } catch (requestError) {
        if (requestError?.name !== "AbortError") {
          setError(errorMessage(requestError, t.requestFailed));
        }
      } finally {
        append ? setLoadingMore(false) : setLoading(false);
      }
    },
    [appliedFilters, t.requestFailed, user],
  );

  useEffect(() => {
    if (!authChecked || !user) return undefined;
    const controller = new AbortController();
    loadItems({ signal: controller.signal });
    return () => controller.abort();
  }, [authChecked, loadItems, user]);

  async function handleStore(event) {
    event.preventDefault();
    if (storing) return;

    setError("");
    setMessage("");

    if (storeMode === "file") {
      if (
        !selectedFile ||
        !Number.isFinite(selectedFile.size) ||
        selectedFile.size <= 0 ||
        selectedFile.size > MAX_VAULT_FILE_BYTES
      ) {
        setError(t.invalidFile);
        return;
      }
      if (!safeVaultFilename(selectedFile.name)) {
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
    try {
      const formData = new FormData();
      formData.set("operation", "store");
      if (storeMode === "file") {
        formData.set("file", selectedFile);
      } else {
        // Vault intentionally stores the exact submitted text. Do not normalize it here.
        formData.set("text", privateText);
      }

      const data = await postAnalyzerFeature("vault", formData, true);
      if (data?.result?.operation !== "store" || !data?.result?.item) {
        throw new Error("BACKEND_RESPONSE_INVALID");
      }

      setSelectedFile(null);
      setPrivateText("");
      setMessage(t.stored);
      await loadItems();
    } catch (requestError) {
      setError(errorMessage(requestError, t.requestFailed));
    } finally {
      setStoring(false);
    }
  }

  async function retrieveText(item) {
    if (!item?.item_id) return;
    setError("");
    setMessage("");

    try {
      const formData = new FormData();
      formData.set("operation", "retrieve");
      formData.set("item_id", item.item_id);
      const data = await postAnalyzerFeature("vault", formData, true);
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
      setError(errorMessage(requestError, t.requestFailed));
    }
  }

  async function confirmDelete() {
    const itemId = deleteTarget?.item_id;
    if (!itemId || deleting) return;

    setDeleting(true);
    setError("");
    setMessage("");

    try {
      const formData = new FormData();
      formData.set("operation", "delete");
      formData.set("item_id", itemId);
      formData.set("confirm_delete", "true");
      const data = await postAnalyzerFeature("vault", formData, true);
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
      setError(errorMessage(requestError, t.requestFailed));
    } finally {
      setDeleting(false);
    }
  }

  function applyFilters(event) {
    event.preventDefault();
    setMessage("");
    setAppliedFilters({
      filename: filenameFilter.trim(),
      contentType: contentTypeFilter.trim(),
    });
  }

  function clearFilters() {
    setFilenameFilter("");
    setContentTypeFilter("");
    setAppliedFilters({ filename: "", contentType: "" });
  }

  if (!authChecked) {
    return (
      <main className="app-shell min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
        <div className="mx-auto flex min-h-[60vh] max-w-6xl items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin app-text-muted" />
        </div>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="app-shell min-h-screen bg-[var(--app-bg)] px-6 py-12 text-[var(--app-text)] md:px-8">
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
      </main>
    );
  }

  return (
    <main className="app-shell min-h-screen bg-[var(--app-bg)] px-4 py-8 text-[var(--app-text)] sm:px-6 md:px-8">
      <div className="mx-auto max-w-7xl">
        <a href="/" className="text-sm font-medium app-text-muted hover:text-[var(--app-text)]">
          ← {t.back}
        </a>

        <section className="mt-5 rounded-3xl border app-surface-strong p-6 shadow-xl md:p-8">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
            <div className="max-w-3xl">
              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-border)] px-3 py-1 text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                <ShieldCheck className="h-3.5 w-3.5" />
                {t.badge}
              </div>
              <h1 className="mt-5 text-3xl font-semibold tracking-tight app-text sm:text-4xl">
                {t.title}
              </h1>
              <p className="mt-4 max-w-3xl leading-7 app-text-muted">{t.description}</p>
            </div>
            <div className="rounded-2xl border app-surface px-4 py-3 text-sm app-text-muted lg:max-w-sm">
              <div className="flex gap-3">
                <FolderLock className="mt-0.5 h-5 w-5 shrink-0" />
                <p>{t.securityNote}</p>
              </div>
            </div>
          </div>
        </section>

        {(error || message) && (
          <div
            className={`mt-5 rounded-2xl border px-4 py-3 text-sm ${
              error
                ? "border-red-400/30 bg-red-400/10 text-red-200"
                : "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
            }`}
            role="status"
          >
            {error || message}
          </div>
        )}

        <div className="mt-6 grid gap-6 xl:grid-cols-[0.82fr_1.18fr]">
          <section className="rounded-3xl border app-surface-strong p-5 shadow-xl md:p-6">
            <h2 className="text-xl font-semibold app-text">{t.storeTitle}</h2>

            <div className="mt-4 grid grid-cols-2 rounded-2xl border app-surface p-1">
              {[
                ["file", t.fileMode, File],
                ["text", t.textMode, FileText],
              ].map(([mode, label, Icon]) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setStoreMode(mode)}
                  className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                    storeMode === mode
                      ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                      : "app-text-muted hover:bg-[var(--app-hover)]"
                  }`}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </button>
              ))}
            </div>

            <form onSubmit={handleStore} className="mt-5 space-y-5">
              {storeMode === "file" ? (
                <div>
                  <label className="block text-sm font-medium app-text">{t.chooseFile}</label>
                  <label className="mt-2 flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--app-border-strong)] app-surface px-5 py-8 text-center transition hover:bg-[var(--app-hover)]">
                    <UploadCloud className="h-8 w-8 app-text-muted" />
                    <span className="mt-3 text-sm font-semibold app-text">
                      {selectedFile?.name || t.chooseFile}
                    </span>
                    {selectedFile ? (
                      <span className="mt-1 text-xs app-text-soft">
                        {formatBytes(selectedFile.size)}
                      </span>
                    ) : null}
                    <input
                      type="file"
                      className="sr-only"
                      onChange={(event) => {
                        setSelectedFile(event.target.files?.[0] || null);
                        setError("");
                        setMessage("");
                      }}
                    />
                  </label>
                  <p className="mt-2 text-xs leading-5 app-text-soft">{t.fileHelp}</p>
                </div>
              ) : (
                <div>
                  <div className="flex items-center justify-between gap-3">
                    <label htmlFor="vault-private-text" className="text-sm font-medium app-text">
                      {t.textLabel}
                    </label>
                    <span className="text-xs app-text-soft">
                      {privateText.length.toLocaleString()} / {MAX_VAULT_TEXT_CHARACTERS.toLocaleString()}
                    </span>
                  </div>
                  <textarea
                    id="vault-private-text"
                    value={privateText}
                    onChange={(event) => {
                      setPrivateText(event.target.value);
                      setError("");
                      setMessage("");
                    }}
                    rows={10}
                    maxLength={MAX_VAULT_TEXT_CHARACTERS}
                    placeholder={t.textPlaceholder}
                    className="mt-2 w-full resize-y rounded-2xl border app-surface px-4 py-3 text-sm app-text outline-none transition focus:border-[var(--app-focus)]"
                  />
                  <p className="mt-2 text-xs leading-5 app-text-soft">{t.textHelp}</p>
                </div>
              )}

              <button
                type="submit"
                disabled={storing}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {storing ? <Loader2 className="h-4 w-4 animate-spin" /> : <LockKeyhole className="h-4 w-4" />}
                {storing ? t.storing : t.store}
              </button>
            </form>
          </section>

          <section className="rounded-3xl border app-surface-strong p-5 shadow-xl md:p-6">
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <h2 className="text-xl font-semibold app-text">{t.itemsTitle}</h2>
              <button
                type="button"
                onClick={() => loadItems()}
                disabled={loading}
                className="inline-flex items-center gap-2 self-start rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text disabled:opacity-50"
              >
                <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
                {t.refresh}
              </button>
            </div>

            <form onSubmit={applyFilters} className="mt-5 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
              <input
                value={filenameFilter}
                onChange={(event) => setFilenameFilter(event.target.value)}
                placeholder={t.searchPlaceholder}
                className="rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
              />
              <input
                value={contentTypeFilter}
                onChange={(event) => setContentTypeFilter(event.target.value)}
                placeholder={t.contentTypePlaceholder}
                className="rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2.5 text-sm font-semibold text-[var(--app-button-text)]"
                >
                  <Search className="h-4 w-4" />
                  {t.search}
                </button>
                {hasFilters ? (
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="rounded-xl border app-surface px-3 py-2.5 text-sm font-semibold app-text"
                  >
                    {t.clear}
                  </button>
                ) : null}
              </div>
            </form>

            <div className="mt-5 space-y-3">
              {loading ? (
                <div className="flex items-center justify-center gap-2 rounded-2xl border app-surface px-4 py-12 text-sm app-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t.loading}
                </div>
              ) : items.length === 0 ? (
                <div className="rounded-2xl border app-surface px-4 py-12 text-center text-sm app-text-muted">
                  {hasFilters ? t.emptyFiltered : t.empty}
                </div>
              ) : (
                items.map((item) => {
                  const isFile = item.item_kind === "file";
                  const ItemIcon = isFile ? File : FileText;
                  const downloadUrl = isFile
                    ? buildVaultDownloadUrl(item.item_id)
                    : "";

                  return (
                    <article
                      key={item.item_id}
                      className="rounded-2xl border app-surface p-4"
                    >
                      <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
                        <div className="min-w-0">
                          <div className="flex items-start gap-3">
                            <div className="rounded-xl border app-surface-strong p-2.5">
                              <ItemIcon className="h-5 w-5 app-text-muted" />
                            </div>
                            <div className="min-w-0">
                              <h3 className="truncate font-semibold app-text">
                                {itemTitle(item, t)}
                              </h3>
                              <p className="mt-1 break-all text-xs app-text-soft">
                                {t.itemId}: {item.item_id}
                              </p>
                            </div>
                          </div>

                          <dl className="mt-4 grid gap-2 text-xs app-text-muted sm:grid-cols-3">
                            <div>
                              <dt className="app-text-soft">{t.created}</dt>
                              <dd className="mt-0.5">{formatDate(item.created_at_iso, language)}</dd>
                            </div>
                            <div>
                              <dt className="app-text-soft">{t.size}</dt>
                              <dd className="mt-0.5">{formatBytes(item.file_size_bytes)}</dd>
                            </div>
                            <div>
                              <dt className="app-text-soft">{t.contentType}</dt>
                              <dd className="mt-0.5 break-all">{item.content_type || "—"}</dd>
                            </div>
                          </dl>
                        </div>

                        <div className="flex shrink-0 flex-wrap gap-2">
                          {isFile ? (
                            <a
                              href={downloadUrl}
                              className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                            >
                              <Download className="h-4 w-4" />
                              {t.download}
                            </a>
                          ) : (
                            <button
                              type="button"
                              onClick={() => retrieveText(item)}
                              className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                            >
                              <Eye className="h-4 w-4" />
                              {t.retrieve}
                            </button>
                          )}
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(item)}
                            className="inline-flex items-center gap-2 rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm font-semibold text-red-200"
                          >
                            <Trash2 className="h-4 w-4" />
                            {t.delete}
                          </button>
                        </div>
                      </div>
                    </article>
                  );
                })
              )}
            </div>

            {nextCursor ? (
              <button
                type="button"
                onClick={() => loadItems({ append: true, cursor: nextCursor })}
                disabled={loadingMore}
                className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl border app-surface px-4 py-2.5 text-sm font-semibold app-text disabled:opacity-50"
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
                <p className="mt-2 break-all text-xs app-text-soft">
                  {preview.item?.item_id}
                </p>
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
    </main>
  );
}
