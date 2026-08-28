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
import logging
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import urlsplit


logger = logging.getLogger(__name__)

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


class UploadSecurityInfrastructureError(RuntimeError):
    """Raised when a required upload-security dependency cannot make a verdict."""


class MalwareScannerUnavailableError(UploadSecurityInfrastructureError):
    """Raised when fail-closed malware scanning is unavailable or fails."""


class MalwareDetectedError(UploadSecurityError):
    """Raised when the configured malware scanner rejects an uploaded file."""


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
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".flac": (b"fLaC",),
    ".webm": (b"\x1a\x45\xdf\xa3",),
    ".ogg": (b"OggS",),
    ".mkv": (b"\x1a\x45\xdf\xa3",),
    ".wmv": (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11\xa6\xd9\x00\xaa\x00\x62\xce\x6c",),
}

PDF_BLOCKED_ACTIVE_CONTENT_NAMES = (
    "javascript",
    "js",
    "launch",
    "embeddedfile",
    "xfa",
    "richmedia",
)

# /OpenAction and /AA are not malware by themselves. Clean PDFs often use
# /OpenAction for an initial page/zoom destination, and generated font names can
# contain strings such as /AAAAAA+FontName. Block them only when the PDF also
# contains action types that can execute code, launch files, or carry active
# payloads.
PDF_CONTEXTUAL_ACTION_NAMES = (
    "openaction",
    "aa",
)

PDF_DANGEROUS_ACTION_NAMES = (
    "javascript",
    "js",
    "launch",
    "richmediaexecute",
    "rendition",
    "movie",
    "sound",
    "submitform",
    "importdata",
    "gotoe",
)

# PDF names are introduced by "/" and end at whitespace or a delimiter. Match
# whole PDF name tokens only; do not match substrings inside benign names such as
# generated font prefixes like /AAAAAA+TimesNewRomanPS-BoldMT.
PDF_NAME_TERMINATOR_BYTES = b"\x00\t\n\f\r ()<>[]{}/%"

def _pdf_name_re(names: tuple[str, ...]) -> re.Pattern[bytes]:
    return re.compile(
        rb"/(?P<name>"
        + b"|".join(re.escape(name.encode("ascii")) for name in names)
        + rb")(?=$|["
        + re.escape(PDF_NAME_TERMINATOR_BYTES)
        + rb"])",
        re.IGNORECASE,
    )

PDF_BLOCKED_ACTIVE_CONTENT_RE = _pdf_name_re(PDF_BLOCKED_ACTIVE_CONTENT_NAMES)
PDF_CONTEXTUAL_ACTION_RE = _pdf_name_re(PDF_CONTEXTUAL_ACTION_NAMES)
PDF_DANGEROUS_ACTION_RE = _pdf_name_re(PDF_DANGEROUS_ACTION_NAMES)
PDF_CONTEXT_LOOKAHEAD_BYTES = int(os.getenv("UPLOAD_PDF_ACTION_CONTEXT_BYTES", "2048"))

DOCX_FORBIDDEN_PART_MARKERS = (
    "vbaproject.bin",
    "word/vbadata.xml",
    "activex/",
    "embeddings/",
    "oleobject",
    "customui/",
)

# OOXML permits external relationships for several very different purposes.
# Ordinary hyperlinks must survive DOCX -> PDF conversion, while relationships
# that can make an Office renderer retrieve or load external content (templates,
# images, OLE packages, and similar resources) remain blocked.
DOCX_RELATIONSHIP_NAMESPACES = frozenset(
    {
        "http://schemas.openxmlformats.org/package/2006/relationships",
        "http://purl.oclc.org/ooxml/package/relationships",
    }
)
DOCX_HYPERLINK_RELATIONSHIP_TYPES = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        "http://purl.oclc.org/ooxml/officeDocument/relationships/hyperlink",
    }
)
DOCX_ALLOWED_HYPERLINK_SCHEMES = frozenset({"http", "https", "mailto", "tel"})

MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES = int(
    os.getenv("UPLOAD_MAX_DOCX_UNCOMPRESSED_BYTES", str(50 * 1024 * 1024))
)
MAX_DOCX_COMPRESSION_RATIO = float(os.getenv("UPLOAD_MAX_DOCX_COMPRESSION_RATIO", "100"))
MAX_DOCX_RELATIONSHIP_PART_BYTES = int(
    os.getenv("UPLOAD_MAX_DOCX_RELATIONSHIP_PART_BYTES", str(1024 * 1024))
)
MAX_DOCX_RELATIONSHIPS_PER_PART = int(
    os.getenv("UPLOAD_MAX_DOCX_RELATIONSHIPS_PER_PART", "10000")
)
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
    elif normalized_extension in {".xlsx", ".pptx"}:
        _assert_safe_conversion_ooxml(source, normalized_extension)
        detected_type = normalized_extension.lstrip(".")
    elif normalized_extension in {".html", ".htm"}:
        _assert_safe_html(source)
        detected_type = "html"
    elif normalized_extension in {".jpg", ".jpeg", ".png"}:
        _assert_safe_image(source)
        detected_type = "image"
    elif normalized_extension == ".txt":
        _assert_safe_text(source)
        detected_type = "text"
    elif normalized_extension in {
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
    }:
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
        raise MalwareScannerUnavailableError(
            "Upload security is temporarily unavailable because malware scanning is misconfigured."
        )

    scanner = _resolve_scanner_binary()
    if scanner is None:
        if mode == "required":
            raise MalwareScannerUnavailableError(
                "File security scanning is temporarily unavailable; the upload was rejected by the required fail-closed policy."
            )
        return None

    scanner_name = Path(scanner).name

    if scanner_name == "clamdscan":
        command = [scanner, "--no-summary", "--fdpass", str(Path(path))]
    else:
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
        if mode == "best_effort":
            return None
        raise MalwareScannerUnavailableError(
            "File security scanning timed out; the upload was rejected by the required fail-closed policy."
        ) from exc
    except OSError as exc:
        if mode == "best_effort":
            return None
        raise MalwareScannerUnavailableError(
            "File security scanning is temporarily unavailable; the upload was rejected by the required fail-closed policy."
        ) from exc

    if result.returncode == 1:
        raise MalwareDetectedError("Malware detected in uploaded file.")

    if result.returncode != 0:
        # Never log the scanner command, source path, filename, stdout, or
        # stderr because scanner output commonly repeats the customer filename.
        logger.error(
            "Upload malware scanner failed: scanner=%s returncode=%s",
            scanner_name,
            result.returncode,
        )

        if mode == "best_effort":
            return None

        raise MalwareScannerUnavailableError(
            "File security scanning is temporarily unavailable; the upload was rejected by the required fail-closed policy."
        )

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
    if extension in {".mp4", ".mov", ".m4a"}:
        _assert_mp4_mov_magic(path)
        return
    if extension == ".wav":
        _assert_riff_magic(path, expected_form_type=b"WAVE", label="WAV")
        return
    if extension == ".avi":
        _assert_riff_magic(path, expected_form_type=b"AVI ", label="AVI")
        return
    if extension == ".aac":
        _assert_aac_magic(path)
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


def _assert_riff_magic(path: Path, *, expected_form_type: bytes, label: str) -> None:
    with path.open("rb") as handle:
        header = handle.read(12)
    if (
        len(header) < 12
        or header[:4] != b"RIFF"
        or header[8:12] != expected_form_type
    ):
        raise UploadSecurityError(
            f"File content does not match a valid {label} RIFF container."
        )


