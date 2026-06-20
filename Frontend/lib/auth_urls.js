const SUPPORTED_AUTH_LOCALES = new Set(["en", "fr"]);

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
