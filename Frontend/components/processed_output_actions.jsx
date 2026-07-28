"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Loader2, Printer, Share2, Users, X } from "lucide-react";
import { useAccount } from "@/components/account_provider";
import {
  buildAnalyzerArtifactUrl,
  createConversation,
  getOrganization,
  getOrganizationConversations,
  normalizeAnalyzerArtifactUrl,
  sendConversationAttachment,
} from "@/lib/api_client";
import { validateTeamAttachments } from "@/lib/team_attachment_policy";

const EMPTY_ARTIFACTS = Object.freeze([]);
const RESULT_CONTAINER_KEYS = [
  "response",
  "analyzer_response",
  "analyzerResponse",
  "data",
  "result",
  "output",
  "batch_result",
  "batchResult",
  "artifact",
  "output_artifact",
  "outputArtifact",
  "pdf_artifact",
  "pdfArtifact",
  "download_artifact",
  "downloadArtifact",
  "result_artifact",
  "resultArtifact",
  "archive_file",
  "archiveFile",
  "preview_artifact",
  "previewArtifact",
];

const RESULT_ARRAY_KEYS = [
  "items",
  "artifacts",
  "output_artifacts",
  "outputArtifacts",
  "downloads",
  "downloadables",
  "outputs",
  "files",
  "output_files",
  "outputFiles",
];

const RESULT_CONTENT_KEYS = [
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

const OFFICE_EXTENSIONS = new Set([".docx", ".xlsx", ".xls", ".ods", ".pptx"]);
const NON_PRINTABLE_EXTENSIONS = new Set([
  ".zip",
  ".7z",
  ".rar",
  ".tar",
  ".gz",
  ".mp3",
  ".mp4",
  ".mov",
  ".mkv",
]);
const PRINTABLE_TEXT_EXTENSIONS = new Set([
  ".txt",
  ".csv",
  ".md",
  ".json",
  ".xml",
  ".html",
  ".htm",
]);
const MAX_NATIVE_SHARE_FILE_BYTES = 200 * 1024 * 1024;
const MAX_NATIVE_SHARE_TOTAL_BYTES = 500 * 1024 * 1024;

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function firstText(values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function safeFilename(value, fallback = "processed-output") {
  const normalized = String(value || "")
    .normalize("NFKC")
    .replace(/[\\/\u0000-\u001f\u007f]/gu, "_")
    .trim()
    .slice(0, 180);
  return normalized || fallback;
}

function filenameFromUrl(value) {
  try {
    const parsed = new URL(String(value || ""), "https://redocx.invalid");
    const queryName = parsed.searchParams.get("download_name");
    if (queryName) return safeFilename(queryName);
    return safeFilename(
      decodeURIComponent(
        parsed.pathname.split("/").filter(Boolean).at(-1) || "",
      ),
      "",
    );
  } catch {
    return "";
  }
}

function filenameFromObject(value, fallback = "") {
  if (!isObject(value)) return fallback;
  return (
    firstText([
      value.filename,
      value.file_name,
      value.fileName,
      value.original_filename,
      value.originalFilename,
      value.original_artifact_name,
      value.originalArtifactName,
      value.artifact_name,
      value.artifactName,
      value.output_filename,
      value.outputFilename,
      value.name,
    ]) || fallback
  );
}

function mimeTypeFromObject(value, fallback = "") {
  if (!isObject(value)) return fallback;
  return (
    firstText([
      value.mimeType,
      value.mime_type,
      value.contentType,
      value.content_type,
      value.mediaType,
      value.media_type,
    ]) || fallback
  );
}

function extensionForFilename(filename) {
  const normalized = String(filename || "").toLowerCase();
  const dot = normalized.lastIndexOf(".");
  return dot >= 0 ? normalized.slice(dot) : "";
}

function inferMimeType(filename) {
  const extension = extensionForFilename(filename);
  return (
    {
      ".pdf": "application/pdf",
      ".docx":
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      ".xlsx":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      ".xls": "application/vnd.ms-excel",
      ".pptx":
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      ".txt": "text/plain",
      ".csv": "text/csv",
      ".md": "text/markdown",
      ".json": "application/json",
      ".xml": "application/xml",
      ".png": "image/png",
      ".jpg": "image/jpeg",
      ".jpeg": "image/jpeg",
    }[extension] || "application/octet-stream"
  );
}

function normalizeArtifactDescriptor(value, fallbackFilename = "") {
  if (!value) return null;

  if (typeof value === "string") {
    const url = normalizeAnalyzerArtifactUrl(value);
    if (!url) return null;
    const filename =
      filenameFromUrl(url) ||
      safeFilename(fallbackFilename, "processed-output");
    return {
      url,
      filename,
      mimeType: inferMimeType(filename),
      text: "",
    };
  }

  if (!isObject(value)) return null;

  const storageKey = firstText([
    value.storage_key,
    value.storageKey,
    value.key,
  ]);
  const rawUrl = firstText([
    value.download_url,
    value.downloadUrl,
    value.url,
    value.href,
  ]);
  const url = normalizeAnalyzerArtifactUrl(
    rawUrl || (storageKey ? buildAnalyzerArtifactUrl(storageKey) : ""),
  );

  if (!url) return null;

  const filename = safeFilename(
    filenameFromObject(value, fallbackFilename) || filenameFromUrl(url),
    "processed-output",
  );
  return {
    url,
    filename,
    mimeType: mimeTypeFromObject(value, inferMimeType(filename)),
    text: "",
  };
}

function collectResultArtifacts(result) {
  const entries = [];
  const seenObjects = new Set();
  const seenUrls = new Set();

  function visit(value, fallbackFilename = "", depth = 0) {
    if (value == null || depth > 7) return;

    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, fallbackFilename, depth + 1));
      return;
    }
    if (!isObject(value) || seenObjects.has(value)) return;
    seenObjects.add(value);

    const ownFilename = filenameFromObject(value, fallbackFilename);
    const descriptor = normalizeArtifactDescriptor(value, ownFilename);
    if (descriptor && !seenUrls.has(descriptor.url)) {
      seenUrls.add(descriptor.url);
      entries.push(descriptor);
    }

    for (const key of RESULT_CONTAINER_KEYS) {
      visit(value[key], ownFilename, depth + 1);
    }
    for (const key of RESULT_ARRAY_KEYS) {
      if (Array.isArray(value[key])) {
        value[key].forEach((item) => visit(item, ownFilename, depth + 1));
      }
    }
  }

  visit(result);
  return entries;
}

