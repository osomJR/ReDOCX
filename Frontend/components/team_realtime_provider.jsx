"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { MessageCircle, PhoneCall, PhoneOff, X } from "lucide-react";
import { useRouter } from "next/navigation";

import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import TeamCallRoom from "@/components/team_call_room";
import {
  TEAM_REALTIME_ACK_TIMEOUT_MS,
  advanceRealtimeCursor,
  loadRealtimeOutbox,
  readRealtimeCursor,
  reconnectDelayWithJitter,
  saveRealtimeOutbox,
  validateRealtimeServerEvent,
  withRealtimeContract,
} from "@/lib/team_realtime_contract";
import {
  getAccountRealtimeWebSocketAuthToken,
  getAccountRealtimeWebSocketUrl,
  getOrganizationRealtimeWebSocketAuthToken,
  getOrganizationRealtimeWebSocketUrl,
  replayOrganizationMessages,
  sendConversationMessage,
  startConversationCall,
  joinCall,
  declineCall,
  reportCallTelemetry,
  endCall,
  leaveCall,
} from "@/lib/api_client";

const NOTIFICATION_VISIBLE_MS = 10_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;
const PING_INTERVAL_MS = 25_000;
const REALTIME_CONNECT_DELAY_MS = 750;
const MAX_SEEN_REALTIME_EVENT_IDS = 2_000;

const TeamRealtimeContext = createContext({
  activeCall: null,
  callMinimized: false,
  connectionState: "idle",
  realtimeReady: false,
  activateCall: () => {
    throw new Error("Call management is not ready.");
  },
  prepareOutgoingCall: () => {
    throw new Error("Call management is not ready.");
  },
  prepareIncomingCall: () => {
    throw new Error("Call management is not ready.");
  },
  declineIncomingCall: async () => {},
  endActiveCall: async () => {},
  leaveActiveCall: async () => {},
  minimizeCall: () => {},
  restoreCall: () => {},
  sendRealtimeEvent: () => {
    throw new Error("Realtime connection is not ready.");
  },
  sendRealtimeMessage: () => {
    throw new Error("Realtime connection is not ready.");
  },
});

