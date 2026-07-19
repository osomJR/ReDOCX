// Install as:
// app/api/conversations/[conversationId]/messages/[messageId]/attachments/[attachmentId]/download/route.js

import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_BASE_URL = (
  process.env.BACKEND_URL ||
  process.env.BACKEND_BASE_URL ||
  process.env.BACKEND_API_URL ||
  process.env.API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

const SAFE_RESPONSE_HEADERS = [
  "content-type",
  "content-length",
  "content-disposition",
  "cache-control",
  "pragma",
  "x-content-type-options",
  "x-download-options",
  "content-security-policy",
  "cross-origin-resource-policy",
  "referrer-policy",
];

async function getRequiredAccessToken() {
  try {
    const session = await auth0.getSession();
    if (!session) return "";
    const tokenSet = await auth0.getAccessToken();
    return typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
  } catch {
    return "";
  }
}

function secureJson(detail, status) {
  return NextResponse.json(
    { detail },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

function isPositiveId(value) {
  return /^[1-9][0-9]*$/u.test(String(value || ""));
}

export async function GET(_req, context) {
  const accessToken = await getRequiredAccessToken();
  if (!accessToken) {
    return secureJson(
      {
        error: "authorization_required",
        message: "You must be signed in to download this attachment.",
      },
      401,
    );
  }

  const { conversationId, messageId, attachmentId } = await context.params;
  if (![conversationId, messageId, attachmentId].every(isPositiveId)) {
    return secureJson(
      {
        error: "invalid_attachment_path",
        message: "Attachment download path is invalid.",
      },
      400,
    );
  }

  const backendUrl =
    `${BACKEND_BASE_URL}/api/v1/conversations/${conversationId}` +
    `/messages/${messageId}/attachments/${attachmentId}/download`;

  try {
    const backendRes = await fetch(backendUrl, {
      method: "GET",
      headers: {
        Accept: "application/octet-stream, application/json",
        Authorization: `Bearer ${accessToken}`,
        "X-Request-ID": crypto.randomUUID(),
      },
      cache: "no-store",
      redirect: "manual",
    });

    const responseHeaders = new Headers();
    for (const name of SAFE_RESPONSE_HEADERS) {
      const value = backendRes.headers.get(name);
      if (value) responseHeaders.set(name, value);
    }
    responseHeaders.set("Cache-Control", "private, no-store, max-age=0");
    responseHeaders.set("X-Content-Type-Options", "nosniff");

    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: responseHeaders,
    });
  } catch {
    return secureJson(
      {
        error: "attachment_service_unavailable",
        message:
          "The secure attachment service is temporarily unavailable. Please try again.",
      },
      503,
    );
  }
}
