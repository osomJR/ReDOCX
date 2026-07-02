"use client";

import { useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Download,
  FileText,
  HelpCircle,
  ListChecks,
  Loader2,
  ShieldCheck,
  MessageCircleQuestion,
  RotateCcw,
  Sparkles,
  Upload,
  XCircle,
} from "lucide-react";
import {
  commonTranslations,
  generateQuestionsPageTranslations,
} from "@/lib/translations";
import AppSidebarLayout from "@/components/app_sidebar";
import BatchResultPanel from "@/components/batch_result_panel";
import { getAnalyzerResultDownloadUrl, postAnalyzerFeature, postAnalyzerBatchFeature } from "@/lib/api_client";
import { FILE_SECURITY_POLICY, validateBrowserUpload, validateBrowserBatchUploads, getBatchUploadLimit } from "@/lib/secure_upload_policy";

const ACCEPTED_EXTENSIONS = [".pdf", ".docx"];
const REJECTED_EXTENSIONS = [".png", ".jpg", ".jpeg"];
const MAX_FILE_SIZE_MB = 10;
const INLINE_TEXT_EXTENSION = ".txt";

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


function replaceVars(template, vars = {}) {
  return String(template || "").replace(
    /\{(\w+)\}/g,
    (_, key) => vars[key] ?? "",
  );
}

function systemLanguageFor(language) {
  return language === "fr" ? "french" : "english";
}

function normalizeAnalyzerPayload(data) {
  const analyzerResponse = data?.analyzer_response || data;
  return {
    analyzerResponse,
    result: analyzerResponse?.result || data?.result || null,
    sidecarQuestionsText:
      data?.generated_questions_text ||
      data?.questions_text ||
      analyzerResponse?.generated_questions_text ||
      "",
  };
}

function resultDownloadInfo(result, fallbackName, fallbackFormat) {
  if (
    !result ||
    typeof result !== "object" ||
    typeof result.content === "string"
  ) {
    return null;
  }

  const url = getAnalyzerResultDownloadUrl(result);

  return {
    filename: result.filename || fallbackName,
    outputFormat: result.output_format || fallbackFormat,
    fileSizeMb: result.file_size_mb,
    url,
  };
}

function parseNumberedItems(text) {
  const normalized = String(text || "")
    .replace(/\r\n/g, "\n")
    .trim();
  if (!normalized) return [];

  const matches = [
    ...normalized.matchAll(
      /(?:^|\n)\s*(\d+)\.\s+([\s\S]*?)(?=\n\s*\d+\.\s+|$)/g,
    ),
  ];
  const items = matches
    .map((match) => ({
      number: Number.parseInt(match[1], 10),
      body: String(match[2] || "")
        .replace(/\s+/g, " ")
        .trim(),
      raw: `${match[1]}. ${String(match[2] || "")
        .replace(/\s+/g, " ")
        .trim()}`,
    }))
    .filter((item) => item.number >= 1 && item.body);

  if (!items.length) return [];

  for (let index = 0; index < items.length; index += 1) {
    if (items[index].number !== index + 1) return [];
  }

  return items.map((item, index) => `${index + 1}. ${item.body}`);
}

function countNumberedItems(text) {
  return parseNumberedItems(text).length;
}

