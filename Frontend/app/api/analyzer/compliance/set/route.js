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

async function getAccessToken() {
  try {
    const session = await auth0.getSession();
    if (!session) return "";
    const tokenSet = await auth0.getAccessToken();
    return typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
  } catch {
    return "";
  }
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

export async function POST(req) {
  const outboundFormData = await buildOutboundFormData(req);
  const accessToken = await getAccessToken();
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

  return jsonWithBackendCookies(data, backendRes);
}
