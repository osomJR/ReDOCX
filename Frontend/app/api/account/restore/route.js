import { NextResponse } from "next/server";
import { accountSessionError, getAccountBackendUrl, readAccountAccessToken } from "@/lib/account_route_session";

const ACCOUNT_RESTORE_TIMEOUT_MS = 20_000;

function jsonNoStore(payload, status = 200) {
  const response = NextResponse.json(payload, { status });
  response.headers.set(
    "Cache-Control",
    "no-store, no-cache, must-revalidate, proxy-revalidate",
  );
  response.headers.set("Pragma", "no-cache");
  response.headers.set("Expires", "0");
  return response;
}


async function readPayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }
  const message = await response.text().catch(() => "");
  return { detail: { message: message || "Request failed." } };
}

export async function POST() {
  const session = await readAccountAccessToken();
  if (!session.token) return jsonNoStore(accountSessionError(session), session.status);
  const accessToken = session.token;

  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    ACCOUNT_RESTORE_TIMEOUT_MS,
  );

  try {
    const backendRes = await fetch(`${getAccountBackendUrl()}/api/v1/account/restore`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${accessToken}`,
        "Cache-Control": "no-cache",
      },
      cache: "no-store",
      signal: controller.signal,
    });

    const data = await readPayload(backendRes);
    return jsonNoStore(data, backendRes.status);
  } catch (error) {
    const timedOut = error?.name === "AbortError";
    return jsonNoStore(
      {
        detail: {
          error: timedOut ? "account_restore_timeout" : "account_restore_failed",
          message: timedOut
            ? "Account restoration timed out. Please retry."
            : "Could not reach the account service.",
        },
      },
      503,
    );
  } finally {
    clearTimeout(timeoutId);
  }
}
