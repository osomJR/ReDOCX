// Preserve normal Response semantics, including status and Content-Disposition.
// Never forward cookies, bearer tokens or signing tokens to the bucket.
export async function fetchReDOCXDownload(url, options = {}) {
  if (typeof window === "undefined" || process.env.NEXT_PUBLIC_RAILWAY_BUCKET_UPLOADS !== "true") return fetch(url, options);
  const target = new URL(url, window.location.origin);
  const isDownload = target.origin === window.location.origin && (
    /^\/api\/(?:v1\/)?analyzer\/artifacts\//u.test(target.pathname)
    || /^\/api\/analyzer\/vault\/items\/[^/]+\/download$/u.test(target.pathname)
    || /^\/api\/sign\/(?:recipient|completed)\/document$/u.test(target.pathname));
  if (!isDownload || (options.method || "GET").toUpperCase() !== "GET") return fetch(url,options);
  const headers = new Headers(options.headers || {});
  headers.set("X-ReDOCX-Download-Link","1");
  const res = await fetch(target.href,{...options,credentials:"same-origin",headers,redirect:"error",cache:"no-store"});
  if (!res.ok || res.headers.get("X-ReDOCX-Bucket-Delivery") !== "1") return res;
  const {download_url} = await res.json();
  const signed = new URL(download_url);
  if(signed.protocol!=="https:" || signed.username || signed.password || !signed.searchParams.has("X-Amz-Signature")) throw new Error("Invalid temporary download location.");
  const downloadHeaders=new Headers();
  if(headers.has("Range")) downloadHeaders.set("Range",headers.get("Range"));
  return fetch(signed.href,{method:"GET",headers:downloadHeaders,credentials:"omit",cache:"no-store",redirect:"error",signal:options.signal,referrerPolicy:"no-referrer"});
}
