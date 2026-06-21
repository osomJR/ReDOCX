const AUTH0_DOMAIN = normalizeAuth0Domain(
  process.env.AUTH0_DOMAIN || process.env.AUTH0_ISSUER,
);
const AUTH0_MGMT_TOKEN = firstNonEmptyText(process.env.AUTH0_MGMT_TOKEN);
const AUTH0_MANAGEMENT_CLIENT_ID = firstNonEmptyText(
  process.env.AUTH0_MANAGEMENT_CLIENT_ID,
  process.env.AUTH0_MGMT_CLIENT_ID,
  process.env.AUTH0_M2M_CLIENT_ID,
);
const AUTH0_MANAGEMENT_CLIENT_SECRET = firstNonEmptyText(
  process.env.AUTH0_MANAGEMENT_CLIENT_SECRET,
  process.env.AUTH0_MGMT_CLIENT_SECRET,
  process.env.AUTH0_M2M_CLIENT_SECRET,
);

if (
  !AUTH0_DOMAIN ||
  (!AUTH0_MGMT_TOKEN &&
    (!AUTH0_MANAGEMENT_CLIENT_ID || !AUTH0_MANAGEMENT_CLIENT_SECRET))
) {
  throw new Error(
    "Missing Auth0 Management API credentials. Set AUTH0_DOMAIN plus either AUTH0_MGMT_TOKEN, or AUTH0_MANAGEMENT_CLIENT_ID and AUTH0_MANAGEMENT_CLIENT_SECRET.",
  );
}

const language = "fr";

const promptUpdates = {
  "email-verification": {
    "email-verification-result": {
      verifiedTitle: "Adresse e-mail vérifiée",
      verifiedDescription: "Votre adresse e-mail a bien été vérifiée.",
      buttonText: "Retour à ReDOCX",
    },
  },

  "reset-password": {
    "reset-password": {
      title: "Changer votre mot de passe",
      description:
        "Saisissez un nouveau mot de passe ci-dessous pour modifier votre mot de passe.",
      passwordPlaceholder: "Nouveau mot de passe",
      reEnterPasswordPlaceholder: "Confirmez le nouveau mot de passe",
      // Kept for compatibility with tenants that still use the older/lowercase key.
      reEnterpasswordPlaceholder: "Confirmez le nouveau mot de passe",
      buttonText: "Réinitialiser le mot de passe",
    },

    "reset-password-success": {
      eventTitle: "Mot de passe modifié !",
      description: "Votre mot de passe a bien été modifié.",
      buttonText: "Retour à ReDOCX",
    },

    "reset-password-error": {
      title: "Lien invalide ou expiré",
      description:
        "Ce lien de réinitialisation n’est plus valide. Demandez un nouveau lien depuis ReDOCX.",
      buttonText: "Retour à ReDOCX",
    },
  },
};

const resetEmailSubject = String.raw`{% assign redocx_locale = user.user_metadata.locale | default: user.user_metadata.lang | default: request_language | default: 'en' | downcase %}{% if redocx_locale contains 'fr' %}Réinitialisez votre mot de passe ReDOCX{% else %}Reset your ReDOCX password{% endif %}`;

const resetEmailBody = String.raw`{% assign redocx_locale = user.user_metadata.locale | default: user.user_metadata.lang | default: request_language | default: 'en' | downcase %}
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{% if redocx_locale contains 'fr' %}Réinitialisez votre mot de passe ReDOCX{% else %}Reset your ReDOCX password{% endif %}</title>
  </head>
  <body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,Helvetica,sans-serif;color:#111827;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f8fb;margin:0;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:18px;padding:32px;border:1px solid #e5e7eb;">
            <tr>
              <td style="font-size:16px;line-height:1.6;color:#111827;">
                {% if redocx_locale contains 'fr' %}
                  <p style="margin:0 0 16px;">Bonjour,</p>
                  <p style="margin:0 0 24px;">Nous avons reçu une demande de réinitialisation de votre mot de passe ReDOCX.</p>
                  <p style="margin:0 0 28px;">
                    <a href="{{ url }}" style="display:inline-block;background:#0b5ed7;color:#ffffff;text-decoration:none;border-radius:10px;padding:13px 22px;font-weight:700;">Réinitialiser votre mot de passe</a>
                  </p>
                  <p style="margin:0 0 20px;color:#4b5563;">Si vous n’êtes pas à l’origine de cette demande, vous pouvez ignorer cet e-mail.</p>
                  <p style="margin:0;color:#111827;">L’équipe ReDOCX</p>
                {% else %}
                  <p style="margin:0 0 16px;">Hello,</p>
                  <p style="margin:0 0 24px;">We received a request to reset your ReDOCX password.</p>
                  <p style="margin:0 0 28px;">
                    <a href="{{ url }}" style="display:inline-block;background:#0b5ed7;color:#ffffff;text-decoration:none;border-radius:10px;padding:13px 22px;font-weight:700;">Reset your password</a>
                  </p>
                  <p style="margin:0 0 20px;color:#4b5563;">If you did not request this, you can safely ignore this email.</p>
                  <p style="margin:0;color:#111827;">ReDOCX Team</p>
                {% endif %}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>`;

