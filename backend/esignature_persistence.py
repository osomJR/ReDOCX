from __future__ import annotations

"""
PostgreSQL persistence adapters for ReDOCX Sign.

These classes implement the repository protocols defined in
backend.src.esignature_service:

- EnvelopeRepository.save/get
- SigningTokenRepository.save

They intentionally persist only StoredSigningToken.token_hash. Raw signing tokens
must only exist in the email/signing link and must never be stored.
"""

from dataclasses import is_dataclass
from enum import Enum
import json
from typing import Any, Mapping

from pydantic import BaseModel

from backend.src.schema import (
    AnalyzerRequest,
    ArchiveFileResult,
    DocumentFileResult,
    ESignatureAuditEvent,
    ESignatureEnvelopeStatus,
    ESignatureField,
    ESignatureRecipientResult,
    ESignatureStepPreview,
    ESignatureWorkflow,
    PAdESSignatureInfo,
)
from backend.src.processing.esignature.envelope import (
    EnvelopeDocumentState,
    EnvelopeState,
)
from backend.src.processing.esignature.audit import build_hash_chain
from backend.src.processing.esignature.tokens import (
    StoredSigningToken,
    hash_token,
    utcnow_iso,
    validate_stored_token,
)


class ESignaturePersistenceError(RuntimeError):
    """Raised when persisted e-signature state cannot be read or written."""


def _value(value: Any) -> Any:
    """Return the database/string value for enums and enum-like objects."""
    return getattr(value, "value", value)


