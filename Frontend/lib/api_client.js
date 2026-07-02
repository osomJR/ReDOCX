let cachedAccessToken = "";
let cachedAccessTokenExpiresAt = 0;
let pendingAccessTokenRequest = null;

export const ACCOUNT_INVALIDATED_EVENT = "redocx:account:invalidated";

const TERMINAL_AUTH_ERROR_CODES = new Set([
  "account_deleted",
  "user_deleted",
  "user_not_found",
  "invalid_token",
]);

const ANALYZER_ARTIFACT_ROUTE_PREFIX = "/api/analyzer/artifacts/";

const ANALYZER_ARTIFACT_URL_PREFIXES = [
  "/api/analyzer/artifacts/",
  "api/analyzer/artifacts/",
  "/api/v1/analyzer/artifacts/",
  "api/v1/analyzer/artifacts/",
  "/artifacts/",
  "artifacts/",
];

function isPlainObject(value) {
  return Object.prototype.toString.call(value) === "[object Object]";
}

export function cleanAnalyzerArtifactStorageKey(value) {
  let key = String(value || "")
    .trim()
    .replaceAll("\\", "/");

  if (!key) return "";

  let changed = true;
  while (changed) {
    changed = false;

    for (const prefix of ANALYZER_ARTIFACT_URL_PREFIXES) {
      if (key.startsWith(prefix)) {
        key = key.slice(prefix.length);
        changed = true;
      }
    }
  }

  return key.replace(/^\/+/, "");
}

export function buildAnalyzerArtifactUrl(storageKey) {
  const cleanStorageKey = cleanAnalyzerArtifactStorageKey(storageKey);
  return cleanStorageKey ? `${ANALYZER_ARTIFACT_ROUTE_PREFIX}${cleanStorageKey}` : "";
}

export function normalizeAnalyzerArtifactUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";

  // Preserve CDN / signed / externally hosted artifact URLs exactly as returned.
  if (/^https?:\/\//i.test(raw)) return raw;

  const usesKnownArtifactPrefix = ANALYZER_ARTIFACT_URL_PREFIXES.some((prefix) =>
    raw.startsWith(prefix),
  );

  return usesKnownArtifactPrefix ? buildAnalyzerArtifactUrl(raw) : raw;
}

export function getAnalyzerResultDownloadUrl(result) {
  if (!result || typeof result !== "object") return "";

  return (
    normalizeAnalyzerArtifactUrl(result.download_url) ||
    buildAnalyzerArtifactUrl(result.storage_key)
  );
}

export function normalizeAnalyzerResponseArtifactUrls(payload) {
  if (Array.isArray(payload)) {
    return payload.map((item) => normalizeAnalyzerResponseArtifactUrls(item));
  }

  if (!isPlainObject(payload)) {
    return payload;
  }

  const normalized = {};

  for (const [key, value] of Object.entries(payload)) {
    if ((key === "download_url" || key === "downloadUrl") && typeof value === "string") {
      normalized[key] = normalizeAnalyzerArtifactUrl(value);
      continue;
    }

    normalized[key] = normalizeAnalyzerResponseArtifactUrls(value);
  }

  return normalized;
}

export function clearAccessTokenCache() {
  cachedAccessToken = "";
  cachedAccessTokenExpiresAt = 0;
  pendingAccessTokenRequest = null;
}

export async function getAccessToken({ forceRefresh = false } = {}) {
  const now = Date.now();

  if (!forceRefresh && cachedAccessToken && now < cachedAccessTokenExpiresAt) {
    return cachedAccessToken;
  }

  if (!forceRefresh && pendingAccessTokenRequest) {
    return pendingAccessTokenRequest;
  }

  pendingAccessTokenRequest = fetch("/auth/access-token", {
    method: "GET",
    credentials: "include",
    cache: "no-store",
  })
    .then(async (res) => {
      if (!res.ok) {
        throw new Error("Could not get access token");
      }

      const data = await res.json();
      const token = data?.token;

      if (!token) {
        throw new Error("Could not get access token");
      }

      cachedAccessToken = token;
      // Keep this intentionally short because this client route does not expose
      // token expiry. It avoids duplicate token calls during page load while
      // still refreshing frequently enough for safety.
      cachedAccessTokenExpiresAt = Date.now() + 55_000;

      return token;
    })
    .finally(() => {
      pendingAccessTokenRequest = null;
    });

  return pendingAccessTokenRequest;
}

