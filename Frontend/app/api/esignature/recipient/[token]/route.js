import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function jsonNoStore(payload, status = 200) {
  const response = NextResponse.json(payload, { status });
  response.headers.set(
    "Cache-Control",
    "no-store, no-cache, must-revalidate, proxy-revalidate",
  );
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Expires", "0");
  return response;
}

function getBackendUrl() {
  const backendUrl = process.env.BACKEND_URL || process.env.BACKEND_BASE_URL;

  if (!backendUrl) {
    throw new Error("BACKEND_URL is not configured.");
  }

  return backendUrl.replace(/\/+$/, "");
}

async function readPayload(response) {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }

  const message = await response.text().catch(() => "");
  return {
    detail: {
      message: message || "Request failed.",
    },
  };
}

function normalizeToken(params) {
  const token = params?.token;
  return Array.isArray(token) ? token.join("/") : String(token || "");
}

export async function GET(_req, context) {
  const params = await context.params;
  const token = normalizeToken(params).trim();

  if (!token) {
    return jsonNoStore(
      {
        detail: {
          error: "missing_signing_token",
          message: "Missing signing token.",
        },
      },
      400,
    );
  }

  let backendRes;

  try {
    backendRes = await fetch(
      `${getBackendUrl()}/api/v1/analyzer/e-signature/recipient/${encodeURIComponent(token)}`,
      {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        cache: "no-store",
      },
    );
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "signing_backend_unreachable",
          message: "Could not reach the signing service.",
        },
      },
      502,
    );
  }

  const data = await readPayload(backendRes);
  return jsonNoStore(data, backendRes.status);
}

export async function POST(req, context) {
  const params = await context.params;
  const token = normalizeToken(params).trim();

  if (!token) {
    return jsonNoStore(
      {
        detail: {
          error: "missing_signing_token",
          message: "Missing signing token.",
        },
      },
      400,
    );
  }

  const body = await req.json().catch(() => ({}));

  let backendRes;

  try {
    backendRes = await fetch(
      `${getBackendUrl()}/api/v1/analyzer/e-signature/recipient/${encodeURIComponent(token)}`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body || {}),
        cache: "no-store",
      },
    );
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "signing_backend_unreachable",
          message: "Could not reach the signing service.",
        },
      },
      502,
    );
  }

  const data = await readPayload(backendRes);
  return jsonNoStore(data, backendRes.status);
}
