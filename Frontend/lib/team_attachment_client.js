// Cookie-authenticated client for team-message attachments only. The Auth0
// access token remains inside the Next.js server proxy and is never attached by
// browser JavaScript to an upload or download request.

import {
  TEAM_ATTACHMENT_MAX_BYTES,
  validateTeamAttachments,
} from "@/lib/team_attachment_policy";


const DOWNLOAD_PATH_RE = /^\/api\/conversations\/([1-9][0-9]*)\/messages\/([1-9][0-9]*)\/attachments\/([1-9][0-9]*)\/download$/u;

export class TeamAttachmentClientError extends Error {
  constructor(message, { status = 0, code = "attachment_request_failed", payload = null } = {}) {
    super(message);
    this.name = "TeamAttachmentClientError";
    this.status = status;
    this.code = code;
    this.payload = payload;
  }
}

function encodePositiveId(value, fieldName) {
  const normalized = String(value ?? "").trim();
  if (!/^[1-9][0-9]*$/u.test(normalized)) {
    throw new TeamAttachmentClientError(`${fieldName} must be a positive integer.`, {
      code: "invalid_path_identifier",
    });
  }
  return normalized;
}

async function readPayload(response) {
  const type = String(response.headers.get("content-type") || "").toLowerCase();
  if (type.includes("application/json")) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }
  try {
    return await response.text();
  } catch {
    return null;
  }
}

function errorDetails(payload, fallback) {
  const detail = payload?.detail && typeof payload.detail === "object"
    ? payload.detail
    : payload;
  return {
    code: String(detail?.error || "attachment_request_failed"),
    message: String(detail?.message || fallback),
  };
}

function sanitizeDownloadFilename(value) {
  const normalized = String(value || "")
    .normalize("NFKC")
    .replace(/[\\/\u0000-\u001f\u007f]/gu, "_")
    .trim()
    .slice(0, 180);
  return normalized || "attachment";
}

function filenameFromContentDisposition(header) {
  const value = String(header || "");
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/iu)?.[1];
  if (encoded) {
    try {
      return sanitizeDownloadFilename(decodeURIComponent(encoded));
    } catch {
      // Fall through to the ASCII filename.
    }
  }
  const quoted = value.match(/filename="([^"]*)"/iu)?.[1];
  return sanitizeDownloadFilename(quoted || "attachment");
}

function parseXhrPayload(xhr) {
  const contentType = String(xhr.getResponseHeader("content-type") || "").toLowerCase();
  if (contentType.includes("application/json")) {
    if (xhr.response && typeof xhr.response === "object") return xhr.response;
    try {
      return JSON.parse(xhr.responseText || "null");
    } catch {
      return null;
    }
  }
  return xhr.responseText || null;
}

function uploadAttachmentForm(url, body, options = {}) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");

    const handleAbort = () => xhr.abort();
    if (options.signal) {
      if (options.signal.aborted) {
        reject(new DOMException("Attachment upload was aborted.", "AbortError"));
        return;
      }
      options.signal.addEventListener("abort", handleAbort, { once: true });
    }

    xhr.upload.onprogress = (event) => {
      if (typeof options.onProgress !== "function") return;
      const total = event.lengthComputable ? event.total : 0;
      options.onProgress({
        loaded: event.loaded,
        total,
        percent: total > 0 ? Math.min(100, Math.round((event.loaded / total) * 100)) : null,
      });
    };

    xhr.onload = () => {
      options.signal?.removeEventListener("abort", handleAbort);
      const payload = parseXhrPayload(xhr);
      if (xhr.status < 200 || xhr.status >= 300) {
        const details = errorDetails(payload, "Could not send attachments securely.");
        reject(
          new TeamAttachmentClientError(details.message, {
            status: xhr.status,
            code: details.code,
            payload,
          }),
        );
        return;
      }
      resolve(payload);
    };

    xhr.onerror = () => {
      options.signal?.removeEventListener("abort", handleAbort);
      reject(
        new TeamAttachmentClientError("Could not reach the secure attachment service.", {
          status: 0,
          code: "attachment_service_unavailable",
        }),
      );
    };

    xhr.onabort = () => {
      options.signal?.removeEventListener("abort", handleAbort);
      reject(new DOMException("Attachment upload was aborted.", "AbortError"));
    };

    xhr.send(body);
  });
}

export async function sendTeamConversationAttachment(
  conversationId,
  fileOrFiles,
  options = {},
) {
  const encodedConversationId = encodePositiveId(conversationId, "conversationId");
  const isFileList =
    typeof FileList !== "undefined" && fileOrFiles instanceof FileList;
  const files = Array.isArray(fileOrFiles)
    ? fileOrFiles
    : Array.from(isFileList ? fileOrFiles : [fileOrFiles]);
  validateTeamAttachments(files);

  const body = new FormData();
  for (const file of files) {
    body.append("files", file, file.name);
  }
  const caption = String(options.caption || "").trim();
  const clientMessageId = String(options.clientMessageId || "").trim();
  if (caption) body.append("caption", caption);
  if (clientMessageId) body.append("client_message_id", clientMessageId);

  const url = `/api/conversations/${encodedConversationId}/attachments`;
  return uploadAttachmentForm(url, body, options);
}

export const sendTeamConversationAttachments = sendTeamConversationAttachment;

export async function downloadTeamConversationAttachment(downloadUrl, options = {}) {
  const rawUrl = String(downloadUrl || "").trim();
  let path;
  try {
    const parsed = new URL(rawUrl, window.location.origin);
    if (parsed.origin !== window.location.origin || parsed.search || parsed.hash) {
      throw new Error("cross-origin-or-parameterized-url");
    }
    path = parsed.pathname;
  } catch {
    throw new TeamAttachmentClientError("Attachment download URL is invalid.", {
      code: "invalid_attachment_download_url",
    });
  }
  if (!DOWNLOAD_PATH_RE.test(path)) {
    throw new TeamAttachmentClientError("Attachment download URL is invalid.", {
      code: "invalid_attachment_download_url",
    });
  }

  const response = await fetch(path, {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/octet-stream" },
    signal: options.signal,
  });
  if (!response.ok) {
    const payload = await readPayload(response);
    const details = errorDetails(payload, "Could not download attachment securely.");
    throw new TeamAttachmentClientError(details.message, {
      status: response.status,
      code: details.code,
      payload,
    });
  }

  const declaredLength = Number(response.headers.get("content-length") || 0);
  if (Number.isFinite(declaredLength) && declaredLength > TEAM_ATTACHMENT_MAX_BYTES) {
    throw new TeamAttachmentClientError("Attachment response exceeds the secure size limit.", {
      status: 502,
      code: "attachment_response_too_large",
    });
  }
  const blob = await response.blob();
  if (blob.size > TEAM_ATTACHMENT_MAX_BYTES) {
    throw new TeamAttachmentClientError("Attachment response exceeds the secure size limit.", {
      status: 502,
      code: "attachment_response_too_large",
    });
  }

  return {
    blob,
    filename: filenameFromContentDisposition(
      response.headers.get("content-disposition"),
    ),
  };
}
