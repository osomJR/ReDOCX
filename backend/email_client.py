from __future__ import annotations

"""
Email client infrastructure for ReDOCX Sign.

This module is intentionally provider-agnostic:
- ConsoleEmailClient is safe for local development.
- SMTPEmailClient works with any SMTP provider.
- ZeptoMailEmailClient sends production transactional email over ZeptoMail's HTTPS API.
"""

from dataclasses import dataclass, field
from email.message import EmailMessage as SMTPEmailMessage
import json
import os
import smtplib
import logging
from typing import Any, Mapping, Optional, Protocol, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest


EMAIL_PROVIDER_ENV = "EMAIL_PROVIDER"

SMTP_HOST_ENV = "SMTP_HOST"
SMTP_PORT_ENV = "SMTP_PORT"
SMTP_USERNAME_ENV = "SMTP_USERNAME"
SMTP_PASSWORD_ENV = "SMTP_PASSWORD"
SMTP_FROM_EMAIL_ENV = "SMTP_FROM_EMAIL"
SMTP_FROM_NAME_ENV = "SMTP_FROM_NAME"
SMTP_USE_TLS_ENV = "SMTP_USE_TLS"

ZEPTOMAIL_SEND_MAIL_TOKEN_ENV = "ZEPTOMAIL_SEND_MAIL_TOKEN"
ZEPTOMAIL_API_URL_ENV = "ZEPTOMAIL_API_URL"
ZEPTOMAIL_FROM_EMAIL_ENV = "ZEPTOMAIL_FROM_EMAIL"
ZEPTOMAIL_FROM_NAME_ENV = "ZEPTOMAIL_FROM_NAME"
ZEPTOMAIL_TIMEOUT_SECONDS_ENV = "ZEPTOMAIL_TIMEOUT_SECONDS"
ZEPTOMAIL_SEND_HTML_ENV = "ZEPTOMAIL_SEND_HTML"

DEFAULT_ZEPTOMAIL_API_URL = "https://api.zeptomail.com/v1.1/email"

logger = logging.getLogger(__name__)


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



@dataclass(frozen=True)
class ZeptoMailEmailConfig:
    send_mail_token: str
    api_url: str = DEFAULT_ZEPTOMAIL_API_URL
    from_email: str = ""
    from_name: str = "ReDOCX Sign"
    timeout_seconds: float = 20.0
    send_html: bool = False

    @classmethod
    def from_env(cls) -> "ZeptoMailEmailConfig":
        token = os.getenv(ZEPTOMAIL_SEND_MAIL_TOKEN_ENV, "").strip()
        if not token:
            raise RuntimeError(
                f"{ZEPTOMAIL_SEND_MAIL_TOKEN_ENV} is required for ZeptoMailEmailClient."
            )

        raw_timeout = os.getenv(ZEPTOMAIL_TIMEOUT_SECONDS_ENV, "20").strip() or "20"
        try:
            timeout_seconds = float(raw_timeout)
        except ValueError as exc:
            raise RuntimeError(
                f"{ZEPTOMAIL_TIMEOUT_SECONDS_ENV} must be a number of seconds."
            ) from exc

        from_email = os.getenv(ZEPTOMAIL_FROM_EMAIL_ENV, "").strip()
        if not from_email:
            raise RuntimeError(
                f"{ZEPTOMAIL_FROM_EMAIL_ENV} is required for ZeptoMailEmailClient. "
                "Use a verified sender address from the same ZeptoMail Mail Agent, "
                "for example donotreply@redocx.app."
            )

        return cls(
            send_mail_token=token,
            api_url=os.getenv(ZEPTOMAIL_API_URL_ENV, DEFAULT_ZEPTOMAIL_API_URL).strip()
            or DEFAULT_ZEPTOMAIL_API_URL,
            from_email=from_email,
            from_name=(
                os.getenv(ZEPTOMAIL_FROM_NAME_ENV)
                or "ReDOCX Sign"
            ).strip(),
            timeout_seconds=timeout_seconds,
            send_html=os.getenv(ZEPTOMAIL_SEND_HTML_ENV, "false").strip().lower()
            in {"1", "true", "yes"},
        )


