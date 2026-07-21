from __future__ import annotations

"""Security boundary for Business/Enterprise team-message attachments.

This module is intentionally independent from the feature-processing upload
pipeline.  It validates, malware-scans, and encrypts only files attached to a
team conversation.  No function in this module is used by document analysis,
conversion, OCR, e-signature, or any other ReDOCX feature.
"""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Any, BinaryIO, Mapping
import unicodedata
from urllib.parse import urlsplit
import warnings
import zipfile
from xml.etree import ElementTree

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


logger = logging.getLogger(__name__)

MIB = 1024 * 1024
DEFAULT_TEAM_SECURE_ATTACHMENT_MAX_BYTES = 20 * MIB
ABSOLUTE_TEAM_SECURE_ATTACHMENT_MAX_BYTES = 20 * MIB
DEFAULT_TEAM_SECURE_ATTACHMENT_ORG_QUOTA_BYTES = 10 * 1024 * MIB
TEAM_ATTACHMENT_VALIDATION_VERSION = "team-attachment-v1"
TEAM_ATTACHMENT_ENCRYPTION_ALGORITHM = "AES-256-GCM"
TEAM_ATTACHMENT_AAD_VERSION = 1
TEAM_ATTACHMENT_TEMP_PREFIX = "redocx_team_attachment_"
MAX_ARCHIVE_ENTRIES = 500
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 20 * MIB
MAX_ARCHIVE_COMPRESSION_RATIO = 500
MAX_IMAGE_PIXELS = 40_000_000
MAX_PDF_PAGES = 2_000
MAX_PDF_XREFS = 100_000
MAX_MEDIA_STREAMS = 16
MAX_MEDIA_DURATION_SECONDS = 4 * 60 * 60