function collectResultTextDescriptors(result, fallbackTitle) {
  const entries = [];
  const seenObjects = new Set();
  const seenContent = new Set();

  function visit(value, fallbackFilename = "", depth = 0) {
    if (value == null || depth > 7) return;
    if (typeof value === "string") return;
    if (Array.isArray(value)) {
      value.forEach((item) => visit(item, fallbackFilename, depth + 1));
      return;
    }
    if (!isObject(value) || seenObjects.has(value)) return;
    seenObjects.add(value);

    const ownFilename = filenameFromObject(value, fallbackFilename);
    const content = firstText(RESULT_CONTENT_KEYS.map((key) => value[key]));
    if (content && !seenContent.has(content)) {
      seenContent.add(content);
      const sourceName = safeFilename(
        ownFilename ||
          fallbackTitle ||
          `processed-output-${entries.length + 1}`,
      );
      const stem = sourceName.replace(/\.[^.]+$/u, "") || "processed-output";
      entries.push({
        url: "",
        filename: `${stem}.txt`,
        mimeType: "text/plain;charset=utf-8",
        text: content,
      });
    }

    for (const key of RESULT_CONTAINER_KEYS) {
      visit(value[key], ownFilename, depth + 1);
    }
    for (const key of RESULT_ARRAY_KEYS) {
      if (Array.isArray(value[key])) {
        value[key].forEach((item) => visit(item, ownFilename, depth + 1));
      }
    }
  }

  visit(result);
  return entries;
}

