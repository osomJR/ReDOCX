// frontend/lib/secure_upload_policy.js
// Shared browser-side upload precheck for ReDOCX.
// This is a UX/security friction layer only. The backend remains the source of truth.

const MB = 1024 * 1024;

export const FILE_SECURITY_POLICY = Object.freeze({
  aiTextDocument: Object.freeze({
    label: "PDF or Word document",
    maxBytes: 10 * MB,
    extensions: Object.freeze([".pdf", ".docx"]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
      ".docx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
    }),
  }),

  documentWithImages: Object.freeze({
    label: "document or image",
    maxBytes: 10 * MB,
    extensions: Object.freeze([".pdf", ".docx", ".jpg", ".jpeg", ".png"]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
      ".docx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
      ".jpg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".jpeg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".png": Object.freeze(["image/png", "image/x-png"]),
    }),
  }),

  conversionDocument: Object.freeze({
    label: "convertible document or image",
    maxBytes: 10 * MB,
    extensions: Object.freeze([".pdf", ".docx", ".jpg", ".jpeg", ".png"]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
      ".docx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
      ".jpg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".jpeg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".png": Object.freeze(["image/png", "image/x-png"]),
    }),
  }),

  pdfTool: Object.freeze({
    label: "PDF document",
    maxBytes: 50 * MB,
    extensions: Object.freeze([".pdf"]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
    }),
  }),

  media: Object.freeze({
    label: "audio or video media",
    extensions: Object.freeze([".mp3", ".mp4", ".mkv", ".mov"]),
    maxBytesByExtension: Object.freeze({
      ".mp3": 10 * MB,
      ".mp4": 25 * MB,
      ".mkv": 25 * MB,
      ".mov": 25 * MB,
    }),
    mimeTypes: Object.freeze({
      ".mp3": Object.freeze(["audio/mpeg", "audio/mp3", "audio/x-mpeg", "audio/mpeg3"]),
      ".mp4": Object.freeze(["video/mp4", "application/mp4"]),
      ".mkv": Object.freeze(["video/x-matroska", "video/webm", "application/octet-stream"]),
      ".mov": Object.freeze(["video/quicktime", "video/mp4"]),
    }),
  }),
});

export const BATCH_UPLOAD_LIMITS_BY_PLAN = Object.freeze({
  personal: 5,
  business: 10,
  enterprise: 20,
});

const BATCH_PLAN_ALIASES = Object.freeze({
  individual: "personal",
  starter: "personal",
  pro: "personal",
  professional: "personal",
  team: "business",
  teams: "business",
  organization: "business",
  organisation: "business",
  corp: "business",
  company: "business",
  enterprise_plus: "enterprise",
  "enterprise-plus": "enterprise",
});

const INACTIVE_BATCH_STATUSES = new Set([
  "cancelled",
  "canceled",
  "expired",
  "inactive",
  "past_due",
  "unpaid",
  "free",
  "none",
  "disabled",
]);

const BATCH_PLAN_KEYS = [
  "plan",
  "plan_id",
  "plan_key",
  "plan_name",
  "plan_slug",
  "product_plan",
  "subscription_plan",
  "tier",
  "tier_id",
  "tier_name",
];

const BATCH_STATUS_KEYS = [
  "status",
  "subscription_status",
  "billing_status",
  "entitlement_status",
];

const BATCH_PAID_KEYS = [
  "is_paid",
  "paid",
  "has_paid_plan",
  "has_active_subscription",
  "active_subscription",
];

const DANGEROUS_EXTENSION_TOKENS = Object.freeze([
  ".ade", ".adp", ".apk", ".app", ".appx", ".bat", ".bin", ".cab",
  ".cmd", ".com", ".cpl", ".crt", ".dll", ".dmg", ".elf", ".exe",
  ".gadget", ".hta", ".inf", ".ins", ".ipa", ".iso", ".jar", ".js",
  ".jse", ".lnk", ".mjs", ".msi", ".msp", ".mst", ".pif", ".ps1",
  ".psm1", ".reg", ".scr", ".sh", ".sys", ".vb", ".vbe", ".vbs",
  ".ws", ".wsc", ".wsf", ".wsh",
]);

const EMPTY_OR_UNKNOWN_MIME_TYPES = new Set(["", "application/octet-stream"]);

export function getFileExtension(filename = "") {
  const cleanName = String(filename || "").trim().toLowerCase();
  const lastDot = cleanName.lastIndexOf(".");
  return lastDot === -1 ? "" : cleanName.slice(lastDot);
}

export function formatUploadLimit(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 MB";
  if (bytes % MB === 0) return `${bytes / MB} MB`;
  return `${(bytes / MB).toFixed(1)} MB`;
}

export async function readFileBytes(file, length = 32, offset = 0) {
  if (!file || typeof file.slice !== "function") return new Uint8Array();
  const buffer = await file.slice(offset, offset + length).arrayBuffer();
  return new Uint8Array(buffer);
}

export function bytesStartWith(bytes, signature) {
  if (!bytes || bytes.length < signature.length) return false;
  return signature.every((byte, index) => bytes[index] === byte);
}

