"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useAccount } from "@/components/account_provider";

const ThemeContext = createContext({
  theme: "system",
  resolvedTheme: "light",
  setTheme: async () => {},
  loading: true,
});

const THEME_STORAGE_KEY = "redocx:appearance:v1";
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

function normalizeTheme(value) {
  return value === "light" || value === "dark" || value === "system"
    ? value
    : "system";
}

function readStoredTheme() {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored ? normalizeTheme(stored) : null;
  } catch {
    return null;
  }
}

function writeStoredTheme(theme) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, normalizeTheme(theme));
  } catch {
    // Local persistence is an enhancement; the server setting remains authoritative.
  }
}

function getSystemTheme() {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

const THEME_GLOBAL_STYLES = `:root,
html.light {
  color-scheme: light;
  --app-bg: #f4f7fb;
  --app-panel: #ffffff;
  --app-surface: #ffffff;
  --app-surface-strong: #f8fafc;
  --app-text: #0f172a;
  --app-text-muted: #475569;
  --app-text-soft: #64748b;
  --app-border: #d8e0ea;
  --app-border-strong: #bcc8d6;
  --app-button-bg: #0f172a;
  --app-button-text: #ffffff;
  --app-accent-bg: #ecfeff;
  --app-accent-border: #67e8f9;
  --app-accent-text: #0e7490;
  --app-selection-bg: #0f172a;
  --app-selection-text: #ffffff;
  --app-hover: #eef3f8;
  --app-focus: #0891b2;
  --app-page-gradient:
    radial-gradient(circle at 15% -10%, rgba(8, 145, 178, 0.08), transparent 32rem),
    radial-gradient(circle at 95% 0%, rgba(59, 130, 246, 0.06), transparent 28rem),
    linear-gradient(180deg, #f8fafc 0%, #f4f7fb 58%, #eef3f8 100%);
}

html.dark {
  color-scheme: dark;
  --app-bg: #090c12;
  --app-panel: #10141c;
  --app-surface: #131923;
  --app-surface-strong: #171e29;
  --app-text: #f8fafc;
  --app-text-muted: #cbd5e1;
  --app-text-soft: #94a3b8;
  --app-border: #293445;
  --app-border-strong: #3a475c;
  --app-button-bg: #f8fafc;
  --app-button-text: #0f172a;
  --app-accent-bg: rgba(34, 211, 238, 0.11);
  --app-accent-border: rgba(34, 211, 238, 0.42);
  --app-accent-text: #a5f3fc;
  --app-selection-bg: #f8fafc;
  --app-selection-text: #0f172a;
  --app-hover: #202938;
  --app-focus: #22d3ee;
  --app-page-gradient:
    radial-gradient(circle at 15% -10%, rgba(34, 211, 238, 0.08), transparent 32rem),
    radial-gradient(circle at 95% 0%, rgba(59, 130, 246, 0.07), transparent 28rem),
    linear-gradient(180deg, #0b0f16 0%, #090c12 58%, #0d1118 100%);
}

@media (prefers-color-scheme: dark) {
  :root:not(.light) {
    color-scheme: dark;
    --app-bg: #090c12;
    --app-panel: #10141c;
    --app-surface: #131923;
    --app-surface-strong: #171e29;
    --app-text: #f8fafc;
    --app-text-muted: #cbd5e1;
    --app-text-soft: #94a3b8;
    --app-border: #293445;
    --app-border-strong: #3a475c;
    --app-button-bg: #f8fafc;
    --app-button-text: #0f172a;
    --app-accent-bg: rgba(34, 211, 238, 0.11);
    --app-accent-border: rgba(34, 211, 238, 0.42);
    --app-accent-text: #a5f3fc;
    --app-selection-bg: #f8fafc;
    --app-selection-text: #0f172a;
    --app-hover: #202938;
    --app-focus: #22d3ee;
    --app-page-gradient:
      radial-gradient(circle at 15% -10%, rgba(34, 211, 238, 0.08), transparent 32rem),
      radial-gradient(circle at 95% 0%, rgba(59, 130, 246, 0.07), transparent 28rem),
      linear-gradient(180deg, #0b0f16 0%, #090c12 58%, #0d1118 100%);
  }
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
  background-color: var(--app-bg) !important;
  background-image: var(--app-page-gradient) !important;
  color: var(--app-text) !important;
}

.app-surface,
.app-surface-strong {
  background-color: var(--app-surface) !important;
  border-color: var(--app-border) !important;
}

.app-surface-strong {
  background-color: var(--app-surface-strong) !important;
  border-color: var(--app-border-strong) !important;
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
  pointer-events: none;
}

[class~="hover:bg-neutral-100"]:hover,
[class~="bg-neutral-100"] {
  background-color: var(--app-hover) !important;
}

input,
textarea,
select {
  background-color: var(--app-panel) !important;
  color: var(--app-text) !important;
  border-color: var(--app-border) !important;
}

input:focus,
textarea:focus,
select:focus {
  border-color: var(--app-focus) !important;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--app-focus) 18%, transparent);
}

input::placeholder,
textarea::placeholder {
  color: var(--app-text-soft) !important;
}

select option {
  background: var(--app-panel);
  color: var(--app-text);
}

html.light [class*="bg-red-"][class*="text-red-"],
html.light [class*="bg-red-"] [class*="text-red-"] {
  color: #b91c1c !important;
}

html.light [class*="bg-emerald-"][class*="text-emerald-"],
html.light [class*="bg-emerald-"] [class*="text-emerald-"] {
  color: #047857 !important;
}

html.light [class*="bg-amber-"][class*="text-amber-"],
html.light [class*="bg-amber-"] [class*="text-amber-"] {
  color: #a16207 !important;
}

html.light [class*="bg-cyan-"][class*="text-cyan-"],
html.light [class*="bg-cyan-"] [class*="text-cyan-"],
html.light [class*="bg-blue-"][class*="text-blue-"],
html.light [class*="bg-blue-"] [class*="text-blue-"] {
  color: #0e7490 !important;
}

::selection {
  background: var(--app-selection-bg);
  color: var(--app-selection-text);
}

* {
  scrollbar-color: var(--app-border-strong) var(--app-bg);
}`;

function applyThemeToDocument(theme) {
  if (typeof document === "undefined") return "light";

  const normalized = normalizeTheme(theme);
  const resolved = normalized === "system" ? getSystemTheme() : normalized;
  const root = document.documentElement;

  root.dataset.theme = resolved;
  root.dataset.themePreference = normalized;
  root.style.colorScheme = resolved;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);

  return resolved;
}

