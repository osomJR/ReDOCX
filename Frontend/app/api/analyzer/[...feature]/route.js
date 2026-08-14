import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
import {
  validateBrowserAuxiliaryPromptText,
  validateBrowserInlineText,
} from "@/lib/secure_upload_policy";

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
  "e-signature/layout",
  "pdf/combine",
  "pdf/split",
  "pdf/edit",
  "pdf/compress",
  "batch/convert",
  "batch/summarize",
  "batch/grammar-correct",
  "batch/translate",
  "batch/explain",
  "batch/generate-questions",
  "batch/generate-answers",
  "batch/pdf/compress",
  "batch/transcribe",
]);

const INLINE_TEXT_FEATURE_PATHS = new Set([
  "summarize",
  "grammar-correct",
  "translate",
  "explain",
  "generate-questions",
  "generate-answers",
]);

const QUESTIONS_JSON_FEATURE_PATHS = new Set([
  "generate-answers",
  "batch/generate-answers",
]);

function nonEmptyStringFields(formData, fieldName) {
  return formData
    .getAll(fieldName)
    .filter((value) => typeof value === "string" && value.trim().length > 0);
}

function validateAnalyzerFormData(featurePath, formData) {
  if (INLINE_TEXT_FEATURE_PATHS.has(featurePath)) {
    const textValues = nonEmptyStringFields(formData, "text");

    if (textValues.length > 1) {
      return {
        error: "duplicate_inline_text",
        message: "Provide the inline text field only once.",
      };
    }

    if (textValues.length === 1) {
      const message = validateBrowserInlineText(textValues[0]);
      if (message) {
        return { error: "unsafe_inline_text", message };
      }
    }
  }

  if (QUESTIONS_JSON_FEATURE_PATHS.has(featurePath)) {
    const questionValues = nonEmptyStringFields(formData, "questions_json");

    if (questionValues.length > 1) {
      return {
        error: "duplicate_questions_json",
        message: "Provide the questions_json field only once.",
      };
    }

    if (questionValues.length === 1) {
      const message = validateBrowserAuxiliaryPromptText(
        questionValues[0],
        "Generated questions",
      );
      if (message) {
        return { error: "unsafe_questions_input", message };
      }
    }
  }

  return null;
}


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
    // getAccessToken() is valid directly in an App Router route handler and
    // refreshes the web-session token when required. A separate getSession()
    // guard can incorrectly suppress token retrieval in request contexts where
    // the session is available to the token API but was not materialized first.
    const tokenSet = await auth0.getAccessToken();
    const token =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";

    if (token) {
      return token;
    }
  } catch {
    // Fall through to a caller-supplied Bearer token. FastAPI remains the
    // authority for signature, audience, issuer, expiry, and user validation.
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

const ANALYZER_ARTIFACT_ROUTE_PREFIX = "/api/analyzer/artifacts/";
const ANALYZER_ARTIFACT_API_PREFIXES = [
  "/api/analyzer/artifacts/",
  "/api/v1/analyzer/artifacts/",
];
const ANALYZER_ARTIFACT_RELATIVE_PREFIXES = [
  ...ANALYZER_ARTIFACT_API_PREFIXES,
  "/artifacts/",
];
const ARTIFACT_URL_PARSE_BASE = "https://redocx.invalid";

function normalizeArtifactUrl(value) {
  const raw = String(value || "")
    .trim()
    .replaceAll("\\", "/");
  if (!raw) return "";

  const isAbsoluteHttpUrl = /^https?:\/\//i.test(raw);
  let parsed;
  try {
    parsed = new URL(raw, ARTIFACT_URL_PARSE_BASE);
  } catch {
    return raw;
  }

  const prefixes = isAbsoluteHttpUrl
    ? ANALYZER_ARTIFACT_API_PREFIXES
    : ANALYZER_ARTIFACT_RELATIVE_PREFIXES;
  const matchedPrefix = prefixes.find((prefix) =>
    parsed.pathname.startsWith(prefix),
  );

  if (!matchedPrefix) return raw;

  const storageKey = parsed.pathname
    .slice(matchedPrefix.length)
    .replace(/^\/+/, "");
  if (!storageKey) return raw;

  return `${ANALYZER_ARTIFACT_ROUTE_PREFIX}${storageKey}${parsed.search}${parsed.hash}`;
}

function normalizeArtifactUrls(value) {
  if (Array.isArray(value)) return value.map(normalizeArtifactUrls);
  if (typeof value === "string") return normalizeArtifactUrl(value);
  if (!value || typeof value !== "object") return value;

  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      normalizeArtifactUrls(item),
    ]),
  );
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

  const formValidationError = validateAnalyzerFormData(
    featurePath,
    outboundFormData,
  );
  if (formValidationError) {
    return jsonNoStore({ detail: formValidationError }, 400);
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

  const data = normalizeArtifactUrls(await readBackendPayload(backendRes));
  const response = jsonNoStore(data, backendRes.status);
  return forwardBackendSetCookies(backendRes, response);
}
