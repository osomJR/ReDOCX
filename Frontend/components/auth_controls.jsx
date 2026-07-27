"use client";

import ProfileMenu from "@/components/profile_menu";
import { buildAuthSignInUrl, buildAuthSignUpUrl } from "@/lib/auth_urls";

export default function AuthControls({
  user,
  authChecked,
  hydrated = true,
  signInLabel = "Sign In",
  signUpLabel = "Sign Up",
  loadingLabel = "Loading...",
  logoutLabel = "Logout",
  logoutConfirmTitle = "Are you sure you want to Logout?",
  logoutConfirmYesLabel = "Yes",
  logoutReturnDashboardLabel = "Return back to Dashboard",
  settingsLabel = "Settings",
  appearanceLabel = "Appearance",
  helpLabel = "Help",
  privacyPolicyLabel = "Privacy Policy",
  termsOfUseLabel = "Terms of Use",
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
  language = "en",
}) {
  if (!hydrated || !authChecked) {
    return <div className="text-sm app-text-soft">{loadingLabel}</div>;
  }

  if (!user) {
    return (
      <div className="flex flex-wrap gap-3">
        <a
          href={buildAuthSignInUrl(language)}
          className="rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
        >
          {signInLabel}
        </a>

        <a
          href={buildAuthSignUpUrl(language)}
          className="rounded-2xl border app-surface px-5 py-3 text-sm font-semibold app-text transition hover:scale-[1.02] hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] hover:shadow-xl"
        >
          {signUpLabel}
        </a>
      </div>
    );
  }

  return (
    <ProfileMenu
      user={user}
      language={language}
      settingsLabel={settingsLabel}
      logoutLabel={logoutLabel}
      logoutConfirmTitle={logoutConfirmTitle}
      logoutConfirmYesLabel={logoutConfirmYesLabel}
      logoutReturnDashboardLabel={logoutReturnDashboardLabel}
      appearanceLabel={appearanceLabel}
      helpLabel={helpLabel}
      privacyPolicyLabel={privacyPolicyLabel}
      termsOfUseLabel={termsOfUseLabel}
      lightLabel={lightLabel}
      darkLabel={darkLabel}
      systemLabel={systemLabel}
      backLabel={backLabel}
      changePasswordLabel={changePasswordLabel}
      changePasswordSendingLabel={changePasswordSendingLabel}
      changePasswordSuccessLabel={changePasswordSuccessLabel}
      changePasswordErrorLabel={changePasswordErrorLabel}
      deleteAccountLabel={deleteAccountLabel}
      deleteAccountCancelLabel={deleteAccountCancelLabel}
      deleteAccountErrorLabel={deleteAccountErrorLabel}
    />
  );
}