import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

export async function proxyBillingRequest(request, backendPath) {
  const json = (data, status) => NextResponse.json(data, { status, headers: { "Cache-Control": "no-store" } });
  if (request.method === "POST") {
    const origin = request.headers.get("origin");
    if (origin && origin !== new URL(request.url).origin) return json({ detail: { error: "invalid_origin", message: "Invalid request origin." } }, 403);
  }
  let token;
  try {
    if (!(await auth0.getSession())) return json({ detail: { error: "authorization_required" } }, 401);
    const result = await auth0.getAccessToken();
    token = typeof result === "string" ? result : result?.token;
    if (!token) return json({ detail: { error: "authorization_required" } }, 401);
  } catch { return json({ detail: { error: "authorization_required" } }, 401); }
  const base = (process.env.BACKEND_URL || process.env.BACKEND_BASE_URL || "").replace(/\/+$/, "");
  if (!base) return json({ detail: { error: "billing_unavailable" } }, 503);
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  const key = request.headers.get("idempotency-key");
  if (key) headers["Idempotency-Key"] = key;
  let body;
  if (request.method === "POST") {
    try {
      const data = await request.json();
      body = JSON.stringify(data);
      if (body.length > 4096) return json({ detail: { error: "request_too_large" } }, 413);
      headers["Content-Type"] = "application/json";
    } catch { return json({ detail: { error: "invalid_json" } }, 400); }
  }
  try {
    const response = await fetch(`${base}${backendPath}${request.method === "GET" ? new URL(request.url).search : ""}`, {
      method: request.method, headers, body, cache: "no-store", signal: AbortSignal.timeout(45000),
    });
    return json(await response.json(), response.status);
  } catch {
    return json({ detail: { error: "billing_operation_pending", message: "The billing request could not be confirmed. Check the existing operation before paying again." } }, 503);
  }
}
