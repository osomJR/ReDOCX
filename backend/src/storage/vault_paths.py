"""Shared filesystem boundary for every supplied runtime-file janitor.

New Vault data lives in PostgreSQL. Old encrypted Vault directories must still
be protected until an operator explicitly retires their verified backup.
"""
from __future__ import annotations

import os
from pathlib import Path

from .vault_errors import VaultStorageUnavailable


def protected_vault_roots(base_dir: Path | None = None) -> tuple[Path, ...]:
    base = (base_dir or Path.cwd()).expanduser().resolve()
    values = ["vault_data", os.getenv("VAULT_STORAGE_DIR", "vault_data")]
    legacy = os.getenv("VAULT_LEGACY_STORAGE_DIR", "").strip()
    if legacy:
        values.append(legacy)
    roots = []
    for value in values:
        path = Path(value).expanduser()
        roots.append((path if path.is_absolute() else base / path).resolve())
    return tuple(dict.fromkeys(roots))


def is_vault_storage_path(path: str | Path, *, base_dir: Path | None = None) -> bool:
    """Resolve symlinks before deciding whether a destructive action is safe."""
    try:
        candidate = Path(path).expanduser().resolve()
        return any(candidate == root or root in candidate.parents
                   for root in protected_vault_roots(base_dir))
    except (OSError, RuntimeError):
        # A path that cannot be resolved must never be eligible for deletion.
        return True


def require_temporary_vault_path(path: str | Path) -> None:
    if is_vault_storage_path(path):
        raise VaultStorageUnavailable("Temporary uploads must be outside protected Vault storage.")
