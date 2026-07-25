"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  ExternalLink,
  Loader2,
  Printer,
  Share2,
  Users,
  X,
} from "lucide-react";

const TEAM_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;
const TEAM_ATTACHMENT_EXTENSIONS = new Set([
  "csv",
  "docx",
  "jpeg",
  "jpg",
  "json",
  "md",
  "mkv",
  "mov",
  "mp3",
  "mp4",
  "pdf",
  "png",
  "pptx",
  "txt",
  "xlsx",
]);
const BUSINESS_OR_ENTERPRISE_PLANS = new Set(["business", "enterprise"]);
const PRINTABLE_TEXT_EXTENSIONS = new Set([
  "csv",
  "htm",
  "html",
  "json",
  "md",
  "rtf",
  "text",
  "tsv",
  "txt",
  "xml",
]);
const PRINTABLE_IMAGE_EXTENSIONS = new Set([
  "avif",
  "bmp",
  "gif",
  "jpeg",
  "jpg",
  "png",
  "svg",
  "webp",
]);

function firstString(values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function safeFilename(value, fallback = "processed-output") {
  const cleaned = String(value || "")
    .replace(/[\u0000-\u001f\u007f]/gu, "")
    .replace(/[\\/]/gu, "-")
    .trim()
    .slice(0, 180);
  return cleaned || fallback;
}

function filenameFromUrl(value) {
  if (!value) return "";

  try {
    const baseUrl =
      typeof window === "undefined"
        ? "http://localhost"
        : window.location.origin;
    const parsed = new URL(value, baseUrl);
    const requestedName = parsed.searchParams.get("download_name");
    if (requestedName) return safeFilename(requestedName);
    return safeFilename(
      decodeURIComponent(parsed.pathname.split("/").pop() || ""),
    );
  } catch {
    return "";
  }
}

function filenameFromDisposition(value) {
  if (!value) return "";

  const encodedMatch = value.match(/filename\*\s*=\s*UTF-8''([^;]+)/iu);
  if (encodedMatch?.[1]) {
    try {
      return safeFilename(decodeURIComponent(encodedMatch[1].trim()));
    } catch {
      // Fall through to the plain filename form.
    }
  }

  const plainMatch = value.match(/filename\s*=\s*"?([^";]+)"?/iu);
  return plainMatch?.[1] ? safeFilename(plainMatch[1].trim()) : "";
}

function extensionOf(filename) {
  const match = String(filename || "")
    .toLowerCase()
    .match(/\.([a-z0-9]+)$/u);
  return match?.[1] || "";
}

function filenameForText(value, fallback) {
  const candidate = safeFilename(value, fallback);
  return extensionOf(candidate) ? candidate : `${candidate}.txt`;
}

function artifactUrlFromStorageKey(storageKey) {
  const cleaned = String(storageKey || "")
    .trim()
    .replace(/^\/+/u, "");
  if (!cleaned) return "";

  return `/api/analyzer/artifacts/${cleaned
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/")}`;
}

function normalizeOutput(value, fallbackTitle) {
  if (!value || typeof value !== "object") return null;

  const url = firstString([value.url, value.downloadUrl, value.download_url]);
  const text =
    typeof value.text === "string"
      ? value.text
      : typeof value.content === "string"
        ? value.content
        : "";

  if (!url && !text) return null;

  const filename = safeFilename(
    firstString([
      value.filename,
      value.fileName,
      value.file_name,
      url ? filenameFromUrl(url) : "",
    ]),
    safeFilename(fallbackTitle, "processed-output"),
  );

  return {
    url,
    text,
    filename: url ? filename : filenameForText(filename, "processed-output"),
    mimeType: firstString([
      value.mimeType,
      value.mime_type,
      value.contentType,
      value.content_type,
    ]),
  };
}

