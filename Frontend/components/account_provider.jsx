"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ACCOUNT_INVALIDATED_EVENT,
  clearAccessTokenCache,
  getAccountMe,
} from "@/lib/api_client";

const AccountContext = createContext({
  account: null,
  user: null,
  settings: null,
  entitlement: null,
  hydrated: false,
  authChecked: false,
  isSignedIn: false,
  loading: true,
  refreshing: false,
  error: null,
  lastSyncedAt: null,
  reloadAccount: async () => null,
  clearAccount: () => {},
  beginAccountExit: () => {},
});

const ACCOUNT_CACHE_KEY = "redocx:account:v2";
const ACCOUNT_SYNC_KEY = "redocx:account-sync:v1";
const ACCOUNT_REFRESH_EVENT = "redocx:account:refresh";
const ACCOUNT_UPDATED_EVENT = "redocx:account:updated";
const ACCOUNT_RETRY_DELAYS_MS = [0, 750, 2_000, 5_000];
const ACCOUNT_BACKGROUND_REFRESH_MS = 60_000;
const AUTH0_LOGOUT_PATH = "/auth/logout";
const SESSION_INVALIDATED_REASON_KEY = "redocx:session-invalidated:v1";
const ACCOUNT_EXIT_SESSION_KEY = "redocx:account-exit:v1";
const ACCOUNT_EXIT_COOKIE_NAME = "redocx-account-exit";
const ACCOUNT_EXIT_MARKER_MAX_AGE_MS = 2 * 60 * 1000;
const ACCOUNT_EXIT_COOKIE_MAX_AGE_SECONDS = Math.ceil(
  ACCOUNT_EXIT_MARKER_MAX_AGE_MS / 1000,
);

function encodeAccountExitMarker(reason = "account_exit") {
  return encodeURIComponent(
    JSON.stringify({ reason: String(reason || "account_exit"), at: Date.now() }),
  );
}

function readAccountExitCookieMarker() {
  if (typeof document === "undefined") return null;

  try {
    const cookie = document.cookie
      .split(";")
      .map((item) => item.trim())
      .find((item) => item.startsWith(`${ACCOUNT_EXIT_COOKIE_NAME}=`));

    if (!cookie) return null;

    const raw = decodeURIComponent(cookie.slice(ACCOUNT_EXIT_COOKIE_NAME.length + 1));
    const marker = JSON.parse(raw);
    const markedAt = Number(marker?.at || 0);

    if (!markedAt || Date.now() - markedAt > ACCOUNT_EXIT_MARKER_MAX_AGE_MS) {
      clearAccountExitCookieMarker();
      return null;
    }

    return marker;
  } catch {
    clearAccountExitCookieMarker();
    return null;
  }
}

function writeAccountExitCookieMarker(reason = "account_exit") {
  if (typeof document === "undefined") return;

  try {
    document.cookie = [
      `${ACCOUNT_EXIT_COOKIE_NAME}=${encodeAccountExitMarker(reason)}`,
      "Path=/",
      `Max-Age=${ACCOUNT_EXIT_COOKIE_MAX_AGE_SECONDS}`,
      "SameSite=Lax",
    ].join("; ");
  } catch {
    // The cookie only prevents duplicate visible account refreshes during exit.
  }
}

function clearAccountExitCookieMarker() {
  if (typeof document === "undefined") return;

  try {
    document.cookie = `${ACCOUNT_EXIT_COOKIE_NAME}=; Path=/; Max-Age=0; SameSite=Lax`;
  } catch {
    // Ignore cookie cleanup failures.
  }
}

function readAccountExitMarker() {
  if (typeof window === "undefined") return null;

  try {
    const raw = window.sessionStorage.getItem(ACCOUNT_EXIT_SESSION_KEY);
    if (!raw) return readAccountExitCookieMarker();

    const marker = JSON.parse(raw);
    const markedAt = Number(marker?.at || 0);

    if (!markedAt || Date.now() - markedAt > ACCOUNT_EXIT_MARKER_MAX_AGE_MS) {
      window.sessionStorage.removeItem(ACCOUNT_EXIT_SESSION_KEY);
      clearAccountExitCookieMarker();
      return null;
    }

    return marker;
  } catch {
    window.sessionStorage.removeItem(ACCOUNT_EXIT_SESSION_KEY);
    return null;
  }
}

function writeAccountExitMarker(reason = "account_exit") {
  if (typeof window === "undefined") return;

  try {
    window.sessionStorage.setItem(
      ACCOUNT_EXIT_SESSION_KEY,
      JSON.stringify({ reason: String(reason || "account_exit"), at: Date.now() }),
    );
    writeAccountExitCookieMarker(reason);
  } catch {
    // The marker only prevents duplicate visible account refreshes during exit.
  }
}

