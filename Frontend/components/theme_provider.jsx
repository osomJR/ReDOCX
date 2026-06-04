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
import { useAccount } from "@/components/account_provider";

const ThemeContext = createContext({
  theme: "system",
  resolvedTheme: "dark",
  setTheme: async () => {},
  loading: true,
});

function normalizeTheme(value) {
  return value === "light" || value === "dark" || value === "system"
    ? value
    : "system";
}

function getSystemTheme() {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

const THEME_GLOBAL_STYLES = `:root,
html.dark {
  color-scheme: dark;
  --app-bg: #000000;
  --app-panel: #000000;
  --app-surface: #000000;
  --app-surface-strong: #000000;
  --app-text: #ffffff;
  --app-text-muted: rgba(255, 255, 255, 0.78);
  --app-text-soft: rgba(255, 255, 255, 0.52);
  --app-border: rgba(255, 255, 255, 0.16);
  --app-border-strong: rgba(255, 255, 255, 0.32);
  --app-button-bg: #ffffff;
  --app-button-text: #000000;
  --app-accent-bg: rgba(34, 211, 238, 0.13);
  --app-accent-border: rgba(34, 211, 238, 0.38);
  --app-accent-text: #a5f3fc;
  --app-selection-bg: #ffffff;
  --app-selection-text: #000000;
}

html.light {
  color-scheme: light;
  --app-bg: #ffffff;
  --app-panel: #ffffff;
  --app-surface: #ffffff;
  --app-surface-strong: #ffffff;
  --app-text: #000000;
  --app-text-muted: rgba(0, 0, 0, 0.72);
  --app-text-soft: rgba(0, 0, 0, 0.50);
  --app-border: rgba(0, 0, 0, 0.16);
  --app-border-strong: rgba(0, 0, 0, 0.30);
  --app-button-bg: #000000;
  --app-button-text: #ffffff;
  --app-accent-bg: rgba(8, 145, 178, 0.10);
  --app-accent-border: rgba(8, 145, 178, 0.32);
  --app-accent-text: #075985;
  --app-selection-bg: #000000;
  --app-selection-text: #ffffff;
}

html,
body {
  background: var(--app-bg) !important;
  color: var(--app-text) !important;
}

body {
  min-height: 100vh;
}

.app-shell,
.app-page,
[data-app-shell="true"] {
  background: var(--app-bg) !important;
  color: var(--app-text) !important;
}

.app-surface,
.app-surface-strong {
  background: var(--app-surface) !important;
  border-color: var(--app-border) !important;
}

.app-surface-strong {
  background: var(--app-surface-strong) !important;
}

.app-text {
  color: var(--app-text) !important;
}

.app-text-muted {
  color: var(--app-text-muted) !important;
}

.app-text-soft {
  color: var(--app-text-soft) !important;
}

.app-hero-overlay,
.app-card-overlay {
  background: transparent !important;
  pointer-events: none;
}

[class*="bg-[radial-gradient"],
[class*="linear-gradient(to_bottom"] {
  background: transparent !important;
}

input,
textarea,
select {
  background-color: var(--app-panel) !important;
  color: var(--app-text) !important;
  border-color: var(--app-border) !important;
}

input::placeholder,
textarea::placeholder {
  color: var(--app-text-soft) !important;
}

::selection {
  background: var(--app-selection-bg);
  color: var(--app-selection-text);
}

* {
  scrollbar-color: var(--app-border-strong) var(--app-bg);
}`;

function applyThemeToDocument(theme) {
  if (typeof document === "undefined") return "dark";

  const resolved = theme === "system" ? getSystemTheme() : theme;
  const root = document.documentElement;

  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);

  return resolved;
}

export function ThemeProvider({ children }) {
  const { settings, authChecked, loading: accountLoading } = useAccount();
  const [theme, setThemeState] = useState("system");
  const [resolvedTheme, setResolvedTheme] = useState("dark");
  const [loading, setLoading] = useState(true);
  const hydratedFromAccountRef = useRef(false);

  useEffect(() => {
    // AccountProvider already loads /api/account/me. Reuse that payload instead
    // of making ThemeProvider issue a second account request on every page load.
    if (!authChecked || accountLoading) {
      return;
    }

    const nextTheme = normalizeTheme(settings?.appearance);
    setThemeState(nextTheme);
    const nextResolved = applyThemeToDocument(nextTheme);
    setResolvedTheme(nextResolved || "dark");
    hydratedFromAccountRef.current = true;
    setLoading(false);
  }, [accountLoading, authChecked, settings?.appearance]);

  useEffect(() => {
    // Fast fallback for signed-out users or account failures: apply system theme
    // on the first paint cycle without blocking the app on another network call.
    if (hydratedFromAccountRef.current || !authChecked || accountLoading) {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      if (hydratedFromAccountRef.current) return;

      const fallbackTheme = "system";
      setThemeState(fallbackTheme);
      const nextResolved = applyThemeToDocument(fallbackTheme);
      setResolvedTheme(nextResolved || "dark");
      setLoading(false);
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [accountLoading, authChecked]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const media = window.matchMedia("(prefers-color-scheme: dark)");

    const handleChange = () => {
      if (theme === "system") {
        const nextResolved = applyThemeToDocument("system");
        setResolvedTheme(nextResolved || "dark");
      }
    };

    media.addEventListener?.("change", handleChange);
    media.addListener?.(handleChange);

    return () => {
      media.removeEventListener?.("change", handleChange);
      media.removeListener?.(handleChange);
    };
  }, [theme]);

  const setTheme = useCallback(async (nextTheme) => {
    const normalized = normalizeTheme(nextTheme);

    setThemeState(normalized);
    const nextResolved = applyThemeToDocument(normalized);
    setResolvedTheme(nextResolved || "dark");

    try {
      const res = await fetch("/api/account/settings", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
        },
        credentials: "include",
        body: JSON.stringify({
          appearance: normalized,
        }),
      });

      if (!res.ok) {
        throw new Error("Could not save appearance setting");
      }
    } catch (error) {
      console.error(error);
    }
  }, []);

  const value = useMemo(
    () => ({
      theme,
      resolvedTheme,
      setTheme,
      loading,
    }),
    [theme, resolvedTheme, setTheme, loading],
  );

  return (
    <ThemeContext.Provider value={value}>
      <style>{THEME_GLOBAL_STYLES}</style>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
