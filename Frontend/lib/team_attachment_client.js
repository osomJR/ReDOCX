// Cookie-authenticated client for team-message attachments only. The Auth0
// access token remains inside the Next.js server proxy and is never attached by
// browser JavaScript to an upload or download request.

import {
  TEAM_ATTACHMENT_MAX_BYTES,
  validateTeamAttachment,
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

export async function sendTeamConversationAttachment(
  conversationId,
  file,
  options = {},
) {
  const encodedConversationId = encodePositiveId(conversationId, "conversationId");
  validateTeamAttachment(file);

  const body = new FormData();
  body.append("file", file, file.name);
  const caption = String(options.caption || "").trim();
  const clientMessageId = String(options.clientMessageId || "").trim();
  if (caption) body.append("caption", caption);
  if (clientMessageId) body.append("client_message_id", clientMessageId);

  const url = `/api/conversations/${encodedConversationId}/attachments`;
  const response = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
    body,
    signal: options.signal,
  });
  const payload = await readPayload(response);
  if (!response.ok) {
    const details = errorDetails(payload, "Could not send attachment securely.");
    throw new TeamAttachmentClientError(details.message, {
      status: response.status,
      code: details.code,
      payload,
    });
  }
  return payload;
}

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