function consumeAccountExitMarker() {
  const marker = readAccountExitMarker();

  if (typeof window !== "undefined") {
    try {
      window.sessionStorage.removeItem(ACCOUNT_EXIT_SESSION_KEY);
    } catch {
      // Ignore storage failures.
    }
  }

  clearAccountExitCookieMarker();
  return marker;
}

const TERMINAL_AUTH_ERROR_CODES = new Set([
  "account_deleted",
  "user_deleted",
  "user_not_found",
  "invalid_token",
]);

function getAccountErrorCode(error) {
  return String(
    error?.code ||
      error?.payload?.detail?.error ||
      error?.payload?.error ||
      error?.payload?.code ||
      "",
  )
    .trim()
    .toLowerCase();
}

function isAuthError(error) {
  return error?.status === 401 || error?.status === 403;
}

function isTerminalAuthError(error) {
  return isAuthError(error) && TERMINAL_AUTH_ERROR_CODES.has(getAccountErrorCode(error));
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function isRetryableAccountError(error) {
  if (isAbortError(error) || isAuthError(error)) return false;

  const status = Number(error?.status || 0);
  return !status || status === 408 || status === 429 || status >= 500;
}

function sleep(ms) {
  if (!ms) return Promise.resolve();
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function normalizeAccount(account) {
  if (!account || typeof account !== "object") return null;
  if (!account.user) return null;
  return account;
}

function readAccountCache() {
  if (typeof window === "undefined") return null;

  try {
    const cached = JSON.parse(
      window.sessionStorage.getItem(ACCOUNT_CACHE_KEY) || "null",
    );

    return normalizeAccount(cached?.account);
  } catch {
    return null;
  }
}

function writeAccountCache(account) {
  if (typeof window === "undefined") return;

  try {
    const normalized = normalizeAccount(account);

    if (!normalized) {
      window.sessionStorage.removeItem(ACCOUNT_CACHE_KEY);
      return;
    }

    window.sessionStorage.setItem(
      ACCOUNT_CACHE_KEY,
      JSON.stringify({ account: normalized, cachedAt: Date.now() }),
    );
  } catch {
    // Account cache is a non-authoritative speed layer only.
  }
}

export function clearAccountCache() {
  if (typeof window === "undefined") return;

  try {
    window.sessionStorage.removeItem(ACCOUNT_CACHE_KEY);
    // Remove the legacy static cache so older builds cannot rehydrate a stale user.
    window.sessionStorage.removeItem("redocx:account:v1");
  } catch {
    // Ignore storage failures.
  }
}

function broadcastAccountState(account) {
  if (typeof window === "undefined") return;

  const payload = {
    at: Date.now(),
    userId: account?.user?.id || null,
    authenticated: Boolean(account?.user),
  };

  window.dispatchEvent(
    new CustomEvent(ACCOUNT_UPDATED_EVENT, {
      detail: payload,
    }),
  );

  try {
    window.localStorage.setItem(ACCOUNT_SYNC_KEY, JSON.stringify(payload));
  } catch {
    // Cross-tab sync is best-effort only.
  }
}

function readAccountSyncPayload(value) {
  try {
    const payload = JSON.parse(value || "null");
    if (!payload || typeof payload !== "object") return null;
    return payload;
  } catch {
    return null;
  }
}

async function fetchAccountWithRetry({ signal, forceRefresh = false } = {}) {
  let lastError = null;

  for (let attempt = 0; attempt < ACCOUNT_RETRY_DELAYS_MS.length; attempt += 1) {
    if (signal?.aborted) {
      throw new DOMException("Account request was aborted.", "AbortError");
    }

    await sleep(ACCOUNT_RETRY_DELAYS_MS[attempt]);

    try {
      return await getAccountMe({
        signal,
        forceRefresh: forceRefresh || attempt > 0,
      });
    } catch (error) {
      lastError = error;

      if (!isRetryableAccountError(error)) {
        throw error;
      }
    }
  }

  throw lastError || new Error("Could not load account.");
}

export function AccountProvider({ children, initialAccountExit = false }) {
  const initialAccountExitMarkerRef = useRef(
    initialAccountExit ? { reason: "account_exit", at: Date.now() } : readAccountExitMarker(),
  );
  const hasInitialAccountExitMarker = Boolean(initialAccountExitMarkerRef.current);

  const [hydrated, setHydrated] = useState(hasInitialAccountExitMarker);
  const [account, setAccount] = useState(null);
  const [authChecked, setAuthChecked] = useState(hasInitialAccountExitMarker);
  const [loading, setLoading] = useState(!hasInitialAccountExitMarker);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [lastSyncedAt, setLastSyncedAt] = useState(null);

  const accountRef = useRef(null);
  const requestSeqRef = useRef(0);
  const abortRef = useRef(null);
  const logoutTriggeredRef = useRef(false);
  const accountRefreshSuppressedRef = useRef(hasInitialAccountExitMarker);
  const accountClearCommittedRef = useRef(hasInitialAccountExitMarker);

  useEffect(() => {
    accountRef.current = account;
  }, [account]);

  const beginAccountExit = useCallback((reason = "account_exit") => {
    writeAccountExitMarker(reason);
    accountRefreshSuppressedRef.current = true;
    requestSeqRef.current += 1;
    abortRef.current?.abort?.();
    abortRef.current = null;
    clearAccessTokenCache();
    clearAccountCache();
  }, []);

  const redirectToLogout = useCallback((reason = "account_invalidated") => {
    if (typeof window === "undefined" || logoutTriggeredRef.current) {
      return;
    }

    logoutTriggeredRef.current = true;
    beginAccountExit(reason);

    try {
      window.sessionStorage.setItem(SESSION_INVALIDATED_REASON_KEY, String(reason));
    } catch {
      // The reason is informational only.
    }

    window.location.replace(AUTH0_LOGOUT_PATH);
  }, [beginAccountExit]);

  const clearAccount = useCallback(
    ({ broadcast = true, exitReason = "" } = {}) => {
      if (exitReason) {
        writeAccountExitMarker(exitReason);
      }

      const alreadyCleared =
        accountClearCommittedRef.current &&
        accountRefreshSuppressedRef.current &&
        accountRef.current === null;

      // Invalidate any in-flight account refresh and suppress same-page refresh
      // triggers so a late focus/pageshow/realtime event cannot briefly restore
      // the signed-in profile while logout or account deletion is already in progress.
      accountRefreshSuppressedRef.current = true;
      requestSeqRef.current += 1;
      abortRef.current?.abort?.();
      abortRef.current = null;
      clearAccessTokenCache();
      clearAccountCache();

      if (alreadyCleared) {
        return;
      }

      accountClearCommittedRef.current = true;
      accountRef.current = null;
      setAccount(null);
      setAuthChecked(true);
      setError(null);
      setLoading(false);
      setRefreshing(false);
      setLastSyncedAt(Date.now());

      if (broadcast) {
        broadcastAccountState(null);
      }
    },
    [],
  );

  const loadAccount = useCallback(
    async ({
      background = false,
      forceRefresh = false,
      allowCurrentAccountFallback = true,
    } = {}) => {
      if (accountRefreshSuppressedRef.current) {
        if (background) {
          setRefreshing(false);
        } else {
          setAuthChecked(true);
          setLoading(false);
        }

        return accountRef.current;
      }

      const requestSeq = requestSeqRef.current + 1;
      requestSeqRef.current = requestSeq;

      abortRef.current?.abort?.();
      const controller = new AbortController();
      abortRef.current = controller;

      if (background) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      setError(null);

      try {
        const nextAccount = normalizeAccount(
          await fetchAccountWithRetry({
            signal: controller.signal,
            forceRefresh,
          }),
        );

        if (requestSeqRef.current !== requestSeq) {
          return nextAccount;
        }

        if (!nextAccount) {
          accountRefreshSuppressedRef.current = true;
          accountClearCommittedRef.current = true;
          clearAccessTokenCache();
          clearAccountCache();
        } else {
          accountRefreshSuppressedRef.current = false;
          accountClearCommittedRef.current = false;
          writeAccountCache(nextAccount);
        }

        accountRef.current = nextAccount;
        setAccount(nextAccount);
        setAuthChecked(true);
        setLastSyncedAt(Date.now());
        broadcastAccountState(nextAccount);
        return nextAccount;
      } catch (caught) {
        if (requestSeqRef.current !== requestSeq || isAbortError(caught)) {
          return accountRef.current;
        }

        if (isAuthError(caught)) {
          const shouldClearAuth0Session =
            isTerminalAuthError(caught) || Boolean(accountRef.current);
          const reason = getAccountErrorCode(caught) || "authorization_required";

          clearAccount({ exitReason: shouldClearAuth0Session ? reason : "" });

          if (shouldClearAuth0Session) {
            redirectToLogout(reason);
          }

          return null;
        }

        const safeFallback = allowCurrentAccountFallback ? accountRef.current : null;
        const nextError =
          caught instanceof Error ? caught : new Error("Could not load account.");

        if (safeFallback) {
          accountRefreshSuppressedRef.current = false;
          accountClearCommittedRef.current = false;
        }

        setError(nextError);
        setAuthChecked(true);
        setAccount(safeFallback);
        return safeFallback;
      } finally {
        if (requestSeqRef.current === requestSeq) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [clearAccount, redirectToLogout],
  );

  useEffect(() => {
    let active = true;
    setHydrated(true);

    if (initialAccountExitMarkerRef.current) {
      consumeAccountExitMarker();
      clearAccessTokenCache();
      clearAccountCache();
      accountRef.current = null;
      setAccount(null);
      setAuthChecked(true);
      setError(null);
      setLoading(false);
      setRefreshing(false);

      return () => {
        active = false;
        abortRef.current?.abort?.();
      };
    }

    // Hydrate from the non-authoritative session cache immediately after mount
    // so returning authenticated users do not see a temporary signed-out/loading
    // state while the authoritative /api/account/me refresh is in flight.
    const cached = readAccountCache();
    if (cached) {
      accountRef.current = cached;
      setAccount(cached);
      setAuthChecked(true);
      setError(null);
      setLoading(false);
      setLastSyncedAt(Date.now());
    }

    void loadAccount({
      background: Boolean(cached),
      forceRefresh: true,
      allowCurrentAccountFallback: Boolean(cached),
    }).finally(() => {
      if (!active) return;
      setHydrated(true);
    });

    return () => {
      active = false;
      abortRef.current?.abort?.();
    };
  }, [loadAccount]);

  useEffect(() => {
    const handleAccountInvalidated = (event) => {
      const reason =
        event?.detail?.code || event?.detail?.reason || "account_invalidated";
      clearAccount({ exitReason: reason });
      redirectToLogout(reason);
    };

    window.addEventListener(ACCOUNT_INVALIDATED_EVENT, handleAccountInvalidated);

    return () => {
      window.removeEventListener(
        ACCOUNT_INVALIDATED_EVENT,
        handleAccountInvalidated,
      );
    };
  }, [clearAccount, redirectToLogout]);

  useEffect(() => {
    if (!hydrated) return undefined;

    const refreshInBackground = () => {
      if (accountRefreshSuppressedRef.current) {
        return;
      }

      void loadAccount({
        background: true,
        forceRefresh: true,
        allowCurrentAccountFallback: true,
      });
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        refreshInBackground();
      }
    };

    const handleAccountRefresh = () => refreshInBackground();

    const handleStorage = (event) => {
      if (event.key !== ACCOUNT_SYNC_KEY || !event.newValue) return;

      const payload = readAccountSyncPayload(event.newValue);
      if (payload?.authenticated === false) {
        clearAccount({ broadcast: false });
        return;
      }

      if (payload?.authenticated === true) {
        accountRefreshSuppressedRef.current = false;
      }

      refreshInBackground();
    };

    window.addEventListener("focus", refreshInBackground);
    window.addEventListener("online", refreshInBackground);
    window.addEventListener("pageshow", refreshInBackground);
    window.addEventListener(ACCOUNT_REFRESH_EVENT, handleAccountRefresh);
    window.addEventListener("storage", handleStorage);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    const intervalId = window.setInterval(
      refreshInBackground,
      ACCOUNT_BACKGROUND_REFRESH_MS,
    );

    return () => {
      window.removeEventListener("focus", refreshInBackground);
      window.removeEventListener("online", refreshInBackground);
      window.removeEventListener("pageshow", refreshInBackground);
      window.removeEventListener(ACCOUNT_REFRESH_EVENT, handleAccountRefresh);
      window.removeEventListener("storage", handleStorage);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.clearInterval(intervalId);
    };
  }, [clearAccount, hydrated, loadAccount]);

  const value = useMemo(
    () => ({
      account,
      user: account?.user || null,
      settings: account?.settings || null,
      entitlement: account?.entitlement || null,
      hydrated,
      authChecked,
      isSignedIn: Boolean(hydrated && authChecked && account?.user),
      loading,
      refreshing,
      error,
      lastSyncedAt,
      reloadAccount: loadAccount,
      clearAccount,
      beginAccountExit,
    }),
    [
      account,
      authChecked,
      beginAccountExit,
      clearAccount,
      error,
      hydrated,
      lastSyncedAt,
      loadAccount,
      loading,
      refreshing,
    ],
  );

  return (
    <AccountContext.Provider value={value}>{children}</AccountContext.Provider>
  );
}

export function useAccount() {
  return useContext(AccountContext);
}
