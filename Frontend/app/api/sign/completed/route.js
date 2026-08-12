import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const SIGNING_TOKEN_RE = /^[A-Za-z0-9_-]{43,256}$/u;
const ARTIFACTS = new Set(["signed_pdf", "certificate"]);

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

function secureJson(payload, status = 200) {
  const response = NextResponse.json(payload, { status });
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Content-Type-Options", "nosniff");
  return response;
}

async function errorPayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => ({}));
  }
  return {
    detail: {
      message: (await response.text().catch(() => "")) || "Request failed.",
    },
  };
}

export async function GET(req) {
  const token = signingToken(req);
  if (!token) {
    return secureJson(
      {
        detail: {
          error: "completion_link_invalid",
          message: "This completion link is invalid or no longer available.",
        },
      },
      404,
    );
  }

  const baseUrl = backendBaseUrl();
  if (!baseUrl) {
    return secureJson(
      { detail: { message: "The signing service is temporarily unavailable." } },
      503,
    );
  }

  const requestUrl = new URL(req.url);
  const artifact = requestUrl.searchParams.get("artifact") || "";
  if (artifact && !ARTIFACTS.has(artifact)) {
    return secureJson({ detail: { message: "Invalid completed artifact." } }, 400);
  }

  const backendPath = artifact
    ? `/api/v1/analyzer/e-signature/completed/document?artifact=${artifact}`
    : "/api/v1/analyzer/e-signature/completed";

  let backendResponse;
  try {
    backendResponse = await fetch(`${baseUrl}${backendPath}`, {
      method: "GET",
      cache: "no-store",
      headers: {
        Accept: artifact ? "application/pdf" : "application/json",
        "X-ReDOCX-Signing-Token": token,
        ...clientMetadataHeaders(req),
      },
    });
  } catch {
    return secureJson(
      { detail: { message: "The signing service is temporarily unavailable." } },
      503,
    );
  }

  if (!backendResponse.ok || !artifact) {
    return secureJson(
      await errorPayload(backendResponse),
      backendResponse.status,
    );
  }

  return new NextResponse(backendResponse.body, {
    status: 200,
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition":
        backendResponse.headers.get("content-disposition") ||
        `attachment; filename="${artifact}.pdf"`,
      "Cache-Control": "private, no-store, max-age=0",
      Pragma: "no-cache",
      "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": "sandbox",
      "Referrer-Policy": "no-referrer",
    },
  });
}