function UploadDropzone({
  t,
  selectedFile,
  selectedFiles = [],
  fileInputRef,
  onDrop,
  onDragOver,
  onFileChange,
  onPick,
}) {
  return (
    <div
      onDrop={onDrop}
      onDragOver={onDragOver}
      className="rounded-3xl border border-dashed border-[var(--app-border-strong)] app-surface p-6 text-center transition hover:border-[var(--app-border)]"
    >
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept={ACCEPTED_EXTENSIONS.join(",")}
        onChange={onFileChange}
        className="hidden"
      />
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl border app-surface-strong">
        <Upload className="h-6 w-6 app-text-muted" />
      </div>
      <h2 className="mt-4 text-lg font-semibold app-text">{t.uploadTitle}</h2>
      <p className="mt-2 text-sm app-text-muted">{t.allowedFileInputs}</p>
      <p className="mt-1 text-xs app-text-soft">{t.wordsLimit}</p>
      <button
        type="button"
        onClick={onPick}
        className="mt-5 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
      >
        {t.uploadTitle}
      </button>
      {selectedFile ? (
        <div className="mt-5 rounded-2xl border border-[var(--app-border)] app-surface-strong p-4 text-left">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-400" />
            <div className="min-w-0">
              <p className="text-sm font-semibold app-text">{t.fileAccepted}</p>
              <p className="truncate text-sm app-text-muted">
                {selectedFiles.length > 1 ? `${selectedFiles.length} files selected` : selectedFile.name}
              </p>
              <p className="text-xs app-text-soft">
                {formatBytes(selectedFile.size)}
              </p>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function DownloadCard({ info, label }) {
  if (!info) return null;

  return (
    <div className="mt-4 rounded-2xl border border-[var(--app-border)] app-surface p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold app-text">
            {info.filename}
          </p>
          <p className="text-xs app-text-muted">
            {info.outputFormat || "file"}
            {info.fileSizeMb ? ` · ${info.fileSizeMb} MB` : ""}
          </p>
        </div>
        {info.url ? (
          <a
            href={info.url}
            download
            className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01]"
          >
            <Download className="h-4 w-4" />
            {label}
          </a>
        ) : null}
      </div>
    </div>
  );
}

function TextOutput({ title, empty, content, icon: Icon = FileText }) {
  return (
    <section className="rounded-3xl border app-surface-strong p-6">
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-11 w-11 items-center justify-center rounded-2xl border app-surface">
          <Icon className="h-5 w-5 app-text-muted" />
        </div>
        <h2 className="text-lg font-semibold app-text">{title}</h2>
      </div>
      {content ? (
        <pre className="max-h-[32rem] whitespace-pre-wrap rounded-2xl border border-[var(--app-border)] app-surface p-4 text-sm leading-6 app-text overflow-auto">
          {content}
        </pre>
      ) : (
        <p className="rounded-2xl border border-[var(--app-border)] app-surface p-4 text-sm app-text-muted">
          {empty}
        </p>
      )}
    </section>
  );
}

export default function QuestionsPage() {
  const router = useRouter();
  const fileInputRef = useRef(null);
  const { language } = useLanguage();
  const account = useAccount();
  const batchAccount = account?.entitlement || account;
  const batchLimit = getBatchUploadLimit(batchAccount);
  const common = commonTranslations[language] || commonTranslations.en;
  const t =
    generateQuestionsPageTranslations[language] ||
    generateQuestionsPageTranslations.en;

  const [mode, setMode] = useState("file");
  const [selectedFile, setSelectedFile] = useState(null);
  const [selectedFiles, setSelectedFiles] = useState([]);
  const [inlineText, setInlineText] = useState("");
  const [error, setError] = useState("");
  const [isGeneratingQuestions, setIsGeneratingQuestions] = useState(false);
  const [isGeneratingAnswers, setIsGeneratingAnswers] = useState(false);
  const [questionsText, setQuestionsText] = useState("");
  const [questionItems, setQuestionItems] = useState([]);
  const [questionDownloadInfo, setQuestionDownloadInfo] = useState(null);
  const [answersText, setAnswersText] = useState("");
  const [answerDownloadInfo, setAnswerDownloadInfo] = useState(null);
  const [batchQuestionResult, setBatchQuestionResult] = useState(null);
  const [batchAnswerResult, setBatchAnswerResult] = useState(null);
  const [batchQuestionItemsByIndex, setBatchQuestionItemsByIndex] = useState({});
  const [answerDecision, setAnswerDecision] = useState("pending");
  const [sourceSnapshot, setSourceSnapshot] = useState(null);

  const inputExtension = useMemo(() => {
    if (mode === "text") return INLINE_TEXT_EXTENSION;
    if (!selectedFile) return "";
    return getFileExtension(selectedFile.name);
  }, [mode, selectedFile]);

  const outputExtension =
    mode === "text" ? INLINE_TEXT_EXTENSION : inputExtension || "";

  const isValidFile = useMemo(() => {
    if (!selectedFile) return false;
    const ext = getFileExtension(selectedFile.name);
    const isAccepted = ACCEPTED_EXTENSIONS.includes(ext);
    const isWithinLimit = selectedFile.size <= MAX_FILE_SIZE_MB * 1024 * 1024;
    return isAccepted && isWithinLimit;
  }, [selectedFile]);

  const canGenerateQuestions =
    !isGeneratingQuestions &&
    !isGeneratingAnswers &&
    ((mode === "file" && selectedFile && isValidFile) ||
      (mode === "text" && inlineText.trim().length > 0));

  const batchQuestionEntries = useMemo(
    () =>
      Object.entries(batchQuestionItemsByIndex)
        .map(([index, questions]) => ({
          index: Number(index),
          questions: Array.isArray(questions) ? questions : [],
        }))
        .filter(
          ({ index, questions }) =>
            Number.isInteger(index) && index > 0 && questions.length > 0,
        )
        .sort((left, right) => left.index - right.index),
    [batchQuestionItemsByIndex],
  );

  const isBatchAnswerFlow =
    sourceSnapshot?.mode === "file" &&
    Array.isArray(sourceSnapshot.files) &&
    sourceSnapshot.files.length > 1;

  const hasBatchQuestionItems = batchQuestionEntries.length > 0;

  const batchQuestionCount = useMemo(
    () =>
      batchQuestionEntries.reduce(
        (total, entry) => total + entry.questions.length,
        0,
      ),
    [batchQuestionEntries],
  );

  const canGenerateAnswers =
    !isGeneratingAnswers &&
    !isGeneratingQuestions &&
    Boolean(sourceSnapshot) &&
    ((isBatchAnswerFlow && hasBatchQuestionItems) ||
      (!isBatchAnswerFlow && questionItems.length > 0));

  const shouldShowAnswerPrompt =
    Boolean(sourceSnapshot) &&
    (Boolean(questionsText) || questionItems.length > 0 || hasBatchQuestionItems);

  const displayedQuestionCount = isBatchAnswerFlow
    ? batchQuestionCount
    : questionItems.length || countNumberedItems(questionsText);

  function clearGeneratedState() {
    setQuestionsText("");
    setQuestionItems([]);
    setQuestionDownloadInfo(null);
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setBatchQuestionResult(null);
    setBatchAnswerResult(null);
    setBatchQuestionItemsByIndex({});
    setAnswerDecision("pending");
    setSourceSnapshot(null);
  }

  function rejectFile(message) {
    setSelectedFile(null);
    setSelectedFiles([]);
    setError(message);
    clearGeneratedState();
  }

  function handleModeChange(nextMode) {
    setMode(nextMode);
    if (nextMode === "text") setSelectedFiles([]);
    setError("");
    clearGeneratedState();
  }

  async function handlePickedFile(file) {
    if (!file) return;

    const securityError = await validateBrowserUpload(file, FILE_SECURITY_POLICY.aiTextDocument);
    if (securityError) {
      rejectFile(securityError);
      return;
    }

    const ext = getFileExtension(file.name);

    if (!ACCEPTED_EXTENSIONS.includes(ext)) {
      rejectFile(replaceVars(t.unsupportedFileType, { ext: ext || "unknown" }));
      return;
    }

    if (file.size > MAX_FILE_SIZE_MB * 1024 * 1024) {
      rejectFile(replaceVars(t.fileTooLarge, { maxSize: MAX_FILE_SIZE_MB }));
      return;
    }

    setError("");
    setSelectedFiles([]);
    setSelectedFile(file);
    clearGeneratedState();
  }

  async function handlePickedFiles(fileList) {
    const files = Array.from(fileList || []).filter(Boolean);
    if (!files.length) return;

    if (files.length === 1) {
      await handlePickedFile(files[0]);
      return;
    }

    const batchValidation = await validateBrowserBatchUploads(files, FILE_SECURITY_POLICY.aiTextDocument, {
      account: batchAccount,
      featureLabel: "question generation",
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

    setError("");
    setSelectedFile(files[0]);
    setSelectedFiles(files);
    clearGeneratedState();
  }

  function handleFileChange(event) {
    handlePickedFiles(event.target.files);
  }

  function handleDrop(event) {
    event.preventDefault();
    event.stopPropagation();
    handlePickedFiles(event.dataTransfer.files);
  }

  function handleDragOver(event) {
    event.preventDefault();
    event.stopPropagation();
  }

  function buildSourceFormData(snapshot = null) {
    const activeSnapshot = snapshot || sourceSnapshot;
    const formData = new FormData();

    if (activeSnapshot?.mode === "file") {
      if (!activeSnapshot.file) {
        throw new Error(t.sourceRequired);
      }
      formData.append("file", activeSnapshot.file);
    } else {
      const text = activeSnapshot?.text || inlineText.trim();
      if (!text) {
        throw new Error(t.sourceRequired);
      }
      formData.append("text", text);
    }

    formData.append("system_language", systemLanguageFor(language));
    return formData;
  }

  async function handleGenerateQuestions(event) {
    event.preventDefault();

    if (mode === "file" && !selectedFile) {
      setError(t.sourceRequired);
      return;
    }

    if (mode === "text" && !inlineText.trim()) {
      setError(t.sourceRequired);
      return;
    }

    const snapshot =
      mode === "file"
        ? {
            mode,
            file: selectedFile,
            files: selectedFiles.length > 1 ? selectedFiles : [],
            sourceLabel:
              selectedFiles.length > 1
                ? `${selectedFiles.length} files`
                : selectedFile?.name || t.inputFile,
          }
        : {
            mode,
            text: inlineText.trim(),
            sourceLabel: t.inputText,
          };

    setIsGeneratingQuestions(true);
    setError("");
    clearGeneratedState();
    setSourceSnapshot(snapshot);

    try {

      if (mode === "file" && selectedFiles.length > 1) {
        const formData = new FormData();
        selectedFiles.forEach((file) => formData.append("files", file));
        formData.append("system_language", systemLanguageFor(language));

        const data = await postAnalyzerBatchFeature("generate-questions", formData);
        const parsedByIndex = {};
        for (const item of data?.items || []) {
          if (!item?.success) continue;
          const { result, sidecarQuestionsText } = normalizeAnalyzerPayload(item.response);
          const content =
            typeof result?.content === "string" && result.content.trim()
              ? result.content.trim()
              : String(sidecarQuestionsText || "").trim();
          const parsed = parseNumberedItems(content);
          if (parsed.length) parsedByIndex[item.index] = parsed;
        }

        setBatchQuestionResult(data);
        setBatchQuestionItemsByIndex(parsedByIndex);
        setAnswerDecision(Object.keys(parsedByIndex).length ? "pending" : "declined");
        return;
      }

      const formData = buildSourceFormData(snapshot);
      const data = await postAnalyzerFeature(
        "generate-questions",
        formData,
        true,
      );
      const { result, sidecarQuestionsText } = normalizeAnalyzerPayload(data);

      if (!result) {
        throw new Error("Backend returned no result.");
      }

      const content =
        typeof result.content === "string" && result.content.trim()
          ? result.content.trim()
          : String(sidecarQuestionsText || "").trim();
      const parsedQuestions = parseNumberedItems(content);

      setQuestionsText(content);
      setQuestionItems(parsedQuestions);
      setQuestionDownloadInfo(
        resultDownloadInfo(
          result,
          "generated-questions",
          outputExtension || "txt",
        ),
      );
      setAnswerDecision("pending");
    } catch (err) {
      setError(err?.message || t.questionsFailed);
      setSourceSnapshot(null);
    } finally {
      setIsGeneratingQuestions(false);
    }
  }

  async function handleGenerateAnswers() {
    if (!sourceSnapshot) {
      setError(t.sourceRequired);
      return;
    }

    if (isBatchAnswerFlow && !hasBatchQuestionItems) {
      setError(t.badQuestionList);
      return;
    }

    if (!isBatchAnswerFlow && !questionItems.length) {
      setError(t.badQuestionList);
      return;
    }

    setIsGeneratingAnswers(true);
    setError("");
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setBatchAnswerResult(null);
    setAnswerDecision("accepted");

    try {

      if (isBatchAnswerFlow) {
        const formData = new FormData();
        sourceSnapshot.files.forEach((file) => formData.append("files", file));

        const questionsByIndex = {};
        for (let index = 0; index < sourceSnapshot.files.length; index += 1) {
          const questionsForFile = batchQuestionItemsByIndex[index + 1];
          if (questionsForFile?.length) {
            questionsByIndex[String(index + 1)] = questionsForFile;
          }
        }

        formData.append("questions_json", JSON.stringify({ questions_by_index: questionsByIndex }));
        formData.append("system_language", systemLanguageFor(language));

        const data = await postAnalyzerBatchFeature("generate-answers", formData);
        setBatchAnswerResult(data);
        return;
      }

      const formData = buildSourceFormData(sourceSnapshot);
      formData.append("questions_json", JSON.stringify(questionItems));

      const data = await postAnalyzerFeature(
        "generate-answers",
        formData,
        true,
      );
      const { result } = normalizeAnalyzerPayload(data);

      if (!result) {
        throw new Error("Backend returned no result.");
      }

      if (typeof result.content === "string") {
        setAnswersText(result.content.trim());
      } else {
        setAnswerDownloadInfo(
          resultDownloadInfo(
            result,
            "generated-answers",
            outputExtension || "txt",
          ),
        );
      }
    } catch (err) {
      setError(err?.message || t.answersFailed);
    } finally {
      setIsGeneratingAnswers(false);
    }
  }

  function handleSkipAnswers() {
    setAnswerDecision("declined");
    setAnswersText("");
    setAnswerDownloadInfo(null);
    setError("");
  }

  return (
    <AppSidebarLayout>
      <div className="relative isolate min-h-screen overflow-hidden bg-[var(--app-bg)] text-[var(--app-text)]">
        <div className="absolute inset-0 bg-[var(--app-bg)]" />

        <div className="relative mx-auto max-w-6xl px-6 py-12 md:px-8 md:py-16">
          <button
            type="button"
            onClick={() => router.push("/")}
            className="mb-8 inline-flex items-center gap-2 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm app-text-muted backdrop-blur transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
          >
            <ArrowLeft className="h-4 w-4" />
            {common.back}
          </button>

          <section className="mb-10">
            <div className="inline-flex items-center gap-2 rounded-full border border-[var(--app-accent-border)] bg-[var(--app-accent-bg)] px-4 py-2 text-sm text-[var(--app-accent-text)]">
              <Sparkles className="h-4 w-4" />
              {t.badge}
            </div>
            <h1 className="mt-5 max-w-4xl text-4xl font-semibold tracking-tight app-text md:text-5xl">
              {t.title}
            </h1>
            <p className="mt-4 max-w-3xl text-base leading-7 app-text-muted">
              {t.description}
            </p>
          </section>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
            <section className="rounded-3xl border app-surface-strong p-6">
              <div className="mb-5 grid grid-cols-2 gap-2 rounded-2xl border border-[var(--app-border)] app-surface p-1">
                {[
                  ["file", t.fileMode],
                  ["text", t.textMode],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => handleModeChange(key)}
                    className={`rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                      mode === key
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : "app-text-muted hover:bg-neutral-100 dark:hover:bg-[#2d2d33]"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <form onSubmit={handleGenerateQuestions} className="space-y-5">
                {mode === "file" ? (
                  <UploadDropzone
                    t={t}
                    selectedFile={selectedFile}
                    selectedFiles={selectedFiles}
                    fileInputRef={fileInputRef}
                    onDrop={handleDrop}
                    onDragOver={handleDragOver}
                    onFileChange={handleFileChange}
                    onPick={() => fileInputRef.current?.click()}
                  />
                ) : (
                  <div className="rounded-3xl border app-surface p-5">
                    <label
                      className="text-sm font-semibold app-text"
                      htmlFor="inlineText"
                    >
                      {t.pasteTextLabel}
                    </label>
                    <textarea
                      id="inlineText"
                      value={inlineText}
                      onChange={(event) => {
                        setInlineText(event.target.value);
                        clearGeneratedState();
                      }}
                      placeholder={t.pasteTextPlaceholder}
                      rows={12}
                      className="mt-3 w-full resize-y rounded-2xl border border-[var(--app-border)] app-surface-strong px-4 py-3 text-sm app-text outline-none transition placeholder:text-[var(--app-text-soft)] focus:border-[var(--app-border-strong)]"
                    />
                    <p className="mt-2 text-xs app-text-muted">
                      {t.inlineTextTreatedAs}
                    </p>
                  </div>
                )}

                {error ? (
                  <div className="flex items-start gap-3 rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-sm text-red-100">
                    <XCircle className="mt-0.5 h-5 w-5 shrink-0" />
                    <p>{error}</p>
                  </div>
                ) : null}

                <button
                  type="submit"
                  disabled={!canGenerateQuestions}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isGeneratingQuestions ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <HelpCircle className="h-4 w-4" />
                  )}
                  {isGeneratingQuestions
                    ? t.generatingQuestions
                    : t.generateQuestions}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    setSelectedFile(null);
                    setSelectedFiles([]);
                    setInlineText("");
                    setError("");
                    clearGeneratedState();
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  className="inline-flex w-full items-center justify-center gap-2 rounded-2xl border app-surface px-5 py-3 text-sm font-semibold app-text transition hover:bg-neutral-100 dark:hover:bg-[#2d2d33]"
                >
                  <RotateCcw className="h-4 w-4" />
                  {t.resetFlow}
                </button>
              </form>

              <div className="mt-6 rounded-3xl border app-surface p-5">
                <div className="flex items-start gap-3">
                  <ShieldCheck className="mt-0.5 h-5 w-5 app-text-muted" />
                  <div>
                    <h3 className="text-sm font-semibold app-text">
                      {t.formatPolicy}
                    </h3>
                    <p className="mt-1 text-sm app-text-muted">
                      {t.policySubtitle}
                    </p>
                  </div>
                </div>
                <dl className="mt-4 grid gap-3 text-sm">
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.allowedUploadsLabel}</dt>
                    <dd className="text-right app-text-muted">.pdf, .docx</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.inlineInputLabel}</dt>
                    <dd className="text-right app-text-muted">
                      {t.inlineInputValue}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">
                      {t.rejectedAutomaticallyLabel}
                    </dt>
                    <dd className="text-right app-text-muted">
                      {REJECTED_EXTENSIONS.join(", ")}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="app-text-soft">{t.outputRuleLabel}</dt>
                    <dd className="max-w-xs text-right app-text-muted">
                      {t.outputRuleValue}
                    </dd>
                  </div>
                </dl>
              </div>
            </section>

            <div className="space-y-6">
<BatchResultPanel result={batchQuestionResult} title="Batch generated questions" />

              <BatchResultPanel result={batchAnswerResult} title="Batch generated answers" />

              {hasBatchQuestionItems ? (
                <section className="rounded-3xl border app-surface p-4">
                  <p className="text-sm font-semibold app-text">
                    Batch questions ready
                  </p>
                  <p className="mt-1 text-sm app-text-muted">
                    {batchQuestionCount} questions parsed across {batchQuestionEntries.length} file{batchQuestionEntries.length === 1 ? "" : "s"}.
                  </p>
                </section>
              ) : null}

              <TextOutput
                title={t.questionsOutputTitle}
                empty={t.previewEmpty}
                content={questionsText}
                icon={ListChecks}
              />

              {questionDownloadInfo ? (
                <DownloadCard
                  info={questionDownloadInfo}
                  label={t.downloadQuestionsFile}
                />
              ) : null}

              {shouldShowAnswerPrompt ? (
                <section className="rounded-3xl border app-surface-strong p-6">
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border app-surface">
                      <MessageCircleQuestion className="h-5 w-5 app-text-muted" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <h2 className="text-lg font-semibold app-text">
                        {t.answerPromptTitle}
                      </h2>
                      <p className="mt-1 text-sm app-text-muted">
                        {t.answerPromptDescription}
                      </p>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs app-text-soft">
                        <span className="rounded-full border border-[var(--app-border)] app-surface px-3 py-1">
                          {t.detectedSource}:{" "}
                          {sourceSnapshot?.sourceLabel || "—"}
                        </span>
                        <span className="rounded-full border border-[var(--app-border)] app-surface px-3 py-1">
                          {t.questionCount}: {displayedQuestionCount}
                        </span>
                      </div>
                    </div>
                  </div>

                  {!isBatchAnswerFlow && questionsText && !questionItems.length ? (
                    <div className="mt-5 flex items-start gap-3 rounded-2xl border border-amber-400/30 bg-amber-400/10 p-4 text-sm text-amber-100">
                      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
                      <div>
                        <p className="font-semibold">
                          {t.cannotGenerateAnswersTitle}
                        </p>
                        <p className="mt-1 text-amber-100/80">
                          {t.cannotGenerateAnswersDescription}
                        </p>
                      </div>
                    </div>
                  ) : null}

                  <div className="mt-5 grid gap-2 md:grid-cols-2">
                    <button
                      type="button"
                      onClick={handleSkipAnswers}
                      disabled={isGeneratingAnswers}
                      className="rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#2d2d33]"
                    >
                      {t.skipAnswers}
                    </button>
                    <button
                      type="button"
                      onClick={handleGenerateAnswers}
                      disabled={!canGenerateAnswers}
                      className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isGeneratingAnswers ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Sparkles className="h-4 w-4" />
                      )}
                      {isGeneratingAnswers ? t.generatingAnswers : t.yes}
                    </button>
                  </div>

                  {answerDecision === "declined" ? (
                    <div className="mt-5 rounded-2xl border border-[var(--app-border)] app-surface p-4">
                      <p className="text-sm font-semibold app-text">
                        {t.declinedTitle}
                      </p>
                      <p className="mt-1 text-sm app-text-muted">
                        {t.declinedDescription}
                      </p>
                    </div>
                  ) : null}
                </section>
              ) : null}

              <TextOutput
                title={t.answersOutputTitle}
                empty={t.answersPreviewEmpty}
                content={answersText}
                icon={CheckCircle2}
              />

              {answerDownloadInfo ? (
                <DownloadCard
                  info={answerDownloadInfo}
                  label={t.downloadAnswersFile}
                />
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </AppSidebarLayout>
  );
}
