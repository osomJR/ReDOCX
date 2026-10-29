"""Atomic, chunked Vault persistence using ReDOCX's existing PostgreSQL pool."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import logging
from typing import BinaryIO, Callable, Iterator
import uuid

from psycopg.rows import dict_row, tuple_row

from .vault_errors import VaultIntegrityError, VaultStorageUnavailable

logger = logging.getLogger(__name__)
CHUNK_BYTES = 1024 * 1024
_TABLES = ("redocx_vault_state", "redocx_vault_items",
           "redocx_vault_content_chunks", "redocx_vault_imports")
_COLUMNS = "item_id, owner_key, item_kind, metadata_blob, created_at_iso, updated_at_iso"


def _default_connection():
    # Reuse the established DATABASE_URL/pool; never open a second infrastructure.
    from backend.database import get_db
    return get_db()


class PostgresVaultRepository:
    def __init__(self, *, key_check: str, expected_store_id: str | None,
                 connection_factory: Callable | None = None):
        self.connection_factory = connection_factory or _default_connection
        self.key_check = key_check
        self.expected_store_id = expected_store_id
        if expected_store_id:
            try:
                self.expected_store_id = str(uuid.UUID(expected_store_id))
            except ValueError as exc:
                raise VaultStorageUnavailable("Vault store identity is invalid.") from exc

    @contextmanager
    def _session(self, *, read_only: bool = False) -> Iterator:
        try:
            with self.connection_factory() as connection:
                if read_only:
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                else:
                    connection.execute("SET LOCAL synchronous_commit = on")
                self._assert_state(connection)
                yield connection
        except (VaultStorageUnavailable, VaultIntegrityError, FileNotFoundError):
            raise
        except Exception as exc:
            # Do not log SQL parameters, filenames, ciphertext, keys, or DSNs.
            logger.error("Vault database operation failed: %s", type(exc).__name__)
            raise VaultStorageUnavailable("Vault storage is temporarily unavailable.") from exc

    def _assert_state(self, connection, *, initialize_key: bool = False) -> None:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(%s) "
                "AND c.relpersistence = 'p' AND c.relkind = 'r'", (list(_TABLES),))
            if cursor.fetchone()["count"] != len(_TABLES):
                raise VaultStorageUnavailable("Vault durable schema is missing or not persistent.")
            cursor.execute(
                "SELECT schema_version, store_id, master_key_check "
                "FROM public.redocx_vault_state WHERE singleton = 1"
                + (" FOR UPDATE" if initialize_key else ""))
            state = cursor.fetchone()
            if state is None or state["schema_version"] != 1:
                raise VaultStorageUnavailable("Vault storage identity is missing or unsupported.")
            if self.expected_store_id and str(state["store_id"]) != self.expected_store_id:
                raise VaultStorageUnavailable("Vault storage identity does not match the configured store.")
            if state["master_key_check"] is None and initialize_key:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM public.redocx_vault_items) AS populated")
                if cursor.fetchone()["populated"]:
                    raise VaultStorageUnavailable("Vault key identity is missing while saved items exist.")
                cursor.execute(
                    "UPDATE public.redocx_vault_state SET master_key_check = %s WHERE singleton = 1",
                    (self.key_check,))
                state["master_key_check"] = self.key_check
            if not state["master_key_check"] or not hmac.compare_digest(state["master_key_check"], self.key_check):
                raise VaultStorageUnavailable("Vault requires the original configured encryption key.")

    def initialize(self) -> None:
        """Validate the operator-provisioned schema; never create or replace it."""
        try:
            with self.connection_factory() as connection:
                connection.execute("SET LOCAL synchronous_commit = on")
                self._assert_state(connection, initialize_key=True)
        except VaultStorageUnavailable:
            raise
        except Exception as exc:
            raise VaultStorageUnavailable("Vault migration 023 and durable storage must be ready.") from exc

    def check_ready(self) -> None:
        with self._session(read_only=True):
            pass

    def get_owned(self, item_id: str, owner_key: str) -> dict:
        with self._session(read_only=True) as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(f"SELECT {_COLUMNS} FROM public.redocx_vault_items "
                           "WHERE item_id = %s AND owner_key = %s", (item_id, owner_key))
            row = cursor.fetchone()
            if row is None:
                raise FileNotFoundError("Vault item not found.")
            return row

    def list_rows(self, owner_key: str, cursor_values: tuple[str, str] | None, limit: int) -> list[dict]:
        sql = f"SELECT {_COLUMNS} FROM public.redocx_vault_items WHERE owner_key = %s"
        params: list = [owner_key]
        if cursor_values:
            sql += " AND (created_at_iso, item_id) < (%s, %s)"
            params.extend(cursor_values)
        sql += " ORDER BY created_at_iso DESC, item_id DESC LIMIT %s"
        params.append(limit)
        with self._session(read_only=True) as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def store(self, row: dict, encrypted: BinaryIO, *, allow_identical: bool = False) -> bool:
        """Commit metadata and every ciphertext chunk together, or none of them."""
        digest = hashlib.sha256()
        size = 0
        encrypted.seek(0)
        while chunk := encrypted.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
        checksum = digest.hexdigest()
        count = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
        if size < 37:
            raise VaultIntegrityError("Vault encrypted content is truncated.")
        encrypted.seek(0)
        with self._session() as connection, connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                "INSERT INTO public.redocx_vault_items "
                "(item_id, owner_key, item_kind, metadata_blob, encrypted_size_bytes, "
                "encrypted_sha256, chunk_count, created_at_iso, updated_at_iso) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (item_id) DO NOTHING RETURNING item_id",
                (row["item_id"], row["owner_key"], row["item_kind"], row["metadata_blob"],
                 size, checksum, count, row["created_at_iso"], row.get("updated_at_iso")))
            if cursor.fetchone() is None:
                cursor.execute("SELECT owner_key, metadata_blob, encrypted_sha256 FROM public.redocx_vault_items "
                               "WHERE item_id = %s", (row["item_id"],))
                existing = cursor.fetchone()
                if allow_identical and existing and existing["owner_key"] == row["owner_key"] \
                        and bytes(existing["metadata_blob"]) == bytes(row["metadata_blob"]) \
                        and hmac.compare_digest(existing["encrypted_sha256"], checksum):
                    verified_digest = hashlib.sha256()
                    verified_size = 0
                    verified_count = 0
                    with connection.cursor(name="vault_import_" + uuid.uuid4().hex, row_factory=tuple_row) as existing_chunks:
                        existing_chunks.itersize = 8
                        existing_chunks.execute("SELECT chunk_index, encrypted_chunk FROM public.redocx_vault_content_chunks WHERE item_id = %s ORDER BY chunk_index", (row["item_id"],))
                        for index, raw in existing_chunks:
                            if index != verified_count:
                                raise VaultIntegrityError("Previously imported Vault content is incomplete.")
                            chunk = bytes(raw)
                            verified_digest.update(chunk)
                            verified_size += len(chunk)
                            verified_count += 1
                    if verified_size != size or verified_count != count or not hmac.compare_digest(verified_digest.hexdigest(), checksum):
                        raise VaultIntegrityError("Previously imported Vault content could not be verified.")
                    return False
                raise VaultIntegrityError("A different saved item already has this Vault identity.")
            written_digest = hashlib.sha256()
            written_size = 0
            for index in range(count):
                chunk = encrypted.read(CHUNK_BYTES)
                written_digest.update(chunk)
                written_size += len(chunk)
                cursor.execute("INSERT INTO public.redocx_vault_content_chunks "
                               "(item_id, chunk_index, encrypted_chunk) VALUES (%s,%s,%s)",
                               (row["item_id"], index, chunk))
            if written_size != size or encrypted.read(1) or not hmac.compare_digest(written_digest.hexdigest(), checksum):
                raise VaultIntegrityError("Vault ciphertext changed during the storage transaction.")
        return True

    def read_content(self, item_id: str, owner_key: str, destination: BinaryIO) -> dict:
        """Read the item and chunks from one snapshot even during deletion."""
        with self._session(read_only=True) as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(f"SELECT {_COLUMNS}, encrypted_size_bytes, encrypted_sha256, chunk_count "
                               "FROM public.redocx_vault_items WHERE item_id = %s AND owner_key = %s",
                               (item_id, owner_key))
                row = cursor.fetchone()
            if row is None:
                raise FileNotFoundError("Vault item not found.")
            size = 0
            count = 0
            digest = hashlib.sha256()
            with connection.cursor(name="vault_read_" + uuid.uuid4().hex, row_factory=tuple_row) as chunks:
                chunks.itersize = 8
                chunks.execute("SELECT chunk_index, encrypted_chunk FROM public.redocx_vault_content_chunks "
                               "WHERE item_id = %s ORDER BY chunk_index", (item_id,))
                for index, raw in chunks:
                    if index != count:
                        raise VaultIntegrityError("Vault content chunks are incomplete.")
                    chunk = bytes(raw)
                    destination.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                    count += 1
            if size != row["encrypted_size_bytes"] or count != row["chunk_count"] \
                    or not hmac.compare_digest(digest.hexdigest(), row["encrypted_sha256"]):
                raise VaultIntegrityError("Vault encrypted content failed verification.")
            destination.seek(0)
            return row

    def delete_owned(self, item_id: str, owner_key: str) -> None:
        with self._session() as connection:
            connection.execute("SELECT set_config('redocx.vault_delete_owner', %s, true)", (owner_key,))
            row = connection.execute("DELETE FROM public.redocx_vault_items WHERE item_id = %s AND owner_key = %s "
                                     "RETURNING item_id", (item_id, owner_key)).fetchone()
            if row is None:
                raise FileNotFoundError("Vault item not found.")
            # The foreign key deletes ciphertext inside this same transaction.

    def import_completed(self, fingerprint: str) -> bool:
        with self._session(read_only=True) as connection:
            return connection.execute("SELECT 1 FROM public.redocx_vault_imports WHERE source_fingerprint = %s",
                                      (fingerprint,)).fetchone() is not None

    def finish_import(self, fingerprint: str, item_count: int) -> None:
        with self._session() as connection:
            connection.execute("INSERT INTO public.redocx_vault_imports(source_fingerprint, item_count) "
                               "VALUES (%s,%s) ON CONFLICT (source_fingerprint) DO NOTHING",
                               (fingerprint, item_count))
