"use client";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import ProfileMenu from "@/components/profile_menu";
import {
  BookOpen,
  EyeClosed,
  EyeOff,
  FileBraces,
  HelpCircle,
  KeyRound,
  CreditCard,
  Languages,
  LayoutDashboard,
  Menu,
  Mic,
  PenTool,
  ShieldCheck,
  Sparkles,
  UsersRound,
  X,
} from "lucide-react";
import { homePageTranslations } from "@/lib/translations";

const actionIcons = {
  summarize: Sparkles,
  grammar: PenTool,
  translate: Languages,
  explain: BookOpen,
  questions: HelpCircle,
  transcribe: Mic,
  redact: EyeOff,
  mask: EyeClosed,
  compliance: ShieldCheck,
  extraction: FileBraces,
  dashboard: LayoutDashboard,
  apiKeys: KeyRound,
  projectsTeam: UsersRound,
  billing: CreditCard,
};

const sidebarActionKeys = [
  "summarize",
  "grammar",
  "translate",
  "explain",
  "questions",
];

const DESKTOP_SIDEBAR_MEDIA_QUERY =
  "(min-width: 1024px) and (hover: hover) and (pointer: fine)";
function getDesktopSidebarDefault() {
  if (typeof window === "undefined") {
    return false;
  }

  return window.matchMedia(DESKTOP_SIDEBAR_MEDIA_QUERY).matches;
}

function watchDesktopSidebarDefault(onChange) {
  if (typeof window === "undefined") {
    return () => {};
  }
const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

  const mediaQuery = window.matchMedia(DESKTOP_SIDEBAR_MEDIA_QUERY);
  const handleChange = () => onChange(mediaQuery.matches);

  handleChange();

  if (typeof mediaQuery.addEventListener === "function") {
    mediaQuery.addEventListener("change", handleChange);
    return () => mediaQuery.removeEventListener("change", handleChange);
  }

  mediaQuery.addListener(handleChange);
  return () => mediaQuery.removeListener(handleChange);
}

const SIDEBAR_SWIPE_MIN_DISTANCE_PX = 48;
const SIDEBAR_SWIPE_MAX_VERTICAL_DRIFT_PX = 96;
const SIDEBAR_SWIPE_HORIZONTAL_DOMINANCE = 1.15;
const SIDEBAR_SWIPE_SUPPRESS_CLICK_MS = 450;