export function asciiFromBytes(bytes) {
  return Array.from(bytes)
    .map((byte) => (byte >= 32 && byte <= 126 ? String.fromCharCode(byte) : " "))
    .join("");
}

function normalizeMimeType(value = "") {
  return String(value || "").trim().toLowerCase();
}

function maxBytesForExtension(policy, extension) {
  return policy.maxBytesByExtension?.[extension] || policy.maxBytes || 0;
}

function hasSuspiciousFilename(filename = "", declaredExtension = "") {
  const cleanName = String(filename || "").trim().toLowerCase();

  if (!cleanName) return true;
  if (cleanName.includes("\0")) return true;
  // Path separators are the traversal boundary. Repeated dots inside a basename
  // (for example, "report..pdf") are harmless and are not traversal by themselves.
  if (cleanName.includes("/") || cleanName.includes("\\")) return true;

  const nameWithoutDeclaredExtension = declaredExtension && cleanName.endsWith(declaredExtension)
    ? cleanName.slice(0, -declaredExtension.length)
    : cleanName;

  return DANGEROUS_EXTENSION_TOKENS.some((token) => nameWithoutDeclaredExtension.includes(token));
}

async function detectMagicMismatch(file, extension) {
  const first32 = await readFileBytes(file, 32);
  const first12Ascii = asciiFromBytes(first32.slice(0, 12));

  if (extension === ".pdf") {
    if (!bytesStartWith(first32, [0x25, 0x50, 0x44, 0x46, 0x2d])) {
      return "This file is named as a PDF, but its file signature is not PDF.";
    }
    return "";
  }

  if (extension === ".png") {
    if (!bytesStartWith(first32, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) {
      return "This file is named as a PNG image, but its file signature is not PNG.";
    }
    return "";
  }

  if (extension === ".jpg" || extension === ".jpeg") {
    if (!bytesStartWith(first32, [0xff, 0xd8, 0xff])) {
      return "This file is named as a JPEG image, but its file signature is not JPEG.";
    }
    return "";
  }

  if (extension === ".docx") {
    if (!bytesStartWith(first32, [0x50, 0x4b])) {
      return "This file is named as a DOCX document, but it is not a ZIP-based Office file.";
    }
    return "";
  }

  if (extension === ".mp3") {
    const looksLikeId3 = bytesStartWith(first32, [0x49, 0x44, 0x33]);
    const looksLikeFrame = first32[0] === 0xff && (first32[1] & 0xe0) === 0xe0;
    if (!looksLikeId3 && !looksLikeFrame) {
      return "This file is named as MP3 audio, but its file signature is not MP3.";
    }
    return "";
  }

  if (extension === ".mkv") {
    if (!bytesStartWith(first32, [0x1a, 0x45, 0xdf, 0xa3])) {
      return "This file is named as MKV video, but its file signature is not MKV/WebM.";
    }
    return "";
  }

  if (extension === ".mp4" || extension === ".mov") {
    const boxType = asciiFromBytes(first32.slice(4, 8));
    if (boxType !== "ftyp") {
      return `This file is named as ${extension.toUpperCase().slice(1)} video, but its file signature is not an MP4/MOV container.`;
    }
    return "";
  }

  if (extension === ".txt") {
    // Browser-side text validation is intentionally light; backend performs strict decoding.
    return "";
  }

  return "";
}

export async function validateBrowserUpload(file, policy, options = {}) {
  const fallbackLabel = policy?.label || "file";

  if (!file) return "No file selected.";
  if (!policy || !Array.isArray(policy.extensions)) {
    return "Upload validation policy is missing or invalid.";
  }

  const extension = getFileExtension(file.name);
  const allowedExtensions = policy.extensions;

  if (!allowedExtensions.includes(extension)) {
    return `Unsupported file type: ${extension || "unknown"}. Allowed types: ${allowedExtensions.join(", ")}.`;
  }

  if (hasSuspiciousFilename(file.name, extension)) {
    return "This filename is not safe. Rename the file and make sure it does not contain executable extensions, path characters, or traversal sequences.";
  }

  if (!Number.isFinite(file.size) || file.size <= 0) {
    return `The selected ${fallbackLabel} is empty or unreadable.`;
  }

  const maxBytes = maxBytesForExtension(policy, extension);
  if (maxBytes && file.size > maxBytes) {
    return `File is too large. Maximum allowed size is ${formatUploadLimit(maxBytes)}.`;
  }

  const mimeType = normalizeMimeType(file.type);
  const allowedMimeTypes = policy.mimeTypes?.[extension] || [];
  const shouldCheckMime = !EMPTY_OR_UNKNOWN_MIME_TYPES.has(mimeType) && allowedMimeTypes.length > 0;

  if (shouldCheckMime && !allowedMimeTypes.includes(mimeType)) {
    return `The browser reports this file as ${mimeType}, which does not match ${extension}.`;
  }

  if (options.skipMagicCheck !== true) {
    const magicError = await detectMagicMismatch(file, extension);
    if (magicError) return magicError;
  }

  return "";
}

export async function validateBrowserUploads(files, policy, options = {}) {
  const list = Array.from(files || []);

  for (const file of list) {
    const message = await validateBrowserUpload(file, policy, options);
    if (message) return { message, file };
  }

  return { message: "", file: null };
}


function normalizeBatchToken(value = "") {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/-/g, "_");
}

