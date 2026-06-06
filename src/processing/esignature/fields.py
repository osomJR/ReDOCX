from __future__ import annotations

"""
ReDOCX E-Signature field and signer validation helpers.

Responsibilities:
- normalize signer identity
- validate field assignment, page bounds, and duplicate field ids
- derive schema.ESignatureRecipientResult objects
- select fields for a specific signer during signing
"""

from dataclasses import dataclass
from typing import Iterable, Optional

try:
    from src.schema import (
        ESignatureField,
        ESignatureFieldType,
        ESignatureRecipient,
        ESignatureRecipientResult,
        ESignatureRecipientRole,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureSelfSigner,
    )
except ImportError:
    from ...schema import (
        ESignatureField,
        ESignatureFieldType,
        ESignatureRecipient,
        ESignatureRecipientResult,
        ESignatureRecipientRole,
        ESignatureRecipientStatus,
        ESignatureRequest,
        ESignatureSelfSigner,
    )


@dataclass(frozen=True)
class SignerIdentity:
    name: str
    email: str
    role: ESignatureRecipientRole
    signing_order: int
    required: bool = True


def normalize_email(value: str) -> str:
    normalized = (value or "").strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("A valid signer email is required.")
    return normalized


def self_signer_identity(self_signer: ESignatureSelfSigner) -> SignerIdentity:
    return SignerIdentity(
        name=self_signer.name,
        email=normalize_email(self_signer.email),
        role=ESignatureRecipientRole.owner,
        signing_order=1,
        required=True,
    )


def recipient_identity(recipient: ESignatureRecipient) -> SignerIdentity:
    return SignerIdentity(
        name=recipient.name,
        email=normalize_email(recipient.email),
        role=recipient.role,
        signing_order=recipient.signing_order,
        required=recipient.required,
    )


def signer_identities(request: ESignatureRequest) -> list[SignerIdentity]:
    identities: list[SignerIdentity] = []
    if request.self_signer is not None:
        identities.append(self_signer_identity(request.self_signer))
    identities.extend(recipient_identity(item) for item in request.recipients)
    return identities


def known_signer_emails(request: ESignatureRequest) -> set[str]:
    return {item.email for item in signer_identities(request)}


def required_signer_emails(request: ESignatureRequest) -> set[str]:
    return {item.email for item in signer_identities(request) if item.required}


def signer_name_for_email(request: ESignatureRequest, email: str) -> str:
    normalized = normalize_email(email)
    for identity in signer_identities(request):
        if identity.email == normalized:
            return identity.name
    raise ValueError(f"Unknown signer email: {email}")


def signer_order_for_email(request: ESignatureRequest, email: str) -> int:
    normalized = normalize_email(email)
    for identity in signer_identities(request):
        if identity.email == normalized:
            return identity.signing_order
    raise ValueError(f"Unknown signer email: {email}")


def validate_field_collection(
    fields: Iterable[ESignatureField],
    *,
    known_emails: set[str],
    page_count: Optional[int] = None,
) -> list[ESignatureField]:
    resolved = list(fields)
    field_ids = [item.field_id for item in resolved if item.field_id]
    if len(set(field_ids)) != len(field_ids):
        raise ValueError("E-signature field_id values must be unique.")

    unknown = {normalize_email(item.assigned_to_email) for item in resolved} - known_emails
    if unknown:
        raise ValueError(f"Field assigned to unknown signer(s): {', '.join(sorted(unknown))}")

    if page_count is not None:
        invalid_pages = [item.page_number for item in resolved if item.page_number > page_count]
        if invalid_pages:
            raise ValueError(f"Field page_number exceeds PDF page_count: {max(invalid_pages)} > {page_count}")

    return resolved


def fields_for_signer(
    fields: Iterable[ESignatureField],
    signer_email: str,
    *,
    required_only: bool = False,
) -> list[ESignatureField]:
    normalized = normalize_email(signer_email)
    selected = [
        item
        for item in fields
        if normalize_email(item.assigned_to_email) == normalized
        and (not required_only or item.required)
    ]
    return sorted(selected, key=lambda item: (item.page_number, item.rectangle.y, item.rectangle.x))


def signable_fields_for_signer(
    fields: Iterable[ESignatureField],
    signer_email: str,
) -> list[ESignatureField]:
    return [
        item for item in fields_for_signer(fields, signer_email)
        if item.field_type in {ESignatureFieldType.signature, ESignatureFieldType.initials}
    ]


def validate_required_signers_have_signable_fields(request: ESignatureRequest) -> None:
    signable_assignees = {
        normalize_email(field.assigned_to_email)
        for field in request.fields
        if field.field_type in {ESignatureFieldType.signature, ESignatureFieldType.initials}
    }

    if request.self_signer and request.self_signer.signature is not None:
        signable_assignees.add(normalize_email(request.self_signer.email))

    missing = required_signer_emails(request) - signable_assignees
    if missing:
        raise ValueError(
            "Each required signer needs a signature/initials field or self_signer.signature. "
            f"Missing: {', '.join(sorted(missing))}"
        )


def recipient_results_for_request(
    request: ESignatureRequest,
    *,
    default_status: ESignatureRecipientStatus = ESignatureRecipientStatus.pending,
) -> list[ESignatureRecipientResult]:
    results: list[ESignatureRecipientResult] = []

    if request.self_signer is not None:
        status = (
            ESignatureRecipientStatus.signed
            if request.self_signer.signature is not None
            else default_status
        )
        results.append(
            ESignatureRecipientResult(
                name=request.self_signer.name,
                email=request.self_signer.email,
                role=ESignatureRecipientRole.owner,
                signing_order=1,
                status=status,
            )
        )

    for recipient in request.recipients:
        results.append(
            ESignatureRecipientResult(
                name=recipient.name,
                email=recipient.email,
                role=recipient.role,
                signing_order=recipient.signing_order,
                status=default_status,
            )
        )

    return sorted_recipient_results(results)


def sorted_recipient_results(
    recipients: Iterable[ESignatureRecipientResult],
) -> list[ESignatureRecipientResult]:
    return sorted(
        list(recipients),
        key=lambda item: (item.signing_order, item.email.lower()),
    )


def update_recipient_status(
    recipients: Iterable[ESignatureRecipientResult],
    *,
    signer_email: str,
    status: ESignatureRecipientStatus,
) -> list[ESignatureRecipientResult]:
    normalized = normalize_email(signer_email)
    updated: list[ESignatureRecipientResult] = []
    found = False
    for recipient in recipients:
        if recipient.email.lower() == normalized:
            found = True
            updated.append(
                ESignatureRecipientResult(
                    name=recipient.name,
                    email=recipient.email,
                    role=recipient.role,
                    signing_order=recipient.signing_order,
                    status=status,
                )
            )
        else:
            updated.append(recipient)
    if not found:
        raise ValueError(f"Unknown signer email: {signer_email}")
    return sorted_recipient_results(updated)


__all__ = [
    "SignerIdentity",
    "normalize_email",
    "self_signer_identity",
    "recipient_identity",
    "signer_identities",
    "known_signer_emails",
    "required_signer_emails",
    "signer_name_for_email",
    "signer_order_for_email",
    "validate_field_collection",
    "fields_for_signer",
    "signable_fields_for_signer",
    "validate_required_signers_have_signable_fields",
    "recipient_results_for_request",
    "sorted_recipient_results",
    "update_recipient_status",
]
