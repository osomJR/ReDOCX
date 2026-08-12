import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SIGNING_TOKEN_RE = /^[A-Za-z0-9_-]{43,256}$/u;

function backendBaseUrl() {
  return String(
    process.env.BACKEND_URL ||
      process.env.BACKEND_BASE_URL ||
      process.env.BACKEND_API_URL ||
      process.env.API_BASE_URL ||
      "",
  ).replace(/\/+$/, "");
}

function signingToken(req) {
  const token = String(
    req.headers.get("x-redocx-signing-token") || "",
  ).trim();
  return SIGNING_TOKEN_RE.test(token) ? token : "";
}

function clientMetadataHeaders(req) {
  const headers = {};
  const userAgent = req.headers.get("user-agent");
  if (userAgent) headers["User-Agent"] = userAgent;
  const clientIp =
    req.headers.get("cf-connecting-ip") ||
    req.headers.get("x-real-ip") ||
    req.headers.get("x-vercel-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    "";
  if (clientIp) {
    headers["X-Forwarded-For"] = clientIp;
    headers["X-Real-IP"] = clientIp;
  }
  return headers;
}

function jsonNoStore(payload, status = 200) {
  const response = NextResponse.json(payload, { status });
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  return response;
}

function unavailableResponse() {
  return jsonNoStore(
    {
      detail: {
        error: "signing_backend_unavailable",
        message: "The signing service is temporarily unavailable.",
      },
    },
    503,
  );
}

async function readPayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => ({}));
  }
  const message = await response.text().catch(() => "");
  return { detail: { message: message || "Request failed." } };
}

async function proxyJson(req, method) {
  const token = signingToken(req);
  if (!token) {
    return jsonNoStore(
      {
        detail: {
          error: "signing_link_invalid",
          message: "This signing link is invalid or no longer available.",
        },
      },
      404,
    );
  }

  const baseUrl = backendBaseUrl();
  if (!baseUrl) return unavailableResponse();

  const headers = {
    Accept: "application/json",
    "X-ReDOCX-Signing-Token": token,
    ...clientMetadataHeaders(req),
  };
  const options = { method, headers, cache: "no-store" };
  if (method === "POST") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(await req.json().catch(() => ({})));
  }

  let backendResponse;
  try {
    backendResponse = await fetch(
      `${baseUrl}/api/v1/analyzer/e-signature/recipient`,
      options,
    );
  } catch {
    return unavailableResponse();
  }

  return jsonNoStore(
    await readPayload(backendResponse),
    backendResponse.status,
  );
}

async function proxyDocument(req) {
  const token = signingToken(req);
  if (!token) {
    return jsonNoStore(
      { detail: { error: "signing_link_invalid", message: "Invalid signing link." } },
      404,
    );
  }

  const baseUrl = backendBaseUrl();
  if (!baseUrl) return unavailableResponse();

  let backendResponse;
  try {
    backendResponse = await fetch(
      `${baseUrl}/api/v1/analyzer/e-signature/recipient/document`,
      {
        method: "GET",
        cache: "no-store",
        headers: {
          Accept: "application/pdf",
          "X-ReDOCX-Signing-Token": token,
          ...clientMetadataHeaders(req),
        },
      },
    );
  } catch {
    return unavailableResponse();
  }

  if (!backendResponse.ok) {
    return jsonNoStore(
      await readPayload(backendResponse),
      backendResponse.status,
    );
  }

  return new NextResponse(backendResponse.body, {
    status: backendResponse.status,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": 'inline; filename="document-to-sign.pdf"',
      "Cache-Control": "private, no-store, max-age=0",
      Pragma: "no-cache",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "sandbox",
      "Referrer-Policy": "no-referrer",
    },
  });
}

export async function GET(req) {
  const requestUrl = new URL(req.url);
  return requestUrl.searchParams.get("document") === "1"
    ? proxyDocument(req)
    : proxyJson(req, "GET");
}

export async function POST(req) {
  return proxyJson(req, "POST");
}
