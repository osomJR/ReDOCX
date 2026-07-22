from __future__ import annotations

"""
V1 artifact storage layer for downloadable document outputs.

Purpose:
- persist generated downloadable artifacts produced by:
  - conversion_processing/convert.py
  - writer.py for AI document actions
  - data_protection/redaction/redact.py
  - data_protection/data_masking/data_mask.py
- provide a stable storage location/key for persisted files
- optionally expose a download URL placeholder
- support cleanup of expired artifacts based on retention policy

Design notes:
- schema-agnostic: this module stores files only
- does not perform conversion, writing, LLM, ASR, analyzer orchestration,
  redaction, or data masking
- local filesystem implementation is the default MVP backend
- cloud/object-storage backends can be added later behind the same protocol
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, Protocol
from contextlib import contextmanager
import mimetypes
import os
import re
import uuid
import shutil
import logging
import json
import threading

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows/local compatibility.
    fcntl = None  # type: ignore


DEFAULT_RETENTION_HOURS = int(os.getenv("ARTIFACT_RETENTION_HOURS", "24"))
DEFAULT_DOWNLOAD_BASE_URL = os.getenv("ARTIFACT_DOWNLOAD_BASE_URL")
DEFAULT_ARTIFACT_STORAGE_DIR = os.getenv("ARTIFACT_STORAGE_DIR", "artifacts")
logger = logging.getLogger(__name__)
_OWNER_INDEX_LOCK = threading.RLock()


@dataclass(frozen=True)
class StoredArtifact:
    """
    Persisted artifact metadata returned by the storage layer.
    """

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


class StorageBackend(Protocol):
    """Artifact persistence interface."""

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


class LocalArtifactStorage:
    """
    Local filesystem storage backend for MVP use.
    """

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        retention_hours: int = DEFAULT_RETENTION_HOURS,
        download_base_url: Optional[str] = None,
    ) -> None:
        resolved_base_dir = base_dir or os.getenv(
            "ARTIFACT_STORAGE_DIR",
            DEFAULT_ARTIFACT_STORAGE_DIR,
        )

        self.base_dir = Path(resolved_base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        if retention_hours < 1:
            raise ValueError("retention_hours must be >= 1.")

        self.retention_hours = retention_hours

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
        if not source_path.exists():
            raise FileNotFoundError(f"Generated artifact not found: {source_path}")

        normalized_artifact_name = _normalize_artifact_name(artifact_name)
        resolved_content_type = content_type or guess_content_type(str(source_path))

        storage_key = self._build_storage_key(normalized_artifact_name)
        destination = self.base_dir / storage_key
        logger.debug("Persisting artifact", extra={"base_dir": str(self.base_dir), "storage_key": storage_key, "destination": str(destination)})
        destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(source_path, destination)

        normalized_storage_key = storage_key.replace(os.sep, "/")

        download_url = None
        if self.download_base_url:
            download_url = f"{self.download_base_url}/{normalized_storage_key}"

        if owner_user_id:
            record_artifact_owner(
                storage_key=normalized_storage_key,
                owner_user_id=owner_user_id,
                organization_id=organization_id,
                feature=feature,
                original_artifact_name=normalized_artifact_name,
                base_dir=str(self.base_dir),
            )

        return StoredArtifact(
            storage_key=normalized_storage_key,
            stored_path=str(destination),
            original_artifact_name=normalized_artifact_name,
            content_type=resolved_content_type,
            download_url=download_url,
            owner_user_id=owner_user_id,
            organization_id=organization_id,
        )

    def cleanup_expired(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        removed = 0
        removed_storage_keys: list[str] = []
        owner_index_path = _owner_index_path(str(self.base_dir)).resolve()

        for path in self.base_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.resolve() == owner_index_path or path.name.startswith(".artifact_owners"):
                continue

            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                removed_storage_keys.append(
                    str(path.relative_to(self.base_dir)).replace(os.sep, "/")
                )
                path.unlink(missing_ok=True)
                removed += 1

        if removed_storage_keys:
            with _OWNER_INDEX_LOCK, _owner_index_file_lock(str(self.base_dir)):
                index = _load_owner_index(base_dir=str(self.base_dir))
                for storage_key in removed_storage_keys:
                    index.pop(storage_key, None)
                _save_owner_index(index, base_dir=str(self.base_dir))

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
        unique_prefix = uuid.uuid4().hex[:12]
        return str(Path(today) / f"{unique_prefix}-{safe_name}")

    def _remove_empty_directories(self) -> None:
        directories = sorted(
            [p for p in self.base_dir.rglob("*") if p.is_dir()],
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for directory in directories:
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
    base_dir: str | None = None,
) -> ArtifactOwnerMetadata:
    normalized_key = _normalize_storage_key(storage_key).replace(os.sep, "/")
    owner = str(owner_user_id or "").strip()
    if not owner:
        raise ValueError("owner_user_id is required for artifact ownership.")

    metadata = ArtifactOwnerMetadata(
        storage_key=normalized_key,
        owner_user_id=owner,
        organization_id=str(organization_id).strip() if organization_id else None,
        feature=str(feature).strip() if feature else None,
        original_artifact_name=str(original_artifact_name).strip() if original_artifact_name else None,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
    )

    with _OWNER_INDEX_LOCK, _owner_index_file_lock(base_dir):
        index = _load_owner_index(base_dir=base_dir)
        index[normalized_key] = asdict(metadata)
        _save_owner_index(index, base_dir=base_dir)
    return metadata


def get_artifact_owner(storage_key: str, *, base_dir: str | None = None) -> Optional[ArtifactOwnerMetadata]:
    normalized_key = _normalize_storage_key(storage_key).replace(os.sep, "/")
    raw = _load_owner_index(base_dir=base_dir).get(normalized_key)
    if not isinstance(raw, dict):
        return None
    try:
        return ArtifactOwnerMetadata(
            storage_key=normalized_key,
            owner_user_id=str(raw.get("owner_user_id") or ""),
            organization_id=raw.get("organization_id"),
            feature=raw.get("feature"),
            original_artifact_name=raw.get("original_artifact_name"),
            created_at_iso=raw.get("created_at_iso"),
        )
    except TypeError:
        return None


def _owner_index_path(base_dir: str | None = None) -> Path:
    resolved_base = Path(base_dir or os.getenv("ARTIFACT_STORAGE_DIR", DEFAULT_ARTIFACT_STORAGE_DIR)).expanduser().resolve()
    resolved_base.mkdir(parents=True, exist_ok=True)
    return resolved_base / ".artifact_owners.json"


@contextmanager
def _owner_index_file_lock(base_dir: str | None = None):
    """Serialize owner-index read/modify/write cycles across worker processes."""
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
            logger.warning("Artifact owner index could not be read; treating as empty", extra={"path": str(path)})
            return {}
        return loaded if isinstance(loaded, dict) else {}


def _save_owner_index(index: dict[str, Any], *, base_dir: str | None = None) -> None:
    with _OWNER_INDEX_LOCK:
        path = _owner_index_path(base_dir)
        temp_path = path.with_name(f".artifact_owners.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

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

    safe_stem = stem or "artifact"
    safe_suffix = suffix if suffix else ""
    return f"{safe_stem}{safe_suffix}"


__all__ = [
    "DEFAULT_RETENTION_HOURS",
    "DEFAULT_DOWNLOAD_BASE_URL",
    "StoredArtifact",
    "ArtifactOwnerMetadata",
    "StorageBackend",
    "LocalArtifactStorage",
    "record_artifact_owner",
    "get_artifact_owner",
    "guess_content_type",
]
