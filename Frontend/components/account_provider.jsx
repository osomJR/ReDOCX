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
import { clearAccessTokenCache, getAccountMe } from "@/lib/api_client";

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
});

const ACCOUNT_CACHE_KEY = "redocx:account:v2";
const ACCOUNT_SYNC_KEY = "redocx:account-sync:v1";
const ACCOUNT_REFRESH_EVENT = "redocx:account:refresh";
const ACCOUNT_UPDATED_EVENT = "redocx:account:updated";
const ACCOUNT_RETRY_DELAYS_MS = [0, 750, 2_000, 5_000];
const ACCOUNT_BACKGROUND_REFRESH_MS = 3 * 60_000;

function isAuthError(error) {
  return error?.status === 401 || error?.status === 403;
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

export function AccountProvider({ children }) {
  const [hydrated, setHydrated] = useState(false);
  const [account, setAccount] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [lastSyncedAt, setLastSyncedAt] = useState(null);

  const accountRef = useRef(null);
  const requestSeqRef = useRef(0);
  const abortRef = useRef(null);

  useEffect(() => {
    accountRef.current = account;
  }, [account]);

  const clearAccount = useCallback(() => {
    abortRef.current?.abort?.();
    clearAccessTokenCache();
    clearAccountCache();
    accountRef.current = null;
    setAccount(null);
    setAuthChecked(true);
    setError(null);
    setLoading(false);
    setRefreshing(false);
    setLastSyncedAt(Date.now());
    broadcastAccountState(null);
  }, []);

  const loadAccount = useCallback(
    async ({
      background = false,
      forceRefresh = false,
      allowCurrentAccountFallback = true,
    } = {}) => {
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
          clearAccessTokenCache();
          clearAccountCache();
        } else {
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
          clearAccount();
          return null;
        }

        const safeFallback = allowCurrentAccountFallback ? accountRef.current : null;
        const nextError =
          caught instanceof Error ? caught : new Error("Could not load account.");

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
    [clearAccount],
  );

  useEffect(() => {
    let active = true;
    setHydrated(true);

    // Do not treat sessionStorage as confirmed auth on first paint. It is only a
    // stale fallback after this tab has already confirmed the current session.
    const cached = readAccountCache();
    if (cached) {
      accountRef.current = cached;
    }

    void loadAccount({
      background: false,
      forceRefresh: true,
      allowCurrentAccountFallback: false,
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
    if (!hydrated) return undefined;

    const refreshInBackground = () => {
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
  }, [hydrated, loadAccount]);

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
    }),
    [
      account,
      authChecked,
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
