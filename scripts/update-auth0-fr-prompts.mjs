const AUTH0_DOMAIN = process.env.AUTH0_DOMAIN;
const AUTH0_MGMT_TOKEN = process.env.AUTH0_MGMT_TOKEN;

if (!AUTH0_DOMAIN || !AUTH0_MGMT_TOKEN) {
  throw new Error("Missing AUTH0_DOMAIN or AUTH0_MGMT_TOKEN");
}

const language = "fr";

const updates = {
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
      reEnterpasswordPlaceholder: "Confirmez le nouveau mot de passe",
      buttonText: "Réinitialiser le mot de passe",
    },

    "reset-password-success": {
      eventTitle: "Mot de passe modifié !",
      description: "Votre mot de passe a bien été modifié.",
      buttonText: "Retour à ReDOCX",
    },
  },
};

function deepMerge(target, source) {
  const output = { ...(target || {}) };

  for (const [key, value] of Object.entries(source)) {
    if (
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      typeof output[key] === "object"
    ) {
      output[key] = deepMerge(output[key], value);
    } else {
      output[key] = value;
    }
  }

  return output;
}

async function getCurrentCustomText(prompt) {
  const res = await fetch(
    `https://${AUTH0_DOMAIN}/api/v2/prompts/${prompt}/custom-text/${language}`,
    {
      headers: {
        Authorization: `Bearer ${AUTH0_MGMT_TOKEN}`,
      },
    },
  );

  if (res.status === 404) return {};

  if (!res.ok) {
    throw new Error(
      `Failed to GET ${prompt}: ${res.status} ${await res.text()}`,
    );
  }

  return await res.json();
}

async function putCustomText(prompt, body) {
  const res = await fetch(
    `https://${AUTH0_DOMAIN}/api/v2/prompts/${prompt}/custom-text/${language}`,
    {
      method: "PUT",
      headers: {
        Authorization: `Bearer ${AUTH0_MGMT_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    },
  );

  if (!res.ok) {
    throw new Error(
      `Failed to PUT ${prompt}: ${res.status} ${await res.text()}`,
    );
  }

  console.log(`Updated ${prompt}/${language}`);
}

for (const [prompt, patch] of Object.entries(updates)) {
  const current = await getCurrentCustomText(prompt);
  const merged = deepMerge(current, patch);
  await putCustomText(prompt, merged);
}

console.log("French Auth0 prompt text updated successfully.");