export async function postAnalyzerFeature(
  feature,
  formData,
  requiresAuth = false,
) {
  const headers = {};

  if (requiresAuth) {
    const token = await getAccessToken();
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`/api/analyzer/${feature}`, {
    method: "POST",
    credentials: "include",
    headers,
    body: formData,
  });

  const data = await res.json();

  if (!res.ok) {
    throw new Error(getErrorMessage(data));
  }

  return normalizeAnalyzerResponseArtifactUrls(data);
}


export async function postAnalyzerBatchFeature(
  feature,
  formData,
  { signal } = {},
) {
  const normalizedFeature = String(feature || "").trim().replace(/^\/+/, "");

  if (!normalizedFeature) {
    throw new Error("Batch feature is required.");
  }

  const token = await getAccessToken();
  const url = `/api/analyzer/batch/${normalizedFeature}`;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  });

  const data = await readResponsePayload(res);

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearAccessTokenCache();
    }

    notifyAccountInvalidated({ status: res.status, data, url });

    const error = new ApiClientError(getErrorMessage(data), {
      status: res.status,
      payload: data,
      url,
    });
    error.code = getAuthErrorCode(data);
    throw error;
  }

  return normalizeAnalyzerResponseArtifactUrls(data);
}

function getAuthErrorCode(data) {
  return String(
    data?.detail?.error ||
      data?.error ||
      data?.code ||
      data?.errorCode ||
      "",
  )
    .trim()
    .toLowerCase();
}

function shouldInvalidateAccount(status, data) {
  if (![401, 403].includes(Number(status))) return false;
  const code = getAuthErrorCode(data);
  return TERMINAL_AUTH_ERROR_CODES.has(code);
}

function notifyAccountInvalidated({ status, data, url }) {
  if (typeof window === "undefined") return;
  if (!shouldInvalidateAccount(status, data)) return;

  window.dispatchEvent(
    new CustomEvent(ACCOUNT_INVALIDATED_EVENT, {
      detail: {
        status,
        code: getAuthErrorCode(data),
        message: getErrorMessage(data, "Your session is no longer valid."),
        url: String(url || ""),
        at: Date.now(),
      },
    }),
  );
}

function getErrorMessage(data, fallback = "Request failed") {
  return (
    data?.error?.message ||
    data?.detail?.message ||
    data?.detail?.error ||
    data?.message ||
    fallback
  );
}

const AUTHENTICATED_BACKEND_API_PREFIXES = [
  "/api/organizations",
  "/api/conversations",
  "/api/calls",
  "/api/billing",
  "/api/analyzer/batch",
];

function shouldAttachAccessToken(url) {
  const path = typeof url === "string" ? url : url?.pathname || "";

  return AUTHENTICATED_BACKEND_API_PREFIXES.some(
    (prefix) => path === prefix || path.startsWith(`${prefix}/`),
  );
}

function normalizeAccountMe(data) {
  if (data?.user && data?.entitlement) {
    return data;
  }

  // Defensive fallback for older Auth0 profile-shaped responses.
  if (data && (data.sub || data.id || data.email || data.name || data.picture)) {
    return {
      user: {
        id: data.id || data.sub || null,
        name: data.name || data.nickname || null,
        email: data.email || null,
        picture: data.picture || null,
      },
      settings: data.settings || null,
      entitlement: data.entitlement || null,
    };
  }

  return data;
}

export async function getAccountMe({ signal, forceRefresh = false } = {}) {
  const query = forceRefresh ? `?refresh=${Date.now()}` : "";
  const res = await fetch(`/api/account/me${query}`, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: {
      Accept: "application/json",
      "Cache-Control": "no-cache",
      ...(forceRefresh ? { "X-Redocx-Account-Refresh": "1" } : {}),
    },
  });

  const data = await res.json().catch(() => null);

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearAccessTokenCache();
    }

    notifyAccountInvalidated({ status: res.status, data, url: "/api/account/me" });

    const error = new Error(getErrorMessage(data, "Could not load account."));
    error.name = shouldInvalidateAccount(res.status, data)
      ? "AccountInvalidatedError"
      : error.name;
    error.status = res.status;
    error.code = getAuthErrorCode(data);
    error.payload = data;
    throw error;
  }

  return normalizeAccountMe(data);
}