function readAnyKey(source, keys) {
  if (!source || typeof source !== "object") return undefined;

  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(source, key)) return source[key];
    const foundKey = Object.keys(source).find(
      (existingKey) => existingKey.toLowerCase() === key.toLowerCase(),
    );
    if (foundKey) return source[foundKey];
  }

  return undefined;
}

function collectEntitlementSources(accountOrEntitlement) {
  const sources = [];
  const add = (value) => {
    if (value && typeof value === "object" && !sources.includes(value)) {
      sources.push(value);
    }
  };

  add(accountOrEntitlement);
  add(accountOrEntitlement?.entitlement);
  add(accountOrEntitlement?.billing_entitlement);
  add(accountOrEntitlement?.subscription);
  add(accountOrEntitlement?.plan_entitlement);
  add(accountOrEntitlement?.app_metadata);
  add(accountOrEntitlement?.user_metadata);
  add(accountOrEntitlement?.claims);

  for (const source of [...sources]) {
    for (const [key, value] of Object.entries(source)) {
      const lowered = key.toLowerCase();
      if (
        value &&
        typeof value === "object" &&
        ["plan", "tier", "entitlement", "subscription", "billing"].some((token) =>
          lowered.includes(token),
        )
      ) {
        add(value);
      }
    }
  }

  return sources;
}

export function normalizeBatchPlan(accountOrEntitlement) {
  const sources = collectEntitlementSources(accountOrEntitlement);

  for (const source of sources) {
    const explicitPaid = readAnyKey(source, BATCH_PAID_KEYS);
    if (explicitPaid === false || String(explicitPaid).toLowerCase() === "false") {
      return "free";
    }
  }

  for (const source of sources) {
    const status = normalizeBatchToken(readAnyKey(source, BATCH_STATUS_KEYS));
    if (INACTIVE_BATCH_STATUSES.has(status)) return "free";
  }

  for (const source of sources) {
    const rawPlan = readAnyKey(source, BATCH_PLAN_KEYS);
    const token = normalizeBatchToken(rawPlan);
    if (token) return BATCH_PLAN_ALIASES[token] || token;
  }

  return "free";
}

export function getBatchUploadLimit(accountOrEntitlement) {
  const plan = normalizeBatchPlan(accountOrEntitlement);
  return BATCH_UPLOAD_LIMITS_BY_PLAN[plan] || 0;
}

export function canUseBatchUploads(accountOrEntitlement) {
  return getBatchUploadLimit(accountOrEntitlement) > 0;
}

export function getSameExtensionBatchSummary(files) {
  const list = Array.from(files || []);
  const extensions = list.map((file) => getFileExtension(file?.name));
  const uniqueExtensions = [...new Set(extensions.filter(Boolean))].sort();

  return {
    count: list.length,
    extension: uniqueExtensions.length === 1 ? uniqueExtensions[0] : "",
    extensions: uniqueExtensions,
    mixedExtensions: uniqueExtensions.length > 1,
    missingExtension: extensions.some((extension) => !extension),
  };
}

export async function validateBrowserBatchUploads(
  files,
  policy,
  { account, entitlement, featureLabel = "this feature", ...uploadOptions } = {},
) {
  const list = Array.from(files || []);
  const accountOrEntitlement = entitlement || account;
  const plan = normalizeBatchPlan(accountOrEntitlement);
  const limit = getBatchUploadLimit(accountOrEntitlement);

  if (limit <= 0) {
    return {
      message: "Batch processing is available only on Personal, Business, and Enterprise plans.",
      file: null,
      plan,
      limit,
    };
  }

  if (list.length === 0) {
    return { message: "Select at least one file to batch process.", file: null, plan, limit };
  }

  if (list.length > limit) {
    return {
      message: `Your ${plan} plan supports up to ${limit} uploads with the same file extension for ${featureLabel}.`,
      file: null,
      plan,
      limit,
    };
  }

  const batchSummary = getSameExtensionBatchSummary(list);
  if (batchSummary.missingExtension) {
    return {
      message: "Every file in a batch must include a valid file extension.",
      file: null,
      plan,
      limit,
    };
  }

  if (batchSummary.mixedExtensions) {
    return {
      message: `All files in a batch must use the same file extension. Selected types: ${batchSummary.extensions.join(", ")}.`,
      file: null,
      plan,
      limit,
    };
  }

  const singleFileResult = await validateBrowserUploads(list, policy, uploadOptions);
  if (singleFileResult.message) {
    return { ...singleFileResult, plan, limit, extension: batchSummary.extension };
  }

  return {
    message: "",
    file: null,
    plan,
    limit,
    extension: batchSummary.extension,
    count: list.length,
  };
}
