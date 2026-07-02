"use client";

import { CheckCircle2, Download, XCircle } from "lucide-react";

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function normalizeArtifactUrl(url = "") {
  if (!url) return "";
  const raw = String(url);
  if (/^https?:\/\//i.test(raw)) return raw;
  return raw.replace(/^\/api\/v1\/analyzer\/artifacts\//, "/api/analyzer/artifacts/");
}

function formatDurationMs(value) {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

function contentPreviewFromResponse(response) {
  const result = response?.result || response?.analyzer_response?.result || response;
  const content = result?.content || response?.generated_questions_text || response?.questions_text;
  return typeof content === "string" && content.trim() ? content.trim() : "";
}

function downloadInfoFromResponse(response) {
  const result = response?.result || response?.analyzer_response?.result || response;
  const artifact = response?.artifact || response?.output_artifact || {};
  const storageKey = pickFirstString([
    result?.storage_key,
    result?.storageKey,
    artifact?.storage_key,
    artifact?.storageKey,
  ]);
  const downloadUrl = normalizeArtifactUrl(
    pickFirstString([
      result?.download_url,
      result?.downloadUrl,
      artifact?.download_url,
      artifact?.downloadUrl,
    ]) || (storageKey ? `/api/analyzer/artifacts/${storageKey}` : ""),
  );
  const filename = pickFirstString([
    result?.filename,
    artifact?.original_artifact_name,
    artifact?.artifact_name,
    artifact?.filename,
  ]);
  return { downloadUrl, filename };
}

export default function BatchResultPanel({ result, title = "Batch results" }) {
  const items = Array.isArray(result?.items) ? result.items : [];
  if (!result || !items.length) return null;

  const summary = result.batch || {};
  return (
    <section className="rounded-3xl border app-surface-strong p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
            {result.success ? <CheckCircle2 className="h-5 w-5" /> : <XCircle className="h-5 w-5" />}
            {title}
          </h2>
          <p className="mt-1 text-sm app-text-muted">
            {summary.succeeded ?? 0} succeeded, {summary.failed ?? 0} failed
            {summary.plan ? ` · ${summary.plan} plan` : ""}
            {summary.extension ? ` · ${summary.extension}` : ""}
            {summary.processing_mode ? ` · ${summary.processing_mode}` : ""}
            {summary.concurrency ? ` · ${summary.concurrency} workers` : ""}
            {summary.elapsed_ms ? ` · ${formatDurationMs(summary.elapsed_ms)}` : ""}
          </p>
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {items.map((item) => {
          const download = item.success ? downloadInfoFromResponse(item.response) : null;
          const message = item.error?.message || item.error?.error || "This file failed.";
          const preview = item.success ? contentPreviewFromResponse(item.response) : "";

          return (
            <div
              key={`${item.index}-${item.filename}`}
              className="rounded-2xl border app-surface p-4 text-sm"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-semibold app-text">
                    {item.index}. {item.filename}
                  </p>
                  <p className={item.success ? "app-text-muted" : "text-red-300"}>
                    {item.success ? "Processed successfully." : message}
                    {item.elapsed_ms ? ` · ${formatDurationMs(item.elapsed_ms)}` : ""}
                  </p>
                  {preview ? (
                    <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded-xl border border-[var(--app-border)] app-surface-strong p-3 text-xs leading-5 app-text">
                      {preview}
                    </pre>
                  ) : null}
                </div>

                {download?.downloadUrl ? (
                  <a
                    href={download.downloadUrl}
                    download={download.filename || undefined}
                    className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)]"
                  >
                    <Download className="h-4 w-4" />
                    Download
                  </a>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
