from __future__ import annotations

"""X.509/PAdES completion seals for ReDOCX Sign.

Visible signer marks are applied before this module is called. The final PDF
revision is then signed incrementally, so the CMS signature covers every byte
of the completed document. The certificate is an organisation/platform seal;
the envelope audit trail records the human signers and their consent events.

This module deliberately fails closed. A PDF is never described as PAdES-signed
unless pyHanko validates its integrity, CMS signature and X.509 trust chain.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Iterable, Optional
from urllib.parse import urlsplit

try:
    from backend.src.schema import PAdESProfile, PAdESSignatureInfo
except ImportError:  # pragma: no cover - direct src-package import
    from ...schema import PAdESProfile, PAdESSignatureInfo


class PAdESConfigurationError(RuntimeError):
    """Raised before signing when the production seal profile is incomplete."""


class PAdESSealError(RuntimeError):
    """Raised when signing or post-signature verification fails."""


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PAdESConfigurationError(f"{name} must be true or false.")


def _secret_from_env_or_file(*, value_name: str, file_name: str) -> Optional[str]:
    secret_file = os.getenv(file_name, "").strip()
    direct = os.getenv(value_name)
    if secret_file and direct is not None:
        raise PAdESConfigurationError(
            f"Configure only one of {value_name} or {file_name}."
        )
    if secret_file:
        path = Path(secret_file).expanduser()
        if not path.is_file():
            raise PAdESConfigurationError(f"{file_name} does not identify a file.")
        return path.read_text(encoding="utf-8").rstrip("\r\n")
    return direct


def _path_list(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return tuple()
    # Comma is the documented separator. os.pathsep is also accepted for
    # conventional secret/config mounts on the current platform.
    separator = "," if "," in raw else os.pathsep
    values = raw.split(separator)
    return tuple(value.strip() for value in values if value.strip())


def _https_url(value: Optional[str], *, name: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise PAdESConfigurationError(f"{name} must be an HTTPS URL without credentials.")
    return normalized


@dataclass(frozen=True)
class PAdESConfig:
    enabled: bool
    profile: PAdESProfile
    pkcs12_path: Optional[str]
    pkcs12_password: Optional[str]
    tsa_url: Optional[str]
    trust_root_paths: tuple[str, ...]
    allow_revocation_fetching: bool = True
    revocation_mode: str = "hard-fail"
    require_trusted_certificate: bool = True
    tsa_timeout_seconds: int = 10
    reason: str = "ReDOCX envelope completion seal"
    location: Optional[str] = None
    contact_info: Optional[str] = None

    @classmethod
    def disabled(cls) -> "PAdESConfig":
        """Return a non-signing config for read-only envelope endpoints."""
        return cls(
            enabled=False,
            profile=PAdESProfile.baseline_b,
            pkcs12_path=None,
            pkcs12_password=None,
            tsa_url=None,
            trust_root_paths=tuple(),
        )

    @classmethod
    def from_env(cls, *, production: bool = False) -> "PAdESConfig":
        profile_value = os.getenv("ESIGN_PADES_PROFILE", "B-LTA").strip().upper()
        try:
            profile = PAdESProfile(profile_value)
        except ValueError as exc:
            raise PAdESConfigurationError(
                "ESIGN_PADES_PROFILE must be one of B-B, B-T, B-LT, or B-LTA."
            ) from exc

        default_revocation_mode = (
            "require"
            if production and profile in {PAdESProfile.baseline_lt, PAdESProfile.baseline_lta}
            else "hard-fail"
        )
        timeout_raw = os.getenv("ESIGN_PADES_TSA_TIMEOUT_SECONDS", "10").strip()
        try:
            tsa_timeout_seconds = int(timeout_raw)
        except ValueError as exc:
            raise PAdESConfigurationError(
                "ESIGN_PADES_TSA_TIMEOUT_SECONDS must be an integer."
            ) from exc

        config = cls(
            enabled=_bool_env("ESIGN_PADES_ENABLED", production),
            profile=profile,
            pkcs12_path=os.getenv("ESIGN_PADES_PKCS12_PATH", "").strip() or None,
            pkcs12_password=_secret_from_env_or_file(
                value_name="ESIGN_PADES_PKCS12_PASSWORD",
                file_name="ESIGN_PADES_PKCS12_PASSWORD_FILE",
            ),
            tsa_url=_https_url(
                os.getenv("ESIGN_PADES_TSA_URL", "").strip() or None,
                name="ESIGN_PADES_TSA_URL",
            ),
            trust_root_paths=_path_list(os.getenv("ESIGN_PADES_TRUST_ROOTS", "")),
            allow_revocation_fetching=_bool_env(
                "ESIGN_PADES_ALLOW_REVOCATION_FETCHING", True
            ),
            revocation_mode=os.getenv(
                "ESIGN_PADES_REVOCATION_MODE", default_revocation_mode
            ).strip().lower(),
            require_trusted_certificate=_bool_env(
                "ESIGN_PADES_REQUIRE_TRUSTED_CERTIFICATE", True
            ),
            tsa_timeout_seconds=tsa_timeout_seconds,
            reason=os.getenv(
                "ESIGN_PADES_REASON", "ReDOCX envelope completion seal"
            ).strip()
            or "ReDOCX envelope completion seal",
            location=os.getenv("ESIGN_PADES_LOCATION", "").strip() or None,
            contact_info=os.getenv("ESIGN_PADES_CONTACT_INFO", "").strip() or None,
        )
        config.validate(production=production)
        return config

    def validate(self, *, production: bool = False) -> None:
        if not self.enabled:
            if production:
                raise PAdESConfigurationError(
                    "ESIGN_PADES_ENABLED must be true in production."
                )
            return

        if not self.pkcs12_path:
            raise PAdESConfigurationError("ESIGN_PADES_PKCS12_PATH is required.")
        pfx = Path(self.pkcs12_path).expanduser()
        if not pfx.is_file():
            raise PAdESConfigurationError(
                "ESIGN_PADES_PKCS12_PATH does not identify a readable PKCS#12 file."
            )

        timestamp_profiles = {
            PAdESProfile.baseline_t,
            PAdESProfile.baseline_lt,
            PAdESProfile.baseline_lta,
        }
        if self.profile in timestamp_profiles and not self.tsa_url:
            raise PAdESConfigurationError(
                f"ESIGN_PADES_TSA_URL is required for profile {self.profile.value}."
            )
        if not self.require_trusted_certificate:
            raise PAdESConfigurationError(
                "ESIGN_PADES_REQUIRE_TRUSTED_CERTIFICATE must remain true."
            )
        if not self.trust_root_paths:
            raise PAdESConfigurationError(
                "ESIGN_PADES_TRUST_ROOTS is required for explicit document-signing trust."
            )
        for root in self.trust_root_paths:
            if not Path(root).expanduser().is_file():
                raise PAdESConfigurationError(
                    f"Configured PAdES trust root is not readable: {root}"
                )
        if self.revocation_mode not in {"soft-fail", "hard-fail", "require"}:
            raise PAdESConfigurationError(
                "ESIGN_PADES_REVOCATION_MODE must be soft-fail, hard-fail, or require."
            )
        if production and self.profile in {
            PAdESProfile.baseline_lt,
            PAdESProfile.baseline_lta,
        } and self.revocation_mode != "require":
            raise PAdESConfigurationError(
                "Production PAdES B-LT/B-LTA requires ESIGN_PADES_REVOCATION_MODE=require."
            )
        if production and self.profile in {
            PAdESProfile.baseline_lt,
            PAdESProfile.baseline_lta,
        } and not self.allow_revocation_fetching:
            raise PAdESConfigurationError(
                "Production PAdES B-LT/B-LTA requires revocation fetching."
            )
        if not 1 <= self.tsa_timeout_seconds <= 30:
            raise PAdESConfigurationError(
                "ESIGN_PADES_TSA_TIMEOUT_SECONDS must be between 1 and 30."
            )


def _load_runtime():
    try:
        from pyhanko.keys import load_certs_from_pemder
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign import signers, timestamps
        from pyhanko.sign.fields import SigSeedSubFilter
        from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
        from pyhanko.sign.validation.dss import DocumentSecurityStore
        from pyhanko_certvalidator import ValidationContext
    except ImportError as exc:  # pragma: no cover - depends on deployment image
        raise PAdESConfigurationError(
            "PAdES signing requires the pinned pyHanko runtime dependency."
        ) from exc
    return (
        load_certs_from_pemder,
        IncrementalPdfFileWriter,
        PdfFileReader,
        signers,
        timestamps,
        SigSeedSubFilter,
        DocumentSecurityStore,
        validate_pdf_signature,
        validate_pdf_timestamp,
        ValidationContext,
    )


def _human_name(name: object) -> str:
    value = getattr(name, "human_friendly", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    rendered = str(name).strip()
    return rendered or "unavailable"


def _safe_field_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_.-")
    return f"ReDOCX_{normalized or 'Document'}_Seal"[:120]


def sign_pdf_pades(
    *,
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    config: PAdESConfig,
    document_id: str,
) -> PAdESSignatureInfo:
    """Apply and verify one incremental X.509/PAdES completion seal."""

    config.validate(production=False)
    if not config.enabled:
        raise PAdESConfigurationError("PAdES completion sealing is disabled.")

    source = Path(source_pdf_path).expanduser().resolve()
    output = Path(output_pdf_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise PAdESSealError("PAdES source must be a readable PDF file.")
    if source == output:
        raise PAdESSealError("PAdES output must not overwrite the source revision.")
    output.parent.mkdir(parents=True, exist_ok=True)

    (
        load_certs_from_pemder,
        IncrementalPdfFileWriter,
        PdfFileReader,
        signers,
        timestamps,
        SigSeedSubFilter,
        DocumentSecurityStore,
        validate_pdf_signature,
        validate_pdf_timestamp,
        ValidationContext,
    ) = _load_runtime()

    roots = list(load_certs_from_pemder(config.trust_root_paths))
    validation_context = ValidationContext(
        trust_roots=roots,
        allow_fetching=config.allow_revocation_fetching,
        revocation_mode=config.revocation_mode,
    )
    passphrase = (
        config.pkcs12_password.encode("utf-8")
        if config.pkcs12_password is not None
        else None
    )
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=Path(config.pkcs12_path or "").expanduser(),
        passphrase=passphrase,
    )
    if signer is None:
        raise PAdESConfigurationError(
            "The configured PKCS#12 file or password could not be loaded."
        )

    timestamped = config.profile != PAdESProfile.baseline_b
    embed_validation_info = config.profile in {
        PAdESProfile.baseline_lt,
        PAdESProfile.baseline_lta,
    }
    use_pades_lta = config.profile == PAdESProfile.baseline_lta
    timestamper = (
        timestamps.HTTPTimeStamper(
            url=config.tsa_url,
            timeout=config.tsa_timeout_seconds,
        )
        if timestamped
        else None
    )
    field_name = _safe_field_name(document_id)
    signature_meta = signers.PdfSignatureMetadata(
        field_name=field_name,
        md_algorithm="sha256",
        subfilter=SigSeedSubFilter.PADES,
        reason=config.reason,
        location=config.location,
        contact_info=config.contact_info,
        embed_validation_info=embed_validation_info,
        use_pades_lta=use_pades_lta,
        validation_context=validation_context,
    )

    try:
        with source.open("rb") as input_stream, output.open("wb") as output_stream:
            writer = IncrementalPdfFileWriter(input_stream)
            signers.sign_pdf(
                writer,
                signature_meta=signature_meta,
                signer=signer,
                timestamper=timestamper,
                existing_fields_only=False,
                output=output_stream,
            )

        with output.open("rb") as signed_stream:
            reader = PdfFileReader(signed_stream)
            if not reader.embedded_regular_signatures:
                raise PAdESSealError("The signed PDF contains no regular PAdES signature.")
            embedded = reader.embedded_regular_signatures[-1]
            status = validate_pdf_signature(
                embedded,
                signer_validation_context=validation_context,
                ts_validation_context=validation_context,
            )

            validation_info_embedded = False
            if embed_validation_info:
                dss = DocumentSecurityStore.read_dss(reader)
                validation_info_embedded = bool(
                    list(dss.load_certs()) and (dss.ocsps or dss.crls)
                )

            document_timestamped = False
            if use_pades_lta:
                if not reader.embedded_timestamp_signatures:
                    raise PAdESSealError(
                        "The PAdES B-LTA output contains no document timestamp."
                    )
                document_timestamp_status = validate_pdf_timestamp(
                    reader.embedded_timestamp_signatures[-1],
                    validation_context=validation_context,
                )
                document_timestamped = bool(
                    getattr(document_timestamp_status, "intact", False)
                    and getattr(document_timestamp_status, "valid", False)
                    and getattr(document_timestamp_status, "trusted", False)
                    and getattr(document_timestamp_status, "docmdp_ok", True)
                    is not False
                )
    except (PAdESConfigurationError, PAdESSealError):
        output.unlink(missing_ok=True)
        raise
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise PAdESSealError("PAdES signing or verification failed.") from exc

    integrity_ok = bool(
        getattr(status, "intact", False)
        and getattr(status, "docmdp_ok", True) is not False
    )
    signature_valid = bool(getattr(status, "valid", False))
    certificate_trusted = bool(getattr(status, "trusted", False))
    if not integrity_ok or not signature_valid or not bool(getattr(status, "bottom_line", False)):
        output.unlink(missing_ok=True)
        raise PAdESSealError("The PAdES signature failed integrity or CMS validation.")
    if not certificate_trusted:
        output.unlink(missing_ok=True)
        raise PAdESSealError("The PAdES signing certificate is not trusted.")
    if timestamped:
        timestamp_status = getattr(status, "timestamp_validity", None)
        timestamp_valid = bool(
            timestamp_status is not None
            and getattr(timestamp_status, "valid", False)
            and getattr(timestamp_status, "trusted", False)
        )
        if not timestamp_valid:
            output.unlink(missing_ok=True)
            raise PAdESSealError(
                "The required RFC 3161 signature timestamp did not validate."
            )
    else:
        timestamp_status = None
    if embed_validation_info and not validation_info_embedded:
        output.unlink(missing_ok=True)
        raise PAdESSealError(
            "The required PAdES document security store lacks revocation evidence."
        )
    if use_pades_lta and not document_timestamped:
        output.unlink(missing_ok=True)
        raise PAdESSealError("The PAdES B-LTA document timestamp did not validate.")

    certificate = signer.signing_cert
    cert_bytes = certificate.dump()
    trusted_signing_time = getattr(timestamp_status, "timestamp", None)
    signing_time = (
        trusted_signing_time
        if isinstance(trusted_signing_time, datetime)
        else datetime.now(timezone.utc)
    )
    if signing_time.tzinfo is None:
        signing_time = signing_time.replace(tzinfo=timezone.utc)
    return PAdESSignatureInfo(
        profile=config.profile,
        field_name=field_name,
        signer_subject=_human_name(certificate.subject),
        signer_issuer=_human_name(certificate.issuer),
        certificate_serial_number=str(certificate.serial_number),
        certificate_sha256=hashlib.sha256(cert_bytes).hexdigest(),
        signing_time_iso=signing_time.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        timestamped=timestamped,
        validation_info_embedded=validation_info_embedded,
        document_timestamped=document_timestamped,
        integrity_ok=integrity_ok,
        signature_valid=signature_valid,
        certificate_trusted=certificate_trusted,
    )


__all__ = [
    "PAdESConfig",
    "PAdESConfigurationError",
    "PAdESSealError",
    "sign_pdf_pades",
]
