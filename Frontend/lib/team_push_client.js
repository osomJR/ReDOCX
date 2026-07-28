import {
  getTeamPushPublicKey,
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

  const configuredKey = String(vapidPublicKey || "").trim();
  const publicKeyRequest = configuredKey
    ? Promise.resolve(configuredKey)
    : getTeamPushPublicKey().then((data) =>
        String(data?.public_key || data?.publicKey || "").trim(),
      );
  // Invoke the browser permission request synchronously from the click handler.
  // Waiting for the configuration request first would consume the transient
  // user activation required by several browsers.
  const permissionRequest =
    Notification.permission === "granted"
      ? Promise.resolve("granted")
      : Notification.requestPermission();
  const [normalizedKey, permission] = await Promise.all([
    publicKeyRequest,
    permissionRequest,
  ]);

  if (permission !== "granted") {
    throw new Error("Notification permission was not granted.");
  }
  if (!normalizedKey) {
    throw new Error("Web-push is not configured on the server.");
  }

  const registration = await navigator.serviceWorker.register("/team-push-sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  await registration.update().catch(() => {});
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