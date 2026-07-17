// Install as:
// app/api/conversations/[conversationId]/attachments/route.js

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
    { status: 401 },
  );
}

export async function POST(req, context) {
  const accessToken = await getRequiredAccessToken();
  if (!accessToken) return authorizationRequired();

  const contentType = req.headers.get("content-type") || "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data;")) {
    return NextResponse.json(
      {
        detail: {
          error: "invalid_attachment_request",
          message: "Attachment uploads must use multipart/form-data.",
        },
      },
      { status: 415 },
    );
  }

  const { conversationId } = await context.params;
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
    });
    const contentLength = req.headers.get("content-length");
    if (contentLength) headers.set("Content-Length", contentLength);

    const backendRes = await fetch(backendUrl, {
      method: "POST",
      headers,
      body: req.body,
      cache: "no-store",
      duplex: "half",
      signal: req.signal,
    });

    const responseHeaders = new Headers();
    const responseContentType = backendRes.headers.get("content-type");
    if (responseContentType) {
      responseHeaders.set("Content-Type", responseContentType);
    }

    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "attachment_service_unavailable",
          message:
            "The attachment service is temporarily unavailable. Please try again.",
        },
      },
      { status: 503 },
    );
  }
}
