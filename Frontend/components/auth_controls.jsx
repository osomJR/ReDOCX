"use client";

import ProfileMenu from "@/components/profile_menu";

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
  lightLabel = "Light",
  darkLabel = "Dark",
  systemLabel = "System Default",
  backLabel = "back",
}) {
  if (!hydrated || !authChecked) {
    return <div className="text-sm app-text-soft">{loadingLabel}</div>;
  }

  if (!user) {
    return (
      <div className="flex flex-wrap gap-3">
        <a
          href="/auth/login?returnTo=/"
          className="rounded-2xl bg-[var(--app-button-bg)] px-5 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
        >
          {signInLabel}
        </a>

        <a
          href="/auth/login?screen_hint=signup&prompt=login&returnTo=/"
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
      settingsLabel={settingsLabel}
      logoutLabel={logoutLabel}
      logoutConfirmTitle={logoutConfirmTitle}
      logoutConfirmYesLabel={logoutConfirmYesLabel}
      logoutReturnDashboardLabel={logoutReturnDashboardLabel}
      appearanceLabel={appearanceLabel}
      lightLabel={lightLabel}
      darkLabel={darkLabel}
      systemLabel={systemLabel}
      backLabel={backLabel}
    />
  );
}