function TeamAccessModal({ message, onClose, signInLabel, closeLabel }) {
  if (!message) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center bg-black/30 px-4 pt-24 backdrop-blur-sm">
      <div
        role="dialog"
        aria-modal="true"
        aria-live="polite"
        className="w-full max-w-sm rounded-2xl border app-surface-strong p-5 text-center shadow-2xl"
      >
        <button
          type="button"
          onClick={onClose}
          aria-label={closeLabel}
          className="ml-auto flex h-8 w-8 items-center justify-center rounded-xl app-text-soft transition hover:bg-neutral-100 hover:text-[var(--app-text)] dark:hover:bg-[#2d2d33]"
        >
          <X className="h-4 w-4" />
        </button>

        <div className="mx-auto mt-1 flex h-11 w-11 items-center justify-center rounded-2xl border app-surface">
          <UsersRound className="h-5 w-5 app-text-muted" />
        </div>

        <p className="mt-4 text-base font-semibold app-text">{message}</p>

        <div className="mt-5 flex justify-center gap-2">
          {signInLabel ? (
            <a
              href="/auth/login?returnTo=/"
              className="rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02]"
            >
              {signInLabel}
            </a>
          ) : null}

          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border app-surface px-4 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
          >
            {closeLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function AppSidebarLayout({ children }) {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, authChecked, hydrated, loading: accountLoading, entitlement } = useAccount();
  const [sidebarOpenState, setSidebarOpen] = useState(getDesktopSidebarDefault);
  const [sidebarHydrated, setSidebarHydrated] = useState(false);
  const sidebarOpen = sidebarHydrated ? sidebarOpenState : true;
  const [teamAccessMessage, setTeamAccessMessage] = useState("");

  const sidebarSwipeRef = useRef({
    active: false,
    pointerId: null,
    startX: 0,
    startY: 0,
    latestX: 0,
    latestY: 0,
    startedOpen: false,
    committed: false,
    cancelled: false,
  });
  const suppressSidebarClickRef = useRef(false);

  useIsomorphicLayoutEffect(() => {
    const stopWatchingDesktopSidebar =
      watchDesktopSidebarDefault(setSidebarOpen);
    setSidebarHydrated(true);

    return stopWatchingDesktopSidebar;
  }, []);

  const beginSidebarSwipe = useCallback(
    (event) => {
      if (event.pointerType === "mouse" || event.isPrimary === false) {
        return;
      }

      sidebarSwipeRef.current = {
        active: true,
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        latestX: event.clientX,
        latestY: event.clientY,
        startedOpen: sidebarOpen,
        committed: false,
        cancelled: false,
      };

      try {
        event.currentTarget.setPointerCapture?.(event.pointerId);
      } catch {
        // Some touch browsers can reject capture when the pointer is already lost.
      }
    },
    [sidebarOpen],
  );

  const suppressNextSidebarClick = useCallback(() => {
    suppressSidebarClickRef.current = true;
    window.setTimeout(() => {
      suppressSidebarClickRef.current = false;
    }, SIDEBAR_SWIPE_SUPPRESS_CLICK_MS);
  }, []);

  const handleSidebarClickCapture = useCallback((event) => {
    if (!suppressSidebarClickRef.current) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    suppressSidebarClickRef.current = false;
  }, []);

  const commitSidebarSwipe = useCallback(
    (swipe, deltaX, deltaY) => {
      if (!swipe.active || swipe.committed || swipe.cancelled) {
        return false;
      }

      const absX = Math.abs(deltaX);
      const absY = Math.abs(deltaY);

      if (
        absY > SIDEBAR_SWIPE_MAX_VERTICAL_DRIFT_PX &&
        absY > absX &&
        absX < SIDEBAR_SWIPE_MIN_DISTANCE_PX
      ) {
        swipe.cancelled = true;
        return false;
      }

      const isHorizontalSwipe =
        absX >= SIDEBAR_SWIPE_MIN_DISTANCE_PX &&
        absX >= absY * SIDEBAR_SWIPE_HORIZONTAL_DOMINANCE;

      if (!isHorizontalSwipe) {
        return false;
      }

      const shouldClose = swipe.startedOpen && deltaX < 0;
      const shouldOpen = !swipe.startedOpen && deltaX > 0;

      if (!shouldClose && !shouldOpen) {
        return false;
      }

      suppressNextSidebarClick();
      setSidebarOpen(shouldOpen);
      swipe.active = false;
      swipe.committed = true;
      return true;
    },
    [suppressNextSidebarClick],
  );

  const continueSidebarSwipe = useCallback(
    (event) => {
      const swipe = sidebarSwipeRef.current;

      if (!swipe.active || swipe.pointerId !== event.pointerId) {
        return;
      }

      swipe.latestX = event.clientX;
      swipe.latestY = event.clientY;

      commitSidebarSwipe(
        swipe,
        event.clientX - swipe.startX,
        event.clientY - swipe.startY,
      );
    },
    [commitSidebarSwipe],
  );

  const endSidebarSwipe = useCallback(
    (event) => {
      const swipe = sidebarSwipeRef.current;

      if (swipe.pointerId !== event.pointerId) {
        return;
      }

      if (swipe.active && !swipe.committed && !swipe.cancelled) {
        const endX = Number.isFinite(event.clientX) ? event.clientX : swipe.latestX;
        const endY = Number.isFinite(event.clientY) ? event.clientY : swipe.latestY;
        commitSidebarSwipe(swipe, endX - swipe.startX, endY - swipe.startY);
      }

      try {
        event.currentTarget.releasePointerCapture?.(event.pointerId);
      } catch {
        // Pointer capture may already be released by the browser.
      }

      sidebarSwipeRef.current = {
        active: false,
        pointerId: null,
        startX: 0,
        startY: 0,
        latestX: 0,
        latestY: 0,
        startedOpen: sidebarOpen,
        committed: false,
        cancelled: false,
      };
    },
    [commitSidebarSwipe, sidebarOpen],
  );

  const authReady = Boolean(hydrated && authChecked && !accountLoading);
  const isSignedIn = Boolean(authReady && user);

  const t = useMemo(
    () => homePageTranslations[language] || homePageTranslations.en,
    [language],
  );

  const teamAccessModal = t.teamAccessModal || homePageTranslations.en.teamAccessModal;

  const hasTeamAccess =
    entitlement?.source === "organization" &&
    entitlement?.status === "active" &&
    ["business", "enterprise"].includes(entitlement?.plan);

  const sidebarActions = useMemo(() => {
    const actionsByKey = new Map(
      [...t.enabledActions, ...t.lockedActions].map((action) => [
        action.key,
        {
          ...action,
          icon: actionIcons[action.key],
          requiresAuth: action.key === "questions",
        },
      ]),
    );

    return sidebarActionKeys
      .map((key) => actionsByKey.get(key))
      .filter(Boolean);
  }, [t]);

  const manageActions = useMemo(
    () =>
      (t.manageActions || []).map((action) => ({
        ...action,
        icon: actionIcons[action.key],
      })),
    [t],
  );

  const accountName =
    user?.name ||
    user?.fullName ||
    user?.displayName ||
    [user?.firstName, user?.lastName].filter(Boolean).join(" ") ||
    user?.email ||
    "Account";

  const avatarText =
    accountName
      ?.split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase() || "A";

  const requiresSignInLabel = t.requiresSignIn;

  const sidebarInteractiveClass =
    "app-text hover:bg-neutral-100 hover:text-[var(--app-text)] hover:shadow-sm dark:hover:bg-[#2d2d33]";

  const sidebarInactiveLanguageClass =
    "app-text hover:bg-neutral-100 hover:text-[var(--app-text)] hover:shadow-sm dark:hover:bg-[#2d2d33]";

  function handleSidebarActionClick(action) {
    if (action.requiresAuth && !isSignedIn) {
      return;
    }

    router.push(action.route);
  }

  function handleManageActionClick(action) {
    if (!action.route) {
      return;
    }

    if (action.key !== "projectsTeam") {
      router.push(action.route);
      return;
    }

    if (!authReady) {
      return;
    }

    if (!isSignedIn) {
      setTeamAccessMessage(teamAccessModal.signInAndUpgrade);
      return;
    }

    if (!hasTeamAccess) {
      setTeamAccessMessage(teamAccessModal.upgradeToTeamPlan);
      return;
    }

    router.push(action.route);
  }

  const sidebarActionList = (
    <div className={sidebarOpen ? "space-y-3" : "flex flex-col items-center gap-3"}>
      <nav
        aria-label={t.aiFeaturesTitle}
        className={sidebarOpen ? "space-y-0.5" : "flex flex-col items-center gap-1"}
      >
        <div
          className={
            sidebarOpen
              ? "mb-1 px-3 text-xs font-semibold normal-case tracking-[0.02em] app-text-soft"
              : "mb-1 text-center text-xs font-semibold normal-case tracking-[0.02em] app-text-soft"
          }
        >
          {sidebarOpen ? t.aiFeaturesTitle : t.aiFeaturesCompactTitle}
        </div>

        {sidebarActions.map((action) => {
          const Icon = action.icon;
          const requiresSignIn = action.requiresAuth && (!authReady || !isSignedIn);

          return (
            <button
              key={`${language}-sidebar-${action.route}`}
              type="button"
              onClick={() => handleSidebarActionClick(action)}
              aria-disabled={requiresSignIn}
              title={
                sidebarOpen
                  ? undefined
                  : requiresSignIn
                    ? `${action.name} - ${requiresSignInLabel}`
                    : action.name
              }
              className={`group flex rounded-xl text-sm transition ${
                sidebarOpen
                  ? "w-full items-center gap-2 px-3 py-1 text-left"
                  : "h-8 w-8 items-center justify-center"
              } ${
                requiresSignIn
                  ? "cursor-not-allowed opacity-60"
                  : sidebarInteractiveClass
              }`}
            >
              <span className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border app-surface-strong">
                <Icon className="h-3.5 w-3.5" />
                {requiresSignIn ? (
                  <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-amber-400 ring-2 ring-[var(--app-bg)]" />
                ) : null}
              </span>

              {sidebarOpen ? (
                <span className="min-w-0 flex-1 leading-tight">
                  <span className="block truncate font-medium app-text">
                    {action.name}
                  </span>
                  {requiresSignIn ? (
                    <span className="block truncate text-[11px] app-text-soft">
                      {requiresSignInLabel}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>

      <nav
        aria-label={t.manageTitle}
        className={sidebarOpen ? "space-y-0.5" : "flex flex-col items-center gap-1"}
      >
        <div
          className={
            sidebarOpen
              ? "mb-1 px-3 text-xs font-semibold normal-case tracking-[0.02em] app-text-soft"
              : "mb-1 text-center text-xs font-semibold normal-case tracking-[0.02em] app-text-soft"
          }
        >
          {sidebarOpen ? t.manageTitle : t.manageCompactTitle}
        </div>

        {manageActions.map((action) => {
          const Icon = action.icon;
          const isDisabled = !action.route;

          return (
            <button
              key={`${language}-manage-${action.key}`}
              type="button"
              disabled={isDisabled}
              aria-disabled={isDisabled ? "true" : undefined}
              onClick={isDisabled ? undefined : () => handleManageActionClick(action)}
              title={
                sidebarOpen
                  ? undefined
                  : isDisabled
                    ? `${action.name} - ${t.soon}`
                    : action.name
              }
              className={`group flex rounded-xl text-sm transition ${
                sidebarOpen
                  ? "w-full items-center gap-2 px-3 py-1 text-left"
                  : "h-8 w-8 items-center justify-center"
              } ${
                isDisabled
                  ? "cursor-not-allowed opacity-60"
                  : sidebarInteractiveClass
              }`}
            >
              <span className="relative flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border app-surface-strong">
                <Icon className="h-3.5 w-3.5" />
              </span>

              {sidebarOpen ? (
                <span className="min-w-0 flex-1 leading-tight">
                  <span
                    className={`block break-words font-medium leading-snug app-text ${
                      action.key === "billing" ? "text-[13px] tracking-[-0.01em]" : ""
                    }`}
                  >
                    {action.name}
                  </span>
                  {isDisabled ? (
                    <span className="block truncate text-[11px] app-text-soft">
                      {t.soon}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
    </div>
  );

  return (
    <main className="app-shell min-h-screen overflow-x-hidden">
      <TeamAccessModal
        message={teamAccessMessage}
        onClose={() => setTeamAccessMessage("")}
        signInLabel={!isSignedIn ? t.signIn : null}
        closeLabel={teamAccessModal.close}
      />
      <aside
        onClickCapture={handleSidebarClickCapture}
        onPointerDown={beginSidebarSwipe}
        onPointerMove={continueSidebarSwipe}
        onPointerUp={endSidebarSwipe}
        onPointerCancel={endSidebarSwipe}
        onLostPointerCapture={endSidebarSwipe}
        style={{ touchAction: "pan-y pinch-zoom" }}
        className={`relative min-h-screen overflow-x-hidden ${
          sidebarHydrated ? "transition-[padding] duration-300" : ""
        } ${
          sidebarHydrated ? (sidebarOpen ? "pl-72" : "pl-16") : "pl-16 lg:pl-72"
        }`}
      >
        <div
          className={`flex min-h-12 items-center border-b border-white/10 ${
            sidebarOpen ? "justify-between px-4" : "justify-center px-2"
          }`}
        >
          {sidebarOpen ? (
            <span className="text-base font-semibold app-text">
              {t.appName}
            </span>
          ) : null}

          <button
            type="button"
            onClick={() => setSidebarOpen((current) => !current)}
            aria-label={sidebarOpen ? "Close sidebar" : "Open sidebar"}
            className={`inline-flex h-9 w-9 items-center justify-center rounded-xl app-text-muted transition ${sidebarInteractiveClass}`}
          >
            {sidebarOpen ? (
              <X className="h-5 w-5" />
            ) : (
              <Menu className="h-5 w-5" />
            )}
          </button>
        </div>

        <div
          className={`min-h-0 flex-1 overflow-y-auto ${
            sidebarOpen ? "px-3 py-2" : "px-2 py-2"
          }`}
        >
          {sidebarActionList}
        </div>

        <div
          className={`absolute bottom-3 left-3 right-3 overflow-visible ${
            sidebarOpen ? "" : "flex justify-center"
          }`}
        >
          {sidebarOpen ? (
            <div className="border-t border-white/10 pt-1.5">
              {isSignedIn ? (
                <ProfileMenu
                  user={user}
                  settingsLabel={t.settings}
                  logoutLabel={t.logout}
                  appearanceLabel={t.appearance}
                  lightLabel={t.light}
                  darkLabel={t.dark}
                  systemLabel={t.systemDefault}
                  backLabel={t.back}
                  menuPlacement="top"
                  menuAlign="left"
                  fullWidth
                />
              ) : (
                <div className="home-sidebar-auth rounded-2xl border app-surface-strong p-2.5 backdrop-blur">
                  {!authReady ? (
                    <div className="text-sm app-text-soft">{t.loading}</div>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      <a
                        href="/auth/login?returnTo=/"
                        className="rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-center text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                      >
                        {t.signIn}
                      </a>

                      <a
                        href="/auth/login?screen_hint=signup&prompt=login&returnTo=/"
                        className="rounded-xl border app-surface px-3 py-2 text-center text-sm font-semibold app-text transition hover:scale-[1.02] hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] hover:shadow-xl"
                      >
                        {t.signUp}
                      </a>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : isSignedIn ? (
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open account menu"
              className={`flex h-10 w-10 items-center justify-center rounded-xl border app-surface-strong text-[11px] font-semibold app-text transition ${sidebarInteractiveClass}`}
            >
              {avatarText}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open sidebar"
              className={`inline-flex h-10 w-10 items-center justify-center rounded-xl app-text-muted transition ${sidebarInteractiveClass}`}
            >
              <Menu className="h-5 w-5" />
            </button>
          )}
        </div>
      </aside>

      <div
        className={`relative min-h-screen overflow-x-hidden ${
          sidebarHydrated ? "transition-[padding] duration-300" : ""
        } ${
          sidebarHydrated ? (sidebarOpen ? "pl-72" : "pl-16") : "pl-16 lg:pl-72"
        }`}
      >
        {children}
      </div>
    </main>
  );
}
