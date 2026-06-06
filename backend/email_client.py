from __future__ import annotations

"""
Email client infrastructure for ReDOCX Sign.

This module is intentionally provider-agnostic:
- ConsoleEmailClient is safe for local development.
- SMTPEmailClient works with any SMTP provider.
- A future Resend/SendGrid/Postmark adapter can implement the same EmailClient protocol.
"""

from dataclasses import dataclass, field
from email.message import EmailMessage as SMTPEmailMessage
import os
import smtplib
from typing import Mapping, Optional, Protocol, Sequence


SMTP_HOST_ENV = "SMTP_HOST"
SMTP_PORT_ENV = "SMTP_PORT"
SMTP_USERNAME_ENV = "SMTP_USERNAME"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
SMTP_FROM_EMAIL_ENV = "SMTP_FROM_EMAIL"
SMTP_FROM_NAME_ENV = "SMTP_FROM_NAME"
SMTP_USE_TLS_ENV = "SMTP_USE_TLS"


@dataclass(frozen=True)
class EmailAddress:
    email: str
    name: Optional[str] = None

    def formatted(self) -> str:
        clean_email = self.email.strip()
        if self.name and self.name.strip():
            return f"{self.name.strip()} <{clean_email}>"
        return clean_email


@dataclass(frozen=True)
class EmailMessage:
    to: Sequence[EmailAddress]
    subject: str
    text_body: str
    html_body: Optional[str] = None
    from_email: Optional[EmailAddress] = None
    cc: Sequence[EmailAddress] = field(default_factory=tuple)
    bcc: Sequence[EmailAddress] = field(default_factory=tuple)
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmailSendResult:
    provider: str
    message_id: Optional[str]
    accepted_recipients: tuple[str, ...]


class EmailClient(Protocol):
    def send(self, message: EmailMessage) -> EmailSendResult:
        ...


class ConsoleEmailClient:
    """Development email client that logs emails instead of sending them."""

    provider = "console"

    def send(self, message: EmailMessage) -> EmailSendResult:
        recipients = tuple(address.email for address in message.to)
        print("=== ReDOCX email preview ===")
        print("To:", ", ".join(recipients))
        print("Subject:", message.subject)
        print(message.text_body)
        print("=== end email preview ===")
        return EmailSendResult(provider=self.provider, message_id=None, accepted_recipients=recipients)


@dataclass(frozen=True)
class SMTPEmailConfig:
    host: str
    port: int = 587
    username: Optional[str] = None
    password: Optional[str] = None
    from_email: str = "no-reply@redocx.com"
    from_name: str = "ReDOCX Sign"
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> "SMTPEmailConfig":
        host = os.getenv(SMTP_HOST_ENV, "").strip()
        if not host:
            raise RuntimeError(f"{SMTP_HOST_ENV} is required for SMTPEmailClient.")
        return cls(
            host=host,
            port=int(os.getenv(SMTP_PORT_ENV, "587")),
            username=os.getenv(SMTP_USERNAME_ENV) or None,
            password=os.getenv(SMTP_PASSWORD_ENV) or None,
            from_email=os.getenv(SMTP_FROM_EMAIL_ENV, "no-reply@redocx.com"),
            from_name=os.getenv(SMTP_FROM_NAME_ENV, "ReDOCX Sign"),
            use_tls=os.getenv(SMTP_USE_TLS_ENV, "true").strip().lower() not in {"0", "false", "no"},
        )


class SMTPEmailClient:
    provider = "smtp"

    def __init__(self, config: Optional[SMTPEmailConfig] = None) -> None:
        self.config = config or SMTPEmailConfig.from_env()

    def send(self, message: EmailMessage) -> EmailSendResult:
        if not message.to:
            raise ValueError("EmailMessage.to cannot be empty.")

        from_address = message.from_email or EmailAddress(
            email=self.config.from_email,
            name=self.config.from_name,
        )

        smtp_message = SMTPEmailMessage()
        smtp_message["From"] = from_address.formatted()
        smtp_message["To"] = ", ".join(address.formatted() for address in message.to)
        if message.cc:
            smtp_message["Cc"] = ", ".join(address.formatted() for address in message.cc)
        smtp_message["Subject"] = message.subject

        for key, value in message.headers.items():
            smtp_message[key] = value

        if message.html_body:
            smtp_message.set_content(message.text_body)
            smtp_message.add_alternative(message.html_body, subtype="html")
        else:
            smtp_message.set_content(message.text_body)

        recipients = [
            *(address.email for address in message.to),
            *(address.email for address in message.cc),
            *(address.email for address in message.bcc),
        ]

        with smtplib.SMTP(self.config.host, self.config.port, timeout=30) as server:
            if self.config.use_tls:
                server.starttls()
            if self.config.username and self.config.password:
                server.login(self.config.username, self.config.password)
            response = server.send_message(smtp_message, from_addr=from_address.email, to_addrs=recipients)

        rejected = set(response.keys())
        accepted = tuple(recipient for recipient in recipients if recipient not in rejected)
        return EmailSendResult(provider=self.provider, message_id=None, accepted_recipients=accepted)


