/* ReDOCX team message and call push service worker. */

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data?.json?.() || {};
  } catch {
    payload = { title: "ReDOCX", body: event.data?.text?.() || "" };
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || "ReDOCX", {
      body: payload.body || "",
      icon: payload.icon || "/icons/icon-192.png",
      badge: payload.badge || "/icons/badge-72.png",
      tag: payload.tag,
      renotify: Boolean(payload.renotify),
      requireInteraction: Boolean(payload.requireInteraction),
      actions: Array.isArray(payload.actions) ? payload.actions : [],
      data: payload.data || {},
    }),
  );
});

async function focusOrOpen(url) {
  const targetUrl = new URL(url || "/team", self.location.origin).href;
  const windows = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  });

  for (const client of windows) {
    if ("navigate" in client) await client.navigate(targetUrl);
    if ("focus" in client) return client.focus();
  }
  return self.clients.openWindow(targetUrl);
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};

  if (event.action === "decline" && data.decline_url) {
    event.waitUntil(
      fetch(data.decline_url, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: { Accept: "application/json" },
      })
        .then((response) => {
          if (!response.ok) return focusOrOpen(data.url || "/team");
          return undefined;
        })
        .catch(() => focusOrOpen(data.url || "/team")),
    );
    return;
  }

  const destination =
    event.action === "join"
      ? data.join_url || data.url
      : data.url || data.join_url || "/team";
  event.waitUntil(focusOrOpen(destination));
});