function buildOutputDescriptors({
  artifactUrl,
  filename,
  mimeType,
  artifacts,
  result,
  textContent,
  textFilename,
  title,
}) {
  const entries = [];
  const seenUrls = new Set();

  function add(value, fallbackFilename = "") {
    const descriptor = normalizeArtifactDescriptor(value, fallbackFilename);
    if (!descriptor || seenUrls.has(descriptor.url)) return;
    seenUrls.add(descriptor.url);
    entries.push(descriptor);
  }

  if (artifactUrl) {
    add(
      {
        download_url: artifactUrl,
        filename,
        content_type: mimeType,
      },
      filename,
    );
  }
  for (const artifact of Array.from(artifacts || [])) {
    add(artifact, filename);
  }
  for (const descriptor of collectResultArtifacts(result)) {
    add(descriptor, descriptor.filename);
  }

  if (entries.length) return entries;

  const explicitText =
    typeof textContent === "string" ? textContent.trim() : "";
  if (explicitText) {
    const requestedTextFilename = safeFilename(
      textFilename || `${safeFilename(title, "processed-output")}.txt`,
      "processed-output.txt",
    );
    const textExtension = extensionForFilename(requestedTextFilename);
    const normalizedTextFilename = PRINTABLE_TEXT_EXTENSIONS.has(textExtension)
      ? requestedTextFilename
      : `${requestedTextFilename.replace(/\.[^.]+$/u, "") || "processed-output"}.txt`;
    return [
      {
        url: "",
        filename: normalizedTextFilename,
        mimeType: "text/plain;charset=utf-8",
        text: explicitText,
      },
    ];
  }

  return collectResultTextDescriptors(result, title);
}

function fileNameFromContentDisposition(value) {
  const header = String(value || "");
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/iu)?.[1];
  if (encoded) {
    try {
      return safeFilename(decodeURIComponent(encoded));
    } catch {
      // Fall through to the quoted ASCII filename.
    }
  }
  return safeFilename(header.match(/filename="([^"]+)"/iu)?.[1] || "", "");
}

async function descriptorToFile(descriptor, signal) {
  if (descriptor.text) {
    return new File([descriptor.text], descriptor.filename, {
      type: descriptor.mimeType || "text/plain;charset=utf-8",
    });
  }

  const response = await fetch(descriptor.url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: { Accept: "application/octet-stream,*/*;q=0.8" },
    signal,
  });
  if (!response.ok) {
    throw new Error(`Could not prepare ${descriptor.filename} for sharing.`);
  }

  const declaredSize = Number(response.headers.get("content-length") || 0);
  if (
    Number.isFinite(declaredSize) &&
    declaredSize > MAX_NATIVE_SHARE_FILE_BYTES
  ) {
    throw new Error(`${descriptor.filename} is too large for native sharing.`);
  }

  const blob = await response.blob();
  if (blob.size > MAX_NATIVE_SHARE_FILE_BYTES) {
    throw new Error(`${descriptor.filename} is too large for native sharing.`);
  }

  const responseFilename = fileNameFromContentDisposition(
    response.headers.get("content-disposition"),
  );
  return new File([blob], responseFilename || descriptor.filename, {
    type:
      blob.type || descriptor.mimeType || inferMimeType(descriptor.filename),
  });
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `processed-share:${crypto.randomUUID()}`;
  }
  return `processed-share:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function isPrintableDescriptor(descriptor) {
  const extension = extensionForFilename(descriptor.filename);
  const mimeType = String(descriptor.mimeType || "").toLowerCase();
  if (NON_PRINTABLE_EXTENSIONS.has(extension)) return false;
  if (
    extension === ".pdf" ||
    OFFICE_EXTENSIONS.has(extension) ||
    PRINTABLE_TEXT_EXTENSIONS.has(extension)
  ) {
    return true;
  }
  return (
    mimeType === "application/pdf" ||
    mimeType.startsWith("image/") ||
    mimeType.startsWith("text/")
  );
}

function printPreviewUrl(descriptor) {
  const normalized = normalizeAnalyzerArtifactUrl(descriptor.url);
  if (!normalized) return "";

  try {
    const parsed = new URL(normalized, window.location.origin);
    if (
      parsed.origin === window.location.origin &&
      (parsed.pathname.startsWith("/api/analyzer/artifacts/") ||
        parsed.pathname.startsWith("/api/v1/analyzer/artifacts/"))
    ) {
      parsed.searchParams.set("print_preview", "true");
      parsed.searchParams.set("disposition", "inline");
      parsed.searchParams.set("download_name", descriptor.filename);
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
  } catch {
    return normalized;
  }

  return normalized;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function textPrintBlob(text, title) {
  const document = `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>${escapeHtml(title)}</title>
    <style>
      @page { size: A4; margin: 16mm; }
      body { color: #111827; font: 11pt/1.5 Arial, Helvetica, sans-serif; }
      pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
    </style>
  </head>
  <body><pre>${escapeHtml(text)}</pre></body>
</html>`;
  return new Blob([document], { type: "text/html;charset=utf-8" });
}

async function printableBlob(descriptor) {
  if (descriptor.text) {
    return textPrintBlob(descriptor.text, descriptor.filename);
  }

  const response = await fetch(printPreviewUrl(descriptor), {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    headers: {
      Accept: "application/pdf,text/html,image/*,text/plain,*/*;q=0.5",
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload?.detail?.message ||
        `Could not prepare ${descriptor.filename} for printing.`,
    );
  }

  const blob = await response.blob();
  const extension = extensionForFilename(descriptor.filename);
  const type = String(blob.type || "").toLowerCase();
  if (
    OFFICE_EXTENSIONS.has(extension) &&
    !(type.includes("application/pdf") || type.includes("text/html"))
  ) {
    throw new Error(
      `The print service did not render ${descriptor.filename} into a safe preview.`,
    );
  }

  if (PRINTABLE_TEXT_EXTENSIONS.has(extension) && !type.includes("text/html")) {
    return textPrintBlob(await blob.text(), descriptor.filename);
  }
  return blob;
}

function renderPrintLoading(popup, filename) {
  if (!popup) return;
  popup.document.open();
  popup.document.write(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Preparing print preview</title>
    <style>
      body {
        min-height: 100vh;
        margin: 0;
        display: grid;
        place-items: center;
        background: #111827;
        color: #f9fafb;
        font: 16px/1.5 Arial, sans-serif;
      }
      div { max-width: 32rem; padding: 2rem; text-align: center; }
    </style>
  </head>
  <body><div>Preparing ${escapeHtml(filename)} for the system print dialog…</div></body>
</html>`);
  popup.document.close();
}

