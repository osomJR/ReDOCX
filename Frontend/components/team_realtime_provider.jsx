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
import { MessageCircle, PhoneCall, X } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { useAccount } from "@/components/account_provider";
import { useLanguage } from "@/components/language_provider";
import {
  getAccountRealtimeWebSocketUrl,
  getOrganizationRealtimeWebSocketUrl,
} from "@/lib/api_client";

const NOTIFICATION_VISIBLE_MS = 10_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 15_000;
const PING_INTERVAL_MS = 25_000;
const REALTIME_CONNECT_DELAY_MS = 750;
const ACCOUNT_REALTIME_DASHBOARD_PATH = "/";

const TeamRealtimeContext = createContext({
  connectionState: "idle",
  realtimeReady: false,
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

const copy = {
  en: {
    directTitle: "New direct message",
    groupTitle: "New group message",
    callTitle: "Incoming video call",
    fromLabel: "From",
    groupLabel: "Group",
    callFromLabel: "Call from",
    openMessage: "Open message",
    openCall: "Open call",
    close: "Close notification",
    fallbackSender: "Team member",
    fallbackGroup: "Team group chat",
    fallbackCall: "Team call",
    directCallBody: "A direct video call has started.",
    groupCallBody: "A group video call has started.",
    attachmentBody: "Sent an attachment.",
  },
  fr: {
    directTitle: "Nouveau message direct",
    groupTitle: "Nouveau message de groupe",
    callTitle: "Appel vidéo entrant",
    fromLabel: "De",
    groupLabel: "Groupe",
    callFromLabel: "Appel de",
    openMessage: "Ouvrir le message",
    openCall: "Ouvrir l’appel",
    close: "Fermer la notification",
    fallbackSender: "Membre de l’équipe",
    fallbackGroup: "Groupe de l’équipe",
    fallbackCall: "Appel d’équipe",
    directCallBody: "Un appel vidéo direct a commencé.",
    groupCallBody: "Un appel vidéo de groupe a commencé.",
    attachmentBody: "A envoyé une pièce jointe.",
  },
};

function truncateText(value = "", maxLength = 120) {
  const normalized = String(value || "").replace(/\s+/g, " ").trim();

  if (normalized.length <= maxLength) return normalized;

  return `${normalized.slice(0, maxLength - 1)}…`;
}

function asPositiveInteger(value, fallback = 0) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
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
  return query ? `/team/messages?${query}` : "/team/messages";
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

  if (event.type === "call.started" && event.call?.id) {
    const call = event.call;
    const conversation = event.conversation || {};

    return {
      id: `call:${call.id}:${Date.now()}`,
      kind: "call",
      conversationId: call.conversation_id,
      callSessionId: call.id,
      conversationType: conversation.type || call.type || "dm",
      conversationName: conversation.name || "",
      senderName: event.sender?.name || event.sender?.email || "",
      body:
        conversation.type === "group"
          ? t.groupCallBody
          : t.directCallBody,
      targetUrl: getConversationUrl({
        conversationId: call.conversation_id,
        callSessionId: call.id,
      }),
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
  const pathname = usePathname();
  const { language } = useLanguage();
  const { user, entitlement, authChecked, loading } = useAccount();
  const t = copy[language] || copy.en;

  const [activeNotification, setActiveNotification] = useState(null);
  const [connectionState, setConnectionState] = useState("idle");
  const socketRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const notificationTimerRef = useRef(null);
  const pingTimerRef = useRef(null);
  const reconnectAttemptRef = useRef(0);
  const closedByCleanupRef = useRef(false);
  const accountSocketRef = useRef(null);
  const accountReconnectTimerRef = useRef(null);
  const accountPingTimerRef = useRef(null);
  const accountReconnectAttemptRef = useRef(0);
  const accountClosedByCleanupRef = useRef(false);

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
    authChecked &&
    !loading &&
    Boolean(user?.id) &&
    pathname === ACCOUNT_REALTIME_DASHBOARD_PATH;

  const accountConnectionKey = useMemo(() => {
    if (!canConnectAccountRealtime) return "";
    return `${user.id}:account`;
  }, [canConnectAccountRealtime, user?.id]);

  useEffect(() => {
    if (!activeNotification) return undefined;

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
  }, [activeNotification?.id]);

  useEffect(() => {
    if (!canConnectAccountRealtime || !accountConnectionKey) {
      return undefined;
    }

    accountClosedByCleanupRef.current = false;

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
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);

      accountReconnectTimerRef.current = window.setTimeout(() => {
        void connectAccountRealtime();
      }, delay);
    }

    async function connectAccountRealtime() {
      clearAccountTimers();

      try {
        const socketUrl = await getAccountRealtimeWebSocketUrl();

        if (accountClosedByCleanupRef.current) return;

        const socket = new WebSocket(socketUrl);
        accountSocketRef.current = socket;

        socket.onopen = () => {
          accountReconnectAttemptRef.current = 0;
          accountPingTimerRef.current = window.setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify({ type: "ping" }));
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

          dispatchTeamRealtimeEvent(event);

          if (String(event.type || "").startsWith("organization.invitation.")) {
            dispatchTeamInvitationRealtimeEvent(event);
          }
        };

        socket.onerror = () => {
          // onclose handles reconnect. Keep this quiet so account realtime
          // failures do not interrupt the app UI.
        };

        socket.onclose = () => {
          if (accountSocketRef.current === socket) {
            accountSocketRef.current = null;
          }

          if (accountPingTimerRef.current) {
            window.clearInterval(accountPingTimerRef.current);
            accountPingTimerRef.current = null;
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
        accountSocketRef.current.close(1000, "Account realtime provider unmounted");
        accountSocketRef.current = null;
      }
    };
  }, [canConnectAccountRealtime, accountConnectionKey, user?.id, pathname]);

  useEffect(() => {
    if (!canConnectRealtime || !connectionKey) {
      setActiveNotification(null);
      setConnectionState("idle");
      return undefined;
    }

    closedByCleanupRef.current = false;

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
      const delay = Math.min(RECONNECT_BASE_MS * 2 ** (attempt - 1), RECONNECT_MAX_MS);

      reconnectTimerRef.current = window.setTimeout(() => {
        void connect();
      }, delay);
    }

    async function connect() {
      clearTimers();
      setConnectionState("connecting");

      try {
        const socketUrl = await getOrganizationRealtimeWebSocketUrl(organizationId);

        if (closedByCleanupRef.current) return;

        const socket = new WebSocket(socketUrl);
        socketRef.current = socket;

        socket.onopen = () => {
          setConnectionState("open");
          reconnectAttemptRef.current = 0;
          pingTimerRef.current = window.setInterval(() => {
            if (socket.readyState === WebSocket.OPEN) {
              socket.send(JSON.stringify({ type: "ping" }));
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

          dispatchTeamRealtimeEvent(event);
          reconcileMessageNotification(event, setActiveNotification);

          const nextNotification = buildNotificationFromEvent(event, user.id, t);
          if (nextNotification) {
            setActiveNotification(nextNotification);
          }
        };

        socket.onerror = () => {
          // onclose handles reconnect. Keep this quiet so realtime failures do
          // not interrupt the rest of the app UI.
        };

        socket.onclose = () => {
          if (socketRef.current === socket) {
            socketRef.current = null;
          }

          setConnectionState(closedByCleanupRef.current ? "closed" : "reconnecting");

          if (pingTimerRef.current) {
            window.clearInterval(pingTimerRef.current);
            pingTimerRef.current = null;
          }

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

      setConnectionState("closed");
    };
  }, [canConnectRealtime, connectionKey, organizationId, user?.id]);

  const sendRealtimeEvent = useCallback((payload) => {
    const socket = socketRef.current;

    if (!socket || socket.readyState !== WebSocket.OPEN) {
      throw new Error("Realtime connection is not ready. Please wait a moment and try again.");
    }

    socket.send(JSON.stringify(payload));
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

      sendRealtimeEvent({
        type: "message.send",
        client_message_id: resolvedClientMessageId,
        conversation_id: resolvedConversationId,
        body: resolvedBody,
      });

      return resolvedClientMessageId;
    },
    [sendRealtimeEvent],
  );

  const realtimeValue = useMemo(
    () => ({
      connectionState,
      realtimeReady: canConnectRealtime && connectionState === "open",
      sendRealtimeEvent,
      sendRealtimeMessage,
    }),
    [canConnectRealtime, connectionState, sendRealtimeEvent, sendRealtimeMessage],
  );

  function openNotification() {
    if (!activeNotification) return;

    const targetUrl = activeNotification.targetUrl || "/team/messages";
    setActiveNotification(null);
    router.push(targetUrl);
  }

  const isCallNotification = activeNotification?.kind === "call";
  const isGroupMessage = activeNotification?.conversationType === "group";
  const notificationTitle = isCallNotification
    ? t.callTitle
    : isGroupMessage
      ? t.groupTitle
      : t.directTitle;
  const senderLabel = activeNotification?.senderName || t.fallbackSender;
  const conversationLabel = activeNotification?.conversationName || t.fallbackGroup;
  const actionLabel = isCallNotification ? t.openCall : t.openMessage;

  return (
    <TeamRealtimeContext.Provider value={realtimeValue}>
      {children}

      {activeNotification ? (
        <section
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-[120] w-[calc(100vw-2.5rem)] max-w-sm rounded-3xl border app-surface-strong p-4 shadow-2xl backdrop-blur md:bottom-7 md:right-7"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border app-surface">
              {isCallNotification ? (
                <PhoneCall className="h-5 w-5 app-text-muted" />
              ) : (
                <MessageCircle className="h-5 w-5 app-text-muted" />
              )}
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
                {isCallNotification
                  ? `${t.callFromLabel}: ${senderLabel}`
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
