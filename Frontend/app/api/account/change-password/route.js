import { NextResponse } from "next/server";
import { auth0 } from "@/lib/auth0";

const CHANGE_PASSWORD_TIMEOUT_MS = 12_000;
const DEFAULT_DATABASE_CONNECTION = "Username-Password-Authentication";
const SUPPORTED_AUTH_LOCALES = new Set(["en", "fr"]);
const MANAGEMENT_TOKEN_SKEW_SECONDS = 60;
const PATCH_USER_LOCALE_BEFORE_PASSWORD_CHANGE =
  process.env.AUTH0_PATCH_USER_LOCALE_BEFORE_PASSWORD_CHANGE === "true";
let cachedManagementToken = "";
let cachedManagementTokenExpiresAt = 0;

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

function firstNonEmptyText(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }

  return "";
}

function normalizeAuth0Domain(value) {
  const raw = firstNonEmptyText(value);

  if (!raw) return "";

  return raw
    .replace(/^https?:\/\//i, "")
    .replace(/\/.*$/, "")
    .replace(/\/+$/, "")
    .trim();
}

function normalizeLocale(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace("_", "-")
    .split("-")[0];

  return SUPPORTED_AUTH_LOCALES.has(normalized) ? normalized : "en";
}

function isDatabasePasswordUser(user) {
  const subject = firstNonEmptyText(user?.sub, user?.id);

  if (subject.startsWith("auth0|")) {
    return true;
  }

  const identities = Array.isArray(user?.identities) ? user.identities : [];
  return identities.some(
    (identity) => String(identity?.provider || "").toLowerCase() === "auth0",
  );
}

function getAuth0PasswordChangeConfig() {
  const domain = normalizeAuth0Domain(
    firstNonEmptyText(process.env.AUTH0_DOMAIN, process.env.AUTH0_ISSUER),
  );
  const clientId = firstNonEmptyText(process.env.AUTH0_CLIENT_ID);
  const connection = firstNonEmptyText(
    process.env.AUTH0_CHANGE_PASSWORD_CONNECTION,
    process.env.AUTH0_DATABASE_CONNECTION,
    DEFAULT_DATABASE_CONNECTION,
  );

  if (!domain || !clientId || !connection) {
    return {
      error: {
        detail: {
          error: "auth0_password_change_not_configured",
          message:
            "Password change is not configured. Set AUTH0_DOMAIN, AUTH0_CLIENT_ID, and AUTH0_DATABASE_CONNECTION or AUTH0_CHANGE_PASSWORD_CONNECTION.",
        },
      },
    };
  }

  return { domain, clientId, connection };
}

function getAuth0ManagementConfig() {
  const domain = normalizeAuth0Domain(
    firstNonEmptyText(process.env.AUTH0_DOMAIN, process.env.AUTH0_ISSUER),
  );
  const clientId = firstNonEmptyText(
    process.env.AUTH0_MANAGEMENT_CLIENT_ID,
    process.env.AUTH0_MGMT_CLIENT_ID,
    process.env.AUTH0_M2M_CLIENT_ID,
  );
  const clientSecret = firstNonEmptyText(
    process.env.AUTH0_MANAGEMENT_CLIENT_SECRET,
    process.env.AUTH0_MGMT_CLIENT_SECRET,
    process.env.AUTH0_M2M_CLIENT_SECRET,
  );

  if (!domain || !clientId || !clientSecret) {
    return null;
  }

  return { domain, clientId, clientSecret };
}

async function getAuth0ManagementToken(config) {
  const now = Date.now() / 1000;

  if (cachedManagementToken && now < cachedManagementTokenExpiresAt - MANAGEMENT_TOKEN_SKEW_SECONDS) {
    return cachedManagementToken;
  }

  const response = await fetch(`https://${config.domain}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      client_id: config.clientId,
      client_secret: config.clientSecret,
      audience: `https://${config.domain}/api/v2/`,
    }),
    cache: "no-store",
  });

  const payload = await response.json().catch(() => null);

  if (!response.ok || !payload?.access_token) {
    throw new Error("Could not obtain Auth0 Management API token.");
  }

  cachedManagementToken = payload.access_token;
  cachedManagementTokenExpiresAt = now + Number(payload.expires_in || 3600);
  return cachedManagementToken;
}

