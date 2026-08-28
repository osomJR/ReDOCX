"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLanguage } from "@/components/language_provider";
import { useAccount } from "@/components/account_provider";
import ActionCard from "@/components/ActionCard";
import ProfileMenu from "@/components/profile_menu";
import { usePathname, useRouter } from "next/navigation";
import AuthControls from "@/components/auth_controls";
import {
  FileText,
  Files,
  Sparkles,
  Languages,
  BookOpen,
  PenTool,
  Mic,
  Volume2,
  Bot,
  HelpCircle,
  Shield,
  ShieldCheck,
  FileBraces,
  Signature,
  LayoutDashboard,
  KeyRound,
  LockKeyhole,
  CreditCard,
  CheckCircle2,
  UsersRound,
  Menu,
  X,
} from "lucide-react";
import { homePageTranslations,
  getPageRuntimeCopy,
  resolveErrorMessage,
} from "@/lib/translations";
import { buildAuthSignInUrl, buildAuthSignUpUrl } from "@/lib/auth_urls";
import { getAccessToken } from "@/lib/api_client";
const actionIcons = {
  convert: FileText,
  summarize: Sparkles,
  grammar: PenTool,
  translate: Languages,
  explain: BookOpen,
  transcribe: Mic,
  textToSpeech: Volume2,
  voiceAgent: Bot,
  questions: HelpCircle,
  sensitiveDataProtection: Shield,
  compliance: ShieldCheck,
  eSignature: Signature,
  vault: LockKeyhole,
  pdfTools: Files,
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

const sidebarActionKeySet = new Set(sidebarActionKeys);

function isSidebarRouteActive(pathname, route) {
  if (!route) return false;
  if (route === "/") return pathname === "/";
  return pathname === route || pathname.startsWith(`${route}/`);
}

const DESKTOP_SIDEBAR_MEDIA_QUERY =
  "(min-width: 1024px) and (hover: hover) and (pointer: fine)";
const SIDEBAR_SESSION_STATE_KEY = "redocx:sidebar-open:v1";

function readStoredDesktopSidebarState() {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const storedState = window.sessionStorage.getItem(
      SIDEBAR_SESSION_STATE_KEY,
    );

    if (storedState === "open") return true;
    if (storedState === "closed") return false;
  } catch {
    // Sidebar persistence is a non-critical UI enhancement only.
  }

  return null;
}

function writeStoredDesktopSidebarState(isOpen) {
  if (typeof window === "undefined") {
    return;
  }

  try {
    if (!window.matchMedia(DESKTOP_SIDEBAR_MEDIA_QUERY).matches) {
      return;
    }

    window.sessionStorage.setItem(
      SIDEBAR_SESSION_STATE_KEY,
      isOpen ? "open" : "closed",
    );
  } catch {
    // Ignore storage failures; the sidebar still works without persistence.
  }
}

function resolveDesktopSidebarDefault(isDesktop) {
  if (!isDesktop) {
    return false;
  }

  const storedState = readStoredDesktopSidebarState();
  return storedState ?? true;
}

function getDesktopSidebarDefault() {
  if (typeof window === "undefined") {
    return false;
  }

  return resolveDesktopSidebarDefault(
    window.matchMedia(DESKTOP_SIDEBAR_MEDIA_QUERY).matches,
  );
}

const useIsomorphicLayoutEffect =
  typeof window === "undefined" ? useEffect : useLayoutEffect;

