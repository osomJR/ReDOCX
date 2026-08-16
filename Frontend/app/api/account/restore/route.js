import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

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

function getBackendUrl() {
  const backendUrl = process.env.BACKEND_URL || process.env.BACKEND_BASE_URL;
  if (!backendUrl) {
    throw new Error("BACKEND_URL is not configured.");
  }
  return backendUrl.replace(/\/+$/, "");
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
  let accessToken = "";

  try {
    const session = await auth0.getSession();
    if (!session) {
      return jsonNoStore(
        {
          detail: {
            error: "authorization_required",
            message: "You must be signed in.",
          },
        },
        401,
      );
    }

    const tokenSet = await auth0.getAccessToken();
    accessToken =
      typeof tokenSet === "string" ? tokenSet : tokenSet?.token || "";
    if (!accessToken) {
      return jsonNoStore(
        {
          detail: {
            error: "authorization_required",
            message: "Could not load a valid access token.",
          },
        },
        401,
      );
    }
  } catch {
    return jsonNoStore(
      {
        detail: {
          error: "authorization_required",
          message: "Could not load session.",
        },
      },
      401,
    );
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(
    () => controller.abort(),
    ACCOUNT_RESTORE_TIMEOUT_MS,
  );

  try {
    const backendRes = await fetch(`${getBackendUrl()}/api/v1/account/restore`, {
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
