from __future__ import annotations

"""Owner-scoped, short-lived storage for downloadable ReDOCX artifacts."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import uuid

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows/local compatibility.
    fcntl = None  # type: ignore


def _default_retention_minutes() -> int:
    explicit = os.getenv("ARTIFACT_RETENTION_MINUTES", "").strip()
    if explicit:
        return int(explicit)
    legacy_hours = os.getenv("ARTIFACT_RETENTION_HOURS", "").strip()
    if legacy_hours:
        return int(legacy_hours) * 60
    return 30


DEFAULT_RETENTION_MINUTES = _default_retention_minutes()
# Kept as a compatibility export for older imports. New code should use minutes.
DEFAULT_RETENTION_HOURS = max(1, (DEFAULT_RETENTION_MINUTES + 59) // 60)
DEFAULT_DOWNLOAD_BASE_URL = os.getenv("ARTIFACT_DOWNLOAD_BASE_URL")
DEFAULT_ARTIFACT_STORAGE_DIR = os.getenv("ARTIFACT_STORAGE_DIR", "artifacts")
logger = logging.getLogger(__name__)
_OWNER_INDEX_LOCK = threading.RLock()


@dataclass(frozen=True)
class ArtifactRequestOwner:
    owner_user_id: str
    organization_id: Optional[str] = None
    feature: Optional[str] = None


_ARTIFACT_OWNER_CONTEXT: ContextVar[ArtifactRequestOwner | None] = ContextVar(
    "redocx_artifact_owner",
    default=None,
)


@dataclass(frozen=True)
class StoredArtifact:
    storage_key: str
    stored_path: str
    original_artifact_name: str
    content_type: Optional[str] = None
    download_url: Optional[str] = None
    owner_user_id: Optional[str] = None
    organization_id: Optional[str] = None


@dataclass(frozen=True)
class ArtifactOwnerMetadata:
    storage_key: str
    owner_user_id: str
    organization_id: Optional[str] = None
    feature: Optional[str] = None
    original_artifact_name: Optional[str] = None
    created_at_iso: Optional[str] = None
    expires_at_iso: Optional[str] = None


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        feature: Optional[str] = None,
    ) -> StoredArtifact:
        ...

    def cleanup_expired(self) -> int:
        ...


@contextmanager
def artifact_owner_context(
    owner_user_id: str,
    *,
    organization_id: Optional[str] = None,
    feature: Optional[str] = None,
) -> Iterator[ArtifactRequestOwner]:
    """Attach the authenticated owner to every artifact created in this context."""
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("Authenticated artifact owner_user_id is required.")
    context = ArtifactRequestOwner(
        owner_user_id=owner,
        organization_id=str(organization_id).strip() if organization_id else None,
        feature=str(feature).strip() if feature else None,
    )
    token = _ARTIFACT_OWNER_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ARTIFACT_OWNER_CONTEXT.reset(token)


def current_artifact_owner() -> ArtifactRequestOwner | None:
    return _ARTIFACT_OWNER_CONTEXT.get()


class LocalArtifactStorage:
    """Local MVP storage with mandatory ownership and automatic expiry metadata."""

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        retention_minutes: int | None = None,
        retention_hours: int | None = None,
        download_base_url: Optional[str] = None,
    ) -> None:
        resolved_base_dir = base_dir or os.getenv(
            "ARTIFACT_STORAGE_DIR",
            DEFAULT_ARTIFACT_STORAGE_DIR,
        )
        self.base_dir = Path(resolved_base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        if retention_minutes is None:
            retention_minutes = (
                int(retention_hours) * 60
                if retention_hours is not None
                else DEFAULT_RETENTION_MINUTES
            )
        if int(retention_minutes) < 1:
            raise ValueError("retention_minutes must be >= 1.")
        self.retention_minutes = int(retention_minutes)
        self.retention_hours = self.retention_minutes / 60

        resolved_download_base_url = (
            download_base_url
            if download_base_url is not None
            else os.getenv("ARTIFACT_DOWNLOAD_BASE_URL")
        )
        self.download_base_url = (
            resolved_download_base_url.rstrip("/")
            if resolved_download_base_url
            else None
        )

    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        feature: Optional[str] = None,
    ) -> StoredArtifact:
        source_path = Path(_normalize_source_file_path(source_file_path))
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError("Generated artifact was not found.")

        context_owner = current_artifact_owner()
        resolved_owner = str(
            owner_user_id
            or (context_owner.owner_user_id if context_owner else "")
            or ""
        ).strip()
        if not resolved_owner:
            raise ValueError(
                "Artifact persistence requires an authenticated owner_user_id."
            )
        resolved_organization = (
            str(organization_id).strip()
            if organization_id
            else context_owner.organization_id
            if context_owner
            else None
        )
        resolved_feature = (
            str(feature).strip()
            if feature
            else context_owner.feature
            if context_owner
            else None
        )

        normalized_artifact_name = _normalize_artifact_name(artifact_name)
        resolved_content_type = content_type or guess_content_type(str(source_path))
        storage_key = self._build_storage_key(normalized_artifact_name)
        destination = self.base_dir / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(source_path, destination)
            normalized_storage_key = storage_key.replace(os.sep, "/")
            record_artifact_owner(
                storage_key=normalized_storage_key,
                owner_user_id=resolved_owner,
                organization_id=resolved_organization,
                feature=resolved_feature,
                original_artifact_name=normalized_artifact_name,
                retention_minutes=self.retention_minutes,
                base_dir=str(self.base_dir),
            )
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        download_url = None
        if self.download_base_url:
            download_url = f"{self.download_base_url}/{normalized_storage_key}"

        return StoredArtifact(
            storage_key=normalized_storage_key,
            stored_path=str(destination),
            original_artifact_name=normalized_artifact_name,
            content_type=resolved_content_type,
            download_url=download_url,
            owner_user_id=resolved_owner,
            organization_id=resolved_organization,
        )

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        legacy_cutoff = now - timedelta(minutes=self.retention_minutes)
        removed = 0

        with _OWNER_INDEX_LOCK, _owner_index_file_lock(str(self.base_dir)):
            index = _load_owner_index(base_dir=str(self.base_dir))
            for storage_key, raw in list(index.items()):
                metadata = _metadata_from_raw(storage_key, raw)
                if metadata is None or is_artifact_expired(metadata, now=now):
                    try:
                        candidate = self.resolve_storage_key(storage_key)
                    except ValueError:
                        candidate = None
                    if candidate is not None and candidate.is_file():
                        candidate.unlink(missing_ok=True)
                        removed += 1
                    index.pop(storage_key, None)
            _save_owner_index(index, base_dir=str(self.base_dir))

        # Remove legacy, unowned downloadable files after the same 30-minute
        # window. Runtime databases, locks and queue sources are never treated
        # as user artifacts.
        for path in self.base_dir.rglob("*"):
            if not path.is_file() or _is_internal_runtime_path(path, self.base_dir):
                continue
            relative_key = str(path.relative_to(self.base_dir)).replace(os.sep, "/")
            if get_artifact_owner(relative_key, base_dir=str(self.base_dir)) is not None:
                continue
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified < legacy_cutoff:
                path.unlink(missing_ok=True)
                removed += 1

        self._remove_empty_directories()
        return removed

    def resolve_storage_key(self, storage_key: str) -> Path:
        normalized_key = _normalize_storage_key(storage_key)
        resolved = (self.base_dir / normalized_key).resolve()
        base_resolved = self.base_dir.resolve()
        if resolved != base_resolved and base_resolved not in resolved.parents:
            raise ValueError("Resolved storage key escaped the artifact base directory.")
        return resolved

    def exists(self, storage_key: str) -> bool:
        return self.resolve_storage_key(storage_key).exists()

    def build_download_url(self, storage_key: str) -> Optional[str]:
        if not self.download_base_url:
            return None
        normalized_key = _normalize_storage_key(storage_key).replace(os.sep, "/")
        return f"{self.download_base_url}/{normalized_key}"

    def _build_storage_key(self, artifact_name: str) -> str:
        today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
        safe_name = _safe_file_name(artifact_name)
        unique_prefix = uuid.uuid4().hex[:16]
        return str(Path(today) / f"{unique_prefix}-{safe_name}")

    def _remove_empty_directories(self) -> None:
        directories = sorted(
            [p for p in self.base_dir.rglob("*") if p.is_dir()],
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for directory in directories:
            if directory.name == "compression_sources":
                continue
            try:
                directory.rmdir()
            except OSError:
                continue


def record_artifact_owner(
    *,
    storage_key: str,
    owner_user_id: str,
    organization_id: Optional[str] = None,
    feature: Optional[str] = None,
    original_artifact_name: Optional[str] = None,
    retention_minutes: int = DEFAULT_RETENTION_MINUTES,
    base_dir: str | None = None,
) -> ArtifactOwnerMetadata:
    normalized_key = _normalize_storage_key(storage_key).replace(os.sep, "/")
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("owner_user_id is required for artifact ownership.")
    if int(retention_minutes) < 1:
        raise ValueError("retention_minutes must be >= 1.")

    created_at = datetime.now(timezone.utc)
    metadata = ArtifactOwnerMetadata(
        storage_key=normalized_key,
        owner_user_id=owner,
        organization_id=str(organization_id).strip() if organization_id else None,
        feature=str(feature).strip() if feature else None,
        original_artifact_name=(
            str(original_artifact_name).strip() if original_artifact_name else None
        ),
        created_at_iso=created_at.isoformat(),
        expires_at_iso=(created_at + timedelta(minutes=int(retention_minutes))).isoformat(),
    )

    with _OWNER_INDEX_LOCK, _owner_index_file_lock(base_dir):
        index = _load_owner_index(base_dir=base_dir)
        index[normalized_key] = asdict(metadata)
        _save_owner_index(index, base_dir=base_dir)
    return metadata


def get_artifact_owner(
    storage_key: str,
    *,
    base_dir: str | None = None,
) -> Optional[ArtifactOwnerMetadata]:
    normalized_key = _normalize_storage_key(storage_key).replace(os.sep, "/")
    raw = _load_owner_index(base_dir=base_dir).get(normalized_key)
    return _metadata_from_raw(normalized_key, raw)


def is_artifact_expired(
    metadata: ArtifactOwnerMetadata,
    *,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.now(timezone.utc)
    expiry = _parse_datetime(metadata.expires_at_iso)
    if expiry is None:
        created = _parse_datetime(metadata.created_at_iso)
        if created is None:
            return True
        expiry = created + timedelta(minutes=DEFAULT_RETENTION_MINUTES)
    return current >= expiry


def _metadata_from_raw(storage_key: str, raw: Any) -> Optional[ArtifactOwnerMetadata]:
    if not isinstance(raw, dict):
        return None
    owner = str(raw.get("owner_user_id") or "").strip()
    if not owner:
        return None
    try:
        return ArtifactOwnerMetadata(
            storage_key=storage_key,
            owner_user_id=owner,
            organization_id=raw.get("organization_id"),
            feature=raw.get("feature"),
            original_artifact_name=raw.get("original_artifact_name"),
            created_at_iso=raw.get("created_at_iso"),
            expires_at_iso=raw.get("expires_at_iso"),
        )
    except TypeError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _owner_index_path(base_dir: str | None = None) -> Path:
    resolved_base = Path(
        base_dir or os.getenv("ARTIFACT_STORAGE_DIR", DEFAULT_ARTIFACT_STORAGE_DIR)
    ).expanduser().resolve()
    resolved_base.mkdir(parents=True, exist_ok=True)
    return resolved_base / ".artifact_owners.json"


@contextmanager
def _owner_index_file_lock(base_dir: str | None = None):
    resolved_base = Path(
        base_dir or os.getenv("ARTIFACT_STORAGE_DIR", DEFAULT_ARTIFACT_STORAGE_DIR)
    ).expanduser().resolve()
    resolved_base.mkdir(parents=True, exist_ok=True)
    lock_path = resolved_base / ".artifact_owners.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _load_owner_index(*, base_dir: str | None = None) -> dict[str, Any]:
    with _OWNER_INDEX_LOCK:
        path = _owner_index_path(base_dir)
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.error("Artifact ownership metadata is unreadable; downloads will fail closed.")
            return {}
        return loaded if isinstance(loaded, dict) else {}


def _save_owner_index(index: dict[str, Any], *, base_dir: str | None = None) -> None:
    with _OWNER_INDEX_LOCK:
        path = _owner_index_path(base_dir)
        temp_path = path.with_name(f".artifact_owners.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(
                json.dumps(index, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            temp_path.chmod(0o600)
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def _is_internal_runtime_path(path: Path, base_dir: Path) -> bool:
    relative = path.relative_to(base_dir)
    if any(part.startswith(".") for part in relative.parts):
        return True
    if "compression_sources" in relative.parts:
        return True
    lower_name = path.name.lower()
    return lower_name.endswith((".sqlite", ".sqlite3", ".db", "-wal", "-shm"))


def guess_content_type(file_path: str) -> Optional[str]:
    guessed, _ = mimetypes.guess_type(file_path)
    return guessed


def _normalize_source_file_path(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("source_file_path must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("source_file_path cannot be empty.")
    return normalized


def _normalize_artifact_name(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("artifact_name must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("artifact_name cannot be empty.")
    return normalized


def _normalize_storage_key(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("storage_key must be a string.")
    normalized = value.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("storage_key cannot be empty.")
    candidate = Path(normalized)
    if candidate.is_absolute():
        raise ValueError("storage_key must be relative.")
    if any(part == ".." for part in candidate.parts):
        raise ValueError("storage_key must not contain parent-directory traversal.")
    if any(part.strip() == "" for part in candidate.parts):
        raise ValueError("storage_key contains invalid path segments.")
    return str(candidate)


def _safe_file_name(value: str) -> str:
    raw = value.strip()
    if not raw:
        return "artifact.bin"
    path = Path(raw)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-._")
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix)
    return f"{stem or 'artifact'}{suffix or ''}"


__all__ = [
    "DEFAULT_RETENTION_MINUTES",
    "DEFAULT_RETENTION_HOURS",
    "DEFAULT_DOWNLOAD_BASE_URL",
    "StoredArtifact",
    "ArtifactOwnerMetadata",
    "ArtifactRequestOwner",
    "StorageBackend",
    "LocalArtifactStorage",
    "artifact_owner_context",
    "current_artifact_owner",
    "record_artifact_owner",
    "get_artifact_owner",
    "is_artifact_expired",
    "guess_content_type",
]