function watchDesktopSidebarDefault(onChange) {
  if (typeof window === "undefined") {
    return () => {};
  }

  const mediaQuery = window.matchMedia(DESKTOP_SIDEBAR_MEDIA_QUERY);
  const handleChange = () =>
    onChange(resolveDesktopSidebarDefault(mediaQuery.matches));

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

const dashboardActionKeys = [
  "convert",
  "compliance",
  "eSignature",
  "vault",
  "pdfTools",
  "extraction",
  "sensitiveDataProtection",
  "transcribe",
  "textToSpeech",
  "voiceAgent",
];

const TEAM_INVITATIONS_CACHE_TTL_MS = 60_000;

function getTeamInvitationsCacheKey(userId) {
  return userId ? `redocx:team-invitations:v1:${userId}` : "";
}

function readTeamInvitationsCache(userId) {
  if (typeof window === "undefined") return null;

  const cacheKey = getTeamInvitationsCacheKey(userId);
  if (!cacheKey) return null;

  try {
    const cached = JSON.parse(
      window.sessionStorage.getItem(cacheKey) || "null",
    );
    if (
      !cached ||
      Date.now() - Number(cached.cachedAt || 0) > TEAM_INVITATIONS_CACHE_TTL_MS
    ) {
      return null;
    }
    return Array.isArray(cached.invitations) ? cached.invitations : [];
  } catch {
    return null;
  }
}

function writeTeamInvitationsCache(userId, invitations) {
  if (typeof window === "undefined") return;

  const cacheKey = getTeamInvitationsCacheKey(userId);
  if (!cacheKey) return;

  try {
    window.sessionStorage.setItem(
      cacheKey,
      JSON.stringify({
        cachedAt: Date.now(),
        invitations: Array.isArray(invitations) ? invitations : [],
      }),
    );
  } catch {
    // Session cache is a performance enhancement only.
  }
}

function titleCase(value) {
  if (!value) return "—";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

async function readJson(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const error = Object.assign(new Error("TEAM_REQUEST_FAILED"), {
      payload: data,
      code: data?.error?.code || data?.detail?.error || "TEAM_REQUEST_FAILED",
      status: response.status,
    });
    throw error;
  }

  return data;
}

function isBusinessOrEnterpriseInvitation(invitation) {
  const plan = invitation?.subscription?.plan;
  const subscriptionStatus = invitation?.subscription?.status;
  const memberStatus = invitation?.member?.status;

  return (
    ["business", "enterprise"].includes(plan) &&
    subscriptionStatus === "active" &&
    memberStatus === "invited"
  );
}

function TeamInvitationToast({
  invitation,
  busy,
  message,
  copy,
  onAccept,
  onDeny,
}) {
  if (!invitation) {
    return null;
  }

  const planLabel = titleCase(invitation?.subscription?.plan);
  const roleLabel = titleCase(invitation?.member?.role);

  return (
    <section
      role="status"
      aria-live="polite"
      className="fixed bottom-5 right-5 z-[90] w-[calc(100vw-2.5rem)] max-w-sm rounded-3xl border app-surface-strong p-5 shadow-2xl backdrop-blur md:bottom-7 md:right-7"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border app-surface">
          <UsersRound className="h-5 w-5 app-text-muted" />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
            {copy.title}
          </p>
          <h2 className="mt-1 text-base font-semibold app-text">
            {copy.body
              .replace(
                "{organization}",
                invitation.name || copy.fallbackOrganization,
              )
              .replace("{plan}", planLabel)}
          </h2>
          <p className="mt-1 text-xs app-text-muted">
            {roleLabel} · {planLabel}
          </p>
        </div>
      </div>

      {message ? (
        <p className="mt-4 rounded-2xl border border-[var(--app-border)] app-surface px-3 py-2 text-xs app-text">
          {message}
        </p>
      ) : null}

      <div className="mt-4 grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={() => onDeny(invitation.id)}
          disabled={Boolean(busy)}
          className="rounded-2xl border app-surface px-4 py-2.5 text-sm font-semibold app-text transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#2d2d33]"
        >
          {busy === `deny:${invitation.id}` ? copy.denying : copy.deny}
        </button>

        <button
          type="button"
          onClick={() => onAccept(invitation.id)}
          disabled={Boolean(busy)}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
        >
          <CheckCircle2 className="h-4 w-4" />
          {busy === `accept:${invitation.id}` ? copy.accepting : copy.accept}
        </button>
      </div>
    </section>
  );
}

function TeamAccessModal({
  message,
  onClose,
  signInLabel,
  closeLabel,
  signInHref = "/auth/login?returnTo=/",
}) {
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
              href={signInHref}
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

export default function HomePage() {
  const router = useRouter();
  const pathname = usePathname();
  const { language } = useLanguage();
  const { user, authChecked, entitlement, reloadAccount } = useAccount();
  const [sidebarOpenState, setSidebarOpenState] = useState(
    getDesktopSidebarDefault,
  );
  const [sidebarHydrated, setSidebarHydrated] = useState(false);
  const sidebarLayoutResolved =
    sidebarHydrated || typeof window !== "undefined";
  const sidebarOpen = sidebarLayoutResolved ? sidebarOpenState : true;
  const [teamAccessMessage, setTeamAccessMessage] = useState("");
  const [teamInvitations, setTeamInvitations] = useState([]);
  const [invitationBusy, setInvitationBusy] = useState("");
  const [invitationMessage, setInvitationMessage] = useState("");
  const invitationLoadStartedRef = useRef(false);

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

  const setSidebarOpen = useCallback((nextValue, { persist = false } = {}) => {
    setSidebarOpenState((current) => {
      const nextOpen =
        typeof nextValue === "function" ? nextValue(current) : nextValue;
      const normalizedNextOpen = Boolean(nextOpen);

      if (persist) {
        writeStoredDesktopSidebarState(normalizedNextOpen);
      }

      return normalizedNextOpen;
    });
  }, []);

  useIsomorphicLayoutEffect(() => {
    const stopWatchingDesktopSidebar =
      watchDesktopSidebarDefault(setSidebarOpen);
    setSidebarHydrated(true);

    return stopWatchingDesktopSidebar;
  }, [setSidebarOpen]);

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
      setSidebarOpen(shouldOpen, { persist: true });
      swipe.active = false;
      swipe.committed = true;
      return true;
    },
    [setSidebarOpen, suppressNextSidebarClick],
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
        const endX = Number.isFinite(event.clientX)
          ? event.clientX
          : swipe.latestX;
        const endY = Number.isFinite(event.clientY)
          ? event.clientY
          : swipe.latestY;
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

  const isSignedIn = !!user;

  const t = useMemo(
    () => homePageTranslations[language] || homePageTranslations.en,
    [language],
  );

  const teamAccessModal =
    t.teamAccessModal || homePageTranslations.en.teamAccessModal;
  const invitationToast =
    t.teamInvitationToast ||
    homePageTranslations.en.teamInvitationToast ||
    defaultInvitationToastCopy;

  const hasTeamAccess =
    entitlement?.source === "organization" &&
    entitlement?.is_paid === true &&
    ["business", "enterprise"].includes(entitlement?.plan);

  const hasPaidAccess =
    entitlement?.is_paid === true &&
    ["personal", "business", "enterprise"].includes(entitlement?.plan);

  const dashboardActions = useMemo(() => {
    const lockedActionKeys = new Set(
      t.lockedActions.map((action) => action.key),
    );
    const actionsByKey = new Map(
      [...t.enabledActions, ...t.lockedActions].map((action) => [
        action.key,
        {
          ...action,
          icon: actionIcons[action.key],
          requiresAuth: lockedActionKeys.has(action.key),
          requiresPaid: Boolean(action.requiresPaid),
        },
      ]),
    );

    return dashboardActionKeys
      .map((key) => actionsByKey.get(key))
      .filter(Boolean);
  }, [t]);

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
  const sidebarActiveClass =
    "bg-neutral-100 text-[var(--app-text)] shadow-sm dark:bg-[#2d2d33]";

  function upsertTeamInvitation(invitation) {
    if (!isBusinessOrEnterpriseInvitation(invitation)) {
      return;
    }

    setTeamInvitations((current) => {
      const filtered = current.filter((item) => item.id !== invitation.id);
      const next = [invitation, ...filtered];
      writeTeamInvitationsCache(user?.id, next);
      return next;
    });
    setInvitationMessage("");
  }

  async function loadTeamInvitations({ preferCache = true } = {}) {
    if (!authChecked || !isSignedIn) {
      setTeamInvitations([]);
      setInvitationMessage("");
      return;
    }

    if (preferCache) {
      const cachedInvitations = readTeamInvitationsCache(user?.id);
      if (cachedInvitations) {
        setTeamInvitations(cachedInvitations);
      }
    }

    try {
      const token = await getAccessToken();

      const response = await fetch("/api/organizations/me", {
        method: "GET",
        credentials: "include",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        },
      });
      const data = await readJson(response);
      const nextInvitations = (data.invitations || []).filter(
        isBusinessOrEnterpriseInvitation,
      );

      setTeamInvitations(nextInvitations);
      writeTeamInvitationsCache(user?.id, nextInvitations);
    } catch (error) {
      // Keep the main dashboard usable even if invitation loading fails.
      if (!readTeamInvitationsCache(user?.id)) {
        setInvitationMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
      }
    }
  }

  async function respondToInvitation(organizationId, action) {
    if (!organizationId || invitationBusy) {
      return;
    }

    setInvitationBusy(`${action}:${organizationId}`);
    setInvitationMessage("");

    try {
      const token = await getAccessToken();

      const response = await fetch(
        `/api/organizations/${organizationId}/invitations/${action}`,
        {
          method: "POST",
          credentials: "include",
          cache: "no-store",
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          },
        },
      );
      await readJson(response);

      setTeamInvitations((current) => {
        const next = current.filter(
          (invitation) => invitation.id !== organizationId,
        );
        writeTeamInvitationsCache(user?.id, next);
        return next;
      });
      setInvitationMessage(
        action === "accept" ? invitationToast.accepted : invitationToast.denied,
      );
      await reloadAccount?.();
    } catch (error) {
      setInvitationMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setInvitationBusy("");
    }
  }

  useEffect(() => {
    invitationLoadStartedRef.current = false;

    if (!authChecked || !isSignedIn) {
      setTeamInvitations([]);
      return undefined;
    }

    const cachedInvitations = readTeamInvitationsCache(user?.id);
    if (cachedInvitations) {
      setTeamInvitations(cachedInvitations);
    }

    const timeoutId = window.setTimeout(() => {
      if (invitationLoadStartedRef.current) return;
      invitationLoadStartedRef.current = true;
      void loadTeamInvitations({ preferCache: false });
    }, 900);

    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    authChecked,
    isSignedIn,
    user?.id,
    user?.email,
    entitlement?.organization_id,
  ]);

  useEffect(() => {
    if (authChecked && isSignedIn && hasTeamAccess) {
      router.prefetch?.("/team");
    }
  }, [authChecked, hasTeamAccess, isSignedIn, router]);

  useEffect(() => {
    if (!authChecked || !isSignedIn) {
      return undefined;
    }

    const handleRealtimeInvitation = (customEvent) => {
      const event = customEvent.detail;

      if (
        event?.type === "organization.invitation.created" &&
        event.invitation
      ) {
        upsertTeamInvitation(event.invitation);
        return;
      }

      if (
        event?.type === "organization.invitation.cancelled" &&
        event.organization_id
      ) {
        setTeamInvitations((current) => {
          const next = current.filter(
            (invitation) => invitation.id !== event.organization_id,
          );
          writeTeamInvitationsCache(user?.id, next);
          return next;
        });
      }
    };

    window.addEventListener(
      "team-invitation-realtime-event",
      handleRealtimeInvitation,
    );

    return () => {
      window.removeEventListener(
        "team-invitation-realtime-event",
        handleRealtimeInvitation,
      );
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authChecked, isSignedIn]);

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

    if (!authChecked) {
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
    <div
      className={sidebarOpen ? "space-y-3" : "flex flex-col items-center gap-3"}
    >
      <nav
        aria-label={t.aiFeaturesTitle}
        className={
          sidebarOpen ? "space-y-0.5" : "flex flex-col items-center gap-1"
        }
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
          const requiresSignIn = action.requiresAuth && !isSignedIn;
          const isActive = isSidebarRouteActive(pathname, action.route);

          return (
            <button
              key={`${language}-sidebar-${action.route}`}
              type="button"
              onClick={() => handleSidebarActionClick(action)}
              aria-disabled={requiresSignIn}
              aria-current={isActive ? "page" : undefined}
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
                  : isActive
                    ? sidebarActiveClass
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
        className={
          sidebarOpen ? "space-y-0.5" : "flex flex-col items-center gap-1"
        }
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
          const isActive =
            !isDisabled && isSidebarRouteActive(pathname, action.route);

          return (
            <button
              key={`${language}-manage-${action.key}`}
              type="button"
              disabled={isDisabled}
              aria-disabled={isDisabled ? "true" : undefined}
              aria-current={isActive ? "page" : undefined}
              onClick={
                isDisabled ? undefined : () => handleManageActionClick(action)
              }
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
                  : isActive
                    ? sidebarActiveClass
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
                      action.key === "billing"
                        ? "text-[13px] tracking-[-0.01em]"
                        : ""
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
    <main className="app-shell min-h-screen bg-[var(--app-bg)] text-[var(--app-text)]">
      <TeamAccessModal
        message={teamAccessMessage}
        onClose={() => setTeamAccessMessage("")}
        signInLabel={!isSignedIn ? t.signIn : null}
        closeLabel={teamAccessModal.close}
        signInHref={buildAuthSignInUrl(language)}
      />
      <TeamInvitationToast
        invitation={teamInvitations[0]}
        busy={invitationBusy}
        message={invitationMessage}
        copy={invitationToast}
        onAccept={(organizationId) =>
          respondToInvitation(organizationId, "accept")
        }
        onDeny={(organizationId) => respondToInvitation(organizationId, "deny")}
      />

      <aside
        onClickCapture={handleSidebarClickCapture}
        onPointerDown={beginSidebarSwipe}
        onPointerMove={continueSidebarSwipe}
        onPointerUp={endSidebarSwipe}
        onPointerCancel={endSidebarSwipe}
        onLostPointerCapture={endSidebarSwipe}
        style={{ touchAction: "pan-y pinch-zoom" }}
        className={`fixed left-0 top-0 z-50 flex h-dvh flex-col border-r app-surface backdrop-blur-xl ${
          sidebarHydrated
            ? "overflow-visible transition-all duration-300"
            : "overflow-hidden lg:overflow-visible opacity-0 lg:opacity-100"
        } ${
          sidebarLayoutResolved
            ? sidebarOpen
              ? "w-72"
              : "w-16"
            : "w-16 lg:w-72"
        }`}
      >
        <div
          className={`flex min-h-12 items-center border-b border-[var(--app-border)] ${
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
            onClick={() =>
              setSidebarOpen((current) => !current, { persist: true })
            }
            aria-label={sidebarOpen
                      ? getPageRuntimeCopy("home", language).closeSidebar
                      : getPageRuntimeCopy("home", language).openSidebar}
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
            <div className="border-t border-[var(--app-border)] pt-1.5">
              {isSignedIn ? (
                <ProfileMenu
                  user={user}
                  settingsLabel={t.settings}
                  logoutLabel={t.logout}
                  logoutConfirmTitle={t.logoutConfirm?.title}
                  logoutConfirmYesLabel={t.logoutConfirm?.yes}
                  logoutReturnDashboardLabel={t.logoutConfirm?.returnDashboard}
                  appearanceLabel={t.appearance}
                  languageLabel={t.languageLabel}
                  englishLabel={t.english}
                  frenchLabel={t.french}
                  helpLabel={t.help?.label}
                  privacyPolicyLabel={t.help?.privacyPolicy}
                  termsOfUseLabel={t.help?.termsOfUse}
                  lightLabel={t.light}
                  darkLabel={t.dark}
                  systemLabel={t.systemDefault}
                  backLabel={t.back}
                  changePasswordLabel={t.changePassword?.label}
                  changePasswordSendingLabel={t.changePassword?.sending}
                  changePasswordSuccessLabel={t.changePassword?.success}
                  changePasswordErrorLabel={t.changePassword?.error}
                  deleteAccountLabel={t.deleteAccount?.label}
                  deleteAccountConfirmTitle={t.deleteAccount?.title}
                  deleteAccountConfirmDescription={t.deleteAccount?.description}
                  deleteAccountConfirmButtonLabel={t.deleteAccount?.confirm}
                  deleteAccountCancelLabel={t.deleteAccount?.cancel}
                  deleteAccountDeletingLabel={t.deleteAccount?.deleting}
                  deleteAccountErrorLabel={t.deleteAccount?.error}
                  menuPlacement="top"
                  menuAlign="left"
                  fullWidth
                />
              ) : (
                <div className="home-sidebar-auth rounded-2xl border app-surface-strong p-2.5 backdrop-blur">
                  {!authChecked ? (
                    <div className="text-sm app-text-soft">{t.loading}</div>
                  ) : (
                    <div className="grid grid-cols-2 gap-2">
                      <a
                        href={buildAuthSignInUrl(language)}
                        className="rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-center text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.02] hover:shadow-xl"
                      >
                        {t.signIn}
                      </a>

                      <a
                        href={buildAuthSignUpUrl(language)}
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
              onClick={() => setSidebarOpen(true, { persist: true })}
              aria-label={getPageRuntimeCopy("home", language).openAccountMenu}
              className={`flex h-10 w-10 items-center justify-center rounded-xl border app-surface-strong text-[11px] font-semibold app-text transition ${sidebarInteractiveClass}`}
            >
              {avatarText}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setSidebarOpen(true, { persist: true })}
              aria-label={getPageRuntimeCopy("home", language).openSidebar}
              className={`inline-flex h-10 w-10 items-center justify-center rounded-xl app-text-muted transition ${sidebarInteractiveClass}`}
            >
              <Menu className="h-5 w-5" />
            </button>
          )}
        </div>
      </aside>

      <div
        className={`relative isolate min-h-screen overflow-visible ${
          sidebarHydrated ? "transition-[padding] duration-300" : ""
        } ${
          sidebarLayoutResolved
            ? sidebarOpen
              ? "pl-72"
              : "pl-16"
            : "pl-16 lg:pl-72"
        }`}
      >
        <div className="absolute inset-0 app-hero-overlay" />

        {!isSignedIn ? (
          <div className="absolute right-6 top-4 z-20 flex items-center gap-3 md:right-8">
            <AuthControls
              user={user}
              authChecked={authChecked}
              signInLabel={t.signIn}
              signUpLabel={t.signUp}
              loadingLabel={t.loading}
              logoutLabel={t.logout}
              logoutConfirmTitle={t.logoutConfirm?.title}
              logoutConfirmYesLabel={t.logoutConfirm?.yes}
              logoutReturnDashboardLabel={t.logoutConfirm?.returnDashboard}
              settingsLabel={t.settings}
              appearanceLabel={t.appearance}
              languageLabel={t.languageLabel}
              englishLabel={t.english}
              frenchLabel={t.french}
              helpLabel={t.help?.label}
              privacyPolicyLabel={t.help?.privacyPolicy}
              termsOfUseLabel={t.help?.termsOfUse}
              lightLabel={t.light}
              darkLabel={t.dark}
              systemLabel={t.systemDefault}
              backLabel={t.back}
              changePasswordLabel={t.changePassword?.label}
              changePasswordSendingLabel={t.changePassword?.sending}
              changePasswordSuccessLabel={t.changePassword?.success}
              changePasswordErrorLabel={t.changePassword?.error}
              deleteAccountLabel={t.deleteAccount?.label}
              deleteAccountConfirmTitle={t.deleteAccount?.title}
              deleteAccountConfirmDescription={t.deleteAccount?.description}
              deleteAccountConfirmButtonLabel={t.deleteAccount?.confirm}
              deleteAccountCancelLabel={t.deleteAccount?.cancel}
              deleteAccountDeletingLabel={t.deleteAccount?.deleting}
              deleteAccountErrorLabel={t.deleteAccount?.error}
              language={language}
            />
          </div>
        ) : null}

        <div className="relative mx-auto max-w-7xl px-6 pb-12 pt-28 md:px-8 md:pb-16 md:pt-32">
          <section className="space-y-8">
            <h1 className="mx-auto max-w-3xl text-center text-3xl font-semibold tracking-tight app-text sm:text-4xl">
              {t.dashboardGreeting}
            </h1>

            <div className="grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
              {dashboardActions.map((action) => {
                const requiresSignIn = action.requiresAuth && !isSignedIn;
                const requiresUpgrade = action.requiresPaid && !hasPaidAccess;
                const isUnavailable = action.comingSoon || !action.route;
                const isLocked =
                  requiresSignIn || requiresUpgrade || isUnavailable;

                return (
                  <ActionCard
                    key={`${language}-${action.key}`}
                    action={action}
                    locked={isLocked}
                    lockedLabel={requiresUpgrade ? t.upgradeToUse : undefined}
                    onClick={
                      isLocked ? undefined : () => router.push(action.route)
                    }
                  />
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}