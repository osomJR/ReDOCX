from __future__ import annotations

"""Durable retention registry for uploaded ReDOCX source files."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator
import json
import os
import threading
import uuid

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


UPLOAD_ROOT = Path(os.getenv("UPLOAD_BASE_DIR", "uploads")).expanduser().resolve()
SUCCESS_RETENTION_MINUTES = int(os.getenv("UPLOAD_SUCCESS_RETENTION_MINUTES", "15"))
FAILURE_RETENTION_MINUTES = int(os.getenv("UPLOAD_FAILURE_RETENTION_MINUTES", "10"))
HEARTBEAT_SECONDS = max(15, int(os.getenv("UPLOAD_RETENTION_HEARTBEAT_SECONDS", "30")))
_REGISTRY_PATH = UPLOAD_ROOT / ".upload_retention.json"
_LOCK_PATH = UPLOAD_ROOT / ".upload_retention.lock"
_THREAD_LOCK = threading.RLock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_upload_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if path == UPLOAD_ROOT or UPLOAD_ROOT not in path.parents:
        raise ValueError("Upload retention may only manage files beneath the upload root.")
    return path


def _key(path: Path) -> str:
    return str(path.relative_to(UPLOAD_ROOT)).replace(os.sep, "/")


@contextmanager
def _registry_lock() -> Iterator[None]:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, _LOCK_PATH.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load() -> dict[str, dict[str, Any]]:
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        value = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Fail safe: the filesystem scan in cleanup_due_uploads still removes
        # stale files even if the registry becomes unreadable.
        return {}
    return value if isinstance(value, dict) else {}


def _save(registry: dict[str, dict[str, Any]]) -> None:
    temp = _REGISTRY_PATH.with_name(f".upload_retention.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(
            json.dumps(registry, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        temp.chmod(0o600)
        temp.replace(_REGISTRY_PATH)
    finally:
        temp.unlink(missing_ok=True)


def register_upload_path(path: str | Path) -> None:
    candidate = _safe_upload_path(path)
    now = _utc_now()
    with _registry_lock():
        registry = _load()
        registry[_key(candidate)] = {
            "status": "processing",
            "created_at": _iso(now),
            "last_activity_at": _iso(now),
            "delete_after": None,
        }
        _save(registry)


def touch_upload_paths(paths: Iterable[str | Path]) -> list[Path]:
    candidates = _normalize_paths(paths)
    if not candidates:
        return []
    now = _utc_now()
    with _registry_lock():
        registry = _load()
        for candidate in candidates:
            item = registry.get(_key(candidate)) or {
                "created_at": _iso(now),
            }
            item.update(
                status="processing",
                last_activity_at=_iso(now),
                delete_after=None,
            )
            registry[_key(candidate)] = item
        _save(registry)
    return candidates


def mark_upload_paths_processed(
    paths: Iterable[str | Path],
    *,
    success: bool,
) -> None:
    candidates = _normalize_paths(paths)
    if not candidates:
        return
    now = _utc_now()
    retention = SUCCESS_RETENTION_MINUTES if success else FAILURE_RETENTION_MINUTES
    with _registry_lock():
        registry = _load()
        for candidate in candidates:
            item = registry.get(_key(candidate)) or {"created_at": _iso(now)}
            item.update(
                status="processed" if success else "failed",
                last_activity_at=_iso(now),
                delete_after=_iso(now + timedelta(minutes=retention)),
            )
            registry[_key(candidate)] = item
        _save(registry)


def collect_request_upload_paths(request: Any) -> list[Path]:
    found: set[Path] = set()
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if value is None or isinstance(value, (bytes, bytearray, int, float, bool)):
            return
        if isinstance(value, str):
            try:
                candidate = _safe_upload_path(value)
            except (OSError, RuntimeError, ValueError):
                return
            if candidate.exists() and candidate.is_file():
                found.add(candidate)
            return

        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)

        if isinstance(value, dict):
            for nested in value.values():
                visit(nested)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                visit(nested)
            return

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                visit(model_dump(mode="python"))
                return
            except Exception:
                pass
        values = getattr(value, "__dict__", None)
        if isinstance(values, dict):
            visit(values)

    visit(request)
    return sorted(found)


@contextmanager
def upload_processing_session(
    request: Any,
    *,
    extra_paths: Iterable[str | Path] = (),
) -> Iterator[list[Path]]:
    paths = _normalize_paths([*collect_request_upload_paths(request), *extra_paths])
    touch_upload_paths(paths)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(HEARTBEAT_SECONDS):
            touch_upload_paths(paths)

    thread = threading.Thread(
        target=heartbeat,
        name="redocx-upload-retention-heartbeat",
        daemon=True,
    )
    if paths:
        thread.start()
    success = False
    try:
        yield paths
        success = True
    finally:
        stop.set()
        if paths:
            thread.join(timeout=2)
        mark_upload_paths_processed(paths, success=success)


def cleanup_due_uploads(*, now: datetime | None = None) -> int:
    current = now or _utc_now()
    removed = 0
    tracked: set[str] = set()

    with _registry_lock():
        registry = _load()
        for relative_key, item in list(registry.items()):
            tracked.add(relative_key)
            try:
                candidate = _safe_upload_path(UPLOAD_ROOT / relative_key)
            except (OSError, RuntimeError, ValueError):
                registry.pop(relative_key, None)
                continue
            if not candidate.exists():
                registry.pop(relative_key, None)
                continue

            status = str(item.get("status") or "processing")
            delete_after = _parse(item.get("delete_after"))
            last_activity = _parse(item.get("last_activity_at")) or _parse(
                item.get("created_at")
            )
            due = delete_after is not None and current >= delete_after
            abandoned = (
                status == "processing"
                and last_activity is not None
                and current >= last_activity + timedelta(minutes=FAILURE_RETENTION_MINUTES)
            )
            if due or abandoned:
                candidate.unlink(missing_ok=True)
                registry.pop(relative_key, None)
                removed += 1
        _save(registry)

    # Crash-safe cleanup for unregistered quarantine or legacy upload files.
    fallback_cutoff = current - timedelta(minutes=FAILURE_RETENTION_MINUTES)
    if UPLOAD_ROOT.exists():
        for candidate in UPLOAD_ROOT.rglob("*"):
            if not candidate.is_file():
                continue
            if candidate.name.startswith(".upload_retention"):
                continue
            relative_key = _key(candidate)
            if relative_key in tracked:
                continue
            try:
                modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified <= fallback_cutoff:
                candidate.unlink(missing_ok=True)
                removed += 1

    _remove_empty_upload_directories()
    return removed


def _normalize_paths(paths: Iterable[str | Path]) -> list[Path]:
    normalized: set[Path] = set()
    for value in paths:
        try:
            candidate = _safe_upload_path(value)
        except (OSError, RuntimeError, TypeError, ValueError):
            continue
        normalized.add(candidate)
    return sorted(normalized)


def _remove_empty_upload_directories() -> None:
    if not UPLOAD_ROOT.exists():
        return
    directories = sorted(
        [path for path in UPLOAD_ROOT.rglob("*") if path.is_dir()],
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            continue


__all__ = [
    "UPLOAD_ROOT",
    "SUCCESS_RETENTION_MINUTES",
    "FAILURE_RETENTION_MINUTES",
    "register_upload_path",
    "touch_upload_paths",
    "mark_upload_paths_processed",
    "collect_request_upload_paths",
    "upload_processing_session",
    "cleanup_due_uploads",
]
