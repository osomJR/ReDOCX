"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { getAccountMe } from "@/lib/api_client";

const AccountContext = createContext({
  account: null,
  user: null,
  settings: null,
  entitlement: null,
  hydrated: false,
  authChecked: false,
  isSignedIn: false,
  loading: true,
  error: null,
  reloadAccount: async () => null,
});

const ACCOUNT_CACHE_KEY = "redocx:account:v1";
const ACCOUNT_CACHE_TTL_MS = 60_000;

function readAccountCache() {
  if (typeof window === "undefined") return null;

  try {
    const cached = JSON.parse(
      window.sessionStorage.getItem(ACCOUNT_CACHE_KEY) || "null",
    );

    if (
      !cached ||
      Date.now() - Number(cached.cachedAt || 0) > ACCOUNT_CACHE_TTL_MS
    ) {
      return null;
    }

    return cached.account || null;
  } catch {
    return null;
  }
}

function writeAccountCache(account) {
  if (typeof window === "undefined") return;

  try {
    if (!account) {
      window.sessionStorage.removeItem(ACCOUNT_CACHE_KEY);
      return;
    }

    window.sessionStorage.setItem(
      ACCOUNT_CACHE_KEY,
      JSON.stringify({ account, cachedAt: Date.now() }),
    );
  } catch {
    // Account cache is a non-authoritative speed layer.
  }
}

export function AccountProvider({ children }) {
  // Hydration-safe initial state: the server and the browser's first render must
  // produce the same account/auth markup. Browser-only cache reads happen after
  // mount inside useEffect/reloadAccount.
  const [hydrated, setHydrated] = useState(false);
  const [account, setAccount] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const loadAccount = useCallback(async ({ background = false } = {}) => {
    const cached = readAccountCache();

    if (cached) {
      setAccount(cached);
      setAuthChecked(true);
      setLoading(false);
    } else if (!background) {
      setLoading(true);
    }

    setError(null);

    try {
      const data = await getAccountMe();
      setAccount(data);
      writeAccountCache(data);
      setAuthChecked(true);
      return data;
    } catch (caught) {
      const status = caught?.status;

      if (status === 401 || status === 403) {
        setAccount(null);
        writeAccountCache(null);
        setAuthChecked(true);
        return null;
      }

      if (!cached) {
        setAccount(null);
        writeAccountCache(null);
        setError(
          caught instanceof Error
            ? caught
            : new Error("Could not load account."),
        );
      }

      setAuthChecked(true);
      return cached || null;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;

    async function initializeAccount() {
      setHydrated(true);

      const cached = readAccountCache();
      if (cached && active) {
        setAccount(cached);
        setAuthChecked(true);
        setLoading(false);
      }

      try {
        const data = await getAccountMe();
        if (!active) return;

        setAccount(data);
        writeAccountCache(data);
        setAuthChecked(true);
        setError(null);
      } catch (caught) {
        if (!active) return;

        const status = caught?.status;

        if (status === 401 || status === 403) {
          setAccount(null);
          writeAccountCache(null);
        } else if (!cached) {
          setAccount(null);
          writeAccountCache(null);
          setError(
            caught instanceof Error
              ? caught
              : new Error("Could not load account."),
          );
        }

        setAuthChecked(true);
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void initializeAccount();

    return () => {
      active = false;
    };
  }, []);

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
      error,
      reloadAccount: loadAccount,
    }),
    [account, authChecked, error, hydrated, loadAccount, loading],
  );

  return (
    <AccountContext.Provider value={value}>{children}</AccountContext.Provider>
  );
}

export function useAccount() {
  return useContext(AccountContext);
}
