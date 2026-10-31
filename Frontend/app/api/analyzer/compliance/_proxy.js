import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const ARTIFACT_ROUTE_PREFIX = "/api/analyzer/artifacts/";
const ARTIFACT_API_PREFIXES = [
  "/api/analyzer/artifacts/",
  "/api/v1/analyzer/artifacts/",
];
const ARTIFACT_RELATIVE_PREFIXES = [
  ...ARTIFACT_API_PREFIXES,
  "/artifacts/",
];
const ARTIFACT_PARSE_BASE = "https://redocx.invalid";

function backendBaseUrl() {
  const configured = String(
    process.env.BACKEND_URL ||
      process.env.BACKEND_BASE_URL ||
      process.env.BACKEND_API_URL ||
      process.env.API_BASE_URL ||
      "",
  ).replace(/\/+$/, "");
  if (!configured) return "";
  try {
    const parsed = new URL(configured);
    return ["http:", "https:"].includes(parsed.protocol) ? configured : "";
  } catch {
    return "";
  }
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
  const candidate = String(value || "").split(",", 1)[0].trim();
  if (!candidate || candidate.length > 128) return "";
  return /^[0-9a-f:.]+$/i.test(candidate) ? candidate : "";
}

function clientIp(req) {
  for (const value of [
    req.headers.get("cf-connecting-ip"),
    req.headers.get("x-vercel-forwarded-for"),
    req.headers.get("x-real-ip"),
    req.headers.get("x-forwarded-for"),
  ]) {
    const normalized = normalizeClientIp(value);
    if (normalized) return normalized;
  }
  return "";
}

async function backendAccessToken(req) {
  try {
    const tokenSet = await auth0.getAccessToken();
    const token =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    if (token) return token;
  } catch {
    // The backend remains the authority for a caller-supplied bearer token.
  }
  const authorization = req.headers.get("authorization") || "";
  return authorization.match(/^Bearer\s+(.+)$/i)?.[1]?.trim() || "";
}

async function backendHeaders(req) {
  const headers = {};
  const cookie = req.headers.get("cookie");
  const userAgent = req.headers.get("user-agent");
  const address = clientIp(req);
  const accessToken = await backendAccessToken(req);
  if (cookie) headers.Cookie = cookie;
  if (address) {
    headers["X-Forwarded-For"] = address;
    headers["X-Real-IP"] = address;
  }
  if (userAgent) headers["User-Agent"] = userAgent;
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return headers;
}

function forwardSetCookies(backendResponse, response) {
  const retryAfter = backendResponse.headers.get("retry-after");
  if (retryAfter) response.headers.set("Retry-After", retryAfter);
  const getter = backendResponse.headers.getSetCookie;
  if (typeof getter === "function") {
    const values = getter.call(backendResponse.headers);
    if (Array.isArray(values) && values.length) {
      for (const value of values) response.headers.append("Set-Cookie", value);
      return response;
    }
  }
  const value = backendResponse.headers.get("set-cookie");
  if (value) response.headers.append("Set-Cookie", value);
  return response;
}

async function readPayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => ({}));
  }
  const message = await response.text().catch(() => "");
  return { detail: { message: message || "Request failed." } };
}

function normalizeArtifactUrl(value) {
  const raw = String(value || "").trim().replaceAll("\\", "/");
  if (!raw) return "";
  const absolute = /^https?:\/\//i.test(raw);
  let parsed;
  try {
    parsed = new URL(raw, ARTIFACT_PARSE_BASE);
  } catch {
    return raw;
  }
  const prefixes = absolute
    ? ARTIFACT_API_PREFIXES
    : ARTIFACT_RELATIVE_PREFIXES;
  const prefix = prefixes.find((item) => parsed.pathname.startsWith(item));
  if (!prefix) return raw;
  const storageKey = parsed.pathname.slice(prefix.length).replace(/^\/+/, "");
  return storageKey
    ? `${ARTIFACT_ROUTE_PREFIX}${storageKey}${parsed.search}${parsed.hash}`
    : raw;
}

function normalizeArtifactUrls(value) {
  if (Array.isArray(value)) return value.map(normalizeArtifactUrls);
  if (!value || typeof value !== "object") return value;
  const containers = new Set([
    "analyzer_response", "response", "result", "artifact", "output_artifact", "data",
  ]);
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      if (["download_url", "downloadUrl"].includes(key) && typeof item === "string") {
        return [key, normalizeArtifactUrl(item)];
      }
      return [key, containers.has(key) ? normalizeArtifactUrls(item) : item];
    }),
  );
}

export async function proxyComplianceRequest(
  req,
  { backendPath, method = "POST", includeFormData = false, artifacts = false },
) {
  const baseUrl = backendBaseUrl();
  if (!baseUrl) {
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

  let body;
  if (includeFormData) {
    try {
      // Reuse the parsed Files. Rebuilding each one through arrayBuffer() would
      // duplicate every compliance upload in server memory.
      body = await req.formData();
    } catch {
      return jsonNoStore(
        {
          detail: {
            error: "invalid_multipart_form",
            message: "The compliance upload form could not be read.",
          },
        },
        400,
      );
    }
  }

  const controller = new AbortController();
  const abortFromClient = () => controller.abort();
  if (req.signal?.aborted) controller.abort();
  req.signal?.addEventListener?.("abort", abortFromClient, { once: true });
  let timedOut = false;
  let abortHeaders;
  const aborted = new Promise((_, reject) => {
    abortHeaders = () => reject(Object.assign(new Error("Compliance request aborted"), { name: "AbortError" }));
    if (controller.signal.aborted) abortHeaders();
    else controller.signal.addEventListener("abort", abortHeaders, { once: true });
  });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, method === "GET" ? 15_000 : 240_000);
  try {
    const headers = await Promise.race([backendHeaders(req), aborted]);
    const backendResponse = await fetch(`${baseUrl}${backendPath}`, {
      method,
      headers,
      ...(includeFormData ? { body } : {}),
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await readPayload(backendResponse);
    const response = jsonNoStore(
      artifacts ? normalizeArtifactUrls(payload) : payload,
      backendResponse.status,
    );
    return forwardSetCookies(backendResponse, response);
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: timedOut ? "compliance_backend_timeout" : "compliance_backend_unreachable",
          message: timedOut
            ? "The Compliance service exceeded its response time limit."
            : "Could not reach the backend compliance service.",
        },
      },
      timedOut ? 504 : 502,
    );
  } finally {
    clearTimeout(timeout);
    controller.signal.removeEventListener("abort", abortHeaders);
    req.signal?.removeEventListener?.("abort", abortFromClient);
  }
}
