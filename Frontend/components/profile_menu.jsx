"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ChevronDown,
  FileText,
  HelpCircle,
  KeyRound,
  Loader2,
  LogOut,
  Monitor,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  Trash2,
} from "lucide-react";
import { useTheme } from "@/components/theme_provider";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { deleteAccount, requestPasswordChange } from "@/lib/api_client";

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

function authProviderFromUser(user) {
  const explicitProvider = user?.auth_provider || user?.provider;
  if (typeof explicitProvider === "string" && explicitProvider.trim()) {
    return explicitProvider.trim().toLowerCase();
  }

  const subject = String(user?.id || user?.sub || "").trim();
  if (subject.includes("|")) {
    return subject.split("|", 1)[0].toLowerCase();
  }

  return "";
}

function supportsPasswordChange(user) {
  if (user?.password_change_supported === true) return true;
  if (user?.password_change_supported === false) return false;

  return authProviderFromUser(user) === "auth0";
}

export default function ProfileMenu({
  user,
  settingsLabel = "Settings",
  logoutLabel = "Logout",
  logoutConfirmTitle = "Are you sure you want to Logout?",
  logoutConfirmYesLabel = "Yes",
  logoutReturnDashboardLabel = "Return back to Dashboard",
  appearanceLabel = "Appearance",
  helpLabel = "Help",
  privacyPolicyLabel = "Privacy Policy",
  termsOfUseLabel = "Terms of Use",
  privacyPolicyHref = "/privacy-policy",
  termsOfUseHref = "/terms-of-use",
  lightLabel = "Light",
  darkLabel = "Dark",
  systemLabel = "System Default",
  backLabel = "back",
  changePasswordLabel = "Change password",
  changePasswordSendingLabel = "Sending...",
  changePasswordSuccessLabel = "Password reset email sent. You are being signed out.",
  changePasswordErrorLabel = "Could not start password change. Please try again.",
  deleteAccountLabel = "Delete my account",
  deleteAccountConfirmTitle = "Delete your account?",
  deleteAccountConfirmDescription = "This permanently deletes your ReDOCX account and signs you out. This action cannot be undone.",
  deleteAccountConfirmButtonLabel = "Delete my account",
  deleteAccountCancelLabel = "Cancel",
  deleteAccountDeletingLabel = "Deleting...",
  deleteAccountErrorLabel = "Could not delete your account. Please try again.",
  menuPlacement = "bottom",
  menuAlign = "right",
  fullWidth = false,
}) {
  const [open, setOpen] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showDeleteAccountConfirm, setShowDeleteAccountConfirm] =
    useState(false);
  const [requestingPasswordChange, setRequestingPasswordChange] =
    useState(false);
  const [changePasswordStatus, setChangePasswordStatus] = useState("");
  const [changePasswordError, setChangePasswordError] = useState("");
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [deleteAccountError, setDeleteAccountError] = useState("");
  const accountExitStartedRef = useRef(false);
  const containerRef = useRef(null);
  const router = useRouter();
  const { theme, setTheme, loading } = useTheme();
  const { entitlement, beginAccountExit } = useAccount();
  const { language } = useLanguage();

  useEffect(() => {
    function handleClickOutside(event) {
      if (!containerRef.current?.contains(event.target)) {
        setOpen(false);
        setShowSettings(false);
        setShowLogoutConfirm(false);
        setShowDeleteAccountConfirm(false);
        setChangePasswordStatus("");
        setChangePasswordError("");
        setDeleteAccountError("");
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
  const showChangePassword = supportsPasswordChange(user);

  const menuPlacementClass =
    menuPlacement === "top" ? "bottom-full mb-3" : "top-full mt-3";

  const menuAlignClass = menuAlign === "left" ? "left-0" : "right-0";

  const hoverItemClass =
    "hover:bg-neutral-100 hover:text-[var(--app-text)] hover:shadow-sm dark:hover:bg-[#2d2d33]";
  const destructiveHoverItemClass =
    "hover:border-red-400/40 hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-950/30 dark:hover:text-red-200";

  function handleLogoutClick(event) {
    event?.preventDefault?.();

    if (accountExitStartedRef.current) {
      return;
    }

    accountExitStartedRef.current = true;
    beginAccountExit?.("logout");
    window.location.replace("/auth/logout");
  }

  async function handleDeleteAccountConfirm() {
    if (deletingAccount || accountExitStartedRef.current) return;

    accountExitStartedRef.current = true;
    setDeletingAccount(true);
    setDeleteAccountError("");

    try {
      await deleteAccount();
      beginAccountExit?.("account_deleted");
      window.location.replace("/auth/logout");
    } catch (error) {
      accountExitStartedRef.current = false;
      setDeleteAccountError(error?.message || deleteAccountErrorLabel);
      setDeletingAccount(false);
    }
  }

  async function handleChangePasswordClick() {
    if (requestingPasswordChange || accountExitStartedRef.current) return;

    setRequestingPasswordChange(true);
    setChangePasswordStatus("");
    setChangePasswordError("");

    try {
      await requestPasswordChange({ locale: language });

      accountExitStartedRef.current = true;
      setChangePasswordStatus(changePasswordSuccessLabel);
      beginAccountExit?.("password_change_requested");

      window.setTimeout(() => {
        window.location.replace("/auth/logout");
      }, 900);
    } catch (error) {
      accountExitStartedRef.current = false;
      setChangePasswordError(error?.message || changePasswordErrorLabel);
      setRequestingPasswordChange(false);
    }
  }

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
            setShowDeleteAccountConfirm(false);
            setChangePasswordStatus("");
            setChangePasswordError("");
            setDeleteAccountError("");
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
          {showDeleteAccountConfirm ? (
            <div className="space-y-3 p-1">
              <div className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-red-600 text-white">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <p className="mt-3 text-sm font-semibold app-text">
                  {deleteAccountConfirmTitle}
                </p>
                <p className="mt-2 text-xs leading-5 app-text-muted">
                  {deleteAccountConfirmDescription}
                </p>
                {deleteAccountError ? (
                  <p className="mt-3 rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs font-medium text-red-600 dark:text-red-200">
                    {deleteAccountError}
                  </p>
                ) : null}
              </div>

              <div className="grid gap-2">
                <button
                  type="button"
                  onClick={handleDeleteAccountConfirm}
                  disabled={deletingAccount}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl bg-red-600 px-4 py-3 text-sm font-semibold text-white transition hover:scale-[1.01] hover:bg-red-700 hover:shadow-xl disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {deletingAccount ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  {deletingAccount
                    ? deleteAccountDeletingLabel
                    : deleteAccountConfirmButtonLabel}
                </button>

                <button
                  type="button"
                  onClick={() => {
                    if (deletingAccount) return;
                    setShowDeleteAccountConfirm(false);
                    setShowSettings(true);
                    setDeleteAccountError("");
                  }}
                  disabled={deletingAccount}
                  className={`rounded-2xl border app-surface px-4 py-3 text-sm font-semibold app-text transition ${hoverItemClass} disabled:cursor-not-allowed disabled:opacity-70`}
                >
                  {deleteAccountCancelLabel}
                </button>
              </div>
            </div>
          ) : showLogoutConfirm ? (
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
                  onClick={handleLogoutClick}
                  className="rounded-2xl bg-[var(--app-button-bg)] px-4 py-3 text-center text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] hover:shadow-xl"
                >
                  {logoutConfirmYesLabel}
                </a>

                <button
                  type="button"
                  onClick={() => {
                    setShowLogoutConfirm(false);
                    setShowSettings(false);
                    setShowDeleteAccountConfirm(false);
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
              <div
                className={`flex items-center gap-3 rounded-2xl px-3 py-3 transition ${hoverItemClass}`}
              >
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
                  setShowDeleteAccountConfirm(false);
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
                  setShowDeleteAccountConfirm(false);
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
                onClick={() => {
                  setShowSettings(false);
                  setDeleteAccountError("");
                }}
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

              <div className="rounded-2xl border app-surface p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-semibold app-text">
                  <HelpCircle className="h-4 w-4 app-text-muted" />
                  {helpLabel}
                </div>

                <div className="space-y-2">
                  <a
                    href={privacyPolicyHref}
                    onClick={() => setOpen(false)}
                    className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm transition app-surface app-text ${hoverItemClass}`}
                  >
                    <ShieldCheck className="h-4 w-4 app-text-muted" />
                    <span>{privacyPolicyLabel}</span>
                  </a>

                  <a
                    href={termsOfUseHref}
                    onClick={() => setOpen(false)}
                    className={`flex w-full items-center gap-3 rounded-2xl px-3 py-3 text-sm transition app-surface app-text ${hoverItemClass}`}
                  >
                    <FileText className="h-4 w-4 app-text-muted" />
                    <span>{termsOfUseLabel}</span>
                  </a>
                </div>
              </div>

              {showChangePassword && changePasswordStatus ? (
                <p className="rounded-2xl border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs font-medium text-emerald-700 dark:text-emerald-200">
                  {changePasswordStatus}
                </p>
              ) : null}

              {showChangePassword && changePasswordError ? (
                <p className="rounded-2xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs font-medium text-red-600 dark:text-red-200">
                  {changePasswordError}
                </p>
              ) : null}

              <div
                className={
                  showChangePassword ? "grid grid-cols-2 gap-2" : "grid gap-2"
                }
              >
                {showChangePassword ? (
                  <button
                    type="button"
                    onClick={handleChangePasswordClick}
                    disabled={requestingPasswordChange}
                    className={`flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border app-surface px-3 py-3 text-center text-xs font-semibold app-text transition ${hoverItemClass} disabled:cursor-not-allowed disabled:opacity-70`}
                  >
                    {requestingPasswordChange ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <KeyRound className="h-4 w-4" />
                    )}
                    <span>
                      {requestingPasswordChange
                        ? changePasswordSendingLabel
                        : changePasswordLabel}
                    </span>
                  </button>
                ) : null}

                <button
                  type="button"
                  onClick={() => {
                    setShowSettings(false);
                    setShowLogoutConfirm(false);
                    setShowDeleteAccountConfirm(true);
                    setChangePasswordStatus("");
                    setChangePasswordError("");
                    setDeleteAccountError("");
                  }}
                  className={`flex min-h-12 w-full items-center justify-center gap-2 rounded-2xl border border-red-400/20 bg-red-400/10 px-3 py-3 text-center text-xs font-semibold text-red-600 transition dark:text-red-200 ${destructiveHoverItemClass}`}
                >
                  <Trash2 className="h-4 w-4" />
                  <span>{deleteAccountLabel}</span>
                </button>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}