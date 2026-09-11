"use client";

import { useMemo, useState } from "react";
import {
  AudioLines,
  Download,
  FileText,
  Gauge,
  Headphones,
  Loader2,
  ShieldCheck,
  UploadCloud,
  Volume2,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  buildAnalyzerArtifactUrl,
  postAnalyzerFeature,
  withAnalyzerDownloadFilename,
} from "@/lib/api_client";
import {
  validateBrowserSafeText,
  validateBrowserUpload,
} from "@/lib/secure_upload_policy";

const KB = 1024;
const MB = 1024 * KB;

const TEXT_TO_SPEECH_INLINE_POLICY = Object.freeze({
  maxBytes: 64 * KB,
  maxChars: 20_000,
  maxWords: null,
  maxLines: 2_000,
  maxLineChars: 20_000,
  maxIdenticalRun: 2_048,
  maxCombiningRun: 16,
});

const TEXT_TO_SPEECH_FILE_POLICY = Object.freeze({
  label: "PDF, Word, or text document",
  maxBytes: 25 * MB,
  extensions: Object.freeze([".pdf", ".docx", ".txt"]),
  mimeTypes: Object.freeze({
    ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
    ".docx": Object.freeze([
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      "application/zip",
      "application/x-zip",
      "application/x-zip-compressed",
    ]),
    ".txt": Object.freeze(["text/plain", "application/octet-stream"]),
  }),
});

const OUTPUT_FORMATS = ["mp3", "wav", "opus", "aac", "flac"];

const copy = {
  en: {
    back: "Back to dashboard",
    badge: "Text to Speech",
    title: "Turn documents and text into speech",
    description:
      "Generate an owned ReDOCX audio artifact from PDF, Word, TXT, or inline text while preserving the source text exactly through the synthesis workflow.",
    authNote:
      "Text to Speech runs through your authenticated ReDOCX account and stores the generated audio as a short-lived owned artifact.",
    fileMode: "Upload document",
    textMode: "Paste text",
    chooseFile: "Choose PDF, DOCX, or TXT",
    fileHelp: "Supported inputs: .pdf, .docx, .txt. Maximum file size: 25 MB.",
    textLabel: "Text to synthesize",
    textPlaceholder: "Paste or type the text you want ReDOCX to speak...",
    textHelp: "Maximum 20,000 characters / 64 KB. The backend remains the final validation authority.",
    voiceLabel: "Voice",
    voiceDefault: "Default ReDOCX voice",
    voiceHelp:
      "The default voice resolves to the provider model configured by your ReDOCX deployment.",
    formatLabel: "Audio format",
    rateLabel: "Speaking rate",
    rateSlow: "Slower",
    rateNormal: "Normal",
    rateFast: "Faster",
    filenameLabel: "Output filename",
    generate: "Generate speech",
    generating: "Generating speech...",
    resultTitle: "Generated audio",
    resultEmpty: "Your generated audio will appear here after processing.",
    download: "Download audio",
    sourceChars: "Source characters",
    duration: "Duration",
    fileSize: "File size",
    syntheticVoice: "Synthetic voice",
    syntheticVoiceValue: "Yes",
    signInTitle: "Sign in required",
    signInBody: "Text to Speech is available only to authenticated ReDOCX users.",
    signIn: "Sign in",
    invalidInput: "Provide either one supported document or non-empty text.",
    invalidFilename: "Use a plain output filename without folders or path characters.",
    requestFailed: "Text to Speech could not be completed.",
    seconds: "seconds",
  },
  fr: {
    back: "Retour au tableau de bord",
    badge: "Synthèse vocale",
    title: "Transformez vos documents et textes en audio",
    description:
      "Générez un fichier audio ReDOCX associé à votre compte à partir d’un PDF, document Word, fichier TXT ou texte saisi, tout en préservant exactement le texte source pendant la synthèse.",
    authNote:
      "La synthèse vocale utilise votre compte ReDOCX authentifié et stocke l’audio généré comme un artefact temporaire appartenant à votre compte.",
    fileMode: "Importer un document",
    textMode: "Coller du texte",
    chooseFile: "Choisir un PDF, DOCX ou TXT",
    fileHelp: "Entrées prises en charge : .pdf, .docx, .txt. Taille maximale : 25 Mo.",
    textLabel: "Texte à synthétiser",
    textPlaceholder: "Collez ou saisissez le texte que ReDOCX doit lire...",
    textHelp: "Maximum 20 000 caractères / 64 Ko. Le backend reste l’autorité finale de validation.",
    voiceLabel: "Voix",
    voiceDefault: "Voix ReDOCX par défaut",
    voiceHelp:
      "La voix par défaut correspond au modèle fournisseur configuré pour votre déploiement ReDOCX.",
    formatLabel: "Format audio",
    rateLabel: "Vitesse de lecture",
    rateSlow: "Plus lente",
    rateNormal: "Normale",
    rateFast: "Plus rapide",
    filenameLabel: "Nom du fichier de sortie",
    generate: "Générer l’audio",
    generating: "Génération de l’audio...",
    resultTitle: "Audio généré",
    resultEmpty: "Votre audio généré apparaîtra ici après traitement.",
    download: "Télécharger l’audio",
    sourceChars: "Caractères source",
    duration: "Durée",
    fileSize: "Taille du fichier",
    syntheticVoice: "Voix synthétique",
    syntheticVoiceValue: "Oui",
    signInTitle: "Connexion requise",
    signInBody: "La synthèse vocale est réservée aux utilisateurs ReDOCX authentifiés.",
    signIn: "Se connecter",
    invalidInput: "Fournissez un document pris en charge ou un texte non vide.",
    invalidFilename: "Utilisez un nom de fichier simple sans dossier ni caractère de chemin.",
    requestFailed: "La synthèse vocale n’a pas pu être effectuée.",
    seconds: "secondes",
  },
};