class ZeptoMailEmailClient:
    provider = "zeptomail"

    def __init__(self, config: Optional[ZeptoMailEmailConfig] = None) -> None:
        self.config = config or ZeptoMailEmailConfig.from_env()

    @staticmethod
    def _email_address_payload(address: EmailAddress) -> dict[str, dict[str, str]]:
        clean_email = address.email.strip()
        payload: dict[str, str] = {"address": clean_email}
        if address.name and address.name.strip():
            payload["name"] = address.name.strip()
        return {"email_address": payload}

    @staticmethod
    def _extract_message_id(payload: Any) -> Optional[str]:
        if isinstance(payload, Mapping):
            for key in ("message_id", "request_id", "id"):
                value = payload.get(key)
                if value:
                    return str(value)

            data = payload.get("data")
            if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
                for item in data:
                    nested_id = ZeptoMailEmailClient._extract_message_id(item)
                    if nested_id:
                        return nested_id
            if isinstance(data, Mapping):
                nested_id = ZeptoMailEmailClient._extract_message_id(data)
                if nested_id:
                    return nested_id

        return None

    @staticmethod
    def _payload_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
        from_payload = payload.get("from") if isinstance(payload, Mapping) else {}
        to_payload = payload.get("to") if isinstance(payload, Mapping) else []
        cc_payload = payload.get("cc") if isinstance(payload, Mapping) else []
        bcc_payload = payload.get("bcc") if isinstance(payload, Mapping) else []

        def _addresses(items: Any) -> list[str]:
            addresses: list[str] = []
            if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    email_address = item.get("email_address")
                    if isinstance(email_address, Mapping):
                        address = email_address.get("address")
                        if address:
                            addresses.append(str(address))
            return addresses

        return {
            "api_url": str(payload.get("_api_url", "")),
            "from": {
                "address": str(from_payload.get("address", "")) if isinstance(from_payload, Mapping) else "",
                "name": str(from_payload.get("name", "")) if isinstance(from_payload, Mapping) else "",
            },
            "to": _addresses(to_payload),
            "cc": _addresses(cc_payload),
            "bcc_count": len(_addresses(bcc_payload)),
            "subject": str(payload.get("subject", "")),
            "has_textbody": bool(payload.get("textbody")),
            "textbody_length": len(str(payload.get("textbody", ""))),
            "has_htmlbody": bool(payload.get("htmlbody")),
            "htmlbody_length": len(str(payload.get("htmlbody", ""))) if payload.get("htmlbody") else 0,
        }

    def _build_payload(self, message: EmailMessage, from_address: EmailAddress) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "from": {
                "address": from_address.email.strip(),
                "name": from_address.name or self.config.from_name,
            },
            "to": [self._email_address_payload(address) for address in message.to],
            "subject": message.subject,
            "textbody": message.text_body,
        }

        if self.config.send_html and message.html_body:
            payload["htmlbody"] = message.html_body

        if message.cc:
            payload["cc"] = [self._email_address_payload(address) for address in message.cc]

        if message.bcc:
            payload["bcc"] = [self._email_address_payload(address) for address in message.bcc]

        for header_key, payload_key in (
            ("X-ZeptoMail-Track-Opens", "track_opens"),
            ("X-ZeptoMail-Track-Clicks", "track_clicks"),
        ):
            raw_value = message.headers.get(header_key)
            if raw_value is not None:
                payload[payload_key] = str(raw_value).strip().lower() in {"1", "true", "yes"}

        return payload

    def send(self, message: EmailMessage) -> EmailSendResult:
        if not message.to:
            raise ValueError("EmailMessage.to cannot be empty.")

        from_address = message.from_email or EmailAddress(
            email=self.config.from_email,
            name=self.config.from_name,
        )

        payload = self._build_payload(message, from_address)
        summary_payload = {**payload, "_api_url": self.config.api_url}
        payload_summary = self._payload_summary(summary_payload)

        body = json.dumps(payload).encode("utf-8")
        request = urlrequest.Request(
            self.config.api_url,
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Zoho-enczapikey {self.config.send_mail_token}",
            },
        )

        try:
            with urlrequest.urlopen(request, timeout=self.config.timeout_seconds) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                parsed_response = json.loads(response_body) if response_body else {}
        except urlerror.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "ZeptoMail API request failed.",
                extra={
                    "provider": self.provider,
                    "status_code": exc.code,
                    "response_body": error_body,
                    "payload_summary": payload_summary,
                },
            )
            raise RuntimeError(
                f"ZeptoMail API request failed with HTTP {exc.code}: {error_body or '<empty response body>'}; "
                f"payload_summary={json.dumps(payload_summary, sort_keys=True)}"
            ) from exc
        except urlerror.URLError as exc:
            logger.warning(
                "ZeptoMail API request failed.",
                extra={
                    "provider": self.provider,
                    "reason": str(exc.reason),
                    "payload_summary": payload_summary,
                },
            )
            raise RuntimeError(
                f"ZeptoMail API request failed: {exc.reason}; "
                f"payload_summary={json.dumps(payload_summary, sort_keys=True)}"
            ) from exc
        except TimeoutError as exc:
            logger.warning(
                "ZeptoMail API request timed out.",
                extra={
                    "provider": self.provider,
                    "payload_summary": payload_summary,
                },
            )
            raise RuntimeError(
                "ZeptoMail API request timed out; "
                f"payload_summary={json.dumps(payload_summary, sort_keys=True)}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("ZeptoMail API returned an invalid JSON response.") from exc

        recipients = tuple(
            address.email.strip()
            for address in (*message.to, *message.cc, *message.bcc)
            if address.email and address.email.strip()
        )

        return EmailSendResult(
            provider=self.provider,
            message_id=self._extract_message_id(parsed_response),
            accepted_recipients=recipients,
        )


def build_default_email_client() -> EmailClient:
    provider = os.getenv(EMAIL_PROVIDER_ENV, "").strip().lower()

    if provider in {"zeptomail", "zepto", "zoho_zeptomail"}:
        return ZeptoMailEmailClient()

    if provider == "smtp":
        return SMTPEmailClient()

    if provider == "console":
        return ConsoleEmailClient()

    if os.getenv(ZEPTOMAIL_SEND_MAIL_TOKEN_ENV, "").strip():
        return ZeptoMailEmailClient()

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
    "ZeptoMailEmailConfig",
    "ZeptoMailEmailClient",
    "build_default_email_client",
    "signing_invitation_message",
    "completion_message",
    "send_signing_invitation",
    "send_completion_email",
]
