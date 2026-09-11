import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function getBackendBaseUrl() {
  return String(
    process.env.BACKEND_URL ||
      process.env.BACKEND_BASE_URL ||
      process.env.BACKEND_API_URL ||
      process.env.API_BASE_URL ||
      "",
  ).replace(/\/+$/, "");
}

function jsonNoStore(payload, status = 200) {
  const response = NextResponse.json(payload, { status });
  response.headers.set(
    "Cache-Control",
    "private, no-cache, no-store, must-revalidate, max-age=0",
  );
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Expires", "0");
  return response;
}

function normalizeClientIp(value) {
  const candidate = String(value || "")
    .split(",", 1)[0]
    .trim();

  if (!candidate || candidate.length > 128) return "";
  return /^[0-9a-f:.]+$/i.test(candidate) ? candidate : "";
}

function getClientIp(req) {
  for (const value of [
    req.headers.get("cf-connecting-ip"),
    req.headers.get("x-vercel-forwarded-for"),
    req.headers.get("x-real-ip"),
    req.headers.get("x-forwarded-for"),
  ]) {
    const candidate = normalizeClientIp(value);
    if (candidate) return candidate;
  }
  return "";
}

async function getBackendAccessToken(req) {
  try {
    const tokenSet = await auth0.getAccessToken();
    const token =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    if (token) return token;
  } catch {
    // Fall through to a caller-supplied bearer token. FastAPI remains authoritative.
  }

  const incomingAuthorization = req.headers.get("authorization") || "";
  const bearerMatch = incomingAuthorization.match(/^Bearer\s+(.+)$/i);
  return bearerMatch?.[1]?.trim() || "";
}

function buildBackendHeaders(req, accessToken) {
  const headers = {
    Accept: "*/*",
  };

  const incomingCookie = req.headers.get("cookie");
  if (incomingCookie) headers.Cookie = incomingCookie;

  const clientIp = getClientIp(req);
  if (clientIp) {
    headers["X-Forwarded-For"] = clientIp;
    headers["X-Real-IP"] = clientIp;
  }

  const userAgent = req.headers.get("user-agent");
  if (userAgent) headers["User-Agent"] = userAgent;

  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return headers;
}

function forwardBackendSetCookies(backendRes, response) {
  const getSetCookie = backendRes.headers.getSetCookie;

  if (typeof getSetCookie === "function") {
    const setCookies = getSetCookie.call(backendRes.headers);
    if (Array.isArray(setCookies) && setCookies.length > 0) {
      for (const value of setCookies) response.headers.append("Set-Cookie", value);
      return response;
    }
  }

  const setCookie = backendRes.headers.get("set-cookie");
  if (setCookie) response.headers.append("Set-Cookie", setCookie);
  return response;
}

async function readBackendError(backendRes) {
  const contentType = backendRes.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return backendRes.json().catch(() => ({}));
  }

  const message = await backendRes.text().catch(() => "");
  return {
    detail: {
      message: message || "Vault download failed.",
    },
  };
}

export async function GET(req, context) {
  const params = await context.params;
  const itemId = String(params?.itemId || "").trim();

  if (!itemId || itemId.length > 512 || /[\x00-\x1F\x7F]/u.test(itemId)) {
    return jsonNoStore(
      {
        detail: {
          error: "invalid_vault_item_id",
          message: "A valid Vault item ID is required.",
        },
      },
      400,
    );
  }

  const backendBaseUrl = getBackendBaseUrl();
  if (!backendBaseUrl) {
    return jsonNoStore(
      {
        detail: {
          error: "backend_url_not_configured",
          message:
            "Backend URL is not configured. Set BACKEND_URL, BACKEND_BASE_URL, BACKEND_API_URL, or API_BASE_URL.",
        },
      },
      500,
    );
  }

  const accessToken = await getBackendAccessToken(req);
  if (!accessToken) {
    return jsonNoStore(
      {
        detail: {
          error: "authorization_required",
          message: "You must be signed in.",
        },
      },
      401,
    );
  }

  const backendUrl = `${backendBaseUrl}/api/v1/analyzer/vault/items/${encodeURIComponent(itemId)}/download`;
  let backendRes;

  try {
    backendRes = await fetch(backendUrl, {
      method: "GET",
      headers: buildBackendHeaders(req, accessToken),
      cache: "no-store",
    });
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "vault_backend_unreachable",
          message: "Could not reach the Vault service.",
        },
      },
      502,
    );
  }

  if (!backendRes.ok) {
    const data = await readBackendError(backendRes);
    const response = jsonNoStore(data, backendRes.status);
    return forwardBackendSetCookies(backendRes, response);
  }

  const responseHeaders = {
    "content-type":
      backendRes.headers.get("content-type") || "application/octet-stream",
    "content-disposition":
      backendRes.headers.get("content-disposition") || "attachment",
    "cache-control": "private, no-store, max-age=0",
    pragma: "no-cache",
    "x-content-type-options": "nosniff",
    "content-security-policy": "sandbox",
    "cross-origin-resource-policy": "same-origin",
    "referrer-policy": "no-referrer",
  };

  for (const headerName of ["content-length", "accept-ranges", "content-range"]) {
    const value = backendRes.headers.get(headerName);
    if (value) responseHeaders[headerName] = value;
  }

  const response = new NextResponse(backendRes.body, {
    status: backendRes.status,
    headers: responseHeaders,
  });
  return forwardBackendSetCookies(backendRes, response);
}