ALLOWED_TYPES: dict[str, tuple[str, str]] = {
    ".pdf": ("document", "application/pdf"),
    ".docx": (
        "document",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".xlsx": (
        "document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".pptx": (
        "document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".txt": ("document", "text/plain"),
    ".csv": ("document", "text/csv"),
    ".md": ("document", "text/markdown"),
    ".json": ("document", "application/json"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".mp3": ("audio", "audio/mpeg"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".mkv": ("video", "video/x-matroska"),
}

DANGEROUS_SUFFIXES = {
    ".ade", ".adp", ".apk", ".app", ".bat", ".bin", ".cmd", ".com",
    ".cpl", ".dll", ".dmg", ".exe", ".gadget", ".hta", ".inf", ".ins",
    ".iso", ".jar", ".js", ".jse", ".lnk", ".mde", ".msc", ".msi",
    ".msp", ".mst", ".nsh", ".pif", ".ps1", ".reg", ".scr", ".sh",
    ".sys", ".vb", ".vbe", ".vbs", ".ws", ".wsc", ".wsf", ".wsh",
}

WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul", "clock$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}

BIDI_CONTROL_CHARACTERS = {
    "\u061c", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c",
    "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069",
}

PDF_ACTIVE_CONTENT_RE = re.compile(
    rb"/(?:javascript|js|openaction|aa|launch|richmedia|embeddedfile|xfa)(?![a-z0-9])",
    re.IGNORECASE,
)

OOXML_BLOCKED_PATH_PARTS = (
    "/activex/",
    "/embeddings/",
    "/externallinks/",
    "/querytables/",
    "/webextensions/",
    "customui/",
)

KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

TEAM_ATTACHMENT_SCAN_POLICY_ENV = "TEAM_ATTACHMENT_SCAN_POLICY"
TEAM_ATTACHMENT_SCAN_POLICY_STRICT = "strict"
TEAM_ATTACHMENT_SCAN_POLICY_BEST_EFFORT = "best_effort"
SCANNER_UNAVAILABLE_CODES = frozenset({
    "attachment_scanner_unavailable",
    "attachment_scanner_database_unavailable",
    "attachment_scanner_database_stale",
})


class TeamAttachmentSecurityError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


@dataclass(frozen=True)
class PreparedTeamAttachment:
    kind: str
    original_filename: str
    stored_filename: str
    storage_key: str
    content_type: str
    detected_content_type: str
    file_size_bytes: int
    checksum_sha256: str
    validation_version: str
    malware_scan_status: str
    malware_scanner: str
    malware_scanner_version: str
    scan_completed_at: datetime
    encryption_algorithm: str
    encryption_key_id: str
    encryption_nonce: bytes
    encryption_aad_version: int
    encrypted_content: bytes
    security_metadata: dict[str, Any]

    def safe_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "original_filename": self.original_filename,
            "content_type": self.content_type,
            "file_size_bytes": self.file_size_bytes,
            "security_status": "secured",
            "malware_scan_status": self.malware_scan_status,
            "available_for_download": self.malware_scan_status in {"clean", "failed"},
        }


@dataclass(frozen=True)
class StagedTeamAttachment:
    kind: str
    original_filename: str
    stored_filename: str
    storage_key: str
    content_type: str
    detected_content_type: str
    file_size_bytes: int
    checksum_sha256: str
    validation_version: str
    malware_scan_status: str
    malware_scanner: str
    malware_scanner_version: str
    scan_completed_at: datetime
    encryption_algorithm: str
    encryption_key_id: str
    encryption_nonce: bytes
    encryption_aad_version: int
    encrypted_content_path: Path
    security_metadata: dict[str, Any]
    cleanup_directory: Path

    def read_encrypted_content(self) -> bytes:
        return self.encrypted_content_path.read_bytes()

    def cleanup(self) -> None:
        shutil.rmtree(self.cleanup_directory, ignore_errors=True)

    def safe_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "original_filename": self.original_filename,
            "content_type": self.content_type,
            "file_size_bytes": self.file_size_bytes,
            "security_status": "secured",
            "malware_scan_status": self.malware_scan_status,
            "available_for_download": self.malware_scan_status in {"clean", "failed"},
        }


def _error(status_code: int, code: str, message: str) -> TeamAttachmentSecurityError:
    return TeamAttachmentSecurityError(status_code, code, message)


def _team_attachment_scan_policy() -> str:
    raw = os.getenv(
        TEAM_ATTACHMENT_SCAN_POLICY_ENV,
        TEAM_ATTACHMENT_SCAN_POLICY_STRICT,
    ).strip().lower()
    return (
        TEAM_ATTACHMENT_SCAN_POLICY_BEST_EFFORT
        if raw in {"best_effort", "best-effort", "besteffort"}
        else TEAM_ATTACHMENT_SCAN_POLICY_STRICT
    )


def _best_effort_scan_result(reason: str) -> tuple[str, str, str, str]:
    logger.warning(
        "Team attachment malware scan unavailable; proceeding with mandatory "
        "structural validation because %s=%s. reason=%s",
        TEAM_ATTACHMENT_SCAN_POLICY_ENV,
        TEAM_ATTACHMENT_SCAN_POLICY_BEST_EFFORT,
        reason,
    )
    return "failed", "unavailable", "unavailable", reason[:240]


def get_team_secure_attachment_max_bytes() -> int:
    raw = os.getenv(
        "TEAM_SECURE_ATTACHMENT_MAX_BYTES",
        str(DEFAULT_TEAM_SECURE_ATTACHMENT_MAX_BYTES),
    ).strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = DEFAULT_TEAM_SECURE_ATTACHMENT_MAX_BYTES
    return max(MIB, min(configured, ABSOLUTE_TEAM_SECURE_ATTACHMENT_MAX_BYTES))


def get_team_secure_attachment_org_quota_bytes() -> int:
    raw = os.getenv(
        "TEAM_SECURE_ATTACHMENT_ORG_QUOTA_BYTES",
        str(DEFAULT_TEAM_SECURE_ATTACHMENT_ORG_QUOTA_BYTES),
    ).strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = DEFAULT_TEAM_SECURE_ATTACHMENT_ORG_QUOTA_BYTES
    return max(get_team_secure_attachment_max_bytes(), min(configured, 1024 * 1024 * MIB))


def normalize_team_attachment_filename(filename: str | None) -> str:
    raw = str(filename or "").strip()
    if not raw:
        raise _error(422, "invalid_attachment_filename", "Attachment filename is required.")

    normalized = unicodedata.normalize("NFKC", raw)
    if any(character in normalized for character in ("/", "\\", "\x00")):
        raise _error(
            422,
            "invalid_attachment_filename",
            "Attachment filename must not contain a path.",
        )
    if any(
        character in BIDI_CONTROL_CHARACTERS
        or unicodedata.category(character) in {"Cc", "Cf"}
        for character in normalized
    ):
        raise _error(
            422,
            "invalid_attachment_filename",
            "Attachment filename contains unsupported control characters.",
        )

    normalized = normalized.strip(" .")
    if not normalized or normalized.startswith("."):
        raise _error(422, "invalid_attachment_filename", "Attachment filename is invalid.")

    suffixes = [suffix.lower() for suffix in Path(normalized).suffixes]
    extension = suffixes[-1] if suffixes else ""
    if extension not in ALLOWED_TYPES:
        raise _error(
            422,
            "unsupported_attachment_type",
            "This file type is not allowed for secure team messaging.",
        )
    if any(suffix in DANGEROUS_SUFFIXES for suffix in suffixes[:-1]):
        raise _error(
            422,
            "dangerous_double_extension",
            "Attachment filename contains a dangerous double extension.",
        )

    stem = normalized[: -len(extension)] if extension else normalized
    if stem.split(".", 1)[0].strip().lower() in WINDOWS_RESERVED_NAMES:
        raise _error(422, "invalid_attachment_filename", "Attachment filename is reserved.")

    safe_stem = "".join(
        character
        if character.isalnum() or character in {" ", ".", "-", "_", "(", ")", "[", "]"}
        else "_"
        for character in stem
    ).strip(" .")
    safe_stem = re.sub(r"\s+", " ", safe_stem)
    if not safe_stem:
        safe_stem = "attachment"

    # Preserve the validated extension while bounding both characters and UTF-8 bytes.
    safe_stem = safe_stem[: 180 - len(extension)]
    while len((safe_stem + extension).encode("utf-8")) > 240 and safe_stem:
        safe_stem = safe_stem[:-1]
    if not safe_stem:
        safe_stem = "attachment"
    return f"{safe_stem}{extension}"


def _decode_key(encoded: str, key_id: str) -> bytes:
    value = str(encoded or "").strip()
    if not value:
        raise _error(
            503,
            "attachment_encryption_not_configured",
            "Secure attachment encryption is not configured.",
        )
    padded = value + "=" * (-len(value) % 4)
    try:
        key = base64.b64decode(
            padded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            f"Secure attachment encryption key {key_id!r} is invalid.",
        ) from exc
    if len(key) != 32:
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "Secure attachment encryption keys must decode to exactly 32 bytes.",
        )
    return key