function renderPrintError(popup, message) {
  if (!popup || popup.closed) return;
  popup.document.body.replaceChildren();
  const container = popup.document.createElement("div");
  container.style.cssText =
    "max-width:42rem;margin:10vh auto;padding:2rem;font:16px/1.5 Arial,sans-serif;color:#991b1b;";
  container.textContent = message;
  popup.document.body.appendChild(container);
}

function mountPrintableFrame({ popup, objectUrl, onError }) {
  const usesPopup = Boolean(popup && !popup.closed);
  const hostDocument = usesPopup ? popup.document : document;
  if (usesPopup) {
    hostDocument.body.replaceChildren();
    hostDocument.body.style.margin = "0";
  }

  const frame = hostDocument.createElement("iframe");
  frame.title = "Print preview";
  frame.style.cssText = usesPopup
    ? "display:block;width:100%;height:100vh;border:0;background:white;"
    : "position:fixed;width:1px;height:1px;right:0;bottom:0;border:0;opacity:0;pointer-events:none;";

  let printed = false;
  const cleanup = () => {
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    if (!usesPopup && frame.isConnected) {
      window.setTimeout(() => frame.remove(), 60_000);
    }
  };

  frame.onload = () => {
    if (printed) return;
    printed = true;
    window.setTimeout(() => {
      try {
        frame.contentWindow?.focus();
        frame.contentWindow?.print();
        cleanup();
      } catch {
        cleanup();
        onError(
          "The browser opened the preview but could not start its print dialog.",
        );
      }
    }, 150);
  };
  frame.onerror = () => {
    cleanup();
    onError("The browser could not display the prepared print preview.");
  };
  frame.src = objectUrl;
  hostDocument.body.appendChild(frame);
}

function downloadFiles(files) {
  for (const file of files) {
    const objectUrl = URL.createObjectURL(file);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = file.name;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  }
}

function partitionTeamShareFiles(files) {
  const accepted = [];
  const skipped = [];

  for (const file of files) {
    try {
      validateTeamAttachments([file]);
      accepted.push(file);
    } catch {
      skipped.push(file);
    }
  }

  return { accepted, skipped };
}

function memberLabel(member) {
  return firstText([member?.name, member?.email]) || "Organization member";
}