def build_default_email_client() -> EmailClient:
    if os.getenv(SMTP_HOST_ENV, "").strip():
        return SMTPEmailClient()
    return ConsoleEmailClient()


def signing_invitation_message(
    *,
    signer_name: str,
    signer_email: str,
    document_name: str,
    signing_url: str,
    sender_name: Optional[str] = None,
    expires_at_iso: Optional[str] = None,
    subject: Optional[str] = None,
    message: Optional[str] = None,
) -> EmailMessage:
    resolved_subject = subject or f"Signature requested: {document_name}"
    intro = f"{sender_name} has requested your signature on {document_name}." if sender_name else f"You have been asked to sign {document_name}."
    expiry = f"\nThis signing link expires at: {expires_at_iso}" if expires_at_iso else ""
    custom = f"\n\nMessage from sender:\n{message.strip()}" if message and message.strip() else ""
    text = (
        f"Hello {signer_name},\n\n"
        f"{intro}\n\n"
        f"Review and sign here:\n{signing_url}"
        f"{expiry}"
        f"{custom}\n\n"
        "If you were not expecting this request, you can ignore this email.\n\n"
        "ReDOCX Sign"
    )
    html = (
        f"<p>Hello {signer_name},</p>"
        f"<p>{intro}</p>"
        f"<p><a href=\"{signing_url}\">Review and sign document</a></p>"
        f"<p>{'This signing link expires at: ' + expires_at_iso if expires_at_iso else ''}</p>"
        f"{'<p><strong>Message from sender:</strong><br>' + message.strip() + '</p>' if message and message.strip() else ''}"
        "<p>If you were not expecting this request, you can ignore this email.</p>"
        "<p>ReDOCX Sign</p>"
    )
    return EmailMessage(
        to=(EmailAddress(email=signer_email, name=signer_name),),
        subject=resolved_subject,
        text_body=text,
        html_body=html,
    )


def completion_message(
    *,
    recipient_email: str,
    recipient_name: str,
    document_name: str,
    download_url: Optional[str] = None,
    certificate_url: Optional[str] = None,
) -> EmailMessage:
    links = []
    if download_url:
        links.append(f"Signed PDF: {download_url}")
    if certificate_url:
        links.append(f"Certificate: {certificate_url}")

    link_text = "\n".join(links) if links else "The completed files are available in your ReDOCX account."
    text = (
        f"Hello {recipient_name},\n\n"
        f"The document '{document_name}' has been completed.\n\n"
        f"{link_text}\n\n"
        "ReDOCX Sign"
    )
    return EmailMessage(
        to=(EmailAddress(email=recipient_email, name=recipient_name),),
        subject=f"Completed: {document_name}",
        text_body=text,
    )


def send_signing_invitation(
    *,
    email_client: EmailClient,
    signer_name: str,
    signer_email: str,
    document_name: str,
    signing_url: str,
    sender_name: Optional[str] = None,
    expires_at_iso: Optional[str] = None,
    subject: Optional[str] = None,
    message: Optional[str] = None,
) -> EmailSendResult:
    return email_client.send(
        signing_invitation_message(
            signer_name=signer_name,
            signer_email=signer_email,
            document_name=document_name,
            signing_url=signing_url,
            sender_name=sender_name,
            expires_at_iso=expires_at_iso,
            subject=subject,
            message=message,
        )
    )


def send_completion_email(
    *,
    email_client: EmailClient,
    recipient_email: str,
    recipient_name: str,
    document_name: str,
    download_url: Optional[str] = None,
    certificate_url: Optional[str] = None,
) -> EmailSendResult:
    return email_client.send(
        completion_message(
            recipient_email=recipient_email,
            recipient_name=recipient_name,
            document_name=document_name,
            download_url=download_url,
            certificate_url=certificate_url,
        )
    )


__all__ = [
    "EmailAddress",
    "EmailMessage",
    "EmailSendResult",
    "EmailClient",
    "ConsoleEmailClient",
    "SMTPEmailConfig",
    "SMTPEmailClient",
    "build_default_email_client",
    "signing_invitation_message",
    "completion_message",
    "send_signing_invitation",
    "send_completion_email",
]
