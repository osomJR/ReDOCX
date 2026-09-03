from __future__ import annotations

"""
ReDOCX E-Signature envelope lifecycle helpers.

This module is intentionally storage-agnostic. It does not talk to FastAPI,
PostgreSQL, object storage, or email providers directly. The service layer should
persist EnvelopeState snapshots or map them to database rows.

Responsibilities:
- create an envelope state from schema.ESignatureRequest
- derive signer/recipient status
- enforce lifecycle transitions
- construct schema-aligned ESignatureResult objects
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Optional
from uuid import uuid4

try:  # Preferred when used inside your backend package.
    from backend.src.schema import (
        AnalyzerRequest,
        ArchiveFileResult,
        DocumentFileResult,
        ESignatureAuditEvent,
        ESignatureDocumentResult,
        ESignatureEnvelopeStatus,
        ESignatureField,
        ESignatureRecipient,
        ESignatureRecipientResult,
        ESignatureRecipientRole,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureResult,
        ESignatureStepPreview,
        ESignatureWorkflow,
        PAdESSignatureInfo,
    )
    from backend.src.validation import build_esignature_result
except ImportError:  # Useful for direct/unit-test imports inside the src package.
    from ...schema import (
        AnalyzerRequest,
        ArchiveFileResult,
        DocumentFileResult,
        ESignatureAuditEvent,
        ESignatureDocumentResult,
        ESignatureEnvelopeStatus,
        ESignatureField,
        ESignatureRecipient,
        ESignatureRecipientResult,
        ESignatureRecipientRole,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureResult,
        ESignatureStepPreview,
        ESignatureWorkflow,
        PAdESSignatureInfo,
    )
    from ...validation import build_esignature_result

from .audit import create_audit_event
from .fields import (
    recipient_results_for_request,
    required_signer_emails,
    sorted_recipient_results,
)


@dataclass(frozen=True)
class EnvelopeDocumentState:
    """One independently versioned PDF in an envelope."""

    document_id: str
    filename: str
    source_sha256: str
    current_sha256: str
    signed_pdf: Optional[DocumentFileResult] = None
    pades_signature: Optional[PAdESSignatureInfo] = None

    def to_result(self) -> ESignatureDocumentResult:
        return ESignatureDocumentResult(
            document_id=self.document_id,
            filename=self.filename,
            source_sha256=self.source_sha256,
            final_sha256=(self.current_sha256 if self.signed_pdf is not None else None),
            signed_pdf=self.signed_pdf,
            pades_signature=self.pades_signature,
        )


@dataclass(frozen=True)
class EnvelopeState:
    """
    In-memory/domain representation of one signing envelope.

    Persist this model directly, or map its fields to normalized DB tables:
    envelopes, recipients, fields, audit_events, previews, files.
    """

    envelope_id: str
    workflow: ESignatureWorkflow
    status: ESignatureEnvelopeStatus
    recipients: tuple[ESignatureRecipientResult, ...]
    fields: tuple[ESignatureField, ...]
    created_at_iso: str
    updated_at_iso: str
    expires_at_iso: Optional[str] = None
    audit_events: tuple[ESignatureAuditEvent, ...] = field(default_factory=tuple)
    previews: tuple[ESignatureStepPreview, ...] = field(default_factory=tuple)
    documents: tuple[EnvelopeDocumentState, ...] = field(default_factory=tuple)
    signed_bundle: Optional[ArchiveFileResult] = None
    signed_pdf: Optional[DocumentFileResult] = None
    audit_certificate: Optional[DocumentFileResult] = None
    source_document_sha256: Optional[str] = None
    owner_email: Optional[str] = None
    owner_user_id: Optional[str] = None
    owner_organization_id: Optional[str] = None
    # Persist the original validated analyzer request with the envelope. External
    # recipients must sign against the exact field/routing contract created by
    # the sender; reconstructing it from recipient rows loses routing mode,
    # required flags, the source PDF reference, and email settings.
    source_request: Optional[AnalyzerRequest] = None


class EnvelopeTransitionError(ValueError):
    """Raised when an invalid e-signature lifecycle transition is requested."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_envelope_state(
    request: ESignatureRequest,
    *,
    envelope_id: Optional[str] = None,
    documents: Iterable[EnvelopeDocumentState] = (),
    source_document_sha256: Optional[str] = None,
    owner_email: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    owner_organization_id: Optional[str] = None,
    expires_at_iso: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    source_request: Optional[AnalyzerRequest] = None,
) -> EnvelopeState:
    """
    Create a draft envelope from a validated ESignatureRequest.

    The returned state includes initial audit events:
    - envelope_created
    - document_uploaded when a source hash is available
    - field_added for each placed field
    """
    now = utcnow_iso()
    resolved_envelope_id = envelope_id or f"env_{uuid4().hex}"
    resolved_documents = tuple(documents)
    if not resolved_documents and source_document_sha256:
        resolved_documents = (
            EnvelopeDocumentState(
                document_id="document_1",
                filename="document.pdf",
                source_sha256=source_document_sha256,
                current_sha256=source_document_sha256,
            ),
        )

    recipients = tuple(
        sorted_recipient_results(
            recipient_results_for_request(
                request,
                default_status=ESignatureRecipientStatus.pending,
            )
        )
    )

    events = [
        create_audit_event(
            event_type="envelope_created",
            actor_email=owner_email,
            ip_address=ip_address,
            user_agent=user_agent,
            document_sha256=source_document_sha256,
            created_at_iso=now,
        )
    ]

    for document in resolved_documents:
        events.append(
            create_audit_event(
                event_type="document_uploaded",
                actor_email=owner_email,
                ip_address=ip_address,
                user_agent=user_agent,
                document_id=document.document_id,
                document_sha256=document.source_sha256,
                created_at_iso=now,
            )
        )

    for field_item in request.fields:
        field_document_id = field_item.document_id or (
            resolved_documents[0].document_id if resolved_documents else "document_1"
        )
        events.append(
            create_audit_event(
                event_type="field_added",
                actor_email=owner_email,
                ip_address=ip_address,
                user_agent=user_agent,
                document_id=field_document_id,
                document_sha256=next(
                    (
                        item.current_sha256
                        for item in resolved_documents
                        if item.document_id == field_document_id
                    ),
                    source_document_sha256,
                ),
                created_at_iso=now,
            )
        )

    return EnvelopeState(
        envelope_id=resolved_envelope_id,
        workflow=request.workflow,
        status=ESignatureEnvelopeStatus.draft,
        recipients=recipients,
        fields=tuple(request.fields),
        created_at_iso=now,
        updated_at_iso=now,
        expires_at_iso=expires_at_iso,
        audit_events=tuple(events),
        documents=resolved_documents,
        source_document_sha256=source_document_sha256,
        owner_email=owner_email,
        owner_user_id=owner_user_id,
        owner_organization_id=owner_organization_id,
        source_request=source_request,
    )


