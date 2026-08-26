"use client";import {
  projectsTeamWorkspaceTranslations,
  getPageRuntimeCopy,
  resolveErrorMessage,
  resolveErrorTranslationKey,
} from "@/lib/translations";


import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Bell,
  BellRing,
  CalendarClock,
  Check,
  Clipboard,
  Download,
  FileText,
  Forward,
  Image as ImageIcon,
  Music,
  Paperclip,
  Phone,
  PlayCircle,
  Plus,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Video,
  Users,
  Trash2,
  X,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";

import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import { useTeamRealtime } from "@/components/team_realtime_provider";
import {
  cancelOrganizationCallLink,
  createOrganizationCallLink,
  createConversation,
  getAccessToken,
  getConversationMessages,
  getOrganizationConversations,
  getOrganizationCallLink,
  getOrganizationPresence,
  getOrganizationUnreadCounts,
  forwardConversationMessage,
  searchOrganizationMessages,
  listCallRecordings,
  listOrganizationCallLinks,
  sendConversationMessage,
  updateConversationReadState,
} from "@/lib/api_client";
import {
  downloadTeamConversationAttachment,
  sendTeamConversationAttachment,
} from "@/lib/team_attachment_client";
import {
  disableTeamPushNotifications,
  enableTeamPushNotifications,
} from "@/lib/team_push_client";
import {
  TEAM_ATTACHMENT_ACCEPT,
  TEAM_ATTACHMENT_MAX_FILES,
  TEAM_DOCUMENT_ATTACHMENT_ACCEPT,
  classifyTeamAttachment,
  validateTeamAttachments,
} from "@/lib/team_attachment_policy";
const copy = projectsTeamWorkspaceTranslations;

const FOCUS_REFRESH_DEBOUNCE_MS = 750;
const TEAM_MESSAGES_CACHE_TTL_MS = 120_000;
const TEAM_MESSAGE_INITIAL_LIMIT = 40;
const MESSAGE_ACK_TIMEOUT_MS = 8_000;

function getTeamMessagesCacheKey(userId, organizationId) {
  return userId && organizationId
    ? `redocx:team-messages:v2:${userId}:${organizationId}`
    : "";
}

function readTeamMessagesCache(userId, organizationId) {
  if (typeof window === "undefined") return null;

  const cacheKey = getTeamMessagesCacheKey(userId, organizationId);
  if (!cacheKey) return null;

  try {
    // v1 could contain internal attachment storage fields emitted by the former
    // plaintext implementation. It is never read by this release.
    window.sessionStorage.removeItem(
      `redocx:team-messages:v1:${userId}:${organizationId}`,
    );
    const cached = JSON.parse(
      window.sessionStorage.getItem(cacheKey) || "null",
    );
    if (
      !cached ||
      Date.now() - Number(cached.cachedAt || 0) > TEAM_MESSAGES_CACHE_TTL_MS
    ) {
      return null;
    }
    return cached;
  } catch {
    return null;
  }
}

function writeTeamMessagesCache(userId, organizationId, value) {
  if (typeof window === "undefined") return;

  const cacheKey = getTeamMessagesCacheKey(userId, organizationId);
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
    // Session cache is a best-effort speed layer.
  }
}

function clearTeamMessagesCache(userId, organizationId) {
  if (typeof window === "undefined") return;

  const cacheKey = getTeamMessagesCacheKey(userId, organizationId);
  if (!cacheKey) return;

  window.sessionStorage.removeItem(cacheKey);
}

function getCachedMessagesForConversation(cache, conversationId) {
  const key = String(conversationId || "");
  const cachedMessages = cache?.messagesByConversation?.[key];
  return Array.isArray(cachedMessages) ? cachedMessages : null;
}

