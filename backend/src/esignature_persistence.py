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
    DocumentFileResult,
    ESignatureAuditEvent,
    ESignatureEnvelopeStatus,
    ESignatureField,
    ESignatureRecipientResult,
    ESignatureStepPreview,
    ESignatureWorkflow,
)
from backend.src.processing.esignature.envelope import EnvelopeState
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
        signed_pdf=(
            DocumentFileResult.model_validate(data["signed_pdf"])
            if data.get("signed_pdf") is not None
            else None
        ),
        audit_certificate=(
            DocumentFileResult.model_validate(data["audit_certificate"])
            if data.get("audit_certificate") is not None
            else None
        ),
        source_document_sha256=data.get("source_document_sha256"),
        owner_email=data.get("owner_email"),
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
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT state_json
                FROM esignature_envelopes
                WHERE envelope_id = %s
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
                    assigned_to_email,
                    field_type,
                    page_number,
                    required,
                    field_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    state.envelope_id,
                    getattr(field, "field_id", None),
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
        for event in state.audit_events:
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
                    document_sha256,
                    created_at_iso,
                    event_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO UPDATE SET
                    envelope_id = EXCLUDED.envelope_id,
                    event_type = EXCLUDED.event_type,
                    actor_email = EXCLUDED.actor_email,
                    ip_address = EXCLUDED.ip_address,
                    user_agent = EXCLUDED.user_agent,
                    document_sha256 = EXCLUDED.document_sha256,
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
                    event.document_sha256,
                    event.created_at_iso,
                    json.dumps(payload),
                ),
            )

    def _replace_files(self, cur: Any, state: EnvelopeState) -> None:
        cur.execute(
            "DELETE FROM esignature_files WHERE envelope_id = %s",
            (state.envelope_id,),
        )

        def insert_file(file_role: str, file_result: Any) -> None:
            if file_result is None:
                return
            payload = _model_dump(file_result)
            _execute_json(
                cur,
                """
                INSERT INTO esignature_files (
                    envelope_id,
                    file_role,
                    filename,
                    storage_key,
                    download_url,
                    content_type,
                    file_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    state.envelope_id,
                    file_role,
                    getattr(file_result, "filename", None),
                    getattr(file_result, "storage_key", None),
                    getattr(file_result, "download_url", None),
                    "application/pdf",
                    json.dumps(payload),
                ),
            )

        insert_file("signed_pdf", state.signed_pdf)
        insert_file("audit_certificate", state.audit_certificate)
        for index, preview in enumerate(state.previews, start=1):
            preview_pdf = getattr(preview, "preview_pdf", None)
            insert_file(f"preview_{index}", preview_pdf)

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
    ) -> None:
        """
        Persist the original uploaded PDF used to create the envelope.

        Recipient signing needs this record when no previous signer has produced
        a signed_pdf yet. Store the backend-readable path in storage_key.
        """
        payload = {
            "filename": filename,
            "storage_key": storage_key or source_path,
            "download_url": download_url,
            "content_type": content_type,
            "file_size_mb": file_size_mb,
        }
        with self.conn.cursor() as cur:
            _execute_json(
                cur,
                """
                DELETE FROM esignature_files
                WHERE envelope_id = %s AND file_role = 'source_pdf'
                """,
                (envelope_id,),
            )
            _execute_json(
                cur,
                """
                INSERT INTO esignature_files (
                    envelope_id,
                    file_role,
                    filename,
                    storage_key,
                    download_url,
                    content_type,
                    file_json
                )
                VALUES (%s, 'source_pdf', %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    envelope_id,
                    filename,
                    storage_key or source_path,
                    download_url,
                    content_type,
                    json.dumps(payload),
                ),
            )

    def get_source_pdf(self, envelope_id: str) -> dict[str, Any]:
        """Return the persisted source PDF record for an envelope."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT filename, storage_key, download_url, content_type, file_json
                FROM esignature_files
                WHERE envelope_id = %s AND file_role = 'source_pdf'
                ORDER BY id DESC
                LIMIT 1
                """,
                (envelope_id,),
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
    ) -> StoredSigningToken:
        """
        Resolve and validate a recipient signing token.

        The caller supplies the raw token from the URL. This method hashes it,
        finds the persisted token_hash, then applies expiry/used/revoked checks.
        """
        token_hash = hash_token(raw_token, secret=secret)
        with self.conn.cursor() as cur:
            cur.execute(
                """
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
                SET used_at_iso = COALESCE(used_at_iso, %s)
                WHERE token_id = %s
                """,
                (used_at, token_id),
            )
            rowcount = getattr(cur, "rowcount", None)

        if rowcount == 0:
            raise KeyError(f"Signing token not found: {token_id}")

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
