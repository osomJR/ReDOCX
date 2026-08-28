// frontend/lib/secure_upload_policy.js
// Shared browser-side upload and inline-text prechecks for ReDOCX.
// This is a UX/security friction layer only. The backend remains the source of truth.

const KB = 1024;
const MB = 1024 * KB;

export const INLINE_TEXT_SECURITY_POLICY = Object.freeze({
  maxBytes: 128 * KB,
  maxChars: 50_000,
  maxWords: 5_000,
  maxLines: 5_000,
  maxLineChars: 50_000,
  maxIdenticalRun: 2_048,
  maxCombiningRun: 16,
});

export const AUXILIARY_PROMPT_SECURITY_POLICY = Object.freeze({
  maxBytes: 128 * KB,
  maxChars: 60_000,
  maxWords: 10_000,
  maxLines: 2_000,
  maxLineChars: 2_000,
  maxIdenticalRun: 1_024,
  maxCombiningRun: 16,
});

const FORBIDDEN_INLINE_TEXT_CODEPOINTS = new Set([
  0x200b,
  0x2060,
  0xfff9,
  0xfffa,
  0xfffb,
  ...Array.from({ length: 5 }, (_, index) => 0x202a + index),
  ...Array.from({ length: 4 }, (_, index) => 0x2066 + index),
]);

export const FILE_SECURITY_POLICY = Object.freeze({
  aiTextDocument: Object.freeze({
    label: "PDF or Word document",
    maxBytes: 25 * MB,
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
    maxBytes: 25 * MB,
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
    label: "convertible document, spreadsheet, presentation, HTML file, or image",
    maxBytes: 25 * MB,
    extensions: Object.freeze([
      ".pdf", ".docx", ".jpg", ".jpeg", ".png",
      ".xlsx", ".html", ".htm", ".pptx",
    ]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
      ".docx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
      ".xlsx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
      ".pptx": Object.freeze([
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/x-zip",
        "application/x-zip-compressed",
      ]),
      ".html": Object.freeze(["text/html", "application/xhtml+xml", "text/plain"]),
      ".htm": Object.freeze(["text/html", "application/xhtml+xml", "text/plain"]),
      ".jpg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".jpeg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".png": Object.freeze(["image/png", "image/x-png"]),
    }),
  }),

  pdfTool: Object.freeze({
    label: "PDF document",
    maxBytes: 100 * MB,
    extensions: Object.freeze([".pdf"]),
    mimeTypes: Object.freeze({
      ".pdf": Object.freeze(["application/pdf", "application/x-pdf"]),
    }),
  }),

  pdfEditImage: Object.freeze({
    label: "PDF edit image",
    maxBytes: 15 * MB,
    extensions: Object.freeze([".jpg", ".jpeg", ".png"]),
    mimeTypes: Object.freeze({
      ".jpg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".jpeg": Object.freeze(["image/jpeg", "image/pjpeg"]),
      ".png": Object.freeze(["image/png", "image/x-png"]),
    }),
  }),

  media: Object.freeze({
    label: "audio or video media",
    extensions: Object.freeze([
      ".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg",
      ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm",
    ]),
    maxBytesByExtension: Object.freeze({
      ".mp3": 25 * MB,
      ".wav": 25 * MB,
      ".aac": 25 * MB,
      ".flac": 25 * MB,
      ".m4a": 25 * MB,
      ".ogg": 25 * MB,
      ".mp4": 100 * MB,
      ".mov": 100 * MB,
      ".avi": 100 * MB,
      ".mkv": 100 * MB,
      ".wmv": 100 * MB,
      ".webm": 25 * MB,
    }),
    mimeTypes: Object.freeze({
      ".mp3": Object.freeze([
        "audio/mpeg",
        "audio/mp3",
        "audio/x-mpeg",
        "audio/mpeg3",
      ]),
      ".wav": Object.freeze([
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/vnd.wave",
      ]),
      ".aac": Object.freeze(["audio/aac", "audio/aacp", "audio/x-aac"]),
      ".flac": Object.freeze(["audio/flac", "audio/x-flac"]),
      ".webm": Object.freeze([
        "audio/webm",
        "video/webm",
        "application/octet-stream",
      ]),
      ".m4a": Object.freeze([
        "audio/mp4",
        "audio/x-m4a",
        "application/mp4",
      ]),
      ".ogg": Object.freeze(["audio/ogg", "application/ogg"]),
      ".mp4": Object.freeze(["video/mp4", "application/mp4"]),
      ".mov": Object.freeze(["video/quicktime", "video/mp4"]),
      ".avi": Object.freeze([
        "video/x-msvideo",
        "video/avi",
        "video/msvideo",
      ]),
      ".mkv": Object.freeze([
        "video/x-matroska",
        "video/webm",
        "application/octet-stream",
      ]),
      ".wmv": Object.freeze([
        "video/x-ms-wmv",
        "video/x-ms-asf",
        "application/vnd.ms-asf",
      ]),
    }),
  }),
});

