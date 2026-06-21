import { NextResponse } from "next/server";

const DEFAULT_DATABASE_CONNECTION = "Username-Password-Authentication";
const FORGOT_PASSWORD_TIMEOUT_MS = 12_000;
const SUPPORTED_AUTH_LOCALES = new Set(["en", "fr"]);
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

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
  return firstNonEmptyText(value)
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

function auth0AcceptLanguageHeader(locale) {
  return locale === "fr" ? "fr-FR,fr;q=0.9,en;q=0.5" : "en-US,en;q=0.9";
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

  return { domain, clientId, connection };
}

function genericSuccess(locale) {
  const message =
    locale === "fr"
      ? "Si un compte existe pour cette adresse e-mail, un lien de réinitialisation du mot de passe sera envoyé."
      : "If an account exists for that email, a password reset link will be sent.";

  return jsonNoStore({
    success: true,
    detail: { message },
  });
}

export async function POST(req) {
  let email = "";
  let locale = "en";

  try {
    const body = await req.json();
    email = String(body?.email || "").trim().toLowerCase();
    locale = normalizeLocale(body?.locale);
  } catch {
    return genericSuccess(locale);
  }

  if (!EMAIL_PATTERN.test(email)) {
    return genericSuccess(locale);
  }

  const config = getAuth0PasswordChangeConfig();

  if (!config.domain || !config.clientId || !config.connection) {
    console.error("Forgot password is not configured.", {
      hasDomain: Boolean(config.domain),
      hasClientId: Boolean(config.clientId),
      hasConnection: Boolean(config.connection),
    });
    return genericSuccess(locale);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FORGOT_PASSWORD_TIMEOUT_MS);

  try {
    const response = await fetch(
      `https://${config.domain}/dbconnections/change_password`,
      {
        method: "POST",
        headers: {
          Accept: "application/json, text/plain",
          "Accept-Language": auth0AcceptLanguageHeader(locale),
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

    if (!response.ok) {
      const payload = await response.text().catch(() => "");
      console.error("Auth0 forgot-password request failed.", {
        status: response.status,
        connection: config.connection,
        payload,
      });
    }
  } catch (error) {
    console.error("Auth0 forgot-password request errored.", {
      message: error?.message,
      connection: config.connection,
    });
  } finally {
    clearTimeout(timeoutId);
  }

  return genericSuccess(locale);
}
