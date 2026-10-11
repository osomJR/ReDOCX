"use client";

import TranscriptionSubtitlePlayer from "@/components/transcription_subtitle_player";

import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  XCircle,
} from "lucide-react";

const DEFAULT_LABELS = {
  succeeded: "succeeded",
  failed: "failed",
  plan: "plan",
  workers: "workers",
  processedSuccessfully: "Processed successfully.",
  fileFailed: "This file failed.",
  downloadReady: "Download ready",
  downloadOutput: "Download output",
  resultReady: "Result ready",
  convertedOutput: "Processed output",
  noOutput:
    "Processing succeeded, but the response contained neither a downloadable artifact nor displayable result content.",
  downloadableOutputs: "downloadable output",
  inlineResults: "inline result",
};

const ARTIFACT_PREFIXES = [
  "/api/analyzer/artifacts/",
  "api/analyzer/artifacts/",
  "/api/v1/analyzer/artifacts/",
  "api/v1/analyzer/artifacts/",
  "/artifacts/",
  "artifacts/",
];

const ARTIFACT_OBJECT_KEYS = [
  "artifact",
  "output_artifact",
  "outputArtifact",
  "pdf_artifact",
  "pdfArtifact",
  "download_artifact",
  "downloadArtifact",
  "result_artifact",
  "resultArtifact",
];

const ARTIFACT_ARRAY_KEYS = [
  "artifacts",
  "output_artifacts",
  "outputArtifacts",
  "downloads",
  "downloadables",
  "outputs",
  "files",
];

