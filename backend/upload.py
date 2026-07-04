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
    2) building DocumentPayload / MediaPayload from saved files
- convert/transcribe depend on real file paths, so saved-path wiring happens here
- uploaded filenames are treated as untrusted input
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re
import shutil
import uuid

from fastapi import UploadFile

from backend.upload_security import validate_upload_file

from backend.src.extraction import (
    build_conversion_document_payload,
    build_document_payload_for_action,
    get_file_size_mb,
)
from backend.src.schema import (
    AudioFormat,
    DocumentPayload,
    FeatureType,
    MediaPayload,
    MediaType,
    VideoFormat,
)


UPLOAD_BASE_DIR = Path("uploads")
DOCUMENT_UPLOAD_DIR = UPLOAD_BASE_DIR / "documents"
MEDIA_UPLOAD_DIR = UPLOAD_BASE_DIR / "media"
QUARANTINE_UPLOAD_DIR = UPLOAD_BASE_DIR / "quarantine"
PDF_TOOL_UPLOAD_DIR = UPLOAD_BASE_DIR / "pdf_tools"

# Broad document/media whitelists at the ingestion layer.
ALLOWED_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt", ".jpg", ".jpeg", ".png"}
ALLOWED_MEDIA_SUFFIXES = {".mp3", ".mp4", ".mkv", ".mov"}

# Action-specific document rules from the product contract / feature handlers.
CONVERSION_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}
TEXT_AI_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".txt"}
PRIVACY_DOCUMENT_SUFFIXES = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}

# Hard byte ceilings enforced while streaming uploads to disk. These are separate
# from schema-level file_size_mb checks because attackers can bypass the frontend
# and lie about metadata.
MAX_UPLOAD_BYTES_BY_SUFFIX = {
    ".pdf": 10 * 1024 * 1024,
    ".docx": 10 * 1024 * 1024,
    ".txt": 2 * 1024 * 1024,
    ".jpg": 10 * 1024 * 1024,
    ".jpeg": 10 * 1024 * 1024,
    ".png": 10 * 1024 * 1024,
    ".mp3": 10 * 1024 * 1024,
    ".mp4": 25 * 1024 * 1024,
    ".mkv": 25 * 1024 * 1024,
    ".mov": 25 * 1024 * 1024,
}
MAX_PDF_TOOL_UPLOAD_BYTES = 50 * 1024 * 1024


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


def ensure_upload_directories() -> None:
    DOCUMENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    PDF_TOOL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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
    except ValueError:
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
    Hardened PDF-only upload path for combine/split/edit/compress/e-signature.

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
    except ValueError:
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
    saved = save_uploaded_file(upload, category="media")

    file_size_mb = get_file_size_mb(Path(saved.stored_path))
    media_format = _detect_media_format(saved.suffix, media_type)

    return MediaPayload(
        media_type=media_type,
        media_format=media_format,
        file_size_mb=file_size_mb,
        duration_seconds=duration_seconds,
        filename=saved.stored_path,
        mime_type=saved.mime_type,
    )


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
    if "/" in raw or "\\" in raw or ".." in raw:
        raise UploadError("Uploaded filename must not contain path separators or traversal sequences.")

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
    if "\x00" in incoming or "/" in incoming or "\\" in incoming or ".." in incoming:
        raise UploadError("Uploaded filename must not contain path separators or traversal sequences.")
    raw = Path(incoming).name
    suffix = Path(raw).suffix.lower() or Path(default).suffix.lower()
    stem = Path(raw).stem or Path(default).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or Path(default).stem
    return f"{stem}{suffix}"


def _detect_media_format(suffix: str, media_type: MediaType):
    normalized = suffix.strip().lower()

    if media_type == MediaType.audio:
        if normalized != AudioFormat.mp3.value:
            raise UploadError("Audio uploads must be mp3.")
        return AudioFormat.mp3

    if normalized == VideoFormat.mp4.value:
        return VideoFormat.mp4
    if normalized == VideoFormat.mkv.value:
        return VideoFormat.mkv
    if normalized == VideoFormat.mov.value:
        return VideoFormat.mov

    raise UploadError("Video uploads must be one of: mp4, mkv, mov.")


__all__ = [
    "UPLOAD_BASE_DIR",
    "DOCUMENT_UPLOAD_DIR",
    "MEDIA_UPLOAD_DIR",
    "QUARANTINE_UPLOAD_DIR",
    "PDF_TOOL_UPLOAD_DIR",
    "ALLOWED_DOCUMENT_SUFFIXES",
    "ALLOWED_MEDIA_SUFFIXES",
    "CONVERSION_DOCUMENT_SUFFIXES",
    "TEXT_AI_DOCUMENT_SUFFIXES",
    "PRIVACY_DOCUMENT_SUFFIXES",
    "SavedUpload",
    "UploadError",
    "ensure_upload_directories",
    "save_uploaded_file",
    "save_pdf_tool_upload",
    "build_uploaded_document_payload",
    "build_uploaded_media_payload",
]
