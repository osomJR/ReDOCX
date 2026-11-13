import { NextResponse } from "next/server";
import { accountSessionError, getAccountBackendUrl, readAccountAccessToken } from "@/lib/account_route_session";

function json(data, status) {
  return NextResponse.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

export async function PATCH(req) {
  const origin = req.headers.get("origin");
  if (origin && origin !== new URL(req.url).origin) {
    return json({ detail: { error: "invalid_origin" } }, 403);
  }
  const session = await readAccountAccessToken();
  if (!session.token) return json(accountSessionError(session), session.status);

  const body = await req.json().catch(() => null);
  if (!["light", "dark", "system"].includes(body?.appearance)) {
    return json({ detail: { error: "invalid_appearance" } }, 422);
  }
  const unavailable = { detail: { error: "account_settings_unavailable", message: "Could not save account settings." } };
  const base = getAccountBackendUrl();
  if (!base) return json(unavailable, 503);

  try {
    const response = await fetch(`${base}/api/v1/account/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${session.token}` },
      body: JSON.stringify({ appearance: body.appearance }),
      cache: "no-store",
      signal: AbortSignal.timeout(12_000),
    });
    const data = await response.json().catch(() => null);
    return data ? json(data, response.status) : json(unavailable, 503);
  } catch {
    return json(unavailable, 503);
  }
}
