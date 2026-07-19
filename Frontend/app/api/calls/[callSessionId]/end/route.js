import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

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

export async function POST(_req, context) {
  const accessToken = await getAccessToken();
  if (!accessToken) {
    return NextResponse.json(
      {
        detail: {
          error: "authorization_required",
          message: "You must be signed in.",
        },
      },
      { status: 401 },
    );
  }

  const { callSessionId } = await context.params;
  let backendRes;
  try {
    backendRes = await fetch(
      `${process.env.BACKEND_URL}/api/v1/calls/${encodeURIComponent(callSessionId)}/end`,
      {
        method: "POST",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${accessToken}`,
        },
        cache: "no-store",
      },
    );
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "call_service_unavailable",
          message: "The call service is temporarily unavailable.",
        },
      },
      { status: 503 },
    );
  }

  const contentType = backendRes.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await backendRes.json()
    : { detail: { message: await backendRes.text() } };

  return NextResponse.json(data, {
    status: backendRes.status,
    headers: { "Cache-Control": "private, no-store, max-age=0" },
  });
}
