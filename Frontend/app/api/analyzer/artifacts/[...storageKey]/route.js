import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
function getBackendBaseUrl() {
  return String(
    process.env.BACKEND_URL ||
      process.env.BACKEND_BASE_URL ||
      process.env.BACKEND_API_URL ||
      process.env.API_BASE_URL ||
      "",
  ).replace(/\/+$/, "");
}

function backendUrlNotConfiguredResponse() {
  return NextResponse.json(
    {
      detail: {
        error: "backend_url_not_configured",
        message:
          "Backend URL is not configured. Set BACKEND_URL, BACKEND_BASE_URL, BACKEND_API_URL, or API_BASE_URL.",
      },
    },
    { status: 500 },
  );
}

function getClientIp(req) {
  const forwardedFor = req.headers.get("x-forwarded-for");
  const vercelForwardedFor = req.headers.get("x-vercel-forwarded-for");
  const realIp = req.headers.get("x-real-ip");
  const cloudflareIp = req.headers.get("cf-connecting-ip");

  return (
    cloudflareIp ||
    realIp ||
    vercelForwardedFor?.split(",")[0]?.trim() ||
    forwardedFor?.split(",")[0]?.trim() ||
    ""
  );
}

function buildBackendHeaders(req, accessToken = "") {
  const headers = {};

  const incomingCookie = req.headers.get("cookie");
  if (incomingCookie) {
    headers.Cookie = incomingCookie;
  }

  const forwardedFor = req.headers.get("x-forwarded-for");
  const clientIp = getClientIp(req);

  if (forwardedFor) {
    headers["X-Forwarded-For"] = forwardedFor;
  } else if (clientIp) {
    headers["X-Forwarded-For"] = clientIp;
  }

  if (clientIp) {
    headers["X-Real-IP"] = clientIp;
  }

  const userAgent = req.headers.get("user-agent");
  if (userAgent) {
    headers["User-Agent"] = userAgent;
  }

  const range = req.headers.get("range");
  if (range) {
    headers.Range = range;
  }

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  return headers;
}

async function getBackendAccessToken(req) {
  try {
    // Route handlers can retrieve the access token directly from the encrypted
    // Auth0 web session. Do not gate this behind a separate getSession() call.
    const tokenSet = await auth0.getAccessToken();
    const token =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";

    if (token) {
      return token;
    }
  } catch {
    // Fall through to a caller-supplied token. The backend validates it.
  }

  const incomingAuthorization = req.headers.get("authorization") || "";
  const bearerMatch = incomingAuthorization.match(/^Bearer\s+(.+)$/i);
  return bearerMatch?.[1]?.trim() || "";
}

function forwardBackendSetCookies(backendRes, response) {
  const getSetCookie = backendRes.headers.getSetCookie;

  if (typeof getSetCookie === "function") {
    const setCookies = getSetCookie.call(backendRes.headers);

    if (Array.isArray(setCookies) && setCookies.length > 0) {
      for (const value of setCookies) {
        response.headers.append("Set-Cookie", value);
      }
      return response;
    }
  }

  const setCookie = backendRes.headers.get("set-cookie");
  if (setCookie) {
    response.headers.append("Set-Cookie", setCookie);
  }

  return response;
}

