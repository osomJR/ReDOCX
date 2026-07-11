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

  if (accessToken) {
    headers.Authorization = `Bearer ${accessToken}`;
  }

  return headers;
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

const ARTIFACT_URL_PREFIXES = [
  "/api/analyzer/artifacts/",
  "api/analyzer/artifacts/",
  "/api/v1/analyzer/artifacts/",
  "api/v1/analyzer/artifacts/",
  "/artifacts/",
  "artifacts/",
];

function normalizeArtifactUrl(value) {
  const raw = String(value || "").trim();
  if (!raw || /^https?:\/\//i.test(raw)) return raw;

  for (const prefix of ARTIFACT_URL_PREFIXES) {
    if (raw.startsWith(prefix)) {
      return `/api/analyzer/artifacts/${raw.slice(prefix.length)}`;
    }
  }

  return raw;
}

function normalizeArtifactUrls(value) {
  if (Array.isArray(value)) return value.map(normalizeArtifactUrls);
  if (!value || typeof value !== "object") return value;

  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      (key === "download_url" || key === "downloadUrl") &&
      typeof item === "string"
        ? normalizeArtifactUrl(item)
        : normalizeArtifactUrls(item),
    ]),
  );
}

export async function POST(req) {
  let incomingFormData;

  try {
    incomingFormData = await req.formData();
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "invalid_form_data",
          message: "The data-masking review request must use multipart form data.",
        },
      },
      { status: 400 },
    );
  }

  const outboundFormData = new FormData();

  for (const [key, value] of incomingFormData.entries()) {
    if (
      typeof value === "object" &&
      value !== null &&
      typeof value.arrayBuffer === "function" &&
      typeof value.name === "string"
    ) {
      const buffer = await value.arrayBuffer();
      const fileBlob = new Blob([buffer], {
        type: value.type || "application/octet-stream",
      });

      outboundFormData.append(key, fileBlob, value.name);
    } else {
      outboundFormData.append(key, value);
    }
  }

  let accessToken = "";

  try {
    const session = await auth0.getSession();

    if (session) {
      const tokenSet = await auth0.getAccessToken();
      accessToken =
        typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    }
  } catch {
    accessToken = "";
  }

  const backendBaseUrl = getBackendBaseUrl();
  if (!backendBaseUrl) {
    return backendUrlNotConfiguredResponse();
  }

  let backendRes;

  try {
    backendRes = await fetch(
      `${backendBaseUrl}/api/v1/analyzer/data-mask/review`,
      {
        method: "POST",
        headers: buildBackendHeaders(req, accessToken),
        body: outboundFormData,
        cache: "no-store",
      },
    );
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "data_mask_review_backend_unreachable",
          message: "Could not reach the backend data-masking review service.",
        },
      },
      { status: 502 },
    );
  }

  const contentType = backendRes.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await backendRes.json().catch(() => ({}))
    : { detail: { message: await backendRes.text().catch(() => "") } };

  return jsonWithBackendCookies(normalizeArtifactUrls(data), backendRes);
}
