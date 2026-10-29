"""Explicit, resumable import of the old encrypted SQLite Vault.

Run from the repository root with the original Vault master key and DATABASE_URL
already in the process environment. Dry-run is the default. The source is never
modified, and an interrupted import can safely be run again.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
import shutil
from tempfile import SpooledTemporaryFile
from pathlib import Path

from backend.src.processing.vault.vault import (
    VaultConfig, VaultEngine, _MASTER_KEY_CHECK_CONTEXT, _load_master_key,
)
from backend.src.storage.vault_errors import VaultIntegrityError
from backend.src.storage.vault_legacy import legacy_database_path, legacy_manifest, open_legacy_database


class _DiscardPlaintext:
    def write(self, value: bytes) -> int:
        return len(value)


def import_legacy_vault(*, source: Path, database_name: str, apply: bool,
                        master_key: bytes | None = None, repository=None) -> dict:
    config = VaultConfig(base_dir=str(source), database_name=database_name)
    key = master_key if master_key is not None else _load_master_key(config.master_key_env)
    key_check = hmac.new(key, _MASTER_KEY_CHECK_CONTEXT, hashlib.sha256).hexdigest()
    manifest = legacy_manifest(source, database_name, key_check)
    if manifest is None:
        raise VaultIntegrityError("No verifiable legacy Vault index was found.")
    fingerprint, total = manifest
    if apply:
        verifier = VaultEngine(config=config, master_key=key, repository=repository, allow_legacy_import=True)
        if verifier.repository.import_completed(fingerprint):
            # Never resurrect items intentionally deleted AFTER a completed import.
            return {"mode": "apply", "already_completed": True, "source_items": total, "imported": 0}
    else:
        # Offline cryptographic validation needs no database connection or writes.
        verifier = VaultEngine.__new__(VaultEngine)
        verifier.config = config
        verifier.base_dir = source.expanduser().resolve()
        verifier.objects_dir = verifier.base_dir / "objects"
        verifier._master_key = key

    imported = 0
    verified = 0
    with closing(open_legacy_database(legacy_database_path(source, database_name))) as connection:
        connection.execute("BEGIN")
        for raw in connection.execute("SELECT * FROM vault_items ORDER BY item_id"):
            row = dict(raw)
            metadata = verifier._verified_metadata(row, owner_key=row["owner_key"])
            content_path = verifier._resolve_content_path(row["content_relpath"])
            if not content_path.is_file():
                raise VaultIntegrityError("A legacy item is missing its encrypted content. Restore a consistent source backup.")
            with content_path.open("rb") as source_file, SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as encrypted:
                # Verify and persist one private ciphertext snapshot, never a mutable source path.
                shutil.copyfileobj(source_file, encrypted, length=1024 * 1024)
                encrypted.seek(0)
                size, checksum = verifier._decrypt_content_with_owner_key(
                    encrypted, _DiscardPlaintext(), owner_key=row["owner_key"], item_id=row["item_id"])
                verifier._verify_decrypted_content(metadata, size, checksum)
                verified += 1
                if apply:
                    encrypted.seek(0)
                    imported += int(verifier.repository.store(row, encrypted, allow_identical=True))
    if legacy_manifest(source, database_name, key_check) != manifest:
        raise VaultIntegrityError("The source changed during import. Stop legacy writes and rerun against a consistent backup.")
    if apply:
        verifier.repository.finish_import(fingerprint, total)
    return {"mode": "apply" if apply else "dry_run", "source_items": total,
            "verified": verified, "imported": imported, "already_completed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--database-name", default="vault.sqlite3")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = import_legacy_vault(source=args.source, database_name=args.database_name, apply=args.apply)
    except Exception:
        # Customer names, filesystem paths and encryption keys must not enter logs.
        print(json.dumps({"success": False, "error": "Legacy Vault verification/import failed; preserve the source and restore a consistent backup before retrying."}))
        raise SystemExit(1)
    print(json.dumps({"success": True, **report}))


if __name__ == "__main__":
    main()
