from __future__ import annotations

"""Background janitor for uploads, artifacts and team attachments."""

import logging
import os
import threading
from pathlib import Path

from backend.src.storage.artifacts import LocalArtifactStorage
from backend.team_retention_jobs import purge_expired_team_attachments
from backend.upload_retention import cleanup_due_uploads


logger = logging.getLogger(__name__)
_INTERVAL_SECONDS = max(15, int(os.getenv("STORAGE_RETENTION_SWEEP_SECONDS", "30")))
_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_THREAD_LOCK = threading.Lock()


def _assert_vault_storage_is_not_artifact_managed(root: Path) -> None:
    artifact_root = root.expanduser().resolve()
    vault_root = Path(os.getenv("VAULT_STORAGE_DIR", "vault_data")).expanduser().resolve()
    if vault_root == artifact_root or artifact_root in vault_root.parents:
        raise RuntimeError(
            "VAULT_STORAGE_DIR must not be located inside ARTIFACT_STORAGE_DIR. "
            "Vault is durable encrypted user storage and must not be managed by "
            "short-lived artifact retention."
        )


def _artifact_storages() -> list[LocalArtifactStorage]:
    root = Path(os.getenv("ARTIFACT_STORAGE_DIR", "artifacts")).expanduser()
    _assert_vault_storage_is_not_artifact_managed(root)
    candidates = [
        root,
        root / "ai_documents",
        root / "compliance",
        root / "structured_extraction",
        root / "pdf_tools" / "combine",
        root / "pdf_tools" / "split",
        root / "pdf_tools" / "edit",
        root / "pdf_tools" / "compress",
        root / "pdf_tools" / "lock",
        root / "pdf_tools" / "preview",
        root / "pdf_tools" / "preview" / "pages",
        root / "esignature" / "signed",
        root / "esignature" / "previews",
        root / "esignature" / "certificates",
        root / "esignature" / "bundles",
    ]

    # Vault's durable encrypted store (VAULT_STORAGE_DIR) is intentionally not an
    # artifact store and must never be retention-swept here. Only the optional
    # short-lived owner-scoped source-artifact store belongs in this janitor.
    vault_source_artifact_dir = os.getenv("VAULT_SOURCE_ARTIFACT_DIR", "").strip()
    if vault_source_artifact_dir:
        candidates.append(Path(vault_source_artifact_dir).expanduser())

    storages: list[LocalArtifactStorage] = []
    seen: set[str] = set()
    for candidate in candidates:
        storage = LocalArtifactStorage(base_dir=str(candidate))
        key = str(storage.base_dir)
        if key not in seen:
            seen.add(key)
            storages.append(storage)
    return storages


def run_retention_sweep() -> dict[str, int]:
    uploads = cleanup_due_uploads()
    artifacts = sum(storage.cleanup_expired() for storage in _artifact_storages())
    team = purge_expired_team_attachments(
        limit=int(os.getenv("TEAM_ATTACHMENT_RETENTION_BATCH_SIZE", "500"))
    )
    return {
        "uploads_deleted": uploads,
        "artifacts_deleted": artifacts,
        "team_attachments_deleted": int(team.get("deleted") or 0),
    }


def _retention_loop() -> None:
    while not _STOP_EVENT.is_set():
        try:
            result = run_retention_sweep()
            if any(result.values()):
                logger.info(
                    "Storage retention sweep completed: uploads=%s artifacts=%s team_attachments=%s",
                    result["uploads_deleted"],
                    result["artifacts_deleted"],
                    result["team_attachments_deleted"],
                )
        except Exception:
            logger.exception("Storage retention sweep failed.")
        _STOP_EVENT.wait(_INTERVAL_SECONDS)


def start_storage_retention_services() -> None:
    global _THREAD
    with _THREAD_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _THREAD = threading.Thread(
            target=_retention_loop,
            name="redocx-storage-retention",
            daemon=True,
        )
        _THREAD.start()


def stop_storage_retention_services() -> None:
    global _THREAD
    with _THREAD_LOCK:
        thread = _THREAD
        if thread is None:
            return
        _STOP_EVENT.set()
    thread.join(timeout=5)
    with _THREAD_LOCK:
        _THREAD = None


__all__ = [
    "run_retention_sweep",
    "start_storage_retention_services",
    "stop_storage_retention_services",
]