def mark_envelope_sent(
    state: EnvelopeState,
    *,
    actor_email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> EnvelopeState:
    if state.status not in {ESignatureEnvelopeStatus.draft, ESignatureEnvelopeStatus.sent}:
        raise EnvelopeTransitionError(f"Cannot send envelope from status '{state.status.value}'.")

    now = utcnow_iso()
    recipients = tuple(
        replace(recipient, status=ESignatureRecipientStatus.sent)
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
        created_at_iso=now,
    )
    return replace(
        state,
        status=ESignatureEnvelopeStatus.sent,
        recipients=recipients,
        updated_at_iso=now,
        audit_events=(*state.audit_events, event),
    )


def mark_signer_viewed(
    state: EnvelopeState,
    *,
    signer_email: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> EnvelopeState:
    if state.status in {ESignatureEnvelopeStatus.completed, ESignatureEnvelopeStatus.voided, ESignatureEnvelopeStatus.expired}:
        raise EnvelopeTransitionError(f"Cannot view envelope from status '{state.status.value}'.")

    normalized = signer_email.strip().lower()
    now = utcnow_iso()
    found = False
    recipients: list[ESignatureRecipientResult] = []
    for recipient in state.recipients:
        if recipient.email.lower() == normalized:
            found = True
            if recipient.status in {ESignatureRecipientStatus.pending, ESignatureRecipientStatus.sent}:
                recipients.append(replace(recipient, status=ESignatureRecipientStatus.viewed))
            else:
                recipients.append(recipient)
        else:
            recipients.append(recipient)

    if not found:
        raise EnvelopeTransitionError(f"Unknown signer email: {signer_email}")

    event = create_audit_event(
        event_type="signer_viewed",
        actor_email=normalized,
        ip_address=ip_address,
        user_agent=user_agent,
        document_sha256=state.source_document_sha256,
        created_at_iso=now,
    )

    return replace(
        state,
        status=ESignatureEnvelopeStatus.viewed if state.status == ESignatureEnvelopeStatus.sent else state.status,
        recipients=tuple(recipients),
        updated_at_iso=now,
        audit_events=(*state.audit_events, event),
    )


def mark_signer_signed(
    state: EnvelopeState,
    *,
    signer_email: str,
    preview: Optional[ESignatureStepPreview] = None,
    signed_pdf: Optional[DocumentFileResult] = None,
    document_sha256: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> EnvelopeState:
    if state.status in {ESignatureEnvelopeStatus.completed, ESignatureEnvelopeStatus.voided, ESignatureEnvelopeStatus.expired}:
        raise EnvelopeTransitionError(f"Cannot sign envelope from status '{state.status.value}'.")

    normalized = signer_email.strip().lower()
    now = utcnow_iso()
    found = False
    recipients: list[ESignatureRecipientResult] = []
    for recipient in state.recipients:
        if recipient.email.lower() == normalized:
            found = True
            recipients.append(replace(recipient, status=ESignatureRecipientStatus.signed))
        else:
            recipients.append(recipient)

    if not found:
        raise EnvelopeTransitionError(f"Unknown signer email: {signer_email}")

    signer_event = create_audit_event(
        event_type="signer_signed",
        actor_email=normalized,
        ip_address=ip_address,
        user_agent=user_agent,
        document_sha256=document_sha256 or state.source_document_sha256,
        created_at_iso=now,
    )

    events = [*state.audit_events, signer_event]
    previews = list(state.previews)
    if preview is not None:
        previews.append(preview)
        events.append(
            create_audit_event(
                event_type="preview_generated",
                actor_email=normalized,
                ip_address=ip_address,
                user_agent=user_agent,
                document_sha256=document_sha256 or state.source_document_sha256,
                created_at_iso=preview.created_at_iso,
            )
        )

    next_status = (
        ESignatureEnvelopeStatus.completed
        if all(r.status == ESignatureRecipientStatus.signed for r in recipients)
        else ESignatureEnvelopeStatus.partially_signed
    )

    return replace(
        state,
        status=next_status,
        recipients=tuple(recipients),
        previews=tuple(previews),
        signed_pdf=signed_pdf or state.signed_pdf,
        # Source identity is immutable; final revision hashes live on documents.
        source_document_sha256=state.source_document_sha256,
        updated_at_iso=now,
        audit_events=tuple(events),
    )


def complete_envelope(
    state: EnvelopeState,
    *,
    signed_pdf: DocumentFileResult,
    audit_certificate: DocumentFileResult,
    documents: Optional[Iterable[EnvelopeDocumentState]] = None,
    signed_bundle: Optional[ArchiveFileResult] = None,
    actor_email: Optional[str] = None,
    document_sha256: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> EnvelopeState:
    unsigned = [r.email for r in state.recipients if r.status != ESignatureRecipientStatus.signed]
    if unsigned:
        raise EnvelopeTransitionError(f"Cannot complete envelope with unsigned recipients: {', '.join(unsigned)}")

    now = utcnow_iso()
    event = create_audit_event(
        event_type="envelope_completed",
        actor_email=actor_email or state.owner_email,
        ip_address=ip_address,
        user_agent=user_agent,
        document_sha256=document_sha256 or state.source_document_sha256,
        created_at_iso=now,
    )
    return replace(
        state,
        status=ESignatureEnvelopeStatus.completed,
        signed_pdf=signed_pdf,
        documents=tuple(documents) if documents is not None else state.documents,
        signed_bundle=signed_bundle or state.signed_bundle,
        audit_certificate=audit_certificate,
        source_document_sha256=state.source_document_sha256,
        updated_at_iso=now,
        audit_events=(*state.audit_events, event),
    )


def void_envelope(
    state: EnvelopeState,
    *,
    actor_email: Optional[str],
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> EnvelopeState:
    if state.status == ESignatureEnvelopeStatus.completed:
        raise EnvelopeTransitionError("Completed envelopes cannot be voided.")

    now = utcnow_iso()
    event = create_audit_event(
        event_type="envelope_voided",
        actor_email=actor_email,
        ip_address=ip_address,
        user_agent=f"{user_agent or ''} reason={reason or 'not_provided'}".strip(),
        document_sha256=state.source_document_sha256,
        created_at_iso=now,
    )
    return replace(
        state,
        status=ESignatureEnvelopeStatus.voided,
        updated_at_iso=now,
        audit_events=(*state.audit_events, event),
    )


def next_required_signers(state: EnvelopeState) -> tuple[ESignatureRecipientResult, ...]:
    """
    Return the next signer(s) for a sequential envelope.

    This function is conservative and uses signing_order. Parallel routing should
    simply return all recipients that are not signed.
    """
    pending = [
        recipient
        for recipient in state.recipients
        if recipient.status != ESignatureRecipientStatus.signed
    ]
    if not pending:
        return tuple()
    minimum_order = min(item.signing_order for item in pending)
    return tuple(item for item in pending if item.signing_order == minimum_order)


def build_envelope_result(
    state: EnvelopeState,
    *,
    algorithm_version: Optional[str] = None,
) -> ESignatureResult:
    previews = list(state.previews)
    latest_preview = previews[-1] if previews else None
    return build_esignature_result(
        envelope_id=state.envelope_id,
        workflow=state.workflow,
        status=state.status,
        recipients=list(state.recipients),
        latest_preview=latest_preview,
        previews=previews,
        documents=[document.to_result() for document in state.documents],
        signed_bundle=state.signed_bundle,
        signed_pdf=state.signed_pdf,
        audit_certificate=state.audit_certificate,
        audit_events=list(state.audit_events),
        algorithm_version=algorithm_version,
    )


__all__ = [
    "EnvelopeDocumentState",
    "EnvelopeState",
    "EnvelopeTransitionError",
    "utcnow_iso",
    "create_envelope_state",
    "mark_envelope_sent",
    "mark_signer_viewed",
    "mark_signer_signed",
    "complete_envelope",
    "void_envelope",
    "next_required_signers",
    "build_envelope_result",
]