function titleCase(value) {
  if (!value) return "—";

  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function getErrorMessage(error, language = "en", fallbackCode = "TEAM_REQUEST_FAILED") {
  return resolveErrorMessage(error, language, fallbackCode);
}

async function fetchJson(path, options = {}) {
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

  const data = await response.json().catch(() => null);

  if (!response.ok) {
    const error = Object.assign(new Error("TEAM_REQUEST_FAILED"), {
      payload: data,
      status: response.status,
      code: data?.error?.code || data?.detail?.error || "TEAM_REQUEST_FAILED",
      translationKey: resolveErrorTranslationKey(data, "TEAM_REQUEST_FAILED"),
    });
    throw error;
  }

  return data;
}

function getMessageCallSessionId(message) {
  const metadata = message?.metadata;

  if (!metadata || typeof metadata !== "object") {
    return null;
  }

  return metadata.call_session_id || metadata.callSessionId || null;
}

function getMessageCallState(message) {
  const call = message?.metadata?.call;
  return call && typeof call === "object" ? call : null;
}

function isTerminalCallState(call) {
  return ["ended", "missed", "cancelled"].includes(call?.status);
}

function getOrganizationName(details, entitlement, language = "en") {
  return (
    details?.organization?.name ||
    details?.name ||
    entitlement?.organization_name ||
    getPageRuntimeCopy("projectsTeam", language).teamName
  );
}

function getMemberEmail(member, language = "en") {
  return (
    member?.email ||
    member?.profile?.email ||
    member?.user?.email ||
    member?.user_id ||
    getPageRuntimeCopy("projectsTeam", language).noEmail
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

  return getPageRuntimeCopy("projectsTeam", language).teamMember;
}

function getMemberInitial(member, language = "en") {
  const name = getMemberName(member, language);
  const email = getMemberEmail(member, language);
  const teamMember = getPageRuntimeCopy("projectsTeam", language).teamMember;
  const source = name && name !== teamMember ? name : email;
  return (
    String(source || "?")
      .trim()
      .charAt(0)
      .toUpperCase() || "?"
  );
}

function getTextInitial(value) {
  return (
    String(value || "?")
      .trim()
      .charAt(0)
      .toUpperCase() || "?"
  );
}

function getMemberJoinedTime(member) {
  const value =
    member?.joined_at ||
    member?.joinedAt ||
    member?.created_at ||
    member?.createdAt ||
    member?.updated_at ||
    member?.updatedAt;

  const timestamp = value ? new Date(value).getTime() : 0;
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function getConversationMemberIds(conversation) {
  const candidates =
    conversation?.member_user_ids ||
    conversation?.memberUserIds ||
    conversation?.participant_user_ids ||
    conversation?.participantUserIds ||
    conversation?.members ||
    conversation?.participants ||
    conversation?.conversation_members ||
    [];

  if (!Array.isArray(candidates)) {
    return [];
  }

  return candidates
    .map((item) => {
      if (typeof item === "string") return item;
      return item?.user_id || item?.userId || item?.id || null;
    })
    .filter(Boolean);
}

function getConversationGroupScope(conversation) {
  if (conversation?.type !== "group") return null;
  return (
    conversation.group_scope ||
    conversation.groupScope ||
    // Groups created before subgroup support were organization-wide.
    "organization"
  );
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `client:${crypto.randomUUID()}`;
  }

  return `client:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function getMessageClientId(message) {
  return (
    message?.client_message_id ||
    message?.clientMessageId ||
    message?.metadata?.client_message_id ||
    message?.metadata?.clientMessageId ||
    ""
  );
}

function formatFileSize(bytes) {
  const value = Number(bytes || 0);

  if (!Number.isFinite(value) || value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function getAttachmentPolicyMessage(error, t, language = "en") {
  if (error?.code === "attachment_too_large") return t.attachmentTooLarge;
  if (error?.code === "too_many_attachments") return t.attachmentTooMany;
  if (error?.code === "attachment_message_too_large") {
    return t.attachmentMessageTooLarge;
  }
  if (
    [
      "unsupported_attachment_type",
      "dangerous_double_extension",
      "invalid_attachment_filename",
    ].includes(error?.code)
  ) {
    return t.attachmentUnsupported;
  }
  return getErrorMessage(error, language, "ATTACHMENT_INVALID");
}

function getMessageAttachments(message) {
  const attachments = message?.metadata?.attachments;
  return Array.isArray(attachments) ? attachments.filter(Boolean) : [];
}

function isForwardedMessage(message) {
  return Boolean(
    message?.metadata?.forwarded_from_message_id ||
      message?.metadata?.forwardedFromMessageId,
  );
}

function getAttachmentDisplayName(attachment, language = "en") {
  return (
    attachment?.original_filename ||
    attachment?.originalFilename ||
    attachment?.filename ||
    getPageRuntimeCopy("projectsTeam", language).attachment
  );
}

function getAttachmentKind(attachment) {
  const kind = String(attachment?.kind || "").toLowerCase();
  if (["image", "audio", "video", "document", "file"].includes(kind)) {
    return kind;
  }

  const contentType = String(attachment?.content_type || "").toLowerCase();
  if (contentType.startsWith("image/")) return "image";
  if (contentType.startsWith("audio/")) return "audio";
  if (contentType.startsWith("video/")) return "video";
  return "file";
}

function AttachmentIcon({ kind, className = "h-4 w-4" }) {
  if (kind === "image") return <ImageIcon className={className} />;
  if (kind === "audio") return <Music className={className} />;
  if (kind === "video") return <PlayCircle className={className} />;
  return <FileText className={className} />;
}

function AttachmentCard({ attachment, isMine, t, language, onOpen }) {
  const kind = getAttachmentKind(attachment);
  const filename = getAttachmentDisplayName(attachment, language);
  const secured = attachment?.security_status === "secured";
  const pendingSecurity = attachment?.security_status === "scanning";
  const available =
    secured &&
    attachment?.available_for_download !== false &&
    Boolean(attachment?.download_url || attachment?.downloadUrl);
  const size = formatFileSize(
    attachment?.file_size_bytes ||
      attachment?.fileSizeBytes ||
      attachment?.size,
  );

  return (
    <button
      type="button"
      onClick={() => available && onOpen(attachment)}
      disabled={!available}
      title={
        available
          ? t.openAttachment
          : pendingSecurity
            ? t.uploadingAttachment
            : t.attachmentUnavailable
      }
      className={`mt-2 flex w-full max-w-sm items-center gap-3 rounded-xl border px-3 py-2 text-left transition hover:scale-[1.01] ${
        isMine
          ? "border-current/20 bg-black/5 text-[var(--app-button-text)]"
          : "app-surface app-text"
      } disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100`}
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--app-border)] bg-white/10">
        <AttachmentIcon kind={kind} className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold">{filename}</span>
        <span className="mt-0.5 block text-[11px] opacity-75">
          {kind} · {size}
        </span>
        <span className="mt-0.5 flex items-center gap-1 text-[10px] opacity-75">
          {secured ? <ShieldCheck className="h-3 w-3" /> : null}
          {available
            ? t.attachmentSecured
            : pendingSecurity
              ? t.uploadingAttachment
              : t.attachmentUnavailable}
        </span>
      </span>
      {available ? <Download className="h-4 w-4 shrink-0 opacity-75" /> : null}
      <span className="sr-only">{t.openAttachment}</span>
    </button>
  );
}

function buildOptimisticTextMessage({
  conversationId,
  organizationId,
  currentUserId,
  body,
  clientMessageId,
}) {
  const now = new Date().toISOString();

  return {
    id: clientMessageId,
    client_message_id: clientMessageId,
    conversation_id: conversationId,
    organization_id: organizationId,
    sender_user_id: currentUserId,
    message_type: "text",
    body,
    metadata: {
      client_message_id: clientMessageId,
      transport: "websocket",
      pending: true,
    },
    edited_at: null,
    deleted_at: null,
    created_at: now,
    updated_at: now,
    pending: true,
  };
}

function buildOptimisticAttachmentMessage({
  conversationId,
  organizationId,
  currentUserId,
  body,
  files,
  clientMessageId,
  language = "en",
}) {
  const now = new Date().toISOString();
  const normalizedFiles = Array.from(files || []);
  const firstFile = normalizedFiles[0];

  return {
    id: clientMessageId,
    client_message_id: clientMessageId,
    conversation_id: conversationId,
    organization_id: organizationId,
    sender_user_id: currentUserId,
    message_type: "attachment",
    body:
      body ||
      (normalizedFiles.length > 1
        ? `${firstFile?.name || getPageRuntimeCopy("projectsTeam", language).attachment} ${getPageRuntimeCopy("projectsTeam", language).moreAttachments.replace("{count}", String(normalizedFiles.length - 1))}`
        : firstFile?.name || getPageRuntimeCopy("projectsTeam", language).attachment),
    metadata: {
      client_message_id: clientMessageId,
      transport: "http_upload",
      pending: true,
      attachment_count: normalizedFiles.length,
      attachments: normalizedFiles.map((file, index) => ({
        id: `${clientMessageId}:${index}`,
        kind: classifyTeamAttachment(file),
        original_filename: file?.name || getPageRuntimeCopy("projectsTeam", language).attachment,
        content_type: file?.type || "application/octet-stream",
        file_size_bytes: file?.size || 0,
        security_status: "scanning",
        available_for_download: false,
      })),
    },
    edited_at: null,
    deleted_at: null,
    created_at: now,
    updated_at: now,
    pending: true,
  };
}

export default function ProjectsTeamPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { language } = useLanguage();
  const {
    activeCall,
    prepareOutgoingCall,
    prepareIncomingCall,
    prepareScheduledCall,
    realtimeReady,
    restoreCall,
    sendRealtimeMessage,
  } = useTeamRealtime();
  const {
    user,
    entitlement,
    authChecked,
    loading: accountLoading,
  } = useAccount();
  const t = copy[language] || copy.en;
  const routeConversationId = useMemo(() => {
    const rawConversationId = searchParams.get("conversationId");
    const parsedConversationId = Number.parseInt(rawConversationId || "", 10);

    return Number.isFinite(parsedConversationId) && parsedConversationId > 0
      ? parsedConversationId
      : null;
  }, [searchParams]);
  const routeMessageId = searchParams.get("messageId") || "";
  const routeCallSessionId = searchParams.get("callSessionId") || "";
  const routeCallAction = searchParams.get("callAction") || "";
  const routeCallLinkPublicId = searchParams.get("callLink") || "";
  const routeCallLinkOrganizationId = searchParams.get("organizationId") || "";
  const routeCallMediaType =
    searchParams.get("mediaType") === "audio" ? "audio" : "video";

  const [loading, setLoading] = useState(true);
  const [organizationDetails, setOrganizationDetails] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [selectedConversationId, setSelectedConversationId] = useState(null);
  const selectedConversationIdRef = useRef(null);
  const refreshInFlightRef = useRef(false);
  const conversationSelectionRequestRef = useRef(0);
  const routeCallHandledRef = useRef("");
  const routeCallLinkHandledRef = useRef("");
  const pendingMessageRetryTimersRef = useRef(new Map());
  const lastReadMessageByConversationRef = useRef(new Map());
  const attachmentInputRef = useRef(null);
  const documentShareInputRef = useRef(null);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [presence, setPresence] = useState([]);
  const [messageDraft, setMessageDraft] = useState("");
  const [attachmentFiles, setAttachmentFiles] = useState([]);
  const [attachmentUploadProgress, setAttachmentUploadProgress] =
    useState(null);
  const [forwardSourceMessage, setForwardSourceMessage] = useState(null);
  const [forwardRecipientUserIds, setForwardRecipientUserIds] = useState([]);
  const [documentShareOpen, setDocumentShareOpen] = useState(false);
  const [documentRecipientUserId, setDocumentRecipientUserId] = useState("");
  const [busy, setBusy] = useState("");
  const [pushNotificationsEnabled, setPushNotificationsEnabled] =
    useState(false);
  const [pushNotificationsBusy, setPushNotificationsBusy] = useState(false);
  const [subgroupComposerOpen, setSubgroupComposerOpen] = useState(false);
  const [subgroupName, setSubgroupName] = useState("");
  const [subgroupMemberUserIds, setSubgroupMemberUserIds] = useState([]);
  const [notice, setNotice] = useState("");
  const [highlightMessageId, setHighlightMessageId] = useState(null);
  const [unreadCounts, setUnreadCounts] = useState([]);
  const [messageSearchQuery, setMessageSearchQuery] = useState("");
  const [messageSearchResults, setMessageSearchResults] = useState([]);
  const [messageSearching, setMessageSearching] = useState(false);
  const [callLinkComposerOpen, setCallLinkComposerOpen] = useState(false);
  const [callLinks, setCallLinks] = useState([]);
  const [recordingsCallId, setRecordingsCallId] = useState(null);
  const [callRecordings, setCallRecordings] = useState([]);
  const [callLinkDraft, setCallLinkDraft] = useState({
    title: "",
    scheduledStartLocal: "",
    durationMinutes: 60,
    maxParticipants: 2,
    mediaType: "video",
  });

  const organizationId = entitlement?.organization_id || null;
  const isBusinessOrEnterprise =
    entitlement?.source === "organization" &&
    entitlement?.status === "active" &&
    ["business", "enterprise"].includes(entitlement?.plan);
  const currentUserId = user?.id;
  const isOwner = entitlement?.organization_role === "owner";
  const canInviteMembers = ["owner", "admin"].includes(
    entitlement?.organization_role,
  );
  const subgroupMemberLimit =
    String(entitlement?.plan || "").toLowerCase() === "enterprise" ? 30 : 18;
  const subgroupSelectableMemberLimit = Math.max(0, subgroupMemberLimit - 1);

  const activeMembers = useMemo(
    () =>
      (organizationDetails?.members || [])
        .filter((member) => member.status === "active")
        .sort((a, b) => getMemberJoinedTime(b) - getMemberJoinedTime(a)),
    [organizationDetails],
  );

  const otherMembers = useMemo(
    () => activeMembers.filter((member) => member.user_id !== currentUserId),
    [activeMembers, currentUserId],
  );

  const memberByUserId = useMemo(
    () => new Map(activeMembers.map((member) => [member.user_id, member])),
    [activeMembers],
  );

  const presenceByUserId = useMemo(
    () => new Map(presence.map((entry) => [entry.user_id, entry])),
    [presence],
  );

  const selectedConversation = useMemo(
    () =>
      conversations.find(
        (conversation) => conversation.id === selectedConversationId,
      ) || null,
    [conversations, selectedConversationId],
  );

  const unreadByConversationId = useMemo(
    () =>
      new Map(
        unreadCounts.map((entry) => [
          Number(entry.conversation_id),
          Number(entry.unread_count || 0),
        ]),
      ),
    [unreadCounts],
  );

  const organizationName = useMemo(
    () => getOrganizationName(organizationDetails, entitlement, language),
    [organizationDetails, entitlement, language],
  );

  const groupConversation = useMemo(
    () =>
      conversations
        .filter(
          (conversation) =>
            conversation.type === "group" &&
            getConversationGroupScope(conversation) === "organization",
        )
        .sort((a, b) => {
          const aTime = new Date(a.updated_at || a.created_at || 0).getTime();
          const bTime = new Date(b.updated_at || b.created_at || 0).getTime();
          return bTime - aTime;
        })[0] || null,
    [conversations],
  );

  const subgroupConversations = useMemo(
    () =>
      conversations
        .filter(
          (conversation) =>
            conversation.type === "group" &&
            getConversationGroupScope(conversation) === "subgroup",
        )
        .sort((a, b) => {
          const aTime = new Date(a.updated_at || a.created_at || 0).getTime();
          const bTime = new Date(b.updated_at || b.created_at || 0).getTime();
          return bTime - aTime;
        }),
    [conversations],
  );

  function updateWorkspaceCache(patch) {
    const current = readTeamMessagesCache(currentUserId, organizationId) || {};
    const next =
      typeof patch === "function" ? patch(current) : { ...current, ...patch };
    writeTeamMessagesCache(currentUserId, organizationId, next);
  }

  function hydrateWorkspaceFromCache(preferredConversationId) {
    const cached = readTeamMessagesCache(currentUserId, organizationId);
    if (!cached) return false;

    const cachedConversations = Array.isArray(cached.conversations)
      ? cached.conversations
      : [];
    const selectedConversation = preferredConversationId
      ? cachedConversations.find(
          (conversation) => conversation.id === preferredConversationId,
        ) || null
      : null;

    setOrganizationDetails(cached.organizationDetails || null);
    setConversations(cachedConversations);
    setPresence(Array.isArray(cached.presence) ? cached.presence : []);

    if (selectedConversation?.id) {
      selectedConversationIdRef.current = selectedConversation.id;
      setSelectedConversationId(selectedConversation.id);
      setMessages(
        getCachedMessagesForConversation(cached, selectedConversation.id) || [],
      );
    } else {
      selectedConversationIdRef.current = null;
      setSelectedConversationId(null);
      setMessages([]);
    }

    setLoading(false);
    return true;
  }

  function getMemberLabel(userId) {
    const member = memberByUserId.get(userId);
    return member ? getMemberName(member, language) : userId;
  }

  function getConversationTitle(conversation) {
    if (!conversation) return "—";
    if (conversation.name) return conversation.name;

    if (conversation.type === "dm") {
      const otherParticipantId = getConversationMemberIds(conversation).find(
        (userId) => userId !== currentUserId,
      );

      return otherParticipantId
        ? getMemberLabel(otherParticipantId)
        : t.directMessage;
    }

    return `${organizationName} ${t.groupChat}`;
  }

  function getMemberPresenceStatus(userId) {
    return presenceByUserId.get(userId)?.status || "offline";
  }

  async function loadOrganizationDetails(nextOrganizationId) {
    const data = await fetchJson(`/api/organizations/${nextOrganizationId}`);
    setOrganizationDetails(data);
    updateWorkspaceCache({ organizationDetails: data });
    return data;
  }

  async function loadConversations(
    nextOrganizationId,
    preferredConversationId,
    { selectFallback = false } = {},
  ) {
    const data = await getOrganizationConversations(nextOrganizationId);
    const nextConversations = data.conversations || [];

    setConversations(nextConversations);
    updateWorkspaceCache({ conversations: nextConversations });

    const currentSelectedId = selectedConversationIdRef.current;
    const nextSelected =
      nextConversations.find(
        (conversation) => conversation.id === preferredConversationId,
      ) ||
      nextConversations.find(
        (conversation) => conversation.id === currentSelectedId,
      ) ||
      (selectFallback
          ? nextConversations.find(
            (conversation) =>
              conversation.type === "group" &&
              getConversationGroupScope(conversation) === "organization",
          ) ||
          nextConversations[0] ||
          null
        : null);

    if (nextSelected) {
      selectedConversationIdRef.current = nextSelected.id;
      setSelectedConversationId(nextSelected.id);
      updateWorkspaceCache({ selectedConversationId: nextSelected.id });
      return nextSelected;
    }

    if (selectFallback) {
      selectedConversationIdRef.current = null;
      setSelectedConversationId(null);
      updateWorkspaceCache({ selectedConversationId: null });
    }

    return null;
  }

  async function loadPresence(nextOrganizationId) {
    const data = await getOrganizationPresence(nextOrganizationId);
    const nextPresence = data.presence || [];
    setPresence(nextPresence);
    updateWorkspaceCache({ presence: nextPresence });
    return nextPresence;
  }

  const loadUnreadCounts = useCallback(
    async (nextOrganizationId = organizationId) => {
      if (!nextOrganizationId) return [];
      const data = await getOrganizationUnreadCounts(nextOrganizationId);
      const nextCounts = Array.isArray(data?.counts) ? data.counts : [];
      setUnreadCounts(nextCounts);
      return nextCounts;
    },
    [organizationId],
  );

  const loadCallLinks = useCallback(
    async (nextOrganizationId = organizationId) => {
      if (!nextOrganizationId) return [];
      const data = await listOrganizationCallLinks(nextOrganizationId, {
        includePast: true,
      });
      const nextLinks = Array.isArray(data?.call_links)
        ? data.call_links.filter((item) => item?.status !== "cancelled")
        : [];
      setCallLinks(nextLinks);
      return nextLinks;
    },
    [organizationId],
  );

  function getUnreadCount(conversationId) {
    return unreadByConversationId.get(Number(conversationId)) || 0;
  }

  async function loadMessages(conversationId, { preferCache = true } = {}) {
    if (!conversationId) {
      setMessages([]);
      setMessagesLoading(false);
      return [];
    }

    const cached = preferCache
      ? getCachedMessagesForConversation(
          readTeamMessagesCache(currentUserId, organizationId),
          conversationId,
        )
      : null;

    if (cached) {
      setMessages(cached);
    }

    setMessagesLoading(!cached);

    try {
      const data = await getConversationMessages(conversationId, {
        limit: TEAM_MESSAGE_INITIAL_LIMIT,
      });
      const nextMessages = data.messages || [];
      setMessages(nextMessages);
      updateWorkspaceCache((current) => ({
        ...current,
        selectedConversationId: conversationId,
        messagesByConversation: {
          ...(current.messagesByConversation || {}),
          [String(conversationId)]: nextMessages,
        },
      }));
      return nextMessages;
    } finally {
      setMessagesLoading(false);
    }
  }

  function upsertConversation(nextConversation) {
    if (!nextConversation?.id) return;

    setConversations((current) => {
      const exists = current.some(
        (conversation) => conversation.id === nextConversation.id,
      );
      const nextConversations = exists
        ? current.map((conversation) =>
            conversation.id === nextConversation.id
              ? { ...conversation, ...nextConversation }
              : conversation,
          )
        : [nextConversation, ...current];

      return [...nextConversations].sort((a, b) => {
        const aTime = new Date(
          a.last_message_at || a.updated_at || a.created_at || 0,
        ).getTime();
        const bTime = new Date(
          b.last_message_at || b.updated_at || b.created_at || 0,
        ).getTime();
        return bTime - aTime;
      });
    });
  }

  function clearPendingMessageRetry(clientMessageId) {
    if (!clientMessageId) return;

    const timeoutId = pendingMessageRetryTimersRef.current.get(clientMessageId);
    if (timeoutId) {
      window.clearTimeout(timeoutId);
      pendingMessageRetryTimersRef.current.delete(clientMessageId);
    }
  }

  function upsertMessage(nextMessage, clientMessageId = "") {
    if (!nextMessage?.id) return;

    const currentSelectedId = Number(selectedConversationIdRef.current || 0);
    const nextConversationId = Number(nextMessage.conversation_id || 0);
    if (
      currentSelectedId < 1 ||
      nextConversationId < 1 ||
      nextConversationId !== currentSelectedId
    ) {
      return;
    }

    const nextClientMessageId =
      clientMessageId || getMessageClientId(nextMessage) || "";
    clearPendingMessageRetry(nextClientMessageId);

    const normalizedMessage = {
      ...nextMessage,
      client_message_id: nextClientMessageId || nextMessage.client_message_id,
      pending: Boolean(nextMessage.pending),
      failed: false,
    };

    setMessages((current) => {
      const nextMessageId = String(normalizedMessage.id);
      const existingIndex = current.findIndex((message) => {
        const currentMessageId = String(message.id);
        const currentClientMessageId = getMessageClientId(message);

        return (
          currentMessageId === nextMessageId ||
          (nextClientMessageId &&
            currentClientMessageId === nextClientMessageId)
        );
      });

      const nextMessages = [...current];
      if (existingIndex >= 0) {
        nextMessages[existingIndex] = {
          ...nextMessages[existingIndex],
          ...normalizedMessage,
        };
      } else {
        nextMessages.push(normalizedMessage);
      }

      return nextMessages.sort((a, b) => {
        const aTime = new Date(a.created_at || 0).getTime();
        const bTime = new Date(b.created_at || 0).getTime();

        if (aTime !== bTime) return aTime - bTime;

        const aId = Number(a.id);
        const bId = Number(b.id);
        if (Number.isFinite(aId) && Number.isFinite(bId)) {
          return aId - bId;
        }

        return String(a.id || "").localeCompare(String(b.id || ""));
      });
    });
  }

  async function persistPendingTextMessage({
    conversationId,
    body,
    clientMessageId,
    restoreDraftOnFailure = false,
  }) {
    clearPendingMessageRetry(clientMessageId);

    try {
      const data = await sendConversationMessage(conversationId, body, {
        clientMessageId,
      });

      if (data?.message) {
        upsertMessage(
          {
            ...data.message,
            pending: false,
          },
          clientMessageId,
        );
      }

      if (data?.conversation) {
        upsertConversation(data.conversation);
      }

      return true;
    } catch (error) {
      const errorMessage = getErrorMessage(error, language);
      markMessageFailed(clientMessageId, errorMessage);

      if (restoreDraftOnFailure) {
        setMessageDraft((current) => current || body);
      }

      setNotice(errorMessage);
      return false;
    }
  }

  function scheduleMessageHttpFallback({
    conversationId,
    body,
    clientMessageId,
  }) {
    clearPendingMessageRetry(clientMessageId);

    const timeoutId = window.setTimeout(() => {
      pendingMessageRetryTimersRef.current.delete(clientMessageId);
      void persistPendingTextMessage({
        conversationId,
        body,
        clientMessageId,
      });
    }, MESSAGE_ACK_TIMEOUT_MS);

    pendingMessageRetryTimersRef.current.set(clientMessageId, timeoutId);
  }

  function markMessageFailed(clientMessageId, errorMessage) {
    if (!clientMessageId) return;

    setMessages((current) =>
      current.map((message) => {
        const currentClientMessageId = getMessageClientId(message);
        const currentMessageId = String(message.id || "");

        if (
          currentMessageId !== clientMessageId &&
          currentClientMessageId !== clientMessageId
        ) {
          return message;
        }

        return {
          ...message,
          pending: false,
          failed: true,
          error: errorMessage || t.messageFailed,
          metadata:
            message.message_type === "attachment"
              ? {
                  ...(message.metadata || {}),
                  attachments: getMessageAttachments(message).map(
                    (attachment) => ({
                      ...attachment,
                      security_status: "rejected",
                      available_for_download: false,
                    }),
                  ),
                }
              : message.metadata,
        };
      }),
    );
  }

  function upsertPresence(nextPresence) {
    if (!nextPresence?.user_id) return;

    setPresence((current) => {
      const exists = current.some(
        (entry) => entry.user_id === nextPresence.user_id,
      );

      if (!exists) return [...current, nextPresence];

      return current.map((entry) =>
        entry.user_id === nextPresence.user_id
          ? { ...entry, ...nextPresence }
          : entry,
      );
    });
  }

  function handleRealtimeEvent(event) {
    if (!event || event.organization_id !== organizationId) return;

    if (
      event.type === "organization.access.revoked" &&
      (!event.user_id || event.user_id === currentUserId)
    ) {
      clearTeamMessagesCache(currentUserId, organizationId);
      for (const timeoutId of pendingMessageRetryTimersRef.current.values()) {
        window.clearTimeout(timeoutId);
      }
      pendingMessageRetryTimersRef.current.clear();
      selectedConversationIdRef.current = null;
      setOrganizationDetails(null);
      setConversations([]);
      setSelectedConversationId(null);
      setMessages([]);
      setMessagesLoading(false);
      setPresence([]);
      setUnreadCounts([]);
      setMessageSearchQuery("");
      setMessageSearchResults([]);
      setMessageDraft("");
      setAttachmentFiles([]);
      setAttachmentUploadProgress(null);
      setForwardSourceMessage(null);
      setForwardRecipientUserIds([]);
      setSubgroupComposerOpen(false);
      setSubgroupName("");
      setSubgroupMemberUserIds([]);
      setCallLinks([]);
      setCallLinkComposerOpen(false);
      setRecordingsCallId(null);
      setCallRecordings([]);
      setDocumentShareOpen(false);
      setDocumentRecipientUserId("");
      setHighlightMessageId(null);
      setBusy("");
      setNotice("");
      setLoading(false);
      router.replace("/");
      return;
    }

    if (event.type === "organization.updated" && event.organization) {
      setOrganizationDetails((current) => {
        const next = {
          ...(current || {}),
          organization: {
            ...(current?.organization || {}),
            ...event.organization,
          },
        };
        updateWorkspaceCache({ organizationDetails: next });
        return next;
      });
      return;
    }

    if (event.conversation) {
      upsertConversation(event.conversation);
    }

    if (event.presence) {
      upsertPresence(event.presence);
    }

    if (event.type === "conversation.created") {
      void loadConversations(
        organizationId,
        selectedConversationIdRef.current,
        { selectFallback: false },
      ).catch(() => {});
      if (event.conversation?.id === selectedConversationIdRef.current) {
        void loadMessages(event.conversation.id);
      }
      return;
    }

    if (
      ["message.created", "message.persisted", "message.ack"].includes(
        event.type,
      )
    ) {
      const eventConversationId = Number(event.message?.conversation_id || 0);
      const senderUserId = String(event.message?.sender_user_id || "");
      if (
        event.type === "message.created" &&
        eventConversationId > 0 &&
        eventConversationId !==
          Number(selectedConversationIdRef.current || 0) &&
        senderUserId !== String(currentUserId || "")
      ) {
        setUnreadCounts((current) => {
          const exists = current.some(
            (entry) => Number(entry.conversation_id) === eventConversationId,
          );
          if (!exists) {
            return [
              ...current,
              { conversation_id: eventConversationId, unread_count: 1 },
            ];
          }
          return current.map((entry) =>
            Number(entry.conversation_id) === eventConversationId
              ? {
                  ...entry,
                  unread_count: Number(entry.unread_count || 0) + 1,
                }
              : entry,
          );
        });
      }
      upsertMessage(
        {
          ...event.message,
          pending:
            event.type === "message.created" && Boolean(event.message?.pending),
        },
        event.client_message_id,
      );
      return;
    }

    if (event.type === "message.failed") {
      clearPendingMessageRetry(event.client_message_id);
      markMessageFailed(event.client_message_id, event.message);
      setNotice(resolveErrorMessage({ code: event.code || "TEAM_REQUEST_FAILED" }, language, "TEAM_REQUEST_FAILED"));
      return;
    }

    if (event.type === "call.started") {
      upsertMessage(event.message);
      return;
    }

    if (
      [
        "call.joined",
        "call.left",
        "call.declined",
        "call.ended",
        "call.cancelled",
        "call.missed",
      ].includes(event.type)
    ) {
      if (event.call?.conversation_id === selectedConversationIdRef.current) {
        void loadMessages(event.call.conversation_id);
      }
    }
  }

  async function loadAll({ preferredConversationId, force = false } = {}) {
    if (!organizationId || !isBusinessOrEnterprise) {
      setLoading(false);
      return;
    }

    const hydrated =
      !force && hydrateWorkspaceFromCache(preferredConversationId);
    setLoading(!hydrated);
    setNotice("");

    try {
      const [, nextSelected] = await Promise.all([
        loadOrganizationDetails(organizationId),
        loadConversations(organizationId, preferredConversationId, {
          selectFallback: false,
        }),
      ]);

      setLoading(false);

      await Promise.all([
        loadPresence(organizationId),
        loadUnreadCounts(organizationId),
        loadCallLinks(organizationId),
        loadMessages(nextSelected?.id, { preferCache: !force }),
      ]);
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setLoading(false);
    }
  }

  async function refreshCurrentConversation() {
    if (!organizationId || refreshInFlightRef.current) return;

    const conversationId = selectedConversationIdRef.current;
    refreshInFlightRef.current = true;

    try {
      const tasks = [
        loadConversations(organizationId, conversationId, {
          selectFallback: false,
        }),
        loadPresence(organizationId),
        loadUnreadCounts(organizationId),
        loadCallLinks(organizationId),
      ];

      if (conversationId) {
        tasks.push(loadMessages(conversationId, { preferCache: false }));
      }

      await Promise.all(tasks);
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      refreshInFlightRef.current = false;
    }
  }

  function openCallLinkComposer() {
    const scheduled = new Date(Date.now() + 60 * 60 * 1000);
    scheduled.setMinutes(Math.ceil(scheduled.getMinutes() / 15) * 15, 0, 0);
    const pad = (value) => String(value).padStart(2, "0");
    const scheduledStartLocal = `${scheduled.getFullYear()}-${pad(
      scheduled.getMonth() + 1,
    )}-${pad(scheduled.getDate())}T${pad(scheduled.getHours())}:${pad(
      scheduled.getMinutes(),
    )}`;
    setCallLinkDraft({
      title: "",
      scheduledStartLocal,
      durationMinutes: 60,
      maxParticipants: Math.max(2, Math.min(500, activeMembers.length)),
      mediaType: "video",
    });
    setCallLinkComposerOpen(true);
    void loadCallLinks(organizationId).catch((error) =>
      setNotice(getErrorMessage(error, language)),
    );
  }

  async function handleCreateCallLink() {
    if (!organizationId || busy === "create-call-link") return;
    const scheduledStart = new Date(callLinkDraft.scheduledStartLocal);
    if (!Number.isFinite(scheduledStart.getTime())) {
      setNotice(t.callLinkDateTime);
      return;
    }
    setBusy("create-call-link");
    try {
      const data = await createOrganizationCallLink(organizationId, {
        title: callLinkDraft.title,
        scheduledStartAt: scheduledStart.toISOString(),
        durationMinutes: callLinkDraft.durationMinutes,
        maxParticipants: callLinkDraft.maxParticipants,
        mediaType: callLinkDraft.mediaType,
      });
      const created = data?.call_link;
      if (created) {
        setCallLinks((current) =>
          [created, ...current.filter((item) => item.id !== created.id)].sort(
            (a, b) =>
              new Date(a.scheduled_start_at).getTime() -
              new Date(b.scheduled_start_at).getTime(),
          ),
        );
        setCallLinkDraft((current) => ({ ...current, title: "" }));
      }
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function copyCallLink(callLink) {
    try {
      const absoluteUrl = new URL(
        callLink.share_url,
        window.location.origin,
      ).toString();
      await navigator.clipboard.writeText(absoluteUrl);
      setNotice(t.callLinkCopied);
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    }
  }

  function openScheduledCall(callLink) {
    if (activeCall) {
      restoreCall();
      setNotice(t.callAlreadyActive);
      return;
    }
    setCallLinkComposerOpen(false);
    prepareScheduledCall({
      organizationId,
      publicId: callLink.public_id,
      callLink,
    });
  }

  async function cancelScheduledCall(callLink) {
    if (!organizationId || busy === `cancel-call-link:${callLink.id}`) return;
    if (!window.confirm(t.cancelScheduledCall)) return;
    setBusy(`cancel-call-link:${callLink.id}`);
    try {
      await cancelOrganizationCallLink(
        organizationId,
        callLink.public_id,
      );
      setCallLinks((current) =>
        current.filter((item) => item.id !== callLink.id),
      );
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function openCallRecordings(callSessionId) {
    const normalizedCallId = Number.parseInt(String(callSessionId || ""), 10);
    if (!Number.isSafeInteger(normalizedCallId) || normalizedCallId < 1) return;
    setRecordingsCallId(normalizedCallId);
    setCallRecordings([]);
    setBusy(`load-recordings:${normalizedCallId}`);
    try {
      const data = await listCallRecordings(normalizedCallId);
      setCallRecordings(
        Array.isArray(data?.recordings) ? data.recordings : [],
      );
    } catch (error) {
      setRecordingsCallId(null);
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function downloadCallRecording(recording) {
    const downloadUrl = String(recording?.download_url || "").trim();
    if (!downloadUrl || !recording?.id || !recordingsCallId) return;
    setBusy(`download-recording:${recording.id}`);
    setNotice("");
    try {
      let token = await getAccessToken();
      const request = () =>
        fetch(downloadUrl, {
          method: "GET",
          credentials: "include",
          cache: "no-store",
          headers: { Authorization: `Bearer ${token}` },
        });
      let response = await request();
      if (response.status === 401) {
        token = await getAccessToken({ forceRefresh: true });
        response = await request();
      }
      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        const error = Object.assign(new Error("RECORDING_DOWNLOAD_FAILED"), {
          code: "RECORDING_DOWNLOAD_FAILED",
          payload,
          translationKey: resolveErrorTranslationKey(payload, "RECORDING_DOWNLOAD_FAILED"),
        });
        throw error;
      }
      const filename = `redocx-call-${recordingsCallId}-recording-${recording.id}.ogg`;
      if (window.showSaveFilePicker && response.body) {
        const handle = await window.showSaveFilePicker({
          suggestedName: filename,
          types: [
            {
              description: getPageRuntimeCopy("projectsTeam", language).audioDescription,
              accept: { "audio/ogg": [".ogg"] },
            },
          ],
        });
        const writable = await handle.createWritable();
        await response.body.pipeTo(writable);
      } else {
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
      }
    } catch (error) {
      if (error?.name !== "AbortError") setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  function selectConversation(conversationId) {
    const nextConversationId = conversationId || null;
    selectedConversationIdRef.current = nextConversationId;
    setSelectedConversationId(nextConversationId);
    setNotice("");
    updateWorkspaceCache({ selectedConversationId: nextConversationId });

    if (!nextConversationId) {
      setMessages([]);
      setMessagesLoading(false);
      return;
    }

    const cachedMessages = getCachedMessagesForConversation(
      readTeamMessagesCache(currentUserId, organizationId),
      nextConversationId,
    );

    setMessages(cachedMessages || []);
    setMessagesLoading(!cachedMessages);
    void loadMessages(nextConversationId, {
      preferCache: Boolean(cachedMessages),
    }).catch((error) => setNotice(getErrorMessage(error, language)));
  }

  async function ensureDmConversation(member) {
    if (
      !organizationId ||
      !member?.user_id ||
      member.user_id === currentUserId
    ) {
      return null;
    }

    const existing = conversations.find((conversation) => {
      if (conversation.type !== "dm") return false;

      const ids = getConversationMemberIds(conversation);
      return ids.includes(member.user_id) && ids.includes(currentUserId);
    });

    if (existing) {
      return existing;
    }

    const data = await createConversation(organizationId, {
      type: "dm",
      member_user_ids: [member.user_id],
    });

    await loadConversations(organizationId, data.conversation?.id, {
      selectFallback: false,
    });
    return data.conversation || null;
  }

  async function handleMessageMember(member) {
    const requestId = conversationSelectionRequestRef.current + 1;
    conversationSelectionRequestRef.current = requestId;

    setBusy(`message:${member.user_id}`);
    setNotice("");
    setDocumentShareOpen(false);

    try {
      const conversation = await ensureDmConversation(member);

      if (
        conversation?.id &&
        conversationSelectionRequestRef.current === requestId
      ) {
        await selectConversation(conversation.id);
      }
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function startCallForConversation(
    conversationId,
    { mediaType = "video" } = {},
  ) {
    if (!conversationId) return;

    if (activeCall) {
      restoreCall();
      return;
    }

    selectedConversationIdRef.current = conversationId;
    setSelectedConversationId(conversationId);
    setNotice("");

    const conversation = conversations.find(
      (item) => Number(item.id) === Number(conversationId),
    );

    try {
      prepareOutgoingCall({
        conversationId,
        mediaType,
        conversation,
      });
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    }
  }

  async function handleCreateGroupConversation() {
    if (!organizationId || !isOwner || groupConversation) {
      return;
    }

    setBusy("create-group");
    setNotice("");

    try {
      const data = await createConversation(organizationId, {
        type: "group",
        group_scope: "organization",
        name: `${organizationName} Team Chat`,
        member_user_ids: [],
      });

      await loadConversations(organizationId, data.conversation?.id, {
        selectFallback: false,
      });
      await selectConversation(data.conversation?.id);
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function handleOpenGroupConversation() {
    if (!groupConversation?.id) return;
    setDocumentShareOpen(false);
    conversationSelectionRequestRef.current += 1;
    await selectConversation(groupConversation.id);
  }

  function openSubgroupComposer() {
    setNotice("");
    setSubgroupName("");
    setSubgroupMemberUserIds([]);
    setSubgroupComposerOpen(true);
  }

  function closeSubgroupComposer() {
    if (busy === "create-subgroup") return;
    setSubgroupComposerOpen(false);
    setSubgroupName("");
    setSubgroupMemberUserIds([]);
  }

  function toggleSubgroupMember(userId) {
    if (
      !subgroupMemberUserIds.includes(userId) &&
      subgroupMemberUserIds.length >= subgroupSelectableMemberLimit
    ) {
      setNotice(t.subgroupLimitReached);
      return;
    }

    setSubgroupMemberUserIds((current) => {
      if (current.includes(userId)) {
        return current.filter((item) => item !== userId);
      }
      return [...current, userId];
    });
    setNotice("");
  }

  async function handleCreateSubgroupConversation(startMediaType = null) {
    const normalizedName = subgroupName.trim();
    if (!organizationId || !normalizedName) return;
    if (subgroupMemberUserIds.length < 2) {
      setNotice(t.subgroupMinimum);
      return;
    }
    if (startMediaType && activeCall) {
      setNotice(t.callAlreadyActive);
      return;
    }

    setBusy("create-subgroup");
    setNotice("");
    try {
      const data = await createConversation(organizationId, {
        type: "group",
        group_scope: "subgroup",
        name: normalizedName,
        member_user_ids: subgroupMemberUserIds,
      });
      const conversation = data?.conversation;
      if (!conversation?.id) {
        throw Object.assign(new Error("SUBGROUP_CREATE_FAILED"), { code: "SUBGROUP_CREATE_FAILED" });
      }

      await loadConversations(organizationId, conversation.id, {
        selectFallback: false,
      });
      await selectConversation(conversation.id);
      setSubgroupComposerOpen(false);
      setSubgroupName("");
      setSubgroupMemberUserIds([]);

      if (startMediaType) {
        prepareOutgoingCall({
          conversationId: conversation.id,
          mediaType: startMediaType,
          conversation,
        });
      }
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function handleStartCurrentConversationCall(mediaType = "video") {
    await startCallForConversation(selectedConversationId, { mediaType });
  }

  async function handleJoinCall(callSessionId, callState = null) {
    if (!callSessionId) return;

    if (String(activeCall?.call?.id || "") === String(callSessionId)) {
      restoreCall();
      return;
    }

    if (activeCall) {
      setNotice(t.callAlreadyActive);
      return;
    }

    setNotice("");
    try {
      prepareIncomingCall({
        call: {
          ...(callState || {}),
          id: Number(callSessionId),
          conversation_id:
            callState?.conversation_id ||
            selectedConversationId ||
            routeConversationId,
          media_type: callState?.media_type || routeCallMediaType || "video",
        },
        conversation: selectedConversation || null,
      });
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    }
  }

  async function refreshPushNotificationState() {
    if (
      typeof window === "undefined" ||
      !("serviceWorker" in navigator) ||
      !("Notification" in window)
    ) {
      setPushNotificationsEnabled(false);
      return;
    }
    const registration = await navigator.serviceWorker.getRegistration("/");
    const subscription = await registration?.pushManager?.getSubscription?.();
    const enabled =
      Notification.permission === "granted" && Boolean(subscription);
    setPushNotificationsEnabled(enabled);
    return enabled;
  }

  async function handleTogglePushNotifications() {
    if (pushNotificationsBusy) return;
    setPushNotificationsBusy(true);
    setNotice("");
    try {
      if (pushNotificationsEnabled) {
        await disableTeamPushNotifications();
        setPushNotificationsEnabled(false);
        setNotice(t.notificationsDisabled);
      } else {
        await enableTeamPushNotifications({
          vapidPublicKey: process.env.NEXT_PUBLIC_WEB_PUSH_VAPID_PUBLIC_KEY,
          locale: language,
        });
        setPushNotificationsEnabled(true);
        setNotice(t.notificationsEnabled);
      }
    } catch (error) {
      await refreshPushNotificationState().catch(() => {});
      setNotice(getErrorMessage(error, language));
    } finally {
      setPushNotificationsBusy(false);
    }
  }

  function handleAttachmentChange(event) {
    const files = Array.from(event.target.files || []);

    if (!files.length) {
      setAttachmentFiles([]);
      return;
    }

    try {
      validateTeamAttachments(files);
    } catch (error) {
      setNotice(getAttachmentPolicyMessage(error, t, language));
      event.target.value = "";
      setAttachmentFiles([]);
      return;
    }

    setNotice("");
    setAttachmentFiles(files);
  }

  function openDocumentShare() {
    setNotice("");
    setDocumentRecipientUserId(otherMembers[0]?.user_id || "");
    setDocumentShareOpen(true);
  }

  function closeDocumentShare() {
    if (busy === "prepare-document") return;
    setDocumentShareOpen(false);
    setDocumentRecipientUserId("");
    if (documentShareInputRef.current) {
      documentShareInputRef.current.value = "";
    }
  }

  async function handleDocumentShareFile(event) {
    const files = Array.from(event.target.files || []);
    const recipient = otherMembers.find(
      (member) => member.user_id === documentRecipientUserId,
    );

    if (!files.length || !recipient) {
      event.target.value = "";
      return;
    }

    try {
      validateTeamAttachments(files, { documentsOnly: true });
    } catch (error) {
      setNotice(getAttachmentPolicyMessage(error, t, language));
      event.target.value = "";
      return;
    }

    setBusy("prepare-document");
    setNotice("");

    try {
      const conversation = await ensureDmConversation(recipient);
      if (!conversation?.id) {
        throw new Error(t.attachmentFailed);
      }

      selectConversation(conversation.id);
      setAttachmentFiles(files);
      setDocumentShareOpen(false);
      setDocumentRecipientUserId("");
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      event.target.value = "";
      setBusy("");
    }
  }

  function clearAttachment() {
    setAttachmentFiles([]);
    setAttachmentUploadProgress(null);
    if (attachmentInputRef.current) {
      attachmentInputRef.current.value = "";
    }
  }

  function openForwardMessage(message) {
    if (!message || message.pending || message.failed) return;
    setNotice("");
    setForwardSourceMessage(message);
    setForwardRecipientUserIds([]);
  }

  function closeForwardMessage() {
    if (busy === "forward-message") return;
    setForwardSourceMessage(null);
    setForwardRecipientUserIds([]);
  }

  function toggleForwardRecipient(userId) {
    setForwardRecipientUserIds((current) =>
      current.includes(userId)
        ? current.filter((item) => item !== userId)
        : [...current, userId],
    );
  }

  async function handleForwardMessage() {
    if (!organizationId || !forwardSourceMessage?.id) return;
    if (!forwardRecipientUserIds.length) {
      setNotice(t.selectRecipient);
      return;
    }

    setBusy("forward-message");
    setNotice("");
    try {
      const result = await forwardConversationMessage(
        organizationId,
        forwardSourceMessage.id,
        forwardRecipientUserIds,
        { clientMessageId: createClientMessageId() },
      );
      if (!Number(result?.delivered_count || 0)) {
        throw Object.assign(new Error("MESSAGE_FORWARD_FAILED"), {
          code: result?.failures?.[0]?.error || "MESSAGE_FORWARD_FAILED",
          payload: result,
          translationKey: resolveErrorTranslationKey(result, "MESSAGE_FORWARD_FAILED"),
        });
      }
      setNotice(result?.partial ? t.forwardPartial : t.forwardedSuccess);
      setForwardSourceMessage(null);
      setForwardRecipientUserIds([]);
      if (organizationId) {
        await loadConversations(
          organizationId,
          selectedConversationIdRef.current,
          {
            selectFallback: false,
          },
        );
      }
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function handleOpenAttachment(attachment) {
    const downloadUrl = attachment?.download_url || attachment?.downloadUrl;

    if (!downloadUrl) {
      setNotice(t.attachmentFailed);
      return;
    }

    setBusy(`download:${attachment.id}`);
    setNotice("");

    try {
      const { blob, filename } =
        await downloadTeamConversationAttachment(downloadUrl);
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = getAttachmentDisplayName(attachment, language) || filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (error) {
      setNotice(getErrorMessage(error, language));
    } finally {
      setBusy("");
    }
  }

  async function handleSendMessage(event) {
    event.preventDefault();

    const trimmedDraft = messageDraft.trim();
    const conversationId = selectedConversationIdRef.current;

    if (!conversationId || (!trimmedDraft && !attachmentFiles.length)) {
      return;
    }

    const clientMessageId = createClientMessageId();

    if (attachmentFiles.length) {
      const filesToSend = [...attachmentFiles];
      const optimisticMessage = buildOptimisticAttachmentMessage({
        conversationId,
        organizationId,
        currentUserId,
        body: trimmedDraft,
        files: filesToSend,
        clientMessageId,
        language,
      });

      setBusy("send-attachment");
      setNotice("");
      setMessageDraft("");
      setAttachmentFiles([]);
      setAttachmentUploadProgress({ loaded: 0, total: 0, percent: 0 });
      if (attachmentInputRef.current) attachmentInputRef.current.value = "";
      setMessages((current) => [...current, optimisticMessage]);

      try {
        const data = await sendTeamConversationAttachment(
          conversationId,
          filesToSend,
          {
            caption: trimmedDraft,
            clientMessageId,
            onProgress: setAttachmentUploadProgress,
          },
        );

        if (data?.message) {
          upsertMessage(
            {
              ...data.message,
              pending: false,
            },
            clientMessageId,
          );
        }

        if (data?.conversation) {
          upsertConversation(data.conversation);
        }
      } catch (error) {
        markMessageFailed(clientMessageId, getErrorMessage(error, language));
        setMessageDraft(trimmedDraft);
        setAttachmentFiles(filesToSend);
        setNotice(getErrorMessage(error, language));
      } finally {
        setAttachmentUploadProgress(null);
        setBusy("");
      }

      return;
    }

    const optimisticMessage = buildOptimisticTextMessage({
      conversationId,
      organizationId,
      currentUserId,
      body: trimmedDraft,
      clientMessageId,
    });

    setNotice("");
    setMessageDraft("");
    setMessages((current) => [...current, optimisticMessage]);

    if (!realtimeReady) {
      await persistPendingTextMessage({
        conversationId,
        body: trimmedDraft,
        clientMessageId,
        restoreDraftOnFailure: true,
      });
      return;
    }

    try {
      sendRealtimeMessage({
        conversationId,
        body: trimmedDraft,
        clientMessageId,
      });
      scheduleMessageHttpFallback({
        conversationId,
        body: trimmedDraft,
        clientMessageId,
      });
    } catch {
      await persistPendingTextMessage({
        conversationId,
        body: trimmedDraft,
        clientMessageId,
        restoreDraftOnFailure: true,
      });
    }
  }

  useEffect(() => {
    const query = messageSearchQuery.trim();
    if (!organizationId || query.length < 2) {
      setMessageSearchResults([]);
      setMessageSearching(false);
      return undefined;
    }

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => {
      setMessageSearching(true);
      searchOrganizationMessages(organizationId, query, {
        limit: 20,
        signal: controller.signal,
      })
        .then((data) => {
          if (!controller.signal.aborted) {
            setMessageSearchResults(
              Array.isArray(data?.messages) ? data.messages : [],
            );
          }
        })
        .catch((error) => {
          if (!controller.signal.aborted && error?.name !== "AbortError") {
            setNotice(getErrorMessage(error, language));
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setMessageSearching(false);
        });
    }, 350);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [messageSearchQuery, organizationId]);

  useEffect(() => {
    if (!organizationId || !selectedConversationId || !messages.length) return;

    const latestMessageId = messages.reduce((latest, message) => {
      const messageId = Number(message?.id);
      return Number.isSafeInteger(messageId) && messageId > latest
        ? messageId
        : latest;
    }, 0);
    if (latestMessageId < 1) return;

    const previous = lastReadMessageByConversationRef.current.get(
      selectedConversationId,
    );
    if (previous && previous >= latestMessageId) return;
    lastReadMessageByConversationRef.current.set(
      selectedConversationId,
      latestMessageId,
    );
    setUnreadCounts((current) =>
      current.map((entry) =>
        Number(entry.conversation_id) === Number(selectedConversationId)
          ? { ...entry, unread_count: 0, latest_message_id: latestMessageId }
          : entry,
      ),
    );

    updateConversationReadState(selectedConversationId, latestMessageId)
      .then(() => loadUnreadCounts(organizationId))
      .catch(() => {
        lastReadMessageByConversationRef.current.delete(selectedConversationId);
      });
  }, [loadUnreadCounts, organizationId, selectedConversationId, messages]);

  useEffect(() => {
    selectedConversationIdRef.current = selectedConversationId;
  }, [selectedConversationId]);

  useEffect(
    () => () => {
      for (const timeoutId of pendingMessageRetryTimersRef.current.values()) {
        window.clearTimeout(timeoutId);
      }
      pendingMessageRetryTimersRef.current.clear();
    },
    [],
  );

  useEffect(() => {
    if (!currentUserId || !organizationId || !selectedConversationId) return;

    updateWorkspaceCache((current) => ({
      ...current,
      selectedConversationId,
      messagesByConversation: {
        ...(current.messagesByConversation || {}),
        [String(selectedConversationId)]: messages,
      },
    }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUserId, organizationId, selectedConversationId, messages]);

  useEffect(() => {
    if (!organizationId || !isBusinessOrEnterprise) return undefined;

    const listener = (event) => {
      handleRealtimeEvent(event.detail);
    };

    window.addEventListener("team-realtime-event", listener);

    return () => {
      window.removeEventListener("team-realtime-event", listener);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [organizationId, isBusinessOrEnterprise, selectedConversationId]);

  useEffect(() => {
    if (!user || !isBusinessOrEnterprise) {
      setPushNotificationsEnabled(false);
      return;
    }
    void refreshPushNotificationState().catch(() => {});
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        void refreshPushNotificationState().catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id, isBusinessOrEnterprise]);

  useEffect(() => {
    if (accountLoading || !authChecked) {
      return;
    }

    if (!user) {
      setLoading(false);
      setOrganizationDetails(null);
      setConversations([]);
      setSelectedConversationId(null);
      setMessages([]);
      setPresence([]);
      setAttachmentFiles([]);
      setAttachmentUploadProgress(null);
      setForwardSourceMessage(null);
      setForwardRecipientUserIds([]);
      setSubgroupComposerOpen(false);
      setSubgroupName("");
      setSubgroupMemberUserIds([]);
      setNotice("");
      return;
    }

    if (routeMessageId) {
      setHighlightMessageId(routeMessageId);
    } else if (routeCallSessionId) {
      setHighlightMessageId(null);
    }

    void loadAll({ preferredConversationId: routeConversationId || undefined });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    accountLoading,
    authChecked,
    user?.id,
    organizationId,
    isBusinessOrEnterprise,
    routeConversationId,
    routeMessageId,
    routeCallSessionId,
    routeCallAction,
  ]);

  useEffect(() => {
    if (routeCallAction !== "join" || !routeCallSessionId || activeCall) return;
    const routeKey = `${routeCallSessionId}:${routeCallAction}`;
    if (routeCallHandledRef.current === routeKey) return;

    const callMessage = messages.find(
      (message) =>
        String(getMessageCallSessionId(message) || "") ===
        String(routeCallSessionId),
    );
    const callState = getMessageCallState(callMessage) || {
      id: Number(routeCallSessionId),
      conversation_id: routeConversationId || selectedConversationId,
      media_type: routeCallMediaType,
    };

    routeCallHandledRef.current = routeKey;
    void handleJoinCall(routeCallSessionId, callState);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeCall,
    messages,
    routeCallAction,
    routeCallMediaType,
    routeCallSessionId,
    routeConversationId,
    selectedConversationId,
  ]);

  useEffect(() => {
    if (
      accountLoading ||
      !authChecked ||
      !user ||
      !organizationId ||
      !isBusinessOrEnterprise ||
      !routeCallLinkPublicId ||
      activeCall
    ) {
      return;
    }
    const requestedOrganizationId = Number.parseInt(
      routeCallLinkOrganizationId || "",
      10,
    );
    if (
      !Number.isSafeInteger(requestedOrganizationId) ||
      requestedOrganizationId !== Number(organizationId)
    ) {
      setNotice(t.unavailableDescription);
      return;
    }
    const routeKey = `${requestedOrganizationId}:${routeCallLinkPublicId}`;
    if (routeCallLinkHandledRef.current === routeKey) return;
    routeCallLinkHandledRef.current = routeKey;
    void getOrganizationCallLink(
      requestedOrganizationId,
      routeCallLinkPublicId,
    )
      .then((data) => {
        if (!data?.call_link) throw new Error(t.unavailableDescription);
        prepareScheduledCall({
          organizationId: requestedOrganizationId,
          publicId: routeCallLinkPublicId,
          callLink: data.call_link,
        });
      })
      .catch((error) => setNotice(getErrorMessage(error, language)));
  }, [
    accountLoading,
    activeCall,
    authChecked,
    isBusinessOrEnterprise,
    organizationId,
    prepareScheduledCall,
    routeCallLinkOrganizationId,
    routeCallLinkPublicId,
    t.unavailableDescription,
    user,
  ]);

  useEffect(() => {
    if (accountLoading || !authChecked || !user) return undefined;
    if (!organizationId || !isBusinessOrEnterprise) return undefined;

    let focusTimeoutId = null;

    const handleFocus = () => {
      if (focusTimeoutId) {
        window.clearTimeout(focusTimeoutId);
      }

      focusTimeoutId = window.setTimeout(() => {
        void refreshCurrentConversation();
      }, FOCUS_REFRESH_DEBOUNCE_MS);
    };

    window.addEventListener("focus", handleFocus);

    return () => {
      if (focusTimeoutId) {
        window.clearTimeout(focusTimeoutId);
      }
      window.removeEventListener("focus", handleFocus);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    accountLoading,
    authChecked,
    user?.id,
    organizationId,
    isBusinessOrEnterprise,
    selectedConversationId,
  ]);

  useEffect(() => {
    if (!highlightMessageId || !messages.length) return undefined;

    const timeoutId = window.setTimeout(() => {
      const target = document.getElementById(
        `team-message-${highlightMessageId}`,
      );
      target?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);

    return () => window.clearTimeout(timeoutId);
  }, [highlightMessageId, messages]);

  if (accountLoading || !authChecked || loading) {
    return (
      <main className="flex h-dvh overflow-hidden app-page px-3 py-3 md:px-4 md:py-4">
        <div className="mx-auto flex h-full w-full max-w-7xl items-center justify-center rounded-2xl border app-surface-strong p-6 app-text">
          {t.loading}
        </div>
      </main>
    );
  }

  if (!organizationId || !isBusinessOrEnterprise) {
    return (
      <main className="flex h-dvh overflow-hidden app-page px-3 py-3 md:px-4 md:py-4">
        <section className="mx-auto flex max-h-full w-full max-w-4xl flex-col justify-center rounded-2xl border app-surface-strong p-6">
          <button
            type="button"
            onClick={() => router.push("/")}
            className="mb-6 inline-flex items-center gap-2 rounded-2xl border app-surface px-4 py-2 text-sm font-semibold app-text"
          >
            <ArrowLeft className="h-4 w-4" />
            {t.backToDashboard}
          </button>
          <h1 className="text-3xl font-semibold app-text">
            {t.unavailableTitle}
          </h1>
          <p className="mt-3 text-sm app-text-muted">
            {t.unavailableDescription}
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="h-dvh overflow-hidden app-page p-0">
      <div className="mx-auto flex h-full max-w-[1800px] flex-col overflow-hidden">
        {notice ? (
          <div className="shrink-0 rounded-2xl border border-[var(--app-border)] app-surface-strong px-3 py-2 text-sm app-text">
            {notice}
          </div>
        ) : null}

        {callLinkComposerOpen ? (
          <div className="fixed inset-0 z-[165] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="scheduled-calls-title"
              className="flex max-h-[calc(100dvh-2rem)] w-full max-w-3xl flex-col rounded-3xl border app-surface-strong p-5 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2
                    id="scheduled-calls-title"
                    className="text-xl font-semibold app-text"
                  >
                    {t.scheduledCalls}
                  </h2>
                  <p className="mt-1 max-w-2xl text-sm leading-6 app-text-muted">
                    {t.scheduledCallDescription}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setCallLinkComposerOpen(false)}
                  disabled={busy === "create-call-link"}
                  aria-label={t.cancel}
                  className="rounded-xl p-2 app-text-muted hover:bg-[var(--app-surface)] disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-5 grid gap-3 rounded-2xl border app-surface p-4 md:grid-cols-2">
                <label className="md:col-span-2">
                  <span className="text-xs font-semibold app-text-muted">
                    {t.callLinkTitle}
                  </span>
                  <input
                    type="text"
                    maxLength={120}
                    value={callLinkDraft.title}
                    onChange={(event) =>
                      setCallLinkDraft((current) => ({
                        ...current,
                        title: event.target.value,
                      }))
                    }
                    placeholder={t.callLinkTitlePlaceholder}
                    className="mt-1.5 w-full rounded-xl border px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="text-xs font-semibold app-text-muted">
                    {t.callLinkDateTime}
                  </span>
                  <input
                    type="datetime-local"
                    value={callLinkDraft.scheduledStartLocal}
                    onChange={(event) =>
                      setCallLinkDraft((current) => ({
                        ...current,
                        scheduledStartLocal: event.target.value,
                      }))
                    }
                    className="mt-1.5 w-full rounded-xl border px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="text-xs font-semibold app-text-muted">
                    {t.callLinkMedia}
                  </span>
                  <select
                    value={callLinkDraft.mediaType}
                    onChange={(event) =>
                      setCallLinkDraft((current) => ({
                        ...current,
                        mediaType: event.target.value,
                      }))
                    }
                    className="mt-1.5 w-full rounded-xl border px-3 py-2.5 text-sm"
                  >
                    <option value="video">{t.videoCall}</option>
                    <option value="audio">{t.audioCall}</option>
                  </select>
                </label>
                <label>
                  <span className="text-xs font-semibold app-text-muted">
                    {t.callLinkDuration}
                  </span>
                  <input
                    type="number"
                    min="15"
                    max="720"
                    step="15"
                    value={callLinkDraft.durationMinutes}
                    onChange={(event) =>
                      setCallLinkDraft((current) => ({
                        ...current,
                        durationMinutes: Number(event.target.value),
                      }))
                    }
                    className="mt-1.5 w-full rounded-xl border px-3 py-2.5 text-sm"
                  />
                </label>
                <label>
                  <span className="text-xs font-semibold app-text-muted">
                    {t.callLinkMaximum}
                  </span>
                  <input
                    type="number"
                    min="2"
                    max={Math.max(2, Math.min(500, activeMembers.length))}
                    value={callLinkDraft.maxParticipants}
                    onChange={(event) =>
                      setCallLinkDraft((current) => ({
                        ...current,
                        maxParticipants: Number(event.target.value),
                      }))
                    }
                    className="mt-1.5 w-full rounded-xl border px-3 py-2.5 text-sm"
                  />
                  <span className="mt-1 block text-xs app-text-soft">
                    {t.callLinkMemberLimit.replace(
                      "{count}",
                      String(activeMembers.length),
                    )}
                  </span>
                </label>
                <button
                  type="button"
                  onClick={() => void handleCreateCallLink()}
                  disabled={
                    busy === "create-call-link" ||
                    activeMembers.length < 2 ||
                    !callLinkDraft.title.trim() ||
                    !callLinkDraft.scheduledStartLocal ||
                    callLinkDraft.maxParticipants < 2 ||
                    callLinkDraft.maxParticipants > 500 ||
                    callLinkDraft.maxParticipants > activeMembers.length
                  }
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] disabled:opacity-50 md:col-span-2"
                >
                  <CalendarClock className="h-4 w-4" />
                  {busy === "create-call-link"
                    ? t.creatingCallLink
                    : t.createCallLink}
                </button>
              </div>

              <div className="mt-5 min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
                {callLinks.length ? (
                  callLinks.map((callLink) => {
                    const canCancel =
                      canInviteMembers ||
                      String(callLink.created_by_user_id || "") ===
                        String(currentUserId || "");
                    const callLinkEnded =
                      callLink.status === "completed" ||
                      new Date(callLink.scheduled_end_at).getTime() <= Date.now();
                    const canViewRecordings =
                      Boolean(callLink.active_call_session_id) &&
                      (Boolean(callLink.current_user_participated) ||
                        canInviteMembers);
                    return (
                      <article
                        key={callLink.id}
                        className="rounded-2xl border app-surface px-4 py-3"
                      >
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                          <div className="min-w-0 flex-1">
                            <h3 className="truncate text-sm font-semibold app-text">
                              {callLink.title}
                            </h3>
                            <p className="mt-1 text-xs app-text-muted">
                              {t.scheduledFor}: {new Date(
                                callLink.scheduled_start_at,
                              ).toLocaleString(
                                language === "fr" ? "fr-FR" : "en-NG",
                              )} · {callLink.duration_minutes} {getPageRuntimeCopy("projectsTeam", language).minuteUnit} {callLink.max_participants} {t.teamMembers.toLowerCase()}
                            </p>
                          </div>
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => void copyCallLink(callLink)}
                              disabled={callLinkEnded}
                              className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold app-text"
                            >
                              <Clipboard className="h-3.5 w-3.5" />
                              {t.copyCallLink}
                            </button>
                            <button
                              type="button"
                              onClick={() => openScheduledCall(callLink)}
                              disabled={Boolean(activeCall) || callLinkEnded}
                              className="inline-flex items-center gap-1.5 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-xs font-semibold text-[var(--app-button-text)] disabled:opacity-50"
                            >
                              {callLink.media_type === "audio" ? (
                                <Phone className="h-3.5 w-3.5" />
                              ) : (
                                <Video className="h-3.5 w-3.5" />
                              )}
                              {t.joinScheduledCall}
                            </button>
                            {callLinkEnded && canViewRecordings ? (
                              <button
                                type="button"
                                onClick={() =>
                                  void openCallRecordings(
                                    callLink.active_call_session_id,
                                  )
                                }
                                className="inline-flex items-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-semibold app-text"
                              >
                                <Download className="h-3.5 w-3.5" />
                                {t.viewRecordings}
                              </button>
                            ) : null}
                            {canCancel ? (
                              <button
                                type="button"
                                onClick={() => void cancelScheduledCall(callLink)}
                                disabled={
                                  busy === `cancel-call-link:${callLink.id}` ||
                                  callLink.status === "active" || callLinkEnded
                                }
                                aria-label={t.cancelScheduledCall}
                                className="rounded-xl border border-red-400/30 p-2 text-red-600 disabled:opacity-40 dark:text-red-300"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            ) : null}
                          </div>
                        </div>
                      </article>
                    );
                  })
                ) : (
                  <p className="rounded-2xl border app-surface px-4 py-6 text-center text-sm app-text-muted">
                    {t.noScheduledCalls}
                  </p>
                )}
              </div>
            </section>
          </div>
        ) : null}

        {recordingsCallId ? (
          <div className="fixed inset-0 z-[175] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="call-recordings-title"
              className="w-full max-w-xl rounded-3xl border app-surface-strong p-5 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2
                    id="call-recordings-title"
                    className="text-xl font-semibold app-text"
                  >
                    {t.callRecordings}
                  </h2>
                  <p className="mt-1 text-sm leading-6 app-text-muted">
                    {t.recordingRetentionNotice}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setRecordingsCallId(null);
                    setCallRecordings([]);
                  }}
                  disabled={String(busy).startsWith("download-recording:")}
                  aria-label={t.cancel}
                  className="rounded-xl p-2 app-text-muted hover:bg-[var(--app-surface)] disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <div className="mt-5 space-y-2">
                {busy === `load-recordings:${recordingsCallId}` ? (
                  <p className="rounded-2xl border app-surface px-4 py-6 text-center text-sm app-text-muted">
                    {t.loadingRecordings}
                  </p>
                ) : callRecordings.length ? (
                  callRecordings.map((recording) => (
                    <article
                      key={recording.id}
                      className="flex items-center justify-between gap-3 rounded-2xl border app-surface px-4 py-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-semibold app-text">
                          {t.callRecordings} #{recording.id}
                        </p>
                        <p className="mt-1 text-xs app-text-muted">
                          {titleCase(recording.status)}
                          {recording.file_size_bytes
                            ? ` · ${formatFileSize(recording.file_size_bytes)}`
                            : ""}
                        </p>
                      </div>
                      {recording.available_for_download ? (
                        <button
                          type="button"
                          onClick={() => void downloadCallRecording(recording)}
                          disabled={busy === `download-recording:${recording.id}`}
                          className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-xs font-semibold text-[var(--app-button-text)] disabled:opacity-50"
                        >
                          <Download className="h-3.5 w-3.5" />
                          {busy === `download-recording:${recording.id}`
                            ? t.downloadingRecording
                            : t.downloadRecording}
                        </button>
                      ) : null}
                    </article>
                  ))
                ) : (
                  <p className="rounded-2xl border app-surface px-4 py-6 text-center text-sm app-text-muted">
                    {t.noCallRecordings}
                  </p>
                )}
              </div>
            </section>
          </div>
        ) : null}

        {forwardSourceMessage ? (
          <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="forward-message-title"
              className="w-full max-w-lg rounded-3xl border app-surface-strong p-5 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2
                    id="forward-message-title"
                    className="text-lg font-semibold app-text"
                  >
                    {t.forwardMessageTitle}
                  </h2>
                  <p className="mt-1 text-sm app-text-muted">
                    {t.forwardMessageDescription}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={closeForwardMessage}
                  disabled={busy === "forward-message"}
                  aria-label={t.cancelDocument}
                  className="rounded-xl p-2 app-text-muted transition hover:bg-[var(--app-surface)] disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="mt-4 rounded-2xl border app-surface px-3 py-3 text-sm app-text">
                <div className="line-clamp-3 whitespace-pre-wrap">
                  {forwardSourceMessage.body}
                </div>
                {getMessageAttachments(forwardSourceMessage).length ? (
                  <div className="mt-2 text-xs app-text-muted">
                    {getMessageAttachments(forwardSourceMessage).length}{" "}
                    {t.selectedAttachments}
                  </div>
                ) : null}
              </div>

              <h3 className="mt-5 text-sm font-semibold app-text">
                {t.chooseRecipients}
              </h3>
              <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                {otherMembers.map((member) => {
                  const selected = forwardRecipientUserIds.includes(
                    member.user_id,
                  );
                  return (
                    <button
                      key={member.user_id}
                      type="button"
                      onClick={() => toggleForwardRecipient(member.user_id)}
                      disabled={busy === "forward-message"}
                      className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition disabled:opacity-50 ${
                        selected
                          ? "border-[var(--app-button-bg)] bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                          : "app-surface app-text hover:bg-[var(--app-surface)]"
                      }`}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-semibold">
                        {getMemberInitial(member, language)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">
                          {getMemberName(member, language)}
                        </span>
                        <span className="block truncate text-xs opacity-70">
                          {getMemberEmail(member, language)}
                        </span>
                      </span>
                      <span className="text-xs font-semibold">
                        {selected ? "✓" : ""}
                      </span>
                    </button>
                  );
                })}
              </div>

              <button
                type="button"
                onClick={handleForwardMessage}
                disabled={
                  !forwardRecipientUserIds.length || busy === "forward-message"
                }
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Forward className="h-4 w-4" />
                {busy === "forward-message" ? t.forwarding : t.forwardSelected}
              </button>
            </section>
          </div>
        ) : null}

        {subgroupComposerOpen ? (
          <div className="fixed inset-0 z-[160] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="create-subgroup-title"
              className="flex max-h-[min(52rem,calc(100dvh-2rem))] w-full max-w-2xl flex-col rounded-3xl border app-surface-strong p-5 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2
                    id="create-subgroup-title"
                    className="text-lg font-semibold app-text"
                  >
                    {t.createSubgroupTitle}
                  </h2>
                  <p className="mt-1 text-sm app-text-muted">
                    {t.subgroupDescription}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={closeSubgroupComposer}
                  disabled={busy === "create-subgroup"}
                  aria-label={t.cancel}
                  className="rounded-xl p-2 app-text-muted transition hover:bg-[var(--app-surface)] disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <label className="mt-5 block">
                <span className="text-sm font-semibold app-text">
                  {t.subgroupName}
                </span>
                <input
                  type="text"
                  value={subgroupName}
                  onChange={(event) => setSubgroupName(event.target.value)}
                  maxLength={120}
                  disabled={busy === "create-subgroup"}
                  placeholder={t.subgroupNamePlaceholder}
                  className="mt-2 w-full rounded-xl border px-3 py-2.5 text-sm"
                />
              </label>

              <div className="mt-5 flex items-end justify-between gap-3">
                <div>
                  <h3 className="text-sm font-semibold app-text">
                    {t.subgroupMembers}
                  </h3>
                  <p className="mt-0.5 text-xs app-text-muted">
                    {t.subgroupMemberLimit.replace(
                      "{count}",
                      String(subgroupMemberLimit),
                    )}
                  </p>
                </div>
                <span className="rounded-full border app-surface px-2.5 py-1 text-xs font-semibold app-text-muted">
                  {subgroupMemberUserIds.length + 1}/{subgroupMemberLimit}
                </span>
              </div>

              <div className="mt-2 min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
                {otherMembers.map((member) => {
                  const selected = subgroupMemberUserIds.includes(member.user_id);
                  const selectionDisabled =
                    !selected &&
                    subgroupMemberUserIds.length >= subgroupSelectableMemberLimit;
                  return (
                    <button
                      key={`subgroup:${member.user_id}`}
                      type="button"
                      onClick={() => toggleSubgroupMember(member.user_id)}
                      disabled={busy === "create-subgroup" || selectionDisabled}
                      aria-pressed={selected}
                      className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition disabled:cursor-not-allowed disabled:opacity-45 ${
                        selected
                          ? "border-[var(--app-button-bg)] bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                          : "app-surface app-text hover:bg-[var(--app-surface)]"
                      }`}
                    >
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-semibold">
                        {getMemberInitial(member, language)}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">
                          {getMemberName(member, language)}
                        </span>
                        <span className="block truncate text-xs opacity-70">
                          {getMemberEmail(member, language)}
                        </span>
                      </span>
                      {selected ? <Check className="h-4 w-4" /> : null}
                    </button>
                  );
                })}
              </div>

              {subgroupMemberUserIds.length > 0 &&
              subgroupMemberUserIds.length < 2 ? (
                <p className="mt-3 text-xs font-medium text-amber-600 dark:text-amber-300">
                  {t.subgroupMinimum}
                </p>
              ) : null}

              <div className="mt-5 grid gap-2 sm:grid-cols-3">
                <button
                  type="button"
                  onClick={() => void handleCreateSubgroupConversation()}
                  disabled={
                    busy === "create-subgroup" ||
                    !subgroupName.trim() ||
                    subgroupMemberUserIds.length < 2
                  }
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Send className="h-4 w-4" />
                  {busy === "create-subgroup"
                    ? t.creatingSubgroup
                    : t.createSubgroupChat}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void handleCreateSubgroupConversation("audio")
                  }
                  disabled={
                    busy === "create-subgroup" ||
                    Boolean(activeCall) ||
                    !subgroupName.trim() ||
                    subgroupMemberUserIds.length < 2
                  }
                  className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-3 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Phone className="h-4 w-4" />
                  {t.createSubgroupAudioCall}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    void handleCreateSubgroupConversation("video")
                  }
                  disabled={
                    busy === "create-subgroup" ||
                    Boolean(activeCall) ||
                    !subgroupName.trim() ||
                    subgroupMemberUserIds.length < 2
                  }
                  className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-3 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Video className="h-4 w-4" />
                  {t.createSubgroupVideoCall}
                </button>
              </div>
            </section>
          </div>
        ) : null}

        <section className="grid min-h-0 flex-1 lg:grid-cols-[minmax(19rem,30rem)_minmax(0,1fr)]">
          <aside className="min-h-0 overflow-hidden">
            <div className="flex h-full min-h-0 flex-col border-r app-surface-strong">
              <div className="flex h-24 shrink-0 flex-col justify-center border-b border-[var(--app-border)] px-5 py-3">
                <button
                  type="button"
                  onClick={() => router.push("/")}
                  className="mb-1 inline-flex w-fit items-center gap-1.5 text-xs font-semibold app-text-muted transition hover:text-[var(--app-text)]"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  {t.back}
                </button>
                <h1 className="text-2xl font-semibold tracking-tight app-text">
                  {t.businessChats}
                </h1>
                <p className="mt-1 truncate text-xs app-text-muted">
                  {organizationName}
                </p>
              </div>

              <div className="min-h-0 flex-1 space-y-1 overflow-y-auto px-2 py-3">
                <label className="mb-2 flex items-center gap-2 rounded-xl border app-surface px-3 py-2">
                  <Search
                    className="h-4 w-4 shrink-0 app-text-soft"
                    aria-hidden="true"
                  />
                  <span className="sr-only">{t.searchMessages}</span>
                  <input
                    type="search"
                    value={messageSearchQuery}
                    onChange={(event) =>
                      setMessageSearchQuery(event.target.value)
                    }
                    placeholder={t.searchPlaceholder}
                    className="min-w-0 flex-1 bg-transparent text-sm outline-none app-text placeholder:app-text-soft"
                  />
                </label>

                {messageSearchQuery.trim().length >= 2 ? (
                  <section
                    aria-label={t.searchMessages}
                    className="mb-3 rounded-xl border app-surface p-2"
                  >
                    {messageSearching ? (
                      <p className="px-2 py-2 text-xs app-text-muted">
                        {t.searchingMessages}
                      </p>
                    ) : messageSearchResults.length ? (
                      <div className="space-y-1">
                        {messageSearchResults.map((result) => (
                          <button
                            key={`search:${result.id}`}
                            type="button"
                            onClick={() => {
                              selectConversation(result.conversation_id);
                              setHighlightMessageId(String(result.id));
                            }}
                            className="block w-full rounded-lg px-2 py-2 text-left transition hover:bg-[var(--app-surface-strong)]"
                          >
                            <span className="block truncate text-xs font-semibold app-text">
                              {getConversationTitle(
                                conversations.find(
                                  (conversation) =>
                                    conversation.id === result.conversation_id,
                                ),
                              )}
                            </span>
                            <span className="mt-0.5 block line-clamp-2 text-xs app-text-muted">
                              {result.body}
                            </span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="px-2 py-2 text-xs app-text-muted">
                        {t.noSearchResults}
                      </p>
                    )}
                  </section>
                ) : null}

                {groupConversation ? (
                  <button
                    type="button"
                    onClick={handleOpenGroupConversation}
                    className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition ${
                      selectedConversationId === groupConversation.id
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : "app-text hover:bg-[var(--app-surface)]"
                    }`}
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border app-surface-strong text-base font-semibold">
                      {getTextInitial(organizationName)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">
                        {getConversationTitle(groupConversation)}
                      </span>
                      <span className="mt-0.5 block truncate text-xs opacity-70">
                        {t.businessGroupChat}
                      </span>
                    </span>
                    {getUnreadCount(groupConversation.id) > 0 ? (
                      <span
                        className="min-w-6 rounded-full bg-rose-500 px-1.5 py-0.5 text-center text-[11px] font-bold text-white"
                        aria-label={`${getUnreadCount(groupConversation.id)} ${t.unreadMessages}`}
                      >
                        {Math.min(getUnreadCount(groupConversation.id), 99)}
                      </span>
                    ) : null}
                  </button>
                ) : isOwner ? (
                  <button
                    type="button"
                    onClick={handleCreateGroupConversation}
                    disabled={busy === "create-group"}
                    className="flex w-full items-center gap-3 rounded-xl border border-[var(--app-border)] bg-[var(--app-button-bg)] px-3 py-3 text-left text-[var(--app-button-text)] shadow-sm transition hover:scale-[1.01] hover:shadow-lg disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border app-surface-strong text-base font-semibold">
                      {getTextInitial(organizationName)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">
                        {busy === "create-group"
                          ? t.creating
                          : t.createGroupChat}
                      </span>
                      <span className="mt-0.5 block truncate text-xs opacity-75">
                        {t.businessGroupChat}
                      </span>
                    </span>
                  </button>
                ) : null}

                <div className="mt-3 flex items-center justify-between gap-2 px-2 pb-1 pt-2">
                  <span className="text-[11px] font-semibold uppercase tracking-[0.12em] app-text-soft">
                    {t.subgroups}
                  </span>
                  <button
                    type="button"
                    onClick={openSubgroupComposer}
                    disabled={otherMembers.length < 2}
                    className="inline-flex items-center gap-1 rounded-lg border app-surface px-2 py-1 text-xs font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-45"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {t.createSubgroup}
                  </button>
                </div>

                {subgroupConversations.map((conversation) => (
                  <button
                    key={`subgroup-conversation:${conversation.id}`}
                    type="button"
                    onClick={() => {
                      setDocumentShareOpen(false);
                      conversationSelectionRequestRef.current += 1;
                      void selectConversation(conversation.id);
                    }}
                    className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition ${
                      selectedConversationId === conversation.id
                        ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                        : "app-text hover:bg-[var(--app-surface)]"
                    }`}
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border app-surface-strong text-base font-semibold">
                      <Users className="h-5 w-5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-semibold">
                        {getConversationTitle(conversation)}
                      </span>
                      <span className="mt-0.5 block truncate text-xs opacity-70">
                        {getConversationMemberIds(conversation).length} {t.teamMembers}
                      </span>
                    </span>
                    {getUnreadCount(conversation.id) > 0 ? (
                      <span
                        className="min-w-6 rounded-full bg-rose-500 px-1.5 py-0.5 text-center text-[11px] font-bold text-white"
                        aria-label={`${getUnreadCount(conversation.id)} ${t.unreadMessages}`}
                      >
                        {Math.min(getUnreadCount(conversation.id), 99)}
                      </span>
                    ) : null}
                  </button>
                ))}

                {otherMembers.length ? (
                  otherMembers.map((member) => {
                    const status = getMemberPresenceStatus(member.user_id);
                    const memberConversation = conversations.find(
                      (conversation) => {
                        if (conversation.type !== "dm") return false;
                        const ids = getConversationMemberIds(conversation);
                        return (
                          ids.includes(member.user_id) &&
                          ids.includes(currentUserId)
                        );
                      },
                    );

                    return (
                      <button
                        key={member.user_id}
                        type="button"
                        onClick={() => handleMessageMember(member)}
                        disabled={busy === `message:${member.user_id}`}
                        className={`flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition disabled:cursor-wait disabled:opacity-60 ${
                          memberConversation?.id === selectedConversationId
                            ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                            : "app-text hover:bg-[var(--app-surface)]"
                        }`}
                      >
                        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border app-surface-strong text-base font-semibold">
                          {getMemberInitial(member, language)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-semibold">
                            {getMemberName(member, language)}
                          </span>
                          <span className="mt-0.5 block truncate text-xs opacity-70">
                            {getMemberEmail(member, language)}
                          </span>
                        </span>
                        {memberConversation &&
                        getUnreadCount(memberConversation.id) > 0 ? (
                          <span
                            className="min-w-6 rounded-full bg-rose-500 px-1.5 py-0.5 text-center text-[11px] font-bold text-white"
                            aria-label={`${getUnreadCount(memberConversation.id)} ${t.unreadMessages}`}
                          >
                            {Math.min(
                              getUnreadCount(memberConversation.id),
                              99,
                            )}
                          </span>
                        ) : null}
                        <span
                          className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                            status === "online"
                              ? "bg-emerald-400"
                              : status === "in_call"
                                ? "bg-purple-400"
                                : "bg-neutral-500"
                          }`}
                          aria-label={t[status] || titleCase(status)}
                          title={t[status] || titleCase(status)}
                        />
                      </button>
                    );
                  })
                ) : (
                  <p className="rounded-xl px-4 py-5 text-sm app-text-muted">
                    {t.noMembers}
                  </p>
                )}
              </div>
            </div>
          </aside>

          <section className="flex min-h-0 flex-col app-surface-strong">
            <div className="flex h-24 shrink-0 items-center justify-between gap-3 border-b border-[var(--app-border)] px-4 py-3">
              {selectedConversation ? (
                <div className="flex min-w-0 items-center gap-3">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border app-surface text-sm font-semibold app-text">
                    {getTextInitial(getConversationTitle(selectedConversation))}
                  </span>
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-semibold app-text">
                      {getConversationTitle(selectedConversation)}
                    </h2>
                    <p className="mt-0.5 truncate text-xs app-text-soft">
                      {selectedConversation.type === "dm"
                        ? t.directMessage
                        : t.groupChat}
                    </p>
                  </div>
                </div>
              ) : (
                <span aria-hidden="true" />
              )}

              <div className="flex shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={openCallLinkComposer}
                  title={t.scheduleCall}
                  aria-label={t.scheduleCall}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                >
                  <CalendarClock className="h-4 w-4" />
                  <span className="hidden xl:inline">{t.scheduleCall}</span>
                </button>
                {selectedConversation ? (
                  activeCall ? (
                    <button
                      type="button"
                      onClick={restoreCall}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                    >
                      <Video className="h-4 w-4" />
                      <span className="hidden sm:inline">{t.returnToCall}</span>
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() =>
                          handleStartCurrentConversationCall("audio")
                        }
                        title={t.startAudioCall}
                        aria-label={t.startAudioCall}
                        className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                      >
                        <Phone className="h-4 w-4" />
                        <span className="hidden lg:inline">{t.audioCall}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() =>
                          handleStartCurrentConversationCall("video")
                        }
                        title={t.startVideoCall}
                        aria-label={t.startVideoCall}
                        className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)]"
                      >
                        <Video className="h-4 w-4" />
                        <span className="hidden lg:inline">{t.videoCall}</span>
                      </button>
                    </>
                  )
                ) : null}

                <button
                  type="button"
                  onClick={() => void handleTogglePushNotifications()}
                  disabled={pushNotificationsBusy}
                  title={
                    pushNotificationsEnabled
                      ? t.disableNotifications
                      : t.enableNotifications
                  }
                  aria-label={
                    pushNotificationsEnabled
                      ? t.disableNotifications
                      : t.enableNotifications
                  }
                  aria-pressed={pushNotificationsEnabled}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border app-surface px-3 py-2 text-sm font-semibold app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-wait disabled:opacity-70"
                >
                  {pushNotificationsEnabled ? (
                    <BellRing className="h-4 w-4" />
                  ) : (
                    <Bell className="h-4 w-4" />
                  )}
                  <span className="hidden xl:inline">
                    {pushNotificationsBusy
                      ? pushNotificationsEnabled
                        ? t.disablingNotifications
                        : t.enablingNotifications
                      : pushNotificationsEnabled
                        ? t.disableNotifications
                        : t.enableNotifications}
                  </span>
                </button>

                <button
                  type="button"
                  onClick={() => router.push("/settings/team")}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-3 py-2 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] hover:shadow-lg"
                >
                  <Settings className="h-4 w-4" />
                  {t.settings}
                </button>
              </div>
            </div>

            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {selectedConversation && messages.length ? (
                  messages.map((message) => {
                    const isMine = message.sender_user_id === currentUserId;
                    const callSessionId = getMessageCallSessionId(message);
                    const messageCallState = getMessageCallState(message);
                    const callHasEnded = isTerminalCallState(messageCallState);
                    const isCallEvent = message.message_type === "call_event";
                    const attachments = getMessageAttachments(message);
                    const isAttachmentMessage =
                      message.message_type === "attachment" ||
                      attachments.length > 0;
                    const isHighlighted =
                      highlightMessageId &&
                      String(message.id) === String(highlightMessageId);

                    return (
                      <div
                        id={`team-message-${message.id}`}
                        key={message.id}
                        className={`flex scroll-mt-24 ${isMine ? "justify-end" : "justify-start"}`}
                      >
                        <div
                          className={`max-w-[85%] rounded-2xl border px-3.5 py-2.5 text-sm transition ${
                            isHighlighted
                              ? "ring-2 ring-[var(--app-button-bg)] ring-offset-2 ring-offset-[var(--app-bg)]"
                              : ""
                          } ${
                            isMine
                              ? "bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                              : "app-surface app-text"
                          }`}
                        >
                          <div
                            className={`mb-1 text-[11px] font-semibold ${
                              isMine ? "opacity-70" : "app-text-soft"
                            }`}
                          >
                            {isMine
                              ? t.you
                              : getMemberLabel(message.sender_user_id)}
                          </div>
                          {isForwardedMessage(message) ? (
                            <div
                              className={`mb-1 inline-flex items-center gap-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${
                                isMine ? "opacity-70" : "app-text-soft"
                              }`}
                            >
                              <Forward className="h-3 w-3" />
                              {t.forwardedLabel}
                            </div>
                          ) : null}
                          {message.body ? (
                            <div className="whitespace-pre-wrap leading-6">
                              {message.body}
                            </div>
                          ) : null}
                          {isAttachmentMessage && attachments.length ? (
                            <div className="space-y-2">
                              {attachments.map(
                                (attachment, attachmentIndex) => (
                                  <AttachmentCard
                                    key={
                                      attachment.id ||
                                      `${message.id}:${attachmentIndex}`
                                    }
                                    attachment={attachment}
                                    isMine={isMine}
                                    t={t}
                                    onOpen={handleOpenAttachment}
                                  />
                                ),
                              )}
                            </div>
                          ) : null}
                          {isMine && (message.pending || message.failed) ? (
                            <div
                              className={`mt-2 text-[11px] font-semibold ${
                                message.failed
                                  ? "text-red-300"
                                  : isMine
                                    ? "opacity-70"
                                    : "app-text-soft"
                              }`}
                            >
                              {message.failed
                                ? message.error || t.messageFailed
                                : t.messagePending}
                            </div>
                          ) : null}
                          {isCallEvent && callSessionId ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                              <button
                                type="button"
                                onClick={() =>
                                  handleJoinCall(callSessionId, messageCallState)
                                }
                                disabled={
                                  callHasEnded ||
                                  Boolean(
                                    activeCall &&
                                    String(activeCall.call?.id || "") !==
                                      String(callSessionId),
                                  )
                                }
                                className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
                                  isMine
                                    ? "border-current/20 text-[var(--app-button-text)]"
                                    : "app-surface app-text"
                                }`}
                              >
                                {messageCallState?.media_type === "audio" ? (
                                  <Phone className="h-3.5 w-3.5" />
                                ) : (
                                  <Video className="h-3.5 w-3.5" />
                                )}
                                {callHasEnded ? t.callEnded : t.joinCall}
                              </button>
                              {callHasEnded ? (
                                <button
                                  type="button"
                                  onClick={() =>
                                    void openCallRecordings(callSessionId)
                                  }
                                  className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
                                    isMine
                                      ? "border-current/20 text-[var(--app-button-text)]"
                                      : "app-surface app-text"
                                  }`}
                                >
                                  <Download className="h-3.5 w-3.5" />
                                  {t.viewRecordings}
                                </button>
                              ) : null}
                            </div>
                          ) : null}
                          {!isCallEvent &&
                          !message.pending &&
                          !message.failed ? (
                            <button
                              type="button"
                              onClick={() => openForwardMessage(message)}
                              className={`mt-3 inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-semibold transition ${
                                isMine
                                  ? "border-current/20 text-[var(--app-button-text)]"
                                  : "app-surface app-text"
                              }`}
                            >
                              <Forward className="h-3.5 w-3.5" />
                              {t.forward}
                            </button>
                          ) : null}
                        </div>
                      </div>
                    );
                  })
                ) : selectedConversation ? (
                  <div className="rounded-xl border app-surface p-4 text-sm app-text-muted">
                    {t.noMessagesOrCalls}
                  </div>
                ) : (
                  <div className="flex h-full min-h-[20rem] items-center justify-center">
                    {documentShareOpen ? (
                      <section className="w-full max-w-md rounded-3xl border app-surface p-5 shadow-xl">
                        {otherMembers.length ? (
                          <>
                            <div className="flex items-start justify-between gap-4">
                              <div>
                                <h2 className="text-lg font-semibold app-text">
                                  {t.chooseRecipient}
                                </h2>
                                <p className="mt-1 text-sm app-text-muted">
                                  {t.sendDocumentDescription}
                                </p>
                              </div>
                              <button
                                type="button"
                                onClick={closeDocumentShare}
                                aria-label={t.cancelDocument}
                                className="rounded-xl p-2 app-text-muted transition hover:bg-[var(--app-surface-strong)] hover:text-[var(--app-text)]"
                              >
                                <X className="h-4 w-4" />
                              </button>
                            </div>

                            <div className="mt-4 max-h-56 space-y-1 overflow-y-auto">
                              {otherMembers.map((member) => (
                                <button
                                  key={member.user_id}
                                  type="button"
                                  onClick={() =>
                                    setDocumentRecipientUserId(member.user_id)
                                  }
                                  className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                                    documentRecipientUserId === member.user_id
                                      ? "border-[var(--app-button-bg)] bg-[var(--app-button-bg)] text-[var(--app-button-text)]"
                                      : "app-surface-strong app-text hover:bg-[var(--app-surface)]"
                                  }`}
                                >
                                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border text-sm font-semibold">
                                    {getMemberInitial(member, language)}
                                  </span>
                                  <span className="min-w-0">
                                    <span className="block truncate text-sm font-semibold">
                                      {getMemberName(member, language)}
                                    </span>
                                    <span className="block truncate text-xs opacity-70">
                                      {getMemberEmail(member, language)}
                                    </span>
                                  </span>
                                </button>
                              ))}
                            </div>

                            <button
                              type="button"
                              onClick={() =>
                                documentShareInputRef.current?.click()
                              }
                              disabled={
                                !documentRecipientUserId ||
                                busy === "prepare-document"
                              }
                              className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-3 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <FileText className="h-4 w-4" />
                              {busy === "prepare-document"
                                ? t.preparingDocument
                                : t.chooseDocument}
                            </button>
                          </>
                        ) : (
                          <div className="text-center">
                            <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full app-surface-strong">
                              <FileText className="h-6 w-6 app-text-muted" />
                            </div>
                            <h2 className="mt-4 text-lg font-semibold app-text">
                              {t.inviteMembersTitle}
                            </h2>
                            <p className="mt-2 text-sm app-text-muted">
                              {canInviteMembers
                                ? t.inviteMembersDescription
                                : t.contactAdminDescription}
                            </p>
                            <div
                              className={`mt-5 grid gap-2 ${
                                canInviteMembers ? "sm:grid-cols-2" : ""
                              }`}
                            >
                              <button
                                type="button"
                                onClick={closeDocumentShare}
                                className="rounded-xl border app-surface-strong px-4 py-2.5 text-sm font-semibold app-text"
                              >
                                {t.cancelDocument}
                              </button>
                              {canInviteMembers ? (
                                <button
                                  type="button"
                                  onClick={() => router.push("/settings/team")}
                                  className="rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)]"
                                >
                                  {t.inviteMembers}
                                </button>
                              ) : null}
                            </div>
                          </div>
                        )}
                      </section>
                    ) : (
                      <button
                        type="button"
                        onClick={openDocumentShare}
                        className="group flex flex-col items-center gap-3 app-text transition hover:scale-[1.03]"
                      >
                        <span className="flex h-16 w-16 items-center justify-center rounded-full app-surface">
                          <FileText className="h-7 w-7 app-text-muted transition group-hover:text-[var(--app-text)]" />
                        </span>
                        <span className="text-sm font-medium">
                          {t.sendDocument}
                        </span>
                      </button>
                    )}
                  </div>
                )}
              </div>

              <input
                ref={attachmentInputRef}
                type="file"
                accept={TEAM_ATTACHMENT_ACCEPT}
                multiple
                onChange={handleAttachmentChange}
                className="hidden"
              />
              <input
                ref={documentShareInputRef}
                type="file"
                accept={TEAM_DOCUMENT_ATTACHMENT_ACCEPT}
                multiple
                onChange={handleDocumentShareFile}
                className="hidden"
              />

              {selectedConversation ? (
                <form
                  onSubmit={handleSendMessage}
                  className="shrink-0 space-y-2 border-t border-[var(--app-border)] p-3"
                >
                  {attachmentFiles.length ? (
                    <div className="flex items-center gap-2 rounded-xl border app-surface px-3 py-2 text-xs app-text">
                      <Paperclip className="h-4 w-4 shrink-0 app-text-muted" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-semibold">
                          {attachmentFiles.length === 1
                            ? attachmentFiles[0].name
                            : `${attachmentFiles.length} ${t.selectedAttachments}`}
                        </div>
                        <div className="app-text-soft">
                          {attachmentFiles.length}/{TEAM_ATTACHMENT_MAX_FILES} ·{" "}
                          {formatFileSize(
                            attachmentFiles.reduce(
                              (total, file) => total + Number(file.size || 0),
                              0,
                            ),
                          )}
                          {attachmentUploadProgress?.percent != null
                            ? ` · ${t.uploadProgress} ${attachmentUploadProgress.percent}%`
                            : ""}
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={clearAttachment}
                        disabled={busy === "send-attachment"}
                        aria-label={t.removeAttachment}
                        className="rounded-lg border app-surface-strong p-1.5 app-text-soft transition hover:text-[var(--app-text)] disabled:opacity-50"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : null}

                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => attachmentInputRef.current?.click()}
                      disabled={
                        !selectedConversation || busy === "send-attachment"
                      }
                      className="inline-flex items-center justify-center rounded-xl border app-surface px-3 py-2.5 app-text transition hover:bg-[var(--app-button-bg)] hover:text-[var(--app-button-text)] disabled:cursor-not-allowed disabled:opacity-50"
                      aria-label={t.attachFile}
                      title={t.attachFile}
                    >
                      <Paperclip className="h-4 w-4" />
                    </button>

                    <input
                      type="text"
                      value={messageDraft}
                      onChange={(event) => setMessageDraft(event.target.value)}
                      placeholder={
                        attachmentFiles.length
                          ? `${t.messagePlaceholder} (${attachmentFiles.length} attachments)`
                          : t.messagePlaceholder
                      }
                      disabled={
                        !selectedConversation || busy === "send-attachment"
                      }
                      className="min-w-0 flex-1 rounded-xl border px-4 py-2.5 text-sm"
                    />
                    <button
                      type="submit"
                      disabled={
                        !selectedConversation ||
                        (!messageDraft.trim() && !attachmentFiles.length) ||
                        busy === "send-attachment"
                      }
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--app-button-bg)] px-4 py-2.5 text-sm font-semibold text-[var(--app-button-text)] transition hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Send className="h-4 w-4" />
                      <span className="hidden sm:inline">
                        {busy === "send-attachment"
                          ? t.uploadingAttachment
                          : t.send}
                      </span>
                    </button>
                  </div>
                </form>
              ) : null}
            </div>
          </section>
        </section>
      </div>
    </main>
  );
}
