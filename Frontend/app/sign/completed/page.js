"use client";

import { useEffect, useState } from "react";
import { useLanguage } from "@/components/language_provider";
import {
  completedEnvelopePageTranslations,
  resolveErrorMessage,
} from "@/lib/translations";
import { CheckCircle2, Download, FileCheck2, Loader2, ShieldCheck } from "lucide-react";

const TOKEN_SESSION_KEY = "redocx:completed-envelope-token";

function tokenFromFragment() {
  if (typeof window === "undefined") return "";
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  const fragmentToken = String(params.get("token") || "").trim();
  const token =
    fragmentToken || String(window.sessionStorage.getItem(TOKEN_SESSION_KEY) || "").trim();
  if (token) {
    window.sessionStorage.setItem(TOKEN_SESSION_KEY, token);
    window.history.replaceState(null, "", window.location.pathname);
  }
  return token;
}

async function jsonPayload(response) {
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error("COMPLETED_ENVELOPE_LOAD_FAILED");
    error.status = response.status;
    error.payload = data;
    error.code =
      data?.error?.code || data?.detail?.error || "COMPLETED_ENVELOPE_LOAD_FAILED";
    throw error;
  }
  return data;
}

function contentDispositionFilename(value, fallback) {
  const match = String(value || "").match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
}

export default function CompletedEnvelopePage() {
  const { language } = useLanguage();
  const t =
    completedEnvelopePageTranslations[language] ||
    completedEnvelopePageTranslations.en;
  const [token, setToken] = useState("");
  const [context, setContext] = useState(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const resolvedToken = tokenFromFragment();
    if (!resolvedToken) {
      setError(resolveErrorMessage("COMPLETION_LINK_INVALID", language));
      setLoading(false);
      return;
    }
    setToken(resolvedToken);
    const controller = new AbortController();
    void fetch("/api/sign/completed", {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
      headers: { "X-ReDOCX-Signing-Token": resolvedToken },
    })
      .then(jsonPayload)
      .then(setContext)
      .catch((caught) => {
        if (caught?.name !== "AbortError") {
          setError(resolveErrorMessage(caught, language, "COMPLETED_ENVELOPE_LOAD_FAILED"));
        }
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);

  async function downloadArtifact(artifact) {
    setError("");
    setDownloading(artifact);
    try {
      const response = await fetch(
        `/api/sign/completed?artifact=${encodeURIComponent(artifact)}`,
        {
          method: "GET",
          cache: "no-store",
          headers: { "X-ReDOCX-Signing-Token": token },
        },
      );
      if (!response.ok) await jsonPayload(response);
      const blobUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = contentDispositionFilename(
        response.headers.get("content-disposition"),
        artifact === "certificate" ? "certificate.pdf" : "signed-document.pdf",
      );
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(blobUrl);
    } catch (caught) {
      setError(resolveErrorMessage(caught, language, "COMPLETED_ENVELOPE_DOWNLOAD_FAILED"));
    } finally {
      setDownloading("");
    }
  }

  return (
    <main className="app-page flex min-h-screen items-center justify-center px-4 py-10 app-text">
      <section className="w-full max-w-2xl rounded-3xl border app-surface-strong p-7 md:p-10">
        <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] app-text-soft">
          <ShieldCheck className="h-4 w-4" /> {t.secureSign}
        </p>

        {loading ? (
          <p className="mt-8 inline-flex items-center gap-3 app-text-muted">
            <Loader2 className="h-5 w-5 animate-spin" /> {t.loading}
          </p>
        ) : null}

        {!loading && context ? (
          <>
            <CheckCircle2 className="mt-7 h-12 w-12 text-emerald-500" />
            <h1 className="mt-4 text-3xl font-semibold">{t.completeTitle}</h1>
            <p className="mt-3 app-text-muted">
              {context.document_filename} · {t.envelopeLabel} {context.envelope_id}
            </p>
            <p className="mt-2 text-sm app-text-muted">
              {t.downloadHelp}
            </p>
            <div className="mt-7 grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                disabled={Boolean(downloading)}
                onClick={() => downloadArtifact("signed_pdf")}
                className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-4 font-semibold text-[var(--app-button-text)] disabled:opacity-60"
              >
                {downloading === "signed_pdf" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Download className="h-4 w-4" />
                )}
                {t.downloadSignedPdf}
              </button>
              <button
                type="button"
                disabled={Boolean(downloading)}
                onClick={() => downloadArtifact("certificate")}
                className="inline-flex items-center justify-center gap-2 rounded-2xl border app-surface px-5 py-4 font-semibold disabled:opacity-60"
              >
                {downloading === "certificate" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FileCheck2 className="h-4 w-4" />
                )}
                {t.downloadCertificate}
              </button>
            </div>
          </>
        ) : null}

        {error ? (
          <p className="mt-7 rounded-2xl border border-red-500/30 bg-red-500/10 p-4 text-red-600 dark:text-red-300">
            {error}
          </p>
        ) : null}
      </section>
    </main>
  );
}
