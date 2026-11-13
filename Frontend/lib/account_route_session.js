import { auth0 } from "@/lib/auth0";

export function getAccountBackendUrl() {
  const base = (process.env.BACKEND_URL || process.env.BACKEND_BASE_URL ||
    process.env.BACKEND_API_URL || process.env.API_BASE_URL || "").trim().replace(/\/+$/, "");
  return base;
}

export async function readAccountAccessToken() {
  try {
    if (!(await auth0.getSession())) return { status: 401, code: "authorization_required" };
    const result = await auth0.getAccessToken();
    const token = typeof result === "string" ? result : result?.token;
    if (token) return { token };
  } catch (error) {
    if (["missing_session", "session_expired"].includes(error?.code)) {
      return { status: 401, code: "authorization_required" };
    }
    if (error?.code === "missing_refresh_token" || error?.cause?.error === "invalid_grant" ||
        error?.cause?.code === "invalid_grant") {
      return { status: 401, code: "session_expired" };
    }
  }
  return { status: 503, code: "account_session_unavailable" };
}

export function accountSessionError(session) {
  return { detail: {
    error: session.code,
    message: session.status === 401 ? "Please sign in to continue." : "Your account session could not be checked. Please retry shortly.",
  } };
}
