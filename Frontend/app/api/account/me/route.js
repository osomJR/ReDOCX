import { NextResponse } from "next/server";
import { accountSessionError, getAccountBackendUrl, readAccountAccessToken } from "@/lib/account_route_session";

const ACCOUNT_ME_TIMEOUT_MS = 12_000;
const ACCOUNT_DELETE_TIMEOUT_MS = 20_000;

function jsonNoStore(payload, status = 200, headers = {}) {
  const response = NextResponse.json(payload, { status });
  response.headers.set(
    "Cache-Control",
    "no-store, no-cache, must-revalidate, proxy-revalidate",
  );
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Expires", "0");

  for (const [name, value] of Object.entries(headers)) {
    response.headers.set(name, value);
  }

  return response;
}

const TERMINAL_AUTH_ERROR_CODES = new Set([
  "account_deleted",
  "user_deleted",
  "user_not_found",
  "invalid_token",
]);

function authErrorCode(payload) {
  return String(
    payload?.detail?.error || payload?.error || payload?.code || "",
  )
    .trim()
    .toLowerCase();
}

function accountInvalidationHeaders(status, payload) {
  const code = authErrorCode(payload);

  if (![401, 403].includes(Number(status)) || !TERMINAL_AUTH_ERROR_CODES.has(code)) {
    return {};
  }

  return {
    "X-Redocx-Account-Invalidated": "1",
    "X-Redocx-Account-Invalidation-Reason": code,
  };
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


export async function GET() {
  const session = await readAccountAccessToken();
  if (!session.token) return jsonNoStore(accountSessionError(session), session.status);
  const accessToken = session.token;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ACCOUNT_ME_TIMEOUT_MS);

  try {
    const backendRes = await fetch(`${getAccountBackendUrl()}/api/v1/account/me`, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        "Cache-Control": "no-cache",
      },
      cache: "no-store",
      signal: controller.signal,
    });

    const data = await readPayload(backendRes);
    return jsonNoStore(
      data,
      backendRes.status,
      accountInvalidationHeaders(backendRes.status, data),
    );
  } catch (error) {
    const timedOut = error?.name === "AbortError";

    return jsonNoStore(
      {
        detail: {
          error: timedOut ? "account_request_timeout" : "account_request_failed",
          message: timedOut
            ? "Account profile refresh timed out. Please retry."
            : "Could not reach the account service.",
        },
      },
      503,
    );
  } finally {
    clearTimeout(timeoutId);
  }
}

export async function DELETE() {
  const session = await readAccountAccessToken();
  if (!session.token) return jsonNoStore(accountSessionError(session), session.status);
  const accessToken = session.token;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), ACCOUNT_DELETE_TIMEOUT_MS);

  try {
    const backendRes = await fetch(`${getAccountBackendUrl()}/api/v1/account/me`, {
      method: "DELETE",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        "Cache-Control": "no-cache",
      },
      cache: "no-store",
      signal: controller.signal,
    });

    const data = await readPayload(backendRes);
    return jsonNoStore(
      data,
      backendRes.status,
      accountInvalidationHeaders(backendRes.status, data),
    );
  } catch (error) {
    const timedOut = error?.name === "AbortError";

    return jsonNoStore(
      {
        detail: {
          error: timedOut ? "account_delete_timeout" : "account_delete_failed",
          message: timedOut
            ? "Account deletion timed out. Please retry."
            : "Could not reach the account service.",
        },
      },
      503,
    );
  } finally {
    clearTimeout(timeoutId);
  }
}

