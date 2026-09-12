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

const TTS_LANGUAGE_OPTIONS = Object.freeze([
  Object.freeze({
    code: "nl",
    labels: Object.freeze({ en: "Dutch", fr: "Néerlandais" }),
    defaultVoice: "aura-2-rhea-nl",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-beatrix-nl", name: "Beatrix", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-daphne-nl", name: "Daphne", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-cornelia-nl", name: "Cornelia", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-sander-nl", name: "Sander", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-hestia-nl", name: "Hestia", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-lars-nl", name: "Lars", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-roman-nl", name: "Roman", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-rhea-nl", name: "Rhea", accent: "Dutch" }),
      Object.freeze({ id: "aura-2-leda-nl", name: "Leda", accent: "Dutch" }),
    ]),
  }),
  Object.freeze({
    code: "en",
    labels: Object.freeze({ en: "English", fr: "Anglais" }),
    defaultVoice: "aura-2-thalia-en",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-amalthea-en", name: "Amalthea", accent: "Filipino" }),
      Object.freeze({ id: "aura-2-andromeda-en", name: "Andromeda", accent: "American" }),
      Object.freeze({ id: "aura-2-apollo-en", name: "Apollo", accent: "American" }),
      Object.freeze({ id: "aura-2-arcas-en", name: "Arcas", accent: "American" }),
      Object.freeze({ id: "aura-2-aries-en", name: "Aries", accent: "American" }),
      Object.freeze({ id: "aura-2-asteria-en", name: "Asteria", accent: "American" }),
      Object.freeze({ id: "aura-2-athena-en", name: "Athena", accent: "American" }),
      Object.freeze({ id: "aura-2-atlas-en", name: "Atlas", accent: "American" }),
      Object.freeze({ id: "aura-2-aurora-en", name: "Aurora", accent: "American" }),
      Object.freeze({ id: "aura-2-callista-en", name: "Callista", accent: "American" }),
      Object.freeze({ id: "aura-2-cora-en", name: "Cora", accent: "American" }),
      Object.freeze({ id: "aura-2-cordelia-en", name: "Cordelia", accent: "American" }),
      Object.freeze({ id: "aura-2-delia-en", name: "Delia", accent: "American" }),
      Object.freeze({ id: "aura-2-draco-en", name: "Draco", accent: "British" }),
      Object.freeze({ id: "aura-2-electra-en", name: "Electra", accent: "American" }),
      Object.freeze({ id: "aura-2-harmonia-en", name: "Harmonia", accent: "American" }),
      Object.freeze({ id: "aura-2-helena-en", name: "Helena", accent: "American" }),
      Object.freeze({ id: "aura-2-hera-en", name: "Hera", accent: "American" }),
      Object.freeze({ id: "aura-2-hermes-en", name: "Hermes", accent: "American" }),
      Object.freeze({ id: "aura-2-hyperion-en", name: "Hyperion", accent: "Australian" }),
      Object.freeze({ id: "aura-2-iris-en", name: "Iris", accent: "American" }),
      Object.freeze({ id: "aura-2-janus-en", name: "Janus", accent: "American Southern" }),
      Object.freeze({ id: "aura-2-juno-en", name: "Juno", accent: "American" }),
      Object.freeze({ id: "aura-2-jupiter-en", name: "Jupiter", accent: "American" }),
      Object.freeze({ id: "aura-2-luna-en", name: "Luna", accent: "American" }),
      Object.freeze({ id: "aura-2-mars-en", name: "Mars", accent: "American" }),
      Object.freeze({ id: "aura-2-minerva-en", name: "Minerva", accent: "American" }),
      Object.freeze({ id: "aura-2-neptune-en", name: "Neptune", accent: "American" }),
      Object.freeze({ id: "aura-2-odysseus-en", name: "Odysseus", accent: "American" }),
      Object.freeze({ id: "aura-2-ophelia-en", name: "Ophelia", accent: "American" }),
      Object.freeze({ id: "aura-2-orion-en", name: "Orion", accent: "American" }),
      Object.freeze({ id: "aura-2-orpheus-en", name: "Orpheus", accent: "American" }),
      Object.freeze({ id: "aura-2-pandora-en", name: "Pandora", accent: "British" }),
      Object.freeze({ id: "aura-2-phoebe-en", name: "Phoebe", accent: "American" }),
      Object.freeze({ id: "aura-2-pluto-en", name: "Pluto", accent: "American" }),
      Object.freeze({ id: "aura-2-saturn-en", name: "Saturn", accent: "American" }),
      Object.freeze({ id: "aura-2-selene-en", name: "Selene", accent: "American" }),
      Object.freeze({ id: "aura-2-thalia-en", name: "Thalia", accent: "American" }),
      Object.freeze({ id: "aura-2-theia-en", name: "Theia", accent: "Australian" }),
      Object.freeze({ id: "aura-2-vesta-en", name: "Vesta", accent: "American" }),
      Object.freeze({ id: "aura-2-zeus-en", name: "Zeus", accent: "American" }),
    ]),
  }),
  Object.freeze({
    code: "fr",
    labels: Object.freeze({ en: "French", fr: "Français" }),
    defaultVoice: "aura-2-agathe-fr",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-agathe-fr", name: "Agathe", accent: "French" }),
      Object.freeze({ id: "aura-2-hector-fr", name: "Hector", accent: "French" }),
    ]),
  }),
  Object.freeze({
    code: "de",
    labels: Object.freeze({ en: "German", fr: "Allemand" }),
    defaultVoice: "aura-2-julius-de",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-elara-de", name: "Elara", accent: "German" }),
      Object.freeze({ id: "aura-2-aurelia-de", name: "Aurelia", accent: "German" }),
      Object.freeze({ id: "aura-2-lara-de", name: "Lara", accent: "German" }),
      Object.freeze({ id: "aura-2-julius-de", name: "Julius", accent: "German" }),
      Object.freeze({ id: "aura-2-fabian-de", name: "Fabian", accent: "German" }),
      Object.freeze({ id: "aura-2-kara-de", name: "Kara", accent: "German" }),
      Object.freeze({ id: "aura-2-viktoria-de", name: "Viktoria", accent: "German" }),
    ]),
  }),
  Object.freeze({
    code: "it",
    labels: Object.freeze({ en: "Italian", fr: "Italien" }),
    defaultVoice: "aura-2-livia-it",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-melia-it", name: "Melia", accent: "Italian" }),
      Object.freeze({ id: "aura-2-elio-it", name: "Elio", accent: "Italian" }),
      Object.freeze({ id: "aura-2-flavio-it", name: "Flavio", accent: "Italian" }),
      Object.freeze({ id: "aura-2-maia-it", name: "Maia", accent: "Italian" }),
      Object.freeze({ id: "aura-2-cinzia-it", name: "Cinzia", accent: "Italian" }),
      Object.freeze({ id: "aura-2-cesare-it", name: "Cesare", accent: "Italian" }),
      Object.freeze({ id: "aura-2-livia-it", name: "Livia", accent: "Italian" }),
      Object.freeze({ id: "aura-2-dionisio-it", name: "Dionisio", accent: "Italian" }),
      Object.freeze({ id: "aura-2-demetra-it", name: "Demetra", accent: "Italian" }),
    ]),
  }),
  Object.freeze({
    code: "ja",
    labels: Object.freeze({ en: "Japanese", fr: "Japonais" }),
    defaultVoice: "aura-2-izanami-ja",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-uzume-ja", name: "Uzume", accent: "Japanese" }),
      Object.freeze({ id: "aura-2-ebisu-ja", name: "Ebisu", accent: "Japanese" }),
      Object.freeze({ id: "aura-2-fujin-ja", name: "Fujin", accent: "Japanese" }),
      Object.freeze({ id: "aura-2-izanami-ja", name: "Izanami", accent: "Japanese" }),
      Object.freeze({ id: "aura-2-ama-ja", name: "Ama", accent: "Japanese" }),
    ]),
  }),
  Object.freeze({
    code: "es",
    labels: Object.freeze({ en: "Spanish", fr: "Espagnol" }),
    defaultVoice: "aura-2-celeste-es",
    voices: Object.freeze([
      Object.freeze({ id: "aura-2-sirio-es", name: "Sirio", accent: "Mexican" }),
      Object.freeze({ id: "aura-2-nestor-es", name: "Nestor", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-carina-es", name: "Carina", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-celeste-es", name: "Celeste", accent: "Colombian" }),
      Object.freeze({ id: "aura-2-alvaro-es", name: "Alvaro", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-diana-es", name: "Diana", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-aquila-es", name: "Aquila", accent: "Latin American" }),
      Object.freeze({ id: "aura-2-selena-es", name: "Selena", accent: "Latin American" }),
      Object.freeze({ id: "aura-2-estrella-es", name: "Estrella", accent: "Mexican" }),
      Object.freeze({ id: "aura-2-javier-es", name: "Javier", accent: "Mexican" }),
      Object.freeze({ id: "aura-2-agustina-es", name: "Agustina", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-antonia-es", name: "Antonia", accent: "Argentine" }),
      Object.freeze({ id: "aura-2-gloria-es", name: "Gloria", accent: "Colombian" }),
      Object.freeze({ id: "aura-2-luciano-es", name: "Luciano", accent: "Mexican" }),
      Object.freeze({ id: "aura-2-olivia-es", name: "Olivia", accent: "Mexican" }),
      Object.freeze({ id: "aura-2-silvia-es", name: "Silvia", accent: "Peninsular" }),
      Object.freeze({ id: "aura-2-valerio-es", name: "Valerio", accent: "Mexican" }),
    ]),
  }),
]);

