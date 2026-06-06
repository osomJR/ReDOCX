
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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Union
from uuid import uuid4

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
        ESignatureSelfSigner,
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
        mark_envelope_sent,
        mark_signer_signed,
        mark_signer_viewed,
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
    from backend.src.processing.esignature.signing import apply_signer_fields_to_pdf
    from backend.src.processing.esignature.tokens import (
        SigningToken,
        StoredSigningToken,
        build_signing_url,
        create_signing_token,
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
        ESignatureSelfSigner,
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
        mark_envelope_sent,
        mark_signer_signed,
        mark_signer_viewed,
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
    from .processing.esignature.signing import apply_signer_fields_to_pdf
    from .processing.esignature.tokens import (
        SigningToken,
        StoredSigningToken,
        build_signing_url,
        create_signing_token,
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



class EmailClient(Protocol):
    """Minimal email client protocol expected by infrastructure email helpers."""
    ...


SourcePathResolver = Callable[[PdfFilePayload], str | Path]
AssetPathResolver = Callable[[str], str | Path]


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


class SigningTokenRepository(Protocol):
    """
    Optional token persistence adapter.

    Store only StoredSigningToken.token_hash. Never store raw_token.
    """

    def save(self, token: StoredSigningToken) -> None:
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


@dataclass(frozen=True)
class SigningDispatch:
    signer_email: str
    signer_name: str
    signing_order: int
    token: SigningToken
    stored_token: StoredSigningToken
    signing_url: str


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
    ) -> None:
        self.config = config or ESignatureServiceConfig()
        self.storage_backend = storage_backend
        self.source_path_resolver = source_path_resolver
        self.asset_path_resolver = asset_path_resolver
        self.email_client = email_client
        self.envelope_repository = envelope_repository
        self.token_repository = token_repository

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

        source_pdf_path = self._resolve_pdf_path(req.input)
        source_hash = req.input.metadata.checksum_sha256 or sha256_file(source_pdf_path)

        state = existing_state or create_envelope_state(
            req.payload,
            source_document_sha256=source_hash,
            owner_email=sender_email or self._owner_email_from_request(req.payload),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        if req.payload.action == ESignatureAction.create_draft:
            state = self._apply_request_self_signature_if_present(
                request=req,
                state=state,
                current_pdf_path=current_pdf_path or source_pdf_path,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        elif req.payload.action == ESignatureAction.send:
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
                self._send_signing_invitations(
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
        from dataclasses import replace

        if state.status not in {ESignatureEnvelopeStatus.draft, ESignatureEnvelopeStatus.sent, ESignatureEnvelopeStatus.partially_signed}:
            raise ValueError(f"Cannot send envelope from status '{state.status.value}'.")

        recipients = tuple(
            self._recipient_with_status(recipient, ESignatureRecipientStatus.sent)
            if recipient.status == ESignatureRecipientStatus.pending
            else recipient
            for recipient in state.recipients
        )
        event = create_audit_event(
            event_type="envelope_sent",
            actor_email=actor_email or state.owner_email,
            ip_address=ip_address,
            user_agent=user_agent,
            document_sha256=state.source_document_sha256,
        )
        return replace(
            state,
            status=ESignatureEnvelopeStatus.sent,
            recipients=recipients,
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
        from dataclasses import replace

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
        from dataclasses import replace

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
    ) -> list[SigningDispatch]:
        if self.email_client is None:
            return []

        payload = request.payload
        if not isinstance(payload, ESignatureRequest):
            return []

        dispatches: list[SigningDispatch] = []

        for recipient in state.recipients:
            # Do not email the owner when they already self-signed.
            if recipient.role.value == "owner" and recipient.status == ESignatureRecipientStatus.signed:
                continue
            if recipient.status not in {ESignatureRecipientStatus.pending, ESignatureRecipientStatus.sent, ESignatureRecipientStatus.viewed}:
                continue

            token = create_signing_token(
                envelope_id=state.envelope_id,
                signer_email=recipient.email,
                expires_in_days=payload.expires_in_days,
                secret=self.config.token_secret,
            )
            stored = to_stored_token(token)
            if self.token_repository is not None:
                self.token_repository.save(stored)

            signing_url = build_signing_url(token.raw_token, base_url=self.config.signing_base_url)

            send_signing_invitation(
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

        return dispatches

    def _send_completion_notifications(self, request: AnalyzerRequest, state: EnvelopeState) -> None:
        if self.email_client is None or state.signed_pdf is None:
            return

        signed_url = state.signed_pdf.download_url
        certificate_url = state.audit_certificate.download_url if state.audit_certificate else None
        document_name = request.input.filename if isinstance(request.input, PdfFilePayload) else "document.pdf"

        for recipient in state.recipients:
            send_completion_email(
                email_client=self.email_client,
                recipient_email=recipient.email,
                recipient_name=recipient.name,
                document_name=document_name,
                download_url=signed_url,
                certificate_url=certificate_url,
            )

    # ------------------------------------------------------------------
    # Field / path / artifact helpers
    # ------------------------------------------------------------------

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

        return list(payload.fields) if payload.fields else selected

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

        return build_document_file_result(
            filename=filename,
            output_format=DocumentFileOutputFormat.pdf,
            file_size_mb=round(path.stat().st_size / (1024 * 1024), 4),
            storage_key=storage_key,
            download_url=download_url,
            algorithm_version=self.config.algorithm_version,
        )

    def _path_from_file_result(self, result: DocumentFileResult) -> Optional[Path]:
        candidates = [result.storage_key, result.download_url, result.filename]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                path = Path(candidate.strip()).expanduser()
                if path.exists() and path.is_file():
                    return path.resolve()
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
    "SourcePathResolver",
    "AssetPathResolver",
    "EnvelopeRepository",
    "SigningTokenRepository",
]
