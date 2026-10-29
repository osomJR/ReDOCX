"""Read-only inventory of the previous SQLite Vault for an explicit import."""
from __future__ import annotations

from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from .vault_errors import VaultMigrationRequired


def legacy_database_path(base_dir: Path, database_name: str) -> Path:
    if not database_name or Path(database_name).name != database_name or "\\" in database_name:
        raise VaultMigrationRequired("The legacy Vault database name must be a basename.")
    return base_dir / database_name


def open_legacy_database(path: Path) -> sqlite3.Connection:
    # mode=ro is essential: sqlite3.connect(path) would silently create a lost index.
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def legacy_manifest(base_dir: Path, database_name: str, key_check: str) -> tuple[str, int] | None:
    path = legacy_database_path(base_dir, database_name)
    objects = base_dir / "objects"
    if not path.is_file():
        if objects.exists() and next(objects.rglob("*.vault"), None) is not None:
            raise VaultMigrationRequired("Legacy Vault objects exist but their index is missing. Restore the index before import.")
        return None
    try:
        with closing(open_legacy_database(path)) as connection:
            stored = connection.execute(
                "SELECT value FROM vault_system_metadata WHERE key = 'master_key_check_v1'"
            ).fetchone()
            if stored is None or not hmac.compare_digest(str(stored[0]), key_check):
                raise VaultMigrationRequired("The original legacy Vault encryption key is required.")
            digest = hashlib.sha256()
            digest.update(key_check.encode("ascii"))
            count = 0
            for row in connection.execute("SELECT * FROM vault_items ORDER BY item_id"):
                record = dict(row)
                record["metadata_blob"] = bytes(record["metadata_blob"]).hex()
                digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                digest.update(b"\n")
                count += 1
            if count == 0 and objects.exists() and next(objects.rglob("*.vault"), None) is not None:
                raise VaultMigrationRequired("The legacy index is empty while encrypted objects remain. Restore a consistent backup.")
            return digest.hexdigest(), count
    except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
        raise VaultMigrationRequired("The legacy Vault index could not be verified. Restore a consistent backup.") from exc
