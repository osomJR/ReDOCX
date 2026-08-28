"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle2,
  RefreshCw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";
import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { getAccessToken } from "@/lib/api_client";
import {
  teamPageTranslations,
  teamSettingsSupplementalTranslations,
  getPageRuntimeCopy,
  resolveErrorMessage,
} from "@/lib/translations";

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

function getMemberName(member, language = "en") {
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

  const email = getMemberEmail(member, language);

  if (email && email.includes("@")) {
    return email.split("@")[0];
  }

  return getPageRuntimeCopy("teamSettings", language).teamMember;
}

function getMemberEmail(member, language = "en") {
  return (
    member?.email ||
    member?.member_email ||
    member?.profile?.email ||
    member?.user?.email ||
    getPageRuntimeCopy("teamSettings", language).noEmail
  );
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
const settingsCopy = teamSettingsSupplementalTranslations;

const TEAM_PAGE_CACHE_TTL_MS = 90_000;

function getTeamPageCacheKey(userId) {
  return userId ? `redocx:team-page:v1:${userId}` : "";
}

function readTeamPageCache(userId) {
  if (typeof window === "undefined") return null;

  const cacheKey = getTeamPageCacheKey(userId);
  if (!cacheKey) return null;

  try {
    const cached = JSON.parse(
      window.sessionStorage.getItem(cacheKey) || "null",
    );
    if (
      !cached ||
      Date.now() - Number(cached.cachedAt || 0) > TEAM_PAGE_CACHE_TTL_MS
    ) {
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

export default function TeamSettingsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { language } = useLanguage();
  const {
    user,
    entitlement,
    reloadAccount,
    beginAccountExit,
    authChecked,
    loading: accountLoading,
  } = useAccount();
  const baseT = teamPageTranslations[language] || teamPageTranslations.en;
  const t = baseT;
  const settingsT = settingsCopy[language] || settingsCopy.en;

  const [loading, setLoading] = useState(true);
  const [organizations, setOrganizations] = useState([]);
  const [userInvitations, setUserInvitations] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [details, setDetails] = useState(null);
  const [subscription, setSubscription] = useState(null);
  const [billingHandoff, setBillingHandoff] = useState(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [transferOwnerUserId, setTransferOwnerUserId] = useState("");
  const [editingOrganizationName, setEditingOrganizationName] = useState(false);
  const [organizationNameDraft, setOrganizationNameDraft] = useState("");
  const handoffCallbackHandledRef = useRef(false);

  const selectedOrganization = useMemo(
    () =>
      organizations.find((org) => org.id === selectedId) ||
      organizations[0] ||
      null,
    [organizations, selectedId],
  );

  const organizationName = useMemo(
    () => getOrganizationName(selectedOrganization, details, entitlement),
    [selectedOrganization, details, entitlement],
  );

  const members = useMemo(
    () =>
      (details?.members || []).filter((member) => member.status === "active"),
    [details],
  );

  const pendingMemberInvitations = useMemo(
    () =>
      (details?.members || []).filter((member) => member.status === "invited"),
    [details],
  );

  const currentUserId = user?.id;
  const hasTeamAccess =
    entitlement?.source === "organization" &&
    entitlement?.is_paid === true &&
    ["business", "enterprise"].includes(entitlement?.plan);
  const currentRole = selectedOrganization?.member?.role;
  const isOwner = currentRole === "owner";
  const isAdmin = currentRole === "admin";
  const ownerUserId =
    selectedOrganization?.owner_user_id ||
    details?.organization?.owner_user_id ||
    details?.owner_user_id ||
    null;
  const ownerCanExitAsSoleMember =
    isOwner && members.length === 1 && pendingMemberInvitations.length === 0;
  const canInviteManage = isOwner || isAdmin;
  const canUpdateRoles = isOwner;
  const canRemoveMembers = isOwner;
  const canLeavePlan =
    ["admin", "member"].includes(currentRole) || ownerCanExitAsSoleMember;
  const ownershipTransferCandidates = members.filter(
    (member) => member.status === "active" && member.user_id !== currentUserId,
  );
  const canTransferOwnership =
    isOwner && ownershipTransferCandidates.length > 0;
  const seatsUsed = subscription?.active_members ?? members.length;
  const maxSeats = subscription?.max_accounts ?? null;
  const hasSeatLimit = typeof maxSeats === "number";
  const seatsAreFull = hasSeatLimit && seatsUsed >= maxSeats;
  const canInvite = canInviteManage && !seatsAreFull;

  function hydrateFromCache() {
    const cached = readTeamPageCache(user?.id);
    if (!cached) return false;

    setOrganizations(
      Array.isArray(cached.organizations) ? cached.organizations : [],
    );
    setUserInvitations(
      Array.isArray(cached.userInvitations) ? cached.userInvitations : [],
    );
    setSelectedId(cached.selectedId || null);
    setDetails(cached.details || null);
    setSubscription(cached.subscription || null);
    setBillingHandoff(cached.billingHandoff || null);
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
    const nextBillingHandoff = subscriptionData.billing_handoff || null;
    setDetails(organizationDetails);
    setSubscription(nextSubscription);
    setBillingHandoff(nextBillingHandoff);

    writeTeamPageCache(user?.id, {
      organizations,
      userInvitations,
      selectedId: organizationId,
      details: organizationDetails,
      subscription: nextSubscription,
      billingHandoff: nextBillingHandoff,
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
        const nextBillingHandoff = subscriptionData.billing_handoff || null;

        setDetails(organizationDetails);
        setSubscription(nextSubscription);
        setBillingHandoff(nextBillingHandoff);
        writeTeamPageCache(user?.id, {
          organizations: orgs,
          userInvitations: invitations,
          selectedId: next.id,
          details: organizationDetails,
          subscription: nextSubscription,
          billingHandoff: nextBillingHandoff,
        });
      } else {
        setDetails(null);
        setSubscription(null);
        setBillingHandoff(null);
        writeTeamPageCache(user?.id, {
          organizations: orgs,
          userInvitations: invitations,
          selectedId: null,
          details: null,
          subscription: null,
          billingHandoff: null,
        });
      }
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setLoading(false);
    }
  }

  async function renameOrganization(event) {
    event.preventDefault();

    if (!selectedOrganization?.id || !isOwner) return;

    const normalizedName = organizationNameDraft.trim().replace(/\s+/g, " ");
    if (normalizedName.length < 2) {
      setMessage(t.organizationNameTooShort);
      return;
    }
    if (normalizedName.length > 100) {
      setMessage(t.organizationNameTooLong);
      return;
    }

    setBusy("rename-organization");
    setMessage("");

    try {
      const data = await api(`/api/organizations/${selectedOrganization.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: normalizedName }),
      });
      const updatedOrganization = data?.organization;

      if (updatedOrganization) {
        setOrganizations((current) =>
          current.map((organization) =>
            organization.id === updatedOrganization.id
              ? { ...organization, ...updatedOrganization }
              : organization,
          ),
        );
        setDetails((current) => ({
          ...current,
          organization: {
            ...(current?.organization || {}),
            ...updatedOrganization,
          },
        }));
      }

      setOrganizationNameDraft(normalizedName);
      setEditingOrganizationName(false);
      await reloadAccount?.({ forceRefresh: true });
      setMessage(t.organizationRenamed);
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setBusy("");
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setTransferOwnerUserId("");
      await loadOrganization(selectedOrganization.id);
      await reloadAccount();
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setBusy("");
    }
  }

  async function transferOwnership() {
    if (
      !selectedOrganization?.id ||
      !canTransferOwnership ||
      !transferOwnerUserId
    ) {
      return;
    }

    setBusy("transfer-ownership");
    setMessage("");

    try {
      const data = await api(
        `/api/organizations/${selectedOrganization.id}/transfer-ownership`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_owner_user_id: transferOwnerUserId }),
        },
      );

      setMessage(
        data?.billing_handoff
          ? settingsT.ownershipTransferredWithBilling
          : getPageRuntimeCopy("teamSettings", language).ownershipTransferred,
      );
      setTransferOwnerUserId("");
      await reloadAccount?.();
      await loadOrganization(selectedOrganization.id);
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setBusy("");
    }
  }

  async function authorizeBillingHandoff() {
    if (!selectedOrganization?.id || !billingHandoff) return;

    setBusy("billing-handoff-authorize");
    setMessage("");
    try {
      const data = await api(
        `/api/organizations/${selectedOrganization.id}/billing-handoff/authorize`,
        { method: "POST" },
      );
      setBillingHandoff(data?.billing_handoff || billingHandoff);
      if (data?.authorization_url) {
        window.location.assign(data.authorization_url);
        return;
      }
      setMessage(settingsT.handoffScheduled);
      await loadOrganization(selectedOrganization.id);
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
    } finally {
      setBusy("");
    }
  }

  async function confirmBillingHandoff(organizationId) {
    const normalizedOrganizationId = Number(organizationId || selectedOrganization?.id);
    if (!normalizedOrganizationId) return;

    setBusy("billing-handoff-confirm");
    setMessage("");
    try {
      const data = await api(
        `/api/organizations/${normalizedOrganizationId}/billing-handoff/confirm`,
        { method: "POST" },
      );
      setBillingHandoff(data?.billing_handoff || null);
      setMessage(settingsT.handoffScheduled);
      await reloadAccount?.();
      await loadOrganization(normalizedOrganizationId);
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      const data = await api(
        `/api/organizations/${selectedOrganization.id}/leave`,
        {
          method: "POST",
        },
      );

      setMessage(t.leftPlan);
      if (
        data?.owner_exit ||
        data?.account_lifecycle?.status === "deactivated_pending_deletion"
      ) {
        beginAccountExit?.("owner_subscription_exit");
        window.location.replace("/auth/logout");
        return;
      }
      await reloadAccount();
      await load();
    } catch (error) {
      setMessage(resolveErrorMessage(error, language, "TEAM_REQUEST_FAILED"));
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
      setBillingHandoff(null);
      return;
    }

    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accountLoading, authChecked, user?.id, entitlement?.organization_id]);

  useEffect(() => {
    if (handoffCallbackHandledRef.current || !user?.id) return;
    const callbackState = searchParams.get("billing_handoff");
    const callbackOrganizationId = Number(searchParams.get("organization_id") || 0);
    if (!callbackState || !callbackOrganizationId) return;

    handoffCallbackHandledRef.current = true;
    if (callbackState === "success") {
      void confirmBillingHandoff(callbackOrganizationId).finally(() => {
        router.replace("/team/settings");
      });
      return;
    }

    if (callbackState === "cancelled") {
      setMessage(settingsT.handoffAuthorizationCancelled);
      router.replace("/team/settings");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, user?.id]);

  useEffect(() => {
    if (!isOwner && role !== "member") {
      setRole("member");
    }
  }, [isOwner, role]);

  useEffect(() => {
    if (!editingOrganizationName) {
      setOrganizationNameDraft(
        organizationName === "—" ? "" : organizationName,
      );
    }
  }, [editingOrganizationName, organizationName]);

  useEffect(() => {
    const handleOrganizationUpdate = (event) => {
      const realtimeEvent = event.detail;
      if (
        realtimeEvent?.type !== "organization.updated" ||
        Number(realtimeEvent?.organization_id) !==
          Number(selectedOrganization?.id)
      ) {
        return;
      }

      const updatedOrganization = realtimeEvent.organization;
      if (!updatedOrganization) return;

      setOrganizations((current) =>
        current.map((organization) =>
          organization.id === updatedOrganization.id
            ? { ...organization, ...updatedOrganization }
            : organization,
        ),
      );
      setDetails((current) => ({
        ...current,
        organization: {
          ...(current?.organization || {}),
          ...updatedOrganization,
        },
      }));
    };

    window.addEventListener("team-realtime-event", handleOrganizationUpdate);
    return () => {
      window.removeEventListener(
        "team-realtime-event",
        handleOrganizationUpdate,
      );
    };
  }, [selectedOrganization?.id]);

  if (
    (accountLoading || loading) &&
    !selectedOrganization &&
    !userInvitations.length
  ) {
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

  if (!user || !hasTeamAccess) {
    return (
      <main className="flex h-dvh app-page px-4 py-4 md:px-6">
        <section className="mx-auto flex w-full max-w-3xl flex-col justify-center rounded-3xl border app-surface-strong p-6">
          <button
            type="button"
            onClick={() => router.push("/")}
            className="mb-6 inline-flex w-fit items-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text"
          >
            <ArrowLeft className="h-4 w-4" />
            {settingsT.backToDashboard}
          </button>
          <h1 className="text-3xl font-semibold app-text">
            {settingsT.unavailableTitle}
          </h1>
          <p className="mt-3 text-sm app-text-muted">
            {settingsT.unavailableDescription}
          </p>
        </section>
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
                onClick={() => router.push("/team")}
                className="mb-2 inline-flex items-center gap-2 rounded-xl border app-surface px-3 py-1.5 text-xs font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                {settingsT.backToWorkspace}
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

        {billingHandoff &&
        billingHandoff.new_owner_user_id === currentUserId ? (
          <section className="shrink-0 rounded-2xl border app-surface-strong p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold app-text">
                  {settingsT.handoffTitle}
                </h2>
                <p className="mt-1 text-xs app-text-muted">
                  {billingHandoff.status === "scheduled"
                    ? settingsT.handoffScheduledDescription
                    : settingsT.handoffDescription}
                </p>
                {billingHandoff.effective_at ? (
                  <p className="mt-1 text-xs app-text-soft">
                    {settingsT.handoffStarts}: {new Date(
                      billingHandoff.effective_at,
                    ).toLocaleString(language === "fr" ? "fr-FR" : "en-US")}
                  </p>
                ) : null}
              </div>
              {billingHandoff.status !== "scheduled" ? (
                <button
                  type="button"
                  onClick={authorizeBillingHandoff}
                  disabled={Boolean(busy)}
                  className="shrink-0 rounded-xl bg-[var(--app-button-bg)] px-4 py-2 text-xs font-semibold text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {busy === "billing-handoff-authorize" ||
                  busy === "billing-handoff-confirm"
                    ? settingsT.handoffAuthorizing
                    : settingsT.handoffAuthorize}
                </button>
              ) : (
                <span className="shrink-0 rounded-full border px-3 py-1 text-xs font-semibold app-text">
                  {settingsT.handoffReady}
                </span>
              )}
            </div>
          </section>
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
                {editingOrganizationName && isOwner ? (
                  <form
                    onSubmit={renameOrganization}
                    className="mt-2 space-y-2"
                  >
                    <input
                      type="text"
                      value={organizationNameDraft}
                      onChange={(event) =>
                        setOrganizationNameDraft(event.target.value)
                      }
                      minLength={2}
                      maxLength={100}
                      autoComplete="organization"
                      autoFocus
                      disabled={busy === "rename-organization"}
                      className="w-full rounded-xl border px-3 py-2 text-sm app-text outline-none"
                      aria-label={t.organizationName}
                    />
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="submit"
                        disabled={busy === "rename-organization"}
                        className="rounded-xl bg-[var(--app-button-bg)] px-3 py-1.5 text-xs font-semibold text-[var(--app-button-text)] disabled:opacity-50"
                      >
                        {busy === "rename-organization" ? t.saving : t.save}
                      </button>
                      <button
                        type="button"
                        disabled={busy === "rename-organization"}
                        onClick={() => {
                          setOrganizationNameDraft(organizationName);
                          setEditingOrganizationName(false);
                        }}
                        className="rounded-xl border app-surface px-3 py-1.5 text-xs font-semibold app-text disabled:opacity-50"
                      >
                        {t.cancel}
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="mt-1 flex min-w-0 items-center justify-between gap-2">
                    <div className="truncate text-base font-semibold app-text">
                      {organizationName}
                    </div>
                    {isOwner ? (
                      <button
                        type="button"
                        onClick={() => setEditingOrganizationName(true)}
                        className="shrink-0 rounded-lg border app-surface px-2 py-1 text-[11px] font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                      >
                        {t.edit}
                      </button>
                    ) : null}
                  </div>
                )}
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
                  <span>
                    {seatsUsed} / {maxSeats ?? "—"}
                  </span>
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
                  <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                    {canTransferOwnership ? (
                      <>
                        <select
                          value={transferOwnerUserId}
                          onChange={(event) =>
                            setTransferOwnerUserId(event.target.value)
                          }
                          disabled={busy === "transfer-ownership"}
                          className="max-w-[10rem] rounded-xl border px-2 py-1.5 text-xs"
                        >
                          <option value="">
                            {getPageRuntimeCopy("teamSettings", language).newOwner}
                          </option>
                          {ownershipTransferCandidates.map((member) => (
                            <option key={member.user_id} value={member.user_id}>
                              {getMemberName(member, language)}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={transferOwnership}
                          disabled={
                            busy === "transfer-ownership" ||
                            !transferOwnerUserId
                          }
                          className="rounded-xl border app-surface px-3 py-1.5 text-xs font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {busy === "transfer-ownership"
                            ? getPageRuntimeCopy("teamSettings", language).transferring
                            : getPageRuntimeCopy("teamSettings", language).transfer}
                        </button>
                      </>
                    ) : null}
                    {canLeavePlan ? (
                      <button
                        type="button"
                        onClick={leavePlan}
                        disabled={busy === "leave-plan"}
                        className="rounded-xl border border-red-400/30 px-3 py-1.5 text-xs font-semibold text-red-200 transition hover:bg-red-400/10 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {busy === "leave-plan" ? t.leavingPlan : t.leavePlan}
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            </section>

            <section className="min-h-0 flex-1 grid gap-4 overflow-hidden lg:grid-cols-[minmax(0,1fr)_360px]">
              <div className="min-h-0 flex flex-col gap-4 overflow-hidden">
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
                                {getMemberName(member, language)}
                              </div>
                              <div className="mt-0.5 truncate text-xs app-text-muted">
                                {getMemberEmail(member, language)}
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
                                    disabled={
                                      busy === `remove:${member.user_id}`
                                    }
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
                      <h2 className="text-lg font-semibold app-text">
                        {t.invite}
                      </h2>
                    </div>
                    {seatsAreFull ? (
                      <div className="mt-3 rounded-2xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-200">
                        <div className="font-semibold">
                          {t.seatLimitReached}
                        </div>
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
                                    {getMemberName(member, language)}
                                  </div>
                                  <div className="mt-0.5 truncate text-xs app-text-muted">
                                    {getMemberEmail(member, language)}
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
