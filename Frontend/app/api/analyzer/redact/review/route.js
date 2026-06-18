import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";
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

export async function POST(req) {
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

  const headers = buildBackendHeaders(req, accessToken);

  const backendRes = await fetch(
    `${process.env.BACKEND_URL}/api/v1/analyzer/redact/review`,
    {
      method: "POST",
      headers,
      body: outboundFormData,
    },
  );

  const contentType = backendRes.headers.get("content-type") || "";

  const data = contentType.includes("application/json")
    ? await backendRes.json().catch(() => ({}))
    : { detail: { message: await backendRes.text().catch(() => "") } };

  return jsonWithBackendCookies(data, backendRes);
}