def _assert_aac_magic(path: Path) -> None:
    """Validate raw AAC in ADTS/ADIF form, including an optional ID3v2 prefix."""

    with path.open("rb") as handle:
        header = handle.read(10)
        if len(header) < 4:
            raise UploadSecurityError("File content does not match valid AAC audio.")

        if header.startswith(b"ID3"):
            if len(header) < 10 or any(byte & 0x80 for byte in header[6:10]):
                raise UploadSecurityError("AAC ID3 metadata header is malformed.")
            tag_size = (
                (header[6] << 21)
                | (header[7] << 14)
                | (header[8] << 7)
                | header[9]
            )
            offset = 10 + tag_size + (10 if header[5] & 0x10 else 0)
            handle.seek(offset)
            header = handle.read(4)

    if header.startswith(b"ADIF"):
        return
    if len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xF6) == 0xF0:
        return

    raise UploadSecurityError("File content does not match valid AAC ADTS/ADIF audio.")


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
    # embedded payload behavior in PDF readers. This is intentionally a PDF-name
    # token scan rather than a raw substring scan, because clean PDFs can contain
    # strings such as /AAAAAA+FontName that would otherwise false-positive /AA.
    with path.open("rb") as handle:
        content = handle.read()

    match = PDF_BLOCKED_ACTIVE_CONTENT_RE.search(content)
    if match is not None:
        marker = "/" + match.group("name").decode("ascii", errors="ignore").lower()
        raise UploadSecurityError(f"PDF contains unsafe active-content marker: {marker}.")

    contextual_match = PDF_CONTEXTUAL_ACTION_RE.search(content)
    while contextual_match is not None:
        window_start = contextual_match.start()
        window_end = min(len(content), contextual_match.end() + PDF_CONTEXT_LOOKAHEAD_BYTES)
        danger_match = PDF_DANGEROUS_ACTION_RE.search(content[window_start:window_end])
        if danger_match is not None:
            marker = "/" + contextual_match.group("name").decode("ascii", errors="ignore").lower()
            dangerous_marker = "/" + danger_match.group("name").decode("ascii", errors="ignore").lower()
            raise UploadSecurityError(
                f"PDF contains unsafe active-content marker: {marker} with {dangerous_marker}."
            )
        contextual_match = PDF_CONTEXTUAL_ACTION_RE.search(content, contextual_match.end())


def _assert_safe_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            lowered_names = [name.lower() for name in names]
            canonical_names = [name.replace("\\", "/").casefold() for name in names]
            if len(canonical_names) != len(set(canonical_names)):
                raise UploadSecurityError(
                    "DOCX contains duplicate or case-colliding package entries."
                )

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
                if (
                    info.filename.startswith("/")
                    or "\\" in info.filename
                    or ".." in name_path.parts
                ):
                    raise UploadSecurityError("DOCX contains unsafe path traversal entries.")

                if info.flag_bits & 0x1:
                    raise UploadSecurityError("DOCX contains encrypted package entries.")

                total_uncompressed += int(info.file_size)
                total_compressed += max(int(info.compress_size), 1)

                if info.filename.lower().endswith(".rels"):
                    _assert_safe_docx_relationship_part(archive, info)

            if total_uncompressed > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
                raise UploadSecurityError("DOCX expands to an unsafe size.")

            compression_ratio = total_uncompressed / max(total_compressed, 1)
            if compression_ratio > MAX_DOCX_COMPRESSION_RATIO:
                raise UploadSecurityError("DOCX has a suspicious compression ratio.")

    except UploadSecurityError:
        raise
    except zipfile.BadZipFile as exc:
        raise UploadSecurityError("DOCX is not a valid Office ZIP package.") from exc


