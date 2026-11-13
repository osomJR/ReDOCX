const SUPPORTED_AUTH_LOCALES = new Set(["en", "fr"]);
const BILLING_RETURN_PARAMETERS = ["checkout", "provider", "reference", "trxref", "seat_job"];

export function getBillingReturnPath(target, origin) {
  if (!target) return "";
  try {
    const url = new URL(target, origin);
    if (url.origin !== origin || !["/billing", "/billing/"].includes(url.pathname)) return "";
    const params = new URLSearchParams();
    for (const key of BILLING_RETURN_PARAMETERS) {
      const value = url.searchParams.get(key);
      if (value && /^[A-Za-z0-9_.=-]{1,128}$/u.test(value)) params.set(key, value);
    }
    return params.size ? `/billing?${params}` : "/billing";
  } catch {
    return "";
  }
}

export function normalizeAuthLocale(language) {
  const normalized = String(language || "")
    .trim()
    .toLowerCase()
    .replace("_", "-")
    .split("-")[0];

  return SUPPORTED_AUTH_LOCALES.has(normalized) ? normalized : "en";
}

export function buildAuthLoginUrl({
  language = "en",
  returnTo = "/",
  screenHint = "",
  prompt = "",
} = {}) {
  const params = new URLSearchParams();

  params.set("returnTo", returnTo || "/");
  params.set("ui_locales", normalizeAuthLocale(language));

  if (screenHint) params.set("screen_hint", screenHint);
  if (prompt) params.set("prompt", prompt);

  return `/auth/login?${params.toString()}`;
}

export function buildAuthSignInUrl(language = "en") {
  return buildAuthLoginUrl({ language, returnTo: "/" });
}

export function buildAuthSignUpUrl(language = "en") {
  return buildAuthLoginUrl({
    language,
    returnTo: "/",
    screenHint: "signup",
    prompt: "login",
  });
}
