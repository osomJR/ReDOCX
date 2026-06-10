"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ChevronDown,
  LogOut,
  Monitor,
  Moon,
  Settings,
  Sun,
} from "lucide-react";
import { useTheme } from "@/components/theme_provider";
import { clearAccountCache, useAccount } from "@/components/account_provider";

function formatPlanLabel(plan) {
  if (!plan) return "Free";

  return String(plan)
    .split("_")
    .join(" ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatRoleLabel(role) {
  if (!role) return "";

  return String(role)
    .split("_")
    .join(" ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}


export default function ProfileMenu({
  user,
  settingsLabel = "Settings",
  logoutLabel = "Logout",
  logoutConfirmTitle = "Are you sure you want to Logout?",
  logoutConfirmYesLabel = "Yes",
  logoutReturnDashboardLabel = "Return back to Dashboard",
  appearanceLabel = "Appearance",
  lightLabel = "Light",
  darkLabel = "Dark",
  systemLabel = "System Default",
  backLabel = "back",
  menuPlacement = "bottom",
  menuAlign = "right",
  fullWidth = false,
}) {
  const [open, setOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const containerRef = useRef(null);
  const router = useRouter();
  const { theme, setTheme, loading } = useTheme();
  const { entitlement } = useAccount();

  useEffect(() => {
    function handleClickOutside(event) {
      if (!containerRef.current?.contains(event.target)) {
        setOpen(false);
        setShowSettings(false);
        setShowLogoutConfirm(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const displayName = user?.name || user?.nickname || user?.email || "Account";
  const displayEmail = user?.email || "";
  const initial = displayName.trim().charAt(0).toUpperCase() || "A";

  const planLabel = formatPlanLabel(entitlement?.plan);
  const organizationName = entitlement?.organization_name || "";
  const organizationRole = formatRoleLabel(entitlement?.organization_role);
  const planDescription = organizationName
    ? `${planLabel} · ${organizationName}`
    : `${planLabel} plan`;
  const showRole = Boolean(organizationRole && organizationName);

  const menuPlacementClass =
    menuPlacement === "top" ? "bottom-full mb-3" : "top-full mt-3";

  const menuAlignClass = menuAlign === "left" ? "left-0" : "right-0";

  const hoverItemClass =
    "hover:bg-neutral-100 hover:text-[var(--app-text)] hover:shadow-sm dark:hover:bg-[#2d2d33]";

  return (
    <div className={`relative ${fullWidth ? "w-full" : ""}`} ref={containerRef}>
      <button
        type="button"
        onClick={() => {
          const nextOpen = !open;
          setOpen(nextOpen);
          if (!nextOpen) {
            setShowSettings(false);
            setShowLogoutConfirm(false);
          }
        }}
        className={`flex items-center gap-3 rounded-2xl border app-surface px-3 py-2 text-sm app-text transition ${hoverItemClass} ${
          fullWidth ? "w-full justify-between" : ""
        }`}
      >
        <span className="flex min-w-0 items-center gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--app-button-bg)] text-sm font-semibold text-[var(--app-button-text)]">
            {initial}
          </span>

          <span className="min-w-0 text-left">
            <span className="block truncate font-medium app-text">
              {displayName}
            </span>
            {displayEmail ? (
              <span className="block truncate text-xs app-text-muted">
                {displayEmail}
              </span>
            ) : null}
            <span className="mt-1 block max-w-[10rem] truncate rounded-full border border-[var(--app-border)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] app-text-soft">
              {planDescription}
            </span>
          </span>
        </span>

        <ChevronDown className="h-4 w-4 shrink-0 app-text-muted" />
      </button>

      {open ? (
        <div
          className={`absolute ${menuAlignClass} ${menuPlacementClass} z-[80] w-72 overflow-hidden rounded-3xl border app-surface-strong p-2 shadow-2xl backdrop-blur-xl`}
        >
          {showLogoutConfirm ? (
            <div className="space-y-3 p-1">
              <div className="rounded-2xl border app-surface p-4 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-[var(--app-button-bg)] text-[var(--app-button-text)]">
                  <LogOut className="h-5 w-5" />
                </div>
                <p className="mt-3 text-sm font-semibold app-text">
                  {logoutConfirmTitle}
                </p>
              </div>

              <div className="grid gap-2">
                <a
                  href="/auth/logout"
                  onClick={() => clearAccountCache()}
                  className="rounded-2xl bg-[var(--app-button-bg)] px-4 py-3 text-center text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] hover:shadow-xl"
                >
                  {logoutConfirmYesLabel}
                </a>

                <button
                  type="button"
                  onClick={() => {
                    setShowLogoutConfirm(false);
                    setShowSettings(false);
                    setOpen(false);
                    router.push("/");
                  }}
                  className={`rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text transition ${hoverItemClass}`}
                >
                  {logoutReturnDashboardLabel}
                </button>
              </div>
            </div>
          ) : !showSettings ? (
            <div className="space-y-1">
              <div className={`flex items-center gap-3 rounded-2xl px-3 py-3 transition ${hoverItemClass}`}>
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--app-button-bg)] text-sm font-semibold text-[var(--app-button-text)]">
                  {initial}
                </div>

                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold app-text">
                    {displayName}
                  </div>
                  {displayEmail ? (
                    <div className="truncate text-xs app-text-muted">
                      {displayEmail}
                    </div>
                  ) : null}
                  <div className="mt-2 inline-flex max-w-full items-center rounded-full border border-[var(--app-border)] px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.08em] app-text-soft">
                    <span className="truncate">{planDescription}</span>
                  </div>
                  {showRole ? (
                    <div className="mt-1 text-[11px] app-text-soft">
                      {organizationRole}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="my-2 h-px bg-white/10" />

              <button
                type="button"
                onClick={() => {
                  setShowLogoutConfirm(false);
                  setShowSettings(true);
                }}
                className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm app-text transition ${hoverItemClass}`}
              >
                <Settings className="h-4 w-4 app-text-muted" />
                <span>{settingsLabel}</span>
              </button>

              <button
                type="button"
                onClick={() => {
                  setShowSettings(false);
                  setShowLogoutConfirm(true);
                }}
                className={`flex w-full items-center gap-3 rounded-2xl px-4 py-3 text-left text-sm app-text transition ${hoverItemClass}`}
              >
                <LogOut className="h-4 w-4 app-text-muted" />
                <span>{logoutLabel}</span>
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setShowSettings(false)}
                className={`rounded-2xl px-3 py-2 text-sm app-text-muted transition ${hoverItemClass}`}
              >
                ← {backLabel}
              </button>

              <div className="rounded-2xl border app-surface p-4">
                <div className="mb-3 text-sm font-semibold app-text">
                  {appearanceLabel}
                </div>

                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={() => setTheme("light")}
                    disabled={loading}
                    className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm transition ${
                      theme === "light"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : `app-surface app-text ${hoverItemClass}`
                    }`}
                  >
                    <Sun className="h-4 w-4" />
                    {lightLabel}
                  </button>

                  <button
                    type="button"
                    onClick={() => setTheme("dark")}
                    disabled={loading}
                    className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm transition ${
                      theme === "dark"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : `app-surface app-text ${hoverItemClass}`
                    }`}
                  >
                    <Moon className="h-4 w-4" />
                    {darkLabel}
                  </button>

                  <button
                    type="button"
                    onClick={() => setTheme("system")}
                    disabled={loading}
                    className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm transition ${
                      theme === "system"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : `app-surface app-text ${hoverItemClass}`
                    }`}
                  >
                    <Monitor className="h-4 w-4" />
                    {systemLabel}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}