const TTS_LANGUAGE_BY_CODE = Object.freeze(
  Object.fromEntries(TTS_LANGUAGE_OPTIONS.map((option) => [option.code, option])),
);

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
    synthesisLanguageLabel: "Speech language",
    synthesisLanguageHelp:
      "Choose the language already used by the source text. Text to Speech synthesizes pronunciation; it does not translate the document.",
    voiceLabel: "Voice",
    voiceHelp:
      "The available Deepgram Aura-2 voices are filtered to the selected speech language.",
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
    synthesisLanguageLabel: "Langue de lecture",
    synthesisLanguageHelp:
      "Choisissez la langue déjà utilisée dans le texte source. La synthèse vocale prononce le texte ; elle ne traduit pas le document.",
    voiceLabel: "Voix",
    voiceHelp:
      "Les voix Deepgram Aura-2 disponibles sont filtrées selon la langue de lecture sélectionnée.",
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

function withInlineArtifactDisposition(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";

  try {
    const base =
      typeof window !== "undefined"
        ? window.location.origin
        : "https://redocx.invalid";
    const parsed = new URL(raw, base);

    // Only mutate same-origin analyzer artifact URLs. External/CDN/signed URLs
    // must remain byte-for-byte intact because adding a query parameter can
    // invalidate a provider signature or change cache semantics.
    if (parsed.origin !== base) return raw;

    parsed.searchParams.set("disposition", "inline");
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    const separator = raw.includes("?") ? "&" : "?";
    return `${raw}${separator}disposition=inline`;
  }
}

