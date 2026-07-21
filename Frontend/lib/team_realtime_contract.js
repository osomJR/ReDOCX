import {
  TEAM_REALTIME_EVENT_VERSION,
} from "./team_realtime_contract_generated.js";

export { TEAM_REALTIME_EVENT_VERSION };
export const TEAM_REALTIME_ACK_TIMEOUT_MS = 6_000;
export const TEAM_REALTIME_OUTBOX_MAX_ITEMS = 200;

const CONTROL_EVENT_TYPES = new Set([
  "auth_failed",
  "error",
  "already_authenticated",
]);

export function withRealtimeContract(payload) {
  return {
    event_version: TEAM_REALTIME_EVENT_VERSION,
    ...payload,
  };
}

export function validateRealtimeServerEvent(event) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    return false;
  }
  const type = String(event.type || "").trim();
  if (!type) return false;
  if (CONTROL_EVENT_TYPES.has(type) && event.event_version == null) return true;
  return Number(event.event_version) === TEAM_REALTIME_EVENT_VERSION;
}

export function reconnectDelayWithJitter(attempt, baseMs, maximumMs) {
  const exponential = Math.min(baseMs * 2 ** Math.max(0, attempt - 1), maximumMs);
  return Math.round(exponential * (0.75 + Math.random() * 0.5));
}

function parseStoredArray(value) {
  try {
    const parsed = JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function realtimeOutboxKey(userId, organizationId) {
  return `redocx:team-realtime-outbox:v1:${userId}:${organizationId}`;
}

export function realtimeCursorKey(userId, organizationId) {
  return `redocx:team-realtime-cursor:v1:${userId}:${organizationId}`;
}

export function loadRealtimeOutbox(userId, organizationId) {
  if (typeof window === "undefined") return [];
  return parseStoredArray(
    window.localStorage.getItem(realtimeOutboxKey(userId, organizationId)),
  ).filter(
    (item) =>
      item &&
      Number(item.conversationId) > 0 &&
      String(item.body || "").trim() &&
      String(item.clientMessageId || "").trim(),
  );
}

export function saveRealtimeOutbox(userId, organizationId, items) {
  if (typeof window === "undefined") return;
  const bounded = items.slice(-TEAM_REALTIME_OUTBOX_MAX_ITEMS);
  window.localStorage.setItem(
    realtimeOutboxKey(userId, organizationId),
    JSON.stringify(bounded),
  );
}

export function readRealtimeCursor(userId, organizationId) {
  if (typeof window === "undefined") return 0;
  const value = Number.parseInt(
    window.localStorage.getItem(realtimeCursorKey(userId, organizationId)) || "0",
    10,
  );
  return Number.isSafeInteger(value) && value > 0 ? value : 0;
}

export function advanceRealtimeCursor(userId, organizationId, messageId) {
  if (typeof window === "undefined") return;
  const next = Number.parseInt(String(messageId || "0"), 10);
  if (!Number.isSafeInteger(next) || next < 1) return;
  const current = readRealtimeCursor(userId, organizationId);
  if (next > current) {
    window.localStorage.setItem(
      realtimeCursorKey(userId, organizationId),
      String(next),
    );
  }
}