export default function ProcessedOutputActions({
  artifactUrl = "",
  filename = "",
  mimeType = "",
  artifacts = EMPTY_ARTIFACTS,
  result = null,
  textContent = "",
  textFilename = "",
  title = "Processed output",
}) {
  const { user, entitlement } = useAccount();
  // Several feature pages construct their artifact arrays inline. Serialize
  // the normalized descriptors so equivalent renders retain stable identity
  // and do not restart file preparation.
  const serializedDescriptors = JSON.stringify(
    buildOutputDescriptors({
      artifactUrl,
      filename,
      mimeType,
      artifacts,
      result,
      textContent,
      textFilename,
      title,
    }),
  );
  const descriptors = useMemo(
    () => JSON.parse(serializedDescriptors),
    [serializedDescriptors],
  );
  const printableDescriptors = useMemo(
    () => descriptors.filter(isPrintableDescriptor),
    [descriptors],
  );
  const canShareWithOrganization =
    entitlement?.source === "organization" &&
    entitlement?.status === "active" &&
    entitlement?.is_paid === true &&
    ["business", "enterprise"].includes(entitlement?.plan) &&
    Number(entitlement?.organization_id || 0) > 0;

  const [preparedFiles, setPreparedFiles] = useState([]);
  const [preparingFiles, setPreparingFiles] = useState(false);
  const [prepareError, setPrepareError] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [teamDialogOpen, setTeamDialogOpen] = useState(false);
  const [teamMembers, setTeamMembers] = useState([]);
  const [selectedMemberIds, setSelectedMemberIds] = useState([]);
  const [organizationGroup, setOrganizationGroup] = useState(null);
  const [organizationGroupSelected, setOrganizationGroupSelected] =
    useState(false);
  const [teamLoading, setTeamLoading] = useState(false);
  const [printDialogOpen, setPrintDialogOpen] = useState(false);
  const teamFilePartition = useMemo(
    () => partitionTeamShareFiles(preparedFiles),
    [preparedFiles],
  );

  useEffect(() => {
    if (!descriptors.length) {
      setPreparedFiles([]);
      setPrepareError("");
      setPreparingFiles(false);
      return undefined;
    }

    const controller = new AbortController();
    setPreparingFiles(true);
    setPrepareError("");

    Promise.all(
      descriptors.map((descriptor) =>
        descriptorToFile(descriptor, controller.signal),
      ),
    )
      .then((files) => {
        const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
        if (totalBytes > MAX_NATIVE_SHARE_TOTAL_BYTES) {
          throw new Error(
            "The processed outputs are too large to share in one native share action.",
          );
        }
        setPreparedFiles(files);
      })
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setPreparedFiles([]);
        setPrepareError(
          error?.message ||
            "Could not prepare the processed output for sharing.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreparingFiles(false);
      });

    return () => controller.abort();
  }, [descriptors]);

  useEffect(() => {
    if (!teamDialogOpen || !canShareWithOrganization) return undefined;

    const controller = new AbortController();
    setTeamLoading(true);
    setSelectedMemberIds([]);
    setOrganizationGroup(null);
    setOrganizationGroupSelected(false);
    setNotice("");

    Promise.all([
      getOrganization(entitlement.organization_id, {
        signal: controller.signal,
      }),
      getOrganizationConversations(entitlement.organization_id, {
        signal: controller.signal,
      }),
    ])
      .then(([organizationData, conversationData]) => {
        const members = Array.from(organizationData?.members || [])
          .filter(
            (member) =>
              member?.status === "active" &&
              String(member?.user_id || "") !== String(user?.id || ""),
          )
          .sort((left, right) =>
            memberLabel(left).localeCompare(memberLabel(right)),
          );
        setTeamMembers(members);

        const group =
          Array.from(conversationData?.conversations || [])
            .filter(
              (conversation) =>
                conversation?.type === "group" &&
                conversation?.status === "active",
            )
            .sort((left, right) => {
              const leftTime = new Date(
                left?.updated_at || left?.created_at || 0,
              ).getTime();
              const rightTime = new Date(
                right?.updated_at || right?.created_at || 0,
              ).getTime();
              return rightTime - leftTime;
            })[0] || null;
        setOrganizationGroup(group);
      })
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setTeamMembers([]);
        setOrganizationGroup(null);
        setNotice(
          error?.message || "Could not load organization sharing destinations.",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setTeamLoading(false);
      });

    return () => controller.abort();
  }, [
    canShareWithOrganization,
    entitlement?.organization_id,
    teamDialogOpen,
    user?.id,
  ]);

  if (!descriptors.length) return null;

  const hasSelectedGroupDestination = Boolean(
    organizationGroupSelected && organizationGroup?.id,
  );
  const selectedOrganizationDestinationCount =
    selectedMemberIds.length + (hasSelectedGroupDestination ? 1 : 0);

  async function handleThirdPartyShare() {
    if (busy || preparingFiles || !preparedFiles.length) return;

    setBusy("native-share");
    setNotice("");
    try {
      if (typeof navigator.share !== "function") {
        downloadFiles(preparedFiles);
        setNotice(
          "This browser does not provide an app share sheet. The files were downloaded so you can attach them to any app.",
        );
        return;
      }

      const payload = {
        title,
        files: preparedFiles,
      };
      const canShareFiles =
        typeof navigator.canShare !== "function" ||
        navigator.canShare({ files: preparedFiles });

      if (canShareFiles) {
        // This call intentionally happens before any await so browser transient
        // user activation is preserved for the native share sheet.
        await navigator.share(payload);
        setNotice("The processed output was handed to the selected app.");
        return;
      }

      const singleTextDescriptor =
        descriptors.length === 1 ? descriptors[0] : null;
      if (singleTextDescriptor?.text) {
        await navigator.share({
          title,
          text: singleTextDescriptor.text,
        });
        setNotice("The processed text was handed to the selected app.");
        return;
      }

      downloadFiles(preparedFiles);
      setNotice(
        "This browser cannot pass files to its share sheet. The files were downloaded so you can attach them to any app.",
      );
    } catch (error) {
      if (error?.name === "AbortError") return;
      if (error?.name === "NotAllowedError") {
        downloadFiles(preparedFiles);
        setNotice(
          "The browser blocked its app share sheet. The files were downloaded so you can attach them without losing the output.",
        );
        return;
      }
      setNotice(error?.message || "Could not share the processed output.");
    } finally {
      setBusy("");
    }
  }

  async function handlePrint(descriptor) {
    if (busy) return;

    setBusy(`print:${descriptor.filename}`);
    setNotice("");
    setPrintDialogOpen(false);

    const popup = window.open(
      "",
      `redocx-print-${Date.now()}`,
      "width=1100,height=800",
    );
    renderPrintLoading(popup, descriptor.filename);

    try {
      const blob = await printableBlob(descriptor);
      const objectUrl = URL.createObjectURL(blob);
      mountPrintableFrame({
        popup,
        objectUrl,
        onError: (message) => {
          setNotice(message);
          renderPrintError(popup, message);
        },
      });
    } catch (error) {
      const message =
        error?.message ||
        "Could not prepare the processed output for printing.";
      setNotice(message);
      renderPrintError(popup, message);
    } finally {
      setBusy("");
    }
  }

  function handlePrintButton() {
    if (printableDescriptors.length === 1) {
      void handlePrint(printableDescriptors[0]);
      return;
    }
    setNotice("");
    setPrintDialogOpen(true);
  }

  function toggleMember(userId) {
    setSelectedMemberIds((current) =>
      current.includes(userId)
        ? current.filter((value) => value !== userId)
        : [...current, userId],
    );
  }

  async function handleOrganizationShare() {
    if (
      busy ||
      !canShareWithOrganization ||
      selectedOrganizationDestinationCount < 1 ||
      !teamFilePartition.accepted.length
    ) {
      return;
    }

    setBusy("team-share");
    setNotice("");
    const successes = [];
    const failures = [];

    try {
      validateTeamAttachments(teamFilePartition.accepted);
      const destinations = [
        ...selectedMemberIds.map((recipientUserId) => ({
          type: "member",
          recipientUserId,
          conversationId: null,
        })),
        ...(hasSelectedGroupDestination
          ? [
              {
                type: "group",
                recipientUserId: null,
                conversationId: organizationGroup.id,
              },
            ]
          : []),
      ];

      for (const destination of destinations) {
        try {
          let conversationId = destination.conversationId;
          if (destination.type === "member") {
            const data = await createConversation(entitlement.organization_id, {
              type: "dm",
              member_user_ids: [destination.recipientUserId],
            });
            conversationId = data?.conversation?.id;
          }

          if (!conversationId) {
            throw new Error("Could not open the secure team conversation.");
          }

          await sendConversationAttachment(
            conversationId,
            teamFilePartition.accepted,
            {
              caption: `Shared processed output: ${title}`,
              clientMessageId: createClientMessageId(),
            },
          );
          successes.push(destination);
        } catch (error) {
          failures.push({
            destination,
            message:
              error?.message ||
              "Could not share with this organization destination.",
          });
        }
      }

      if (!successes.length) {
        throw new Error(
          failures[0]?.message ||
            "Could not share the processed output with the selected members.",
        );
      }

      setTeamDialogOpen(false);
      setSelectedMemberIds([]);
      setOrganizationGroupSelected(false);
      setNotice(
        failures.length
          ? `Shared securely to ${successes.length} destination${
              successes.length === 1 ? "" : "s"
            }; ${failures.length} delivery failed.`
          : `Shared securely to ${successes.length} organization destination${
              successes.length === 1 ? "" : "s"
            }.${
              teamFilePartition.skipped.length
                ? ` ${teamFilePartition.skipped.length} output${
                    teamFilePartition.skipped.length === 1 ? "" : "s"
                  } not supported by secure team attachments ${
                    teamFilePartition.skipped.length === 1 ? "was" : "were"
                  } left out.`
                : ""
            }`,
      );
    } catch (error) {
      setNotice(
        error?.message ||
          "Could not share the processed output with the organization.",
      );
    } finally {
      setBusy("");
    }
  }

  const nativeShareDisabled =
    preparingFiles ||
    Boolean(prepareError) ||
    !preparedFiles.length ||
    Boolean(busy);
  const printBusy = busy.startsWith("print:");

  return (
    <>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleThirdPartyShare}
          disabled={nativeShareDisabled}
          className="inline-flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-surface-strong)] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {preparingFiles || busy === "native-share" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Share2 className="h-4 w-4" />
          )}
          Share to app
        </button>

        <button
          type="button"
          onClick={handlePrintButton}
          disabled={!printableDescriptors.length || Boolean(busy)}
          className="inline-flex items-center gap-2 rounded-xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-surface-strong)] disabled:cursor-not-allowed disabled:opacity-60"
          title={
            printableDescriptors.length
              ? "Open the browser's system print dialog"
              : "This output type cannot be printed safely"
          }
        >
          {printBusy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Printer className="h-4 w-4" />
          )}
          Print
        </button>

        {canShareWithOrganization ? (
          <button
            type="button"
            onClick={() => {
              setPrintDialogOpen(false);
              setTeamDialogOpen(true);
            }}
            disabled={nativeShareDisabled || !teamFilePartition.accepted.length}
            title={
              teamFilePartition.accepted.length
                ? "Share securely with members of this organization"
                : "None of these outputs is supported by secure team attachments"
            }
            className="inline-flex items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Users className="h-4 w-4" />
            Share with team
          </button>
        ) : null}
      </div>

      {prepareError ? (
        <p className="mt-2 text-sm text-red-300" role="alert">
          {prepareError}
        </p>
      ) : null}
      {notice ? (
        <p className="mt-2 text-sm app-text-muted" aria-live="polite">
          {notice}
        </p>
      ) : null}

      {printDialogOpen ? (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="processed-print-title"
        >
          <section className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  id="processed-print-title"
                  className="text-lg font-semibold app-text"
                >
                  Choose a file to print
                </h2>
                <p className="mt-1 text-sm app-text-muted">
                  Each file opens in the browser&apos;s system print dialog.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setPrintDialogOpen(false)}
                className="rounded-xl border border-[var(--app-border)] p-2 app-text"
                aria-label="Close print chooser"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 space-y-2">
              {printableDescriptors.map((descriptor) => (
                <button
                  key={`${descriptor.url}:${descriptor.filename}`}
                  type="button"
                  onClick={() => void handlePrint(descriptor)}
                  className="flex w-full items-center justify-between gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-left app-text"
                >
                  <span className="min-w-0 truncate text-sm font-semibold">
                    {descriptor.filename}
                  </span>
                  <Printer className="h-4 w-4 shrink-0" />
                </button>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      {teamDialogOpen ? (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="processed-team-share-title"
        >
          <section className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-3xl border border-[var(--app-border)] bg-[var(--app-surface-strong)] p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  id="processed-team-share-title"
                  className="text-lg font-semibold app-text"
                >
                  Share within{" "}
                  {entitlement?.organization_name || "organization"}
                </h2>
                <p className="mt-1 text-sm app-text-muted">
                  Choose individual member DMs, the existing organization group
                  chat, or both. Every destination is restricted to this exact
                  organization.
                </p>
                {teamFilePartition.skipped.length ? (
                  <p className="mt-1 text-xs text-amber-300">
                    {teamFilePartition.skipped.length} output
                    {teamFilePartition.skipped.length === 1 ? "" : "s"} cannot
                    be sent through secure team attachments and will be left
                    out.
                  </p>
                ) : null}
              </div>
              <button
                type="button"
                onClick={() => setTeamDialogOpen(false)}
                disabled={busy === "team-share"}
                className="rounded-xl border border-[var(--app-border)] p-2 app-text disabled:opacity-60"
                aria-label="Close team share"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 space-y-2">
              {teamLoading ? (
                <p className="flex items-center gap-2 rounded-2xl border border-[var(--app-border)] p-4 text-sm app-text-muted">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading organization destinations…
                </p>
              ) : (
                <>
                  <p className="px-1 text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                    Organization group
                  </p>
                  {organizationGroup ? (
                    <button
                      type="button"
                      onClick={() =>
                        setOrganizationGroupSelected((current) => !current)
                      }
                      disabled={busy === "team-share"}
                      aria-pressed={organizationGroupSelected}
                      className="flex w-full items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-left disabled:opacity-60"
                    >
                      <span
                        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                          organizationGroupSelected
                            ? "border-emerald-400 bg-emerald-400 text-black"
                            : "border-[var(--app-border)]"
                        }`}
                      >
                        {organizationGroupSelected ? (
                          <Check className="h-3.5 w-3.5" />
                        ) : null}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold app-text">
                          {organizationGroup.name ||
                            `${entitlement?.organization_name || "Organization"} Team Chat`}
                        </span>
                        <span className="block truncate text-xs app-text-muted">
                          Group chat ·{" "}
                          {Array.from(organizationGroup.member_user_ids || [])
                            .length || "All"}{" "}
                          active members
                        </span>
                      </span>
                    </button>
                  ) : (
                    <p className="rounded-2xl border border-[var(--app-border)] p-4 text-sm app-text-muted">
                      No active organization group chat has been created.
                    </p>
                  )}

                  <p className="px-1 pt-3 text-xs font-semibold uppercase tracking-[0.14em] app-text-soft">
                    Individual members
                  </p>
                  {teamMembers.length ? (
                    teamMembers.map((member) => {
                      const selected = selectedMemberIds.includes(
                        member.user_id,
                      );
                      return (
                        <button
                          key={member.user_id}
                          type="button"
                          onClick={() => toggleMember(member.user_id)}
                          disabled={busy === "team-share"}
                          aria-pressed={selected}
                          className="flex w-full items-center gap-3 rounded-2xl border border-[var(--app-border)] bg-[var(--app-surface)] px-4 py-3 text-left disabled:opacity-60"
                        >
                          <span
                            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
                              selected
                                ? "border-emerald-400 bg-emerald-400 text-black"
                                : "border-[var(--app-border)]"
                            }`}
                          >
                            {selected ? (
                              <Check className="h-3.5 w-3.5" />
                            ) : null}
                          </span>
                          <span className="min-w-0">
                            <span className="block truncate text-sm font-semibold app-text">
                              {memberLabel(member)}
                            </span>
                            {member.email &&
                            member.email !== memberLabel(member) ? (
                              <span className="block truncate text-xs app-text-muted">
                                {member.email}
                              </span>
                            ) : null}
                          </span>
                        </button>
                      );
                    })
                  ) : (
                    <p className="rounded-2xl border border-[var(--app-border)] p-4 text-sm app-text-muted">
                      No other active organization members are available.
                    </p>
                  )}
                </>
              )}
            </div>

            {hasSelectedGroupDestination && selectedMemberIds.length ? (
              <p className="mt-3 text-xs text-amber-300">
                The group will receive one copy, and each selected member will
                also receive a separate copy in their direct conversation.
              </p>
            ) : null}

            {notice ? (
              <p className="mt-3 text-sm app-text-muted" aria-live="polite">
                {notice}
              </p>
            ) : null}

            <button
              type="button"
              onClick={handleOrganizationShare}
              disabled={
                (!selectedMemberIds.length && !hasSelectedGroupDestination) ||
                busy === "team-share" ||
                teamLoading ||
                !teamFilePartition.accepted.length
              }
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy === "team-share" ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Users className="h-4 w-4" />
              )}
              {busy === "team-share"
                ? "Sharing securely…"
                : `Share to ${selectedOrganizationDestinationCount} destination${
                    selectedOrganizationDestinationCount === 1 ? "" : "s"
                  }`}
            </button>
          </section>
        </div>
      ) : null}
    </>
  );
}
