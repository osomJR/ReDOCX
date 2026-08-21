"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ChevronDown,
  FileText,
  HelpCircle,
  Languages,
  KeyRound,
  Loader2,
  LogOut,
  Monitor,
  Moon,
  RotateCcw,
  Settings,
  ShieldCheck,
  Sun,
  Trash2,
  UsersRound,
} from "lucide-react";
import { useTheme } from "@/components/theme_provider";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  deleteAccount,
  requestPasswordChange,
  restoreAccount,
} from "@/lib/api_client";

const PLAN_LABELS = Object.freeze({
  en: Object.freeze({
    free: "Free",
    personal: "Personal",
    business: "Business",
    enterprise: "Enterprise",
  }),
  fr: Object.freeze({
    free: "Gratuit",
    personal: "Personnel",
    business: "Professionnel",
    enterprise: "Entreprise",
  }),
});

function formatPlanLabel(plan, language = "en") {
  const normalizedPlan = String(plan || "free").trim().toLowerCase();
  const localizedLabels = PLAN_LABELS[language] || PLAN_LABELS.en;

  if (localizedLabels[normalizedPlan]) {
    return localizedLabels[normalizedPlan];
  }

  return normalizedPlan
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
  languageLabel = "Language",
  englishLabel = "English",
  frenchLabel = "Français",
  teamSettingsLabel,
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
  deleteAccountCancelLabel = "Cancel",
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
  const [restoringAccount, setRestoringAccount] = useState(false);
  const [restoreAccountError, setRestoreAccountError] = useState("");
  const accountExitStartedRef = useRef(false);
  const containerRef = useRef(null);
  const router = useRouter();
  const { theme, setTheme, loading } = useTheme();
  const { account, entitlement, beginAccountExit, reloadAccount } = useAccount();
  const { language, setLanguage } = useLanguage();
  const resolvedTeamSettingsLabel =
    teamSettingsLabel ||
    (language === "fr" ? "Paramètres de l’équipe" : "Team settings");
  const accountDeletionCopy =
    language === "fr"
      ? {
          title: "Désactiver et programmer la suppression ?",
          description:
            "Votre compte sera désactivé immédiatement et tout renouvellement futur sera arrêté. Un compte gratuit est définitivement supprimé après 30 jours. S’il reste une période payée, la suppression définitive intervient 30 jours après la fin de cette période. Connectez-vous avant l’échéance applicable, puis choisissez explicitement « Restaurer mon compte » pour annuler la suppression.",
          confirm: "Désactiver mon compte",
          deleting: "Désactivation...",
        }
      : {
          title: "Deactivate and schedule deletion?",
          description:
            "Your account will be deactivated immediately and any future subscription renewal will be stopped. Free accounts are permanently deleted after 30 days. If a paid period remains, permanent deletion occurs 30 days after that period ends. Sign in before the applicable deadline, then explicitly choose “Restore my account” to cancel deletion.",
          confirm: "Deactivate my account",
          deleting: "Deactivating...",
        };
  const lifecycleStatus = String(account?.account_lifecycle?.status || "")
    .trim()
    .toLowerCase();
  const canRestoreAccount = lifecycleStatus === "deactivated_pending_deletion";
  const deactivationInProgress = lifecycleStatus === "deactivation_requested";
  const purgeDue = lifecycleStatus === "purge_due";
  const accountRestricted =
    canRestoreAccount || deactivationInProgress || purgeDue;
  const restoreCopy =
    language === "fr"
      ? {
          label: "Restaurer mon compte",
          restoring: "Restauration...",
          error: "Impossible de restaurer votre compte. Veuillez réessayer.",
        }
      : {
          label: "Restore my account",
          restoring: "Restoring...",
          error: "Could not restore your account. Please try again.",
        };
  const restrictedStatusCopy =
    language === "fr"
      ? deactivationInProgress
        ? "La désactivation de votre compte est en cours. ReDOCX réessaiera automatiquement toute étape inachevée."
        : "La période de restauration a expiré. La suppression définitive de votre compte est en attente."
      : deactivationInProgress
        ? "Your account deactivation is being finalized. ReDOCX will automatically retry any incomplete step."
        : "Your recovery window has elapsed. Permanent account deletion is pending.";

  useEffect(() => {
    if (!accountRestricted) return;
    setShowSettings(false);
    setShowDeleteAccountConfirm(false);
    setChangePasswordStatus("");
    setChangePasswordError("");
    setDeleteAccountError("");
  }, [accountRestricted]);

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
        setRestoreAccountError("");
      }
    }

    document.addEventListener("mousedown", handleClickOutside);

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const displayEmail = String(user?.email || "").trim();
  const preferredDisplayName =
    user?.name ||
    user?.fullName ||
    user?.displayName ||
    [user?.firstName, user?.lastName].filter(Boolean).join(" ") ||
    user?.nickname ||
    "";
  const displayName = String(preferredDisplayName || displayEmail || "Account").trim();
  const showDisplayEmail = Boolean(
    displayEmail && displayEmail.toLowerCase() !== displayName.toLowerCase(),
  );
  const initial = displayName.charAt(0).toUpperCase() || "A";

  const planLabel = formatPlanLabel(entitlement?.plan, language);
  const organizationName = entitlement?.organization_name || "";
  const organizationRole = formatRoleLabel(entitlement?.organization_role);
  const planDescription = organizationName
    ? `${planLabel} · ${organizationName}`
    : planLabel;
  const showRole = Boolean(organizationRole && organizationName);
  const showTeamSettings =
    entitlement?.source === "organization" &&
    entitlement?.status === "active" &&
    ["business", "enterprise"].includes(entitlement?.plan);
  const showChangePassword = supportsPasswordChange(user);

  const menuPlacementClass =
    menuPlacement === "top"
      ? "bottom-full mb-3 max-h-[calc(100dvh-8rem)]"
      : "top-full mt-3 max-h-[calc(100dvh-8rem)]";
  const menuWidthClass = showSettings
    ? "w-[min(22rem,calc(100vw-2rem))]"
    : "w-[min(18rem,calc(100vw-2rem))]";

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
      const deletion = await deleteAccount();
      const deletionStatus = String(deletion?.lifecycle?.status || "").trim();
      beginAccountExit?.(deletionStatus || "account_deactivated_pending_deletion");
      window.location.replace("/auth/logout");
    } catch (error) {
      accountExitStartedRef.current = false;
      setDeleteAccountError(error?.message || deleteAccountErrorLabel);
      setDeletingAccount(false);
    }
  }

  async function handleRestoreAccount() {
    if (restoringAccount || accountExitStartedRef.current) return;

    setRestoringAccount(true);
    setRestoreAccountError("");

    try {
      await restoreAccount();
      await reloadAccount?.({
        background: false,
        forceRefresh: true,
        allowCurrentAccountFallback: false,
      });
      setOpen(false);
      setShowSettings(false);
    } catch (error) {
      setRestoreAccountError(error?.message || restoreCopy.error);
    } finally {
      setRestoringAccount(false);
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
            setRestoreAccountError("");
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
            {showDisplayEmail ? (
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
          className={`absolute ${menuAlignClass} ${menuPlacementClass} ${menuWidthClass} z-[80] overflow-y-auto overscroll-contain rounded-3xl border app-surface-strong p-2 shadow-2xl backdrop-blur-xl`}
        >
          {showDeleteAccountConfirm ? (
            <div className="space-y-3 p-1">
              <div className="rounded-2xl border border-red-400/30 bg-red-400/10 p-4 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-red-600 text-white">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <p className="mt-3 text-sm font-semibold app-text">
                  {accountDeletionCopy.title}
                </p>
                <p className="mt-2 text-xs leading-5 app-text-muted">
                  {accountDeletionCopy.description}
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
                    ? accountDeletionCopy.deleting
                    : accountDeletionCopy.confirm}
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
                  {showDisplayEmail ? (
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

              <div className="my-2 h-px bg-[var(--app-border)]" />

              {canRestoreAccount ? (
                <>
                  <button
                    type="button"
                    onClick={handleRestoreAccount}
                    disabled={restoringAccount}
                    className={`flex w-full items-center gap-3 rounded-2xl border border-emerald-400/30 bg-emerald-400/10 px-4 py-3 text-left text-sm font-semibold text-emerald-700 transition dark:text-emerald-200 ${hoverItemClass} disabled:cursor-not-allowed disabled:opacity-70`}
                  >
                    {restoringAccount ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )}
                    <span>
                      {restoringAccount
                        ? restoreCopy.restoring
                        : restoreCopy.label}
                    </span>
                  </button>
                  {restoreAccountError ? (
                    <p className="rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs font-medium text-red-600 dark:text-red-200">
                      {restoreAccountError}
                    </p>
                  ) : null}
                </>
              ) : accountRestricted ? (
                <div className="rounded-2xl border border-amber-400/30 bg-amber-400/10 px-4 py-3 text-xs font-medium leading-5 text-amber-800 dark:text-amber-200">
                  {restrictedStatusCopy}
                </div>
              ) : (
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
              )}

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

              {showTeamSettings ? (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    setShowSettings(false);
                    router.push("/settings/team");
                  }}
                  className={`flex w-full items-center gap-3 rounded-2xl border app-surface px-4 py-3 text-left text-sm font-semibold app-text transition ${hoverItemClass}`}
                >
                  <UsersRound className="h-4 w-4 app-text-muted" />
                  <span>{resolvedTeamSettingsLabel}</span>
                </button>
              ) : null}

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
                  <Languages className="h-4 w-4 app-text-muted" />
                  {languageLabel}
                </div>

                <div className="space-y-2">
                  <button
                    type="button"
                    onClick={() => setLanguage("en")}
                    className={`flex w-full items-center rounded-2xl px-3 py-3 text-sm transition ${
                      language === "en"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : `app-surface app-text ${hoverItemClass}`
                    }`}
                  >
                    {englishLabel}
                  </button>

                  <button
                    type="button"
                    onClick={() => setLanguage("fr")}
                    className={`flex w-full items-center rounded-2xl px-3 py-3 text-sm transition ${
                      language === "fr"
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : `app-surface app-text ${hoverItemClass}`
                    }`}
                  >
                    {frenchLabel}
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

                {!accountRestricted ? (
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
                ) : null}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}