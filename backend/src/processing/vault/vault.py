from __future__ import annotations

"""Owner-scoped encrypted ReDOCX Vault processing engine.

The engine implements the existing Analyzer VaultEngine protocol without changing
schema.py, validation.py, extraction.py, analyzer.py, or workflow_router.py.

Security invariants:
- authenticated ownership is resolved server-side and never accepted from payloads
- all Vault content is encrypted at rest with AES-256-GCM
- sensitive item metadata is also encrypted at rest
- each item key is independently derived from the configured master key
- item and metadata authentication bind owner + item identity through AAD
- cross-account item references are indistinguishable from missing items
- inline text is persisted byte-for-byte exactly after the supplied validator
- persisted plaintext .txt uploads are validated with the same exact-text policy
- pagination cursors are owner-bound authenticated ciphertext
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
from typing import BinaryIO, Callable, Mapping, Optional, Protocol, Union
from urllib.parse import quote
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

try:
    from backend.src.inline_text_security import VAULT_INLINE_TEXT_POLICY
    from backend.src.schema import (
        AnalyzerRequest,
        FeatureType,
        VaultDeleteResult,
        VaultFilePayload,
        VaultItemKind,
        VaultItemMetadata,
        VaultItemReferencePayload,
        VaultItemResult,
        VaultListResult,
        VaultOperation,
        VaultQueryPayload,
        VaultRequest,
        VaultTextPayload,
    )
    from backend.src.storage.artifacts import (
        LocalArtifactStorage,
        current_artifact_owner,
        get_artifact_owner,
        is_artifact_expired,
    )
    from backend.src.validation import (
        build_vault_delete_result,
        build_vault_item_metadata,
        build_vault_item_result,
        build_vault_list_result,
    )
except ImportError:  # pragma: no cover - package-relative local compatibility
    from ...inline_text_security import VAULT_INLINE_TEXT_POLICY
    from ...schema import (
        AnalyzerRequest,
        FeatureType,
        VaultDeleteResult,
        VaultFilePayload,
        VaultItemKind,
        VaultItemMetadata,
        VaultItemReferencePayload,
        VaultItemResult,
        VaultListResult,
        VaultOperation,
        VaultQueryPayload,
        VaultRequest,
        VaultTextPayload,
    )
    from ...storage.artifacts import (
        LocalArtifactStorage,
        current_artifact_owner,
        get_artifact_owner,
        is_artifact_expired,
    )
    from ...validation import (
        build_vault_delete_result,
        build_vault_item_metadata,
        build_vault_item_result,
        build_vault_list_result,
    )


VaultResult = Union[VaultItemResult, VaultListResult, VaultDeleteResult]
TextValidator = Callable[[str], str]
OwnerResolver = Callable[[], str]
DownloadUrlBuilder = Callable[[str, str], Optional[str]]
UploadIdResolver = Callable[[str, str], Union[str, Path]]

_MAGIC = b"RDXVAULT1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
_STREAM_CHUNK_BYTES = 1024 * 1024
_METADATA_VERSION = 1


class VaultFileSourceResolver(Protocol):
    def resolve(self, payload: VaultFilePayload, *, owner_user_id: str) -> Path:
        ...


@dataclass(frozen=True)
class VaultConfig:
    base_dir: str = os.getenv("VAULT_STORAGE_DIR", "vault_data")
    database_name: str = os.getenv("VAULT_DATABASE_NAME", "vault.sqlite3")
    master_key_env: str = os.getenv("VAULT_MASTER_KEY_ENV", "REDOCX_VAULT_MASTER_KEY")
    algorithm_version: Optional[str] = "vault-aes256gcm-v1"
    download_base_url: Optional[str] = os.getenv("VAULT_DOWNLOAD_BASE_URL") or None
    sqlite_busy_timeout_ms: int = max(
        1000,
        int(os.getenv("VAULT_SQLITE_BUSY_TIMEOUT_MS", "10000")),
    )


class ArtifactVaultFileSourceResolver:
    """Resolve Vault upload references from ReDOCX LocalArtifactStorage safely.

    ``storage_key`` is resolved only inside the configured artifact store and only
    when its recorded owner matches the authenticated Vault owner. ``upload_id``
    intentionally requires an injected resolver because the attached ReDOCX files
    define no upload-id repository or path convention.
    """

    def __init__(
        self,
        *,
        storage: Optional[LocalArtifactStorage] = None,
        upload_id_resolver: Optional[UploadIdResolver] = None,
    ) -> None:
        self.storage = storage or LocalArtifactStorage(
            base_dir=os.getenv("VAULT_SOURCE_ARTIFACT_DIR") or None
        )
        self.upload_id_resolver = upload_id_resolver

    def resolve(self, payload: VaultFilePayload, *, owner_user_id: str) -> Path:
        if payload.storage_key:
            metadata = get_artifact_owner(
                payload.storage_key,
                base_dir=str(self.storage.base_dir),
            )
            if metadata is None:
                raise FileNotFoundError("Vault source upload was not found.")
            if not hmac.compare_digest(metadata.owner_user_id, owner_user_id):
                raise FileNotFoundError("Vault source upload was not found.")
            if is_artifact_expired(metadata):
                raise FileNotFoundError("Vault source upload was not found.")

            path = self.storage.resolve_storage_key(payload.storage_key)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError("Vault source upload was not found.")
            return path

        if payload.upload_id and self.upload_id_resolver is not None:
            resolved = Path(
                self.upload_id_resolver(payload.upload_id, owner_user_id)
            ).expanduser().resolve()
            if not resolved.exists() or not resolved.is_file():
                raise FileNotFoundError("Vault source upload was not found.")
            return resolved

        if payload.upload_id:
            raise RuntimeError(
                "VaultFilePayload uses upload_id but no upload_id_resolver is configured."
            )
        raise ValueError("VaultFilePayload requires storage_key or upload_id.")


class VaultEngine:
    """Encrypted local Vault implementation of analyzer.VaultEngine."""

    def __init__(
        self,
        *,
        config: Optional[VaultConfig] = None,
        master_key: Optional[bytes] = None,
        owner_resolver: Optional[OwnerResolver] = None,
        file_source_resolver: Optional[VaultFileSourceResolver] = None,
        download_url_builder: Optional[DownloadUrlBuilder] = None,
    ) -> None:
        self.config = config or VaultConfig()
        self.base_dir = Path(self.config.base_dir).expanduser().resolve()
        self.objects_dir = self.base_dir / "objects"
        self.database_path = self.base_dir / self.config.database_name
        self._master_key = master_key or _load_master_key(self.config.master_key_env)
        if len(self._master_key) != 32:
            raise ValueError("Vault master key must contain exactly 32 bytes.")

        self.owner_resolver = owner_resolver or _owner_from_artifact_context
        self.file_source_resolver = file_source_resolver or ArtifactVaultFileSourceResolver()
        self.download_url_builder = download_url_builder or self._default_download_url

        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        _best_effort_chmod(self.base_dir, 0o700)
        _best_effort_chmod(self.objects_dir, 0o700)
        self._initialize_database()

    def process(
        self,
        request: AnalyzerRequest,
        *,
        text_validator: TextValidator,
    ) -> VaultResult:
        """Process one schema-validated Vault request."""
        if request.action != FeatureType.vault:
            raise ValueError("VaultEngine only handles vault requests.")
        if not isinstance(request.payload, VaultRequest):
            raise ValueError("vault requires VaultRequest payload.")
        if not callable(text_validator):
            raise TypeError("Vault text_validator must be callable.")

        owner_user_id = self._resolve_owner_user_id()
        operation = request.payload.operation

        if operation == VaultOperation.store:
            if isinstance(request.input, VaultTextPayload):
                return self._store_text(
                    request.input,
                    owner_user_id=owner_user_id,
                    text_validator=text_validator,
                )
            if isinstance(request.input, VaultFilePayload):
                return self._store_file(
                    request.input,
                    owner_user_id=owner_user_id,
                    text_validator=text_validator,
                )
            raise ValueError("Vault store requires VaultTextPayload or VaultFilePayload.")

        if operation == VaultOperation.retrieve:
            if not isinstance(request.input, VaultItemReferencePayload):
                raise ValueError("Vault retrieve requires VaultItemReferencePayload.")
            return self._retrieve_item(
                request.input.item_id,
                owner_user_id=owner_user_id,
                text_validator=text_validator,
            )

        if operation == VaultOperation.list:
            if not isinstance(request.input, VaultQueryPayload):
                raise ValueError("Vault list requires VaultQueryPayload.")
            return self._list_items(request.input, owner_user_id=owner_user_id)

        if operation == VaultOperation.delete:
            if not isinstance(request.input, VaultItemReferencePayload):
                raise ValueError("Vault delete requires VaultItemReferencePayload.")
            return self._delete_item(request.input.item_id, owner_user_id=owner_user_id)

        raise ValueError(f"Unsupported Vault operation: {operation!r}")

    def stream_file(
        self,
        item_id: str,
        destination: BinaryIO,
        *,
        owner_user_id: Optional[str] = None,
    ) -> VaultItemMetadata:
        """Decrypt a file Vault item directly to a response/file-like stream.

        This method is intended for the authenticated download endpoint represented
        by ``VaultItemResult.download_url``. It never materializes a plaintext copy
        inside the durable Vault storage directory.
        """
        if not hasattr(destination, "write"):
            raise TypeError("destination must be a writable binary stream.")
        owner = owner_user_id or self._resolve_owner_user_id()
        row = self._get_owned_row(item_id, owner_user_id=owner)
        metadata = self._metadata_from_row(row, owner_user_id=owner)
        if metadata.item_kind != VaultItemKind.file:
            raise TypeError("Requested Vault item is not a file item.")

        content_path = self._resolve_content_path(row["content_relpath"])
        actual_size, actual_checksum = self._decrypt_content_to_stream(
            content_path,
            destination,
            owner_user_id=owner,
            item_id=metadata.item_id,
        )
        self._verify_decrypted_content(metadata, actual_size, actual_checksum)
        return metadata

    def _store_text(
        self,
        payload: VaultTextPayload,
        *,
        owner_user_id: str,
        text_validator: TextValidator,
    ) -> VaultItemResult:
        validated = text_validator(payload.text)
        if validated != payload.text:
            raise ValueError("Vault text validator must not alter submitted text.")

        raw = payload.text.encode("utf-8", errors="strict")
        created_at_iso = _utc_now_iso()
        item_id = uuid.uuid4().hex
        checksum = hashlib.sha256(raw).hexdigest()

        metadata = build_vault_item_metadata(
            item_id=item_id,
            item_kind=VaultItemKind.text,
            filename=None,
            content_type="text/plain; charset=utf-8",
            file_size_bytes=len(raw),
            text_character_count=len(payload.text),
            checksum_sha256=checksum,
            client_encrypted=False,
            created_at_iso=created_at_iso,
            updated_at_iso=None,
        )
        metadata, _stored_size, _stored_checksum = self._persist_item(
            metadata,
            source=io.BytesIO(raw),
            owner_user_id=owner_user_id,
        )

        return build_vault_item_result(
            operation=VaultOperation.store,
            item=metadata,
            text=None,
            download_url=None,
            algorithm_version=self.config.algorithm_version,
        )

    def _store_file(
        self,
        payload: VaultFilePayload,
        *,
        owner_user_id: str,
        text_validator: TextValidator,
    ) -> VaultItemResult:
        source_path = self.file_source_resolver.resolve(
            payload,
            owner_user_id=owner_user_id,
        )
        if not source_path.exists() or not source_path.is_file():
            raise FileNotFoundError("Vault source upload was not found.")

        actual_size = source_path.stat().st_size
        if actual_size != payload.file_size_bytes:
            raise ValueError(
                "Vault source file size does not match VaultFilePayload.file_size_bytes."
            )

        if source_path.suffix.casefold() == ".txt" and not payload.client_encrypted:
            self._validate_plaintext_txt_file(source_path, text_validator=text_validator)

        created_at_iso = _utc_now_iso()
        item_id = uuid.uuid4().hex
        placeholder_metadata = build_vault_item_metadata(
            item_id=item_id,
            item_kind=VaultItemKind.file,
            filename=payload.filename,
            content_type=payload.content_type,
            file_size_bytes=payload.file_size_bytes,
            text_character_count=None,
            checksum_sha256=payload.checksum_sha256,
            client_encrypted=payload.client_encrypted,
            created_at_iso=created_at_iso,
            updated_at_iso=None,
        )

        metadata, _stored_size, _stored_checksum = self._persist_item(
            placeholder_metadata,
            source=source_path,
            owner_user_id=owner_user_id,
            expected_checksum=payload.checksum_sha256,
        )

        return build_vault_item_result(
            operation=VaultOperation.store,
            item=metadata,
            text=None,
            download_url=None,
            algorithm_version=self.config.algorithm_version,
        )

    def _retrieve_item(
        self,
        item_id: str,
        *,
        owner_user_id: str,
        text_validator: TextValidator,
    ) -> VaultItemResult:
        row = self._get_owned_row(item_id, owner_user_id=owner_user_id)
        metadata = self._metadata_from_row(row, owner_user_id=owner_user_id)
        content_path = self._resolve_content_path(row["content_relpath"])
        if not content_path.exists() or not content_path.is_file():
            raise FileNotFoundError("Vault item not found.")

        if metadata.item_kind == VaultItemKind.text:
            buffer = io.BytesIO()
            actual_size, actual_checksum = self._decrypt_content_to_stream(
                content_path,
                buffer,
                owner_user_id=owner_user_id,
                item_id=metadata.item_id,
            )
            self._verify_decrypted_content(metadata, actual_size, actual_checksum)
            try:
                text = buffer.getvalue().decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise ValueError("Stored Vault text is not valid UTF-8.") from exc
            validated = text_validator(text)
            if validated != text:
                raise ValueError("Vault text validator altered retrieved text.")
            if metadata.text_character_count != len(text):
                raise ValueError("Stored Vault text character count failed integrity validation.")

            return build_vault_item_result(
                operation=VaultOperation.retrieve,
                item=metadata,
                text=text,
                download_url=None,
                algorithm_version=self.config.algorithm_version,
            )

        return build_vault_item_result(
            operation=VaultOperation.retrieve,
            item=metadata,
            text=None,
            download_url=self.download_url_builder(metadata.item_id, owner_user_id),
            algorithm_version=self.config.algorithm_version,
        )

    def _list_items(
        self,
        query: VaultQueryPayload,
        *,
        owner_user_id: str,
    ) -> VaultListResult:
        owner_key = self._owner_key(owner_user_id)
        cursor_values = self._decode_cursor(query.cursor, owner_key=owner_key) if query.cursor else None

        sql = (
            "SELECT item_id, owner_key, item_kind, metadata_blob, content_relpath, "
            "created_at_iso, updated_at_iso FROM vault_items WHERE owner_key = ?"
        )
        params: list[object] = [owner_key]
        if cursor_values is not None:
            created_at_iso, item_id = cursor_values
            sql += (
                " AND (created_at_iso < ? OR "
                "(created_at_iso = ? AND item_id < ?))"
            )
            params.extend([created_at_iso, created_at_iso, item_id])
        sql += " ORDER BY created_at_iso DESC, item_id DESC"

        matches: list[tuple[sqlite3.Row, VaultItemMetadata]] = []
        with self._connect() as connection:
            for row in connection.execute(sql, params):
                metadata = self._metadata_from_row(row, owner_user_id=owner_user_id)
                if not _metadata_matches_query(metadata, query):
                    continue
                matches.append((row, metadata))
                if len(matches) > query.limit:
                    break

        next_cursor: Optional[str] = None
        if len(matches) > query.limit:
            last_returned_row = matches[query.limit - 1][0]
            next_cursor = self._encode_cursor(
                str(last_returned_row["created_at_iso"]),
                str(last_returned_row["item_id"]),
                owner_key=owner_key,
            )
            matches = matches[: query.limit]

        return build_vault_list_result(
            items=[metadata for _row, metadata in matches],
            next_cursor=next_cursor,
            algorithm_version=self.config.algorithm_version,
        )

    def _delete_item(self, item_id: str, *, owner_user_id: str) -> VaultDeleteResult:
        row = self._get_owned_row(item_id, owner_user_id=owner_user_id)
        content_path = self._resolve_content_path(row["content_relpath"])
        trash_dir = self.objects_dir / ".trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        _best_effort_chmod(trash_dir, 0o700)
        trash_path = trash_dir / f"{uuid.uuid4().hex}.vault"

        moved = False
        if content_path.exists():
            os.replace(content_path, trash_path)
            moved = True

        try:
            owner_key = self._owner_key(owner_user_id)
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM vault_items WHERE item_id = ? AND owner_key = ?",
                    (item_id, owner_key),
                )
                if cursor.rowcount != 1:
                    raise FileNotFoundError("Vault item not found.")
        except Exception:
            if moved and trash_path.exists() and not content_path.exists():
                os.replace(trash_path, content_path)
            raise

        if moved:
            trash_path.unlink(missing_ok=True)

        return build_vault_delete_result(
            item_id=item_id,
            algorithm_version=self.config.algorithm_version,
        )

    def _persist_item(
        self,
        metadata: VaultItemMetadata,
        *,
        source: Union[BinaryIO, Path],
        owner_user_id: str,
        expected_checksum: Optional[str] = None,
    ) -> tuple[VaultItemMetadata, int, str]:
        owner_key = self._owner_key(owner_user_id)
        relpath = str(Path(metadata.item_id[:2]) / f"{metadata.item_id}.vault")
        content_path = self._resolve_content_path(relpath)
        content_path.parent.mkdir(parents=True, exist_ok=True)
        _best_effort_chmod(content_path.parent, 0o700)
        temp_path = content_path.with_name(f".{content_path.name}.{uuid.uuid4().hex}.tmp")

        try:
            size, checksum = self._encrypt_source_to_path(
                source,
                temp_path,
                owner_user_id=owner_user_id,
                item_id=metadata.item_id,
            )
            if size != metadata.file_size_bytes:
                raise ValueError("Vault source size changed while it was being stored.")
            if expected_checksum and not secrets.compare_digest(
                checksum.casefold(), expected_checksum.casefold()
            ):
                raise ValueError("Vault source checksum does not match VaultFilePayload.checksum_sha256.")

            if metadata.checksum_sha256 is None:
                metadata = VaultItemMetadata.model_validate(
                    {**metadata.model_dump(mode="json"), "checksum_sha256": checksum}
                )

            os.replace(temp_path, content_path)
            _best_effort_chmod(content_path, 0o600)

            metadata_blob = self._encrypt_metadata(metadata, owner_user_id=owner_user_id)
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO vault_items (
                        item_id, owner_key, item_kind, metadata_blob, content_relpath,
                        created_at_iso, updated_at_iso
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata.item_id,
                        owner_key,
                        metadata.item_kind.value,
                        metadata_blob,
                        relpath.replace(os.sep, "/"),
                        metadata.created_at_iso,
                        metadata.updated_at_iso,
                    ),
                )
            return metadata, size, checksum
        except Exception:
            temp_path.unlink(missing_ok=True)
            content_path.unlink(missing_ok=True)
            raise

    def _validate_plaintext_txt_file(
        self,
        path: Path,
        *,
        text_validator: TextValidator,
    ) -> None:
        if path.stat().st_size > VAULT_INLINE_TEXT_POLICY.max_bytes:
            raise ValueError(
                "Vault .txt file exceeds the exact-text validation limit of "
                f"{VAULT_INLINE_TEXT_POLICY.max_bytes:,} UTF-8 bytes."
            )
        try:
            raw = path.read_bytes()
            decoded = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("Vault .txt uploads must contain valid UTF-8 text.") from exc
        validated = text_validator(decoded)
        if validated != decoded:
            raise ValueError("Vault text validator must not alter persisted .txt content.")

    def _get_owned_row(self, item_id: str, *, owner_user_id: str) -> sqlite3.Row:
        normalized_item_id = str(item_id).strip()
        if not normalized_item_id:
            raise ValueError("Vault item_id cannot be empty.")
        owner_key = self._owner_key(owner_user_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT item_id, owner_key, item_kind, metadata_blob, content_relpath,
                       created_at_iso, updated_at_iso
                FROM vault_items
                WHERE item_id = ? AND owner_key = ?
                """,
                (normalized_item_id, owner_key),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Vault item not found.")
        return row

    def _metadata_from_row(
        self,
        row: sqlite3.Row,
        *,
        owner_user_id: str,
    ) -> VaultItemMetadata:
        metadata = self._decrypt_metadata(
            bytes(row["metadata_blob"]),
            owner_user_id=owner_user_id,
            item_id=str(row["item_id"]),
        )
        if metadata.item_id != str(row["item_id"]):
            raise ValueError("Vault metadata item_id failed integrity validation.")
        if metadata.item_kind.value != str(row["item_kind"]):
            raise ValueError("Vault metadata item_kind failed integrity validation.")
        if metadata.created_at_iso != str(row["created_at_iso"]):
            raise ValueError("Vault metadata timestamp failed integrity validation.")
        return metadata

    def _encrypt_metadata(
        self,
        metadata: VaultItemMetadata,
        *,
        owner_user_id: str,
    ) -> bytes:
        owner_key = self._owner_key(owner_user_id)
        key = self._derive_item_key(owner_key, metadata.item_id, purpose=b"metadata")
        nonce = os.urandom(_NONCE_BYTES)
        payload = {
            "version": _METADATA_VERSION,
            "item": metadata.model_dump(mode="json"),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(
            nonce,
            raw,
            self._aad(owner_key, metadata.item_id, purpose=b"metadata"),
        )
        return nonce + ciphertext

    def _decrypt_metadata(
        self,
        blob: bytes,
        *,
        owner_user_id: str,
        item_id: str,
    ) -> VaultItemMetadata:
        if len(blob) <= _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("Vault metadata is truncated.")
        owner_key = self._owner_key(owner_user_id)
        key = self._derive_item_key(owner_key, item_id, purpose=b"metadata")
        nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
        try:
            raw = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                self._aad(owner_key, item_id, purpose=b"metadata"),
            )
        except InvalidTag as exc:
            raise ValueError("Vault metadata failed authentication.") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Vault metadata is invalid.") from exc
        if payload.get("version") != _METADATA_VERSION or not isinstance(payload.get("item"), Mapping):
            raise ValueError("Unsupported or invalid Vault metadata format.")
        return VaultItemMetadata.model_validate(payload["item"])

    def _encrypt_source_to_path(
        self,
        source: Union[BinaryIO, Path],
        output_path: Path,
        *,
        owner_user_id: str,
        item_id: str,
    ) -> tuple[int, str]:
        owner_key = self._owner_key(owner_user_id)
        key = self._derive_item_key(owner_key, item_id, purpose=b"content")
        nonce = os.urandom(_NONCE_BYTES)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(
            self._aad(owner_key, item_id, purpose=b"content")
        )
        digest = hashlib.sha256()
        total = 0

        close_source = False
        if isinstance(source, Path):
            handle = source.open("rb")
            close_source = True
        else:
            handle = source

        try:
            with output_path.open("wb") as destination:
                destination.write(_MAGIC)
                destination.write(nonce)
                while True:
                    chunk = handle.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise TypeError("Vault source stream must be binary.")
                    raw = bytes(chunk)
                    digest.update(raw)
                    total += len(raw)
                    destination.write(encryptor.update(raw))
                destination.write(encryptor.finalize())
                destination.write(encryptor.tag)
                destination.flush()
                os.fsync(destination.fileno())
            _best_effort_chmod(output_path, 0o600)
        finally:
            if close_source:
                handle.close()

        return total, digest.hexdigest()

    def _decrypt_content_to_stream(
        self,
        encrypted_path: Path,
        destination: BinaryIO,
        *,
        owner_user_id: str,
        item_id: str,
    ) -> tuple[int, str]:
        size = encrypted_path.stat().st_size
        header_size = len(_MAGIC) + _NONCE_BYTES
        minimum = header_size + _TAG_BYTES
        if size < minimum:
            raise ValueError("Vault encrypted content is truncated.")

        owner_key = self._owner_key(owner_user_id)
        key = self._derive_item_key(owner_key, item_id, purpose=b"content")
        digest = hashlib.sha256()
        total = 0

        with encrypted_path.open("rb") as source:
            magic = source.read(len(_MAGIC))
            if magic != _MAGIC:
                raise ValueError("Unsupported or invalid Vault content format.")
            nonce = source.read(_NONCE_BYTES)
            source.seek(-_TAG_BYTES, os.SEEK_END)
            tag = source.read(_TAG_BYTES)
            ciphertext_length = size - header_size - _TAG_BYTES
            source.seek(header_size)

            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(
                self._aad(owner_key, item_id, purpose=b"content")
            )

            remaining = ciphertext_length
            try:
                while remaining > 0:
                    chunk = source.read(min(_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("Vault encrypted content ended unexpectedly.")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    if plaintext:
                        destination.write(plaintext)
                        digest.update(plaintext)
                        total += len(plaintext)
                tail = decryptor.finalize()
            except InvalidTag as exc:
                raise ValueError("Vault content failed authentication.") from exc

            if tail:
                destination.write(tail)
                digest.update(tail)
                total += len(tail)

        return total, digest.hexdigest()

    @staticmethod
    def _verify_decrypted_content(
        metadata: VaultItemMetadata,
        actual_size: int,
        actual_checksum: str,
    ) -> None:
        if actual_size != metadata.file_size_bytes:
            raise ValueError("Vault item size failed integrity validation.")
        if metadata.checksum_sha256 and not secrets.compare_digest(
            metadata.checksum_sha256.casefold(), actual_checksum.casefold()
        ):
            raise ValueError("Vault item checksum failed integrity validation.")

    def _owner_key(self, owner_user_id: str) -> str:
        owner = str(owner_user_id).strip()
        if not owner:
            raise PermissionError("Authenticated Vault owner is required.")
        return hmac.new(
            self._master_key,
            b"redocx-vault-owner-v1\x00" + owner.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _derive_item_key(self, owner_key: str, item_id: str, *, purpose: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=bytes.fromhex(owner_key),
            info=b"redocx-vault-item-v1\x00" + purpose + b"\x00" + item_id.encode("utf-8"),
        ).derive(self._master_key)

    @staticmethod
    def _aad(owner_key: str, item_id: str, *, purpose: bytes) -> bytes:
        return (
            b"redocx-vault-v1\x00"
            + purpose
            + b"\x00"
            + owner_key.encode("ascii")
            + b"\x00"
            + item_id.encode("utf-8")
        )

    def _resolve_owner_user_id(self) -> str:
        owner = str(self.owner_resolver() or "").strip()
        if not owner:
            raise PermissionError("Authenticated Vault owner is required.")
        return owner

    def _resolve_content_path(self, relative_path: str) -> Path:
        candidate = Path(str(relative_path).replace("\\", "/"))
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ValueError("Invalid Vault content path.")
        resolved = (self.objects_dir / candidate).resolve()
        objects = self.objects_dir.resolve()
        if resolved != objects and objects not in resolved.parents:
            raise ValueError("Vault content path escaped the storage directory.")
        return resolved

    def _encode_cursor(self, created_at_iso: str, item_id: str, *, owner_key: str) -> str:
        key = self._derive_cursor_key(owner_key)
        nonce = os.urandom(_NONCE_BYTES)
        raw = json.dumps(
            {"created_at_iso": created_at_iso, "item_id": item_id},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        ciphertext = AESGCM(key).encrypt(
            nonce,
            raw,
            b"redocx-vault-cursor-v1\x00" + owner_key.encode("ascii"),
        )
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")

    def _decode_cursor(self, token: str, *, owner_key: str) -> tuple[str, str]:
        try:
            padding = "=" * (-len(token) % 4)
            blob = base64.urlsafe_b64decode((token + padding).encode("ascii"))
            if len(blob) <= _NONCE_BYTES + _TAG_BYTES:
                raise ValueError
            nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
            raw = AESGCM(self._derive_cursor_key(owner_key)).decrypt(
                nonce,
                ciphertext,
                b"redocx-vault-cursor-v1\x00" + owner_key.encode("ascii"),
            )
            payload = json.loads(raw.decode("utf-8"))
            created_at_iso = str(payload["created_at_iso"]).strip()
            item_id = str(payload["item_id"]).strip()
            if not created_at_iso or not item_id:
                raise ValueError
            return created_at_iso, item_id
        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError, InvalidTag) as exc:
            raise ValueError("Invalid or expired Vault cursor.") from exc

    def _derive_cursor_key(self, owner_key: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=bytes.fromhex(owner_key),
            info=b"redocx-vault-cursor-key-v1",
        ).derive(self._master_key)

    def _default_download_url(self, item_id: str, _owner_user_id: str) -> Optional[str]:
        if not self.config.download_base_url:
            return None
        return f"{self.config.download_base_url.rstrip('/')}/{quote(item_id, safe='')}"

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vault_items (
                    item_id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    item_kind TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    content_relpath TEXT NOT NULL,
                    created_at_iso TEXT NOT NULL,
                    updated_at_iso TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vault_owner_created
                ON vault_items(owner_key, created_at_iso DESC, item_id DESC)
                """
            )
        _best_effort_chmod(self.database_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.database_path),
            timeout=max(1.0, self.config.sqlite_busy_timeout_ms / 1000.0),
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={int(self.config.sqlite_busy_timeout_ms)}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def _owner_from_artifact_context() -> str:
    context = current_artifact_owner()
    if context is None or not str(context.owner_user_id).strip():
        raise PermissionError(
            "Vault requires authenticated server-side owner context. Wrap the request "
            "in artifact_owner_context(...) or inject VaultEngine(owner_resolver=...)."
        )
    return str(context.owner_user_id).strip()


def _load_master_key(env_name: str) -> bytes:
    raw = (os.getenv(env_name) or "").strip()
    if not raw and env_name != "VAULT_MASTER_KEY":
        raw = (os.getenv("VAULT_MASTER_KEY") or "").strip()
    if not raw:
        raise RuntimeError(
            f"{env_name} is not configured. Provide a cryptographically random 32-byte "
            "Vault master key encoded as 64 hex characters or base64."
        )

    if len(raw) == 64:
        try:
            decoded = bytes.fromhex(raw)
            if len(decoded) == 32:
                return decoded
        except ValueError:
            pass

    try:
        padding = "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode((raw + padding).encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError("Vault master key is not valid hex/base64 data.") from exc
    if len(decoded) != 32:
        raise ValueError("Vault master key must decode to exactly 32 bytes.")
    return decoded


def _metadata_matches_query(metadata: VaultItemMetadata, query: VaultQueryPayload) -> bool:
    if query.filename_contains is not None:
        if metadata.filename is None:
            return False
        if query.filename_contains.casefold() not in metadata.filename.casefold():
            return False
    if query.content_type is not None:
        if metadata.content_type.casefold() != query.content_type.casefold():
            return False
    return True


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_effort_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        pass


__all__ = [
    "VaultConfig",
    "VaultFileSourceResolver",
    "ArtifactVaultFileSourceResolver",
    "VaultEngine",
]
