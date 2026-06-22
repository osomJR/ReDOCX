from __future__ import annotations

"""
Defensive upload validation for ReDOCX.

This module is intentionally independent from FastAPI route code so every upload
path can share the same security decision before a file reaches any parser,
converter, OCR engine, PDF tool, LLM pipeline, or artifact route.

Production recommendation:
- install ClamAV in the runtime image;
- run clamd locally/private to the app network;
- set UPLOAD_MALWARE_SCAN_MODE=required.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import hashlib
import os
import shutil
import subprocess
import zipfile

try:  # PyMuPDF is already used by ReDOCX PDF processing.
    import fitz  # type: ignore
except Exception:  # pragma: no cover - dependency is validated at runtime.
    fitz = None  # type: ignore

try:  # Pillow is already used by ReDOCX image/OCR paths.
    from PIL import Image, UnidentifiedImageError  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    UnidentifiedImageError = OSError  # type: ignore


class UploadSecurityError(ValueError):
    """Raised when an upload fails a security gate."""


@dataclass(frozen=True)
class UploadSecurityVerdict:
    sha256: str
    extension: str
    detected_type: str
    scanner: Optional[str] = None


# Conservative magic-byte allowlist for formats ReDOCX intentionally accepts.
MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".mkv": (b"\x1a\x45\xdf\xa3",),
}

PDF_ACTIVE_CONTENT_MARKERS = (
    b"/javascript",
    b"/js",
    b"/openaction",
    b"/aa",
    b"/launch",
    b"/embeddedfile",
    b"/xfa",
    b"/richmedia",
)

DOCX_FORBIDDEN_PART_MARKERS = (
    "vbaproject.bin",
    "word/vbadata.xml",
    "activex/",
    "embeddings/",
    "oleobject",
    "customui/",
)

MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = int(
    os.getenv("UPLOAD_MAX_DOCX_UNCOMPRESSED_BYTES", str(50 * 1024 * 1024))
)
MAX_DOCX_COMPRESSION_RATIO = float(os.getenv("UPLOAD_MAX_DOCX_COMPRESSION_RATIO", "100"))
MAX_TEXT_CONTROL_CHAR_RATIO = float(os.getenv("UPLOAD_MAX_TEXT_CONTROL_CHAR_RATIO", "0.02"))
DEFAULT_SCAN_TIMEOUT_SECONDS = float(os.getenv("UPLOAD_MALWARE_SCAN_TIMEOUT_SECONDS", "45"))


def sha256_file(path: str | Path) -> str:
    source = Path(path)
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_upload_file(
    path: str | Path,
    *,
    extension: str,
    allowed_extensions: Iterable[str],
    run_malware_scan: bool = True,
) -> UploadSecurityVerdict:
    """
    Validate a saved quarantine file before it is promoted to clean storage.

    This function deliberately trusts neither filename nor client-provided MIME.
    It validates declared extension against an allowlist, verifies file content,
    checks format-specific active-content risks, then runs a malware scanner when
    configured.
    """
    source = Path(path)
    normalized_extension = _normalize_extension(extension)
    allowed = {_normalize_extension(item) for item in allowed_extensions}

    if normalized_extension not in allowed:
        raise UploadSecurityError(
            f"Unsupported file extension: {normalized_extension}. Allowed: {', '.join(sorted(allowed))}."
        )

    if not source.exists() or not source.is_file():
        raise UploadSecurityError("Uploaded file was not saved correctly.")

    if source.stat().st_size <= 0:
        raise UploadSecurityError("Uploaded file is empty.")

    _reject_dangerous_filename(source.name)
    _assert_magic_signature(source, normalized_extension)

    detected_type = normalized_extension.lstrip(".")
    if normalized_extension == ".pdf":
        _assert_safe_pdf(source)
        detected_type = "pdf"
    elif normalized_extension == ".docx":
        _assert_safe_docx(source)
        detected_type = "docx"
    elif normalized_extension in {".jpg", ".jpeg", ".png"}:
        _assert_safe_image(source)
        detected_type = "image"
    elif normalized_extension == ".txt":
        _assert_safe_text(source)
        detected_type = "text"
    elif normalized_extension in {".mp3", ".mp4", ".mkv", ".mov"}:
        _assert_safe_media_container(source, normalized_extension)
        detected_type = "media"

    scanner_used = None
    if run_malware_scan:
        scanner_used = scan_with_malware_scanner(source)

    return UploadSecurityVerdict(
        sha256=sha256_file(source),
        extension=normalized_extension,
        detected_type=detected_type,
        scanner=scanner_used,
    )


def scan_with_malware_scanner(path: str | Path) -> Optional[str]:
    """
    Scan with ClamAV-compatible CLI.

    Modes:
    - required: fail closed when scanner is missing or errors.
    - best_effort: scan when available, but do not block if unavailable.
    - disabled: skip scanning; useful only for local development.
    """
    mode = os.getenv("UPLOAD_MALWARE_SCAN_MODE", "required").strip().lower()
    if mode in {"0", "false", "off", "disabled", "none"}:
        return None
    if mode not in {"required", "best_effort"}:
        raise UploadSecurityError("UPLOAD_MALWARE_SCAN_MODE must be one of: required, best_effort, disabled.")

    scanner = _resolve_scanner_binary()
    if scanner is None:
        if mode == "required":
            raise UploadSecurityError(
                "Malware scanner is not available. Install ClamAV or set UPLOAD_MALWARE_SCAN_MODE=best_effort/disabled for non-production development."
            )
        return None

    command = [scanner, "--no-summary", str(Path(path))]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=DEFAULT_SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UploadSecurityError("Malware scan timed out; upload rejected.") from exc
    except OSError as exc:
        if mode == "best_effort":
            return None
        raise UploadSecurityError("Malware scanner could not be executed; upload rejected.") from exc

    output = f"{result.stdout}\n{result.stderr}".strip()
    normalized_output = output.lower()

    if result.returncode == 1 or "found" in normalized_output:
        raise UploadSecurityError("Malware detected in uploaded file.")

    if result.returncode != 0:
        print(
            "[ERROR] Malware scanner failed",
            {
                "scanner": scanner,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            flush=True,
        )

        if mode == "best_effort":
            return scanner

        raise UploadSecurityError("Malware scanner failed; upload rejected by fail-closed policy.")

    return scanner


def _resolve_scanner_binary() -> Optional[str]:
    configured = os.getenv("UPLOAD_MALWARE_SCANNER", "").strip()
    candidates = [configured] if configured else ["clamdscan", "clamscan"]
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    return None


def _normalize_extension(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    if normalized == ".jpe":
        normalized = ".jpg"
    return normalized


def _reject_dangerous_filename(filename: str) -> None:
    lowered = filename.lower()
    if "\x00" in filename:
        raise UploadSecurityError("Filename contains a null byte.")

    dangerous_extensions = (
        ".exe", ".dll", ".bat", ".cmd", ".sh", ".js", ".mjs", ".php", ".py",
        ".jar", ".scr", ".vbs", ".ps1", ".msi", ".apk", ".com", ".pif",
    )
    parts = Path(lowered).suffixes
    if len(parts) > 1 and any(part in dangerous_extensions for part in parts[:-1]):
        raise UploadSecurityError("Filename contains a dangerous double extension.")


def _assert_magic_signature(path: Path, extension: str) -> None:
    if extension in {".mp4", ".mov"}:
        _assert_mp4_mov_magic(path)
        return

    signatures = MAGIC_SIGNATURES.get(extension)
    if not signatures:
        return

    with path.open("rb") as handle:
        header = handle.read(16)

    if not any(header.startswith(signature) for signature in signatures):
        raise UploadSecurityError(
            f"File content does not match declared extension '{extension}'."
        )


def _assert_mp4_mov_magic(path: Path) -> None:
    with path.open("rb") as handle:
        header = handle.read(16)
    if len(header) < 12 or header[4:8] != b"ftyp":
        raise UploadSecurityError("File content does not match a valid MP4/MOV container.")


def _assert_safe_pdf(path: Path) -> None:
    if fitz is None:
        raise UploadSecurityError("PyMuPDF is required for safe PDF validation.")

    try:
        with fitz.open(path) as pdf:
            if pdf.is_encrypted:
                raise UploadSecurityError("Encrypted or password-protected PDFs are not accepted.")
            if int(pdf.page_count) < 1:
                raise UploadSecurityError("PDF has no pages.")
    except UploadSecurityError:
        raise
    except Exception as exc:
        raise UploadSecurityError("PDF could not be safely parsed.") from exc

    # Scan the raw bytes for active-content markers that can trigger script or
    # embedded payload behavior in PDF readers.
    with path.open("rb") as handle:
        content = handle.read().lower()

    for marker in PDF_ACTIVE_CONTENT_MARKERS:
        if marker in content:
            raise UploadSecurityError(
                f"PDF contains unsafe active-content marker: {marker.decode('ascii', errors='ignore')}."
            )


def _assert_safe_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            lowered_names = [name.lower() for name in names]
            required = {"[content_types].xml", "_rels/.rels", "word/document.xml"}
            if not required.issubset(set(lowered_names)):
                raise UploadSecurityError("DOCX is missing required Office document parts.")

            for marker in DOCX_FORBIDDEN_PART_MARKERS:
                if any(marker in name for name in lowered_names):
                    raise UploadSecurityError(
                        "DOCX contains macros, embedded objects, ActiveX, or custom UI content."
                    )

            total_uncompressed = 0
            total_compressed = 0
            for info in archive.infolist():
                name_path = Path(info.filename)
                if info.filename.startswith("/") or ".." in name_path.parts:
                    raise UploadSecurityError("DOCX contains unsafe path traversal entries.")

                total_uncompressed += int(info.file_size)
                total_compressed += max(int(info.compress_size), 1)

                if info.filename.lower().endswith(".rels"):
                    relationship_xml = archive.read(info.filename)[:1024 * 1024].lower()
                    if b'targetmode="external"' in relationship_xml or b"targetmode='external'" in relationship_xml:
                        raise UploadSecurityError("DOCX contains external relationships.")

            if total_uncompressed > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
                raise UploadSecurityError("DOCX expands to an unsafe size.")

            compression_ratio = total_uncompressed / max(total_compressed, 1)
            if compression_ratio > MAX_DOCX_COMPRESSION_RATIO:
                raise UploadSecurityError("DOCX has a suspicious compression ratio.")

    except UploadSecurityError:
        raise
    except zipfile.BadZipFile as exc:
        raise UploadSecurityError("DOCX is not a valid Office ZIP package.") from exc


def _assert_safe_image(path: Path) -> None:
    if Image is None:
        raise UploadSecurityError("Pillow is required for safe image validation.")

    try:
        with Image.open(path) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadSecurityError("Image could not be safely parsed.") from exc


def _assert_safe_text(path: Path) -> None:
    data = path.read_bytes()
    if b"\x00" in data:
        raise UploadSecurityError("Text file contains null bytes.")

    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UploadSecurityError("Text file must be valid UTF-8.") from exc

    if data:
        control_chars = sum(1 for byte in data if byte < 32 and byte not in {9, 10, 13})
        if control_chars / len(data) > MAX_TEXT_CONTROL_CHAR_RATIO:
            raise UploadSecurityError("Text file contains too many control characters.")


def _assert_safe_media_container(path: Path, extension: str) -> None:
    # The magic-byte checks above catch obvious spoofing. Full codec probing should
    # still run in the transcribe path inside a sandboxed FFmpeg/media worker.
    if extension == ".mp3":
        return
    if extension in {".mp4", ".mov", ".mkv"}:
        return
    raise UploadSecurityError("Unsupported media container.")


__all__ = [
    "UploadSecurityError",
    "UploadSecurityVerdict",
    "validate_upload_file",
    "scan_with_malware_scanner",
    "sha256_file",
]