function errorMessage(error, fallback) {
  return (
    error?.payload?.detail?.message ||
    error?.payload?.error?.message ||
    error?.message ||
    fallback
  );
}

function replaceFilenameExtension(filename, extension) {
  const raw = String(filename || "spoken-document").trim() || "spoken-document";
  const clean = raw.replace(/[\\/]/gu, "-");
  const stem = clean.replace(/\.[A-Za-z0-9]+$/u, "") || "spoken-document";
  return `${stem}.${extension}`;
}

function isPlainFilename(value) {
  const filename = String(value || "").trim();
  return Boolean(filename) && filename !== "." && filename !== ".." && !/[\\/\0]/u.test(filename);
}

function formatMegabytes(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size < 0) return "—";
  if (size < 0.01) return `${Math.round(size * 1024)} KB`;
  return `${size.toFixed(size < 1 ? 3 : 2)} MB`;
}

export default function TextToSpeechPage() {
  const { user, authChecked } = useAccount();
  const { language } = useLanguage();
  const t = copy[language] || copy.en;

  const [inputMode, setInputMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [text, setText] = useState("");
  const voiceId = "default";
  const [outputFormat, setOutputFormat] = useState("mp3");
  const [outputFilename, setOutputFilename] = useState("spoken-document.mp3");
  const [speakingRate, setSpeakingRate] = useState(1.0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const textBytes = useMemo(() => {
    try {
      return new TextEncoder().encode(text).byteLength;
    } catch {
      return Number.POSITIVE_INFINITY;
    }
  }, [text]);

  const resultUrl = useMemo(() => {
    if (!result) return "";
    return result.download_url || buildAnalyzerArtifactUrl(result.storage_key);
  }, [result]);

  const downloadUrl = useMemo(() => {
    if (!resultUrl || !result?.filename) return resultUrl;
    return withAnalyzerDownloadFilename(resultUrl, result.filename);
  }, [result, resultUrl]);

  async function handleFileChange(event) {
    const file = event.target.files?.[0] || null;
    setError("");
    setResult(null);

    if (!file) {
      setSelectedFile(null);
      return;
    }

    const validationMessage = await validateBrowserUpload(
      file,
      TEXT_TO_SPEECH_FILE_POLICY,
    );
    if (validationMessage) {
      setSelectedFile(null);
      event.target.value = "";
      setError(validationMessage);
      return;
    }

    setSelectedFile(file);
  }

  function handleFormatChange(nextFormat) {
    setOutputFormat(nextFormat);
    setOutputFilename((current) =>
      replaceFilenameExtension(current, nextFormat),
    );
    setResult(null);
    setError("");
  }

  async function handleSubmit(event) {
    event.preventDefault();
    if (submitting) return;

    setError("");
    setResult(null);

    if (!isPlainFilename(outputFilename)) {
      setError(t.invalidFilename);
      return;
    }

    const resolvedOutputFilename = outputFilename.toLowerCase().endsWith(`.${outputFormat}`)
      ? outputFilename.trim()
      : replaceFilenameExtension(outputFilename, outputFormat);

    if (resolvedOutputFilename !== outputFilename) {
      setOutputFilename(resolvedOutputFilename);
    }

    if (inputMode === "file") {
      if (!selectedFile) {
        setError(t.invalidInput);
        return;
      }
      const validationMessage = await validateBrowserUpload(
        selectedFile,
        TEXT_TO_SPEECH_FILE_POLICY,
      );
      if (validationMessage) {
        setError(validationMessage);
        return;
      }
    } else {
      const validationMessage = validateBrowserSafeText(text, {
        policy: TEXT_TO_SPEECH_INLINE_POLICY,
        fieldName: "Text-to-Speech input",
      });
      if (validationMessage) {
        setError(validationMessage);
        return;
      }
    }

    setSubmitting(true);
    try {
      const formData = new FormData();
      if (inputMode === "file") {
        formData.set("file", selectedFile);
      } else {
        formData.set("text", text);
      }
      formData.set("voice_id", voiceId);
      formData.set("output_format", outputFormat);
      formData.set("output_filename", resolvedOutputFilename);
      formData.set("speaking_rate", String(speakingRate));
      formData.set(
        "system_language",
        language === "fr" ? "french" : "english",
      );

      const data = await postAnalyzerFeature(
        "text-to-speech",
        formData,
        true,
      );
      const generated = data?.result;
      if (
        !generated ||
        generated.output_format !== outputFormat ||
        !generated.filename ||
        (!generated.storage_key && !generated.download_url)
      ) {
        throw new Error("BACKEND_RESPONSE_INVALID");
      }
      setResult(generated);
    } catch (requestError) {
      setError(errorMessage(requestError, t.requestFailed));
    } finally {
      setSubmitting(false);
    }
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
            <Volume2 className="mx-auto h-10 w-10 app-text-muted" />
            <h1 className="mt-5 text-3xl font-semibold app-text">{t.signInTitle}</h1>
            <p className="mx-auto mt-3 max-w-xl app-text-muted">{t.signInBody}</p>
            <a
              href="/auth/login?returnTo=/text-to-speech"
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
                <AudioLines className="h-3.5 w-3.5" />
                {t.badge}
              </div>
              <h1 className="mt-5 text-3xl font-semibold tracking-tight app-text sm:text-4xl">
                {t.title}
              </h1>
              <p className="mt-4 max-w-3xl leading-7 app-text-muted">{t.description}</p>
            </div>
            <div className="rounded-2xl border app-surface px-4 py-3 text-sm app-text-muted lg:max-w-sm">
              <div className="flex gap-3">
                <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
                <p>{t.authNote}</p>
              </div>
            </div>
          </div>
        </section>

        {error ? (
          <div
            className="mt-5 rounded-2xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200"
            role="alert"
          >
            {error}
          </div>
        ) : null}

        <div className="mt-6 grid gap-6 xl:grid-cols-[0.92fr_1.08fr]">
          <section className="rounded-3xl border app-surface-strong p-5 shadow-xl md:p-6">
            <form onSubmit={handleSubmit} className="space-y-6">
              <div className="grid grid-cols-2 rounded-2xl border app-surface p-1">
                {[
                  ["file", t.fileMode, UploadCloud],
                  ["text", t.textMode, FileText],
                ].map(([mode, label, Icon]) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => {
                      setInputMode(mode);
                      setError("");
                      setResult(null);
                    }}
                    className={`flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-semibold transition ${
                      inputMode === mode
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : "app-text-muted hover:bg-[var(--app-hover)]"
                    }`}
                  >
                    <Icon className="h-4 w-4" />
                    {label}
                  </button>
                ))}
              </div>

              {inputMode === "file" ? (
                <div>
                  <label className="block text-sm font-medium app-text">{t.chooseFile}</label>
                  <label className="mt-2 flex cursor-pointer flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--app-border-strong)] app-surface px-5 py-8 text-center transition hover:bg-[var(--app-hover)]">
                    <UploadCloud className="h-8 w-8 app-text-muted" />
                    <span className="mt-3 text-sm font-semibold app-text">
                      {selectedFile?.name || t.chooseFile}
                    </span>
                    {selectedFile ? (
                      <span className="mt-1 text-xs app-text-soft">
                        {(selectedFile.size / MB).toFixed(2)} MB
                      </span>
                    ) : null}
                    <input
                      type="file"
                      accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
                      className="sr-only"
                      onChange={handleFileChange}
                    />
                  </label>
                  <p className="mt-2 text-xs leading-5 app-text-soft">{t.fileHelp}</p>
                </div>
              ) : (
                <div>
                  <div className="flex items-center justify-between gap-3">
                    <label htmlFor="tts-text" className="text-sm font-medium app-text">
                      {t.textLabel}
                    </label>
                    <span className="text-xs app-text-soft">
                      {text.length.toLocaleString()} / {TEXT_TO_SPEECH_INLINE_POLICY.maxChars.toLocaleString()}
                      {` · ${Math.ceil(textBytes / 1024).toLocaleString()} KB`}
                    </span>
                  </div>
                  <textarea
                    id="tts-text"
                    value={text}
                    onChange={(event) => {
                      setText(event.target.value);
                      setError("");
                      setResult(null);
                    }}
                    rows={12}
                    maxLength={TEXT_TO_SPEECH_INLINE_POLICY.maxChars}
                    placeholder={t.textPlaceholder}
                    className="mt-2 w-full resize-y rounded-2xl border app-surface px-4 py-3 text-sm app-text outline-none transition focus:border-[var(--app-focus)]"
                  />
                  <p className="mt-2 text-xs leading-5 app-text-soft">{t.textHelp}</p>
                </div>
              )}

              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <label className="text-sm font-medium app-text">{t.voiceLabel}</label>
                  <select
                    value={voiceId}
                    disabled
                    className="mt-2 w-full rounded-xl border app-surface px-3 py-2.5 text-sm app-text disabled:opacity-80"
                  >
                    <option value="default">{t.voiceDefault}</option>
                  </select>
                  <p className="mt-2 text-xs leading-5 app-text-soft">{t.voiceHelp}</p>
                </div>

                <div>
                  <label htmlFor="tts-format" className="text-sm font-medium app-text">
                    {t.formatLabel}
                  </label>
                  <select
                    id="tts-format"
                    value={outputFormat}
                    onChange={(event) => handleFormatChange(event.target.value)}
                    className="mt-2 w-full rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
                  >
                    {OUTPUT_FORMATS.map((format) => (
                      <option key={format} value={format}>
                        {format.toUpperCase()}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between gap-3">
                  <label htmlFor="tts-rate" className="text-sm font-medium app-text">
                    {t.rateLabel}
                  </label>
                  <span className="rounded-full border app-surface px-2.5 py-1 text-xs font-semibold app-text-muted">
                    {Number(speakingRate).toFixed(2)}×
                  </span>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <span className="text-xs app-text-soft">{t.rateSlow}</span>
                  <input
                    id="tts-rate"
                    type="range"
                    min="0.5"
                    max="2"
                    step="0.05"
                    value={speakingRate}
                    onChange={(event) => {
                      setSpeakingRate(Number(event.target.value));
                      setResult(null);
                    }}
                    className="w-full accent-[var(--app-focus)]"
                  />
                  <span className="text-xs app-text-soft">{t.rateFast}</span>
                </div>
              </div>

              <div>
                <label htmlFor="tts-filename" className="text-sm font-medium app-text">
                  {t.filenameLabel}
                </label>
                <input
                  id="tts-filename"
                  value={outputFilename}
                  onChange={(event) => {
                    setOutputFilename(event.target.value);
                    setResult(null);
                    setError("");
                  }}
                  className="mt-2 w-full rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Volume2 className="h-4 w-4" />
                )}
                {submitting ? t.generating : t.generate}
              </button>
            </form>
          </section>

          <section className="rounded-3xl border app-surface-strong p-5 shadow-xl md:p-6">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl border app-surface">
                <Headphones className="h-5 w-5 app-text-muted" />
              </div>
              <h2 className="text-xl font-semibold app-text">{t.resultTitle}</h2>
            </div>

            {!result ? (
              <div className="mt-5 flex min-h-72 flex-col items-center justify-center rounded-2xl border app-surface px-6 text-center">
                <AudioLines className="h-10 w-10 app-text-soft" />
                <p className="mt-4 max-w-md text-sm leading-6 app-text-muted">
                  {t.resultEmpty}
                </p>
              </div>
            ) : (
              <div className="mt-5 space-y-5">
                <div className="rounded-2xl border app-surface p-5">
                  <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <p className="truncate text-lg font-semibold app-text">
                        {result.filename}
                      </p>
                      <p className="mt-1 text-sm app-text-muted">
                        {String(result.output_format || "").toUpperCase()} · {result.voice_id}
                      </p>
                    </div>
                    {downloadUrl ? (
                      <a
                        href={downloadUrl}
                        className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)]"
                      >
                        <Download className="h-4 w-4" />
                        {t.download}
                      </a>
                    ) : null}
                  </div>

                  {resultUrl ? (
                    <audio
                      className="mt-5 w-full"
                      controls
                      preload="metadata"
                      src={resultUrl}
                    />
                  ) : null}
                </div>

                <dl className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-2xl border app-surface p-4">
                    <dt className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.08em] app-text-soft">
                      <FileText className="h-4 w-4" />
                      {t.sourceChars}
                    </dt>
                    <dd className="mt-2 text-lg font-semibold app-text">
                      {Number(result.source_character_count || 0).toLocaleString()}
                    </dd>
                  </div>
                  <div className="rounded-2xl border app-surface p-4">
                    <dt className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.08em] app-text-soft">
                      <Gauge className="h-4 w-4" />
                      {t.duration}
                    </dt>
                    <dd className="mt-2 text-lg font-semibold app-text">
                      {result.duration_seconds
                        ? `${Number(result.duration_seconds).toFixed(2)} ${t.seconds}`
                        : "—"}
                    </dd>
                  </div>
                  <div className="rounded-2xl border app-surface p-4">
                    <dt className="text-xs font-semibold uppercase tracking-[0.08em] app-text-soft">
                      {t.fileSize}
                    </dt>
                    <dd className="mt-2 text-lg font-semibold app-text">
                      {formatMegabytes(result.file_size_mb)}
                    </dd>
                  </div>
                  <div className="rounded-2xl border app-surface p-4">
                    <dt className="text-xs font-semibold uppercase tracking-[0.08em] app-text-soft">
                      {t.syntheticVoice}
                    </dt>
                    <dd className="mt-2 text-lg font-semibold app-text">
                      {result.synthetic_voice === true ? t.syntheticVoiceValue : "—"}
                    </dd>
                  </div>
                </dl>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
