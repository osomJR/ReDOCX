// Binary transport only. The analyzer continues receiving ordinary multipart inputs.
export const DIRECT_UPLOAD_THRESHOLD = 4 * 1024 * 1024;
export const STAGED_UPLOAD_HEADER = "X-ReDOCX-Staged-Upload";

export function splitUploadForm(formData) {
  const files = [];
  const metadata = new FormData();
  let totalBytes = 0;
  for (const [field, value] of formData.entries()) {
    if (typeof value === "string") metadata.append(field, value);
    else {
      files.push({ field, file: value });
      totalBytes += value.size;
    }
  }
  return { files, metadata, totalBytes };
}

export async function prepareAnalyzerUpload(feature, formData, { headers = {}, signal, requestStage, onProgress } = {}) {
  // Route by size, independent of the build-time public environment flag.
  // Railway's server-side staging endpoint is the authoritative rollout switch.
  // Never fall back to sending an oversized multipart form through Vercel.
  if (!(formData instanceof FormData)) {
    return { body: formData, headers };
  }
  const { files, metadata, totalBytes } = splitUploadForm(formData);
  const metadataEntries = Array.from(metadata.entries());
  const metadataText = JSON.stringify(metadataEntries);
  const metadataBytes = new TextEncoder().encode(metadataText).length;
  // 4 MiB of binary plus a modest multipart envelope remains on the legacy path.
  // A large edit JSON or unusually large form envelope must also bypass Vercel.
  const overhead = 1024 + 512 * (files.length + metadataEntries.length)
    + files.reduce((n, {field,file}) => n + 4 * (field.length + file.name.length + file.type.length), 0);
  const stageMetadata = metadataBytes + overhead + (totalBytes <= DIRECT_UPLOAD_THRESHOLD ? totalBytes : 0) > 4_400_000
    || metadataEntries.some(([,value]) => new TextEncoder().encode(value).length > 900_000);
  if (totalBytes <= DIRECT_UPLOAD_THRESHOLD && !stageMetadata) return { body: formData, headers };
  if (stageMetadata) {
    if (metadataBytes > 16 * 1024 * 1024) throw new Error("Upload form metadata is too large.");
    files.push({field:"_redocx_form_metadata",file:new File([metadataText],"form.json",{type:"application/json"})});
  }
  const manifest = files.map(({ field, file }) => ({ field, filename: file.name,
    size: file.size, content_type: file.type || "application/octet-stream" }));
  const ticket = await requestStage(feature, manifest);
  if (!ticket || typeof ticket.upload_id !== "string" || !Array.isArray(ticket.uploads) || ticket.uploads.length !== files.length) {
    throw new Error("Invalid large-upload authorization response.");
  }
  // Bounded concurrency; only the signed bucket form is sent to storage, never Auth0 or cookies.
  let cursor = 0;
  let uploadedBytes = 0;
  const uploadTotal = files.reduce((sum, entry) => sum + entry.file.size, 0);
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  const worker = async () => {
    while (cursor < files.length) {
      const index = cursor++;
      const target = ticket.uploads[index];
      if (!target?.url || new URL(target.url).protocol !== "https:" || !target.fields || typeof target.fields !== "object") {
        throw new Error("Invalid bucket upload authorization.");
      }
      const form = new FormData();
      for (const [name, value] of Object.entries(target.fields)) form.append(name, String(value));
      form.append("file", files[index].file);
      const response = await fetch(target.url, { method: "POST", body: form, credentials: "omit",
        cache: "no-store", redirect: "error", signal: controller.signal });
      if (!response.ok) throw new Error("Large file upload failed. Please try again.");
      uploadedBytes += files[index].file.size;
      onProgress?.({ uploadedBytes, totalBytes: uploadTotal });
    }
  };
  try {
    const workers = Array.from({ length: Math.min(3, files.length) }, () => worker().catch(error => {
      controller.abort();
      throw error;
    }));
    const settled = await Promise.allSettled(workers);
    const failed = settled.find(result => result.status === "rejected");
    if (failed) throw failed.reason;
  } finally {
    signal?.removeEventListener("abort", abort);
  }
  return { body: stageMetadata ? new FormData() : metadata, headers: { ...headers, [STAGED_UPLOAD_HEADER]: ticket.upload_id } };
}