function collectResultOutputs(result, fallbackTitle) {
  if (!result || typeof result !== "object") return [];

  const collected = [];
  const visited = new WeakSet();

  function visit(node, inheritedFilename = "", depth = 0) {
    if (!node || typeof node !== "object" || depth > 9) return;
    if (visited.has(node)) return;
    visited.add(node);

    if (Array.isArray(node)) {
      node.forEach((item) => visit(item, inheritedFilename, depth + 1));
      return;
    }

    const filename = firstString([
      node.filename,
      node.file_name,
      node.output_filename,
      node.outputFilename,
      inheritedFilename,
    ]);
    const downloadUrl =
      firstString([
        node.download_url,
        node.downloadUrl,
        node.artifact_url,
        node.artifactUrl,
      ]) ||
      artifactUrlFromStorageKey(
        firstString([node.storage_key, node.storageKey]),
      );

    if (downloadUrl) {
      const normalized = normalizeOutput(
        {
          url: downloadUrl,
          filename,
          mimeType: firstString([
            node.mime_type,
            node.mimeType,
            node.content_type,
            node.contentType,
          ]),
        },
        fallbackTitle,
      );
      if (normalized) collected.push(normalized);
    } else if (
      typeof node.content === "string" &&
      node.content.trim() &&
      ["text", "txt"].includes(
        String(node.output_format || node.outputFormat || "").toLowerCase(),
      )
    ) {
      const normalized = normalizeOutput(
        {
          text: node.content,
          filename: filenameForText(filename, `${fallbackTitle}.txt`),
          mimeType: "text/plain;charset=utf-8",
        },
        fallbackTitle,
      );
      if (normalized) collected.push(normalized);
    }

    for (const [key, value] of Object.entries(node)) {
      if (
        value &&
        typeof value === "object" &&
        !["error", "metadata", "security_metadata", "usage"].includes(key)
      ) {
        visit(value, filename || inheritedFilename, depth + 1);
      }
    }
  }

  visit(result);
  return collected;
}

function deduplicateOutputs(outputs) {
  const seen = new Set();
  return outputs.filter((output) => {
    const identity = output.url
      ? `url:${output.url}`
      : `text:${output.filename}:${output.text.length}:${output.text.slice(0, 80)}`;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}

function outputIdentity(output) {
  return output?.url
    ? `url:${output.url}`
    : `text:${output?.filename || ""}:${output?.text?.length || 0}:${
        output?.text?.slice(0, 80) || ""
      }`;
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    const message =
      (typeof detail === "string" && detail) ||
      (detail && typeof detail.message === "string" && detail.message) ||
      (typeof payload?.message === "string" && payload.message);
    return message || fallback;
  } catch {
    return fallback;
  }
}

function createClientMessageId() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") {
    return `share:${cryptoApi.randomUUID()}`;
  }

  const randomValues = new Uint32Array(4);
  if (typeof cryptoApi?.getRandomValues === "function") {
    cryptoApi.getRandomValues(randomValues);
  } else {
    randomValues.set([
      Date.now() >>> 0,
      Math.floor(Math.random() * 0xffffffff),
      Math.floor(Math.random() * 0xffffffff),
      Math.floor(Math.random() * 0xffffffff),
    ]);
  }
  return `share:${Date.now()}:${Array.from(randomValues).join("-")}`;
}

async function outputAsFile(output) {
  if (output.text && !output.url) {
    const type = output.mimeType || "text/plain;charset=utf-8";
    return new File([output.text], output.filename, { type });
  }

  const response = await fetch(output.url, {
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(
      await responseError(response, "Could not load the processed output."),
    );
  }

  const blob = await response.blob();
  const filename = safeFilename(
    filenameFromDisposition(response.headers.get("content-disposition")) ||
      output.filename ||
      filenameFromUrl(output.url),
    "processed-output",
  );

  return new File([blob], filename, {
    type: blob.type || output.mimeType || "application/octet-stream",
  });
}

function readFileAsText(file) {
  if (typeof file.text === "function") return file.text();

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener(
      "load",
      () => resolve(String(reader.result || "")),
      {
        once: true,
      },
    );
    reader.addEventListener(
      "error",
      () =>
        reject(reader.error || new Error("Could not read the text output.")),
      { once: true },
    );
    reader.readAsText(file);
  });
}