def _organization_key_id_from_environment(organization_id: int) -> str | None:
    raw = os.getenv("TEAM_ATTACHMENT_ORG_ACTIVE_KEY_IDS_JSON", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "TEAM_ATTACHMENT_ORG_ACTIVE_KEY_IDS_JSON must be valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "TEAM_ATTACHMENT_ORG_ACTIVE_KEY_IDS_JSON must contain an organization-to-key map.",
        )
    value = parsed.get(str(int(organization_id)))
    if value is None:
        return None
    resolved = str(value).strip()
    if not KEY_ID_RE.fullmatch(resolved):
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "A tenant secure attachment encryption key identifier is invalid.",
        )
    return resolved


def resolve_team_attachment_active_key_id(organization_id: int) -> str:
    """Resolve the tenant key version without storing key material in PostgreSQL.

    The policy table selects a key ID. Key material remains in the deployment
    keyring (or an external KMS adapter added behind the same interface).
    """

    selected: str | None = None
    try:
        from psycopg import errors as psycopg_errors
        from backend.database import get_db

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT active_encryption_key_id
                    FROM organization_communication_policies
                    WHERE organization_id = %s
                    """,
                    (int(organization_id),),
                )
                row = cur.fetchone()
                selected = str(row[0]).strip() if row and row[0] else None
    except (psycopg_errors.UndefinedTable, psycopg_errors.UndefinedColumn):
        selected = None

    selected = selected or _organization_key_id_from_environment(organization_id)
    selected = selected or os.getenv("TEAM_ATTACHMENT_ACTIVE_KEY_ID", "v1").strip() or "v1"
    if not KEY_ID_RE.fullmatch(selected):
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "The resolved tenant secure attachment encryption key identifier is invalid.",
        )
    return selected


def load_team_attachment_keyring(
    *,
    active_key_id: str | None = None,
) -> tuple[str, dict[str, bytes]]:
    active_key_id = (
        str(active_key_id or "").strip()
        or os.getenv("TEAM_ATTACHMENT_ACTIVE_KEY_ID", "v1").strip()
        or "v1"
    )
    if not KEY_ID_RE.fullmatch(active_key_id):
        raise _error(
            503,
            "attachment_encryption_configuration_invalid",
            "The secure attachment active key identifier is invalid.",
        )

    raw_keyring = os.getenv("TEAM_ATTACHMENT_ENCRYPTION_KEYS_JSON", "").strip()
    encoded_keys: dict[str, str]
    if raw_keyring:
        try:
            parsed = json.loads(raw_keyring)
        except json.JSONDecodeError as exc:
            raise _error(
                503,
                "attachment_encryption_configuration_invalid",
                "TEAM_ATTACHMENT_ENCRYPTION_KEYS_JSON must be valid JSON.",
            ) from exc
        if not isinstance(parsed, dict) or not parsed:
            raise _error(
                503,
                "attachment_encryption_configuration_invalid",
                "TEAM_ATTACHMENT_ENCRYPTION_KEYS_JSON must contain a key map.",
            )
        encoded_keys = {str(key): str(value) for key, value in parsed.items()}
    else:
        single_key = os.getenv("TEAM_ATTACHMENT_ENCRYPTION_KEY", "").strip()
        default_key_id = os.getenv("TEAM_ATTACHMENT_ACTIVE_KEY_ID", "v1").strip() or "v1"
        encoded_keys = {default_key_id: single_key} if single_key else {}

    if active_key_id not in encoded_keys:
        raise _error(
            503,
            "attachment_encryption_not_configured",
            f"Secure attachment key {active_key_id!r} is not configured for this tenant.",
        )

    keys: dict[str, bytes] = {}
    for key_id, encoded in encoded_keys.items():
        if not KEY_ID_RE.fullmatch(key_id):
            raise _error(
                503,
                "attachment_encryption_configuration_invalid",
                "A secure attachment encryption key identifier is invalid.",
            )
        keys[key_id] = _decode_key(encoded, key_id)
    return active_key_id, keys


def build_attachment_aad(
    *,
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
    storage_key: str,
    original_filename: str,
    detected_content_type: str,
    kind: str,
    file_size_bytes: int,
    checksum_sha256: str,
    aad_version: int = TEAM_ATTACHMENT_AAD_VERSION,
) -> bytes:
    value = {
        "aad_version": int(aad_version),
        "organization_id": int(organization_id),
        "conversation_id": int(conversation_id),
        "uploaded_by_user_id": str(uploaded_by_user_id),
        "storage_key": str(storage_key),
        "original_filename": str(original_filename),
        "detected_content_type": str(detected_content_type),
        "kind": str(kind),
        "file_size_bytes": int(file_size_bytes),
        "checksum_sha256": str(checksum_sha256),
    }
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _private_temp_directory() -> Path:
    configured_root = os.getenv("TEAM_SECURE_ATTACHMENT_TEMP_ROOT", "").strip()
    root = Path(configured_root).expanduser() if configured_root else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
    directory = Path(tempfile.mkdtemp(prefix=TEAM_ATTACHMENT_TEMP_PREFIX, dir=root))
    os.chmod(directory, 0o700)
    return directory


def _copy_stream_to_private_file(stream: BinaryIO, destination: Path) -> tuple[int, str]:
    maximum = get_team_secure_attachment_max_bytes()
    total = 0
    digest = hashlib.sha256()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = stream.read(MIB)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise _error(
                        413,
                        "attachment_too_large",
                        f"Attachment is too large. The maximum allowed size is {maximum // MIB} MB.",
                    )
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise _error(422, "empty_attachment", "Attachment file is empty.")
    return total, digest.hexdigest()


@lru_cache(maxsize=4)
def _scanner_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
        version = (completed.stdout or completed.stderr or "").strip().splitlines()[0]
        return version[:160] or "unknown"
    except Exception:
        return "unknown"


def _scan_for_malware(path: Path) -> tuple[str, str, str, str | None]:
    """Return status, scanner name, scanner version, and fallback reason.

    Strict mode fails closed. Best-effort mode only falls through when the
    scanner, definitions, or scanner execution are unavailable. A positive
    malware result always rejects the attachment.
    """

    policy = _team_attachment_scan_policy()
    best_effort = policy == TEAM_ATTACHMENT_SCAN_POLICY_BEST_EFFORT

    try:
        timeout = float(os.getenv("TEAM_ATTACHMENT_SCAN_TIMEOUT_SECONDS", "45"))
    except ValueError:
        timeout = 45.0
    timeout = max(5.0, min(timeout, 120.0))

    available_scanners = [
        (scanner_name, executable)
        for scanner_name in ("clamdscan", "clamscan")
        if (executable := shutil.which(scanner_name))
    ]
    if not available_scanners:
        logger.error("No team attachment malware scanner executable is available.")
        if best_effort:
            return _best_effort_scan_result("scanner_executable_unavailable")
        raise _error(
            503,
            "attachment_scanner_unavailable",
            "Secure attachment scanning is temporarily unavailable. Please try again later.",
        )

    try:
        _assert_clamav_database_fresh()
    except TeamAttachmentSecurityError as exc:
        if best_effort and exc.code in SCANNER_UNAVAILABLE_CODES:
            return _best_effort_scan_result(exc.code)
        raise

    failures: list[str] = []
    for scanner_name, executable in available_scanners:
        arguments = [executable, "--no-summary"]
        if scanner_name == "clamdscan":
            arguments.append("--fdpass")
        arguments.append(str(path))

        try:
            completed = subprocess.run(
                arguments,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            failures.append(f"{scanner_name}:timeout")
            continue
        except OSError:
            failures.append(f"{scanner_name}:execution_failed")
            continue

        if completed.returncode == 0:
            return "clean", scanner_name, _scanner_version(executable), None
        if completed.returncode == 1:
            logger.warning("Team attachment rejected by malware scanner %s.", scanner_name)
            raise _error(
                422,
                "malware_detected",
                "The attachment was rejected by security scanning.",
            )

        failures.append(f"{scanner_name}:exit_{completed.returncode}")

    logger.error("No team attachment malware scanner completed successfully: %s", failures)
    if best_effort:
        return _best_effort_scan_result(",".join(failures) or "scanner_failed")
    raise _error(
        503,
        "attachment_scanner_unavailable",
        "Secure attachment scanning is temporarily unavailable. Please try again later.",
    )


def _assert_clamav_database_fresh() -> None:
    database_root = Path(os.getenv("CLAMAV_DB_DIR", "/var/lib/clamav")).expanduser()
    database_suffixes = {".cvd", ".cld", ".cud", ".hdb", ".ldb", ".ndb"}
    try:
        database_files = [
            candidate
            for candidate in database_root.iterdir()
            if candidate.is_file() and candidate.suffix.lower() in database_suffixes
        ]
    except OSError as exc:
        raise _error(
            503,
            "attachment_scanner_database_unavailable",
            "Secure attachment malware definitions are unavailable.",
        ) from exc
    if not database_files:
        raise _error(
            503,
            "attachment_scanner_database_unavailable",
            "Secure attachment malware definitions are unavailable.",
        )
    daily_files = [
        candidate
        for candidate in database_files
        if candidate.name.lower().startswith("daily.")
    ]
    if not daily_files:
        raise _error(
            503,
            "attachment_scanner_database_unavailable",
            "Current secure attachment malware definitions are unavailable.",
        )

    try:
        maximum_age_hours = float(
            os.getenv("TEAM_ATTACHMENT_MAX_SIGNATURE_AGE_HOURS", "72")
        )
    except ValueError:
        maximum_age_hours = 72.0
    maximum_age_hours = max(1.0, min(maximum_age_hours, 168.0))
    newest_mtime = max(candidate.stat().st_mtime for candidate in daily_files)
    if time.time() - newest_mtime > maximum_age_hours * 60 * 60:
        raise _error(
            503,
            "attachment_scanner_database_stale",
            "Secure attachment malware definitions are stale. Please try again later.",
        )


def _validate_pdf(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if not raw.startswith(b"%PDF-"):
        raise _error(422, "attachment_content_mismatch", "File content does not match its PDF extension.")
    if PDF_ACTIVE_CONTENT_RE.search(raw):
        raise _error(
            422,
            "unsafe_pdf_content",
            "PDFs containing active or embedded content are not allowed.",
        )

    try:
        import fitz

        with fitz.open(path) as document:
            if not document.is_pdf or document.needs_pass:
                raise _error(
                    422,
                    "unsupported_encrypted_document",
                    "Password-protected or unreadable PDFs are not allowed.",
                )
            if document.page_count > MAX_PDF_PAGES:
                raise _error(422, "pdf_page_limit_exceeded", "PDF contains too many pages.")
            if document.xref_length() > MAX_PDF_XREFS:
                raise _error(422, "unsafe_pdf_structure", "PDF structure exceeds the safe limit.")
            for xref in range(1, document.xref_length()):
                try:
                    object_source = document.xref_object(xref, compressed=False)
                except Exception:
                    continue
                if PDF_ACTIVE_CONTENT_RE.search(object_source.encode("latin-1", "ignore")):
                    raise _error(
                        422,
                        "unsafe_pdf_content",
                        "PDFs containing active or embedded content are not allowed.",
                    )
            embedded_names = document.embfile_names() if hasattr(document, "embfile_names") else []
            if embedded_names:
                raise _error(
                    422,
                    "unsafe_pdf_content",
                    "PDFs containing embedded files are not allowed.",
                )
            page_count = document.page_count
    except TeamAttachmentSecurityError:
        raise
    except Exception as exc:
        raise _error(422, "invalid_pdf", "PDF structure is invalid or unsupported.") from exc
    return {"page_count": page_count}


def _safe_archive_name(name: str) -> str:
    if not name or "\\" in name or name.startswith("/"):
        raise _error(422, "unsafe_office_archive", "Office document archive contains an unsafe path.")
    pure = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise _error(422, "unsafe_office_archive", "Office document archive contains an unsafe path.")
    return pure.as_posix()


def _validate_ooxml(path: Path, extension: str) -> dict[str, Any]:
    required_root = {
        ".docx": "word/document.xml",
        ".xlsx": "xl/workbook.xml",
        ".pptx": "ppt/presentation.xml",
    }[extension]
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > MAX_ARCHIVE_ENTRIES:
                raise _error(422, "unsafe_office_archive", "Office document archive has too many entries.")

            seen: set[str] = set()
            total_uncompressed = 0
            relationship_entries: list[zipfile.ZipInfo] = []
            word_xml_entries: list[zipfile.ZipInfo] = []

            for info in infos:
                safe_name = _safe_archive_name(info.filename)
                lower_name = safe_name.lower()
                if lower_name in seen:
                    raise _error(422, "unsafe_office_archive", "Office document archive has duplicate entries.")
                seen.add(lower_name)

                unix_mode = (info.external_attr >> 16) & 0o170000
                if unix_mode == stat.S_IFLNK:
                    raise _error(422, "unsafe_office_archive", "Office document archive contains a symbolic link.")
                if info.flag_bits & 0x1:
                    raise _error(422, "unsafe_office_archive", "Encrypted Office archives are not allowed.")

                total_uncompressed += int(info.file_size)
                if total_uncompressed > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise _error(422, "unsafe_office_archive", "Office document expands beyond the safe limit.")
                if info.file_size and info.compress_size == 0:
                    raise _error(422, "unsafe_office_archive", "Office document has an invalid compression layout.")
                if info.compress_size and info.file_size / info.compress_size > MAX_ARCHIVE_COMPRESSION_RATIO:
                    raise _error(422, "unsafe_office_archive", "Office document compression ratio is unsafe.")

                padded = f"/{lower_name}"
                if (
                    lower_name.endswith(("vbaproject.bin", "vba.bin"))
                    or lower_name.endswith("connections.xml")
                    or any(part in padded for part in OOXML_BLOCKED_PATH_PARTS)
                ):
                    raise _error(
                        422,
                        "unsafe_office_content",
                        "Office files with macros, embedded objects, or external data are not allowed.",
                    )
                if lower_name.endswith(".rels"):
                    relationship_entries.append(info)
                if extension == ".docx" and lower_name.startswith("word/") and lower_name.endswith(".xml"):
                    word_xml_entries.append(info)

            if "[content_types].xml" not in seen or required_root not in seen:
                raise _error(422, "attachment_content_mismatch", "File is not a valid document for its extension.")

            for info in relationship_entries:
                if info.file_size > 2 * MIB:
                    raise _error(422, "unsafe_office_content", "Office relationship metadata is too large.")
                try:
                    root = ElementTree.fromstring(archive.read(info))
                except ElementTree.ParseError as exc:
                    raise _error(422, "invalid_office_document", "Office relationship metadata is invalid.") from exc
                for element in root.iter():
                    if str(element.attrib.get("TargetMode", "")).lower() == "external":
                        relationship_type = str(element.attrib.get("Type", "")).lower()
                        target = str(element.attrib.get("Target", "")).strip()
                        scheme = urlsplit(target).scheme.lower()
                        safe_hyperlink = (
                            relationship_type.endswith("/hyperlink")
                            and scheme in {"https", "mailto"}
                            and len(target) <= 2048
                            and not any(
                                unicodedata.category(character) in {"Cc", "Cf"}
                                for character in target
                            )
                        )
                        if not safe_hyperlink:
                            raise _error(
                                422,
                                "unsafe_office_content",
                                "Office files with unsafe external relationships are not allowed.",
                            )

            for info in word_xml_entries:
                if info.file_size <= 2 * MIB and b"DDEAUTO" in archive.read(info).upper():
                    raise _error(
                        422,
                        "unsafe_office_content",
                        "Office files containing automatic external commands are not allowed.",
                    )

            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise _error(422, "invalid_office_document", "Office document archive is corrupted.")
    except TeamAttachmentSecurityError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise _error(422, "invalid_office_document", "Office document structure is invalid.") from exc

    return {"archive_entries": len(infos), "uncompressed_bytes": total_uncompressed}


def _validate_text(path: Path, extension: str) -> dict[str, Any]:
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise _error(422, "invalid_text_attachment", "Text attachments must not contain binary data.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error(422, "invalid_text_attachment", "Text attachments must use UTF-8 encoding.") from exc

    if extension == ".json":
        try:
            json.loads(text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise _error(422, "invalid_json_attachment", "JSON attachment is not valid JSON.") from exc
    return {"text_characters": len(text)}


def _validate_image(path: Path, extension: str) -> dict[str, Any]:
    signature = path.read_bytes()[:16]
    if extension == ".png" and not signature.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _error(422, "attachment_content_mismatch", "File content does not match its PNG extension.")
    if extension in {".jpg", ".jpeg"} and not signature.startswith(b"\xff\xd8\xff"):
        raise _error(422, "attachment_content_mismatch", "File content does not match its JPEG extension.")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise _error(
            503,
            "attachment_image_validator_unavailable",
            "Secure image validation is temporarily unavailable.",
        ) from exc

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise _error(422, "unsafe_image_dimensions", "Image dimensions exceed the safe limit.")
                expected = "PNG" if extension == ".png" else "JPEG"
                if image.format != expected:
                    raise _error(422, "attachment_content_mismatch", "Image content does not match its extension.")
                frame_count = int(getattr(image, "n_frames", 1))
                if frame_count > 100:
                    raise _error(422, "unsafe_image_frames", "Image has too many frames.")
                image.verify()
    except TeamAttachmentSecurityError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise _error(422, "invalid_image", "Image structure is invalid or unsafe.") from exc
    return {"width": width, "height": height, "frames": frame_count}


def _validate_media(path: Path, extension: str) -> dict[str, Any]:
    signature = path.read_bytes()[:16]
    if extension == ".mp3" and not (
        signature.startswith(b"ID3")
        or (len(signature) >= 2 and signature[0] == 0xFF and signature[1] & 0xE0 == 0xE0)
    ):
        raise _error(422, "attachment_content_mismatch", "File content does not match its MP3 extension.")
    if extension in {".mp4", ".mov"} and signature[4:8] != b"ftyp":
        raise _error(422, "attachment_content_mismatch", "File content does not match its media extension.")
    if extension == ".mkv" and not signature.startswith(b"\x1aE\xdf\xa3"):
        raise _error(422, "attachment_content_mismatch", "File content does not match its MKV extension.")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise _error(
            503,
            "attachment_media_validator_unavailable",
            "Secure media validation is temporarily unavailable.",
        )
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=format_name,duration:stream=codec_type,codec_name",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _error(422, "invalid_media_attachment", "Media file could not be validated safely.") from exc
    if completed.returncode != 0 or len(completed.stdout) > MIB:
        raise _error(422, "invalid_media_attachment", "Media file structure is invalid.")
    try:
        metadata = json.loads(completed.stdout)
        streams = metadata.get("streams") or []
        format_data = metadata.get("format") or {}
        duration = float(format_data.get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error(422, "invalid_media_attachment", "Media metadata is invalid.") from exc

    if not streams or len(streams) > MAX_MEDIA_STREAMS:
        raise _error(422, "invalid_media_attachment", "Media stream layout is not allowed.")
    stream_types = {str(stream.get("codec_type") or "") for stream in streams}
    if not stream_types.issubset({"audio", "video"}):
        raise _error(422, "unsafe_media_content", "Media attachments may contain only audio and video streams.")
    if extension == ".mp3" and stream_types != {"audio"}:
        raise _error(422, "attachment_content_mismatch", "MP3 attachments may contain audio only.")
    if extension in {".mp4", ".mov", ".mkv"} and "video" not in stream_types:
        raise _error(422, "attachment_content_mismatch", "Video attachment does not contain a video stream.")
    if duration < 0 or duration > MAX_MEDIA_DURATION_SECONDS:
        raise _error(422, "media_duration_limit_exceeded", "Media duration exceeds the safe limit.")
    return {"duration_seconds": duration, "stream_count": len(streams)}


def _validate_content(path: Path, filename: str) -> tuple[str, str, dict[str, Any]]:
    extension = Path(filename).suffix.lower()
    kind, canonical_content_type = ALLOWED_TYPES[extension]
    if extension == ".pdf":
        details = _validate_pdf(path)
    elif extension in {".docx", ".xlsx", ".pptx"}:
        details = _validate_ooxml(path, extension)
    elif extension in {".txt", ".csv", ".md", ".json"}:
        details = _validate_text(path, extension)
    elif extension in {".png", ".jpg", ".jpeg"}:
        details = _validate_image(path, extension)
    else:
        details = _validate_media(path, extension)
    return kind, canonical_content_type, details


def prepare_team_attachment_from_stream(
    *,
    stream: BinaryIO,
    filename: str | None,
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
) -> PreparedTeamAttachment:
    original_filename = normalize_team_attachment_filename(filename)
    temporary_directory = _private_temp_directory()
    plaintext_path = temporary_directory / "upload.bin"
    try:
        file_size_bytes, checksum_sha256 = _copy_stream_to_private_file(stream, plaintext_path)

        # A positive malware result always rejects the upload. In explicit
        # best-effort mode, scanner unavailability is recorded and the
        # mandatory format/structure validators still run before encryption.
        malware_scan_status, scanner, scanner_version, scan_fallback_reason = (
            _scan_for_malware(plaintext_path)
        )
        kind, detected_content_type, validation_details = _validate_content(
            plaintext_path,
            original_filename,
        )

        active_key_id = resolve_team_attachment_active_key_id(organization_id)
        active_key_id, keyring = load_team_attachment_keyring(active_key_id=active_key_id)
        opaque_id = os.urandom(16).hex()
        extension = Path(original_filename).suffix.lower()
        stored_filename = f"{opaque_id}{extension}"
        storage_key = f"postgres-encrypted/team-attachments/{opaque_id}"
        aad = build_attachment_aad(
            organization_id=organization_id,
            conversation_id=conversation_id,
            uploaded_by_user_id=uploaded_by_user_id,
            storage_key=storage_key,
            original_filename=original_filename,
            detected_content_type=detected_content_type,
            kind=kind,
            file_size_bytes=file_size_bytes,
            checksum_sha256=checksum_sha256,
        )
        nonce = os.urandom(12)
        plaintext = plaintext_path.read_bytes()
        encrypted_content = AESGCM(keyring[active_key_id]).encrypt(nonce, plaintext, aad)
        scan_completed_at = datetime.now(timezone.utc)

        return PreparedTeamAttachment(
            kind=kind,
            original_filename=original_filename,
            stored_filename=stored_filename,
            storage_key=storage_key,
            content_type=detected_content_type,
            detected_content_type=detected_content_type,
            file_size_bytes=file_size_bytes,
            checksum_sha256=checksum_sha256,
            validation_version=TEAM_ATTACHMENT_VALIDATION_VERSION,
            malware_scan_status=malware_scan_status,
            malware_scanner=scanner,
            malware_scanner_version=scanner_version,
            scan_completed_at=scan_completed_at,
            encryption_algorithm=TEAM_ATTACHMENT_ENCRYPTION_ALGORITHM,
            encryption_key_id=active_key_id,
            encryption_nonce=nonce,
            encryption_aad_version=TEAM_ATTACHMENT_AAD_VERSION,
            encrypted_content=encrypted_content,
            security_metadata={
                "validation": validation_details,
                "scanner": scanner,
                "scanner_version": scanner_version,
                "malware_scan_status": malware_scan_status,
                "scan_policy": _team_attachment_scan_policy(),
                "scan_fallback_reason": scan_fallback_reason,
                "encryption_key_scope": "organization",
                "organization_id": int(organization_id),
            },
        )
    except TeamAttachmentSecurityError:
        raise
    except OSError as exc:
        raise _error(
            503,
            "attachment_security_storage_unavailable",
            "Secure attachment staging is temporarily unavailable.",
        ) from exc
    finally:
        shutil.rmtree(temporary_directory, ignore_errors=True)


def prepare_team_attachment(
    *,
    upload: Any,
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
) -> PreparedTeamAttachment:
    stream = getattr(upload, "file", upload)
    if not hasattr(stream, "read"):
        raise _error(422, "invalid_attachment", "Attachment upload is invalid.")
    return prepare_team_attachment_from_stream(
        stream=stream,
        filename=getattr(upload, "filename", None),
        organization_id=organization_id,
        conversation_id=conversation_id,
        uploaded_by_user_id=uploaded_by_user_id,
    )


def _stage_prepared_team_attachment(
    prepared: PreparedTeamAttachment,
) -> StagedTeamAttachment:
    directory = _private_temp_directory()
    encrypted_path = directory / "encrypted.bin"
    try:
        encrypted_path.write_bytes(prepared.encrypted_content)
        os.chmod(encrypted_path, 0o600)
        return StagedTeamAttachment(
            kind=prepared.kind,
            original_filename=prepared.original_filename,
            stored_filename=prepared.stored_filename,
            storage_key=prepared.storage_key,
            content_type=prepared.content_type,
            detected_content_type=prepared.detected_content_type,
            file_size_bytes=prepared.file_size_bytes,
            checksum_sha256=prepared.checksum_sha256,
            validation_version=prepared.validation_version,
            malware_scan_status=prepared.malware_scan_status,
            malware_scanner=prepared.malware_scanner,
            malware_scanner_version=prepared.malware_scanner_version,
            scan_completed_at=prepared.scan_completed_at,
            encryption_algorithm=prepared.encryption_algorithm,
            encryption_key_id=prepared.encryption_key_id,
            encryption_nonce=prepared.encryption_nonce,
            encryption_aad_version=prepared.encryption_aad_version,
            encrypted_content_path=encrypted_path,
            security_metadata=dict(prepared.security_metadata),
            cleanup_directory=directory,
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise


def stage_team_attachment(
    *,
    upload: Any,
    organization_id: int,
    conversation_id: int,
    uploaded_by_user_id: str,
) -> StagedTeamAttachment:
    return _stage_prepared_team_attachment(
        prepare_team_attachment(
            upload=upload,
            organization_id=organization_id,
            conversation_id=conversation_id,
            uploaded_by_user_id=uploaded_by_user_id,
        )
    )


def prepare_forwarded_team_attachment(
    *,
    source: Mapping[str, Any],
    target_conversation_id: int,
    forwarded_by_user_id: str,
) -> PreparedTeamAttachment:
    plaintext = decrypt_team_attachment(source)
    organization_id = int(source["organization_id"])
    original_filename = str(source["original_filename"])
    kind = str(source["kind"])
    detected_content_type = str(source["detected_content_type"])
    file_size_bytes = int(source["file_size_bytes"])
    checksum_sha256 = str(source["checksum_sha256"])

    active_key_id = resolve_team_attachment_active_key_id(organization_id)
    active_key_id, keyring = load_team_attachment_keyring(active_key_id=active_key_id)
    opaque_id = os.urandom(16).hex()
    extension = Path(original_filename).suffix.lower()
    stored_filename = f"{opaque_id}{extension}"
    storage_key = f"postgres-encrypted/team-attachments/{opaque_id}"
    aad = build_attachment_aad(
        organization_id=organization_id,
        conversation_id=target_conversation_id,
        uploaded_by_user_id=forwarded_by_user_id,
        storage_key=storage_key,
        original_filename=original_filename,
        detected_content_type=detected_content_type,
        kind=kind,
        file_size_bytes=file_size_bytes,
        checksum_sha256=checksum_sha256,
    )
    nonce = os.urandom(12)
    encrypted_content = AESGCM(keyring[active_key_id]).encrypt(nonce, plaintext, aad)

    source_security_metadata = source.get("security_metadata")
    security_metadata = (
        dict(source_security_metadata)
        if isinstance(source_security_metadata, Mapping)
        else {}
    )
    security_metadata["forwarding"] = {
        "source_attachment_id": int(source.get("id") or 0),
        "source_message_id": int(source.get("message_id") or 0),
        "source_conversation_id": int(source.get("conversation_id") or 0),
        "forwarded_by_user_id": str(forwarded_by_user_id),
        "integrity_verified_before_forward": True,
    }

    return PreparedTeamAttachment(
        kind=kind,
        original_filename=original_filename,
        stored_filename=stored_filename,
        storage_key=storage_key,
        content_type=str(source.get("content_type") or detected_content_type),
        detected_content_type=detected_content_type,
        file_size_bytes=file_size_bytes,
        checksum_sha256=checksum_sha256,
        validation_version=str(source.get("validation_version") or TEAM_ATTACHMENT_VALIDATION_VERSION),
        malware_scan_status=str(source.get("malware_scan_status") or "clean"),
        malware_scanner=str(source.get("malware_scanner") or "forwarded-secured-source"),
        malware_scanner_version=str(source.get("malware_scanner_version") or "1"),
        scan_completed_at=source.get("scan_completed_at") or datetime.now(timezone.utc),
        encryption_algorithm=TEAM_ATTACHMENT_ENCRYPTION_ALGORITHM,
        encryption_key_id=active_key_id,
        encryption_nonce=nonce,
        encryption_aad_version=TEAM_ATTACHMENT_AAD_VERSION,
        encrypted_content=encrypted_content,
        security_metadata=security_metadata,
    )


def stage_forwarded_team_attachment(
    *,
    source: Mapping[str, Any],
    target_conversation_id: int,
    forwarded_by_user_id: str,
) -> StagedTeamAttachment:
    return _stage_prepared_team_attachment(
        prepare_forwarded_team_attachment(
            source=source,
            target_conversation_id=target_conversation_id,
            forwarded_by_user_id=forwarded_by_user_id,
        )
    )


def decrypt_team_attachment(row: Mapping[str, Any]) -> bytes:
    if str(row.get("security_status") or "") != "secured":
        raise _error(
            409,
            "attachment_not_secured",
            "This legacy attachment is locked until its security migration is complete.",
        )
    if str(row.get("encryption_algorithm") or "") != TEAM_ATTACHMENT_ENCRYPTION_ALGORITHM:
        raise _error(503, "attachment_encryption_unsupported", "Attachment encryption format is unsupported.")

    key_id = str(row.get("encryption_key_id") or "")
    _active_key_id, keyring = load_team_attachment_keyring(active_key_id=key_id)
    key = keyring.get(key_id)
    if key is None:
        raise _error(
            503,
            "attachment_decryption_key_unavailable",
            "The attachment decryption key is temporarily unavailable.",
        )

    nonce = bytes(row.get("encryption_nonce") or b"")
    encrypted_content = bytes(row.get("encrypted_content") or b"")
    if len(nonce) != 12 or len(encrypted_content) < 17:
        raise _error(503, "attachment_integrity_failure", "Attachment integrity verification failed.")

    aad = build_attachment_aad(
        organization_id=int(row["organization_id"]),
        conversation_id=int(row["conversation_id"]),
        uploaded_by_user_id=str(row["uploaded_by_user_id"]),
        storage_key=str(row["storage_key"]),
        original_filename=str(row["original_filename"]),
        detected_content_type=str(row["detected_content_type"]),
        kind=str(row["kind"]),
        file_size_bytes=int(row["file_size_bytes"]),
        checksum_sha256=str(row["checksum_sha256"]),
        aad_version=int(row.get("encryption_aad_version") or 0),
    )
    try:
        plaintext = AESGCM(key).decrypt(nonce, encrypted_content, aad)
    except InvalidTag as exc:
        raise _error(503, "attachment_integrity_failure", "Attachment integrity verification failed.") from exc

    digest = hashlib.sha256(plaintext).hexdigest()
    if len(plaintext) != int(row["file_size_bytes"]) or not hmac.compare_digest(
        digest,
        str(row["checksum_sha256"]),
    ):
        raise _error(503, "attachment_integrity_failure", "Attachment integrity verification failed.")
    return plaintext


__all__ = [
    "ABSOLUTE_TEAM_SECURE_ATTACHMENT_MAX_BYTES",
    "PreparedTeamAttachment",
    "StagedTeamAttachment",
    "TeamAttachmentSecurityError",
    "decrypt_team_attachment",
    "get_team_secure_attachment_max_bytes",
    "get_team_secure_attachment_org_quota_bytes",
    "normalize_team_attachment_filename",
    "prepare_forwarded_team_attachment",
    "prepare_team_attachment",
    "prepare_team_attachment_from_stream",
    "stage_forwarded_team_attachment",
    "stage_team_attachment",
]