export async function deleteAccount({ signal } = {}) {
  const res = await fetch("/api/account/me", {
    method: "DELETE",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: {
      Accept: "application/json",
      "Cache-Control": "no-cache",
    },
  });

  const data = await readResponsePayload(res);

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearAccessTokenCache();
    }

    notifyAccountInvalidated({ status: res.status, data, url: "/api/account/me" });

    const error = new Error(
      getErrorMessage(data, "Could not delete account."),
    );
    error.status = res.status;
    error.code = getAuthErrorCode(data);
    error.payload = data;
    throw error;
  }

  clearAccessTokenCache();
  return data;
}

export async function requestPasswordChange({ signal, locale = "en" } = {}) {
  const res = await fetch("/api/account/change-password", {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: {
      Accept: "application/json",
      "Cache-Control": "no-cache",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ locale }),
  });

  const data = await readResponsePayload(res);

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      clearAccessTokenCache();
    }

    notifyAccountInvalidated({
      status: res.status,
      data,
      url: "/api/account/change-password",
    });

    const error = new Error(
      getErrorMessage(data, "Could not start password change."),
    );
    error.status = res.status;
    error.code = getAuthErrorCode(data);
    error.payload = data;
    throw error;
  }

  return data;
}

export async function requestForgotPassword({ email, locale = "en", signal } = {}) {
  const res = await fetch("/api/account/forgot-password", {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    signal,
    headers: {
      Accept: "application/json",
      "Cache-Control": "no-cache",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ email, locale }),
  });

  const data = await readResponsePayload(res);

  if (!res.ok) {
    const error = new Error(
      getErrorMessage(data, "Could not start password reset."),
    );
    error.status = res.status;
    error.code = getAuthErrorCode(data);
    error.payload = data;
    throw error;
  }

  return data;
}

export class ApiClientError extends Error {
  constructor(message, { status = null, payload = null, url = null } = {}) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.payload = payload;
    this.url = url;
  }
}

async function readResponsePayload(res) {
  const contentType = res.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    return await res.json().catch(() => null);
  }

  const text = await res.text().catch(() => "");

  if (!text) {
    return null;
  }

  return {
    detail: {
      message: text,
    },
  };
}

async function requestJson(url, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    signal,
    cache = "no-store",
  } = options;

  const fetchOptions = {
    method,
    credentials: "include",
    cache,
    signal,
    headers: {
      Accept: "application/json",
      ...headers,
    },
  };

  if (body !== undefined) {
    fetchOptions.headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(body);
  }

  const needsAccessToken = shouldAttachAccessToken(url);

  if (needsAccessToken && !fetchOptions.headers.Authorization) {
    const token = await getAccessToken();
    fetchOptions.headers.Authorization = `Bearer ${token}`;
  }

  let res = await fetch(url, fetchOptions);
  let data = await readResponsePayload(res);

  if (!res.ok && res.status === 401 && needsAccessToken) {
    clearAccessTokenCache();

    const refreshedToken = await getAccessToken({ forceRefresh: true });
    fetchOptions.headers.Authorization = `Bearer ${refreshedToken}`;

    res = await fetch(url, fetchOptions);
    data = await readResponsePayload(res);
  }

  if (!res.ok) {
    notifyAccountInvalidated({ status: res.status, data, url });

    const error = new ApiClientError(getErrorMessage(data), {
      status: res.status,
      payload: data,
      url,
    });
    error.code = getAuthErrorCode(data);
    throw error;
  }

  return data;
}

function encodeRequiredPathId(value, label) {
  const normalized = String(value ?? "").trim();

  if (!normalized) {
    throw new ApiClientError(`${label} is required.`);
  }

  return encodeURIComponent(normalized);
}

function normalizeLiveKitCallResponse(data, fallbackActionLabel) {
  const token = data?.livekit?.token;
  const serverUrl = data?.livekit?.server_url;
  const roomName = data?.livekit?.room_name;

  if (!data?.success) {
    throw new ApiClientError(`Could not ${fallbackActionLabel}.`, {
      payload: data,
    });
  }

  if (!token || !serverUrl || !roomName) {
    throw new ApiClientError(
      `The call was created, but LiveKit connection details were missing.`,
      {
        payload: data,
      },
    );
  }

  return data;
}

/**
 * Start a LiveKit-backed call for an existing conversation.
 *
 * Proxies to:
 * POST /api/conversations/{conversationId}/calls
 */
