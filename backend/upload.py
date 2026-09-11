from __future__ import annotations

"""
V1 upload / ingestion layer.

Purpose:
- receive uploaded files from the API layer
- persist them into a stable uploads directory
- build schema-aligned input payloads for analyzer.py
- keep analyzer.py free of request-ingestion and file-saving concerns

Design notes:
- this module does NOT call analyzer.py directly
- this module does NOT perform LLM / ASR / conversion work
- this module is responsible for:
    1) saving uploaded files
    2) building DocumentPayload / MediaPayload / VaultFilePayload from saved files
- convert/transcribe depend on real file paths, so saved-path wiring happens here
- uploaded filenames are treated as untrusted input
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import math
import mimetypes
import os
import re
import shutil
import subprocess
import uuid

from fastapi import UploadFile

from backend.upload_retention import register_upload_path
from backend.upload_security import (
    UploadSecurityError,
    UploadSecurityInfrastructureError,
    validate_upload_file,
)

from backend.src.extraction import (
    build_conversion_document_payload,
    build_document_payload_for_action,
    build_vault_file_payload,
    get_file_size_mb,
)
from backend.src.schema import (
    AudioFormat,
    DocumentPayload,
    FeatureType,
    MediaPayload,
    MediaType,
    VaultFilePayload,
    VideoFormat,
)
from backend.src.storage.artifacts import LocalArtifactStorage, StorageBackend


UPLOAD_BASE_DIR = Path(os.getenv("UPLOAD_BASE_DIR", "uploads")).expanduser()
DOCUMENT_UPLOAD_DIR = UPLOAD_BASE_DIR / "documents"
MEDIA_UPLOAD_DIR = UPLOAD_BASE_DIR / "media"
QUARANTINE_UPLOAD_DIR = UPLOAD_BASE_DIR / "quarantine"
PDF_TOOL_UPLOAD_DIR = UPLOAD_BASE_DIR / "pdf_tools"
PDF_EDIT_ASSET_UPLOAD_DIR = PDF_TOOL_UPLOAD_DIR / "edit_assets"
VAULT_QUARANTINE_UPLOAD_DIR = QUARANTINE_UPLOAD_DIR / "vault"

# Broad document/media whitelists at the ingestion layer.
ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png", ".xlsx", ".html", ".htm", ".pptx"}
ALLOWED_MEDIA_SUFFIXES = {
    ".mp3",
    ".wav",
    ".aac",
    ".flac",
    ".webm",
    ".m4a",
    ".ogg",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".wmv",
}

# Action-specific document rules from the product contract / feature handlers.
CONVERSION_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png", ".xlsx", ".html", ".htm", ".pptx"}
TEXT_AI_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt"}
PRIVACY_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}

# Hard byte ceilings enforced while streaming uploads to disk. These are separate
# from schema-level file_size_mb checks because attackers can bypass the frontend
# and lie about metadata.
MAX_UPLOAD_BYTES_BY_SUFFIX = {
    ".pdf": 25 * 1024 * 1024,
    ".docx": 25 * 1024 * 1024,
    ".txt": 5 * 1024 * 1024,
    ".jpg": 25 * 1024 * 1024,
    ".jpeg": 25 * 1024 * 1024,
    ".png": 25 * 1024 * 1024,
    ".xlsx": 25 * 1024 * 1024,
    ".html": 25 * 1024 * 1024,
    ".htm": 25 * 1024 * 1024,
    ".pptx": 25 * 1024 * 1024,
    ".mp3": 25 * 1024 * 1024,
    ".wav": 25 * 1024 * 1024,
    ".aac": 25 * 1024 * 1024,
    ".flac": 25 * 1024 * 1024,
    ".webm": 25 * 1024 * 1024,
    ".m4a": 25 * 1024 * 1024,
    ".ogg": 25 * 1024 * 1024,
    ".mp4": 100 * 1024 * 1024,
    ".mov": 100 * 1024 * 1024,
    ".avi": 100 * 1024 * 1024,
    ".mkv": 100 * 1024 * 1024,
    ".wmv": 100 * 1024 * 1024,
}
MAX_PDF_TOOL_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PDF_EDIT_ASSET_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_VAULT_UPLOAD_BYTES = max(
    1,
    int(os.getenv("VAULT_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024))),
)
ALLOWED_PDF_EDIT_ASSET_SUFFIXES = {".jpg", ".jpeg", ".png"}
MEDIA_PROBE_TIMEOUT_SECONDS = max(
    3.0,
    float(os.getenv("MEDIA_PROBE_TIMEOUT_SECONDS", "15")),
)


@dataclass(frozen=True)
class SavedUpload:
    """
    Result of persisting an uploaded file to local storage.
    """

    original_filename: str
    safe_original_filename: str
    stored_filename: str
    stored_path: str
    suffix: str
    mime_type: Optional[str]


class UploadError(ValueError):
    """Raised when uploaded file ingestion fails."""


class UploadServiceUnavailableError(RuntimeError):
    """Raised when upload ingestion cannot run because a required service is unavailable."""


def ensure_upload_directories() -> None:
    DOCUMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PDF_TOOL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    VAULT_QUARANTINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def save_uploaded_file(
    upload: UploadFile,
    *,
    category: str,
) -> SavedUpload:
    """
    Persist an uploaded file only after it passes server-side security checks.

    Security rules:
    - never trust the client filename or Content-Type;
    - whitelist extensions by feature category;
    - stream to quarantine with hard byte limits;
    - validate magic bytes and format structure;
    - run malware scanning through backend.upload_security;
    - promote only clean files into the stable uploads directory.
    """
    ensure_upload_directories()

    if upload is None:
        raise UploadError("No upload file was provided.")

    original_filename = (upload.filename or "").strip()
    if not original_filename:
        raise UploadError("Uploaded file must have a filename.")

    safe_original_filename = _safe_original_filename(original_filename)

    raw_suffix = Path(original_filename).suffix
    suffix = _validate_upload_suffix(suffix=raw_suffix, category=category)
    allowed = ALLOWED_DOCUMENT_SUFFIXES if category == "documents" else ALLOWED_MEDIA_SUFFIXES

    stored_filename = f"{uuid.uuid4().hex}{suffix}"

    if category == "documents":
        destination_dir = DOCUMENT_UPLOAD_DIR
    elif category == "media":
        destination_dir = MEDIA_UPLOAD_DIR
    else:
        raise UploadError("category must be either 'documents' or 'media'.")

    quarantine_dir = QUARANTINE_UPLOAD_DIR / category
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination_dir.mkdir(parents=True, exist_ok=True)

    quarantine_path = quarantine_dir / stored_filename
    destination_path = destination_dir / stored_filename
    max_bytes = MAX_UPLOAD_BYTES_BY_SUFFIX.get(suffix, 10 * 1024 * 1024)

    try:
        _copy_upload_with_limit(upload, quarantine_path, max_bytes=max_bytes)
        _validate_quarantined_file(quarantine_path, suffix=suffix, allowed_extensions=allowed)
        shutil.move(str(quarantine_path), str(destination_path))
        register_upload_path(destination_path)
    except (ValueError, UploadServiceUnavailableError):
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise UploadError(f"Failed to persist uploaded file: {exc}") from exc
    finally:
        try:
            upload.file.close()
        except Exception:
            pass

    return SavedUpload(
        original_filename=original_filename,
        safe_original_filename=safe_original_filename,
        stored_filename=stored_filename,
        stored_path=str(destination_path),
        suffix=suffix.lstrip("."),
        mime_type=upload.content_type,
    )


def save_pdf_tool_upload(
    upload: UploadFile,
    *,
    subdir: str,
    default_name: str = "document.pdf",
    base_dir: Path | str | None = None,
) -> Path:
    """
    Hardened PDF-only upload path for combine/split/edit/compress/lock/e-signature.

    This keeps route_v1.py from maintaining a parallel, weaker upload path.
    """
    ensure_upload_directories()

    if upload is None:
        raise UploadError("No upload file was provided.")

    filename = _safe_upload_name(upload.filename, default=default_name)
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf":
        raise UploadError("Only PDF uploads are accepted for PDF tools and e-signature.")

    safe_subdir = re.sub(r"[^A-Za-z0-9._-]+", "-", str(subdir or "pdf")).strip("-._") or "pdf"
    destination_root = Path(base_dir) if base_dir is not None else PDF_TOOL_UPLOAD_DIR
    destination_dir = destination_root / safe_subdir
    quarantine_dir = QUARANTINE_UPLOAD_DIR / "pdf_tools" / safe_subdir
    destination_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}-{filename}"
    quarantine_path = quarantine_dir / stored_filename
    destination_path = destination_dir / stored_filename

    try:
        _copy_upload_with_limit(upload, quarantine_path, max_bytes=MAX_PDF_TOOL_UPLOAD_BYTES)
        _validate_quarantined_file(
            quarantine_path,
            suffix=".pdf",
            allowed_extensions={".pdf"},
        )
        shutil.move(str(quarantine_path), str(destination_path))
        register_upload_path(destination_path)
    except (ValueError, UploadServiceUnavailableError):
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise UploadError(f"Failed to persist uploaded PDF: {exc}") from exc
    finally:
        try:
            upload.file.close()
        except Exception:
            pass

    return destination_path.resolve()


def save_pdf_edit_asset_upload(upload: UploadFile) -> Path:
    """Persist a PNG/JPEG used by a PDF edit operation after security checks."""
    ensure_upload_directories()

    if upload is None:
        raise UploadError("No PDF edit asset was provided.")

    filename = _safe_upload_name(upload.filename, default="edit-asset.png")
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_PDF_EDIT_ASSET_SUFFIXES:
        raise UploadError("PDF edit images must be PNG, JPG, or JPEG files.")

    quarantine_dir = QUARANTINE_UPLOAD_DIR / "pdf_tools" / "edit_assets"
    destination_dir = PDF_EDIT_ASSET_UPLOAD_DIR
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    destination_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}-{filename}"
    quarantine_path = quarantine_dir / stored_filename
    destination_path = destination_dir / stored_filename

    try:
        _copy_upload_with_limit(
            upload,
            quarantine_path,
            max_bytes=MAX_PDF_EDIT_ASSET_UPLOAD_BYTES,
        )
        _validate_quarantined_file(
            quarantine_path,
            suffix=suffix,
            allowed_extensions=ALLOWED_PDF_EDIT_ASSET_SUFFIXES,
        )
        shutil.move(str(quarantine_path), str(destination_path))
        register_upload_path(destination_path)
    except (ValueError, UploadServiceUnavailableError):
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        quarantine_path.unlink(missing_ok=True)
        destination_path.unlink(missing_ok=True)
        raise UploadError(f"Failed to persist PDF edit image: {exc}") from exc
    finally:
        try:
            upload.file.close()
        except Exception:
            pass

    return destination_path.resolve()


def _copy_upload_with_limit(upload: UploadFile, destination_path: Path, *, max_bytes: int) -> int:
    total = 0
    try:
        upload.file.seek(0)
        with destination_path.open("wb") as destination:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise UploadError(
                        f"Uploaded file exceeds the maximum allowed size of {max_bytes // (1024 * 1024)} MB."
                    )
                destination.write(chunk)
    except UploadError:
        raise
    except ValueError as exc:
        raise UploadError(
            "Uploaded file stream is closed. Submit a fresh upload request instead of reusing a consumed file stream."
        ) from exc

    if total <= 0:
        raise UploadError("Uploaded file is empty.")
    return total


def _validate_quarantined_file(
    path: Path,
    *,
    suffix: str,
    allowed_extensions: set[str],
) -> None:
    try:
        validate_upload_file(
            path,
            extension=suffix,
            allowed_extensions=allowed_extensions,
            run_malware_scan=True,
        )
    except UploadSecurityInfrastructureError as exc:
        raise UploadServiceUnavailableError(str(exc)) from exc
    except UploadSecurityError as exc:
        raise UploadError(str(exc)) from exc
    except ValueError as exc:
        raise UploadError(str(exc)) from exc


def build_uploaded_document_payload(
    *,
    action: FeatureType,
    upload: UploadFile,
) -> DocumentPayload:
    """
    Save an uploaded document and convert it into the correct DocumentPayload.

    Used for:
    - convert
    - text_to_speech
    - summarize
    - grammar_correct
    - translate
    - explain
    - redact
    - data_mask
    - structured_extract
    - compliance
    - generate_questions
    - generate_answers
    """
    _validate_document_action(action)

    original_filename = (upload.filename or "").strip()
    suffix = Path(original_filename).suffix.strip().lower()
    _validate_document_suffix_for_action(action=action, suffix=suffix)

    saved = save_uploaded_file(upload, category="documents")

    if action == FeatureType.convert:
        payload = build_conversion_document_payload(saved.stored_path)
    else:
        payload = build_document_payload_for_action(
            action=action,
            file_path=saved.stored_path,
        )

    payload.filename = saved.stored_path
    return payload


def build_uploaded_vault_file_payload(
    *,
    upload: UploadFile,
    owner_user_id: str,
    organization_id: Optional[str] = None,
    client_encrypted: bool = False,
    storage_backend: Optional[StorageBackend] = None,
) -> VaultFilePayload:
    """Securely stage a Vault file upload and return an owner-scoped Vault payload.

    Vault intentionally accepts arbitrary binary file types, so this path does not
    apply the document/media extension whitelists. The upload still passes through
    the same quarantine, hard-size, dangerous-filename, malware-scan, and known-
    format validation pipeline as every other ReDOCX upload.

    The clean source is copied into owner-scoped short-lived artifact storage and
    referenced by ``storage_key``. This matches ``ArtifactVaultFileSourceResolver``
    and keeps the durable encrypted Vault store separate from transient uploads.
    """
    ensure_upload_directories()

    if upload is None:
        raise UploadError("No Vault upload file was provided.")

    owner = str(owner_user_id or "").strip()
    if not owner:
        raise UploadError("Authenticated owner_user_id is required for Vault uploads.")

    original_filename = (upload.filename or "").strip()
    if not original_filename:
        raise UploadError("Uploaded file must have a filename.")

    safe_original_filename = _safe_original_filename(original_filename)
    # Vault content is stored as an opaque binary object. Do not route it through
    # document/media parsers based on the filename; malware scanning remains the
    # authoritative upload-security gate for arbitrary Vault file types.
    validation_suffix = ".bin"
    content_type = (
        "application/octet-stream"
        if client_encrypted
        else mimetypes.guess_type(safe_original_filename)[0]
        or "application/octet-stream"
    )
    quarantine_path = (
        VAULT_QUARANTINE_UPLOAD_DIR
        / f"{uuid.uuid4().hex}{validation_suffix}"
    )

    try:
        _copy_upload_with_limit(
            upload,
            quarantine_path,
            max_bytes=MAX_VAULT_UPLOAD_BYTES,
        )
        _validate_quarantined_file(
            quarantine_path,
            suffix=validation_suffix,
            allowed_extensions={validation_suffix},
        )

        storage = storage_backend or LocalArtifactStorage(
            base_dir=os.getenv("VAULT_SOURCE_ARTIFACT_DIR") or None
        )
        stored = storage.persist(
            source_file_path=str(quarantine_path),
            artifact_name=safe_original_filename,
            content_type=content_type,
            owner_user_id=owner,
            organization_id=(
                str(organization_id).strip() if organization_id else None
            ),
            feature=FeatureType.vault.value,
        )
        storage_key = str(getattr(stored, "storage_key", "") or "").strip()
        if not storage_key:
            raise RuntimeError(
                "Vault source artifact storage did not return a storage key."
            )

        payload = build_vault_file_payload(
            quarantine_path,
            storage_key=storage_key,
            content_type=content_type,
            client_encrypted=client_encrypted,
        )
        return payload.model_copy(update={"filename": safe_original_filename})
    except (ValueError, UploadServiceUnavailableError):
        raise
    except OSError as exc:
        raise UploadError(f"Failed to persist Vault upload: {exc}") from exc
    finally:
        quarantine_path.unlink(missing_ok=True)
        try:
            upload.file.close()
        except Exception:
            pass


def build_uploaded_media_payload(
    *,
    upload: UploadFile,
    media_type: MediaType,
    duration_seconds: int,
) -> MediaPayload:
    """
    Save an uploaded media file and build a schema-aligned MediaPayload.

    Used for:
    - transcribe
    """
    try:
        reported_duration_seconds = int(duration_seconds)
    except (TypeError, ValueError) as exc:
        raise UploadError("Media duration must be a positive integer.") from exc
    if reported_duration_seconds < 1:
        raise UploadError("Media duration must be at least one second.")

    saved = save_uploaded_file(upload, category="media")

    file_size_mb = get_file_size_mb(Path(saved.stored_path))
    media_format = _detect_media_format(saved.suffix, media_type)
    probed_duration_seconds = _probe_media_duration_seconds(saved.stored_path)

    return MediaPayload(
        media_type=media_type,
        media_format=media_format,
        file_size_mb=file_size_mb,
        # duration_seconds remains part of the public request contract for
        # compatibility, but the backend uses an authoritative probe so clients
        # cannot bypass media limits by submitting forged duration metadata.
        duration_seconds=probed_duration_seconds,
        filename=saved.stored_path,
        mime_type=saved.mime_type,
    )


def _run_ffprobe(args: list[str], *, timeout_seconds: float) -> str:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise UploadServiceUnavailableError(
            "ffprobe is required for authoritative media validation but was not found on PATH."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise UploadError("Media validation timed out while reading duration.") from exc
    except subprocess.CalledProcessError as exc:
        raise UploadError("Uploaded media could not be parsed safely.") from exc

    return result.stdout or ""


def _positive_finite_float(value: str) -> float | None:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _probe_packet_duration_seconds(path: Path) -> float | None:
    """Fallback for containers that omit aggregate duration metadata.

    Browser MediaRecorder WebM files can be perfectly decodable while reporting
    ``format.duration=N/A``. Packet timestamps remain authoritative and let us
    enforce the same server-side duration limits without trusting client metadata.
    """

    output = _run_ffprobe(
        [
            "-show_entries",
            "packet=pts_time,dts_time,duration_time",
            "-of",
            "compact=p=0:nk=0",
            str(path),
        ],
        timeout_seconds=MEDIA_PROBE_TIMEOUT_SECONDS,
    )

    max_end = 0.0
    for line in output.splitlines():
        fields: dict[str, str] = {}
        for item in line.split("|"):
            key, separator, value = item.partition("=")
            if separator:
                fields[key.strip()] = value.strip()

        timestamp = None
        for key in ("pts_time", "dts_time"):
            raw = fields.get(key)
            try:
                candidate = float(raw) if raw is not None else float("nan")
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate) and candidate >= 0:
                timestamp = candidate
                break

        if timestamp is None:
            continue

        packet_duration = _positive_finite_float(fields.get("duration_time", "")) or 0.0
        max_end = max(max_end, timestamp + packet_duration, timestamp)

    return max_end if max_end > 0 else None


def _probe_media_duration_seconds(file_path: str | Path) -> int:
    path = Path(file_path)

    # Fast path for normal files with container-level duration metadata.
    output = _run_ffprobe(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout_seconds=MEDIA_PROBE_TIMEOUT_SECONDS,
    )
    duration = _positive_finite_float(output.strip())

    # Some valid streaming/browser-recorded containers (notably WebM) omit the
    # aggregate duration. Fall back to packet timestamps rather than rejecting a
    # valid upload or trusting the client-reported duration.
    if duration is None:
        duration = _probe_packet_duration_seconds(path)

    if duration is None:
        raise UploadError("Uploaded media did not expose a valid duration.")

    return max(1, math.ceil(duration))


def _validate_upload_suffix(*, suffix: str, category: str) -> str:
    normalized = suffix.strip().lower()
    if not normalized.startswith("."):
        raise UploadError("Uploaded file must include a valid extension.")

    if category == "documents":
        allowed = ALLOWED_DOCUMENT_SUFFIXES
    elif category == "media":
        allowed = ALLOWED_MEDIA_SUFFIXES
    else:
        raise UploadError("category must be either 'documents' or 'media'.")

    if normalized not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise UploadError(
            f"Unsupported file extension for {category}: {normalized}. Allowed: {allowed_text}"
        )

    return normalized


def _validate_document_action(action: FeatureType) -> None:
    allowed_actions = {
        FeatureType.convert,
        FeatureType.text_to_speech,
        FeatureType.summarize,
        FeatureType.grammar_correct,
        FeatureType.translate,
        FeatureType.explain,
        FeatureType.redact,
        FeatureType.data_mask,
        FeatureType.structured_extract,
        FeatureType.compliance,
        FeatureType.generate_questions,
        FeatureType.generate_answers,
    }
    if action not in allowed_actions:
        raise UploadError(f"Unsupported document upload action: {action.value}.")


def _allowed_document_suffixes_for_action(action: FeatureType) -> set[str]:
    if action == FeatureType.convert:
        return CONVERSION_DOCUMENT_SUFFIXES

    if action in {
        FeatureType.text_to_speech,
        FeatureType.summarize,
        FeatureType.grammar_correct,
        FeatureType.translate,
        FeatureType.explain,
        FeatureType.generate_questions,
        FeatureType.generate_answers,
    }:
        return TEXT_AI_DOCUMENT_SUFFIXES

    if action in {
        FeatureType.redact,
        FeatureType.data_mask,
        FeatureType.structured_extract,
        FeatureType.compliance,
    }:
        return PRIVACY_DOCUMENT_SUFFIXES

    raise UploadError(f"Unsupported document upload action: {action.value}.")


def _validate_document_suffix_for_action(*, action: FeatureType, suffix: str) -> str:
    normalized = suffix.strip().lower()
    if not normalized.startswith("."):
        raise UploadError("Uploaded file must include a valid extension.")

    allowed = _allowed_document_suffixes_for_action(action)
    if normalized not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise UploadError(
            f"Unsupported file extension for action '{action.value}': {normalized}. "
            f"Allowed: {allowed_text}"
        )
    return normalized


def _safe_original_filename(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise UploadError("Uploaded file must have a filename.")
    if "\x00" in raw:
        raise UploadError("Uploaded filename contains invalid characters.")
    # Reject actual path input. Repeated dots in a basename (for example,
    # "report..pdf") are not traversal and remain safe because storage uses a
    # generated filename and the file content is validated independently.
    if "/" in raw or "\\" in raw:
        raise UploadError("Uploaded filename must not contain path separators.")

    basename = Path(raw).name
    suffixes = [item.lower() for item in Path(basename).suffixes]
    dangerous_extensions = {
        ".exe", ".dll", ".bat", ".cmd", ".sh", ".js", ".mjs",
        ".php", ".py", ".jar", ".scr", ".vbs", ".ps1", ".msi",
        ".apk", ".com", ".pif",
    }
    if len(suffixes) > 1 and any(item in dangerous_extensions for item in suffixes[:-1]):
        raise UploadError("Uploaded filename contains a dangerous double extension.")

    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", basename).strip("-._")
    return safe or "upload"


def _safe_upload_name(filename: str | None, *, default: str) -> str:
    incoming = str(filename or default)
    if "\x00" in incoming:
        raise UploadError("Uploaded filename contains invalid characters.")
    if "/" in incoming or "\\" in incoming:
        raise UploadError("Uploaded filename must not contain path separators.")
    raw = Path(incoming).name
    suffix = Path(raw).suffix.lower() or Path(default).suffix.lower()
    stem = Path(raw).stem or Path(default).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or Path(default).stem
    return f"{stem}{suffix}"


def _detect_media_format(suffix: str, media_type: MediaType):
    # SavedUpload.suffix is normalized with a leading dot (for example, ".mp3"),
    # while schema enum values intentionally omit it. Normalize once before
    # comparing so valid uploads are not rejected at ingestion.
    normalized = suffix.strip().lower().lstrip(".")

    if media_type == MediaType.audio:
        audio_formats = {
            AudioFormat.mp3.value: AudioFormat.mp3,
            AudioFormat.wav.value: AudioFormat.wav,
            AudioFormat.aac.value: AudioFormat.aac,
            AudioFormat.flac.value: AudioFormat.flac,
            AudioFormat.webm.value: AudioFormat.webm,
            AudioFormat.m4a.value: AudioFormat.m4a,
            AudioFormat.ogg.value: AudioFormat.ogg,
        }
        resolved = audio_formats.get(normalized)
        if resolved is None:
            raise UploadError(
                "Audio uploads must be one of: mp3, wav, aac, flac, webm, m4a, ogg."
            )
        return resolved

    video_formats = {
        VideoFormat.mp4.value: VideoFormat.mp4,
        VideoFormat.mov.value: VideoFormat.mov,
        VideoFormat.avi.value: VideoFormat.avi,
        VideoFormat.mkv.value: VideoFormat.mkv,
        VideoFormat.wmv.value: VideoFormat.wmv,
        # WebM is shared with browser-recorded audio in the existing schema, so
        # retain the established AudioFormat.webm enum value for this container.
        AudioFormat.webm.value: AudioFormat.webm,
    }
    resolved = video_formats.get(normalized)
    if resolved is not None:
        return resolved

    raise UploadError("Video uploads must be one of: mp4, mov, avi, mkv, wmv, webm.")


__all__ = [
    "UPLOAD_BASE_DIR",
    "DOCUMENT_UPLOAD_DIR",
    "MEDIA_UPLOAD_DIR",
    "QUARANTINE_UPLOAD_DIR",
    "PDF_TOOL_UPLOAD_DIR",
    "VAULT_QUARANTINE_UPLOAD_DIR",
    "ALLOWED_DOCUMENT_SUFFIXES",
    "ALLOWED_MEDIA_SUFFIXES",
    "CONVERSION_DOCUMENT_SUFFIXES",
    "TEXT_AI_DOCUMENT_SUFFIXES",
    "PRIVACY_DOCUMENT_SUFFIXES",
    "MAX_VAULT_UPLOAD_BYTES",
    "SavedUpload",
    "UploadError",
    "UploadServiceUnavailableError",
    "ensure_upload_directories",
    "save_uploaded_file",
    "save_pdf_tool_upload",
    "save_pdf_edit_asset_upload",
    "build_uploaded_document_payload",
    "build_uploaded_vault_file_payload",
    "build_uploaded_media_payload",
]
