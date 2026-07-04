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
  Mic,
} from "lucide-react";
import {
  commonTranslations,
  transcribePageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import { postAnalyzerBatchFeature } from "@/lib/api_client";
import BatchResultPanel from "@/components/batch_result_panel";
import { FILE_SECURITY_POLICY, validateBrowserUpload, validateBrowserBatchUploads, getBatchUploadLimit } from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".mp3", ".mp4", ".mkv", ".mov"];
const AUDIO_EXTENSIONS = [".mp3"];
const VIDEO_EXTENSIONS = [".mp4", ".mov", ".mkv"];

const OUTPUT_EXTENSION = ".txt";
const MAX_AUDIO_FILE_SIZE_MB = 10;
const MAX_VIDEO_FILE_SIZE_MB = 25;
const MAX_AUDIO_DURATION_SECONDS = 120;
const MAX_VIDEO_DURATION_SECONDS = 180;

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


function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—";
  const wholeSeconds = Math.round(seconds);
  const minutes = Math.floor(wholeSeconds / 60);
  const remainingSeconds = wholeSeconds % 60;
  return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
}

function replaceVars(template, vars = {}) {
  return template.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
}

function getMediaType(extension = "") {
  if (AUDIO_EXTENSIONS.includes(extension)) return "audio";
  if (VIDEO_EXTENSIONS.includes(extension)) return "video";
  return "unknown";
}

function getMaxFileSizeMb(mediaType) {
  return mediaType === "audio"
    ? MAX_AUDIO_FILE_SIZE_MB
    : MAX_VIDEO_FILE_SIZE_MB;
}

function getMaxDurationSeconds(mediaType) {
  return mediaType === "audio"
    ? MAX_AUDIO_DURATION_SECONDS
    : MAX_VIDEO_DURATION_SECONDS;
}

function getMediaTypeLabel(extension, t) {
  const mediaType = getMediaType(extension);
  if (mediaType === "audio") return t.audioType;
  if (mediaType === "video") return t.videoType;
  return t.unknownType;
}

function pickFirstString(values = []) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function extractResponseMessage(responseData, fallbackMessage = "") {
  const detail = responseData?.detail;

  if (typeof detail === "string" && detail.trim()) return detail;
  if (typeof detail?.message === "string" && detail.message.trim()) {
    return detail.message.trim();
  }
  if (typeof detail?.error === "string" && detail.error.trim()) {
    return detail.error.trim();
  }
  if (
    typeof responseData?.message === "string" &&
    responseData.message.trim()
  ) {
    return responseData.message.trim();
  }
  if (typeof responseData?.error === "string" && responseData.error.trim()) {
    return responseData.error.trim();
  }

  try {
    return JSON.stringify(detail || responseData);
  } catch {
    return fallbackMessage;
  }
}

function extractTranscriptText(responseData) {
  const candidates = [
    responseData?.result,
    responseData?.data,
    responseData,
  ].filter(Boolean);

  for (const candidate of candidates) {
    const directText = pickFirstString([
      candidate?.content,
      candidate?.transcript_text,
      candidate?.transcriptText,
      candidate?.transcript,
      candidate?.text,
    ]);

    if (directText) {
      return directText;
    }

    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }

  return "";
}

function cleanArtifactStorageKey(value) {
  if (typeof value !== "string" || !value.trim()) return "";

  let key = value.trim().replaceAll("\\", "/");

  try {
    const parsedUrl = new URL(
      key,
      typeof window !== "undefined" ? window.location.origin : "http://local",
    );

    key = parsedUrl.pathname;
  } catch {
    // Keep key as-is when it is not URL-shaped.
  }

  const prefixes = [
    "/api/analyzer/artifacts/",
    "api/analyzer/artifacts/",
    "/api/v1/analyzer/artifacts/",
    "api/v1/analyzer/artifacts/",
    "/artifacts/",
    "artifacts/",
  ];

  let changed = true;

  while (changed) {
    changed = false;

    for (const prefix of prefixes) {
      if (key.startsWith(prefix)) {
        key = key.slice(prefix.length);
        changed = true;
      }
    }
  }

  return key.replace(/^\/+/, "");
}