export function ThemeProvider({ children }) {
  const { settings, authChecked, loading: accountLoading } = useAccount();
  const [theme, setThemeState] = useState("system");
  const [resolvedTheme, setResolvedTheme] = useState(() => getSystemTheme());
  const [loading, setLoading] = useState(true);
  const hydratedFromAccountRef = useRef(false);

  useIsomorphicLayoutEffect(() => {
    const initialTheme = readStoredTheme() || "system";
    setThemeState(initialTheme);
    setResolvedTheme(applyThemeToDocument(initialTheme));
  }, []);

  useEffect(() => {
    if (!authChecked || accountLoading) {
      return;
    }

    const nextTheme = normalizeTheme(settings?.appearance);
    setThemeState(nextTheme);
    setResolvedTheme(applyThemeToDocument(nextTheme));
    writeStoredTheme(nextTheme);
    hydratedFromAccountRef.current = true;
    setLoading(false);
  }, [accountLoading, authChecked, settings?.appearance]);

  useEffect(() => {
    if (hydratedFromAccountRef.current || !authChecked || accountLoading) {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      if (hydratedFromAccountRef.current) return;

      const fallbackTheme = readStoredTheme() || "system";
      setThemeState(fallbackTheme);
      setResolvedTheme(applyThemeToDocument(fallbackTheme));
      setLoading(false);
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, [accountLoading, authChecked]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const media = window.matchMedia("(prefers-color-scheme: dark)");

    const handleChange = () => {
      if (theme === "system") {
        setResolvedTheme(applyThemeToDocument("system"));
      }
    };

    media.addEventListener?.("change", handleChange);
    media.addListener?.(handleChange);

    return () => {
      media.removeEventListener?.("change", handleChange);
      media.removeListener?.(handleChange);
    };
  }, [theme]);

  useEffect(() => {
    if (typeof window === "undefined") return undefined;

    const handleStorage = (event) => {
      if (event.key !== THEME_STORAGE_KEY || !event.newValue) return;
      const nextTheme = normalizeTheme(event.newValue);
      setThemeState(nextTheme);
      setResolvedTheme(applyThemeToDocument(nextTheme));
    };

    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const setTheme = useCallback(
    async (nextTheme) => {
      const normalized = normalizeTheme(nextTheme);
      const previousTheme = theme;

      setThemeState(normalized);
      setResolvedTheme(applyThemeToDocument(normalized));
      writeStoredTheme(normalized);

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
        setThemeState(previousTheme);
        setResolvedTheme(applyThemeToDocument(previousTheme));
        writeStoredTheme(previousTheme);
        console.error(error);
      }
    },
    [theme],
  );

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