export async function startConversationCall(conversationId, options = {}) {
  const encodedConversationId = encodeRequiredPathId(
    conversationId,
    "conversationId",
  );

  const data = await requestJson(
    `/api/conversations/${encodedConversationId}/calls`,
    {
      method: "POST",
      signal: options.signal,
    },
  );

  return normalizeLiveKitCallResponse(data, "start call");
}

/**
 * Join an existing LiveKit-backed call.
 *
 * Proxies to:
 * POST /api/calls/{callSessionId}/join
 */
export async function joinCall(callSessionId, options = {}) {
  const encodedCallSessionId = encodeRequiredPathId(
    callSessionId,
    "callSessionId",
  );

  const data = await requestJson(`/api/calls/${encodedCallSessionId}/join`, {
    method: "POST",
    signal: options.signal,
  });

  return normalizeLiveKitCallResponse(data, "join call");
}

/**
 * Leave an existing call.
 *
 * Proxies to:
 * POST /api/calls/{callSessionId}/leave
 */
export async function leaveCall(callSessionId, options = {}) {
  const encodedCallSessionId = encodeRequiredPathId(
    callSessionId,
    "callSessionId",
  );

  return requestJson(`/api/calls/${encodedCallSessionId}/leave`, {
    method: "POST",
    signal: options.signal,
  });
}

/**
 * Decline an incoming or invited call.
 *
 * Proxies to:
 * POST /api/calls/{callSessionId}/decline
 */
export async function declineCall(callSessionId, options = {}) {
  const encodedCallSessionId = encodeRequiredPathId(
    callSessionId,
    "callSessionId",
  );

  return requestJson(`/api/calls/${encodedCallSessionId}/decline`, {
    method: "POST",
    signal: options.signal,
  });
}

function normalizeWebSocketBaseUrl(value) {
  const raw = String(value || "").trim();

  if (!raw) return "";

  const withoutTrailingSlash = raw.replace(/\/+$/, "");

  if (withoutTrailingSlash.startsWith("wss://") || withoutTrailingSlash.startsWith("ws://")) {
    return withoutTrailingSlash;
  }

  if (withoutTrailingSlash.startsWith("https://")) {
    return `wss://${withoutTrailingSlash.slice("https://".length)}`;
  }

  if (withoutTrailingSlash.startsWith("http://")) {
    return `ws://${withoutTrailingSlash.slice("http://".length)}`;
  }

  return withoutTrailingSlash;
}

function getRealtimeBackendBaseUrl() {
  const configured =
    process.env.NEXT_PUBLIC_BACKEND_WS_URL ||
    process.env.NEXT_PUBLIC_BACKEND_BASE_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "";

  if (configured) {
    return normalizeWebSocketBaseUrl(configured);
  }

  if (typeof window === "undefined") {
    return "";
  }

  const { hostname } = window.location;

  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return "ws://localhost:8000";
  }

  return "";
}
function buildQueryString(params = {}) {
  const searchParams = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }

    searchParams.set(key, String(value));
  }

  const query = searchParams.toString();
  return query ? `?${query}` : "";
}

function assertObjectPayload(payload, label) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ApiClientError(`${label} must be an object.`);
  }

  return payload;
}

function normalizeMessageBody(body) {
  const normalized = String(body ?? "").trim();

  if (!normalized) {
    throw new ApiClientError("Message body is required.");
  }

  return normalized;
}

function normalizeOptionalCaption(caption) {
  return String(caption ?? "").trim().slice(0, 5000);
}

function assertAttachmentFile(file) {
  if (!file || typeof file !== "object") {
    throw new ApiClientError("Attachment file is required.");
  }

  if (typeof File !== "undefined" && !(file instanceof File)) {
    throw new ApiClientError("Attachment must be a browser File object.");
  }

  return file;
}

function extractFilenameFromContentDisposition(value) {
  const header = String(value || "");
  if (!header) return "attachment";

  const encodedMatch = header.match(/filename\*=UTF-8''([^;]+)/i);
  if (encodedMatch?.[1]) {
    try {
      return decodeURIComponent(encodedMatch[1]);
    } catch {
      return encodedMatch[1];
    }
  }

  const plainMatch = header.match(/filename="?([^";]+)"?/i);
  return plainMatch?.[1] || "attachment";
}