export const BATCH_UPLOAD_LIMITS_BY_PLAN = Object.freeze({
  personal: 20,
  business: 50,
  enterprise: 100,
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

const UNICODE_FORMAT_CONTROL_RE = /\p{Cf}/u;
const UNICODE_COMBINING_MARK_RE = /\p{M}/u;

export function validateBrowserSafeText(
  value,
  {
    policy = INLINE_TEXT_SECURITY_POLICY,
    fieldName = "Inline text",
  } = {},
) {
  if (typeof value !== "string") return `${fieldName} must be a string.`;
  if (!policy || typeof policy !== "object") {
    return `${fieldName} validation policy is missing or invalid.`;
  }

  const rawCharacters = Array.from(value);
  if (rawCharacters.length > policy.maxChars * 2) {
    return `${fieldName} is too long. Maximum allowed length is ${policy.maxChars.toLocaleString()} characters.`;
  }

  const encoder = new TextEncoder();
  if (encoder.encode(value).byteLength > policy.maxBytes) {
    return `${fieldName} is too large. Maximum allowed size is ${policy.maxBytes.toLocaleString()} UTF-8 bytes.`;
  }

  let normalized = value.replace(/\r\n?/g, "\n");
  if (normalized.startsWith("\uFEFF")) normalized = normalized.slice(1);

  try {
    normalized = normalized.normalize("NFC").trim();
  } catch {
    return `${fieldName} contains invalid Unicode data.`;
  }

  if (!normalized) return `${fieldName} cannot be empty.`;

  const characters = Array.from(normalized);
  if (characters.length > policy.maxChars) {
    return `${fieldName} is too long. Maximum allowed length is ${policy.maxChars.toLocaleString()} characters.`;
  }

  const byteLength = encoder.encode(normalized).byteLength;
  if (byteLength > policy.maxBytes) {
    return `${fieldName} is too large. Maximum allowed size is ${policy.maxBytes.toLocaleString()} UTF-8 bytes.`;
  }

  const lines = normalized.split("\n");
  if (lines.length > policy.maxLines) {
    return `${fieldName} contains too many lines. Maximum allowed is ${policy.maxLines.toLocaleString()}.`;
  }
  if (lines.some((line) => Array.from(line).length > policy.maxLineChars)) {
    return `${fieldName} contains a line longer than ${policy.maxLineChars.toLocaleString()} characters.`;
  }

  if (Number.isFinite(policy.maxWords)) {
    const wordCount = normalized.split(/\s+/u).filter(Boolean).length;
    if (wordCount > policy.maxWords) {
      return `${fieldName} contains too many words. Maximum allowed is ${policy.maxWords.toLocaleString()}.`;
    }
  }

  let previous = "";
  let identicalRun = 0;
  let combiningRun = 0;

  for (const character of characters) {
    const codepoint = character.codePointAt(0);

    if (codepoint >= 0xd800 && codepoint <= 0xdfff) {
      return `${fieldName} contains invalid Unicode data.`;
    }

    if (
      (codepoint < 0x20 && character !== "\t" && character !== "\n") ||
      (codepoint >= 0x7f && codepoint <= 0x9f)
    ) {
      return `${fieldName} contains a forbidden control character.`;
    }

    if (FORBIDDEN_INLINE_TEXT_CODEPOINTS.has(codepoint)) {
      return `${fieldName} contains an unsafe invisible or bidirectional control character.`;
    }

    if (
      UNICODE_FORMAT_CONTROL_RE.test(character) &&
      codepoint !== 0x200c &&
      codepoint !== 0x200d
    ) {
      if (codepoint === 0xfeff) {
        return `${fieldName} contains an unexpected byte-order mark.`;
      }
      return `${fieldName} contains an unsafe invisible or formatting control character.`;
    }

    if (
      (codepoint >= 0xfdd0 && codepoint <= 0xfdef) ||
      (codepoint & 0xffff) === 0xfffe ||
      (codepoint & 0xffff) === 0xffff
    ) {
      return `${fieldName} contains an invalid Unicode noncharacter.`;
    }

    if (character === previous) {
      identicalRun += 1;
    } else {
      previous = character;
      identicalRun = 1;
    }

    if (identicalRun > policy.maxIdenticalRun) {
      return `${fieldName} contains an excessively repeated character sequence.`;
    }

    if (UNICODE_COMBINING_MARK_RE.test(character)) {
      combiningRun += 1;
      if (combiningRun > policy.maxCombiningRun) {
        return `${fieldName} contains an excessive combining-mark sequence.`;
      }
    } else {
      combiningRun = 0;
    }
  }

  return "";
}

export function validateBrowserInlineText(
  value,
  policy = INLINE_TEXT_SECURITY_POLICY,
) {
  return validateBrowserSafeText(value, { policy, fieldName: "Inline text" });
}

export function validateBrowserAuxiliaryPromptText(
  value,
  fieldName = "Prompt input",
) {
  return validateBrowserSafeText(value, {
    policy: AUXILIARY_PROMPT_SECURITY_POLICY,
    fieldName,
  });
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
  return String(value || "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
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

  if (extension === ".docx" || extension === ".xlsx" || extension === ".pptx") {
    if (!bytesStartWith(first32, [0x50, 0x4b])) {
      return `This file is named as ${extension.toUpperCase().slice(1)}, but it is not a ZIP-based Office file.`;
    }
    return "";
  }

  if (extension === ".html" || extension === ".htm") {
    // Backend performs strict UTF-8 and active-content checks. HTML has no stable
    // magic-byte signature, so browser validation is intentionally metadata-only.
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

  if (extension === ".wav" || extension === ".avi") {
    const isRiff = bytesStartWith(first32, [0x52, 0x49, 0x46, 0x46]);
    const formType = asciiFromBytes(first32.slice(8, 12));
    const expectedFormType = extension === ".wav" ? "WAVE" : "AVI ";
    if (!isRiff || formType !== expectedFormType) {
      return `This file is named as ${extension.toUpperCase().slice(1)}, but its RIFF signature does not match the declared format.`;
    }
    return "";
  }

  if (extension === ".flac") {
    if (!bytesStartWith(first32, [0x66, 0x4c, 0x61, 0x43])) {
      return "This file is named as FLAC audio, but its file signature is not FLAC.";
    }
    return "";
  }

  if (extension === ".aac") {
    const looksLikeAdif = bytesStartWith(first32, [0x41, 0x44, 0x49, 0x46]);
    const looksLikeAdts = first32[0] === 0xff && (first32[1] & 0xf6) === 0xf0;
    const looksLikeId3 = bytesStartWith(first32, [0x49, 0x44, 0x33]);
    if (!looksLikeAdif && !looksLikeAdts && !looksLikeId3) {
      return "This file is named as AAC audio, but its file signature is not AAC.";
    }
    return "";
  }

  if (extension === ".wmv") {
    if (!bytesStartWith(first32, [
      0x30, 0x26, 0xb2, 0x75, 0x8e, 0x66, 0xcf, 0x11,
      0xa6, 0xd9, 0x00, 0xaa, 0x00, 0x62, 0xce, 0x6c,
    ])) {
      return "This file is named as WMV video, but its file signature is not ASF/WMV.";
    }
    return "";
  }

  if (extension === ".ogg") {
    if (!bytesStartWith(first32, [0x4f, 0x67, 0x67, 0x53])) {
      return "This file is named as OGG audio, but its file signature is not Ogg.";
    }
    return "";
  }

  if (extension === ".webm" || extension === ".mkv") {
    if (!bytesStartWith(first32, [0x1a, 0x45, 0xdf, 0xa3])) {
      return `This file is named as ${extension.toUpperCase().slice(1)}, but its file signature is not WebM/Matroska.`;
    }
    return "";
  }

  if (extension === ".m4a" || extension === ".mp4" || extension === ".mov") {
    const boxType = asciiFromBytes(first32.slice(4, 8));
    if (boxType !== "ftyp") {
      return `This file is named as ${extension.toUpperCase().slice(1)}, but its file signature is not an MP4/MOV container.`;
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

export async function partitionDuplicateBrowserUploads(files = []) {
  const fileList = Array.from(files || []).filter(Boolean);
  const acceptedFiles = [];
  const duplicates = [];
  const acceptedIndexesByObject = new Map();

  for (const [index, file] of fileList.entries()) {
    const originalIndex = acceptedIndexesByObject.get(file);
    if (originalIndex !== undefined) {
      duplicates.push({
        file,
        index,
        originalFile: fileList[originalIndex],
        originalIndex,
      });
      continue;
    }
    acceptedIndexesByObject.set(file, index);
    acceptedFiles.push(file);
  }

  if (!globalThis.crypto?.subtle || acceptedFiles.length < 2) {
    return { acceptedFiles, duplicates };
  }

  const filesBySize = new Map();
  for (const file of acceptedFiles) {
    const sizeKey = Number.isFinite(file?.size) ? file.size : "unknown";
    const bucket = filesBySize.get(sizeKey) || [];
    bucket.push(file);
    filesBySize.set(sizeKey, bucket);
  }

  const contentDuplicates = new Set();
  for (const bucket of filesBySize.values()) {
    if (bucket.length < 2) continue;

    const seenHashes = new Map();
    for (const file of bucket) {
      const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
      const hash = Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      const originalFile = seenHashes.get(hash);

      if (originalFile) {
        contentDuplicates.add(file);
        duplicates.push({
          file,
          index: fileList.indexOf(file),
          originalFile,
          originalIndex: fileList.indexOf(originalFile),
        });
        continue;
      }

      seenHashes.set(hash, file);
    }
  }

  return {
    acceptedFiles: acceptedFiles.filter((file) => !contentDuplicates.has(file)),
    duplicates,
  };
}

export async function getDuplicateBrowserUploadMessage(files = []) {
  const { duplicates } = await partitionDuplicateBrowserUploads(files);
  const duplicate = duplicates[0];
  if (!duplicate) return "";

  return `Duplicate file rejected: "${duplicate.file.name}" has the same content as "${duplicate.originalFile.name}". The other selected files can still be processed.`;
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
  const submittedFiles = Array.from(files || []);
  const accountOrEntitlement = entitlement || account;
  const plan = normalizeBatchPlan(accountOrEntitlement);
  const limit = getBatchUploadLimit(accountOrEntitlement);

  if (limit <= 0) {
    return {
      message: "Batch processing is available only on Personal, Business, and Enterprise plans.",
      file: null,
      files: [],
      duplicates: [],
      plan,
      limit,
    };
  }

  if (submittedFiles.length === 0) {
    return {
      message: "Select at least one file to batch process.",
      file: null,
      files: [],
      duplicates: [],
      plan,
      limit,
    };
  }

  const { acceptedFiles, duplicates } =
    await partitionDuplicateBrowserUploads(submittedFiles);

  if (acceptedFiles.length > limit) {
    return {
      message: `Your ${plan} plan supports up to ${limit} unique uploads with the same file extension for ${featureLabel}.`,
      file: null,
      files: acceptedFiles,
      duplicates,
      plan,
      limit,
    };
  }

  const batchSummary = getSameExtensionBatchSummary(acceptedFiles);
  if (batchSummary.missingExtension) {
    return {
      message: "Every file in a batch must include a valid file extension.",
      file: null,
      files: acceptedFiles,
      duplicates,
      plan,
      limit,
    };
  }

  if (batchSummary.mixedExtensions) {
    return {
      message: `All files in a batch must use the same file extension. Selected types: ${batchSummary.extensions.join(", ")}.`,
      file: null,
      files: acceptedFiles,
      duplicates,
      plan,
      limit,
    };
  }

  const singleFileResult = await validateBrowserUploads(
    acceptedFiles,
    policy,
    uploadOptions,
  );
  if (singleFileResult.message) {
    return {
      ...singleFileResult,
      files: acceptedFiles,
      duplicates,
      plan,
      limit,
      extension: batchSummary.extension,
    };
  }

  return {
    message: "",
    duplicateMessage: duplicates.length
      ? `${duplicates.length} duplicate file${duplicates.length === 1 ? "" : "s"} will be rejected while the unique files continue.`
      : "",
    file: null,
    files: acceptedFiles,
    duplicates,
    plan,
    limit,
    extension: batchSummary.extension,
    count: acceptedFiles.length,
  };
}