def _assert_safe_docx_relationship_part(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> None:
    """Validate one OOXML relationship part without dereferencing its targets."""
    if int(info.file_size) > MAX_DOCX_RELATIONSHIP_PART_BYTES:
        raise UploadSecurityError("DOCX relationship metadata is too large.")

    relationship_xml = archive.read(info)
    lowered_xml = relationship_xml.lower()
    if b"<!doctype" in lowered_xml or b"<!entity" in lowered_xml:
        raise UploadSecurityError(
            "DOCX relationship metadata contains forbidden XML declarations."
        )

    try:
        root = ET.fromstring(relationship_xml)
    except ET.ParseError as exc:
        raise UploadSecurityError("DOCX contains malformed relationship metadata.") from exc

    namespace, local_name = _split_xml_name(root.tag)
    if local_name != "Relationships" or namespace not in DOCX_RELATIONSHIP_NAMESPACES:
        raise UploadSecurityError("DOCX contains invalid relationship metadata.")

    relationship_ids: set[str] = set()
    relationship_count = 0

    for relationship in root:
        child_namespace, child_name = _split_xml_name(relationship.tag)
        if child_name != "Relationship" or child_namespace != namespace:
            raise UploadSecurityError("DOCX contains invalid relationship metadata.")

        relationship_count += 1
        if relationship_count > MAX_DOCX_RELATIONSHIPS_PER_PART:
            raise UploadSecurityError("DOCX contains too many relationships.")

        relationship_id = relationship.attrib.get("Id", "").strip()
        relationship_type = relationship.attrib.get("Type", "").strip()
        target = relationship.attrib.get("Target", "")
        target_mode = relationship.attrib.get("TargetMode", "").strip().casefold()

        if not relationship_id or not relationship_type or not target:
            raise UploadSecurityError("DOCX contains incomplete relationship metadata.")
        if relationship_id in relationship_ids:
            raise UploadSecurityError("DOCX contains duplicate relationship identifiers.")
        relationship_ids.add(relationship_id)

        if target_mode not in {"", "internal", "external"}:
            raise UploadSecurityError("DOCX contains an invalid relationship target mode.")

        if target_mode == "external":
            if relationship_type not in DOCX_HYPERLINK_RELATIONSHIP_TYPES:
                raise UploadSecurityError(
                    "DOCX contains an unsafe external content relationship."
                )
            _assert_safe_docx_hyperlink_target(target)
        elif _looks_like_external_relationship_target(target):
            # TargetMode defaults to Internal. An absolute URI or network/local
            # path without an explicit External mode is malformed and must not
            # be allowed to bypass the external-target policy.
            raise UploadSecurityError(
                "DOCX contains an external target with an invalid target mode."
            )


def _split_xml_name(value: str) -> tuple[str, str]:
    if value.startswith("{") and "}" in value:
        namespace, local_name = value[1:].split("}", 1)
        return namespace, local_name
    return "", value


def _looks_like_external_relationship_target(target: str) -> bool:
    normalized = target.strip()
    if not normalized:
        return False
    if "\\" in normalized or normalized.startswith("//"):
        return True
    try:
        return bool(urlsplit(normalized).scheme)
    except ValueError:
        return True


def _assert_safe_docx_hyperlink_target(target: str) -> None:
    if target != target.strip() or not target:
        raise UploadSecurityError("DOCX contains an invalid external hyperlink target.")
    if "\\" in target or target.startswith("//"):
        raise UploadSecurityError("DOCX contains an unsafe external hyperlink target.")
    if any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in target
    ):
        raise UploadSecurityError("DOCX contains an unsafe external hyperlink target.")

    try:
        parsed = urlsplit(target)
    except ValueError as exc:
        raise UploadSecurityError("DOCX contains an invalid external hyperlink target.") from exc

    scheme = parsed.scheme.casefold()
    if scheme not in DOCX_ALLOWED_HYPERLINK_SCHEMES:
        raise UploadSecurityError("DOCX contains an unsafe external hyperlink scheme.")

    if scheme in {"http", "https"}:
        try:
            has_host = bool(parsed.netloc and parsed.hostname)
        except ValueError as exc:
            raise UploadSecurityError(
                "DOCX contains an invalid external hyperlink target."
            ) from exc
        if not has_host:
            raise UploadSecurityError("DOCX contains an invalid external hyperlink target.")
    elif not parsed.path:
        raise UploadSecurityError("DOCX contains an invalid external hyperlink target.")



