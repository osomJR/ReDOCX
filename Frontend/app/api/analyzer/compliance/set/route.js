import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const BACKEND_BASE_URL =
  process.env.BACKEND_URL ||
  process.env.BACKEND_BASE_URL ||
  process.env.BACKEND_API_URL ||
  process.env.API_BASE_URL ||
  "http://localhost:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function buildBackendHeaders(req, accessToken = "") {
  const headers = new Headers();
  const cookie = req.headers.get("cookie");
  const forwardedFor = req.headers.get("x-forwarded-for");
  const realIp = req.headers.get("x-real-ip");
  const userAgent = req.headers.get("user-agent");

  if (cookie) headers.set("cookie", cookie);
  if (forwardedFor) headers.set("x-forwarded-for", forwardedFor);
  if (realIp) headers.set("x-real-ip", realIp);
  if (userAgent) headers.set("user-agent", userAgent);
  if (accessToken) headers.set("authorization", `Bearer ${accessToken}`);

  return headers;
}

function forwardBackendSetCookies(backendRes, response) {
  const setCookie = backendRes.headers.get("set-cookie");
  if (setCookie) response.headers.append("Set-Cookie", setCookie);
  return response;
}

function jsonWithBackendCookies(data, backendRes) {
  const response = NextResponse.json(data, { status: backendRes.status });
  return forwardBackendSetCookies(backendRes, response);
}

async function getBackendAccessToken(req) {
  try {
    const tokenSet = await auth0.getAccessToken();
    const token =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    if (token) return token;
  } catch {
    // Fall through to a caller-supplied bearer token. FastAPI validates it.
  }

  const incomingAuthorization = req.headers.get("authorization") || "";
  const bearerMatch = incomingAuthorization.match(/^Bearer\s+(.+)$/i);
  return bearerMatch?.[1]?.trim() || "";
}

async function buildOutboundFormData(req) {
  const incomingFormData = await req.formData();
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

  return outboundFormData;
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

export async function POST(req) {
  const outboundFormData = await buildOutboundFormData(req);
  const accessToken = await getBackendAccessToken(req);
  const headers = buildBackendHeaders(req, accessToken);

  let backendRes;
  try {
    backendRes = await fetch(`${BACKEND_BASE_URL}/api/v1/analyzer/compliance/set`, {
      method: "POST",
      headers,
      body: outboundFormData,
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "compliance_set_backend_unreachable",
          message: "Could not reach backend compliance document-set service.",
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