def _jsonable(value: Any) -> Any:
    """Convert dataclasses, Pydantic models, enums, tuples, and Paths to JSON."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {name: _jsonable(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _loads_jsonb(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    raise ESignaturePersistenceError("Stored e-signature state_json is invalid.")


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return _jsonable(value)


def _fetchone_value(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return next(iter(row.values()), None)
    return row[0] if len(row) else None


def _execute_json(cur: Any, sql: str, params: tuple[Any, ...]) -> None:
    # Cast JSON placeholders with ::jsonb in SQL. Passing a JSON string works
    # with psycopg2 and psycopg3 without requiring adapter-specific imports.
    cur.execute(sql, params)


def envelope_state_to_json(state: EnvelopeState) -> dict[str, Any]:
    return _jsonable(state)


def envelope_state_from_json(payload: Mapping[str, Any]) -> EnvelopeState:
    data = dict(payload)
    legacy_signed_pdf = (
        DocumentFileResult.model_validate(data["signed_pdf"])
        if data.get("signed_pdf") is not None
        else None
    )
    documents = tuple(
        EnvelopeDocumentState(
            document_id=str(item["document_id"]),
            filename=str(item["filename"]),
            source_sha256=str(item["source_sha256"]),
            current_sha256=str(item.get("current_sha256") or item["source_sha256"]),
            signed_pdf=(
                DocumentFileResult.model_validate(item["signed_pdf"])
                if item.get("signed_pdf") is not None
                else None
            ),
            pades_signature=(
                PAdESSignatureInfo.model_validate(item["pades_signature"])
                if item.get("pades_signature") is not None
                else None
            ),
        )
        for item in data.get("documents", [])
    )
    if not documents and data.get("source_document_sha256"):
        documents = (
            EnvelopeDocumentState(
                document_id="document_1",
                filename=getattr(legacy_signed_pdf, "filename", "document.pdf"),
                source_sha256=str(data["source_document_sha256"]),
                current_sha256=str(data["source_document_sha256"]),
                signed_pdf=legacy_signed_pdf,
            ),
        )
    return EnvelopeState(
        envelope_id=str(data["envelope_id"]),
        workflow=ESignatureWorkflow(_value(data["workflow"])),
        status=ESignatureEnvelopeStatus(_value(data["status"])),
        recipients=tuple(
            ESignatureRecipientResult.model_validate(item)
            for item in data.get("recipients", [])
        ),
        fields=tuple(
            ESignatureField.model_validate(item)
            for item in data.get("fields", [])
        ),
        created_at_iso=str(data["created_at_iso"]),
        updated_at_iso=str(data["updated_at_iso"]),
        expires_at_iso=data.get("expires_at_iso"),
        audit_events=tuple(
            ESignatureAuditEvent.model_validate(item)
            for item in data.get("audit_events", [])
        ),
        previews=tuple(
            ESignatureStepPreview.model_validate(item)
            for item in data.get("previews", [])
        ),
        documents=documents,
        signed_bundle=(
            ArchiveFileResult.model_validate(data["signed_bundle"])
            if data.get("signed_bundle") is not None
            else None
        ),
        signed_pdf=legacy_signed_pdf,
        audit_certificate=(
            DocumentFileResult.model_validate(data["audit_certificate"])
            if data.get("audit_certificate") is not None
            else None
        ),
        source_document_sha256=data.get("source_document_sha256"),
        owner_email=data.get("owner_email"),
        owner_user_id=data.get("owner_user_id"),
        owner_organization_id=data.get("owner_organization_id"),
        source_request=(
            AnalyzerRequest.model_validate(data["source_request"])
            if data.get("source_request") is not None
            else None
        ),
    )


class PostgresEnvelopeRepository:
    """
    Envelope repository using an existing psycopg connection.

    The caller owns transaction boundaries. In route_v1.py this repository is
    used inside `with get_db() as conn:` so token inserts and envelope inserts
    commit/rollback together.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def save(self, state: EnvelopeState) -> None:
        state_json = envelope_state_to_json(state)
        signed_pdf_storage_key = (
            state.signed_pdf.storage_key
            if state.signed_pdf is not None
            else None
        )
        certificate_storage_key = (
            state.audit_certificate.storage_key
            if state.audit_certificate is not None
            else None
        )

        with self.conn.cursor() as cur:
            _execute_json(
                cur,
                """
                INSERT INTO esignature_envelopes (
                    envelope_id,
                    owner_email,
                    workflow,
                    status,
                    source_document_sha256,
                    signed_pdf_storage_key,
                    audit_certificate_storage_key,
                    state_json,
                    created_at_iso,
                    updated_at_iso,
                    expires_at_iso,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, NOW())
                ON CONFLICT (envelope_id) DO UPDATE SET
                    owner_email = EXCLUDED.owner_email,
                    workflow = EXCLUDED.workflow,
                    status = EXCLUDED.status,
                    source_document_sha256 = EXCLUDED.source_document_sha256,
                    signed_pdf_storage_key = EXCLUDED.signed_pdf_storage_key,
                    audit_certificate_storage_key = EXCLUDED.audit_certificate_storage_key,
                    state_json = EXCLUDED.state_json,
                    created_at_iso = EXCLUDED.created_at_iso,
                    updated_at_iso = EXCLUDED.updated_at_iso,
                    expires_at_iso = EXCLUDED.expires_at_iso,
                    updated_at = NOW()
                """,
                (
                    state.envelope_id,
                    state.owner_email,
                    _value(state.workflow),
                    _value(state.status),
                    state.source_document_sha256,
                    signed_pdf_storage_key,
                    certificate_storage_key,
                    json.dumps(state_json),
                    state.created_at_iso,
                    state.updated_at_iso,
                    state.expires_at_iso,
                ),
            )

            self._replace_recipients(cur, state)
            self._replace_fields(cur, state)
            self._replace_audit_events(cur, state)
            self._replace_files(cur, state)

    def get(self, envelope_id: str) -> EnvelopeState:
        return self._get(envelope_id, for_update=False)

    def get_for_update(self, envelope_id: str) -> EnvelopeState:
        """Load and lock an envelope until the caller's transaction ends.

        Recipient signatures must be serialized even for parallel routing;
        otherwise two signers can both start from the same PDF version and the
        later commit silently discards the earlier signature.
        """
        return self._get(envelope_id, for_update=True)

    def _get(self, envelope_id: str, *, for_update: bool) -> EnvelopeState:
        lock_clause = " FOR UPDATE" if for_update else ""
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT state_json
                FROM esignature_envelopes
                WHERE envelope_id = %s
                {lock_clause}
                """,
                (envelope_id,),
            )
            raw = _fetchone_value(cur.fetchone())

        if raw is None:
            raise KeyError(f"E-signature envelope not found: {envelope_id}")

        return envelope_state_from_json(_loads_jsonb(raw))

    def _replace_recipients(self, cur: Any, state: EnvelopeState) -> None:
        cur.execute(
            "DELETE FROM esignature_recipients WHERE envelope_id = %s",
            (state.envelope_id,),
        )
        for recipient in state.recipients:
            payload = _model_dump(recipient)
            _execute_json(
                cur,
                """
                INSERT INTO esignature_recipients (
                    envelope_id,
                    signer_email,
                    signer_name,
                    role,
                    signing_order,
                    status,
                    recipient_json,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                """,
                (
                    state.envelope_id,
                    recipient.email.strip().lower(),
                    recipient.name,
                    _value(recipient.role),
                    recipient.signing_order,
                    _value(recipient.status),
                    json.dumps(payload),
                ),
            )

    def _replace_fields(self, cur: Any, state: EnvelopeState) -> None:
        cur.execute(
            "DELETE FROM esignature_fields WHERE envelope_id = %s",
            (state.envelope_id,),
        )
        for field in state.fields:
            payload = _model_dump(field)
            _execute_json(
                cur,
                """
                INSERT INTO esignature_fields (
                    envelope_id,
                    field_id,
                    document_id,
                    assigned_to_email,
                    field_type,
                    page_number,
                    required,
                    field_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    state.envelope_id,
                    getattr(field, "field_id", None),
                    getattr(field, "document_id", None) or "document_1",
                    field.assigned_to_email.strip().lower(),
                    _value(field.field_type),
                    field.page_number,
                    bool(field.required),
                    json.dumps(payload),
                ),
            )

    def _replace_audit_events(self, cur: Any, state: EnvelopeState) -> None:
        cur.execute(
            "DELETE FROM esignature_audit_events WHERE envelope_id = %s",
            (state.envelope_id,),
        )
        for ledger_entry in build_hash_chain(state.audit_events):
            event = ledger_entry.event
            payload = _model_dump(event)
            _execute_json(
                cur,
                """
                INSERT INTO esignature_audit_events (
                    event_id,
                    envelope_id,
                    event_type,
                    actor_email,
                    ip_address,
                    user_agent,
                    document_id,
                    document_sha256,
                    previous_event_hash,
                    event_hash,
                    created_at_iso,
                    event_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO UPDATE SET
                    envelope_id = EXCLUDED.envelope_id,
                    event_type = EXCLUDED.event_type,
                    actor_email = EXCLUDED.actor_email,
                    ip_address = EXCLUDED.ip_address,
                    user_agent = EXCLUDED.user_agent,
                    document_id = EXCLUDED.document_id,
                    document_sha256 = EXCLUDED.document_sha256,
                    previous_event_hash = EXCLUDED.previous_event_hash,
                    event_hash = EXCLUDED.event_hash,
                    created_at_iso = EXCLUDED.created_at_iso,
                    event_json = EXCLUDED.event_json
                """,
                (
                    event.event_id,
                    state.envelope_id,
                    _value(event.event_type),
                    event.actor_email,
                    event.ip_address,
                    event.user_agent,
                    event.document_id,
                    event.document_sha256,
                    ledger_entry.previous_hash,
                    ledger_entry.event_hash,
                    event.created_at_iso,
                    json.dumps(payload),
                ),
            )

    def _replace_files(self, cur: Any, state: EnvelopeState) -> None:
        cur.execute(
            """
            DELETE FROM esignature_files
            WHERE envelope_id = %s AND file_role NOT LIKE 'source_pdf%%'
            """,
            (state.envelope_id,),
        )

        def insert_file(
            file_role: str,
            file_result: Any,
            *,
            document_id: str | None = None,
        ) -> None:
            if file_result is None:
                return
            payload = _model_dump(file_result)
            _execute_json(
                cur,
                """
                INSERT INTO esignature_files (
                    envelope_id,
                    file_role,
                    document_id,
                    filename,
                    storage_key,
                    download_url,
                    content_type,
                    file_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    state.envelope_id,
                    file_role,
                    document_id,
                    getattr(file_result, "filename", None),
                    getattr(file_result, "storage_key", None),
                    getattr(file_result, "download_url", None),
                    (
                        "application/zip"
                        if str(getattr(file_result, "output_format", "")) in {"zip", "DocumentFileOutputFormat.zip"}
                        else "application/pdf"
                    ),
                    json.dumps(payload),
                ),
            )

        for document in state.documents:
            insert_file(
                f"signed_pdf:{document.document_id}",
                document.signed_pdf,
                document_id=document.document_id,
            )
        insert_file(
            "signed_pdf",
            state.signed_pdf,
            document_id=(state.documents[0].document_id if state.documents else None),
        )
        insert_file("signed_bundle", state.signed_bundle)
        insert_file("audit_certificate", state.audit_certificate)
        for index, preview in enumerate(state.previews, start=1):
            preview_pdf = getattr(preview, "preview_pdf", None)
            insert_file(
                f"preview_{index}:{preview.document_id}",
                preview_pdf,
                document_id=preview.document_id,
            )

    def save_source_pdf(
        self,
        *,
        envelope_id: str,
        source_path: str,
        filename: str,
        file_size_mb: float | None = None,
        storage_key: str | None = None,
        download_url: str | None = None,
        content_type: str = "application/pdf",
        document_id: str = "document_1",
    ) -> None:
        """
        Persist the original uploaded PDF used to create the envelope.

        Recipient signing needs this record when no previous signer has produced
        a signed_pdf yet. Store the backend-readable path in storage_key.
        """
        payload = {
            "document_id": document_id,
            "filename": filename,
            "storage_key": storage_key or source_path,
            "source_path": source_path,
            "download_url": download_url,
            "content_type": content_type,
            "file_size_mb": file_size_mb,
        }
        with self.conn.cursor() as cur:
            file_role = f"source_pdf:{document_id}"
            _execute_json(
                cur,
                """
                DELETE FROM esignature_files
                WHERE envelope_id = %s AND file_role = %s
                """,
                (envelope_id, file_role),
            )
            _execute_json(
                cur,
                """
                INSERT INTO esignature_files (
                    envelope_id,
                    file_role,
                    document_id,
                    filename,
                    storage_key,
                    download_url,
                    content_type,
                    file_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    envelope_id,
                    file_role,
                    document_id,
                    filename,
                    storage_key or source_path,
                    download_url,
                    content_type,
                    json.dumps(payload),
                ),
            )

    def get_source_pdfs(self, envelope_id: str) -> list[dict[str, Any]]:
        """Return all source PDFs in stable envelope order."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT file_role, filename, storage_key, download_url, content_type, file_json
                FROM esignature_files
                WHERE envelope_id = %s AND file_role LIKE 'source_pdf%%'
                ORDER BY id ASC
                """,
                (envelope_id,),
            )
            rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            if isinstance(row, Mapping):
                file_role = row.get("file_role")
                filename = row.get("filename")
                storage_key = row.get("storage_key")
                download_url = row.get("download_url")
                content_type = row.get("content_type")
                file_json = row.get("file_json")
            else:
                file_role, filename, storage_key, download_url, content_type, file_json = row
            item = _loads_jsonb(file_json) if file_json is not None else {}
            item.update(
                {
                    "document_id": item.get("document_id")
                    or str(file_role or "source_pdf:document_1").partition(":")[2]
                    or "document_1",
                    "filename": filename or item.get("filename"),
                    "storage_key": storage_key or item.get("storage_key"),
                    "download_url": download_url or item.get("download_url"),
                    "content_type": content_type or item.get("content_type") or "application/pdf",
                }
            )
            results.append(item)
        if not results:
            raise KeyError(f"Source PDF not found for e-signature envelope: {envelope_id}")
        return results

    def get_source_pdf(
        self,
        envelope_id: str,
        document_id: str = "document_1",
    ) -> dict[str, Any]:
        """Return the persisted source PDF record for an envelope."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT filename, storage_key, download_url, content_type, file_json
                FROM esignature_files
                WHERE envelope_id = %s
                  AND file_role IN (%s, 'source_pdf')
                ORDER BY id DESC
                LIMIT 1
                """,
                (envelope_id, f"source_pdf:{document_id}"),
            )
            row = cur.fetchone()

        if row is None:
            raise KeyError(f"Source PDF not found for e-signature envelope: {envelope_id}")

        if isinstance(row, Mapping):
            filename = row.get("filename")
            storage_key = row.get("storage_key")
            download_url = row.get("download_url")
            content_type = row.get("content_type")
            file_json = row.get("file_json")
        else:
            filename, storage_key, download_url, content_type, file_json = row

        payload = _loads_jsonb(file_json) if file_json is not None else {}
        payload.update(
            {
                "document_id": payload.get("document_id") or document_id,
                "filename": filename or payload.get("filename"),
                "storage_key": storage_key or payload.get("storage_key"),
                "download_url": download_url or payload.get("download_url"),
                "content_type": content_type or payload.get("content_type") or "application/pdf",
            }
        )
        return payload


