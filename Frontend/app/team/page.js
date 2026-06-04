"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  RefreshCw,
  Send,
  Trash2,
  Video,
  XCircle,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { getAccessToken } from "@/lib/api_client";
import { teamPageTranslations } from "@/lib/translations";



function titleCase(value) {
  if (!value) return "—";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function getOrganizationName(selectedOrganization, details, entitlement) {
  return (
    selectedOrganization?.name ||
    details?.organization?.name ||
    details?.name ||
    entitlement?.organization_name ||
    "—"
  );
}

function getMemberName(member) {
  const explicitName =
    member?.name ||
    member?.full_name ||
    member?.fullName ||
    member?.display_name ||
    member?.displayName ||
    member?.profile?.name ||
    member?.user?.name;

  if (explicitName) {
    return explicitName;
  }

  const email = getMemberEmail(member);

  if (email && email.includes("@")) {
    return email.split("@")[0];
  }

  return "Team member";
}

function getMemberEmail(member) {
  return (
    member?.email ||
    member?.member_email ||
    member?.profile?.email ||
    member?.user?.email ||
    "No email available"
  );
}

async function readJson(response) {
  const data = await response.json().catch(() => null);

  if (!response.ok) {
    throw new Error(
      data?.detail?.message ||
        data?.detail?.error ||
        data?.error?.message ||
        "Request failed",
    );
  }

  return data;
}

const communicationCopy = {
  en: {
    backToDashboard: "Back to dashboard",
    messagesCalls: "Messages & Calls",
    messagesCallsDescription:
      "Open your team communication workspace for direct messages, group chats, and calls.",
    openWorkspace: "Open workspace",
  },
  fr: {
    backToDashboard: "Retour au tableau de bord",
    messagesCalls: "Messages & appels",
    messagesCallsDescription:
      "Ouvrez l’espace de communication de votre équipe pour les messages directs, les groupes et les appels.",
    openWorkspace: "Ouvrir l’espace",
  },
};

const TEAM_PAGE_CACHE_TTL_MS = 90_000;

function getTeamPageCacheKey(userId) {
  return userId ? `redocx:team-page:v1:${userId}` : "";
}

function readTeamPageCache(userId) {
  if (typeof window === "undefined") return null;

  const cacheKey = getTeamPageCacheKey(userId);
  if (!cacheKey) return null;

  try {
    const cached = JSON.parse(window.sessionStorage.getItem(cacheKey) || "null");
    if (!cached || Date.now() - Number(cached.cachedAt || 0) > TEAM_PAGE_CACHE_TTL_MS) {
      return null;
    }
    return cached;
  } catch {
    return null;
  }
}

function writeTeamPageCache(userId, value) {
  if (typeof window === "undefined") return;

  const cacheKey = getTeamPageCacheKey(userId);
  if (!cacheKey) return;

  try {
    window.sessionStorage.setItem(
      cacheKey,
      JSON.stringify({
        ...value,
        cachedAt: Date.now(),
      }),
    );
  } catch {
    // Session cache is best-effort only.
  }
}

export default function TeamPage() {
  const router = useRouter();
  const { language } = useLanguage();
  const {
    user,
    entitlement,
    reloadAccount,
    authChecked,
    loading: accountLoading,
  } = useAccount();
  const baseT = teamPageTranslations[language] || teamPageTranslations.en;
  const t = {
    ...baseT,
    subtitle:
      language === "fr"
        ? "Gérez votre organisation, vos membres et vos invitations."
        : "Manage your organization, members and invitations.",
  };
  const communicationT = communicationCopy[language] || communicationCopy.en;

  const [loading, setLoading] = useState(true);
  const [organizations, setOrganizations] = useState([]);
  const [userInvitations, setUserInvitations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [details, setDetails] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const selectedOrganization = useMemo(
    () => organizations.find((org) => org.id === selectedId) || organizations[0] || null,
    [organizations, selectedId],
  );

  const organizationName = useMemo(
    () => getOrganizationName(selectedOrganization, details, entitlement),
    [selectedOrganization, details, entitlement],
  );

  const members = useMemo(
    () => (details?.members || []).filter((member) => member.status === "active"),
    [details],
  );

  const pendingMemberInvitations = useMemo(
    () => (details?.members || []).filter((member) => member.status === "invited"),
    [details],
  );

  const currentUserId = user?.id;
  const currentRole = selectedOrganization?.member?.role;
  const isOwner = currentRole === "owner";
  const isAdmin = currentRole === "admin";
  const ownerUserId =
    selectedOrganization?.owner_user_id ||
    details?.organization?.owner_user_id ||
    details?.owner_user_id ||
    null;
  const canInviteManage = isOwner || isAdmin;
  const canUpdateRoles = isOwner;
  const canRemoveMembers = isOwner;
  const canLeavePlan = ["admin", "member"].includes(currentRole);
  const seatsUsed = subscription?.active_members ?? members.length;
  const maxSeats = subscription?.max_accounts ?? null;
  const hasSeatLimit = typeof maxSeats === "number";
  const seatsAreFull = hasSeatLimit && seatsUsed >= maxSeats;
  const canInvite = canInviteManage && !seatsAreFull;

  function hydrateFromCache() {
    const cached = readTeamPageCache(user?.id);
    if (!cached) return false;

    setOrganizations(Array.isArray(cached.organizations) ? cached.organizations : []);
    setUserInvitations(
      Array.isArray(cached.userInvitations) ? cached.userInvitations : [],
    );
    setSelectedId(cached.selectedId || null);
    setDetails(cached.details || null);
    setSubscription(cached.subscription || null);
    setLoading(false);
    return true;
  }

  function isPlanOwnerMember(member) {
    return Boolean(ownerUserId && member?.user_id === ownerUserId);
  }

  function canChangeMemberRole(member) {
    return (
      canUpdateRoles &&
      member?.status === "active" &&
      !isPlanOwnerMember(member)
    );
  }

  function canRemoveMember(member) {
    return (
      canRemoveMembers &&
      member?.status === "active" &&
      !isPlanOwnerMember(member)
    );
  }

  function canCancelInvitation(member) {
    if (member?.status !== "invited") {
      return false;
    }

    if (isOwner) {
      return true;
    }

    if (!isAdmin || !ownerUserId || !member?.invited_by_user_id) {
      return false;
    }

    return member.invited_by_user_id !== ownerUserId;
  }

  async function api(path, options = {}) {
    const token = await getAccessToken();

    const response = await fetch(path, {
      ...options,
      credentials: "include",
      cache: "no-store",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.headers || {}),
      },
    });

    return readJson(response);
  }

  async function loadOrganization(organizationId) {
    const [organizationDetails, subscriptionData] = await Promise.all([
      api(`/api/organizations/${organizationId}`),
      api(`/api/organizations/${organizationId}/subscription`),
    ]);

    const nextSubscription = subscriptionData.subscription;
    setDetails(organizationDetails);
    setSubscription(nextSubscription);

    writeTeamPageCache(user?.id, {
      organizations,
      userInvitations,
      selectedId: organizationId,
      details: organizationDetails,
      subscription: nextSubscription,
    });
  }

  async function load({ force = false } = {}) {
    const hydrated = !force && hydrateFromCache();
    setLoading(!hydrated);
    setMessage("");

    try {
      const data = await api("/api/organizations/me");
      const orgs = data.organizations || [];
      const invitations = data.invitations || [];

      setOrganizations(orgs);
      setUserInvitations(invitations);

      const next =
        orgs.find((org) => org.id === entitlement?.organization_id) ||
        orgs[0] ||
        null;

      setSelectedId(next?.id || null);

      if (next?.id) {
        const [organizationDetails, subscriptionData] = await Promise.all([
          api(`/api/organizations/${next.id}`),
          api(`/api/organizations/${next.id}/subscription`),
        ]);
        const nextSubscription = subscriptionData.subscription;

        setDetails(organizationDetails);
        setSubscription(nextSubscription);
        writeTeamPageCache(user?.id, {
          organizations: orgs,
          userInvitations: invitations,
          selectedId: next.id,
          details: organizationDetails,
          subscription: nextSubscription,
        });
      } else {
        setDetails(null);
        setSubscription(null);
        writeTeamPageCache(user?.id, {
          organizations: orgs,
          userInvitations: invitations,
          selectedId: null,
          details: null,
          subscription: null,
        });
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function changeOrganization(event) {
    const organizationId = Number(event.target.value);
    setSelectedId(organizationId);
    setLoading(true);

    try {
      await loadOrganization(organizationId);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function acceptInvitation(organizationId) {
    setBusy(`accept:${organizationId}`);
    setMessage("");

    try {
      await api(`/api/organizations/${organizationId}/invitations/accept`, {
        method: "POST",
      });

      setMessage(t.acceptedInvitation);
      await reloadAccount();
      await load();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }



  async function denyInvitation(organizationId) {
    setBusy(`deny:${organizationId}`);
    setMessage("");

    try {
      await api(`/api/organizations/${organizationId}/invitations/deny`, {
        method: "POST",
      });

      setMessage(t.deniedInvitation);
      await reloadAccount?.();
      await load();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  async function inviteMember(event) {
    event.preventDefault();

    if (!selectedOrganization?.id || !email.trim()) {
      return;
    }

    if (seatsAreFull) {
      setMessage(t.upgradeRequiredDescription);
      return;
    }

    setBusy("invite");
    setMessage("");

    try {
      await api(`/api/organizations/${selectedOrganization.id}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), role }),
      });

      setEmail("");
      setRole("member");
      await loadOrganization(selectedOrganization.id);
      await reloadAccount();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  async function cancelInvitation(member) {
    if (!selectedOrganization?.id || member?.status !== "invited") {
      return;
    }

    setBusy(`cancel-invite:${member.user_id}`);
    setMessage("");

    try {
      await api(
        `/api/organizations/${selectedOrganization.id}/members/${encodeURIComponent(member.user_id)}`,
        { method: "DELETE" },
      );

      setMessage(t.invitationCancelled);
      await loadOrganization(selectedOrganization.id);
      await reloadAccount();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  async function updateRole(member, nextRole) {
    if (!selectedOrganization?.id) {
      return;
    }

    setBusy(`role:${member.user_id}`);
    setMessage("");

    try {
      await api(
        `/api/organizations/${selectedOrganization.id}/members/${encodeURIComponent(member.user_id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role: nextRole }),
        },
      );

      await loadOrganization(selectedOrganization.id);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  async function removeMember(member) {
    if (!selectedOrganization?.id) {
      return;
    }

    setBusy(`remove:${member.user_id}`);
    setMessage("");

    try {
      await api(
        `/api/organizations/${selectedOrganization.id}/members/${encodeURIComponent(member.user_id)}`,
        { method: "DELETE" },
      );

      await loadOrganization(selectedOrganization.id);
      await reloadAccount();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  async function leavePlan() {
    if (!selectedOrganization?.id || !canLeavePlan) {
      return;
    }

    setBusy("leave-plan");
    setMessage("");

    try {
      await api(`/api/organizations/${selectedOrganization.id}/leave`, {
        method: "POST",
      });

      setMessage(t.leftPlan);
      await reloadAccount();
      await load();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    if (accountLoading || !authChecked) {
      return;
    }

    if (!user) {
      setLoading(false);
      setOrganizations([]);
      setUserInvitations([]);
      setSelectedId(null);
      setDetails(null);
      setSubscription(null);
      return;
    }

    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountLoading, authChecked, user?.id, entitlement?.organization_id]);

  useEffect(() => {
    if (!isOwner && role !== "member") {
      setRole("member");
    }
  }, [isOwner, role]);

  useEffect(() => {
    if (selectedOrganization?.id) {
      router.prefetch?.("/team/messages");
    }
  }, [router, selectedOrganization?.id]);

  if ((accountLoading || loading) && !selectedOrganization && !userInvitations.length) {
    return (
      <main className="h-dvh overflow-hidden app-page px-4 py-4 md:px-6">
        <div className="mx-auto flex h-full max-w-7xl items-center justify-center">
          <div className="w-full max-w-md rounded-3xl border app-surface-strong p-6 text-center app-text shadow-xl">
            {t.loading}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="h-dvh overflow-hidden app-page px-4 py-3 md:px-6 md:py-4">
      <div className="mx-auto flex h-full max-w-7xl flex-col gap-3 overflow-hidden">
        <header className="shrink-0 rounded-3xl border app-surface-strong px-4 py-3 shadow-sm">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <button
                type="button"
                onClick={() => router.push("/")}
                className="mb-2 inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-1.5 text-xs font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                {communicationT.backToDashboard}
              </button>
              <h1 className="truncate text-2xl font-semibold tracking-tight app-text md:text-3xl">
                {t.title}
              </h1>
              <p className="mt-1 max-w-3xl truncate text-sm app-text-muted">
                {t.subtitle}
              </p>
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {organizations.length > 1 ? (
                <select
                  value={selectedOrganization?.id || ""}
                  onChange={changeOrganization}
                  className="min-w-48 rounded-xl border px-3 py-2 text-sm"
                >
                  {organizations.map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
                </select>
              ) : null}

              <button
                type="button"
                onClick={() => load({ force: true })}
                className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
              >
                <RefreshCw className="h-4 w-4" />
                {t.refresh}
              </button>
            </div>
          </div>
        </header>

        {message ? (
          <div className="shrink-0 rounded-2xl border border-[var(--app-border)] app-surface-strong px-4 py-2 text-sm app-text">
            {message}
          </div>
        ) : null}

        {userInvitations.length ? (
          <section className="shrink-0 rounded-2xl border app-surface-strong p-3">
            <div className="mb-2 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold app-text">
                  {t.invitationsForYou}
                </h2>
                <p className="truncate text-xs app-text-muted">
                  {t.invitationsForYouDescription}
                </p>
              </div>
            </div>

            <div className="max-h-28 overflow-y-auto pr-1">
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {userInvitations.map((invitation) => (
                  <div
                    key={invitation.id}
                    className="rounded-2xl border app-surface p-3"
                  >
                    <div className="truncate text-sm font-semibold app-text">
                      {invitation.name}
                    </div>
                    <div className="mt-0.5 truncate text-xs app-text-soft">
                      {titleCase(invitation.member?.role)} ·{" "}
                      {titleCase(invitation.subscription?.plan)}
                    </div>

                    <div className="mt-3 grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={() => denyInvitation(invitation.id)}
                        disabled={Boolean(busy)}
                        className="inline-flex items-center justify-center gap-1.5 rounded-xl border app-surface px-3 py-2 text-xs font-semibold app-text transition hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-50 dark:hover:bg-[#2d2d33]"
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        {busy === `deny:${invitation.id}`
                          ? t.denying
                          : t.denyInvitation}
                      </button>

                      <button
                        type="button"
                        onClick={() => acceptInvitation(invitation.id)}
                        disabled={Boolean(busy)}
                        className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-xs font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        {busy === `accept:${invitation.id}`
                          ? t.accepting
                          : t.acceptInvitation}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </section>
        ) : null}

        {!selectedOrganization ? (
          <section className="min-h-0 flex-1 rounded-3xl border app-surface-strong p-6">
            <div className="flex h-full items-center justify-center rounded-2xl border app-surface p-6 text-center">
              <h2 className="text-xl font-semibold app-text">{t.noTeam}</h2>
            </div>
          </section>
        ) : (
          <>
            <section className="shrink-0 grid gap-3 md:grid-cols-4">
              <div className="rounded-2xl border app-surface-strong p-3">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] app-text-soft">
                  {t.organization}
                </div>
                <div className="mt-1 truncate text-base font-semibold app-text">
                  {organizationName}
                </div>
              </div>
              <div className="rounded-2xl border app-surface-strong p-3">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] app-text-soft">
                  {t.plan}
                </div>
                <div className="mt-1 text-base font-semibold app-text">
                  {titleCase(subscription?.plan)}
                </div>
              </div>
              <div className="rounded-2xl border app-surface-strong p-3">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] app-text-soft">
                  {t.seats}
                </div>
                <div className="mt-1 flex items-center gap-2 text-base font-semibold app-text">
                  <span>{seatsUsed} / {maxSeats ?? "—"}</span>
                  {seatsAreFull ? (
                    <span className="rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[10px] font-semibold text-amber-200">
                      {t.upgradeRequired}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="rounded-2xl border app-surface-strong p-3">
                <div className="text-[11px] font-semibold uppercase tracking-[0.12em] app-text-soft">
                  {t.role}
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <div className="truncate text-base font-semibold app-text">
                    {titleCase(selectedOrganization.member?.role)}
                  </div>
                  {canLeavePlan ? (
                    <button
                      type="button"
                      onClick={leavePlan}
                      disabled={busy === "leave-plan"}
                      className="shrink-0 rounded-xl border border-red-400/30 px-3 py-1.5 text-xs font-semibold text-red-200 transition hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {busy === "leave-plan" ? t.leavingPlan : t.leavePlan}
                    </button>
                  ) : null}
                </div>
              </div>
            </section>

            <section className="min-h-0 flex-1 grid gap-4 overflow-hidden lg:grid-cols-[minmax(0,1fr)_360px]">
              <div className="min-h-0 flex flex-col gap-4 overflow-hidden">
                <section className="shrink-0 rounded-2xl border app-surface-strong p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="flex min-w-0 items-start gap-3">
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border app-surface">
                        <Video className="h-4 w-4 app-text-muted" />
                      </div>

                      <div className="min-w-0">
                        <h2 className="truncate text-base font-semibold app-text">
                          {communicationT.messagesCalls}
                        </h2>
                        <p className="mt-1 max-w-2xl truncate text-xs app-text-muted">
                          {communicationT.messagesCallsDescription}
                        </p>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={() => router.push("/team/messages")}
                      className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                    >
                      {communicationT.openWorkspace}
                      <ArrowRight className="h-4 w-4" />
                    </button>
                  </div>
                </section>

                <section className="min-h-0 flex-1 rounded-3xl border app-surface-strong p-4">
                  <div className="flex h-full min-h-0 flex-col">
                    <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
                      <h2 className="text-lg font-semibold app-text">
                        {t.members}
                      </h2>
                      <span className="rounded-full border app-surface px-2.5 py-1 text-xs font-semibold app-text-soft">
                        {members.length}
                      </span>
                    </div>

                    <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
                      {members.length ? (
                        members.map((member) => (
                          <div
                            key={member.user_id}
                            className="flex flex-col gap-3 rounded-2xl border app-surface p-3 md:flex-row md:items-center md:justify-between"
                          >
                            <div className="min-w-0">
                              <div className="truncate text-sm font-semibold app-text">
                                {getMemberName(member)}
                              </div>
                              <div className="mt-0.5 truncate text-xs app-text-muted">
                                {getMemberEmail(member)}
                              </div>
                              <div className="mt-1 text-xs app-text-soft">
                                {titleCase(member.role)} ·{" "}
                                {titleCase(member.status)}
                              </div>
                            </div>

                            {canChangeMemberRole(member) ||
                            canRemoveMember(member) ? (
                              <div className="flex shrink-0 flex-wrap gap-2">
                                {canChangeMemberRole(member) ? (
                                  <select
                                    value={member.role}
                                    disabled={busy === `role:${member.user_id}`}
                                    onChange={(event) =>
                                      updateRole(member, event.target.value)
                                    }
                                    className="rounded-xl border px-3 py-2 text-xs"
                                  >
                                    <option value="member">{t.member}</option>
                                    <option value="admin">{t.admin}</option>
                                  </select>
                                ) : null}

                                {canRemoveMember(member) ? (
                                  <button
                                    type="button"
                                    disabled={busy === `remove:${member.user_id}`}
                                    onClick={() => removeMember(member)}
                                    className="inline-flex items-center gap-2 rounded-xl border border-red-400/30 px-3 py-2 text-xs font-semibold text-red-200 transition hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                    {t.remove}
                                  </button>
                                ) : null}
                              </div>
                            ) : null}
                          </div>
                        ))
                      ) : (
                        <p className="rounded-2xl border app-surface p-4 text-sm app-text-muted">
                          {t.none}
                        </p>
                      )}
                    </div>
                  </div>
                </section>
              </div>

              <aside className="min-h-0 overflow-hidden">
                <div className="flex h-full min-h-0 flex-col gap-4">
                  <form
                    onSubmit={inviteMember}
                    className="shrink-0 rounded-3xl border app-surface-strong p-4"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <h2 className="text-lg font-semibold app-text">{t.invite}</h2>
                    </div>
                    {seatsAreFull ? (
                      <div className="mt-3 rounded-2xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-200">
                        <div className="font-semibold">{t.seatLimitReached}</div>
                        <div className="mt-0.5 text-xs">
                          {t.upgradeRequiredDescription}
                        </div>
                      </div>
                    ) : null}
                    <div className="mt-3 space-y-2">
                      <input
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        placeholder={t.email}
                        disabled={!canInvite || busy === "invite"}
                        className="w-full rounded-xl border px-3 py-2 text-sm"
                      />
                      <select
                        value={role}
                        onChange={(event) => setRole(event.target.value)}
                        disabled={!canInvite || busy === "invite"}
                        className="w-full rounded-xl border px-3 py-2 text-sm"
                      >
                        <option value="member">{t.member}</option>
                        {isOwner ? (
                          <option value="admin">{t.admin}</option>
                        ) : null}
                      </select>
                      <button
                        type="submit"
                        disabled={
                          !canInvite || !email.trim() || busy === "invite"
                        }
                        className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Send className="h-4 w-4" />
                        {seatsAreFull ? t.upgradeRequired : t.send}
                      </button>
                    </div>
                  </form>

                  <section className="min-h-0 flex-1 rounded-3xl border app-surface-strong p-4">
                    <div className="flex h-full min-h-0 flex-col">
                      <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
                        <h2 className="text-lg font-semibold app-text">
                          {t.invitations}
                        </h2>
                        <span className="rounded-full border app-surface px-2.5 py-1 text-xs font-semibold app-text-soft">
                          {pendingMemberInvitations.length}
                        </span>
                      </div>

                      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
                        {pendingMemberInvitations.length ? (
                          pendingMemberInvitations.map((member) => (
                            <div
                              key={member.user_id}
                              className="rounded-2xl border app-surface p-3"
                            >
                              <div className="flex flex-col gap-3">
                                <div className="min-w-0">
                                  <div className="truncate text-sm font-semibold app-text">
                                    {getMemberName(member)}
                                  </div>
                                  <div className="mt-0.5 truncate text-xs app-text-muted">
                                    {getMemberEmail(member)}
                                  </div>
                                  <div className="mt-1 text-xs app-text-soft">
                                    {titleCase(member.role)} ·{" "}
                                    {titleCase(member.status)}
                                  </div>
                                </div>

                                {canCancelInvitation(member) ? (
                                  <button
                                    type="button"
                                    onClick={() => cancelInvitation(member)}
                                    disabled={
                                      busy === `cancel-invite:${member.user_id}`
                                    }
                                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-red-400/30 px-3 py-2 text-xs font-semibold text-red-200 transition hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                    {busy === `cancel-invite:${member.user_id}`
                                      ? t.cancellingInvitation
                                      : t.cancelInvitation}
                                  </button>
                                ) : null}
                              </div>
                            </div>
                          ))
                        ) : (
                          <p className="rounded-2xl border app-surface p-4 text-sm app-text-muted">
                            {t.none}
                          </p>
                        )}
                      </div>
                    </div>
                  </section>
                </div>
              </aside>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
