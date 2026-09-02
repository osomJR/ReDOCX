
from __future__ import annotations

"""
ReDOCX E-Signature service layer.

This module orchestrates the e-signature processing modules:
- envelope.py       lifecycle state/result construction
- fields.py         signer/field validation
- signing.py        applying signatures to PDF
- preview.py        preview after each signer signs
- certificate.py    completion certificate
- tokens.py         secure signing link tokens
- infrastructure/email_client.py optional delivery

It is intentionally framework-agnostic. FastAPI routes should provide:
- source_path_resolver: maps PdfFilePayload.storage_key/upload_id to a local PDF path
- storage_backend: persists generated PDFs/certificates/previews
- state persistence: save/load EnvelopeState in your DB between signer steps
- token persistence: store StoredSigningToken, never raw tokens
"""

from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Union
from uuid import uuid4

import fitz  # PyMuPDF

try:
    from backend.src.schema import (
        AddSignatureOperation,
        AnalyzerRequest,
        AnalyzerResponse,
        DocumentFileOutputFormat,
        DocumentFileResult,
        ESignatureAction,
        ESignatureEnvelopeStatus,
        ESignatureField,
        ESignatureFieldType,
        ESignatureRecipientResult,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureRoutingMode,
        FeatureType,
        PdfFilePayload,
    )
    from backend.src.validation import (
        build_document_file_result,
        validate_analyzer_request,
        validate_analyzer_response,
    )
    from backend.src.processing.esignature.audit import create_audit_event, sha256_file
    from backend.src.processing.esignature.certificate import generate_completion_certificate
    from backend.src.processing.esignature.envelope import (
        EnvelopeState,
        build_envelope_result,
        complete_envelope,
        create_envelope_state,
        next_required_signers,
        void_envelope,
    )
    from backend.src.processing.esignature.fields import (
        fields_for_signer,
        normalize_email,
        signer_name_for_email,
        signer_order_for_email,
        validate_field_collection,
        validate_required_signers_have_signable_fields,
    )
    from backend.src.processing.esignature.preview import generate_step_preview
    from backend.src.processing.esignature.layout import (
        append_signature_pages,
        assert_safe_field_placements,
        signers_from_esignature_payload,
    )
    from backend.src.processing.esignature.signing import apply_signer_fields_to_pdf
    from backend.src.processing.esignature.tokens import (
        SigningToken,
        StoredSigningToken,
        build_completion_url,
        build_signing_url,
        create_signing_token,
        is_expired,
        iso_in_days,
        to_stored_token,
    )
except ImportError:  # pragma: no cover - useful when this file is placed inside src/services
    from .schema import (
        AddSignatureOperation,
        AnalyzerRequest,
        AnalyzerResponse,
        DocumentFileOutputFormat,
        DocumentFileResult,
        ESignatureAction,
        ESignatureEnvelopeStatus,
        ESignatureField,
        ESignatureFieldType,
        ESignatureRecipientResult,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureRoutingMode,
        FeatureType,
        PdfFilePayload,
    )
    from .validation import (
        build_document_file_result,
        validate_analyzer_request,
        validate_analyzer_response,
    )
    from .processing.esignature.audit import create_audit_event, sha256_file
    from .processing.esignature.certificate import generate_completion_certificate
    from .processing.esignature.envelope import (
        EnvelopeState,
        build_envelope_result,
        complete_envelope,
        create_envelope_state,
        next_required_signers,
        void_envelope,
    )
    from .processing.esignature.fields import (
        fields_for_signer,
        normalize_email,
        signer_name_for_email,
        signer_order_for_email,
        validate_field_collection,
        validate_required_signers_have_signable_fields,
    )
    from .processing.esignature.preview import generate_step_preview
    from .processing.esignature.layout import (
        append_signature_pages,
        assert_safe_field_placements,
        signers_from_esignature_payload,
    )
    from .processing.esignature.signing import apply_signer_fields_to_pdf
    from .processing.esignature.tokens import (
        SigningToken,
        StoredSigningToken,
        build_completion_url,
        build_signing_url,
        create_signing_token,
        is_expired,
        iso_in_days,
        to_stored_token,
    )


try:  # Optional infrastructure adapter generated separately.
    from backend.email_client import (
        send_completion_email,
        send_signing_invitation,
    )
except ImportError:  # pragma: no cover
    try:
        from backend.email_client import (
            send_completion_email,
            send_signing_invitation,
        )
    except ImportError:  # pragma: no cover - service still works without email dependency
        def send_signing_invitation(**_kwargs: Any) -> None:
            return None

        def send_completion_email(**_kwargs: Any) -> None:
            return None



logger = logging.getLogger(__name__)


class EmailClient(Protocol):
    """Minimal email client protocol expected by infrastructure email helpers."""
    ...


SourcePathResolver = Callable[[PdfFilePayload], str | Path]
AssetPathResolver = Callable[[str], str | Path]
DownloadUrlBuilder = Callable[[str], str]


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


class EnvelopeRepository(Protocol):
    """
    Optional persistence adapter.

    In production, implement this against PostgreSQL tables such as:
    envelopes, recipients, fields, audit_events, previews, files.
    """

    def save(self, state: EnvelopeState) -> None:
        ...

    def get(self, envelope_id: str) -> EnvelopeState:
        ...

    def get_for_update(self, envelope_id: str) -> EnvelopeState:
        ...

    def save_source_pdf(self, **kwargs: Any) -> None:
        ...

    def get_source_pdf(self, envelope_id: str) -> Mapping[str, Any]:
        ...


class SigningTokenRepository(Protocol):
    """
    Optional token persistence adapter.

    Store only StoredSigningToken.token_hash. Never store raw_token.
    """

    def save(self, token: StoredSigningToken) -> None:
        ...

    def get_valid_for_raw_token(
        self,
        raw_token: str,
        *,
        secret: Optional[str] = None,
        for_update: bool = False,
    ) -> StoredSigningToken:
        ...

    def mark_used(self, token_id: str) -> None:
        ...

    def revoke_active_for_signer(self, *, envelope_id: str, signer_email: str) -> None:
        ...