function firstNonEmptyText(...values) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) return value.trim();
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

function deepMerge(target, source) {
  const output = { ...(target || {}) };

  for (const [key, value] of Object.entries(source)) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      typeof output[key] === "object" &&
      output[key] !== null &&
      !Array.isArray(output[key])
    ) {
      output[key] = deepMerge(output[key], value);
    } else {
      output[key] = value;
    }
  }

  return output;
}

let cachedManagementToken = "";
let cachedManagementTokenExpiresAt = 0;

async function getManagementToken() {
  if (AUTH0_MGMT_TOKEN) return AUTH0_MGMT_TOKEN;

  const now = Date.now() / 1000;
  if (cachedManagementToken && now < cachedManagementTokenExpiresAt - 60) {
    return cachedManagementToken;
  }

  const res = await fetch(`https://${AUTH0_DOMAIN}/oauth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      grant_type: "client_credentials",
      client_id: AUTH0_MANAGEMENT_CLIENT_ID,
      client_secret: AUTH0_MANAGEMENT_CLIENT_SECRET,
      audience: `https://${AUTH0_DOMAIN}/api/v2/`,
    }),
  });

  const payload = await res.json().catch(() => null);

  if (!res.ok || !payload?.access_token) {
    throw new Error(
      `Failed to obtain Management API token: ${res.status} ${JSON.stringify(payload)}`,
    );
  }

  cachedManagementToken = payload.access_token;
  cachedManagementTokenExpiresAt = now + Number(payload.expires_in || 3600);
  return cachedManagementToken;
}

async function auth0Fetch(path, { method = "GET", body } = {}) {
  const token = await getManagementToken();
  const res = await fetch(`https://${AUTH0_DOMAIN}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/json",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (res.status === 404) return { status: res.status, data: null };

  const text = await res.text();
  const data = text ? safeJsonParse(text) ?? text : null;

  if (!res.ok) {
    throw new Error(
      `Auth0 ${method} ${path} failed: ${res.status} ${typeof data === "string" ? data : JSON.stringify(data)}`,
    );
  }

  return { status: res.status, data };
}

function safeJsonParse(value) {
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

async function getCurrentCustomText(prompt) {
  const { data } = await auth0Fetch(
    `/api/v2/prompts/${prompt}/custom-text/${language}`,
  );
  return data || {};
}

async function putCustomText(prompt, body) {
  await auth0Fetch(`/api/v2/prompts/${prompt}/custom-text/${language}`, {
    method: "PUT",
    body,
  });
  console.log(`Updated prompt custom text: ${prompt}/${language}`);
}

async function patchResetEmailTemplate() {
  await auth0Fetch("/api/v2/email-templates/reset_email", {
    method: "PATCH",
    body: {
      subject: resetEmailSubject,
      body: resetEmailBody,
      syntax: "liquid",
      enabled: true,
    },
  });

  console.log("Updated email template: reset_email");
}

for (const [prompt, patch] of Object.entries(promptUpdates)) {
  const current = await getCurrentCustomText(prompt);
  await putCustomText(prompt, deepMerge(current, patch));
}

await patchResetEmailTemplate();

console.log("French Auth0 prompts and reset email template updated successfully.");
