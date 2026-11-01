import {
  ACCOUNT_INVALIDATED_EVENT,
  ApiClientError,
  clearAccessTokenCache,
  postAnalyzerFeature,
} from "@/lib/api_client";

async function checkedPayload(response, url) {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 || response.status === 403) {
      clearAccessTokenCache();
      const code = String(payload?.detail?.error || payload?.error?.code || "").toLowerCase();
      if (["account_deleted", "user_deleted", "user_not_found", "invalid_token"].includes(code)
          && typeof window !== "undefined") {
        window.dispatchEvent(new window.CustomEvent(ACCOUNT_INVALIDATED_EVENT, {
          detail: { code, status: response.status, url },
        }));
      }
    }
    throw new ApiClientError("Vault request failed", {
      status: response.status, payload, url,
    });
  }
  if (!payload) throw new Error("BACKEND_RESPONSE_INVALID");
  return payload;
}

// Every selection uses the existing authenticated Next/Vercel proxy, regardless
// of file size. A future Blob/storage handoff belongs in that established flow;
// this helper never discovers or calls a public backend URL from the browser.
export async function uploadVaultFiles(files, { signal } = {}) {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  return postAnalyzerFeature("batch/vault", body, true, { signal });
}

export async function downloadVaultFile(itemId, { signal } = {}) {
  const url = `/api/analyzer/vault/items/${encodeURIComponent(String(itemId))}/download`;
  const response = await fetch(url, {
    credentials: "include", cache: "no-store", signal,
  });
  if (!response.ok) await checkedPayload(response, url);
  return response;
}