const CONTENT_KEYS = [
  "content",
  "text",
  "summary",
  "explanation",
  "corrected_text",
  "correctedText",
  "translated_text",
  "translatedText",
  "transcript_text",
  "transcriptText",
  "transcription",
  "generated_questions_text",
  "generatedQuestionsText",
  "questions_text",
  "questionsText",
  "generated_answers_text",
  "generatedAnswersText",
  "answers_text",
  "answersText",
  "answer_text",
  "answerText",
];

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function cleanArtifactStorageKey(value = "") {
  let key = String(value || "")
    .trim()
    .replaceAll("\\", "/");

  if (!key) return "";

  if (/^https?:\/\//i.test(key)) {
    try {
      key = new URL(key).pathname;
    } catch {
      return "";
    }
  }

  let changed = true;
  while (changed) {
    changed = false;
    for (const prefix of ARTIFACT_PREFIXES) {
      if (key.startsWith(prefix)) {
        key = key.slice(prefix.length);
        changed = true;
      }
    }
  }

  return key
    .split(/[?#]/, 1)[0]
    .replace(/^\/+/, "")
    .trim();
}

function buildArtifactDownloadUrl(storageKey) {
  const cleanStorageKey = cleanArtifactStorageKey(storageKey);
  return cleanStorageKey
    ? `/api/analyzer/artifacts/${cleanStorageKey}`
    : "";
}

function normalizeArtifactUrl(value = "") {
  const raw = String(value || "").trim();
  if (!raw) return "";

  if (/^https?:\/\//i.test(raw) || /^blob:/i.test(raw)) return raw;

  if (raw.startsWith("//")) return "";

  if (ARTIFACT_PREFIXES.some((prefix) => raw.startsWith(prefix))) {
    try {
      const parsed = new URL(raw, "https://redocx.invalid");
      const normalizedPath = buildArtifactDownloadUrl(parsed.pathname);
      return normalizedPath
        ? `${normalizedPath}${parsed.search}${parsed.hash}`
        : "";
    } catch {
      return buildArtifactDownloadUrl(raw);
    }
  }

  // Preserve legitimate application-relative routes. Plain values remain
  // compatible with backend storage keys.
  return raw.startsWith("/") ? raw : buildArtifactDownloadUrl(raw);
}

function formatDurationMs(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(
    milliseconds < 10_000 ? 1 : 0,
  )} s`;
}

function getBatchPayload(result) {
  if (Array.isArray(result)) {
    return { items: result, batch: {}, success: undefined };
  }

  const candidates = [
    result,
    result?.batch_result,
    result?.batchResult,
    result?.data?.batch_result,
    result?.data?.batchResult,
    result?.data,
    result?.result,
  ].filter(isObject);

  const payload =
    candidates.find((candidate) => Array.isArray(candidate?.items)) || null;

  if (!payload) return null;

  return {
    ...payload,
    batch:
      payload.batch ||
      payload.summary ||
      result?.batch ||
      result?.summary ||
      {},
    success:
      typeof payload.success === "boolean"
        ? payload.success
        : typeof result?.success === "boolean"
          ? result.success
          : undefined,
  };
}

function getResponseRoot(item) {
  if (!isObject(item)) return item;
  return (
    item.response ??
    item.analyzer_response ??
    item.analyzerResponse ??
    item.data ??
    item.result ??
    item.output ??
    item
  );
}

function collectResponseCandidates(response) {
  const candidates = [];
  const seen = new Set();

  function add(value, depth = 0) {
    if (value == null || depth > 4) return;

    if (typeof value === "string") {
      candidates.push(value);
      return;
    }

    if (!isObject(value) || seen.has(value)) return;
    seen.add(value);
    candidates.push(value);

    for (const key of [
      "analyzer_response",
      "analyzerResponse",
      "data",
      "result",
      ...ARTIFACT_OBJECT_KEYS,
    ]) {
      add(value[key], depth + 1);
    }

    for (const key of ARTIFACT_ARRAY_KEYS) {
      if (Array.isArray(value[key])) {
        for (const entry of value[key]) add(entry, depth + 1);
      }
    }
  }

  add(response);
  return candidates;
}

function contentPreviewFromResponse(response) {
  if (typeof response === "string" && response.trim()) return response.trim();

  const candidates = collectResponseCandidates(response);
  return pickFirstString(
    candidates.flatMap((candidate) => {
      if (!isObject(candidate)) return [];
      return CONTENT_KEYS.map((key) => candidate[key]);
    }),
  );
}


function downloadFilenameFromUrl(url = "") {
  try {
    return new URL(String(url || ""), "https://redocx.invalid").searchParams.get(
      "download_name",
    ) || "";
  } catch {
    return "";
  }
}

function filenameFromCandidate(candidate, fallback = "") {
  if (!isObject(candidate)) return fallback;
  return (
    pickFirstString([
      candidate.filename,
      candidate.file_name,
      candidate.fileName,
      candidate.original_filename,
      candidate.originalFilename,
      candidate.original_artifact_name,
      candidate.originalArtifactName,
      candidate.artifact_name,
      candidate.artifactName,
      candidate.output_filename,
      candidate.outputFilename,
      candidate.name,
    ]) || fallback
  );
}

function downloadEntriesFromResponse(response, fallbackFilename = "") {
  const candidates = collectResponseCandidates(response);
  const downloads = [];
  const seenUrls = new Set();

  const sharedFilename = pickFirstString(
    candidates.map((candidate) =>
      isObject(candidate) ? filenameFromCandidate(candidate) : "",
    ),
  );

  for (const candidate of candidates) {
    if (!isObject(candidate)) continue;

    const storageKey = pickFirstString([
      candidate.storage_key,
      candidate.storageKey,
      candidate.key,
    ]);

    const returnedUrl = pickFirstString([
      candidate.download_url,
      candidate.downloadUrl,
      candidate.url,
      candidate.href,
    ]);

    const downloadUrl = normalizeArtifactUrl(
      returnedUrl || (storageKey ? buildArtifactDownloadUrl(storageKey) : ""),
    );

    if (!downloadUrl || seenUrls.has(downloadUrl)) continue;
    seenUrls.add(downloadUrl);

    downloads.push({
      downloadUrl,
      filename:
        downloadFilenameFromUrl(downloadUrl) ||
        filenameFromCandidate(candidate) ||
        sharedFilename ||
        fallbackFilename ||
        "",
    });
  }

  return downloads;
}

function getItemError(item, labels) {
  const error = item?.error;
  return (
    pickFirstString([
      error?.message,
      error?.detail?.message,
      error?.detail,
      error?.error,
      item?.message,
    ]) || labels.fileFailed
  );
}

function pluralize(count, singular) {
  return `${singular}${count === 1 ? "" : "s"}`;
}

function getBatchItems(payload) {
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  return rawItems.map((rawItem, arrayIndex) => {
    const item = isObject(rawItem) ? rawItem : { response: rawItem };
    const response = getResponseRoot(item);
    const index = item.index ?? item.position ?? arrayIndex + 1;
    const itemLabel =
      pickFirstString([
        item.filename,
        item.file_name,
        item.fileName,
        item.original_filename,
        item.originalFilename,
        item.name,
      ]) || `File ${index}`;
    const success =
      typeof item.success === "boolean"
        ? item.success
        : !item.error && item.status !== "failed";
    const preview = success ? contentPreviewFromResponse(response) : "";
    const downloads = success
      ? downloadEntriesFromResponse(response, itemLabel)
      : [];

    return {
      raw: item,
      response,
      index,
      itemLabel,
      success,
      preview,
      downloads,
    };
  });
}

// The Lock page uses the same artifact normalization as the rendered rows.
// Count actual per-file results so a malformed aggregate cannot imply success.
export function getBatchResultSummary(result) {
  const items = getBatchItems(getBatchPayload(result));
  const succeeded = items.filter((item) => item.success).length;
  const ready = items.filter((item) => item.success && item.downloads.length > 0).length;
  return {
    total: items.length,
    succeeded,
    failed: items.length - succeeded,
    ready,
    missing: succeeded - ready,
  };
}

export default function BatchResultPanel({
  result,
  title = "Batch results",
  embedded = false,
  compact = false,
  description = "",
  headingId,
  className = "",
  showBatchMetadata = true,
  labels: labelOverrides,
  transcriptionPlaybackTitle = "Synchronized media playback",
  transcriptionSubtitleLabel = "Timed subtitles",
}) {
  const labels = { ...DEFAULT_LABELS, ...(labelOverrides || {}) };
  const payload = getBatchPayload(result);
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];

  if (!payload || !rawItems.length) return null;

  const items = getBatchItems(payload);

  const succeeded =
    payload.batch?.succeeded ??
    payload.batch?.success_count ??
    items.filter((item) => item.success).length;
  const failed =
    payload.batch?.failed ??
    payload.batch?.failure_count ??
    items.length - succeeded;
  const overallSuccess =
    typeof payload.success === "boolean" ? payload.success : failed === 0;
  const downloadableItemCount = items.filter(
    (item) => item.downloads.length > 0,
  ).length;
  const inlineResultCount = items.filter(
    (item) => item.preview && item.downloads.length === 0,
  ).length;

  const summaryParts = [
    `${succeeded} ${labels.succeeded}`,
    `${failed} ${labels.failed}`,
    // Operational batch metadata is hidden for Compress PDF only. Other callers
    // preserve their current display by relying on showBatchMetadata=true.
    ...(showBatchMetadata
      ? [
          payload.batch?.plan
            ? `${payload.batch.plan} ${labels.plan}`
            : "",
          payload.batch?.extension || "",
          payload.batch?.processing_mode || payload.batch?.processingMode || "",
          payload.batch?.concurrency != null
            ? `${payload.batch.concurrency} ${labels.workers}`
            : "",
        ]
      : []),
    payload.batch?.elapsed_ms != null
      ? formatDurationMs(payload.batch.elapsed_ms)
      : payload.batch?.elapsedMs != null
        ? formatDurationMs(payload.batch.elapsedMs)
        : "",
  ].filter(Boolean);

  const outputParts = [
    downloadableItemCount
      ? `${downloadableItemCount} ${pluralize(
          downloadableItemCount,
          labels.downloadableOutputs,
        )}`
      : "",
    inlineResultCount
      ? `${inlineResultCount} ${pluralize(
          inlineResultCount,
          labels.inlineResults,
        )}`
      : "",
  ].filter(Boolean);

  // Opt-in PDF Lock presentation. Existing feature pages retain the full
  // player, preview, metadata and artifact-card presentation below.
  if (compact) {
    const summary = getBatchResultSummary(result);
    const hasIssues = summary.failed > 0 || summary.missing > 0;
    const elapsed = formatDurationMs(payload.batch?.elapsed_ms ?? payload.batch?.elapsedMs);
    return (
      <section className={[embedded ? "min-w-0" : "min-w-0 rounded-3xl border app-surface-strong p-5", className].filter(Boolean).join(" ")}>
        <div className="flex items-start gap-3">
          {hasIssues ? (
            <AlertTriangle className="mt-1 h-6 w-6 shrink-0 text-amber-500" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="mt-1 h-6 w-6 shrink-0 text-emerald-500" aria-hidden="true" />
          )}
          <div className="min-w-0">
            <h2 id={headingId} className="break-words text-xl font-semibold app-text">{title}</h2>
            {description ? <p className="mt-1 text-sm app-text-muted">{description}</p> : null}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs app-text-muted">
          <span className="rounded-lg border app-surface px-3 py-1.5">{summary.succeeded} {labels.succeeded}</span>
          <span className="rounded-lg border app-surface px-3 py-1.5">{summary.failed} {labels.failed}</span>
          {summary.missing > 0 ? <span className="rounded-lg border border-amber-400/30 px-3 py-1.5">{summary.missing} {labels.downloadUnavailable || labels.noOutput}</span> : null}
          {elapsed ? <span className="px-1">{elapsed}</span> : null}
        </div>
        <div
          className="mt-4 max-h-[32rem] overflow-y-auto rounded-2xl border app-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--app-accent-border)]"
          role="region"
          aria-label={labels.outputsList || title}
          tabIndex={0}
        >
          <ol className="divide-y divide-[var(--app-border)]">
            {items.map((item, arrayIndex) => (
              <li key={`${item.index}-${item.itemLabel}-${arrayIndex}`} className="flex min-w-0 flex-col gap-3 p-4 sm:flex-row sm:items-center">
                <div className="flex min-w-0 flex-1 items-start gap-3">
                  {item.success && item.downloads.length > 0 ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" aria-hidden="true" />
                  ) : item.success ? (
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden="true" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" aria-hidden="true" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="break-all text-sm font-medium app-text">{item.index}. {item.itemLabel}</p>
                    {!item.success ? (
                      <p className="mt-1 break-words text-xs app-text-muted">{getItemError(item.raw, labels)}</p>
                    ) : item.downloads.length === 0 ? (
                      <p className="mt-1 break-words text-xs app-text-muted">{labels.noOutput}</p>
                    ) : (
                      <p className="mt-1 text-xs app-text-soft">{labels.processedSuccessfully}</p>
                    )}
                  </div>
                </div>
                {item.downloads.length > 0 ? (
                  <div className="flex shrink-0 flex-col gap-2 sm:max-w-[50%]">
                    {item.downloads.map((download, downloadIndex) => (
                      <a
                        key={`${download.downloadUrl}-${downloadIndex}`}
                        href={download.downloadUrl}
                        download={download.filename || undefined}
                        target="_blank"
                        rel="noopener noreferrer"
                        aria-label={`${labels.downloadOutput}: ${download.filename || item.itemLabel}`}
                        title={download.filename || item.itemLabel}
                        className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--app-accent-border)] sm:w-auto"
                      >
                        <Download className="h-4 w-4 shrink-0" aria-hidden="true" />
                        <span>{labels.compactDownload || labels.downloadOutput}</span>
                      </a>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ol>
        </div>
      </section>
    );
  }

  return (
    <section
      className={[
        embedded
          ? "min-w-0"
          : "rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-5",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      aria-live="polite"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-lg font-semibold app-text">
            {overallSuccess ? (
              <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-300" />
            ) : (
              <XCircle className="h-5 w-5 shrink-0 text-red-300" />
            )}
            <span className="break-words">{title}</span>
          </h2>

          <p className="mt-1 text-sm app-text-muted">
            {summaryParts.join(" · ")}
          </p>

          {outputParts.length ? (
            <p className="mt-1 text-xs app-text-soft">
              {outputParts.join(" · ")}
            </p>
          ) : null}
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {items.map((item, arrayIndex) => {
          const elapsed =
            item.raw?.elapsed_ms ?? item.raw?.elapsedMs ?? undefined;

          return (
            <article
              key={`${item.index}-${item.itemLabel}-${arrayIndex}`}
              className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-sm"
            >
              <div className="min-w-0">
                <p className="break-words font-semibold app-text">
                  {item.index}. {item.itemLabel}
                </p>

                <p
                  className={
                    item.success ? "mt-1 app-text-muted" : "mt-1 text-red-300"
                  }
                >
                  {item.success
                    ? labels.processedSuccessfully
                    : getItemError(item.raw, labels)}
                  {elapsed != null ? ` · ${formatDurationMs(elapsed)}` : ""}
                </p>

                {item.success ? (
                  <TranscriptionSubtitlePlayer
                    responseData={item.response}
                    title={transcriptionPlaybackTitle}
                    subtitleLabel={transcriptionSubtitleLabel}
                    className="mt-3"
                  />
                ) : null}

                {item.preview ? (
                  <div className="mt-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-3">
                    <p className="mb-2 flex items-center gap-2 text-xs font-semibold app-text-muted">
                      <FileText className="h-4 w-4 shrink-0" />
                      {labels.resultReady}
                    </p>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-5 app-text">
                      {item.preview}
                    </pre>
                  </div>
                ) : null}

                {item.downloads.length ? (
                  <div className="mt-3 space-y-2">
                    {item.downloads.map((download, downloadIndex) => (
                      <div
                        key={`${download.downloadUrl}-${downloadIndex}`}
                        className="rounded-2xl border border-emerald-400/25 bg-emerald-400/10 p-3"
                      >
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                          <div className="min-w-0">
                            <p className="flex items-center gap-2 font-medium text-emerald-100">
                              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-300" />
                              {labels.downloadReady}
                            </p>
                            <p className="mt-1 break-all text-xs text-emerald-100/80">
                              {download.filename || labels.convertedOutput}
                            </p>
                          </div>

                          <a
                            href={download.downloadUrl}
                            download={download.filename || undefined}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-label={`${labels.downloadOutput}: ${
                              download.filename || item.itemLabel
                            }`}
                            className="inline-flex w-full shrink-0 items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] shadow-lg transition hover:scale-[1.02] hover:shadow-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--app-accent-border)] sm:w-auto"
                          >
                            <Download className="h-4 w-4" />
                            {labels.downloadOutput}
                          </a>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : item.success && !item.preview ? (
                  <div className="mt-3 flex items-start gap-2 rounded-xl border border-amber-400/25 bg-amber-400/10 p-3 text-amber-100">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <p className="text-xs leading-5">{labels.noOutput}</p>
                  </div>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