function normalizePresenceStatus(status) {
  const normalized = String(status ?? "").trim().toLowerCase();

  if (!["online", "offline", "in_call"].includes(normalized)) {
    throw new ApiClientError(
      "Presence status must be one of: online, offline, in_call.",
    );
  }

  return normalized;
}

/**
 * Load conversations for a Business/Enterprise organization.
 *
 * Proxies to:
 * GET /api/organizations/{organizationId}/conversations
 */
export async function getOrganizationConversations(
  organizationId,
  options = {},
) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );

  return requestJson(
    `/api/organizations/${encodedOrganizationId}/conversations`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}

/**
 * Load recent incoming team-message notifications for the current user.
 *
 * Optional options:
 * - afterMessageId: only return messages with id greater than this value
 * - limit: max number of notifications to return
 *
 * Proxies to:
 * GET /api/organizations/{organizationId}/message-notifications
 */
export async function getOrganizationMessageNotifications(
  organizationId,
  options = {},
) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );

  const queryString = buildQueryString({
    after_message_id: options.afterMessageId,
    limit: options.limit,
  });

  return requestJson(
    `/api/organizations/${encodedOrganizationId}/message-notifications${queryString}`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}

/**
 * Build an authenticated WebSocket URL for organization realtime events.
 *
 * Browser WebSocket cannot send custom Authorization headers, so the Auth0
 * access token is passed as a short-lived query parameter over ws/wss.
 */
export async function getOrganizationRealtimeWebSocketUrl(organizationId) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );
  const token = await getAccessToken();
  const backendBase = getRealtimeBackendBaseUrl();

  if (!backendBase) {
    throw new ApiClientError(
      "Realtime backend URL is not configured. Set NEXT_PUBLIC_BACKEND_WS_URL.",
    );
  }

  const queryString = buildQueryString({ token });
  return `${backendBase}/api/v1/organizations/${encodedOrganizationId}/realtime${queryString}`;
}

/**
 * Build an authenticated user-scoped WebSocket URL for account/dashboard events.
 *
 * This is separate from the organization realtime channel because a pending
 * invitee is not yet an active organization member.
 */
export async function getAccountRealtimeWebSocketUrl() {
  const token = await getAccessToken();
  const backendBase = getRealtimeBackendBaseUrl();

  if (!backendBase) {
    throw new ApiClientError(
      "Realtime backend URL is not configured. Set NEXT_PUBLIC_BACKEND_WS_URL.",
    );
  }

  const queryString = buildQueryString({ token });
  return `${backendBase}/api/v1/account/realtime${queryString}`;
}

/**
 * Create a DM or group conversation.
 *
 * Expected payload examples:
 *
 * DM:
 * { type: "dm", member_user_ids: ["auth0|member"] }
 *
 * Group:
 * { type: "group", name: "Legal Review", member_user_ids: ["auth0|member"] }
 *
 * Proxies to:
 * POST /api/organizations/{organizationId}/conversations
 */
export async function createConversation(
  organizationId,
  payload,
  options = {},
) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );

  const body = assertObjectPayload(payload, "conversation payload");

  return requestJson(
    `/api/organizations/${encodedOrganizationId}/conversations`,
    {
      method: "POST",
      body,
      signal: options.signal,
    },
  );
}

/**
 * Load messages for a conversation.
 *
 * Optional options:
 * - limit: number from 1 to 100
 * - beforeMessageId: message id for pagination
 *
 * Proxies to:
 * GET /api/conversations/{conversationId}/messages
 */
export async function getConversationMessages(
  conversationId,
  options = {},
) {
  const encodedConversationId = encodeRequiredPathId(
    conversationId,
    "conversationId",
  );

  const queryString = buildQueryString({
    limit: options.limit,
    before_message_id: options.beforeMessageId,
  });

  return requestJson(
    `/api/conversations/${encodedConversationId}/messages${queryString}`,
    {
      method: "GET",
      signal: options.signal,
    },
  );
}

/**
 * Send a text message to a conversation.
 *
 * Proxies to:
 * POST /api/conversations/{conversationId}/messages
 */
export async function sendConversationMessage(
  conversationId,
  body,
  options = {},
) {
  const encodedConversationId = encodeRequiredPathId(
    conversationId,
    "conversationId",
  );

  return requestJson(
    `/api/conversations/${encodedConversationId}/messages`,
    {
      method: "POST",
      body: {
        body: normalizeMessageBody(body),
      },
      signal: options.signal,
    },
  );
}

