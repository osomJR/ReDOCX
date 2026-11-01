// Browser prechecks for Vault only. The backend verifies the current entitlement
// and actual multipart stream sizes again before storing anything.
export const VAULT_UPLOAD_LIMITS = Object.freeze({
  free: Object.freeze({ maxFiles: 5, maxTotalBytes: 20 * 1024 * 1024 }),
  personal: Object.freeze({ maxFiles: 20, maxTotalBytes: 100 * 1024 * 1024 }),
  business: Object.freeze({ maxFiles: 50, maxTotalBytes: 250 * 1024 * 1024 }),
  enterprise: Object.freeze({ maxFiles: 100, maxTotalBytes: 500 * 1024 * 1024 }),
});

export function vaultUploadPolicy(entitlement) {
  const candidate = String(entitlement?.plan || "free").trim().toLowerCase();
  const plan = Object.hasOwn(VAULT_UPLOAD_LIMITS, candidate)
    && (candidate === "free" || entitlement?.is_paid === true) ? candidate : "free";
  return { plan, ...VAULT_UPLOAD_LIMITS[plan] };
}

export function vaultUploadErrorCode(files, policy) {
  if (!files?.length) return "INPUT_FILE_REQUIRED";
  if (files.length > policy.maxFiles) {
    return `VAULT_${policy.plan.toUpperCase()}_UPLOAD_COUNT_EXCEEDED`;
  }
  let total = 0;
  for (const file of files) {
    if (!Number.isSafeInteger(file?.size) || file.size <= 0) return "FILE_EMPTY";
    total += file.size;
    if (total > policy.maxTotalBytes) {
      return `VAULT_${policy.plan.toUpperCase()}_UPLOAD_SIZE_EXCEEDED`;
    }
  }
  return "";
}