class PostgresSigningTokenRepository:
    """
    Signing-token repository using an existing psycopg connection.

    Stores only token_hash. The raw token is never written to the database.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def save(self, token: StoredSigningToken) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO esignature_tokens (
                    token_id,
                    envelope_id,
                    signer_email,
                    token_hash,
                    expires_at_iso,
                    used_at_iso,
                    revoked_at_iso
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (token_id) DO UPDATE SET
                    envelope_id = EXCLUDED.envelope_id,
                    signer_email = EXCLUDED.signer_email,
                    token_hash = EXCLUDED.token_hash,
                    expires_at_iso = EXCLUDED.expires_at_iso,
                    used_at_iso = EXCLUDED.used_at_iso,
                    revoked_at_iso = EXCLUDED.revoked_at_iso
                """,
                (
                    token.token_id,
                    token.envelope_id,
                    token.signer_email.strip().lower(),
                    token.token_hash,
                    token.expires_at_iso,
                    token.used_at_iso,
                    token.revoked_at_iso,
                ),
            )

    def get_valid_for_raw_token(
        self,
        raw_token: str,
        *,
        secret: str | None = None,
        for_update: bool = False,
    ) -> StoredSigningToken:
        """
        Resolve and validate a recipient signing token.

        The caller supplies the raw token from the URL. This method hashes it,
        finds the persisted token_hash, then applies expiry/used/revoked checks.
        """
        token_hash = hash_token(raw_token, secret=secret)
        lock_clause = " FOR UPDATE" if for_update else ""
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    token_id,
                    envelope_id,
                    signer_email,
                    token_hash,
                    expires_at_iso,
                    used_at_iso,
                    revoked_at_iso
                FROM esignature_tokens
                WHERE token_hash = %s
                LIMIT 1
                {lock_clause}
                """,
                (token_hash,),
            )
            row = cur.fetchone()

        if row is None:
            raise KeyError("Signing token was not found.")

        token = self._stored_token_from_row(row)
        validate_stored_token(raw_token=raw_token, stored=token, secret=secret)
        return token

    def mark_used(self, token_id: str) -> None:
        used_at = utcnow_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE esignature_tokens
                SET used_at_iso = %s
                WHERE token_id = %s AND used_at_iso IS NULL
                """,
                (used_at, token_id),
            )
            rowcount = getattr(cur, "rowcount", None)

        if rowcount == 0:
            raise ValueError("Signing token has already been used or does not exist.")

    def revoke_active_for_signer(self, *, envelope_id: str, signer_email: str) -> None:
        """Revoke previously issued, unused links before issuing a replacement."""
        revoked_at = utcnow_iso()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE esignature_tokens
                SET revoked_at_iso = %s
                WHERE envelope_id = %s
                  AND signer_email = %s
                  AND used_at_iso IS NULL
                  AND revoked_at_iso IS NULL
                """,
                (revoked_at, envelope_id, signer_email.strip().lower()),
            )

    @staticmethod
    def _stored_token_from_row(row: Any) -> StoredSigningToken:
        if isinstance(row, Mapping):
            return StoredSigningToken(
                token_id=str(row["token_id"]),
                envelope_id=str(row["envelope_id"]),
                signer_email=str(row["signer_email"]),
                token_hash=str(row["token_hash"]),
                expires_at_iso=str(row["expires_at_iso"]),
                used_at_iso=row.get("used_at_iso"),
                revoked_at_iso=row.get("revoked_at_iso"),
            )

        return StoredSigningToken(
            token_id=str(row[0]),
            envelope_id=str(row[1]),
            signer_email=str(row[2]),
            token_hash=str(row[3]),
            expires_at_iso=str(row[4]),
            used_at_iso=row[5],
            revoked_at_iso=row[6],
        )


__all__ = [
    "ESignaturePersistenceError",
    "PostgresEnvelopeRepository",
    "PostgresSigningTokenRepository",
    "envelope_state_from_json",
    "envelope_state_to_json",
]