def _assert_safe_conversion_ooxml(path: Path, extension: str) -> None:
    """Validate XLSX/PPTX packages before LibreOffice or parsers receive them."""
    required_by_extension = {
        ".xlsx": {"[content_types].xml", "_rels/.rels", "xl/workbook.xml"},
        ".pptx": {"[content_types].xml", "_rels/.rels", "ppt/presentation.xml"},
    }
    forbidden_markers = (
        "vbaproject.bin", "activex/", "embeddings/", "oleobject", "customui/",
    )
    required = required_by_extension[extension]

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            canonical_names = [name.replace("\\", "/").casefold() for name in names]
            if len(canonical_names) != len(set(canonical_names)):
                raise UploadSecurityError("Office file contains duplicate or case-colliding package entries.")
            if not required.issubset(set(canonical_names)):
                raise UploadSecurityError(f"{extension.upper().lstrip('.')} is missing required Office package parts.")

            total_uncompressed = 0
            total_compressed = 0
            for info in archive.infolist():
                normalized_name = info.filename.replace("\\", "/")
                name_path = Path(normalized_name)
                lowered = normalized_name.casefold()
                if normalized_name.startswith("/") or ".." in name_path.parts:
                    raise UploadSecurityError("Office file contains unsafe path traversal entries.")
                if info.flag_bits & 0x1:
                    raise UploadSecurityError("Office file contains encrypted package entries.")
                if any(marker in lowered for marker in forbidden_markers):
                    raise UploadSecurityError("Office file contains macros, embedded objects, ActiveX, or custom UI content.")

                total_uncompressed += int(info.file_size)
                total_compressed += max(int(info.compress_size), 1)
                if lowered.endswith(".rels"):
                    _assert_safe_conversion_ooxml_relationship_part(archive, info)

            if total_uncompressed > MAX_DOCX_TOTAL_UNCOMPRESSED_BYTES:
                raise UploadSecurityError("Office file expands to an unsafe size.")
            if total_uncompressed / max(total_compressed, 1) > MAX_DOCX_COMPRESSION_RATIO:
                raise UploadSecurityError("Office file has a suspicious compression ratio.")
    except UploadSecurityError:
        raise
    except zipfile.BadZipFile as exc:
        raise UploadSecurityError("Office file is not a valid OOXML ZIP package.") from exc


def _assert_safe_conversion_ooxml_relationship_part(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
) -> None:
    if int(info.file_size) > MAX_DOCX_RELATIONSHIP_PART_BYTES:
        raise UploadSecurityError("Office relationship metadata is too large.")
    payload = archive.read(info)
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise UploadSecurityError("Office relationship metadata contains forbidden XML declarations.")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise UploadSecurityError("Office file contains malformed relationship metadata.") from exc

    relationship_count = 0
    for relationship in root:
        relationship_count += 1
        if relationship_count > MAX_DOCX_RELATIONSHIPS_PER_PART:
            raise UploadSecurityError("Office file contains too many relationships.")
        target = relationship.attrib.get("Target", "").strip()
        target_mode = relationship.attrib.get("TargetMode", "").strip().casefold()
        relationship_type = relationship.attrib.get("Type", "").strip().casefold()
        if target_mode == "external":
            # External hyperlinks are inert conversion metadata; all other external
            # resources are rejected so LibreOffice cannot be used as an SSRF client.
            if not relationship_type.endswith("/hyperlink"):
                raise UploadSecurityError("Office file contains an unsafe external content relationship.")
            _assert_safe_docx_hyperlink_target(target)
        elif _looks_like_external_relationship_target(target):
            raise UploadSecurityError("Office file contains an external target with an invalid target mode.")


def _assert_safe_html(path: Path) -> None:
    """Accept UTF-8 HTML while rejecting active/embedded executable content."""
    _assert_safe_text(path)
    text = path.read_text(encoding="utf-8").casefold()
    forbidden = ("<script", "<object", "<embed", "<iframe", "<applet")
    if any(marker in text for marker in forbidden):
        raise UploadSecurityError("HTML contains active or embedded content that is not allowed for conversion.")

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
    # The magic-byte checks above catch obvious spoofing. The upload layer also
    # performs authoritative ffprobe duration parsing, while Transcribe normalizes
    # browser-recorded/video containers through FFmpeg before ASR.
    if extension in {
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
    }:
        return
    raise UploadSecurityError("Unsupported media container.")


__all__ = [
    "MalwareDetectedError",
    "MalwareScannerUnavailableError",
    "UploadSecurityError",
    "UploadSecurityInfrastructureError",
    "UploadSecurityVerdict",
    "validate_upload_file",
    "scan_with_malware_scanner",
    "sha256_file",
]