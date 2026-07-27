"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, CheckCircle2, Download, Files, Loader2, UploadCloud, X } from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { normalizeAnalyzerArtifactUrl, postAnalyzerFeature } from "@/lib/api_client";
import { combinePdfPageTranslations } from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import ProcessedOutputActions from "@/components/processed_output_actions";
import { FILE_SECURITY_POLICY, partitionDuplicateBrowserUploads, validateBrowserUploads } from "@/lib/secure_upload_policy";

const FEATURE_PATH = "pdf/combine";
const MAX_PDF_SIZE_MB = 50;
const MAX_FILES = 10;

const copy = combinePdfPageTranslations;

function systemLanguageFor(language) { return language === "fr" ? "french" : "english"; }
function isPdf(file) { const type = String(file?.type || "").toLowerCase(); const name = String(file?.name || "").toLowerCase(); return type === "application/pdf" || type === "application/x-pdf" || name.endsWith(".pdf"); }
function fileSizeMb(file) { return file.size / (1024 * 1024); }
function getFileStem(filename = "") { const name = String(filename || ""); const lastDot = name.lastIndexOf("."); return (lastDot > 0 ? name.slice(0, lastDot) : name) || "document"; }
function normalizePdfFilename(value, fallback) { const raw = String(value || "").trim() || fallback; return raw.toLowerCase().endsWith(".pdf") ? raw : `${raw}.pdf`; }
function normalizeArtifactUrl(url) { return normalizeAnalyzerArtifactUrl(url); }
function AuthRequired({ t }) { return <section className="rounded-3xl border border-amber-400/30 bg-amber-400/10 p-6"><h2 className="text-lg font-semibold app-text">{t.signInTitle}</h2><p className="mt-2 text-sm app-text-muted">{t.signInDescription}</p><a href="/auth/login?returnTo=/pdf-tools/combine" className="mt-5 inline-flex rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)]">{t.signIn}</a></section>; }

export default function CombinePdfPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked } = useAccount();
  const t = useMemo(() => copy[language] || copy.en, [language]);
  const [files, setFiles] = useState([]);
  const outputFilename = files[0] ? `${getFileStem(files[0].name)}.combined.pdf` : "combined-document.pdf";
  const [preserveBookmarks, setPreserveBookmarks] = useState(true);
  const [preserveMetadata, setPreserveMetadata] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState(null);

  async function addFiles(fileList) {
    const incomingFiles = Array.from(fileList || []);
    const { message } = await validateBrowserUploads(incomingFiles, FILE_SECURITY_POLICY.pdfTool);
    if (message) {
      setError(message);
      return;
    }
    const submittedFiles = [...files, ...incomingFiles];
    const { acceptedFiles, duplicates } =
      await partitionDuplicateBrowserUploads(submittedFiles);
    setError(
      duplicates.length
        ? `${duplicates.length} duplicate PDF${duplicates.length === 1 ? " was" : "s were"} rejected. The remaining PDFs are ready.`
        : "",
    );
    setFiles(acceptedFiles.slice(0, MAX_FILES));
  }
  function validate() { if (files.length < 2) return t.noFiles; if (files.length > MAX_FILES) return t.tooMany; if (files.some((file) => !isPdf(file))) return t.invalidFile; if (files.some((file) => fileSizeMb(file) > MAX_PDF_SIZE_MB)) return t.tooLarge; return ""; }
  async function handleSubmit(event) { event.preventDefault(); setError(""); setResponse(null); const validationError = validate(); if (validationError) return setError(validationError); const formData = new FormData(); files.forEach((file) => formData.append("files", file)); formData.append("output_filename", normalizePdfFilename(outputFilename, "combined-document.pdf")); formData.append("preserve_bookmarks", String(preserveBookmarks)); formData.append("preserve_metadata", String(preserveMetadata)); formData.append("system_language", systemLanguageFor(language)); setBusy(true); try { setResponse(await postAnalyzerFeature(FEATURE_PATH, formData, true)); } catch (caught) { setError(caught?.message || t.failed); } finally { setBusy(false); } }
  const result = response?.result || null;
  const downloadUrl = normalizeArtifactUrl(result?.download_url || result?.pdf_artifact?.download_url);

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
      <main className="app-page min-h-screen px-4 py-6 app-text md:px-8"><button type="button" onClick={() => router.back()} className="mb-6 inline-flex items-center gap-2 text-sm app-text-muted"><ArrowLeft className="h-4 w-4" />{t.back}</button><section className="mb-8 rounded-3xl border app-surface-strong p-6"><p className="text-xs font-semibold uppercase tracking-[0.18em] app-text-soft">{t.badge}</p><h1 className="mt-3 text-3xl font-semibold app-text md:text-4xl">{t.title}</h1><p className="mt-3 max-w-3xl app-text-muted">{t.description}</p></section><form onSubmit={handleSubmit} className="grid gap-6 lg:grid-cols-[1fr_0.85fr]"><section className="rounded-3xl border app-surface-strong p-5"><h2 className="text-lg font-semibold app-text">{t.uploadTitle}</h2><p className="mt-1 text-sm app-text-muted">{t.uploadHelp}</p><label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-3xl border border-dashed app-surface p-8 text-center"><UploadCloud className="h-10 w-10 app-text-muted" /><span className="mt-3 text-sm font-semibold app-text">{t.chooseFiles}</span><input type="file" multiple accept="application/pdf,.pdf" className="hidden" onChange={(event) => addFiles(event.target.files)} /></label><div className="mt-4 space-y-2">{files.map((file, index) => <div key={`${file.name}-${index}`} className="flex items-center justify-between gap-3 rounded-2xl border app-surface px-4 py-3 text-sm"><span className="truncate app-text"><Files className="mr-2 inline h-4 w-4" />{index + 1}. {file.name}</span><button type="button" onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))} className="text-red-300"><X className="h-4 w-4" /></button></div>)}</div></section><section className="space-y-6"><div className="rounded-3xl border app-surface-strong p-5"><input value={outputFilename} readOnly className="w-full rounded-2xl border app-surface px-4 py-3 app-text" /><label className="mt-4 flex items-center gap-3 text-sm app-text"><input type="checkbox" checked={preserveBookmarks} onChange={(event) => setPreserveBookmarks(event.target.checked)} />{t.preserveBookmarks}</label><label className="mt-3 flex items-center gap-3 text-sm app-text"><input type="checkbox" checked={preserveMetadata} onChange={(event) => setPreserveMetadata(event.target.checked)} />{t.preserveMetadata}</label></div>{error ? <p className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-200"><AlertTriangle className="mr-2 inline h-4 w-4" />{error}</p> : null}<button type="submit" disabled={busy} className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-60">{busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Files className="h-4 w-4" />}{busy ? t.combining : t.combine}</button>{result ? <section className="rounded-3xl border border-emerald-400/30 bg-emerald-400/10 p-5"><h2 className="flex items-center gap-2 text-lg font-semibold app-text"><CheckCircle2 className="h-5 w-5" />{t.resultTitle}</h2>{downloadUrl ? <a href={downloadUrl} className="mt-4 inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"><Download className="h-4 w-4" />{t.download}</a> : null}<ProcessedOutputActions artifactUrl={downloadUrl} filename={outputFilename} mimeType="application/pdf" title="Combined PDF" /></section> : null}</section></form></main>
    </AppSidebarLayout>
  );
}
