import {
  registerTeamPushSubscription,
  revokeTeamPushSubscription,
} from "@/lib/api_client";

function base64UrlToUint8Array(value) {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replaceAll("-", "+").replaceAll("_", "/");
  const raw = window.atob(base64);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

export async function enableTeamPushNotifications({
  vapidPublicKey,
  locale = "en",
} = {}) {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) {
    throw new Error("Push notifications are not supported in this browser.");
  }
  if (!("PushManager" in window)) {
    throw new Error("Push notifications are not supported in this browser.");
  }
  const normalizedKey = String(vapidPublicKey || "").trim();
  if (!normalizedKey) {
    throw new Error("The web-push public key is not configured.");
  }

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }

  const registration = await navigator.serviceWorker.register("/team-push-sw.js", {
    scope: "/",
  });
  const existing = await registration.pushManager.getSubscription();
  const subscription =
    existing ||
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64UrlToUint8Array(normalizedKey),
    }));

  const serialized = subscription.toJSON();
  await registerTeamPushSubscription({
    endpoint: serialized.endpoint,
    p256dh: serialized.keys?.p256dh,
    auth: serialized.keys?.auth,
    user_agent: navigator.userAgent,
    locale: locale === "fr" ? "fr" : "en",
  });
  return subscription;
}

export async function disableTeamPushNotifications() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  const registration = await navigator.serviceWorker.getRegistration("/");
  const subscription = await registration?.pushManager.getSubscription();
  if (!subscription) return;
  await revokeTeamPushSubscription(subscription.endpoint);
  await subscription.unsubscribe();
}
