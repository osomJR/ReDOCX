from __future__ import annotations

"""Owner-scoped encrypted ReDOCX Vault processing engine.

The engine preserves the Analyzer VaultEngine boundary and v1 encrypted format.
Ciphertext and encrypted metadata commit atomically to the existing PostgreSQL database.

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

from contextlib import nullcontext
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
from tempfile import SpooledTemporaryFile
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


from backend.src.storage.vault_errors import VaultIntegrityError, VaultMigrationRequired, VaultStorageUnavailable
from backend.src.storage.vault_repository import PostgresVaultRepository
from backend.src.storage.vault_legacy import legacy_manifest

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
_MASTER_KEY_CHECK_NAME = "master_key_check_v1"
_MASTER_KEY_CHECK_CONTEXT = b"redocx-vault-master-key-check-v1"


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
    storage_backend: str = os.getenv("VAULT_STORAGE_BACKEND", "postgres").strip().lower()
    expected_store_id: Optional[str] = os.getenv("VAULT_EXPECTED_STORE_ID") or None
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
    """Owner-scoped encrypted Vault backed by the existing Neon/PostgreSQL database."""

    def __init__(
        self,
        *,
        config: Optional[VaultConfig] = None,
        master_key: Optional[bytes] = None,
        owner_resolver: Optional[OwnerResolver] = None,
        file_source_resolver: Optional[VaultFileSourceResolver] = None,
        download_url_builder: Optional[DownloadUrlBuilder] = None,
        repository: Optional[PostgresVaultRepository] = None,
        allow_legacy_import: bool = False,
    ) -> None:
        self.config = config or VaultConfig()
        self.base_dir = Path(self.config.base_dir).expanduser().resolve()
        self.objects_dir = self.base_dir / "objects"
        self.database_path = self.base_dir / self.config.database_name
        try:
            self._master_key = master_key if master_key is not None else _load_master_key(self.config.master_key_env)
        except (ValueError, RuntimeError) as exc:
            raise VaultStorageUnavailable("Vault requires its configured original encryption key.") from exc
        if len(self._master_key) != 32:
            raise VaultStorageUnavailable("Vault master key must contain exactly 32 bytes.")
        _validate_production_storage_configuration(self.config, config_was_explicit=config is not None)
        self.owner_resolver = owner_resolver or _owner_from_artifact_context
        self.file_source_resolver = file_source_resolver
        self.download_url_builder = download_url_builder or self._default_download_url
        self.key_check = hmac.new(self._master_key, _MASTER_KEY_CHECK_CONTEXT, hashlib.sha256).hexdigest()
        # Inspect legacy state before binding a key to a newly provisioned store.
        self._legacy_manifest = legacy_manifest(self.base_dir, self.config.database_name, self.key_check)
        self.repository = repository or PostgresVaultRepository(
            key_check=self.key_check, expected_store_id=self.config.expected_store_id)
        self.repository.initialize()
        if self._legacy_manifest and not allow_legacy_import:
            if not self.repository.import_completed(self._legacy_manifest[0]):
                raise VaultMigrationRequired("Existing local Vault items need the verified PostgreSQL import before cutover.")

    def check_ready(self) -> None:
        self.repository.check_ready()

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
        if not hasattr(destination, "write"):
            raise TypeError("destination must be a writable binary stream.")
        owner = owner_user_id or self._resolve_owner_user_id()
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as encrypted:
            row = self.repository.read_content(item_id, self._owner_key(owner), encrypted)
            metadata = self._metadata_from_row(row, owner_user_id=owner)
            if metadata.item_kind != VaultItemKind.file:
                raise TypeError("Requested Vault item is not a file item.")
            actual_size, actual_checksum = self._decrypt_content_to_stream(
                encrypted, destination, owner_user_id=owner, item_id=metadata.item_id)
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
        resolver = self.file_source_resolver or ArtifactVaultFileSourceResolver()
        source_path = resolver.resolve(
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
        if metadata.item_kind == VaultItemKind.text:
            with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as encrypted:
                content_row = self.repository.read_content(item_id, self._owner_key(owner_user_id), encrypted)
                # Use metadata from the same database snapshot as the ciphertext.
                metadata = self._metadata_from_row(content_row, owner_user_id=owner_user_id)
                buffer = io.BytesIO()
                actual_size, actual_checksum = self._decrypt_content_to_stream(
                    encrypted, buffer, owner_user_id=owner_user_id, item_id=metadata.item_id)
                self._verify_decrypted_content(metadata, actual_size, actual_checksum)
                try:
                    text = buffer.getvalue().decode("utf-8", errors="strict")
                    validated = text_validator(text)
                    if validated != text or metadata.text_character_count != len(text):
                        raise ValueError("Vault exact-text integrity check failed.")
                except (UnicodeDecodeError, ValueError) as exc:
                    raise VaultIntegrityError("Saved Vault text could not be verified.") from exc
            return build_vault_item_result(
                operation=VaultOperation.retrieve, item=metadata, text=text,
                download_url=None, algorithm_version=self.config.algorithm_version)
        return build_vault_item_result(
            operation=VaultOperation.retrieve, item=metadata, text=None,
            download_url=self.download_url_builder(metadata.item_id, owner_user_id),
            algorithm_version=self.config.algorithm_version)

    def _list_items(
        self,
        query: VaultQueryPayload,
        *,
        owner_user_id: str,
    ) -> VaultListResult:
        owner_key = self._owner_key(owner_user_id)
        cursor_values = self._decode_cursor(query.cursor, owner_key=owner_key) if query.cursor else None
        scan_limit = min(1000, max(query.limit + 1, query.limit * 5))
        rows = self.repository.list_rows(owner_key, cursor_values, scan_limit + 1)
        items = []
        unavailable = []
        examined = 0
        last_row = None
        for row in rows[:scan_limit]:
            examined += 1
            last_row = row
            try:
                metadata = self._metadata_from_row(row, owner_user_id=owner_user_id)
            except VaultIntegrityError:
                # Keep an explicit owner-scoped warning; never silently discard data.
                unavailable.append({"item_id": str(row["item_id"]), "reason": "integrity_error"})
                continue
            if _metadata_matches_query(metadata, query):
                items.append(metadata)
            if len(items) >= query.limit:
                break
        next_cursor = None
        if last_row is not None and examined < len(rows):
            next_cursor = self._encode_cursor(str(last_row["created_at_iso"]), str(last_row["item_id"]), owner_key=owner_key)
        return build_vault_list_result(
            items=items, unavailable_items=unavailable, next_cursor=next_cursor,
            algorithm_version=self.config.algorithm_version)

    def _delete_item(self, item_id: str, *, owner_user_id: str) -> VaultDeleteResult:
        # Metadata and ciphertext are deleted by one transaction and FK cascade.
        # Damaged metadata does not prevent the verified owner from deleting it.
        self.repository.delete_owned(str(item_id).strip(), self._owner_key(owner_user_id))
        return build_vault_delete_result(item_id=item_id, algorithm_version=self.config.algorithm_version)

    def _persist_item(
        self,
        metadata: VaultItemMetadata,
        *,
        source: Union[BinaryIO, Path],
        owner_user_id: str,
        expected_checksum: Optional[str] = None,
    ) -> tuple[VaultItemMetadata, int, str]:
        # Only ciphertext is spooled. No deploy-local file is a durable record.
        with SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as encrypted:
            size, checksum = self._encrypt_source_to_stream(
                source, encrypted, owner_user_id=owner_user_id, item_id=metadata.item_id)
            if size != metadata.file_size_bytes:
                raise ValueError("Vault source size changed while it was being stored.")
            if expected_checksum and not secrets.compare_digest(checksum.casefold(), expected_checksum.casefold()):
                raise ValueError("Vault source checksum does not match the upload.")
            if metadata.checksum_sha256 is None:
                metadata = VaultItemMetadata.model_validate(
                    {**metadata.model_dump(mode="json"), "checksum_sha256": checksum})
            row = {"item_id": metadata.item_id, "owner_key": self._owner_key(owner_user_id),
                   "item_kind": metadata.item_kind.value,
                   "metadata_blob": self._encrypt_metadata(metadata, owner_user_id=owner_user_id),
                   "created_at_iso": metadata.created_at_iso, "updated_at_iso": metadata.updated_at_iso}
            self.repository.store(row, encrypted)
        return metadata, size, checksum

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

    def _get_owned_row(self, item_id: str, *, owner_user_id: str) -> Mapping:
        normalized = str(item_id).strip()
        if not normalized:
            raise ValueError("Vault item_id cannot be empty.")
        return self.repository.get_owned(normalized, self._owner_key(owner_user_id))

    def _metadata_from_row(self, row: Mapping, *, owner_user_id: str) -> VaultItemMetadata:
        return self._verified_metadata(row, owner_key=self._owner_key(owner_user_id))

    def _verified_metadata(self, row: Mapping, *, owner_key: str) -> VaultItemMetadata:
        try:
            metadata = self._decrypt_metadata_for_owner_key(
                bytes(row["metadata_blob"]), owner_key=owner_key, item_id=str(row["item_id"]))
            if metadata.item_id != str(row["item_id"]) or metadata.item_kind.value != str(row["item_kind"]) \
                    or metadata.created_at_iso != str(row["created_at_iso"]):
                raise ValueError("Vault metadata identity check failed.")
            return metadata
        except (ValueError, TypeError, KeyError, InvalidTag) as exc:
            raise VaultIntegrityError("Saved Vault item metadata could not be verified.") from exc

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

    def _decrypt_metadata_for_owner_key(
        self,
        blob: bytes,
        *,
        owner_key: str,
        item_id: str,
    ) -> VaultItemMetadata:
        if len(blob) <= _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("Vault metadata is truncated.")
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
        if not isinstance(payload, Mapping) or payload.get("version") != _METADATA_VERSION or not isinstance(payload.get("item"), Mapping):
            raise ValueError("Unsupported or invalid Vault metadata format.")
        return VaultItemMetadata.model_validate(payload["item"])


    def _encrypt_source_to_stream(
        self, source: Union[BinaryIO, Path], destination: BinaryIO,
        *, owner_user_id: str, item_id: str,
    ) -> tuple[int, str]:
        owner_key = self._owner_key(owner_user_id)
        key = self._derive_item_key(owner_key, item_id, purpose=b"content")
        nonce = os.urandom(_NONCE_BYTES)
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(self._aad(owner_key, item_id, purpose=b"content"))
        digest = hashlib.sha256()
        total = 0
        context = source.open("rb") if isinstance(source, Path) else nullcontext(source)
        with context as handle:
            destination.write(_MAGIC)
            destination.write(nonce)
            while chunk := handle.read(_STREAM_CHUNK_BYTES):
                if not isinstance(chunk, (bytes, bytearray)):
                    raise TypeError("Vault source stream must be binary.")
                raw = bytes(chunk)
                digest.update(raw)
                total += len(raw)
                destination.write(encryptor.update(raw))
            destination.write(encryptor.finalize())
            destination.write(encryptor.tag)
            destination.flush()
            destination.seek(0)
        return total, digest.hexdigest()

    def _decrypt_content_to_stream(
        self, encrypted_source: Union[Path, BinaryIO], destination: BinaryIO,
        *, owner_user_id: str, item_id: str,
    ) -> tuple[int, str]:
        return self._decrypt_content_with_owner_key(
            encrypted_source, destination, owner_key=self._owner_key(owner_user_id), item_id=item_id)

    def _decrypt_content_with_owner_key(
        self, encrypted_source: Union[Path, BinaryIO], destination: BinaryIO,
        *, owner_key: str, item_id: str,
    ) -> tuple[int, str]:
        context = encrypted_source.open("rb") if isinstance(encrypted_source, Path) else nullcontext(encrypted_source)
        with context as source:
            source.seek(0, os.SEEK_END)
            size = source.tell()
            source.seek(0)
            header_size = len(_MAGIC) + _NONCE_BYTES
            if size < header_size + _TAG_BYTES:
                raise VaultIntegrityError("Vault encrypted content is truncated.")
            if source.read(len(_MAGIC)) != _MAGIC:
                raise VaultIntegrityError("Unsupported Vault encrypted content format.")
            nonce = source.read(_NONCE_BYTES)
            source.seek(-_TAG_BYTES, os.SEEK_END)
            tag = source.read(_TAG_BYTES)
            remaining = size - header_size - _TAG_BYTES
            source.seek(header_size)
            key = self._derive_item_key(owner_key, item_id, purpose=b"content")
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(self._aad(owner_key, item_id, purpose=b"content"))
            digest = hashlib.sha256()
            total = 0
            try:
                while remaining > 0:
                    chunk = source.read(min(_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise VaultIntegrityError("Vault encrypted content ended unexpectedly.")
                    remaining -= len(chunk)
                    plaintext = decryptor.update(chunk)
                    destination.write(plaintext)
                    digest.update(plaintext)
                    total += len(plaintext)
                tail = decryptor.finalize()
            except InvalidTag as exc:
                raise VaultIntegrityError("Saved Vault content failed authentication.") from exc
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
            raise VaultIntegrityError("Vault item size failed integrity validation.")
        if metadata.checksum_sha256 and not secrets.compare_digest(
            metadata.checksum_sha256.casefold(), actual_checksum.casefold()
        ):
            raise VaultIntegrityError("Vault item checksum failed integrity validation.")

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








def _is_production_environment() -> bool:
    environment = (
        os.getenv("APP_ENV", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
        or os.getenv("RAILWAY_ENVIRONMENT_NAME", "").strip()
    ).lower()
    return environment in {"production", "prod"}


def _validate_production_storage_configuration(
    config: VaultConfig, *, config_was_explicit: bool,
) -> None:
    if config.storage_backend != "postgres":
        raise VaultStorageUnavailable("Vault requires the durable PostgreSQL storage backend.")
    if Path(config.database_name).name != config.database_name or "\\" in config.database_name:
        raise VaultStorageUnavailable("The legacy Vault database name must be a basename.")
    if _is_production_environment() and not config.expected_store_id:
        raise VaultStorageUnavailable("Vault requires VAULT_EXPECTED_STORE_ID from migration 023 in production.")


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