function buildArtifactDownloadUrl(storageKey) {
  const cleanStorageKey = cleanArtifactStorageKey(storageKey);
  if (!cleanStorageKey) return "";

  return `/api/analyzer/artifacts/${cleanStorageKey}`;
}

function normalizeArtifactDownloadUrl(url = "") {
  const raw = String(url || "").trim();
  if (!raw) return "";

  if (/^https?:\/\//i.test(raw)) {
    return raw;
  }

  const storageKey = cleanArtifactStorageKey(raw);
  return storageKey ? buildArtifactDownloadUrl(storageKey) : "";
}

function extractTranscriptPdfArtifact(responseData) {
  const result = responseData?.result || responseData?.data?.result || null;
  const artifact = result?.pdf_artifact || result?.pdfArtifact || null;

  if (!artifact) return null;

  const downloadUrl =
    normalizeArtifactDownloadUrl(artifact.download_url || artifact.downloadUrl) ||
    buildArtifactDownloadUrl(artifact.storage_key || artifact.storageKey);

  if (!downloadUrl) return null;

  return {
    filename: artifact.filename || "transcript.pdf",
    downloadUrl,
  };
}

function readMediaDuration(file, mediaType) {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file);
    const element =
      mediaType === "audio"
        ? document.createElement("audio")
        : document.createElement("video");

    let settled = false;

    const cleanup = () => {
      element.removeAttribute("src");
      element.load();
      URL.revokeObjectURL(objectUrl);
    };

    element.preload = "metadata";

    element.onloadedmetadata = () => {
      if (settled) return;
      settled = true;

      const duration = Number(element.duration);
      cleanup();

      if (!Number.isFinite(duration) || duration <= 0) {
        reject(new Error("Could not read media duration."));
        return;
      }

      resolve(duration);
    };

    element.onerror = () => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new Error("Could not read media duration."));
    };

    element.src = objectUrl;
  });
}

async function validatePickedFile(file, t) {
  const extension = getFileExtension(file.name);

  if (!ACCEPTED_EXTENSIONS.includes(extension)) {
    throw new Error(
      replaceVars(t.unsupportedFileType, {
        ext: extension || "unknown",
      }),
    );
  }

  const mediaType = getMediaType(extension);
  if (mediaType === "unknown") {
    throw new Error(
      replaceVars(t.unsupportedFileType, {
        ext: extension || "unknown",
      }),
    );
  }

  const maxSizeMb = getMaxFileSizeMb(mediaType);
  if (file.size > maxSizeMb * 1024 * 1024) {
    throw new Error(
      replaceVars(t.fileTooLarge, {
        maxSize: maxSizeMb,
      }),
    );
  }

  let durationSeconds;
  try {
    durationSeconds = await readMediaDuration(file, mediaType);
  } catch {
    throw new Error(t.couldNotReadDuration);
  }

  const maxDurationSeconds = getMaxDurationSeconds(mediaType);
  if (durationSeconds > maxDurationSeconds) {
    throw new Error(
      replaceVars(t.mediaTooLong, {
        maxDuration: formatDuration(maxDurationSeconds),
      }),
    );
  }

  return {
    mediaType,
    durationSeconds,
    maxSizeMb,
    maxDurationSeconds,
  };
}

