// Team-message attachment policy only. Do not reuse this module for ReDOCX
// document processing, conversion, OCR, e-signature, or other feature uploads.

export const TEAM_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024;

export const TEAM_ATTACHMENT_ACCEPT = [
  ".pdf",
  ".docx",
  ".xlsx",
  ".pptx",
  ".txt",
  ".csv",
  ".md",
  ".json",
  ".png",
  ".jpg",
  ".jpeg",
  ".mp3",
  ".mp4",
  ".mov",
  ".mkv",
].join(",");

export const TEAM_DOCUMENT_ATTACHMENT_ACCEPT = [
  ".pdf",
  ".docx",
  ".xlsx",
  ".pptx",
  ".txt",
  ".csv",
  ".md",
  ".json",
].join(",");

const ALLOWED_EXTENSIONS = new Set(
  TEAM_ATTACHMENT_ACCEPT.split(","),
);

const DANGEROUS_EXTENSIONS = new Set([
  ".ade", ".adp", ".apk", ".app", ".bat", ".bin", ".cmd", ".com",
  ".cpl", ".dll", ".dmg", ".exe", ".gadget", ".hta", ".inf", ".ins",
  ".iso", ".jar", ".js", ".jse", ".lnk", ".mde", ".msc", ".msi",
  ".msp", ".mst", ".nsh", ".pif", ".ps1", ".reg", ".scr", ".sh",
  ".sys", ".vb", ".vbe", ".vbs", ".ws", ".wsc", ".wsf", ".wsh",
]);

const BIDI_OR_CONTROL_RE = /[\u0000-\u001f\u007f-\u009f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u;

export class TeamAttachmentPolicyError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "TeamAttachmentPolicyError";
    this.code = code;
  }
}

export function getTeamAttachmentExtension(filename) {
  const normalized = String(filename || "").normalize("NFKC").trim();
  const dot = normalized.lastIndexOf(".");
  return dot >= 0 ? normalized.slice(dot).toLowerCase() : "";
}

export function classifyTeamAttachment(file) {
  const extension = getTeamAttachmentExtension(file?.name);
  if ([".png", ".jpg", ".jpeg"].includes(extension)) return "image";
  if (extension === ".mp3") return "audio";
  if ([".mp4", ".mov", ".mkv"].includes(extension)) return "video";
  return "document";
}

export function validateTeamAttachment(file, { documentsOnly = false } = {}) {
  if (!(file instanceof File)) {
    throw new TeamAttachmentPolicyError(
      "invalid_attachment",
      "Choose a valid attachment file.",
    );
  }

  const rawName = String(file.name || "");
  const normalizedName = rawName.normalize("NFKC").trim();
  if (
    !normalizedName ||
    normalizedName.includes("/") ||
    normalizedName.includes("\\") ||
    BIDI_OR_CONTROL_RE.test(normalizedName)
  ) {
    throw new TeamAttachmentPolicyError(
      "invalid_attachment_filename",
      "The attachment filename is not allowed.",
    );
  }

  const extension = getTeamAttachmentExtension(normalizedName);
  const allowed = documentsOnly
    ? new Set(TEAM_DOCUMENT_ATTACHMENT_ACCEPT.split(","))
    : ALLOWED_EXTENSIONS;
  if (!allowed.has(extension)) {
    throw new TeamAttachmentPolicyError(
      "unsupported_attachment_type",
      "This file type is not allowed for secure team messaging.",
    );
  }

  const suffixes = normalizedName.toLowerCase().match(/\.[a-z0-9]+/gu) || [];
  if (suffixes.slice(0, -1).some((suffix) => DANGEROUS_EXTENSIONS.has(suffix))) {
    throw new TeamAttachmentPolicyError(
      "dangerous_double_extension",
      "The attachment filename contains a dangerous double extension.",
    );
  }

  if (!Number.isFinite(file.size) || file.size <= 0) {
    throw new TeamAttachmentPolicyError(
      "empty_attachment",
      "The attachment file is empty.",
    );
  }
  if (file.size > TEAM_ATTACHMENT_MAX_BYTES) {
    throw new TeamAttachmentPolicyError(
      "attachment_too_large",
      "Attachment is too large. Maximum size is 20 MB.",
    );
  }

  return {
    extension,
    kind: classifyTeamAttachment(file),
    normalizedName,
    size: file.size,
  };
}