function popupDocument(popup, title) {
  popup.document.open();
  popup.document.write(
    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>@page{margin:16mm}html,body{margin:0;background:#fff;color:#111;font-family:Arial,sans-serif}body{padding:16mm}pre{font:12pt/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}img{display:block;max-width:100%;height:auto;margin:0 auto}</style></head><body></body></html>',
  );
  popup.document.close();
  popup.document.title = title;
}

async function printOutput(output) {
  const popup = window.open("", "_blank", "width=980,height=760");
  if (!popup) {
    throw new Error(
      "The print window was blocked. Allow pop-ups for ReDOCX and try again.",
    );
  }
  popup.opener = null;
  popupDocument(popup, `Print ${output.filename}`);
  popup.document.body.textContent = "Preparing the processed output…";

  try {
    const file = await outputAsFile(output);
    const extension = extensionOf(file.name);
    const type = String(file.type || "").toLowerCase();

    if (
      type.startsWith("text/") ||
      type.includes("json") ||
      type.includes("xml") ||
      PRINTABLE_TEXT_EXTENSIONS.has(extension)
    ) {
      const content = await readFileAsText(file);
      popupDocument(popup, file.name);
      const pre = popup.document.createElement("pre");
      pre.textContent = content;
      popup.document.body.appendChild(pre);
      popup.focus();
      popup.print();
      return;
    }

    const objectUrl = URL.createObjectURL(file);
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);

    if (type === "application/pdf" || extension === "pdf") {
      let printTriggered = false;
      const triggerPrint = () => {
        if (printTriggered || popup.closed) return;
        printTriggered = true;
        try {
          popup.focus();
          popup.print();
        } catch {
          // The browser PDF viewer still exposes its own print control.
        }
      };
      popup.addEventListener(
        "load",
        () => window.setTimeout(triggerPrint, 250),
        { once: true },
      );
      popup.location.replace(objectUrl);
      window.setTimeout(triggerPrint, 2_500);
      return;
    }

    if (
      type.startsWith("image/") ||
      PRINTABLE_IMAGE_EXTENSIONS.has(extension)
    ) {
      popupDocument(popup, file.name);
      const image = popup.document.createElement("img");
      image.alt = file.name;
      image.addEventListener(
        "load",
        () => {
          popup.focus();
          popup.print();
        },
        { once: true },
      );
      image.src = objectUrl;
      popup.document.body.appendChild(image);
      return;
    }

    popup.close();
    throw new Error(
      "Direct browser printing is available for PDF, image, and text outputs. Download this output and print it from an application that supports its file format.",
    );
  } catch (error) {
    if (!popup.closed) popup.close();
    throw error;
  }
}

function memberLabel(member) {
  return (
    firstString([member?.name, member?.email, member?.user_id]) || "Member"
  );
}