/**
 * Send a file/image/audio/video attachment to a conversation.
 *
 * Proxies to:
 * POST /api/conversations/{conversationId}/attachments
 */
export async function sendConversationAttachment(
  conversationId,
  file,
  options = {},
) {
  const encodedConversationId = encodeRequiredPathId(
    conversationId,
    "conversationId",
  );
  const attachmentFile = assertAttachmentFile(file);
  const formData = new FormData();

  formData.append("file", attachmentFile);

  const caption = normalizeOptionalCaption(options.caption);
  if (caption) {
    formData.append("caption", caption);
  }

  if (options.clientMessageId) {
    formData.append("client_message_id", String(options.clientMessageId));
  }

  const token = await getAccessToken();
  let res = await fetch(`/api/conversations/${encodedConversationId}/attachments`, {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    signal: options.signal,
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  });
  let data = await readResponsePayload(res);

  if (!res.ok && res.status === 401) {
    clearAccessTokenCache();
    const refreshedToken = await getAccessToken({ forceRefresh: true });

    res = await fetch(`/api/conversations/${encodedConversationId}/attachments`, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      signal: options.signal,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${refreshedToken}`,
      },
      body: formData,
    });
    data = await readResponsePayload(res);
  }

  if (!res.ok) {
    throw new ApiClientError(getErrorMessage(data, "Could not send attachment."), {
      status: res.status,
      payload: data,
      url: `/api/conversations/${encodedConversationId}/attachments`,
    });
  }

  return data;
}

/**
 * Download a conversation attachment with the same Auth0 bearer-token path used
 * by the JSON API. Returns a Blob plus the safest filename available.
 */
export async function downloadConversationAttachment(downloadUrl, options = {}) {
  const url = String(downloadUrl || "").trim();
  if (!url) {
    throw new ApiClientError("Attachment download URL is required.");
  }

  const token = await getAccessToken();
  let res = await fetch(url, {
    method: "GET",
    credentials: "include",
    cache: "no-store",
    signal: options.signal,
    headers: {
      Authorization: `Bearer ${token}`,
    },
  });

  if (!res.ok && res.status === 401) {
    clearAccessTokenCache();
    const refreshedToken = await getAccessToken({ forceRefresh: true });
    res = await fetch(url, {
      method: "GET",
      credentials: "include",
      cache: "no-store",
      signal: options.signal,
      headers: {
        Authorization: `Bearer ${refreshedToken}`,
      },
    });
  }

  if (!res.ok) {
    const data = await readResponsePayload(res);
    throw new ApiClientError(getErrorMessage(data, "Could not download attachment."), {
      status: res.status,
      payload: data,
      url,
    });
  }

  const blob = await res.blob();
  const filename = extractFilenameFromContentDisposition(
    res.headers.get("content-disposition"),
  );

  return { blob, filename };
}

/**
 * Load organization member presence.
 *
 * Proxies to:
 * GET /api/organizations/{organizationId}/presence
 */
export async function getOrganizationPresence(
  organizationId,
  options = {},
) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );

  return requestJson(`/api/organizations/${encodedOrganizationId}/presence`, {
    method: "GET",
    signal: options.signal,
  });
}

/**
 * Update the current user's presence inside an organization.
 *
 * Valid statuses:
 * - online
 * - offline
 * - in_call
 *
 * Proxies to:
 * POST /api/organizations/{organizationId}/presence
 */
export async function updateOrganizationPresence(
  organizationId,
  status,
  options = {},
) {
  const encodedOrganizationId = encodeRequiredPathId(
    organizationId,
    "organizationId",
  );

  return requestJson(`/api/organizations/${encodedOrganizationId}/presence`, {
    method: "POST",
    body: {
      status: normalizePresenceStatus(status),
    },
    signal: options.signal,
  });
}


export async function getBillingPlans() {
  return requestJson("/api/billing/plans", {
    method: "GET",
  });
}

export async function createBillingUpgradeIntent(targetPlan, options = {}) {
  const body = {
    target_plan: targetPlan,
  };

  const provider = options.provider || options.providerName;
  const regionHint = options.regionHint || options.region_hint;

  if (provider) {
    body.provider = provider;
  }

  if (regionHint) {
    body.region_hint = regionHint;
  }

  return requestJson("/api/billing/upgrade-intents", {
    method: "POST",
    body,
  });
}
