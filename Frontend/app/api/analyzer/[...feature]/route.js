import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ALLOWED_FEATURE_PATHS = new Set([
  "convert",
  "summarize",
  "grammar-correct",
  "translate",
  "transcribe",
  "explain",
  "generate-questions",
  "generate-answers",
  "redact",
  "data-mask",
  "compliance",
  "structured-extraction",
  "e-signature",
  "pdf/combine",
  "pdf/split",
  "pdf/edit",
  "pdf/compress",
]);

function normalizeFeaturePath(feature) {
  return (Array.isArray(feature) ? feature : [feature])
    .map((segment) => String(segment || "").trim())
    .filter(Boolean)
    .join("/");
}

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

function backendUrlNotConfiguredResponse() {
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

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  return headers;
}

async function getBackendAccessToken(req) {
  try {
    const session = await auth0.getSession();

    if (session) {
      const tokenSet = await auth0.getAccessToken();
      const token =
        typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";

      if (token) {
        return token;
      }
    }
  } catch {
    // The backend remains authoritative for validating a bearer token.
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

async function readBackendPayload(backendRes) {
  const contentType = backendRes.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    return backendRes.json().catch(() => ({}));
  }

  const message = await backendRes.text().catch(() => "");
  return {
    detail: {
      message: message || "Request failed.",
    },
  };
}

export async function POST(req, context) {
  const params = await context.params;
  const featurePath = normalizeFeaturePath(params?.feature);

  if (!ALLOWED_FEATURE_PATHS.has(featurePath)) {
    return jsonNoStore(
      {
        detail: {
          error: "invalid_feature",
          message: "Unsupported analyzer feature.",
        },
      },
      400,
    );
  }

  const backendBaseUrl = getBackendBaseUrl();
  if (!backendBaseUrl) {
    return backendUrlNotConfiguredResponse();
  }

  let outboundFormData;

  try {
    // Forward the parsed FormData directly. Rebuilding every File through
    // arrayBuffer() needlessly duplicates the complete upload in memory.
    outboundFormData = await req.formData();
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "invalid_multipart_form",
          message: "The multipart form data could not be read.",
        },
      },
      400,
    );
  }

  const accessToken = await getBackendAccessToken(req);
  const headers = buildBackendHeaders(req, accessToken);

  let backendRes;

  try {
    backendRes = await fetch(
      `${backendBaseUrl}/api/v1/analyzer/${featurePath}`,
      {
        method: "POST",
        headers,
        body: outboundFormData,
        cache: "no-store",
      },
    );
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "analyzer_backend_unreachable",
          message: "Could not reach backend analyzer service.",
        },
      },
      502,
    );
  }

  const data = await readBackendPayload(backendRes);
  const response = jsonNoStore(data, backendRes.status);
  return forwardBackendSetCookies(backendRes, response);
}
