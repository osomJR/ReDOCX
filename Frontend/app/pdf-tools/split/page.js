"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, CheckCircle2, Download, FileStack, Loader2, UploadCloud } from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { normalizeAnalyzerArtifactUrl, postAnalyzerFeature } from "@/lib/api_client";
import { splitPdfPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import { FILE_SECURITY_POLICY, validateBrowserUpload } from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/split";
const MAX_PDF_SIZE_MB = 50;

const copy = splitPdfPageTranslations;
function systemLanguageFor(language) { return language === "fr" ? "french" : "english"; }
function isPdf(file) { const type = String(file?.type || "").toLowerCase(); const name = String(file?.name || "").toLowerCase(); return type === "application/pdf" || type === "application/x-pdf" || name.endsWith(".pdf"); }
function fileSizeMb(file) { return file.size / (1024 * 1024); }
function getFileStem(filename = "") { const name = String(filename || ""); const lastDot = name.lastIndexOf("."); return (lastDot > 0 ? name.slice(0, lastDot) : name) || "document"; }
function normalizeArtifactUrl(url) { return normalizeAnalyzerArtifactUrl(url); }
function downloadFilenameFromUrl(url = "") {
  try {
    return new URL(
      String(url || ""),
      typeof window !== "undefined" ? window.location.origin : "http://local",
    ).searchParams.get("download_name") || "";
  } catch {
    return "";
  }
}
function hasValidSelectedPages(value) {
  const tokens = String(value || "").split(",").map((item) => item.trim());
  if (!tokens.length || tokens.some((item) => !/^[1-9][0-9]*$/.test(item))) return false;
  return new Set(tokens.map(Number)).size === tokens.length;
}
function hasValidPageRanges(value) {
  const tokens = String(value || "").split(",").map((item) => item.trim());
  if (!tokens.length || tokens.some((item) => !item)) return false;
  return tokens.every((item) => {
    const match = item.match(/^([1-9][0-9]*)(?:\s*-\s*([1-9][0-9]*))?$/);
    if (!match) return false;
    return Number(match[2] || match[1]) >= Number(match[1]);
  });
}
function AuthRequired({ t }) { return <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6"><h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2><p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p><a href="/auth/login?returnTo=/pdf-tools/split" className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]">{t.signIn}</a></section>; }

export default function SplitPdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const [file, setFile] = useState(null);
  const [mode, setMode] = useState("every_page");
  const [selectedPages, setSelectedPages] = useState("");
  const [pageRanges, setPageRanges] = useState("");
  const [outputBasename, setOutputBasename] = useState("split-document");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);
  function validate() {
    if (!file) return t.noFile;
    if (!isPdf(file)) return t.invalidFile;
    if (fileSizeMb(file) > MAX_PDF_SIZE_MB) return t.tooLarge;
    if (mode === "extract_selected_pages" && !hasValidSelectedPages(selectedPages)) return t.badSelected;
    if (mode === "page_ranges" && !hasValidPageRanges(pageRanges)) return t.badRanges;
    return "";
  }
  async function handlePickedPdfFile(file) {
    if (!file) {
      setFile(null);
      return;
    }

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.pdfTool);
    if (securityError) {
      setFile(null);
      setError(securityError);
      return;
    }

    setError("");
    setFile(file);
    setOutputBasename(`${getFileStem(file.name)}.split`);
  }
  async function handleSubmit(event) { event.preventDefault(); setError(""); setResponse(null); const validationError = validate(); if (validationError) return setError(validationError); const formData = new FormData(); formData.append("file", file); formData.append("mode", mode); if (mode === "extract_selected_pages") formData.append("selected_pages", selectedPages.trim()); if (mode === "page_ranges") formData.append("page_ranges", pageRanges.trim()); formData.append("output_basename", outputBasename.trim() || "split-document"); formData.append("system_language", systemLanguageFor(language)); setBusy(true); try { setResponse(await postAnalyzerFeature(FEATURE_PATH, formData, true)); } catch (caught) { setError(caught?.message || t.failed); } finally { setBusy(false); } }
  const result = response?.result || null;
  const archiveUrl = normalizeArtifactUrl(result?.archive_file?.download_url);
  const outputFiles = Array.isArray(result?.output_files) ? result.output_files : [];
  const archiveFilename =
    result?.archive_file?.filename ||
    downloadFilenameFromUrl(archiveUrl) ||
    result?.archive_file?.file_name ||
    `${outputBasename}.zip`;
  const processedArtifacts = [
    ...outputFiles
      .map((item) => ({
        url: normalizeArtifactUrl(item.download_url),
        filename:
          item.filename ||
          downloadFilenameFromUrl(normalizeArtifactUrl(item.download_url)) ||
          item.file_name,
        mimeType: "application/pdf",
      }))
      .filter((item) => item.url),
    ...(archiveUrl
      ? [{
          url: archiveUrl,
          filename: archiveFilename,
          mimeType: "application/zip",
        }]
      : []),
  ];
  if (!authChecked) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6 app-text">{t.loading}</main>
    </AppSidebarLayout>
  );
  if (!user) return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen p-6"><AuthRequired t={t} /></main>
    </AppSidebarLayout>
  );
  return (
    <AppSidebarLayout>
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8"><button type="button" onClick={() => router.back()} className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"><ArrowLeft className="h-4 w-4" />{t.back}</button><section className="mb-8 rounded-3xl border app-surface-strong p-6"><p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">{t.badge}</p><h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">{t.title}</h1><p className="mt-3 max-w-3xl app-text-muted">{t.description}</p></section><form onSubmit={handleSubmit} className="grid gap-6 lg:grid-cols-[1fr_0.85fr]"><section className="rounded-3xl border app-surface-strong p-5"><h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2><p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p><label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center"><UploadCloud className="h-10 w-10 app-text-muted" /><span className="mt-3 text-sm font-semibold app-text">{file?.name || t.chooseFile}</span><input type="file" accept="application/pdf,.pdf" className="hidden" onChange={(event) => handlePickedPdfFile(event.target.files?.[0] || null)} /></label></section><section className="space-y-6"><div className="rounded-3xl border app-surface-strong p-5"><label className="block text-sm font-medium app-text">{t.mode}<select value={mode} onChange={(event) => setMode(event.target.value)} className="mt-2 w-full rounded-2xl border app-surface px-4 py-3 app-text"><option value="every_page">{t.everyPage}</option><option value="extract_selected_pages">{t.selectedPages}</option><option value="page_ranges">{t.pageRanges}</option></select></label>{mode === "extract_selected_pages" ? <input value={selectedPages} onChange={(event) => setSelectedPages(event.target.value)} placeholder={t.selectedPagesInput} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}{mode === "page_ranges" ? <input value={pageRanges} onChange={(event) => setPageRanges(event.target.value)} placeholder={t.pageRangesInput} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /> : null}<input value={outputBasename} readOnly placeholder={t.outputBasename} className="mt-4 w-full rounded-2xl border app-surface px-4 py-3 app-text" /></div>{error ? <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"><AlertTriangle className="mr-2 inline h-4 w-4" />{error}</p> : null}<button type="submit" disabled={busy} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileStack className="h-4 w-4" />}{busy ? t.splitting : t.split}</button>{result ? <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5"><h2 className="flex items-center gap-2 text-lg font-semibold app-text"><CheckCircle2 className="h-5 w-5" />{t.resultTitle}</h2><div className="mt-4 flex flex-col gap-2">{archiveUrl ? <a href={archiveUrl} className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"><Download className="h-4 w-4" />{t.downloadArchive}</a> : null}{outputFiles.map((item, index) => { const url = normalizeArtifactUrl(item.download_url); return url ? <a key={item.file_name || index} href={url} className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text">{t.downloadFile} {index + 1}</a> : null; })}</div><ProcessedOutputActions artifacts={processedArtifacts} title="Split PDF output" /></section> : null}</section></form></main>
    </AppSidebarLayout>
  );
}