async function updateAuth0UserLocaleIfConfigured(userId, locale) {
  if (!PATCH_USER_LOCALE_BEFORE_PASSWORD_CHANGE) {
    return;
  }

  const config = getAuth0ManagementConfig();

  if (!config || !userId || !locale) {
    return;
  }

  try {
    const token = await getAuth0ManagementToken(config);
    const response = await fetch(
      `https://${config.domain}/api/v2/users/${encodeURIComponent(userId)}`,
      {
        method: "PATCH",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_metadata: {
            locale,
            lang: locale,
          },
        }),
        cache: "no-store",
      },
    );

    if (!response.ok) {
      const payload = await response.text().catch(() => "");
      console.warn(
        "Could not update Auth0 user locale before password change.",
        {
          status: response.status,
          payload,
        },
      );
    }
  } catch (error) {
    console.warn("Could not update Auth0 user locale before password change.", {
      message: error?.message,
    });
  }
}

async function readAuth0Payload(response) {
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("application/json")) {
    return response.json().catch(() => null);
  }

  const text = await response.text().catch(() => "");
  return text ? { message: text } : null;
}

function getAuth0ErrorMessage(payload) {
  return firstNonEmptyText(
    payload?.error_description,
    payload?.message,
    payload?.error,
    "Could not start password change.",
  );
}

function errorStatusForAuth0Response(status) {
  if (status === 429) return 429;
  if (status === 400) return 400;
  if (status === 401 || status === 403) return 503;
  if (status >= 500) return 503;
  return 502;
}

export async function POST(req) {
  let session = null;

  try {
    session = await auth0.getSession();
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

  if (!session?.user) {
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

  let locale = "en";
  try {
    const body = await req.json();
    locale = normalizeLocale(body?.locale);
  } catch {
    locale = "en";
  }

  if (!isDatabasePasswordUser(session.user)) {
    return jsonNoStore(
      {
        detail: {
          error: "password_change_not_supported",
          message:
            "Password changes for social sign-in accounts are managed by the identity provider used to sign in.",
        },
      },
      403,
    );
  }

  const email = firstNonEmptyText(session.user.email);

  if (!email) {
    return jsonNoStore(
      {
        detail: {
          error: "password_change_email_unavailable",
          message:
            "Your account does not have an email address that can receive a password reset link.",
        },
      },
      400,
    );
  }

  const config = getAuth0PasswordChangeConfig();

  if (config.error) {
    return jsonNoStore(config.error, 503);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), CHANGE_PASSWORD_TIMEOUT_MS);

  try {
    await updateAuth0UserLocaleIfConfigured(
      firstNonEmptyText(session.user.sub, session.user.id),
      locale,
    );

    const auth0Res = await fetch(
      `https://${config.domain}/dbconnections/change_password`,
      {
        method: "POST",
        headers: {
          Accept: "application/json, text/plain",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          client_id: config.clientId,
          email,
          connection: config.connection,
        }),
        cache: "no-store",
        signal: controller.signal,
      },
    );

    const payload = await readAuth0Payload(auth0Res);

    if (!auth0Res.ok) {
      return jsonNoStore(
        {
          detail: {
            error:
              auth0Res.status === 429
                ? "password_change_rate_limited"
                : "password_change_failed",
            message: getAuth0ErrorMessage(payload),
          },
        },
        errorStatusForAuth0Response(auth0Res.status),
      );
    }

    return jsonNoStore({
      success: true,
      detail: {
        message:
          "Password reset email sent. Check your inbox to continue.",
      },
    });
  } catch (error) {
    const timedOut = error?.name === "AbortError";

    return jsonNoStore(
      {
        detail: {
          error: timedOut
            ? "password_change_timeout"
            : "password_change_unavailable",
          message: timedOut
            ? "Password change request timed out. Please retry."
            : "Could not reach the password change service.",
        },
      },
      503,
    );
  } finally {
    clearTimeout(timeoutId);
  }
}