export default function TranscribePage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);

  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    transcribePageTranslations[language] || transcribePageTranslations.en;

  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [selectedFileMeta, setSelectedFileMeta] = useState(null);
  const [selectedFileMetas, setSelectedFileMetas] = useState([]);
  const [error, setError] = useState("");
  const [isCheckingFile, setIsCheckingFile] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [batchResult, setBatchResult] = useState(null);
  const [transcriptResult, setTranscriptResult] = useState("");
  const [transcriptPdfArtifact, setTranscriptPdfArtifact] = useState(null);
  const [preserveFillerWords, setPreserveFillerWords] = useState(true);
  const [removeBackgroundNoise, setRemoveBackgroundNoise] = useState(false);
  const [diarizeSpeakers, setDiarizeSpeakers] = useState(false);

  const inputExtension = useMemo(() => {
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [selectedFile]);

  const canSubmit =
    !isCheckingFile && !isSubmitting && !!selectedFile && !!selectedFileMeta;

  function resetResultState() {
    setTranscriptResult("");
    setTranscriptPdfArtifact(null);
    setBatchResult(null);
  }

  function resetFileState() {
    setSelectedFile(null);
    setSelectedFiles([]);
    setSelectedFileMeta(null);
    setSelectedFileMetas([]);

    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }

  function rejectFile(message) {
    resetFileState();
    setError(message);
    resetResultState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.media);
    if (securityError) {
      rejectFile(securityError);
      return;
    }

    setIsCheckingFile(true);
    setError("");
    resetResultState();

    try {
      const validated = await validatePickedFile(file, t);
      setSelectedFiles([]);
      setSelectedFile(file);
      setSelectedFileMetas([]);
      setSelectedFileMeta(validated);
    } catch (pickedFileError) {
      rejectFile(pickedFileError?.message || t.unsupportedFileType);
    } finally {
      setIsCheckingFile(false);
    }
  }

  async function handlePickedFiles(fileList) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;

    if (files.length === 1) {
      await handlePickedFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(files, FILE_SECURITY_POLICY.media, {
      account: batchAccount,
      featureLabel: "speech to text",
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

    setIsCheckingFile(true);
    setError("");
    resetResultState();

    try {
      const validations = [];
      for (const file of files) {
        validations.push(await validatePickedFile(file, t));
      }

      const mediaTypes = [...new Set(validations.map((item) => item.mediaType))];
      if (mediaTypes.length !== 1) {
        throw new Error("All files in a speech-to-text batch must use the same media type.");
      }

      setSelectedFile(files[0]);
      setSelectedFileMeta(validations[0]);
      setSelectedFiles(files);
      setSelectedFileMetas(validations);
    } catch (pickedFileError) {
      rejectFile(pickedFileError.message || t.validationFailed);
    } finally {
      setIsCheckingFile(false);
    }
  }

  function handleFileChange(event) {
    void handlePickedFiles(event.target.files);
  }

function handleDrop(event) {
  event.preventDefault();
  event.stopPropagation();
  void handlePickedFiles(event.dataTransfer.files);
}

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  async function handleSubmit(event) {
    event.preventDefault();

    if (!selectedFile || !selectedFileMeta) {
      setError(t.chooseFileToTranscribe);
      return;
    }

    setIsSubmitting(true);
    setError("");
    resetResultState();

    try {

      if (selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append("media_type", selectedFileMetas[0]?.mediaType || selectedFileMeta.mediaType);
        selectedFileMetas.forEach((meta) => {
          formData.append("duration_seconds", String(Math.round(meta.durationSeconds)));
        });
        formData.append(
          "system_language",
          language === "fr" ? "french" : "english",
        );
        formData.append("preserve_filler_words", String(preserveFillerWords));
        formData.append("remove_background_noise", String(removeBackgroundNoise));
        formData.append("diarize_speakers", String(diarizeSpeakers));

        const data = await postAnalyzerBatchFeature("transcribe", formData);
        setBatchResult(data);
        setTranscriptResult("");
        setTranscriptPdfArtifact(null);
        return;
      }

      const extension = getFileExtension(selectedFile.name);

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("media_type", selectedFileMeta.mediaType);
      formData.append("media_format", extension.replace(".", ""));
      formData.append(
        "duration_seconds",
        String(Math.round(selectedFileMeta.durationSeconds)),
      );
      formData.append(
        "system_language",
        language === "fr" ? "french" : "english",
      );
      formData.append("preserve_filler_words", String(preserveFillerWords));
      formData.append("remove_background_noise", String(removeBackgroundNoise));
      formData.append("diarize_speakers", String(diarizeSpeakers));

      const response = await fetch("/api/analyzer/transcribe", {
        method: "POST",
        credentials: "include",
        body: formData,
      });

      const responseData = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(
          extractResponseMessage(responseData, t.transcriptionFailed),
        );
      }

      const transcriptText = extractTranscriptText(responseData);

      if (!transcriptText) {
        throw new Error("Backend returned no transcript text.");
      }

      setTranscriptResult(transcriptText);
      const pdfArtifact = extractTranscriptPdfArtifact(responseData);
      if (!pdfArtifact) {
        throw new Error(
          "Backend returned transcript text but no PDF download artifact.",
        );
      }

      setTranscriptPdfArtifact(pdfArtifact);
    } catch (submitError) {
      setError(submitError?.message || t.transcriptionFailed);
    } finally {
      setIsSubmitting(false);
    }
  }

  const actionLabel = isCheckingFile
    ? t.validatingMedia
    : isSubmitting
      ? common.transcribing || common.generating
      : common.transcribe;

  const optionsDisabled = isSubmitting;

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-x-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 py-5 md:px-6 lg:py-6">
          <header className="mb-4 shrink-0">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => router.push("/")}
                className="inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
              >
                <ArrowLeft className="h-4 w-4" />
                {common.back}
              </button>

              <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-4 py-2 text-sm text-[var(--app-accent-text)] backdrop-blur">
                <Sparkles className="h-4 w-4" />
                {t.badge}
              </div>
            </div>

            <div className="mt-4">
              <h1 className="max-w-full text-3xl font-semibold tracking-tight text-[var(--app-text)] sm:text-4xl lg:whitespace-nowrap lg:text-[2.65rem] lg:leading-tight xl:text-5xl">
                {t.title}
              </h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 app-text-muted md:text-base">
                {t.description}
              </p>
            </div>
          </header>

          <section className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
            <form
              onSubmit={handleSubmit}
              className="relative min-h-0 overflow-hidden rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5"
            >
              <div className="absolute inset-0 app-card-overlay" />

              <div className="relative flex h-full min-h-0 flex-col">
                <div
                  onDrop={handleDrop}
                  onDragOver={handleDragOver}
                  className="rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center transition hover:border-[var(--app-border-strong)] hover:bg-[var(--app-surface-strong)] md:p-5"
                >
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                    <Upload className="h-5 w-5 text-cyan-300" />
                  </div>

                  <h2 className="text-base font-semibold text-[var(--app-text)]">
                    {t.uploadTitle}
                  </h2>

                  <p className="mt-2 text-sm leading-6 app-text-muted">
                    {t.allowedFileInputs}
                  </p>

                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    onChange={handleFileChange}
                    className="hidden"
                  />

                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="mt-3 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                  >
                    {common.chooseFile}
                  </button>
                </div>

                {selectedFile && selectedFileMeta && (
                  <div className="mt-3 rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                      <div className="min-w-0">
                        <p className="font-medium text-emerald-100">
                          {common.fileAccepted}
                        </p>
                        <p className="mt-1 truncate text-sm text-emerald-100/80">
                          {selectedFile.name} • {formatBytes(selectedFile.size)}
                        </p>
                        <p className="mt-1 text-sm text-emerald-100/80">
                          {t.detectedTypeLabel}{" "}
                          {getMediaTypeLabel(inputExtension, t)}
                        </p>
                        <p className="mt-1 text-sm text-emerald-100/80">
                          {t.durationLabel}{" "}
                          {formatDuration(selectedFileMeta.durationSeconds)}
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-4">
                  <div className="mb-3 flex items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)]">
                      <Mic className="h-5 w-5 text-cyan-300" />
                    </div>
                    <div>
                      <h2 className="text-base font-semibold text-[var(--app-text)]">
                        {t.transcriptOptionsTitle}
                      </h2>
                      <p className="text-sm app-text-soft">
                        {t.transcriptOptionsSubtitle}
                      </p>
                    </div>
                  </div>

                  <div className="grid gap-3 lg:grid-cols-3">
                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.preserveFillerWordsLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.preserveFillerWordsHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={preserveFillerWords}
                          disabled={optionsDisabled}
                          onChange={(e) =>
                            setPreserveFillerWords(e.target.checked)
                          }
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>

                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.removeBackgroundNoiseLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.removeBackgroundNoiseHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={removeBackgroundNoise}
                          disabled={optionsDisabled}
                          onChange={(e) =>
                            setRemoveBackgroundNoise(e.target.checked)
                          }
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>

                    <label className="rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-[var(--app-text)]">
                            {t.diarizeSpeakersLabel}
                          </p>
                          <p className="mt-1 text-xs leading-5 app-text-soft">
                            {t.diarizeSpeakersHelp}
                          </p>
                        </div>
                        <input
                          type="checkbox"
                          checked={diarizeSpeakers}
                          disabled={optionsDisabled}
                          onChange={(e) => setDiarizeSpeakers(e.target.checked)}
                          className="mt-1 h-4 w-4 rounded border-[var(--app-border)] bg-transparent text-cyan-300 focus:ring-cyan-300 disabled:cursor-not-allowed disabled:opacity-50"
                        />
                      </div>
                    </label>
                  </div>
                </div>

                {error && (
                  <div className="mt-3 rounded-2xl border border-red-400/20 bg-red-400/10 p-3">
                    <div className="flex items-start gap-3">
                      <XCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-300" />
                      <p className="text-sm leading-6 text-red-100">{error}</p>
                    </div>
                  </div>
                )}

                <div className="mt-auto pt-4">
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="submit"
                      disabled={!canSubmit}
                      className={`rounded-2xl px-5 py-2.5 text-sm font-semibold transition ${
                        canSubmit
                          ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)] hover:scale-[1.02] hover:shadow-xl"
                          : "cursor-not-allowed bg-[var(--app-surface)] app-text-soft"
                      }`}
                    >
                      {actionLabel}
                    </button>
                  </div>

                  <div className="mt-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-sm app-text-soft">
                    {common.outputFormat}{" "}
                    <span className="font-medium app-text-muted">
                      {OUTPUT_EXTENSION}
                    </span>
                  </div>
                </div>
              </div>
            <BatchResultPanel result={batchResult} title="Batch speech-to-text results" />
            </form>

            <aside className="min-h-0">
              <div className="flex min-h-[270px] flex-col rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-4 backdrop-blur-xl md:p-5 lg:max-h-[calc(100vh-11rem)]">
                <div className="flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold text-[var(--app-text)]">
                    {t.transcriptOutput}
                  </h2>
                  <span className="rounded-full border border-[var(--app-border)] bg-[var(--app-surface)] px-3 py-1 text-xs app-text-soft">
                    {OUTPUT_EXTENSION}
                  </span>
                </div>

                <div className="mt-3 min-h-0 flex-1 overflow-y-auto rounded-2xl border border-[var(--app-border)] bg-[var(--app-panel)] p-4 max-h-[420px] lg:max-h-[calc(100vh-16rem)]">
                  {transcriptResult ? (
                    <div className="space-y-3">
                      <div className="rounded-2xl border border-emerald-400/20 bg-emerald-400/10 p-3">
                        <div className="flex items-start gap-3">
                          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-300" />
                          <div>
                            <p className="font-medium text-emerald-100">
                              {t.transcriptReady}
                            </p>
                            <p className="mt-1 text-sm text-emerald-100/80">
                              {t.transcriptReadyText}
                            </p>
                            <p className="mt-1 text-sm text-emerald-100/80">
                              {t.transcriptMetaLabel}: {t.transcriptMetaValue}
                            </p>
                          </div>
                        </div>
                      </div>

                      {transcriptPdfArtifact && (
                        <a
                          href={transcriptPdfArtifact.downloadUrl}
                          download={transcriptPdfArtifact.filename}
                          className="inline-flex items-center justify-center rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                        >
                          {t.downloadPdfTranscript || "Download PDF transcript"}
                        </a>
                      )}

                      <pre className="whitespace-pre-wrap break-words pr-1 text-xs leading-6 app-text-muted md:text-sm">
                        {transcriptResult}
                      </pre>
                    </div>
                  ) : (
                    <div className="flex h-full min-h-[180px] items-center justify-center rounded-2xl border border-dashed border-[var(--app-border)] bg-[var(--app-surface)] p-4 text-center">
                      <p className="max-w-sm text-sm leading-6 app-text-soft">
                        {t.previewText}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </aside>
          </section>
        </div>
      </div>
    </AppSidebarLayout>
  );
}