export function useTeamRealtime() {
  return useContext(TeamRealtimeContext);
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `client:${crypto.randomUUID()}`;
  }

  return `client:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

function isDuplicateRealtimeEvent(event, seenEventIds) {
  const eventId = String(event?.event_id || "").trim();
  if (!eventId) return false;
  if (seenEventIds.has(eventId)) return true;

  seenEventIds.add(eventId);
  if (seenEventIds.size > MAX_SEEN_REALTIME_EVENT_IDS) {
    const oldestEventId = seenEventIds.values().next().value;
    if (oldestEventId) seenEventIds.delete(oldestEventId);
  }

  return false;
}

function parsePositiveInteger(value, label) {
  const parsed = Number.parseInt(String(value ?? ""), 10);

  if (!Number.isFinite(parsed) || parsed < 1) {
    throw new Error(`${label} is required.`);
  }

  return parsed;
}

function normalizeMessageBody(value) {
  const normalized = String(value ?? "").trim();

  if (!normalized) {
    throw new Error("Message body is required.");
  }

  return normalized;
}

function getCallErrorMessage(error) {
  return (
    error?.payload?.detail?.message ||
    error?.payload?.detail?.error ||
    error?.message ||
    "Could not update the active call."
  );
}

const copy = {
  en: {
    directTitle: "New direct message",
    groupTitle: "New group message",
    callTitle: "Incoming call",
    fromLabel: "From",
    groupLabel: "Group",
    callFromLabel: "Call from",
    openMessage: "Open message",
    openCall: "Open call",
    joinCall: "Join",
    declineCall: "Decline",
    decliningCall: "Declining…",
    close: "Close notification",
    fallbackSender: "Team member",
    fallbackGroup: "Team group chat",
    fallbackCall: "Team call",
    directCallBody: "A direct video call has started.",
    groupCallBody: "A group video call has started.",
    directAudioCallBody: "A direct audio call has started.",
    groupAudioCallBody: "A group audio call has started.",
    attachmentBody: "Sent an attachment.",
    memberLeftTitle: "Team member left",
    ownershipTransferredTitle: "Ownership changed",
    memberLeftBody: "A team member left the subscription.",
    ownerChangedBody: "The organization owner has changed.",
    viewTeam: "View team",
  },
  fr: {
    directTitle: "Nouveau message direct",
    groupTitle: "Nouveau message de groupe",
    callTitle: "Appel entrant",
    fromLabel: "De",
    groupLabel: "Groupe",
    callFromLabel: "Appel de",
    openMessage: "Ouvrir le message",
    openCall: "Ouvrir l’appel",
    joinCall: "Rejoindre",
    declineCall: "Refuser",
    decliningCall: "Refus…",
    close: "Fermer la notification",
    fallbackSender: "Membre de l’équipe",
    fallbackGroup: "Groupe de l’équipe",
    fallbackCall: "Appel d’équipe",
    directCallBody: "Un appel vidéo direct a commencé.",
    groupCallBody: "Un appel vidéo de groupe a commencé.",
    directAudioCallBody: "Un appel audio direct a commencé.",
    groupAudioCallBody: "Un appel audio de groupe a commencé.",
    attachmentBody: "A envoyé une pièce jointe.",
    memberLeftTitle: "Membre parti",
    ownershipTransferredTitle: "Propriété modifiée",
    memberLeftBody: "Un membre a quitté le forfait.",
    ownerChangedBody: "Le propriétaire de l’organisation a changé.",
    viewTeam: "Voir l’équipe",
  },
};

function truncateText(value = "", maxLength = 120) {
  const normalized = String(value || "")
    .replace(/\s+/g, " ")
    .trim();

  if (normalized.length <= maxLength) return normalized;

  return `${normalized.slice(0, maxLength - 1)}…`;
}

function dispatchTeamRealtimeEvent(event) {
  if (typeof window === "undefined") return;

  window.dispatchEvent(
    new CustomEvent("team-realtime-event", {
      detail: event,
    }),
  );
}

function dispatchTeamInvitationRealtimeEvent(event) {
  if (typeof window === "undefined") return;

  window.dispatchEvent(
    new CustomEvent("team-invitation-realtime-event", {
      detail: event,
    }),
  );
}

function clearRevokedOrganizationCache(userId, organizationId) {
  if (typeof window === "undefined" || !userId || !organizationId) return;

  window.sessionStorage.removeItem(
    `redocx:team-messages:v1:${userId}:${organizationId}`,
  );
}

function shouldRefreshAccountFromRealtime(event) {
  const eventType = String(event?.type || "").toLowerCase();

  if (!eventType) return false;

  return (
    eventType === "account.updated" ||
    eventType === "account.refresh" ||
    eventType === "user.updated" ||
    eventType === "user.profile.updated" ||
    eventType === "user.entitlement.updated" ||
    eventType === "subscription.updated" ||
    eventType.startsWith("account.") ||
    eventType.startsWith("user.") ||
    eventType.startsWith("subscription.") ||
    eventType === "organization.updated" ||
    eventType === "organization.access.revoked" ||
    eventType.startsWith("organization.invitation.") ||
    eventType.startsWith("organization.member.")
  );
}

function getEventSenderId(event) {
  return (
    event?.message?.sender_user_id ||
    event?.call?.created_by_user_id ||
    event?.sender?.id ||
    event?.sender?.user_id ||
    event?.user?.id ||
    event?.user?.user_id ||
    ""
  );
}

function getConversationUrl({ conversationId, messageId, callSessionId }) {
  const params = new URLSearchParams();

  if (conversationId) params.set("conversationId", String(conversationId));
  if (messageId) params.set("messageId", String(messageId));
  if (callSessionId) params.set("callSessionId", String(callSessionId));

  const query = params.toString();
  return query ? `/team?${query}` : "/team";
}

function buildNotificationFromEvent(event, currentUserId, t) {
  if (!event || event.organization_id == null) return null;

  const senderId = getEventSenderId(event);
  if (senderId && senderId === currentUserId) return null;

  if (
    event.type === "message.created" &&
    ["text", "attachment"].includes(event.message?.message_type)
  ) {
    const message = event.message;
    const conversation = event.conversation || {};
    const attachments = Array.isArray(message.metadata?.attachments)
      ? message.metadata.attachments
      : [];
    const firstAttachment = attachments[0];
    const attachmentName =
      firstAttachment?.original_filename || firstAttachment?.filename || "";

    return {
      id: `message:${message.id}:${Date.now()}`,
      kind: "message",
      conversationId: message.conversation_id,
      messageId: message.id,
      clientMessageId:
        event.client_message_id ||
        message.client_message_id ||
        message.metadata?.client_message_id ||
        "",
      conversationType: conversation.type || "dm",
      conversationName: conversation.name || "",
      senderName: event.sender?.name || event.sender?.email || "",
      body:
        message.message_type === "attachment"
          ? message.body || attachmentName || t.attachmentBody
          : message.body || "",
      targetUrl: getConversationUrl({
        conversationId: message.conversation_id,
        messageId: message.id,
      }),
    };
  }

  if (event.type === "organization.member.left") {
    const member = event.member || {};
    const actor = event.actor || {};
    const memberName =
      member.name ||
      member.email ||
      actor.name ||
      actor.email ||
      t.fallbackSender;

    return {
      id: `member-left:${event.organization_id}:${member.user_id || memberName}:${Date.now()}`,
      kind: "team",
      title: t.memberLeftTitle,
      senderName: memberName,
      body: `${memberName} ${t.memberLeftBody}`,
      targetUrl: "/team",
    };
  }

  if (event.type === "organization.ownership.transferred") {
    const newOwner = event.new_owner || {};
    const ownerName =
      newOwner.name ||
      newOwner.email ||
      event.new_owner_user_id ||
      t.fallbackSender;

    return {
      id: `ownership:${event.organization_id}:${event.new_owner_user_id}:${Date.now()}`,
      kind: "team",
      title: t.ownershipTransferredTitle,
      senderName: ownerName,
      body: `${t.ownerChangedBody} ${ownerName}`,
      targetUrl: "/team",
    };
  }


  return null;
}

function reconcileMessageNotification(event, setActiveNotification) {
  if (event?.type !== "message.persisted" || !event?.client_message_id) {
    return;
  }

  const persistedMessage = event.message;
  if (!persistedMessage?.conversation_id || !persistedMessage?.id) {
    return;
  }

  setActiveNotification((current) => {
    if (!current || current.kind !== "message") {
      return current;
    }

    if (current.clientMessageId !== event.client_message_id) {
      return current;
    }

    return {
      ...current,
      messageId: persistedMessage.id,
      targetUrl: getConversationUrl({
        conversationId: persistedMessage.conversation_id,
        messageId: persistedMessage.id,
      }),
    };
  });
}

export default function TeamRealtimeProvider({ children }) {
  const router = useRouter();
  const { language } = useLanguage();
  const { user, entitlement, authChecked, loading, reloadAccount } =
    useAccount();
  const t = copy[language] || copy.en;

  const [activeNotification, setActiveNotification] = useState(null);
  const [incomingCall, setIncomingCall] = useState(null);
  const [incomingCallBusy, setIncomingCallBusy] = useState(false);
  const [activeCall, setActiveCall] = useState(null);
  const [callMinimized, setCallMinimized] = useState(false);
  const [callError, setCallError] = useState("");
  const [connectionState, setConnectionState] = useState("idle");
  const activeCallRef = useRef(null);
  const leaveCallPromiseRef = useRef(null);
  const endCallPromiseRef = useRef(null);
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const notificationTimerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const closedByCleanupRef = useRef(false);
  const organizationAuthFailedRef = useRef(false);
  const accountSocketRef = useRef(null);
  const accountReconnectTimerRef = useRef(null);
  const accountPingTimerRef = useRef(null);
  const accountReconnectAttemptRef = useRef(0);
  const accountClosedByCleanupRef = useRef(false);
  const accountAuthFailedRef = useRef(false);
  const seenRealtimeEventIdsRef = useRef(new Set());
  const pendingMessageAckTimersRef = useRef(new Map());
  const outboxFlushPromiseRef = useRef(null);
  const pendingCallTelemetryRef = useRef([]);

  const organizationId = entitlement?.organization_id || null;
  const canConnectRealtime =
    authChecked &&
    !loading &&
    user?.id &&
    entitlement?.source === "organization" &&
    entitlement?.status === "active" &&
    ["business", "enterprise"].includes(entitlement?.plan) &&
    organizationId;

  const connectionKey = useMemo(() => {
    if (!canConnectRealtime) return "";
    return `${user.id}:${organizationId}:${entitlement.plan}`;
  }, [canConnectRealtime, entitlement?.plan, organizationId, user?.id]);

  const canConnectAccountRealtime =
    authChecked && !loading && Boolean(user?.id);

  const accountConnectionKey = useMemo(() => {
    if (!canConnectAccountRealtime) return "";
    return `${user.id}:account`;
  }, [canConnectAccountRealtime, user?.id]);

  const activeNotificationId = activeNotification?.id || "";
  const hasActiveNotification = Boolean(activeNotification);


  const removePendingMessage = useCallback(
    (clientMessageId) => {
      const normalizedId = String(clientMessageId || "").trim();
      if (!normalizedId || !user?.id || !organizationId) return;

      const timer = pendingMessageAckTimersRef.current.get(normalizedId);
      if (timer) window.clearTimeout(timer);
      pendingMessageAckTimersRef.current.delete(normalizedId);

      const remaining = loadRealtimeOutbox(user.id, organizationId).filter(
        (item) => item.clientMessageId !== normalizedId,
      );
      saveRealtimeOutbox(user.id, organizationId, remaining);
    },
    [organizationId, user?.id],
  );

  const sendPendingMessageOverRest = useCallback(
    async (item) => {
      if (!item || !user?.id || !organizationId) return null;
      try {
        const result = await sendConversationMessage(
          item.conversationId,
          item.body,
          { clientMessageId: item.clientMessageId },
        );
        removePendingMessage(item.clientMessageId);
        const message = result?.message;
        if (message?.id) {
          advanceRealtimeCursor(user.id, organizationId, message.id);
          dispatchTeamRealtimeEvent(
            withRealtimeContract({
              type: "message.ack",
              organization_id: organizationId,
              client_message_id: item.clientMessageId,
              message,
              conversation: result?.conversation || null,
              delivery: "committed",
              transport: "http_fallback",
              duplicate: Boolean(result?.duplicate),
            }),
          );
        }
        return result;
      } catch (error) {
        window.dispatchEvent(
          new CustomEvent("team-message-send-failed", {
            detail: {
              client_message_id: item.clientMessageId,
              conversation_id: item.conversationId,
              message: error?.message || "Could not send message.",
            },
          }),
        );
        return null;
      }
    },
    [organizationId, removePendingMessage, user?.id],
  );

  const flushDurableOutbox = useCallback(async () => {
    if (!user?.id || !organizationId) return;
    if (outboxFlushPromiseRef.current) return outboxFlushPromiseRef.current;

    const request = (async () => {
      const items = loadRealtimeOutbox(user.id, organizationId);
      for (const item of items) {
        await sendPendingMessageOverRest(item);
      }
    })().finally(() => {
      outboxFlushPromiseRef.current = null;
    });

    outboxFlushPromiseRef.current = request;
    return request;
  }, [organizationId, sendPendingMessageOverRest, user?.id]);

  const replayMissedMessages = useCallback(async () => {
    if (!user?.id || !organizationId) return;
    let cursor = readRealtimeCursor(user.id, organizationId);

    for (let page = 0; page < 10; page += 1) {
      const result = await replayOrganizationMessages(organizationId, {
        afterMessageId: cursor,
        limit: 200,
      });
      const events = Array.isArray(result?.events) ? result.events : [];
      if (!events.length) break;

      for (const event of events) {
        if (!validateRealtimeServerEvent(event) || !event.message?.id) continue;
        cursor = Math.max(cursor, Number(event.message.id));
        dispatchTeamRealtimeEvent(event);
      }
      advanceRealtimeCursor(user.id, organizationId, cursor);
      if (!result?.has_more) break;
    }
  }, [organizationId, user?.id]);

  useEffect(() => {
    if (!callError) return undefined;

    const timeoutId = window.setTimeout(() => {
      setCallError("");
    }, 8_000);

    return () => window.clearTimeout(timeoutId);
  }, [callError]);

  useEffect(() => {
    if (!incomingCall?.call?.ringing_expires_at) return undefined;
    const expiresAt = new Date(incomingCall.call.ringing_expires_at).getTime();
    const delay = expiresAt - Date.now();
    if (!Number.isFinite(delay) || delay <= 0) {
      queueMicrotask(() => setIncomingCall(null));
      return undefined;
    }
    const timeoutId = window.setTimeout(() => setIncomingCall(null), delay);
    return () => window.clearTimeout(timeoutId);
  }, [incomingCall?.call?.ringing_expires_at]);

  useEffect(() => {
    if (!hasActiveNotification) return undefined;

    if (notificationTimerRef.current) {
      window.clearTimeout(notificationTimerRef.current);
    }

    notificationTimerRef.current = window.setTimeout(() => {
      setActiveNotification(null);
    }, NOTIFICATION_VISIBLE_MS);

    return () => {
      if (notificationTimerRef.current) {
        window.clearTimeout(notificationTimerRef.current);
        notificationTimerRef.current = null;
      }
    };
  }, [activeNotificationId, hasActiveNotification]);

  useEffect(() => {
    if (!canConnectAccountRealtime || !accountConnectionKey) {
      return undefined;
    }

    accountClosedByCleanupRef.current = false;
    accountAuthFailedRef.current = false;

    function clearAccountTimers() {
      if (accountReconnectTimerRef.current) {
        window.clearTimeout(accountReconnectTimerRef.current);
        accountReconnectTimerRef.current = null;
      }

      if (accountPingTimerRef.current) {
        window.clearInterval(accountPingTimerRef.current);
        accountPingTimerRef.current = null;
      }
    }

    function scheduleAccountReconnect() {
      if (accountClosedByCleanupRef.current) return;

      const attempt = accountReconnectAttemptRef.current + 1;
      accountReconnectAttemptRef.current = attempt;
      const delay = reconnectDelayWithJitter(
        attempt,
        RECONNECT_BASE_MS,
        RECONNECT_MAX_MS,
      );

      accountReconnectTimerRef.current = window.setTimeout(() => {
        void connectAccountRealtime();
      }, delay);
    }

    async function connectAccountRealtime() {
      clearAccountTimers();

      try {
        const socketUrl = getAccountRealtimeWebSocketUrl();
        const token = await getAccountRealtimeWebSocketAuthToken();

        if (accountClosedByCleanupRef.current) return;

        const socket = new WebSocket(socketUrl);
        accountSocketRef.current = socket;

        socket.onopen = () => {
          socket.send(JSON.stringify(withRealtimeContract({ type: "auth", token })));

          accountPingTimerRef.current = window.setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify(withRealtimeContract({ type: "ping" })));
            }
          }, PING_INTERVAL_MS);
        };

        socket.onmessage = (messageEvent) => {
          let event = null;

          try {
            event = JSON.parse(messageEvent.data);
          } catch {
            return;
          }

          if (!event || typeof event !== "object") return;
          if (!validateRealtimeServerEvent(event)) return;

          if (event.type === "reauth.required") {
            void getAccountRealtimeWebSocketAuthToken({ forceRefresh: true })
              .then((refreshedToken) => {
                if (socket.readyState === WebSocket.OPEN) {
                  socket.send(
                    JSON.stringify(
                      withRealtimeContract({
                        type: "auth.refresh",
                        token: refreshedToken,
                      }),
                    ),
                  );
                }
              })
              .catch(() => socket.close(1008, "Account token refresh failed"));
            return;
          }

          if (event.type === "auth_failed") {
            accountAuthFailedRef.current = true;
            socket.close(1008, "Account realtime authentication failed");
            return;
          }

          if (
            ["account.realtime.connected", "account.realtime.ready"].includes(
              event.type,
            )
          ) {
            accountReconnectAttemptRef.current = 0;
            return;
          }

          if (
            isDuplicateRealtimeEvent(event, seenRealtimeEventIdsRef.current)
          ) {
            return;
          }

          dispatchTeamRealtimeEvent(event);

          if (String(event.type || "").startsWith("organization.invitation.")) {
            dispatchTeamInvitationRealtimeEvent(event);
          }

          if (shouldRefreshAccountFromRealtime(event)) {
            void reloadAccount?.({
              background: true,
              forceRefresh: true,
              allowCurrentAccountFallback: true,
            });
          }
        };

        socket.onerror = () => {
          // onclose handles reconnect. Keep this quiet so account realtime
          // failures do not interrupt the app UI.
        };

        socket.onclose = (event) => {
          if (accountSocketRef.current === socket) {
            accountSocketRef.current = null;
          }

          if (accountPingTimerRef.current) {
            window.clearInterval(accountPingTimerRef.current);
            accountPingTimerRef.current = null;
          }

          if (event.code === 1008 || accountAuthFailedRef.current) {
            accountAuthFailedRef.current = true;
            return;
          }

          scheduleAccountReconnect();
        };
      } catch {
        scheduleAccountReconnect();
      }
    }

    accountReconnectTimerRef.current = window.setTimeout(() => {
      void connectAccountRealtime();
    }, REALTIME_CONNECT_DELAY_MS);

    return () => {
      accountClosedByCleanupRef.current = true;
      clearAccountTimers();

      if (accountSocketRef.current) {
        accountSocketRef.current.close(
          1000,
          "Account realtime provider unmounted",
        );
        accountSocketRef.current = null;
      }
    };
  }, [
    canConnectAccountRealtime,
    accountConnectionKey,
    reloadAccount,
    user?.id,
  ]);

  useEffect(() => {
    if (!canConnectRealtime || !connectionKey) {
      queueMicrotask(() => {
        setActiveNotification(null);
        setConnectionState("idle");
      });
      return undefined;
    }

    closedByCleanupRef.current = false;
    organizationAuthFailedRef.current = false;

    function clearTimers() {
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }

      if (pingTimerRef.current) {
        window.clearInterval(pingTimerRef.current);
        pingTimerRef.current = null;
      }
    }

    function scheduleReconnect() {
      if (closedByCleanupRef.current) return;

      const attempt = reconnectAttemptRef.current + 1;
      reconnectAttemptRef.current = attempt;
      const delay = reconnectDelayWithJitter(
        attempt,
        RECONNECT_BASE_MS,
        RECONNECT_MAX_MS,
      );

      reconnectTimerRef.current = window.setTimeout(() => {
        void connect();
      }, delay);
    }

    async function connect() {
      clearTimers();
      setConnectionState("connecting");

      try {
        const socketUrl = getOrganizationRealtimeWebSocketUrl(organizationId);
        const token = await getOrganizationRealtimeWebSocketAuthToken();

        if (closedByCleanupRef.current) return;

        const socket = new WebSocket(socketUrl);
        socketRef.current = socket;

        socket.onopen = () => {
          socket.send(JSON.stringify(withRealtimeContract({ type: "auth", token })));

          pingTimerRef.current = window.setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify(withRealtimeContract({ type: "ping" })));
            }
          }, PING_INTERVAL_MS);
        };

        socket.onmessage = (messageEvent) => {
          let event = null;

          try {
            event = JSON.parse(messageEvent.data);
          } catch {
            return;
          }

          if (!event || typeof event !== "object") return;
          if (!validateRealtimeServerEvent(event)) return;

          if (event.type === "reauth.required") {
            void getOrganizationRealtimeWebSocketAuthToken({ forceRefresh: true })
              .then((refreshedToken) => {
                if (socket.readyState === WebSocket.OPEN) {
                  socket.send(
                    JSON.stringify(
                      withRealtimeContract({
                        type: "auth.refresh",
                        token: refreshedToken,
                      }),
                    ),
                  );
                }
              })
              .catch(() => socket.close(1008, "Organization token refresh failed"));
            return;
          }

          if (event.type === "auth_failed") {
            organizationAuthFailedRef.current = true;
            setConnectionState("closed");
            socket.close(1008, "Organization realtime authentication failed");
            return;
          }

          if (event.type === "realtime.connected") {
            reconnectAttemptRef.current = 0;
            setConnectionState("open");
            void replayMissedMessages().catch(() => {});
            void flushDurableOutbox();
            return;
          }

          if (
            event.type === "organization.access.revoked" &&
            String(event.organization_id || "") === String(organizationId) &&
            (!event.user_id || String(event.user_id) === String(user.id))
          ) {
            organizationAuthFailedRef.current = true;
            clearRevokedOrganizationCache(user.id, organizationId);
            activeCallRef.current = null;
            setActiveCall(null);
            setIncomingCall(null);
            setCallMinimized(false);
            setCallError("");
            setActiveNotification(null);
            setConnectionState("closed");
            dispatchTeamRealtimeEvent(event);

            void reloadAccount?.({
              background: true,
              forceRefresh: true,
              allowCurrentAccountFallback: false,
            });

            socket.close(1008, "Organization access revoked");
            return;
          }

          const eventClientMessageId = String(
            event.client_message_id ||
              event.message?.client_message_id ||
              event.message?.metadata?.client_message_id ||
              "",
          ).trim();
          if (
            ["message.ack", "message.persisted", "message.created"].includes(
              event.type,
            ) && eventClientMessageId
          ) {
            removePendingMessage(eventClientMessageId);
          }
          if (event.type === "message.failed" && eventClientMessageId) {
            removePendingMessage(eventClientMessageId);
            window.dispatchEvent(
              new CustomEvent("team-message-send-failed", { detail: event }),
            );
          }
          if (event.message?.id) {
            advanceRealtimeCursor(user.id, organizationId, event.message.id);
          }

          if (
            isDuplicateRealtimeEvent(event, seenRealtimeEventIdsRef.current)
          ) {
            return;
          }

          dispatchTeamRealtimeEvent(event);
          reconcileMessageNotification(event, setActiveNotification);

          if (
            event.type === "call.started" &&
            event.call?.id &&
            String(getEventSenderId(event) || "") !== String(user.id)
          ) {
            const createdAt = new Date(event.call.created_at || 0).getTime();
            void reportCallTelemetry(event.call.id, {
              eventType: "prejoin.opened",
              clientEventId: `incoming-realtime:${event.event_id || event.call.id}`,
              occurredAt: new Date().toISOString(),
              metrics: {
                source: "realtime_notification",
                notification_delay_ms:
                  Number.isFinite(createdAt) && createdAt > 0
                    ? Math.max(0, Date.now() - createdAt)
                    : null,
              },
            }).catch(() => {});
            setIncomingCall({
              id: Number(event.call.id),
              call: event.call,
              participants: Array.isArray(event.participants)
                ? event.participants
                : [],
              conversation: event.conversation || null,
              sender: event.sender || null,
              receivedAt: Date.now(),
            });
          }

          if (
            event.type === "call.declined" &&
            String(event.participant?.user_id || "") === String(user.id)
          ) {
            setIncomingCall((current) =>
              String(current?.call?.id || current?.id || "") ===
              String(event.call?.id || "")
                ? null
                : current,
            );
          }

          if (["call.ended", "call.cancelled", "call.missed"].includes(event.type)) {
            const terminalCallId = String(event.call?.id || "");
            if (
              String(activeCallRef.current?.call?.id || "") === terminalCallId
            ) {
              activeCallRef.current = null;
              pendingCallTelemetryRef.current = [];
              setActiveCall(null);
              setCallMinimized(false);
            }
            setIncomingCall((current) =>
              String(current?.call?.id || current?.id || "") === terminalCallId
                ? null
                : current,
            );
          }

          if (shouldRefreshAccountFromRealtime(event)) {
            void reloadAccount?.({
              background: true,
              forceRefresh: true,
              allowCurrentAccountFallback: true,
            });
          }

          const nextNotification = buildNotificationFromEvent(
            event,
            user.id,
            t,
          );
          if (nextNotification) {
            setActiveNotification(nextNotification);
          }
        };

        socket.onerror = () => {
          // onclose handles reconnect. Keep this quiet so realtime failures do
          // not interrupt the rest of the app UI.
        };

        socket.onclose = (event) => {
          if (socketRef.current === socket) {
            socketRef.current = null;
          }

          if (pingTimerRef.current) {
            window.clearInterval(pingTimerRef.current);
            pingTimerRef.current = null;
          }

          if (event.code === 1008 || organizationAuthFailedRef.current) {
            organizationAuthFailedRef.current = true;
            setConnectionState("closed");
            return;
          }

          setConnectionState(
            closedByCleanupRef.current ? "closed" : "reconnecting",
          );
          scheduleReconnect();
        };
      } catch {
        setConnectionState("reconnecting");
        scheduleReconnect();
      }
    }

    reconnectTimerRef.current = window.setTimeout(() => {
      void connect();
    }, REALTIME_CONNECT_DELAY_MS);

    return () => {
      closedByCleanupRef.current = true;
      clearTimers();

      if (socketRef.current) {
        socketRef.current.close(1000, "Provider unmounted");
        socketRef.current = null;
      }
      for (const timer of pendingMessageAckTimersRef.current.values()) {
        window.clearTimeout(timer);
      }
      pendingMessageAckTimersRef.current.clear();

      setConnectionState("closed");
    };
  }, [
    canConnectRealtime,
    connectionKey,
    flushDurableOutbox,
    organizationId,
    reloadAccount,
    removePendingMessage,
    replayMissedMessages,
    t,
    user?.id,
  ]);

  const sendRealtimeEvent = useCallback((payload) => {
    const socket = socketRef.current;

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error(
        "Realtime connection is not ready. Please wait a moment and try again.",
      );
    }

    socket.send(JSON.stringify(withRealtimeContract(payload)));
  }, []);

  const sendRealtimeMessage = useCallback(
    ({ conversationId, body, clientMessageId } = {}) => {
      const resolvedConversationId = parsePositiveInteger(
        conversationId,
        "conversationId",
      );
      const resolvedBody = normalizeMessageBody(body);
      const resolvedClientMessageId = String(
        clientMessageId || createClientMessageId(),
      );
      const item = {
        conversationId: resolvedConversationId,
        body: resolvedBody,
        clientMessageId: resolvedClientMessageId,
        queuedAt: Date.now(),
      };

      if (user?.id && organizationId) {
        const existing = loadRealtimeOutbox(user.id, organizationId).filter(
          (queued) => queued.clientMessageId !== resolvedClientMessageId,
        );
        saveRealtimeOutbox(user.id, organizationId, [...existing, item]);
      }

      const socket = socketRef.current;
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(
          JSON.stringify(
            withRealtimeContract({
              type: "message.send",
              client_message_id: resolvedClientMessageId,
              conversation_id: resolvedConversationId,
              body: resolvedBody,
            }),
          ),
        );
        const existingTimer = pendingMessageAckTimersRef.current.get(
          resolvedClientMessageId,
        );
        if (existingTimer) window.clearTimeout(existingTimer);
        const timer = window.setTimeout(() => {
          pendingMessageAckTimersRef.current.delete(resolvedClientMessageId);
          void sendPendingMessageOverRest(item);
        }, TEAM_REALTIME_ACK_TIMEOUT_MS);
        pendingMessageAckTimersRef.current.set(resolvedClientMessageId, timer);
      } else {
        void sendPendingMessageOverRest(item);
      }

      return resolvedClientMessageId;
    },
    [organizationId, sendPendingMessageOverRest, user?.id],
  );

  const commitActiveCall = useCallback((callPayload) => {
    activeCallRef.current = callPayload;
    setActiveCall(callPayload);
    setCallMinimized(false);
    setCallError("");
  }, []);

  const activateCall = useCallback(
    (callPayload) => {
      if (!callPayload?.call) {
        throw new Error("The call response is missing call details.");
      }
      commitActiveCall(callPayload);
    },
    [commitActiveCall],
  );

  const prepareOutgoingCall = useCallback(
    ({ conversationId, mediaType = "video", conversation = null } = {}) => {
      pendingCallTelemetryRef.current = [];
      const resolvedConversationId = parsePositiveInteger(
        conversationId,
        "conversationId",
      );
      const normalizedMediaType = mediaType === "audio" ? "audio" : "video";
      const prepared = {
        intent: {
          kind: "start",
          conversationId: resolvedConversationId,
          mediaType: normalizedMediaType,
          preparedAt: Date.now(),
        },
        call: {
          id: null,
          conversation_id: resolvedConversationId,
          media_type: normalizedMediaType,
          created_by_user_id: user?.id || "",
          status: "prejoin",
        },
        conversation,
        participants: Array.isArray(conversation?.members)
          ? conversation.members
          : [],
        livekit: null,
      };
      commitActiveCall(prepared);
      return prepared;
    },
    [commitActiveCall, user?.id],
  );

  const prepareIncomingCall = useCallback(
    (callPayload = {}) => {
      const source = callPayload?.call ? callPayload : { call: callPayload };
      const callId = parsePositiveInteger(
        source.call?.id || source.callSessionId,
        "callSessionId",
      );
      const prepared = {
        ...source,
        intent: {
          kind: "join",
          callSessionId: callId,
          preparedAt: Date.now(),
        },
        call: {
          ...(source.call || {}),
          id: callId,
          media_type: source.call?.media_type || source.mediaType || "video",
        },
        livekit: null,
      };
      setIncomingCall(null);
      commitActiveCall(prepared);
      return prepared;
    },
    [commitActiveCall],
  );

  const flushPendingCallTelemetry = useCallback(async (callId) => {
    const pending = pendingCallTelemetryRef.current.splice(0, 30);
    for (const item of pending) {
      await reportCallTelemetry(callId, item).catch(() => {});
    }
  }, []);

  const reportActiveCallTelemetry = useCallback(
    (item) => {
      const callId = activeCallRef.current?.call?.id;
      if (!callId) {
        pendingCallTelemetryRef.current = [
          ...pendingCallTelemetryRef.current.slice(-29),
          item,
        ];
        return;
      }
      void reportCallTelemetry(callId, item).catch(() => {});
    },
    [],
  );

  const prepareActiveCallConnection = useCallback(
    async (preferences) => {
      const current = activeCallRef.current;
      if (!current?.intent) {
        throw new Error("Call preparation is not available.");
      }

      const response =
        current.intent.kind === "start"
          ? await startConversationCall(current.intent.conversationId, {
              mediaType: current.intent.mediaType,
            })
          : await joinCall(current.intent.callSessionId || current.call?.id);

      const prepared = {
        ...current,
        ...response,
        conversation: response?.conversation || current.conversation || null,
        participants: response?.participants || current.participants || [],
        intent: {
          ...current.intent,
          kind: "connected",
          originalKind: current.intent.kind,
          connectedRequestAt: Date.now(),
        },
        joinPreferences: preferences,
      };
      commitActiveCall(prepared);
      setIncomingCall(null);
      if (response?.call?.id) {
        void flushPendingCallTelemetry(response.call.id);
      }
      return prepared;
    },
    [commitActiveCall, flushPendingCallTelemetry],
  );

  const recoverActiveCall = useCallback(
    async () => {
      const current = activeCallRef.current;
      const callId = current?.call?.id;
      if (!callId) throw new Error("Call recovery requires a call ID.");
      const response = await joinCall(callId);
      const recovered = {
        ...current,
        ...response,
        conversation: response?.conversation || current.conversation || null,
        participants: response?.participants || current.participants || [],
        recoveredAt: Date.now(),
      };
      activeCallRef.current = recovered;
      setActiveCall(recovered);
      setCallError("");
      return recovered;
    },
    [],
  );

  const declineIncomingCall = useCallback(async () => {
    const callId = incomingCall?.call?.id || incomingCall?.id;
    if (!callId || incomingCallBusy) return;
    setIncomingCallBusy(true);
    try {
      await declineCall(callId);
      setIncomingCall(null);
    } catch (error) {
      setCallError(getCallErrorMessage(error));
    } finally {
      setIncomingCallBusy(false);
    }
  }, [incomingCall, incomingCallBusy]);

  const cancelActiveCallPrejoin = useCallback(async () => {
    const current = activeCallRef.current;
    const callId = current?.call?.id;
    const shouldDecline =
      Boolean(callId) &&
      ["join", "connected"].includes(
        current?.intent?.originalKind || current?.intent?.kind,
      );

    activeCallRef.current = null;
    pendingCallTelemetryRef.current = [];
    setActiveCall(null);
    setCallMinimized(false);
    if (shouldDecline) {
      await declineCall(callId).catch((error) => {
        setCallError(getCallErrorMessage(error));
      });
    }
  }, []);

  const minimizeCall = useCallback(() => {
    if (activeCallRef.current) setCallMinimized(true);
  }, []);

  const restoreCall = useCallback(() => {
    if (activeCallRef.current) setCallMinimized(false);
  }, []);

  const leaveActiveCall = useCallback(async () => {
    const callId = activeCallRef.current?.call?.id;

    if (!callId) {
      activeCallRef.current = null;
      setActiveCall(null);
      setCallMinimized(false);
      return;
    }

    if (leaveCallPromiseRef.current) return leaveCallPromiseRef.current;

    activeCallRef.current = null;
    pendingCallTelemetryRef.current = [];
    setActiveCall(null);
    setCallMinimized(false);

    const request = leaveCall(callId)
      .catch((error) => setCallError(getCallErrorMessage(error)))
      .finally(() => {
        leaveCallPromiseRef.current = null;
      });

    leaveCallPromiseRef.current = request;
    return request;
  }, []);

  const endActiveCall = useCallback(async () => {
    const callId = activeCallRef.current?.call?.id;
    if (!callId) return;
    if (endCallPromiseRef.current) return endCallPromiseRef.current;

    activeCallRef.current = null;
    pendingCallTelemetryRef.current = [];
    setActiveCall(null);
    setCallMinimized(false);

    const request = endCall(callId)
      .catch((error) => setCallError(getCallErrorMessage(error)))
      .finally(() => {
        endCallPromiseRef.current = null;
      });

    endCallPromiseRef.current = request;
    return request;
  }, []);

  const realtimeValue = useMemo(
    () => ({
      activeCall,
      incomingCall,
      callMinimized,
      connectionState,
      realtimeReady: canConnectRealtime && connectionState === "open",
      activateCall,
      prepareOutgoingCall,
      prepareIncomingCall,
      declineIncomingCall,
      endActiveCall,
      leaveActiveCall,
      minimizeCall,
      restoreCall,
      sendRealtimeEvent,
      sendRealtimeMessage,
    }),
    [
      activeCall,
      incomingCall,
      activateCall,
      canConnectRealtime,
      callMinimized,
      connectionState,
      declineIncomingCall,
      endActiveCall,
      leaveActiveCall,
      minimizeCall,
      prepareIncomingCall,
      prepareOutgoingCall,
      restoreCall,
      sendRealtimeEvent,
      sendRealtimeMessage,
    ],
  );

  function openNotification() {
    if (!activeNotification) return;

    const targetUrl = activeNotification.targetUrl || "/team";
    setActiveNotification(null);
    router.push(targetUrl);
  }

  const isTeamNotification = activeNotification?.kind === "team";
  const isGroupMessage = activeNotification?.conversationType === "group";
  const notificationTitle =
    activeNotification?.title ||
    (isGroupMessage ? t.groupTitle : t.directTitle);
  const senderLabel = activeNotification?.senderName || t.fallbackSender;
  const conversationLabel =
    activeNotification?.conversationName || t.fallbackGroup;
  const actionLabel = isTeamNotification ? t.viewTeam : t.openMessage;

  return (
    <TeamRealtimeContext.Provider value={realtimeValue}>
      {children}

      {activeCall ? (
        <TeamCallRoom
          serverUrl={activeCall.livekit?.server_url}
          token={activeCall.livekit?.token}
          roomName={
            activeCall.livekit?.room_name ||
            activeCall.conversation?.name ||
            t.fallbackCall
          }
          mediaType={activeCall.call?.media_type || "video"}
          participantCount={
            activeCall.participants?.length ||
            activeCall.conversation?.member_user_ids?.length ||
            2
          }
          callCreatedAt={activeCall.call?.created_at}
          intentPreparedAt={activeCall.intent?.preparedAt}
          intentKind={
            activeCall.intent?.originalKind || activeCall.intent?.kind || "join"
          }
          language={language}
          minimized={callMinimized}
          isHost={
            !activeCall.call?.id ||
            String(activeCall.call?.created_by_user_id || "") ===
              String(user?.id || "")
          }
          canEndForEveryone={
            Boolean(activeCall.call?.id) &&
            (String(activeCall.call?.created_by_user_id || "") ===
              String(user?.id || "") ||
              ["owner", "admin"].includes(entitlement?.organization_role))
          }
          onPrepareConnection={prepareActiveCallConnection}
          onRecover={recoverActiveCall}
          onCancelPrejoin={cancelActiveCallPrejoin}
          onTelemetry={reportActiveCallTelemetry}
          onMinimize={minimizeCall}
          onRestore={restoreCall}
          onEnd={endActiveCall}
          onLeave={leaveActiveCall}
        />
      ) : null}

      {incomingCall ? (
        <section
          role="alertdialog"
          aria-live="assertive"
          className="fixed bottom-5 right-5 z-[135] w-[calc(100vw-2.5rem)] max-w-sm rounded-3xl border border-emerald-400/30 app-surface-strong p-5 shadow-2xl backdrop-blur md:bottom-7 md:right-7"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-emerald-400/30 bg-emerald-400/10">
              <PhoneCall className="h-5 w-5 app-text" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                {t.callTitle}
              </p>
              <h2 className="mt-1 truncate text-base font-semibold app-text">
                {t.callFromLabel}: {incomingCall.sender?.name || incomingCall.sender?.email || t.fallbackSender}
              </h2>
              <p className="mt-2 text-sm app-text-muted">
                {incomingCall.call?.media_type === "audio"
                  ? incomingCall.conversation?.type === "group"
                    ? t.groupAudioCallBody
                    : t.directAudioCallBody
                  : incomingCall.conversation?.type === "group"
                    ? t.groupCallBody
                    : t.directCallBody}
              </p>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <button
              type="button"
              onClick={() => void declineIncomingCall()}
              disabled={incomingCallBusy}
              className="inline-flex items-center justify-center gap-2 rounded-2xl border border-red-400/30 px-4 py-3 text-sm font-semibold text-red-600 transition hover:bg-red-500/10 disabled:opacity-60 dark:text-red-200"
            >
              <PhoneOff className="h-4 w-4" />
              {incomingCallBusy ? t.decliningCall : t.declineCall}
            </button>
            <button
              type="button"
              onClick={() => prepareIncomingCall(incomingCall)}
              disabled={incomingCallBusy || Boolean(activeCall)}
              className="inline-flex items-center justify-center gap-2 rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:opacity-60"
            >
              <PhoneCall className="h-4 w-4" />
              {t.joinCall}
            </button>
          </div>
        </section>
      ) : null}

      {callError ? (
        <div
          role="alert"
          className="fixed left-1/2 top-5 z-[160] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 rounded-2xl border border-red-400/30 bg-red-950/95 px-4 py-3 text-sm text-red-100 shadow-2xl"
        >
          {callError}
        </div>
      ) : null}

      {activeNotification ? (
        <section
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-[120] w-[calc(100vw-2.5rem)] max-w-sm rounded-3xl border app-surface-strong p-4 shadow-2xl backdrop-blur md:bottom-7 md:right-7"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border app-surface">
              <MessageCircle className="h-5 w-5 app-text-muted" />
            </div>

            <button
              type="button"
              onClick={openNotification}
              className="min-w-0 flex-1 text-left"
            >
              <p className="text-xs font-semibold uppercase tracking-[0.12em] app-text-soft">
                {notificationTitle}
              </p>
              <h2 className="mt-1 truncate text-base font-semibold app-text">
                {isTeamNotification
                  ? senderLabel
                  : isGroupMessage
                    ? `${t.groupLabel}: ${conversationLabel}`
                    : `${t.fromLabel}: ${senderLabel}`}
              </h2>
              <p className="mt-2 line-clamp-2 text-sm app-text-muted">
                {truncateText(activeNotification.body)}
              </p>
              <p className="mt-3 text-xs font-semibold app-text-soft">
                {actionLabel}
              </p>
            </button>

            <button
              type="button"
              onClick={() => setActiveNotification(null)}
              aria-label={t.close}
              className="rounded-xl border app-surface p-2 app-text-soft transition hover:text-[var(--app-text)]"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </section>
      ) : null}
    </TeamRealtimeContext.Provider>
  );
}
