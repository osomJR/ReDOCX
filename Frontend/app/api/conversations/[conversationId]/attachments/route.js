// Install as:
// app/api/conversations/[conversationId]/attachments/route.js

import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_TEAM_ATTACHMENT_REQUEST_BYTES = 21 * 1024 * 1024;

const BACKEND_BASE_URL = (
  process.env.BACKEND_URL ||
  process.env.BACKEND_BASE_URL ||
  process.env.BACKEND_API_URL ||
  process.env.API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

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

function authorizationRequired() {
  return NextResponse.json(
    {
      detail: {
        error: "authorization_required",
        message: "You must be signed in to send an attachment.",
      },
    },
    {
      status: 401,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
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

export async function POST(req, context) {
  const requestOrigin = new URL(req.url).origin;
  const origin = req.headers.get("origin");
  const fetchSite = String(req.headers.get("sec-fetch-site") || "").toLowerCase();
  if ((origin && origin !== requestOrigin) || fetchSite === "cross-site") {
    return secureJson(
      {
        error: "cross_site_attachment_request_denied",
        message: "Cross-site attachment uploads are not allowed.",
      },
      403,
    );
  }

  const accessToken = await getRequiredAccessToken();
  if (!accessToken) return authorizationRequired();

  const contentType = req.headers.get("content-type") || "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data;")) {
    return secureJson(
      {
        error: "invalid_attachment_request",
        message: "Attachment uploads must use multipart/form-data.",
      },
      415,
    );
  }

  const { conversationId } = await context.params;
  if (!isPositiveId(conversationId)) {
    return secureJson(
      {
        error: "invalid_conversation_id",
        message: "Conversation identifier is invalid.",
      },
      400,
    );
  }

  const contentLength = req.headers.get("content-length");
  if (contentLength) {
    const parsedLength = Number(contentLength);
    if (
      !Number.isSafeInteger(parsedLength) ||
      parsedLength < 0 ||
      parsedLength > MAX_TEAM_ATTACHMENT_REQUEST_BYTES
    ) {
      return secureJson(
        {
          error: "attachment_request_too_large",
          message: "Attachment request is too large. Maximum file size is 20 MB.",
        },
        413,
      );
    }
  }

  const backendUrl =
    `${BACKEND_BASE_URL}/api/v1/conversations/` +
    `${encodeURIComponent(conversationId)}/attachments`;

  try {
    // Stream the original multipart body unchanged so the boundary and file
    // bytes received by FastAPI exactly match the browser request.
    const headers = new Headers({
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": contentType,
      "X-Request-ID": crypto.randomUUID(),
    });
    if (contentLength) headers.set("Content-Length", contentLength);

    const backendRes = await fetch(backendUrl, {
      method: "POST",
      headers,
      body: req.body,
      cache: "no-store",
      duplex: "half",
      redirect: "manual",
      signal: req.signal,
    });

    const responseHeaders = new Headers();
    const responseContentType = backendRes.headers.get("content-type");
    if (responseContentType) {
      responseHeaders.set("Content-Type", responseContentType);
    }
    responseHeaders.set("Cache-Control", "no-store");
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