export default function ProcessedOutputActions({
  artifactUrl = "",
  filename = "",
  mimeType = "",
  textContent = "",
  textFilename = "",
  artifacts = [],
  result = null,
  title = "Processed output",
  className = "",
}) {
  const outputs = useMemo(() => {
    const explicit = [];

    for (const artifact of Array.isArray(artifacts) ? artifacts : []) {
      const normalized = normalizeOutput(artifact, title);
      if (normalized) explicit.push(normalized);
    }

    const primary = normalizeOutput(
      {
        url: artifactUrl,
        filename: artifactUrl ? filename : textFilename || filename,
        mimeType,
        text: artifactUrl ? "" : textContent,
      },
      title,
    );
    if (primary) explicit.push(primary);

    if (!artifactUrl && textContent && !primary) {
      const textOutput = normalizeOutput(
        {
          text: textContent,
          filename: textFilename || filenameForText(filename, `${title}.txt`),
          mimeType: mimeType || "text/plain;charset=utf-8",
        },
        title,
      );
      if (textOutput) explicit.push(textOutput);
    }

    return deduplicateOutputs([
      ...explicit,
      ...collectResultOutputs(result, safeFilename(title, "processed-output")),
    ]);
  }, [
    artifactUrl,
    artifacts,
    filename,
    mimeType,
    result,
    textContent,
    textFilename,
    title,
  ]);

  const [shareOpen, setShareOpen] = useState(false);
  const [printOpen, setPrintOpen] = useState(false);
  const [selectedOutputIndex, setSelectedOutputIndex] = useState(0);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [organizations, setOrganizations] = useState([]);
  const [members, setMembers] = useState([]);
  const [currentUserId, setCurrentUserId] = useState("");
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [selectedMemberId, setSelectedMemberId] = useState("");
  const [loadingOrganizations, setLoadingOrganizations] = useState(false);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [organizationError, setOrganizationError] = useState("");
  const [preparingFileKey, setPreparingFileKey] = useState("");
  const [preparedFileKey, setPreparedFileKey] = useState("");
  const fileCacheRef = useRef(new Map());
  const prepareSequenceRef = useRef(0);

  const selectedOutput =
    outputs[Math.min(selectedOutputIndex, Math.max(outputs.length - 1, 0))] ||
    null;
  const selectedOutputKey = outputIdentity(selectedOutput);
  const selectedFile =
    preparedFileKey === selectedOutputKey
      ? fileCacheRef.current.get(selectedOutputKey) || null
      : null;

  useEffect(() => {
    if (!shareOpen && !printOpen) return undefined;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event) => {
      if (event.key === "Escape" && !busyAction) {
        setShareOpen(false);
        setPrintOpen(false);
        setError("");
      }
    };
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [busyAction, printOpen, shareOpen]);

  useEffect(() => {
    if (selectedOutputIndex >= outputs.length) setSelectedOutputIndex(0);
  }, [outputs.length, selectedOutputIndex]);

  async function loadMembers(organizationId, ownUserId) {
    if (!organizationId) {
      setMembers([]);
      setSelectedMemberId("");
      return;
    }

    setLoadingMembers(true);
    setOrganizationError("");
    try {
      const response = await fetch(
        `/api/organizations/${encodeURIComponent(organizationId)}`,
        { credentials: "same-origin", cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(
          await responseError(response, "Could not load organization members."),
        );
      }

      const payload = await response.json();
      const eligibleMembers = (payload?.members || []).filter(
        (member) =>
          member?.status === "active" &&
          !member?.is_email_invitation &&
          member?.user_id &&
          member.user_id !== ownUserId,
      );
      setMembers(eligibleMembers);
      setSelectedMemberId(eligibleMembers[0]?.user_id || "");
    } catch (loadError) {
      setMembers([]);
      setSelectedMemberId("");
      setOrganizationError(
        loadError instanceof Error
          ? loadError.message
          : "Could not load organization members.",
      );
    } finally {
      setLoadingMembers(false);
    }
  }

  async function loadOrganizations() {
    setLoadingOrganizations(true);
    setOrganizationError("");
    try {
      const response = await fetch("/api/organizations/me", {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(
          await responseError(response, "Could not load your organizations."),
        );
      }

      const payload = await response.json();
      const ownUserId = firstString([
        payload?.user?.id,
        payload?.user?.user_id,
      ]);
      const eligibleOrganizations = (payload?.organizations || []).filter(
        (organization) =>
          organization?.member?.status === "active" &&
          organization?.subscription?.status === "active" &&
          BUSINESS_OR_ENTERPRISE_PLANS.has(
            String(organization?.subscription?.plan || "").toLowerCase(),
          ),
      );

      setCurrentUserId(ownUserId);
      setOrganizations(eligibleOrganizations);

      const firstOrganizationId = eligibleOrganizations[0]?.id;
      setSelectedOrganizationId(
        firstOrganizationId ? String(firstOrganizationId) : "",
      );
      if (firstOrganizationId) {
        await loadMembers(String(firstOrganizationId), ownUserId);
      } else {
        setMembers([]);
        setSelectedMemberId("");
      }
    } catch (loadError) {
      setOrganizations([]);
      setMembers([]);
      setSelectedOrganizationId("");
      setSelectedMemberId("");
      setOrganizationError(
        loadError instanceof Error
          ? loadError.message
          : "Could not load your organizations.",
      );
    } finally {
      setLoadingOrganizations(false);
    }
  }

  async function prepareOutputForShare(output) {
    if (!output) return;

    const key = outputIdentity(output);
    const cachedFile = fileCacheRef.current.get(key);
    if (cachedFile) {
      setPreparedFileKey(key);
      setPreparingFileKey("");
      return;
    }

    const sequence = prepareSequenceRef.current + 1;
    prepareSequenceRef.current = sequence;
    setPreparedFileKey("");
    setPreparingFileKey(key);
    try {
      const file = await outputAsFile(output);
      fileCacheRef.current.set(key, file);
      if (prepareSequenceRef.current === sequence) {
        setPreparedFileKey(key);
      }
    } catch (prepareError) {
      if (prepareSequenceRef.current === sequence) {
        setError(
          prepareError instanceof Error
            ? prepareError.message
            : "Could not prepare the output for sharing.",
        );
      }
    } finally {
      if (prepareSequenceRef.current === sequence) {
        setPreparingFileKey("");
      }
    }
  }

  function openShare() {
    setError("");
    setNotice("");
    setSelectedOutputIndex(0);
    setShareOpen(true);
    void loadOrganizations();
    void prepareOutputForShare(outputs[0]);
  }

  function closeDialog() {
    if (busyAction) return;
    setShareOpen(false);
    setPrintOpen(false);
    setError("");
  }

  async function handleExternalShare() {
    if (!selectedOutput) return;
    setBusyAction("external");
    setError("");
    setNotice("");

    try {
      if (typeof navigator.share !== "function") {
        throw new Error(
          "This browser does not support sharing files to other applications. Use Download to attach the output manually.",
        );
      }

      const file = selectedFile;
      if (!file) {
        throw new Error(
          "The output is still being prepared. Wait a moment and try again.",
        );
      }
      const shareData = {
        title,
        text: `Shared from ReDOCX: ${file.name}`,
        files: [file],
      };
      if (
        typeof navigator.canShare === "function" &&
        !navigator.canShare({ files: [file] })
      ) {
        throw new Error(
          "This browser cannot share this file type to other applications. Use Download to attach the output manually.",
        );
      }

      await navigator.share(shareData);
      setShareOpen(false);
      setNotice("Output shared.");
    } catch (shareError) {
      if (shareError?.name !== "AbortError") {
        setError(
          shareError instanceof Error
            ? shareError.message
            : "Could not share the output.",
        );
      }
    } finally {
      setBusyAction("");
    }
  }

  async function handleOrganizationChange(event) {
    const organizationId = event.target.value;
    setSelectedOrganizationId(organizationId);
    setSelectedMemberId("");
    setMembers([]);
    await loadMembers(organizationId, currentUserId);
  }

  function handleOutputSelection(event) {
    const index = Number(event.target.value);
    setSelectedOutputIndex(index);
    setError("");
    void prepareOutputForShare(outputs[index]);
  }

  async function handleOrganizationShare() {
    if (!selectedOutput || !selectedOrganizationId || !selectedMemberId) {
      setError("Choose an organization member.");
      return;
    }

    setBusyAction("organization");
    setError("");
    setNotice("");

    try {
      const file = selectedFile;
      if (!file) {
        throw new Error(
          "The output is still being prepared. Wait a moment and try again.",
        );
      }
      if (!TEAM_ATTACHMENT_EXTENSIONS.has(extensionOf(file.name))) {
        throw new Error(
          "This output format is not supported by secure organization attachments. Choose another output file or use Share to another application.",
        );
      }
      if (file.size > TEAM_ATTACHMENT_MAX_BYTES) {
        throw new Error(
          "This output is larger than the 20 MB secure team-attachment limit.",
        );
      }

      const conversationResponse = await fetch(
        `/api/organizations/${encodeURIComponent(
          selectedOrganizationId,
        )}/conversations`,
        {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            type: "dm",
            member_user_ids: [selectedMemberId],
          }),
        },
      );
      if (!conversationResponse.ok) {
        throw new Error(
          await responseError(
            conversationResponse,
            "Could not open a secure conversation with this member.",
          ),
        );
      }

      const conversationPayload = await conversationResponse.json();
      const conversationId = conversationPayload?.conversation?.id;
      if (!conversationId) {
        throw new Error(
          "The organization service returned no conversation identifier.",
        );
      }

      const formData = new FormData();
      formData.append("file", file, file.name);
      formData.append("caption", `Shared from ${title}`.slice(0, 5000));
      formData.append("client_message_id", createClientMessageId());

      const attachmentResponse = await fetch(
        `/api/conversations/${encodeURIComponent(conversationId)}/attachments`,
        {
          method: "POST",
          credentials: "same-origin",
          body: formData,
        },
      );
      if (!attachmentResponse.ok) {
        throw new Error(
          await responseError(
            attachmentResponse,
            "Could not securely share this output.",
          ),
        );
      }

      const member = members.find(
        (candidate) => candidate.user_id === selectedMemberId,
      );
      setShareOpen(false);
      setNotice(`Shared securely with ${memberLabel(member)}.`);
    } catch (shareError) {
      setError(
        shareError instanceof Error
          ? shareError.message
          : "Could not securely share this output.",
      );
    } finally {
      setBusyAction("");
    }
  }

  async function handlePrint(output = selectedOutput) {
    if (!output) return;
    setBusyAction("print");
    setError("");
    setNotice("");

    try {
      await printOutput(output);
      setPrintOpen(false);
    } catch (printError) {
      setError(
        printError instanceof Error
          ? printError.message
          : "Could not print this output.",
      );
    } finally {
      setBusyAction("");
    }
  }

  function requestPrint() {
    setError("");
    setNotice("");
    if (outputs.length === 1) {
      void handlePrint(outputs[0]);
      return;
    }
    setSelectedOutputIndex(0);
    setPrintOpen(true);
  }

  if (!outputs.length) return null;

  const actionButtonClass =
    "inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text transition hover:border-cyan-300/50 disabled:cursor-not-allowed disabled:opacity-60";
  const primaryButtonClass =
    "inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] transition disabled:cursor-not-allowed disabled:opacity-60";
  const fieldClass =
    "mt-2 w-full rounded-xl border app-surface px-3 py-2 text-sm app-text";
  const selectedOutputExtension = extensionOf(selectedOutput?.filename);
  const selectedOutputIsTeamShareable =
    !selectedOutputExtension ||
    TEAM_ATTACHMENT_EXTENSIONS.has(selectedOutputExtension);
  const selectedOutputIsPrepared = Boolean(selectedFile);
  const selectedOutputIsPreparing =
    preparingFileKey === selectedOutputKey && !selectedOutputIsPrepared;

  return (
    <>
      <div className={`mt-4 ${className}`.trim()}>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={openShare}
            disabled={Boolean(busyAction)}
            className={actionButtonClass}
          >
            <Share2 className="h-4 w-4" aria-hidden="true" />
            Share
          </button>
          <button
            type="button"
            onClick={requestPrint}
            disabled={Boolean(busyAction)}
            className={actionButtonClass}
          >
            {busyAction === "print" ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Printer className="h-4 w-4" aria-hidden="true" />
            )}
            Print
          </button>
        </div>
        {notice ? (
          <p
            className="mt-2 flex items-center gap-2 text-sm text-emerald-300"
            role="status"
          >
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            {notice}
          </p>
        ) : null}
        {error && !shareOpen && !printOpen ? (
          <p className="mt-2 text-sm text-red-300" role="alert">
            {error}
          </p>
        ) : null}
      </div>

      {shareOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDialog();
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="processed-output-share-title"
            className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-3xl border app-surface-strong p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  id="processed-output-share-title"
                  className="text-xl font-semibold app-text"
                >
                  Share processed output
                </h2>
                <p className="mt-1 text-sm app-text-muted">
                  Share the file with another application or securely with a
                  member of your organization.
                </p>
              </div>
              <button
                type="button"
                onClick={closeDialog}
                disabled={Boolean(busyAction)}
                aria-label="Close share dialog"
                className="rounded-lg p-2 app-text-muted hover:text-[var(--app-text)]"
              >
                <X className="h-5 w-5" aria-hidden="true" />
              </button>
            </div>

            {outputs.length > 1 ? (
              <label className="mt-5 block text-sm font-medium app-text">
                Output
                <select
                  value={selectedOutputIndex}
                  onChange={handleOutputSelection}
                  disabled={Boolean(busyAction)}
                  className={fieldClass}
                >
                  {outputs.map((output, index) => (
                    <option
                      key={`${output.url || output.filename}-${index}`}
                      value={index}
                    >
                      {output.filename}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            <button
              type="button"
              onClick={() => void handleExternalShare()}
              disabled={
                Boolean(busyAction) ||
                selectedOutputIsPreparing ||
                !selectedOutputIsPrepared
              }
              className={`${primaryButtonClass} mt-5 w-full`}
            >
              {busyAction === "external" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
              )}
              Share to another application
            </button>
            {selectedOutputIsPreparing ? (
              <p className="mt-2 flex items-center gap-2 text-xs app-text-muted">
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
                Preparing the output for secure sharing…
              </p>
            ) : null}

            {loadingOrganizations ? (
              <p className="mt-5 flex items-center gap-2 text-sm app-text-muted">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading organization sharing…
              </p>
            ) : organizations.length ? (
              <div className="mt-5 border-t border-[var(--app-border)] pt-5">
                <h3 className="flex items-center gap-2 font-semibold app-text">
                  <Users className="h-4 w-4" aria-hidden="true" />
                  Share securely with a member
                </h3>
                <p className="mt-1 text-sm app-text-muted">
                  Available only inside your active Business or Enterprise
                  organization.
                </p>

                {organizations.length > 1 ? (
                  <label className="mt-4 block text-sm font-medium app-text">
                    Organization
                    <select
                      value={selectedOrganizationId}
                      onChange={(event) => void handleOrganizationChange(event)}
                      disabled={Boolean(busyAction) || loadingMembers}
                      className={fieldClass}
                    >
                      {organizations.map((organization) => (
                        <option
                          key={organization.id}
                          value={String(organization.id)}
                        >
                          {organization.name}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}

                <label className="mt-4 block text-sm font-medium app-text">
                  Member
                  <select
                    value={selectedMemberId}
                    onChange={(event) =>
                      setSelectedMemberId(event.target.value)
                    }
                    disabled={
                      Boolean(busyAction) ||
                      loadingMembers ||
                      members.length === 0
                    }
                    className={fieldClass}
                  >
                    {loadingMembers ? (
                      <option value="">Loading members…</option>
                    ) : members.length ? (
                      members.map((member) => (
                        <option key={member.user_id} value={member.user_id}>
                          {memberLabel(member)}
                        </option>
                      ))
                    ) : (
                      <option value="">No other active members</option>
                    )}
                  </select>
                </label>

                <button
                  type="button"
                  onClick={() => void handleOrganizationShare()}
                  disabled={
                    Boolean(busyAction) ||
                    loadingMembers ||
                    !selectedMemberId ||
                    !selectedOutputIsTeamShareable ||
                    !selectedOutputIsPrepared
                  }
                  className={`${actionButtonClass} mt-4 w-full`}
                >
                  {busyAction === "organization" ? (
                    <Loader2
                      className="h-4 w-4 animate-spin"
                      aria-hidden="true"
                    />
                  ) : (
                    <Users className="h-4 w-4" aria-hidden="true" />
                  )}
                  Share securely
                </button>
                {!selectedOutputIsTeamShareable ? (
                  <p className="mt-2 text-xs text-amber-300">
                    Choose an output format supported by secure organization
                    attachments.
                  </p>
                ) : null}
              </div>
            ) : null}

            {organizationError ? (
              <p className="mt-4 text-sm text-amber-300" role="status">
                {organizationError}
              </p>
            ) : null}
            {error ? (
              <p className="mt-4 text-sm text-red-300" role="alert">
                {error}
              </p>
            ) : null}
          </section>
        </div>
      ) : null}

      {printOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDialog();
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="processed-output-print-title"
            className="w-full max-w-md rounded-3xl border app-surface-strong p-5 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2
                  id="processed-output-print-title"
                  className="text-xl font-semibold app-text"
                >
                  Print processed output
                </h2>
                <p className="mt-1 text-sm app-text-muted">
                  Choose the output to send to your browser&apos;s print dialog.
                </p>
              </div>
              <button
                type="button"
                onClick={closeDialog}
                disabled={Boolean(busyAction)}
                aria-label="Close print dialog"
                className="rounded-lg p-2 app-text-muted hover:text-[var(--app-text)]"
              >
                <X className="h-5 w-5" aria-hidden="true" />
              </button>
            </div>

            <label className="mt-5 block text-sm font-medium app-text">
              Output
              <select
                value={selectedOutputIndex}
                onChange={(event) =>
                  setSelectedOutputIndex(Number(event.target.value))
                }
                disabled={Boolean(busyAction)}
                className={fieldClass}
              >
                {outputs.map((output, index) => (
                  <option
                    key={`${output.url || output.filename}-${index}`}
                    value={index}
                  >
                    {output.filename}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => void handlePrint()}
              disabled={Boolean(busyAction)}
              className={`${primaryButtonClass} mt-5 w-full`}
            >
              {busyAction === "print" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Printer className="h-4 w-4" aria-hidden="true" />
              )}
              Open print dialog
            </button>
            {error ? (
              <p className="mt-4 text-sm text-red-300" role="alert">
                {error}
              </p>
            ) : null}
          </section>
        </div>
      ) : null}
    </>
  );
}