function jsonWithBackendCookies(data, backendRes) {
  const response = NextResponse.json(data, { status: backendRes.status });
  return forwardBackendSetCookies(backendRes, response);
}

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function cleanArtifactStorageKey(value) {
  let key = String(value || "")
    .trim()
    .replaceAll("\\", "/");

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

function encodeStorageKeyPath(storageKey) {
  return cleanArtifactStorageKey(storageKey)
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
}

const INLINE_PREVIEW_CONTENT_TYPES = new Set([
  "application/pdf",
  "image/jpeg",
  "image/png",
  "audio/mpeg",
  "audio/mp4",
  "audio/ogg",
  "audio/webm",
  "audio/wav",
  "audio/aac",
  "audio/flac",
  "video/mp4",
  "video/webm",
]);

function normalizeContentType(value) {
  return String(value || "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
}

function inlineContentDisposition(value) {
  const current = String(value || "").trim();
  if (!current) return 'inline; filename="preview"';
  return current.replace(/^attachment\b/i, "inline");
}

function encodeRfc5987Value(value) {
  return encodeURIComponent(value).replace(/[!'()*]/g, (character) =>
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`,
  );
}

function fallbackContentDisposition(filename, disposition = "attachment") {
  const raw = String(filename || "artifact")
    .replaceAll("\\", "/")
    .split("/")
    .pop();
  const clean = String(raw || "artifact")
    .replace(/[\x00-\x1F\x7F"]/g, "")
    .trim() || "artifact";
  const ascii = clean.replace(/[^\x20-\x7E]/g, "").trim() || "artifact";
  return `${disposition}; filename="${ascii}"; filename*=UTF-8''${encodeRfc5987Value(clean)}`;
}

export async function GET(req, context) {
  const params = await context.params;
  const { storageKey = [] } = params || {};

  const resolvedStorageKey = Array.isArray(storageKey)
    ? storageKey.join("/")
    : String(storageKey || "");

  const cleanStorageKey = cleanArtifactStorageKey(resolvedStorageKey);

  if (!cleanStorageKey) {
    return NextResponse.json(
      {
        detail: {
          error: "missing_storage_key",
          message: "Missing artifact storage key.",
        },
      },
      { status: 400 },
    );
  }

  const backendBaseUrl = getBackendBaseUrl();
  if (!backendBaseUrl) {
    return backendUrlNotConfiguredResponse();
  }

  const accessToken = await getBackendAccessToken(req);
  const headers = buildBackendHeaders(req, accessToken);

  const encodedStorageKey = encodeStorageKeyPath(cleanStorageKey);
  const requestUrl = new URL(req.url);
  const requestedDisposition = requestUrl.searchParams.get("disposition");
  const requestedDownloadName = requestUrl.searchParams.get("download_name");
  const wantsInlinePreview = requestedDisposition === "inline";
  const backendQuery = new URLSearchParams();
  if (wantsInlinePreview) backendQuery.set("disposition", "inline");
  if (requestedDownloadName) {
    backendQuery.set("download_name", requestedDownloadName);
  }
  const queryString = backendQuery.toString();
  const backendUrl = `${backendBaseUrl}/api/v1/analyzer/artifacts/${encodedStorageKey}${queryString ? `?${queryString}` : ""}`;

  let backendRes;

  try {
    backendRes = await fetch(backendUrl, {
      method: "GET",
      headers,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "artifact_backend_unreachable",
          message: "Could not reach backend artifact service.",
        },
      },
      { status: 502 },
    );
  }

  if (!backendRes.ok) {
    const contentType = backendRes.headers.get("content-type") || "";
    const errorData = contentType.includes("application/json")
      ? await backendRes.json().catch(() => ({}))
      : {
          detail: {
            message: await backendRes.text().catch(() => ""),
          },
        };

    return jsonWithBackendCookies(errorData, backendRes);
  }

  const contentType =
    backendRes.headers.get("content-type") || "application/pdf";

  const backendContentDisposition =
    backendRes.headers.get("content-disposition") ||
    fallbackContentDisposition(
      requestedDownloadName || cleanStorageKey.split("/").pop(),
    );

  const contentDisposition =
    wantsInlinePreview &&
    INLINE_PREVIEW_CONTENT_TYPES.has(normalizeContentType(contentType))
      ? inlineContentDisposition(backendContentDisposition)
      : backendContentDisposition;

  const responseHeaders = {
    "content-type": contentType,
    "content-disposition": contentDisposition,
    "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox",
    "cross-origin-resource-policy": "same-origin",
  };

  for (const headerName of ["accept-ranges", "content-range", "content-length"]) {
    const value = backendRes.headers.get(headerName);
    if (value) responseHeaders[headerName] = value;
  }

  const response = new NextResponse(backendRes.body, {
    status: backendRes.status,
    headers: responseHeaders,
  });

  return forwardBackendSetCookies(backendRes, response);
}

export async function HEAD(req, context) {
  const response = await GET(req, context);
  return new NextResponse(null, {
    status: response.status,
    headers: response.headers,
  });
}