@dataclass(frozen=True)
class ESignatureServiceConfig:
    algorithm_version: Optional[str] = "esignature-service-v1.0.0"
    signed_artifacts_dir: str = "artifacts/esignature/signed"
    preview_artifacts_dir: str = "artifacts/esignature/previews"
    certificate_artifacts_dir: str = "artifacts/esignature/certificates"
    signing_base_url: Optional[str] = None
    token_secret: Optional[str] = None
    send_completion_emails: bool = True
    completion_access_days: int = 30


@dataclass(frozen=True)
class SigningDispatch:
    signer_email: str
    signer_name: str
    signing_order: int
    token: SigningToken
    stored_token: StoredSigningToken
    signing_url: str


@dataclass(frozen=True)
class RecipientSigningSession:
    """Validated, token-bound context exposed to the public signing route."""

    token: StoredSigningToken
    state: EnvelopeState
    signer: ESignatureRecipientResult
    fields: tuple[ESignatureField, ...]
    current_pdf_path: str


@dataclass(frozen=True)
class CompletedEnvelopeSession:
    """Token-bound access to completed artifacts for a sender or signer."""

    token: StoredSigningToken
    state: EnvelopeState
    document_filename: str
    signed_pdf_path: str
    certificate_path: str


class ESignatureService:
    """
    Contract-first orchestration service for ReDOCX Sign.

    Common API route patterns:

    1. Create/send:
        response = service.process(request, sender_email=current_user.email)

    2. External signer completion:
        state = repo.get(envelope_id)
        response = service.process(
            request,
            existing_state=state,
            current_pdf_path=latest_pdf_path,
            signer_email=signer.email,
            signer_signature=signature,
            field_values=form_values,
        )

    The service returns AnalyzerResponse and leaves DB persistence to the API
    layer or an injected repository.
    """

    def __init__(
        self,
        *,
        config: Optional[ESignatureServiceConfig] = None,
        storage_backend: Optional[StorageBackend] = None,
        source_path_resolver: Optional[SourcePathResolver] = None,
        asset_path_resolver: Optional[AssetPathResolver] = None,
        email_client: Optional[EmailClient] = None,
        envelope_repository: Optional[EnvelopeRepository] = None,
        token_repository: Optional[SigningTokenRepository] = None,
        download_url_builder: Optional[DownloadUrlBuilder] = None,
    ) -> None:
        self.config = config or ESignatureServiceConfig()
        self.storage_backend = storage_backend
        self.source_path_resolver = source_path_resolver
        self.asset_path_resolver = asset_path_resolver
        self.email_client = email_client
        self.envelope_repository = envelope_repository
        self.token_repository = token_repository
        self.download_url_builder = download_url_builder

    def process(
        self,
        request: Union[AnalyzerRequest, Mapping[str, Any]],
        *,
        existing_state: Optional[EnvelopeState] = None,
        current_pdf_path: Optional[str | Path] = None,
        signer_email: Optional[str] = None,
        signer_signature: Optional[AddSignatureOperation] = None,
        field_values: Optional[Mapping[str, str]] = None,
        sender_email: Optional[str] = None,
        sender_name: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        owner_organization_id: Optional[str] = None,
        send_emails: bool = True,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AnalyzerResponse:
        req = validate_analyzer_request(request)

        if req.action != FeatureType.e_signature:
            raise ValueError(f"ESignatureService cannot handle action: {req.action.value}")
        if not isinstance(req.input, PdfFilePayload):
            raise ValueError("e_signature requires PdfFilePayload input.")
        if not isinstance(req.payload, ESignatureRequest):
            raise ValueError("e_signature requires ESignatureRequest payload.")

        if existing_state is not None:
            state = existing_state
            source_pdf_path = self._require_existing_pdf(
                Path(current_pdf_path).expanduser().resolve()
                if current_pdf_path is not None
                else Path(self.current_pdf_path(state))
            )
        else:
            source_pdf_path = self._resolve_pdf_path(req.input)
            prepared_source_path: Optional[Path] = None
            new_envelope_source_ready = False
            try:
                if req.payload.add_signature_page:
                    prepared_source_path = self._prepared_source_output_path()
                    append_signature_pages(
                        source_pdf_path,
                        prepared_source_path,
                        signers=signers_from_esignature_payload(req.payload),
                    )
                    source_pdf_path = prepared_source_path
                    with fitz.open(source_pdf_path) as prepared_pdf:
                        prepared_page_count = int(prepared_pdf.page_count)
                    prepared_hash = sha256_file(source_pdf_path)
                    prepared_metadata = req.input.metadata.model_copy(
                        update={
                            "page_count": prepared_page_count,
                            "file_size_mb": round(
                                source_pdf_path.stat().st_size / (1024 * 1024), 4
                            ),
                            "checksum_sha256": prepared_hash,
                        }
                    )
                    prepared_input = req.input.model_copy(
                        update={
                            "metadata": prepared_metadata,
                            "storage_key": str(source_pdf_path),
                            "upload_id": None,
                        }
                    )
                    req = req.model_copy(update={"input": prepared_input})

                # Fail closed before an envelope is persisted or invitation email is
                # sent. This is the server-side guarantee behind the sender warnings.
                assert_safe_field_placements(source_pdf_path, req.payload.fields)

                source_hash = req.input.metadata.checksum_sha256 or sha256_file(
                    source_pdf_path
                )
                req = self._persist_source_request(req, source_pdf_path=source_pdf_path)
                persisted_source_path = self._resolve_pdf_path(req.input)
                source_pdf_path = persisted_source_path
                state = create_envelope_state(
                    req.payload,
                    source_document_sha256=source_hash,
                    owner_email=sender_email
                    or self._owner_email_from_request(req.payload),
                    owner_user_id=owner_user_id,
                    owner_organization_id=owner_organization_id,
                    expires_at_iso=iso_in_days(req.payload.expires_in_days),
                    ip_address=ip_address,
                    user_agent=user_agent,
                    source_request=req,
                )
                self._persist_new_envelope_source(
                    state=state,
                    request=req,
                    source_pdf_path=source_pdf_path,
                )
                new_envelope_source_ready = True
            finally:
                # A generated signature-page working copy is disposable after it
                # has been copied to durable storage. On failure it is always
                # removed. In standalone/no-storage deployments it remains the
                # envelope source only when source creation completed successfully.
                if prepared_source_path is not None and prepared_source_path.exists():
                    keep_as_source = (
                        new_envelope_source_ready
                        and prepared_source_path == source_pdf_path
                    )
                    if not keep_as_source:
                        prepared_source_path.unlink(missing_ok=True)

        if req.payload.action == ESignatureAction.create_draft:
            state = self._apply_request_self_signature_if_present(
                request=req,
                state=state,
                current_pdf_path=current_pdf_path or source_pdf_path,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        elif req.payload.action == ESignatureAction.send:
            if req.payload.recipients and not send_emails:
                raise ValueError(
                    "Sending an e-signature envelope to recipients requires email delivery."
                )
            state = self._apply_request_self_signature_if_present(
                request=req,
                state=state,
                current_pdf_path=current_pdf_path or source_pdf_path,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            validate_required_signers_have_signable_fields(req.payload)
            state = self._mark_envelope_sent(
                state,
                actor_email=sender_email or state.owner_email,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            if send_emails:
                state, _dispatches = self._send_signing_invitations(
                    request=req,
                    state=state,
                    document_name=req.input.filename,
                    sender_name=sender_name,
                )

        elif req.payload.action in {ESignatureAction.sign, ESignatureAction.complete_signing}:
            resolved_signer_email = signer_email or self._self_signer_email_if_present(req.payload)
            if not resolved_signer_email:
                raise ValueError("sign/complete_signing requires signer_email or payload.self_signer.")

            resolved_signature = signer_signature or self._signature_for_signer_from_payload(
                req.payload,
                signer_email=resolved_signer_email,
            )
            if resolved_signature is None:
                raise ValueError("sign/complete_signing requires a signer signature.")

            self._assert_signer_can_act(
                state,
                payload=req.payload,
                signer_email=resolved_signer_email,
            )

            state = self._apply_signer_step(
                request=req,
                state=state,
                current_pdf_path=current_pdf_path or source_pdf_path,
                signer_email=resolved_signer_email,
                signature=resolved_signature,
                field_values=field_values,
                ip_address=ip_address,
                user_agent=user_agent,
            )

            if state.status != ESignatureEnvelopeStatus.completed and send_emails:
                state, _dispatches = self._send_signing_invitations(
                    request=req,
                    state=state,
                    document_name=req.input.filename,
                    sender_name=sender_name,
                )

        elif req.payload.action == ESignatureAction.void:
            state = void_envelope(
                state,
                actor_email=sender_email or state.owner_email,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        else:  # pragma: no cover - enum guarded by schema
            raise ValueError(f"Unsupported e-signature action: {req.payload.action.value}")

        if state.status == ESignatureEnvelopeStatus.completed and state.audit_certificate is None:
            state = self._complete_with_certificate(
                request=req,
                state=state,
                sender_email=sender_email or state.owner_email,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            if self.config.send_completion_emails and send_emails:
                self._send_completion_notifications(req, state)

        if self.envelope_repository is not None:
            self.envelope_repository.save(state)

        response = AnalyzerResponse(
            action=req.action,
            input_format="pdf_file",
            policy=req.policy,
            system_language=req.system_language,
            result=build_envelope_result(
                state,
                algorithm_version=self.config.algorithm_version,
            ),
        )
        return validate_analyzer_response(response, request=req)

    # ------------------------------------------------------------------
    # Public recipient-link workflow
    # ------------------------------------------------------------------

    def get_recipient_session(
        self,
        raw_token: str,
        *,
        mark_viewed_event: bool = True,
        for_update: bool = False,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> RecipientSigningSession:
        """Resolve a one-time email token to its locked-down signing context."""
        self._require_recipient_dependencies()
        assert self.token_repository is not None
        assert self.envelope_repository is not None

        should_lock = for_update or mark_viewed_event
        token = self.token_repository.get_valid_for_raw_token(
            raw_token,
            secret=self.config.token_secret,
            for_update=should_lock,
        )
        if should_lock and hasattr(self.envelope_repository, "get_for_update"):
            state = self.envelope_repository.get_for_update(token.envelope_id)
        else:
            state = self.envelope_repository.get(token.envelope_id)

        payload = self._payload_from_state(state)
        self._assert_signer_can_act(
            state,
            payload=payload,
            signer_email=token.signer_email,
        )

        signer = self._recipient_for_email(state, token.signer_email)
        if mark_viewed_event and signer.status in {
            ESignatureRecipientStatus.pending,
            ESignatureRecipientStatus.sent,
        }:
            state = self._mark_signer_viewed(
                state,
                signer_email=token.signer_email,
                ip_address=ip_address,
                user_agent=user_agent,
            )
            self.envelope_repository.save(state)
            signer = self._recipient_for_email(state, token.signer_email)

        assigned_fields = tuple(fields_for_signer(state.fields, token.signer_email))
        if not assigned_fields:
            raise ValueError("No e-signature fields are assigned to this recipient.")

        return RecipientSigningSession(
            token=token,
            state=state,
            signer=signer,
            fields=assigned_fields,
            current_pdf_path=self.current_pdf_path(state),
        )

    def sign_recipient(
        self,
        raw_token: str,
        *,
        signature: AddSignatureOperation,
        field_values: Optional[Mapping[str, str]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AnalyzerResponse:
        """Atomically consume an emailed token and apply that recipient's fields."""
        session = self.get_recipient_session(
            raw_token,
            mark_viewed_event=False,
            for_update=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        source_request = session.state.source_request
        if source_request is None or not isinstance(source_request.payload, ESignatureRequest):
            raise RuntimeError(
                "This envelope predates recipient-signing persistence and cannot be completed safely."
            )

        signing_payload = source_request.payload.model_copy(
            update={"action": ESignatureAction.sign}
        )
        signing_request = source_request.model_copy(update={"payload": signing_payload})
        response = self.process(
            signing_request,
            existing_state=session.state,
            current_pdf_path=session.current_pdf_path,
            signer_email=session.token.signer_email,
            signer_signature=signature,
            field_values=field_values,
            sender_email=session.state.owner_email,
            send_emails=True,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        assert self.token_repository is not None
        self.token_repository.mark_used(session.token.token_id)
        return response

    def current_pdf_path(self, state: EnvelopeState) -> str:
        """Return the durable current PDF version for previewing or signing."""
        if state.signed_pdf is not None:
            resolved = self._path_from_file_result(state.signed_pdf)
            if resolved is not None:
                return str(resolved)

        if self.envelope_repository is not None and hasattr(
            self.envelope_repository, "get_source_pdf"
        ):
            source = self.envelope_repository.get_source_pdf(state.envelope_id)
            for key in ("source_path", "storage_key"):
                resolved = self._resolve_persisted_path(source.get(key))
                if resolved is not None:
                    return str(self._require_existing_pdf(resolved))

        if state.source_request is not None and isinstance(
            state.source_request.input, PdfFilePayload
        ):
            return str(self._resolve_pdf_path(state.source_request.input))

        raise FileNotFoundError(
            f"No durable PDF is available for e-signature envelope {state.envelope_id}."
        )

    def get_completed_session(self, raw_token: str) -> CompletedEnvelopeSession:
        """Resolve a completion-access token without exposing storage paths."""
        self._require_recipient_dependencies()
        assert self.token_repository is not None
        assert self.envelope_repository is not None

        token = self.token_repository.get_valid_for_raw_token(
            raw_token,
            secret=self.config.token_secret,
            for_update=False,
        )
        state = self.envelope_repository.get(token.envelope_id)
        if state.status != ESignatureEnvelopeStatus.completed:
            raise ValueError("This envelope has not been completed.")

        allowed_emails = {
            recipient.email.strip().lower() for recipient in state.recipients
        }
        if state.owner_email:
            allowed_emails.add(state.owner_email.strip().lower())
        if token.signer_email.strip().lower() not in allowed_emails:
            raise ValueError("This completion link does not belong to the envelope.")
        if state.signed_pdf is None or state.audit_certificate is None:
            raise RuntimeError("Completed envelope artifacts are unavailable.")

        signed_path = self._path_from_file_result(state.signed_pdf)
        certificate_path = self._path_from_file_result(state.audit_certificate)
        if signed_path is None or certificate_path is None:
            raise FileNotFoundError("Completed envelope artifacts could not be resolved.")

        source_request = state.source_request
        document_filename = (
            source_request.input.filename
            if source_request is not None
            and isinstance(source_request.input, PdfFilePayload)
            else "signed-document.pdf"
        )
        return CompletedEnvelopeSession(
            token=token,
            state=state,
            document_filename=document_filename,
            signed_pdf_path=str(self._require_existing_pdf(signed_path)),
            certificate_path=str(self._require_existing_pdf(certificate_path)),
        )

    # ------------------------------------------------------------------
    # Signing workflow helpers
    # ------------------------------------------------------------------

    def _apply_request_self_signature_if_present(
        self,
        *,
        request: AnalyzerRequest,
        state: EnvelopeState,
        current_pdf_path: str | Path,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        payload = request.payload
        if not isinstance(payload, ESignatureRequest):
            raise ValueError("Expected ESignatureRequest.")
        if payload.self_signer is None or payload.self_signer.signature is None:
            return state

        # Avoid applying the same self-signing step twice when an existing state
        # already has the owner marked signed and a matching preview.
        self_email = normalize_email(payload.self_signer.email)
        already_signed = any(
            recipient.email.lower() == self_email and recipient.status == ESignatureRecipientStatus.signed
            for recipient in state.recipients
        )
        already_previewed = any(preview.signer_email.lower() == self_email for preview in state.previews)
        if already_signed and already_previewed:
            return state

        return self._apply_signer_step(
            request=request,
            state=state,
            current_pdf_path=current_pdf_path,
            signer_email=payload.self_signer.email,
            signature=payload.self_signer.signature,
            field_values=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _apply_signer_step(
        self,
        *,
        request: AnalyzerRequest,
        state: EnvelopeState,
        current_pdf_path: str | Path,
        signer_email: str,
        signature: AddSignatureOperation,
        field_values: Optional[Mapping[str, str]],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        payload = request.payload
        if not isinstance(payload, ESignatureRequest):
            raise ValueError("Expected ESignatureRequest.")

        normalized_email = normalize_email(signer_email)
        signer_name = signer_name_for_email(payload, normalized_email)
        signing_order = signer_order_for_email(payload, normalized_email)

        # Record viewing/consent before signing, useful for audit trails.
        state = self._mark_signer_viewed(
            state,
            signer_email=normalized_email,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        state = self._append_audit_event(
            state,
            event_type="signer_consented",
            actor_email=normalized_email,
            document_sha256=state.source_document_sha256,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        fields = self._fields_for_signing(payload, signer_email=normalized_email, signature=signature)
        output_path = self._signed_output_path(state.envelope_id, normalized_email)
        signed_artifact = apply_signer_fields_to_pdf(
            source_pdf_path=current_pdf_path,
            output_pdf_path=output_path,
            fields=fields,
            signer_email=normalized_email,
            signer_name=signer_name,
            signature=signature,
            values=field_values,
            storage_key_resolver=self._resolve_asset_path if self.asset_path_resolver is not None else None,
        )

        signed_pdf_result = self._persist_pdf_as_document_result(
            path=Path(signed_artifact.path),
            filename=signed_artifact.filename,
        )

        preview_path = self._preview_output_path(state.envelope_id, normalized_email)
        preview = generate_step_preview(
            source_pdf_path=signed_artifact.path,
            output_pdf_path=preview_path,
            signer_email=normalized_email,
            signer_name=signer_name,
            signing_order=signing_order,
            preview_stage=f"signed_by_{normalized_email}",
            storage_backend=self.storage_backend,
            algorithm_version=self.config.algorithm_version,
        )
        self._attach_download_url(preview.preview_pdf)

        return self._mark_signer_signed(
            state,
            signer_email=normalized_email,
            preview=preview,
            signed_pdf=signed_pdf_result,
            document_sha256=signed_artifact.sha256,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _complete_with_certificate(
        self,
        *,
        request: AnalyzerRequest,
        state: EnvelopeState,
        sender_email: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        if state.signed_pdf is None:
            raise ValueError("Cannot complete envelope without signed_pdf.")

        signed_pdf_path = self._path_from_file_result(state.signed_pdf)
        signed_hash = sha256_file(signed_pdf_path) if signed_pdf_path is not None else state.source_document_sha256

        certificate_path = self._certificate_output_path(state.envelope_id)
        certificate = generate_completion_certificate(
            output_pdf_path=certificate_path,
            envelope_id=state.envelope_id,
            document_name=request.input.filename if isinstance(request.input, PdfFilePayload) else "document.pdf",
            workflow=state.workflow,
            status=ESignatureEnvelopeStatus.completed,
            recipients=state.recipients,
            audit_events=state.audit_events,
            signed_pdf_sha256=signed_hash,
            sender_email=sender_email,
            storage_backend=self.storage_backend,
            algorithm_version=self.config.algorithm_version,
        )
        self._attach_download_url(certificate.result)

        return complete_envelope(
            state,
            signed_pdf=state.signed_pdf,
            audit_certificate=certificate.result,
            actor_email=sender_email,
            document_sha256=signed_hash,
            ip_address=ip_address,
            user_agent=user_agent,
        )


    def _mark_envelope_sent(
        self,
        state: EnvelopeState,
        *,
        actor_email: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        if state.status not in {ESignatureEnvelopeStatus.draft, ESignatureEnvelopeStatus.sent, ESignatureEnvelopeStatus.partially_signed}:
            raise ValueError(f"Cannot send envelope from status '{state.status.value}'.")
        event = create_audit_event(
            event_type="envelope_sent",
            actor_email=actor_email or state.owner_email,
            ip_address=ip_address,
            user_agent=user_agent,
            document_sha256=state.source_document_sha256,
        )
        return replace(
            state,
            status=(
                ESignatureEnvelopeStatus.partially_signed
                if any(
                    recipient.status == ESignatureRecipientStatus.signed
                    for recipient in state.recipients
                )
                else ESignatureEnvelopeStatus.sent
            ),
            updated_at_iso=event.created_at_iso,
            audit_events=(*state.audit_events, event),
        )

    def _mark_signer_viewed(
        self,
        state: EnvelopeState,
        *,
        signer_email: str,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        if state.status in {ESignatureEnvelopeStatus.completed, ESignatureEnvelopeStatus.voided, ESignatureEnvelopeStatus.expired}:
            raise ValueError(f"Cannot view envelope from status '{state.status.value}'.")

        normalized = normalize_email(signer_email)
        found = False
        recipients: list[ESignatureRecipientResult] = []
        for recipient in state.recipients:
            if recipient.email.lower() == normalized:
                found = True
                if recipient.status in {ESignatureRecipientStatus.pending, ESignatureRecipientStatus.sent}:
                    recipients.append(self._recipient_with_status(recipient, ESignatureRecipientStatus.viewed))
                else:
                    recipients.append(recipient)
            else:
                recipients.append(recipient)

        if not found:
            raise ValueError(f"Unknown signer email: {signer_email}")

        event = create_audit_event(
            event_type="signer_viewed",
            actor_email=normalized,
            ip_address=ip_address,
            user_agent=user_agent,
            document_sha256=state.source_document_sha256,
        )
        next_status = ESignatureEnvelopeStatus.viewed if state.status == ESignatureEnvelopeStatus.sent else state.status
        return replace(
            state,
            status=next_status,
            recipients=tuple(recipients),
            updated_at_iso=event.created_at_iso,
            audit_events=(*state.audit_events, event),
        )

    def _mark_signer_signed(
        self,
        state: EnvelopeState,
        *,
        signer_email: str,
        preview: Any,
        signed_pdf: DocumentFileResult,
        document_sha256: str,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        if state.status in {ESignatureEnvelopeStatus.completed, ESignatureEnvelopeStatus.voided, ESignatureEnvelopeStatus.expired}:
            raise ValueError(f"Cannot sign envelope from status '{state.status.value}'.")

        normalized = normalize_email(signer_email)
        found = False
        recipients: list[ESignatureRecipientResult] = []
        for recipient in state.recipients:
            if recipient.email.lower() == normalized:
                found = True
                recipients.append(self._recipient_with_status(recipient, ESignatureRecipientStatus.signed))
            else:
                recipients.append(recipient)

        if not found:
            raise ValueError(f"Unknown signer email: {signer_email}")

        events = [
            *state.audit_events,
            create_audit_event(
                event_type="signer_signed",
                actor_email=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
                document_sha256=document_sha256,
            ),
            create_audit_event(
                event_type="preview_generated",
                actor_email=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
                document_sha256=document_sha256,
                created_at_iso=getattr(preview, "created_at_iso", None),
            ),
        ]

        next_status = (
            ESignatureEnvelopeStatus.completed
            if all(item.status == ESignatureRecipientStatus.signed for item in recipients)
            else ESignatureEnvelopeStatus.partially_signed
        )

        return replace(
            state,
            status=next_status,
            recipients=tuple(recipients),
            previews=(*state.previews, preview),
            signed_pdf=signed_pdf,
            source_document_sha256=document_sha256,
            updated_at_iso=events[-1].created_at_iso,
            audit_events=tuple(events),
        )

    @staticmethod
    def _recipient_with_status(
        recipient: ESignatureRecipientResult,
        status: ESignatureRecipientStatus,
    ) -> ESignatureRecipientResult:
        return ESignatureRecipientResult(
            name=recipient.name,
            email=recipient.email,
            role=recipient.role,
            signing_order=recipient.signing_order,
            status=status,
        )


    # ------------------------------------------------------------------
    # Email/token helpers
    # ------------------------------------------------------------------

    def _send_signing_invitations(
        self,
        *,
        request: AnalyzerRequest,
        state: EnvelopeState,
        document_name: str,
        sender_name: Optional[str],
    ) -> tuple[EnvelopeState, list[SigningDispatch]]:
        if self.email_client is None:
            raise RuntimeError("E-signature email delivery is not configured.")
        if self.token_repository is None:
            raise RuntimeError("E-signature token persistence is not configured.")
        if not (self.config.signing_base_url or "").strip():
            raise RuntimeError("E-signature public signing URL is not configured.")
        if not (self.config.token_secret or "").strip():
            raise RuntimeError("E-signature token pepper is not configured.")

        payload = request.payload
        if not isinstance(payload, ESignatureRequest):
            raise ValueError("Expected ESignatureRequest.")

        dispatches: list[SigningDispatch] = []
        pending = [
            recipient
            for recipient in state.recipients
            if recipient.status == ESignatureRecipientStatus.pending
        ]
        if payload.routing_mode == ESignatureRoutingMode.sequential and pending:
            minimum_order = min(recipient.signing_order for recipient in pending)
            pending = [
                recipient
                for recipient in pending
                if recipient.signing_order == minimum_order
            ]

        next_state = state
        for recipient in pending:
            self.token_repository.revoke_active_for_signer(
                envelope_id=state.envelope_id,
                signer_email=recipient.email,
            )

            token = create_signing_token(
                envelope_id=state.envelope_id,
                signer_email=recipient.email,
                expires_in_days=payload.expires_in_days,
                expires_at_iso=state.expires_at_iso,
                secret=self.config.token_secret,
            )
            stored = to_stored_token(token)
            self.token_repository.save(stored)

            signing_url = build_signing_url(token.raw_token, base_url=self.config.signing_base_url)

            try:
                delivery = send_signing_invitation(
                    email_client=self.email_client,
                    signer_name=recipient.name,
                    signer_email=recipient.email,
                    document_name=document_name,
                    signing_url=signing_url,
                    sender_name=sender_name,
                    expires_at_iso=token.expires_at_iso,
                    subject=payload.email_subject,
                    message=payload.email_message,
                )
                accepted = {
                    str(value).strip().lower()
                    for value in getattr(delivery, "accepted_recipients", ())
                }
                if recipient.email.strip().lower() not in accepted:
                    raise RuntimeError("The email provider rejected the recipient.")
            except Exception as exc:  # pragma: no cover - provider/network dependent
                raise RuntimeError(
                    f"Could not deliver the signing invitation to {recipient.email}."
                ) from exc

            dispatches.append(
                SigningDispatch(
                    signer_email=recipient.email,
                    signer_name=recipient.name,
                    signing_order=recipient.signing_order,
                    token=token,
                    stored_token=stored,
                    signing_url=signing_url,
                )
            )

            recipients = tuple(
                self._recipient_with_status(item, ESignatureRecipientStatus.sent)
                if item.email.lower() == recipient.email.lower()
                else item
                for item in next_state.recipients
            )
            email_event = create_audit_event(
                event_type="email_sent",
                actor_email=next_state.owner_email,
                document_sha256=next_state.source_document_sha256,
            )
            next_state = replace(
                next_state,
                status=(
                    ESignatureEnvelopeStatus.partially_signed
                    if any(
                        item.status == ESignatureRecipientStatus.signed
                        for item in recipients
                    )
                    else ESignatureEnvelopeStatus.sent
                ),
                recipients=recipients,
                updated_at_iso=email_event.created_at_iso,
                audit_events=(*next_state.audit_events, email_event),
            )

        return next_state, dispatches

    def _send_completion_notifications(self, request: AnalyzerRequest, state: EnvelopeState) -> None:
        if self.email_client is None or state.signed_pdf is None:
            return
        if (
            self.token_repository is None
            or not (self.config.signing_base_url or "").strip()
            or not (self.config.token_secret or "").strip()
        ):
            logger.warning(
                "Completion email skipped because secure completion links are not configured.",
                extra={"envelope_id": state.envelope_id},
            )
            return

        document_name = request.input.filename if isinstance(request.input, PdfFilePayload) else "document.pdf"

        recipients = [
            (recipient.email, recipient.name)
            for recipient in state.recipients
        ]
        known_emails = {email.lower() for email, _name in recipients}
        if state.owner_email and state.owner_email.lower() not in known_emails:
            recipients.append((state.owner_email, "Document sender"))

        for recipient_email, recipient_name in recipients:
            try:
                access_token = create_signing_token(
                    envelope_id=state.envelope_id,
                    signer_email=recipient_email,
                    expires_in_days=self.config.completion_access_days,
                    secret=self.config.token_secret,
                )
                self.token_repository.save(to_stored_token(access_token))
                completion_url = build_completion_url(
                    access_token.raw_token,
                    base_url=self.config.signing_base_url,
                )
                delivery = send_completion_email(
                    email_client=self.email_client,
                    recipient_email=recipient_email,
                    recipient_name=recipient_name,
                    document_name=document_name,
                    completion_url=completion_url,
                    download_url=None,
                    certificate_url=None,
                )
                accepted = {
                    str(value).strip().lower()
                    for value in getattr(delivery, "accepted_recipients", ())
                }
                if recipient_email.strip().lower() not in accepted:
                    raise RuntimeError("The email provider rejected the recipient.")
            except Exception as exc:  # pragma: no cover - provider/network dependent
                logger.warning(
                    "E-signature completion email delivery failed; completed signing will continue.",
                    extra={
                        "envelope_id": state.envelope_id,
                        "recipient_email": recipient_email,
                        "error": str(exc),
                    },
                )

    # ------------------------------------------------------------------
    # Field / path / artifact helpers
    # ------------------------------------------------------------------

    def _require_recipient_dependencies(self) -> None:
        if self.envelope_repository is None:
            raise RuntimeError("E-signature envelope persistence is not configured.")
        if self.token_repository is None:
            raise RuntimeError("E-signature token persistence is not configured.")

    @staticmethod
    def _payload_from_state(state: EnvelopeState) -> ESignatureRequest:
        source_request = state.source_request
        if source_request is None or not isinstance(source_request.payload, ESignatureRequest):
            raise RuntimeError(
                "The persisted envelope does not contain its original signing contract."
            )
        return source_request.payload

    @staticmethod
    def _recipient_for_email(
        state: EnvelopeState,
        signer_email: str,
    ) -> ESignatureRecipientResult:
        normalized = normalize_email(signer_email)
        for recipient in state.recipients:
            if recipient.email.lower() == normalized:
                return recipient
        raise ValueError("This signing token does not belong to an envelope recipient.")

    def _assert_signer_can_act(
        self,
        state: EnvelopeState,
        *,
        payload: ESignatureRequest,
        signer_email: str,
    ) -> None:
        if state.status in {
            ESignatureEnvelopeStatus.completed,
            ESignatureEnvelopeStatus.voided,
            ESignatureEnvelopeStatus.expired,
        }:
            raise ValueError(f"Envelope is {state.status.value} and can no longer be signed.")
        if state.expires_at_iso and is_expired(state.expires_at_iso):
            raise ValueError("Envelope has expired and can no longer be signed.")

        signer = self._recipient_for_email(state, signer_email)
        if signer.status == ESignatureRecipientStatus.signed:
            raise ValueError("This recipient has already signed the envelope.")
        if signer.status == ESignatureRecipientStatus.declined:
            raise ValueError("This recipient has declined the envelope.")

        if payload.routing_mode == ESignatureRoutingMode.sequential:
            allowed = {
                recipient.email.lower()
                for recipient in next_required_signers(state)
            }
            if allowed and signer.email.lower() not in allowed:
                raise ValueError("It is not this recipient's turn to sign yet.")

    def _persist_source_request(
        self,
        request: AnalyzerRequest,
        *,
        source_pdf_path: Path,
    ) -> AnalyzerRequest:
        """Copy the upload into owner-scoped durable storage before emailing links."""
        if self.storage_backend is None:
            return request
        if not isinstance(request.input, PdfFilePayload):
            raise ValueError("Expected PdfFilePayload.")

        stored = self.storage_backend.persist(
            source_file_path=str(source_pdf_path),
            artifact_name=request.input.filename,
            content_type=request.input.mime_type,
        )
        storage_key = getattr(stored, "storage_key", None)
        if not storage_key:
            raise RuntimeError("Durable e-signature storage did not return a storage key.")

        durable_input = request.input.model_copy(
            update={"storage_key": storage_key, "upload_id": None}
        )
        return request.model_copy(update={"input": durable_input})

    def _persist_new_envelope_source(
        self,
        *,
        state: EnvelopeState,
        request: AnalyzerRequest,
        source_pdf_path: Path,
    ) -> None:
        if self.envelope_repository is None:
            return

        # Save the draft first so the source-file row and token FK always have
        # a parent envelope inside the same transaction.
        self.envelope_repository.save(state)
        if not hasattr(self.envelope_repository, "save_source_pdf"):
            return

        source_input = request.input
        if not isinstance(source_input, PdfFilePayload):
            return
        durable_path = self._resolve_persisted_path(source_input.storage_key)
        self.envelope_repository.save_source_pdf(
            envelope_id=state.envelope_id,
            source_path=str(durable_path or source_pdf_path),
            filename=source_input.filename,
            file_size_mb=source_input.metadata.file_size_mb,
            storage_key=source_input.storage_key or str(durable_path or source_pdf_path),
            download_url=(
                self.download_url_builder(source_input.storage_key)
                if source_input.storage_key and self.download_url_builder is not None
                else None
            ),
            content_type=source_input.mime_type,
        )

    def _resolve_persisted_path(self, value: Any) -> Optional[Path]:
        if not isinstance(value, str) or not value.strip():
            return None
        candidate = Path(value.strip()).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

        resolver = getattr(self.storage_backend, "resolve_storage_key", None)
        if callable(resolver):
            try:
                resolved = Path(resolver(value.strip())).expanduser().resolve()
            except (ValueError, FileNotFoundError):
                return None
            if resolved.exists() and resolved.is_file():
                return resolved
        return None

    def _attach_download_url(self, result: Any) -> Any:
        storage_key = getattr(result, "storage_key", None)
        download_url = getattr(result, "download_url", None)
        if (
            storage_key
            and not download_url
            and self.download_url_builder is not None
            and hasattr(result, "download_url")
        ):
            result.download_url = self.download_url_builder(storage_key)
        return result

    def _fields_for_signing(
        self,
        payload: ESignatureRequest,
        *,
        signer_email: str,
        signature: AddSignatureOperation,
    ) -> list[ESignatureField]:
        known = {recipient.email.lower() for recipient in payload.recipients}
        if payload.self_signer is not None:
            known.add(payload.self_signer.email.lower())

        page_count = None
        validate_field_collection(payload.fields, known_emails=known, page_count=page_count)

        selected = fields_for_signer(payload.fields, signer_email)

        # Self-sign convenience: when the frontend submits the signature placement
        # through self_signer.signature instead of a separate ESignatureField,
        # synthesize a signature field from the operation rectangle/page.
        if not selected:
            normalized = normalize_email(signer_email)
            if payload.self_signer and normalize_email(payload.self_signer.email) == normalized:
                selected = [
                    ESignatureField(
                        field_id="self_signature",
                        assigned_to_email=normalized,
                        field_type=ESignatureFieldType.signature,
                        page_number=signature.page_number,
                        rectangle=signature.rectangle,
                        required=True,
                        label="Signature",
                    )
                ]

        if not selected:
            raise ValueError(f"No e-signature fields assigned to signer: {signer_email}")

        return selected

    def _append_audit_event(
        self,
        state: EnvelopeState,
        *,
        event_type: str,
        actor_email: Optional[str],
        document_sha256: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> EnvelopeState:
        from dataclasses import replace

        event = create_audit_event(
            event_type=event_type,
            actor_email=actor_email,
            document_sha256=document_sha256,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return replace(state, audit_events=(*state.audit_events, event))

    def _resolve_pdf_path(self, payload: PdfFilePayload) -> Path:
        if self.source_path_resolver is not None:
            resolved = Path(self.source_path_resolver(payload)).expanduser().resolve()
            return self._require_existing_pdf(resolved)

        candidates = [
            getattr(payload, "local_path", None),
            getattr(payload, "file_path", None),
            getattr(payload, "path", None),
            payload.storage_key,
            payload.upload_id,
            payload.filename,
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                path = Path(candidate.strip()).expanduser()
                if path.exists():
                    return self._require_existing_pdf(path.resolve())

        raise ValueError(
            "Could not resolve PdfFilePayload to a readable local PDF path. "
            "Pass source_path_resolver to ESignatureService so storage_key/upload_id "
            "can be mapped to a backend file path."
        )

    def _resolve_asset_path(self, storage_key: str) -> str:
        if self.asset_path_resolver is None:
            path = Path(storage_key).expanduser().resolve()
        else:
            path = Path(self.asset_path_resolver(storage_key)).expanduser().resolve()

        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Signature asset not found: {storage_key}")
        return str(path)

    @staticmethod
    def _require_existing_pdf(path: Path) -> Path:
        if not path.exists():
            raise FileNotFoundError(f"PDF source not found: {path}")
        if not path.is_file():
            raise ValueError(f"PDF source is not a file: {path}")
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"PDF source must end with .pdf: {path.name}")
        return path

    def _prepared_source_output_path(self) -> Path:
        directory = Path(self.config.signed_artifacts_dir) / "prepared"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"prepared-{uuid4().hex}.pdf"

    def _signed_output_path(self, envelope_id: str, signer_email: str) -> Path:
        directory = Path(self.config.signed_artifacts_dir)
        directory.mkdir(parents=True, exist_ok=True)
        safe_email = self._safe_slug(signer_email)
        return directory / f"{envelope_id}-{safe_email}-{uuid4().hex[:8]}.pdf"

    def _preview_output_path(self, envelope_id: str, signer_email: str) -> Path:
        directory = Path(self.config.preview_artifacts_dir)
        directory.mkdir(parents=True, exist_ok=True)
        safe_email = self._safe_slug(signer_email)
        return directory / f"{envelope_id}-{safe_email}-preview-{uuid4().hex[:8]}.pdf"

    def _certificate_output_path(self, envelope_id: str) -> Path:
        directory = Path(self.config.certificate_artifacts_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{envelope_id}-certificate.pdf"

    def _persist_pdf_as_document_result(self, *, path: Path, filename: str) -> DocumentFileResult:
        storage_key = None
        download_url = None
        if self.storage_backend is not None:
            stored = self.storage_backend.persist(
                source_file_path=str(path),
                artifact_name=filename,
                content_type="application/pdf",
            )
            storage_key = getattr(stored, "storage_key", None)
            download_url = getattr(stored, "download_url", None)
            if storage_key and not download_url and self.download_url_builder is not None:
                download_url = self.download_url_builder(storage_key)

        return build_document_file_result(
            filename=filename,
            output_format=DocumentFileOutputFormat.pdf,
            file_size_mb=round(path.stat().st_size / (1024 * 1024), 4),
            storage_key=storage_key,
            download_url=download_url,
            algorithm_version=self.config.algorithm_version,
        )

    def _path_from_file_result(self, result: DocumentFileResult) -> Optional[Path]:
        candidates = [result.storage_key, result.filename]
        for candidate in candidates:
            resolved = self._resolve_persisted_path(candidate)
            if resolved is not None:
                return resolved
        return None

    @staticmethod
    def _owner_email_from_request(payload: ESignatureRequest) -> Optional[str]:
        return payload.self_signer.email if payload.self_signer is not None else None

    @staticmethod
    def _self_signer_email_if_present(payload: ESignatureRequest) -> Optional[str]:
        return payload.self_signer.email if payload.self_signer is not None else None

    @staticmethod
    def _signature_for_signer_from_payload(
        payload: ESignatureRequest,
        *,
        signer_email: str,
    ) -> Optional[AddSignatureOperation]:
        if payload.self_signer and normalize_email(payload.self_signer.email) == normalize_email(signer_email):
            return payload.self_signer.signature
        return None

    @staticmethod
    def _safe_slug(value: str) -> str:
        import re

        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
        cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
        return cleaned or "signer"


__all__ = [
    "ESignatureService",
    "ESignatureServiceConfig",
    "SigningDispatch",
    "RecipientSigningSession",
    "CompletedEnvelopeSession",
    "SourcePathResolver",
    "AssetPathResolver",
    "DownloadUrlBuilder",
    "EnvelopeRepository",
    "SigningTokenRepository",
]