export default function TextToSpeechPage() {
  const { user, authChecked } = useAccount();
  const { language } = useLanguage();
  const t = copy[language] || copy.en;

  const [inputMode, setInputMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [text, setText] = useState("");
  const [synthesisLanguage, setSynthesisLanguage] = useState("en");
  const [voiceId, setVoiceId] = useState(
    TTS_LANGUAGE_BY_CODE.en.defaultVoice,
  );
  const [outputFormat, setOutputFormat] = useState("mp3");
  const [outputFilename, setOutputFilename] = useState("spoken-document.mp3");
  const [speakingRate, setSpeakingRate] = useState(1.0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const selectedLanguageOption =
    TTS_LANGUAGE_BY_CODE[synthesisLanguage] || TTS_LANGUAGE_BY_CODE.en;
  const selectedLanguageLabel =
    selectedLanguageOption.labels[language] || selectedLanguageOption.labels.en;

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

  const previewUrl = useMemo(
    () => withInlineArtifactDisposition(resultUrl),
    [resultUrl],
  );

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

  function handleSynthesisLanguageChange(nextLanguage) {
    const languageOption =
      TTS_LANGUAGE_BY_CODE[nextLanguage] || TTS_LANGUAGE_BY_CODE.en;
    setSynthesisLanguage(languageOption.code);
    setVoiceId(languageOption.defaultVoice);
    setResult(null);
    setError("");
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
      formData.set("synthesis_language", synthesisLanguage);
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
        generated.synthesis_language !== synthesisLanguage ||
        generated.voice_id !== voiceId ||
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

              <div className="grid gap-4 lg:grid-cols-3">
                <div>
                  <label htmlFor="tts-language" className="text-sm font-medium app-text">
                    {t.synthesisLanguageLabel}
                  </label>
                  <select
                    id="tts-language"
                    value={synthesisLanguage}
                    onChange={(event) =>
                      handleSynthesisLanguageChange(event.target.value)
                    }
                    className="mt-2 w-full rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
                  >
                    {TTS_LANGUAGE_OPTIONS.map((option) => (
                      <option key={option.code} value={option.code}>
                        {option.labels[language] || option.labels.en}
                      </option>
                    ))}
                  </select>
                  <p className="mt-2 text-xs leading-5 app-text-soft">
                    {t.synthesisLanguageHelp}
                  </p>
                </div>

                <div>
                  <label htmlFor="tts-voice" className="text-sm font-medium app-text">
                    {t.voiceLabel}
                  </label>
                  <select
                    id="tts-voice"
                    value={voiceId}
                    onChange={(event) => {
                      setVoiceId(event.target.value);
                      setResult(null);
                      setError("");
                    }}
                    className="mt-2 w-full rounded-xl border app-surface px-3 py-2.5 text-sm app-text outline-none focus:border-[var(--app-focus)]"
                  >
                    {selectedLanguageOption.voices.map((voice) => (
                      <option key={voice.id} value={voice.id}>
                        {voice.name} — {voice.accent}
                      </option>
                    ))}
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
                        {String(result.output_format || "").toUpperCase()} · {selectedLanguageLabel} · {result.voice_id}
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

                  {previewUrl ? (
                    <audio
                      className="mt-5 w-full"
                      controls
                      preload="metadata"
                      src={previewUrl}
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
