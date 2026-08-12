from __future__ import annotations

import atexit
import base64
import binascii
import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from backend.src.schema import SensitiveDataType

DEFAULT_MIN_LIKELIHOOD = "POSSIBLE"
DEFAULT_DLP_LOCATION = "global"
GOOGLE_TEXT_CHUNK_MAX_BYTES = 350 * 1024
GOOGLE_TEXT_CHUNK_OVERLAP = 256
MAX_NAME_CHARACTERS = 16_384
MAX_NAME_WORDS = 512

_MASKED_VALUE_RE = re.compile(
    r"(?i)(?:\b(?:masked|redacted|withheld|not\s+provided|"
    r"not\s+applicable|n/?a)\b|X{3,})"
)
_IDENTIFIER_PLACEHOLDERS = {
    "unknown",
    "pending",
    "none",
    "nil",
    "number",
    "idnumber",
    "notavailable",
    "notprovided",
    "tobeprovided",
}
_UNICODE_NAME_WORD_RE = r"[^\W\d_](?:[^\W\d_]|['’.\-])*"
_NAME_VALUE_RE = (
    rf"{_UNICODE_NAME_WORD_RE}"
    rf"(?:[ \t]+{_UNICODE_NAME_WORD_RE}){{0,{MAX_NAME_WORDS - 1}}}"
)
_NAME_LABEL_RE = (
    r"(?:full\s+name|legal\s+name|preferred\s+name|name|"
    r"surname(?:/nom)?|given\s+names?(?:/pr[eé]noms?)?|"
    r"first\s+name|last\s+name|applicant(?:'s)?\s+name|"
    r"customer(?:'s)?\s+name|client(?:'s)?\s+name|"
    r"patient(?:'s)?\s+name|employee(?:'s)?\s+name|"
    r"account\s+holder(?:'s)?\s+name|policyholder(?:'s)?\s+name|"
    r"beneficiary(?:'s)?\s+name|insured(?:'s)?\s+name|"
    r"borrower(?:'s)?\s+name|tenant(?:'s)?\s+name|"
    r"landlord(?:'s)?\s+name|signer(?:'s)?\s+name|"
    r"signatory(?:'s)?\s+name|nom(?:\s+complet)?)"
)
_NAME_DISALLOWED_WORDS = {
    "account",
    "address",
    "administrator",
    "admissions",
    "analyst",
    "application",
    "architect",
    "assistant",
    "bank",
    "board",
    "birth",
    "business",
    "chief",
    "company",
    "commission",
    "consultant",
    "contact",
    "contract",
    "coordinator",
    "corporation",
    "curriculum",
    "data",
    "date",
    "department",
    "designer",
    "developer",
    "director",
    "document",
    "doctor",
    "education",
    "email",
    "engineer",
    "engineering",
    "executive",
    "experience",
    "finance",
    "group",
    "headquarters",
    "invoice",
    "junior",
    "lead",
    "legal",
    "limited",
    "management",
    "manager",
    "matriculation",
    "mobile",
    "name",
    "national",
    "number",
    "officer",
    "operations",
    "passport",
    "phone",
    "product",
    "professional",
    "profile",
    "project",
    "representative",
    "resume",
    "sales",
    "scientist",
    "senior",
    "services",
    "skills",
    "software",
    "specialist",
    "signature",
    "summary",
    "tax",
    "technologies",
    "telephone",
    "university",
    "vitae",
}
_CONTACT_SIGNAL_RE = re.compile(
    r"(?i)(?:"
    r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}|"
    r"\b(?:phone|mobile|telephone|tel\.?|email|e-mail|linkedin)\b|"
    r"\+\d[\d ()\-]{7,}\d"
    r")"
)

_CREDENTIALS_LOCK = threading.Lock()
_GENERATED_CREDENTIALS_PATH: Path | None = None


@dataclass(frozen=True)
class DetectionCandidate:
    label: str
    quote: str
    occurrences: int
    source: str


@dataclass(frozen=True)
class TextFinding:
    start: int
    end: int
    quote: str
    label: str
    source: str


@dataclass(frozen=True)
class ImageFinding:
    bbox: tuple[int, int, int, int]
    quote: str
    label: str
    source: str


def configure_google_application_credentials() -> None:
    """Configure Google ADC from Railway environment variables.

    Local development can keep using gcloud/application-default credentials.
    Railway should provide GOOGLE_SERVICE_ACCOUNT_JSON_B64, which is decoded
    into a temporary credentials file before the Google DLP client is created.
    """
    existing_credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    encoded_credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON_B64")
    raw_credentials = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    if existing_credentials_path and Path(existing_credentials_path).exists():
        return

    if encoded_credentials:
        try:
            credentials_content = base64.b64decode(
                encoded_credentials,
                validate=True,
            ).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON_B64 is set but is not valid "
                "base64-encoded UTF-8 service account JSON."
            ) from exc
    elif raw_credentials:
        credentials_content = raw_credentials
    elif existing_credentials_path:
        raise RuntimeError(
            "GOOGLE_APPLICATION_CREDENTIALS is set, but the file does not exist: "
            f"{existing_credentials_path}"
        )
    else:
        return

    try:
        credential_data = json.loads(credentials_content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Google service-account credentials must be valid JSON."
        ) from exc
    if (
        not isinstance(credential_data, dict)
        or credential_data.get("type") != "service_account"
    ):
        raise RuntimeError(
            "Google credentials must contain a service-account JSON object."
        )

    global _GENERATED_CREDENTIALS_PATH
    with _CREDENTIALS_LOCK:
        current_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if current_path and Path(current_path).exists():
            return

        descriptor, generated_path = tempfile.mkstemp(
            prefix="redocx-google-sdp-",
            suffix=".json",
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(credential_data, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(generated_path).unlink(missing_ok=True)
            raise

        credentials_path = Path(generated_path)
        _GENERATED_CREDENTIALS_PATH = credentials_path
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)
        atexit.register(credentials_path.unlink, missing_ok=True)


def _import_dlp_v2():
    try:
        from google.cloud import dlp_v2  # type: ignore
        return dlp_v2
    except Exception as exc:
        raise RuntimeError(
            "Google Sensitive Data Protection client library is required. "
            "Install it with: pip install google-cloud-dlp"
        ) from exc


class GoogleSDPClient:
    """
    Shared Google Sensitive Data Protection wrapper for text inspection and
    plain-text de-identification helpers.

    For structure-preserving DOCX and PDF workflows, use this client for
    detection and then apply document-native edits in redact.py/data_mask.py.
    """

    def __init__(
        self,
        project_id: str,
        *,
        location: str = DEFAULT_DLP_LOCATION,
        min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
        client: Any | None = None,
    ) -> None:
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")
        if not location or not location.strip():
            raise ValueError("location is required.")

        self.project_id = project_id.strip()
        self.location = location.strip()
        self.min_likelihood = min_likelihood

        if client is None:
            configure_google_application_credentials()
        self.client = client or _import_dlp_v2().DlpServiceClient()

    @property
    def parent(self) -> str:
        return f"projects/{self.project_id}/locations/{self.location}"

    def inspect_text(
        self,
        *,
        text: str,
        targets: Sequence[SensitiveDataType],
        review_exclusions: Sequence[str] = (),
        min_likelihood: Optional[str] = None,
    ) -> list[TextFinding]:
        return inspect_sensitive_text(
            sdp=self,
            text=text,
            targets=targets,
            review_exclusions=review_exclusions,
            min_likelihood=min_likelihood or self.min_likelihood,
        )

    def inspect_image(
        self,
        *,
        image_bytes: bytes,
        targets: Sequence[SensitiveDataType],
        image_type: str = "IMAGE_JPEG",
        review_exclusions: Sequence[str] = (),
        min_likelihood: Optional[str] = None,
    ) -> list[ImageFinding]:
        return inspect_sensitive_image(
            sdp=self,
            image_bytes=image_bytes,
            image_type=image_type,
            targets=targets,
            review_exclusions=review_exclusions,
            min_likelihood=min_likelihood or self.min_likelihood,
        )

    def preview_candidates(
        self,
        *,
        text: str,
        targets: Sequence[SensitiveDataType],
        review_exclusions: Sequence[str] = (),
        min_likelihood: Optional[str] = None,
    ) -> list[DetectionCandidate]:
        return preview_candidates_from_text(
            sdp=self,
            text=text,
            targets=targets,
            review_exclusions=review_exclusions,
            min_likelihood=min_likelihood or self.min_likelihood,
        )

    def deidentify_text_redact(
        self,
        *,
        text: str,
        targets: Sequence[SensitiveDataType],
        min_likelihood: Optional[str] = None,
    ) -> str:
        info_types = _google_info_types_for_targets(targets)
        if not info_types:
            return text

        response = self.client.deidentify_content(
            request={
                "parent": self.parent,
                "inspect_config": {
                    "info_types": info_types,
                    "min_likelihood": min_likelihood or self.min_likelihood,
                },
                "deidentify_config": {
                    "info_type_transformations": {
                        "transformations": [
                            {
                                "primitive_transformation": {
                                    "redact_config": {}
                                }
                            }
                        ]
                    }
                },
                "item": {"value": text},
            }
        )
        return response.item.value

    def deidentify_text_mask(
        self,
        *,
        text: str,
        targets: Sequence[SensitiveDataType],
        masking_character: str = "X",
        number_to_mask: Optional[int] = None,
        min_likelihood: Optional[str] = None,
    ) -> str:
        info_types = _google_info_types_for_targets(targets)
        if not info_types:
            return text

        mask_config: dict[str, Any] = {"masking_character": masking_character}
        if number_to_mask is not None:
            mask_config["number_to_mask"] = number_to_mask

        response = self.client.deidentify_content(
            request={
                "parent": self.parent,
                "inspect_config": {
                    "info_types": info_types,
                    "min_likelihood": min_likelihood or self.min_likelihood,
                },
                "deidentify_config": {
                    "info_type_transformations": {
                        "transformations": [
                            {
                                "primitive_transformation": {
                                    "character_mask_config": mask_config
                                }
                            }
                        ]
                    }
                },
                "item": {"value": text},
            }
        )
        return response.item.value


def build_google_sdp_client(
    *,
    project_id: str,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> GoogleSDPClient:
    return GoogleSDPClient(
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )


def _coerce_sdp(
    sdp: GoogleSDPClient | None = None,
    *,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> GoogleSDPClient:
    if sdp is not None:
        return sdp
    if client is not None and project_id:
        return build_google_sdp_client(
            project_id=project_id,
            location=location,
            min_likelihood=min_likelihood,
            client=client,
        )
    if project_id:
        return build_google_sdp_client(
            project_id=project_id,
            location=location,
            min_likelihood=min_likelihood,
        )
    raise ValueError("Provide either sdp or project_id.")


def _normalize_text_for_compare(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _normalized_exclusions(values: Sequence[str]) -> set[str]:
    return {_normalize_text_for_compare(v) for v in values if v and v.strip()}


def _digit_count(value: str) -> int:
    return len(re.findall(r"\d", value or ""))


def _letter_count(value: str) -> int:
    return sum(character.isalpha() for character in value or "")


def _compact_alphanumeric(value: str) -> str:
    return "".join(character for character in value or "" if character.isalnum())


def _is_masked_or_placeholder(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", (value or "").casefold())
    return (
        not normalized
        or normalized in _IDENTIFIER_PLACEHOLDERS
        or bool(_MASKED_VALUE_RE.search(value or ""))
    )


def _passes_luhn(value: str) -> bool:
    digits = [int(character) for character in value if character.isdigit()]
    if not 12 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False

    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _is_name_like(
    value: str,
    *,
    require_multiple_words: bool = False,
) -> bool:
    candidate = re.sub(r"\s+", " ", (value or "").strip(" \t:;,#"))
    if (
        not candidate
        or len(candidate) > MAX_NAME_CHARACTERS
        or _is_masked_or_placeholder(candidate)
    ):
        return False
    if not re.fullmatch(_NAME_VALUE_RE, candidate, flags=re.UNICODE):
        return False

    words = [word.strip(".'’-").casefold() for word in candidate.split()]
    words = [word for word in words if word]
    if not words or (require_multiple_words and len(words) < 2):
        return False
    if _letter_count(candidate) < 2:
        return False
    return not any(word in _NAME_DISALLOWED_WORDS for word in words)


def _is_identifier_like(
    target: SensitiveDataType,
    quote: str,
) -> bool:
    if _is_masked_or_placeholder(quote):
        return False

    compact = _compact_alphanumeric(quote)
    digits = _digit_count(compact)
    letters = _letter_count(compact)
    if len(compact) > 1 and len(set(compact.casefold())) == 1:
        return False

    if target == SensitiveDataType.account_number:
        if re.fullmatch(r"(?i)[A-Z]{2}\d{2}[A-Z0-9]{10,30}", compact):
            return True
        return 6 <= len(compact) <= 34 and digits >= 6

    if target == SensitiveDataType.card_number:
        return _passes_luhn(quote)

    if target == SensitiveDataType.phone_number:
        return 7 <= digits <= 15

    if target in {
        SensitiveDataType.national_id,
        SensitiveDataType.tax_id,
        SensitiveDataType.passport_number,
    }:
        if not 5 <= len(compact) <= 40:
            return False
        return digits >= 3 or (digits >= 1 and letters >= 2)

    return True


def _is_valid_structured_local_quote(target: SensitiveDataType, quote: str) -> bool:
    """Reject context-only false positives such as "Account Management".

    Local rules use nearby words like Account, ID, and TIN as context. The
    captured value still needs identifier-like structure; plain alphabetic
    business terms must not become findings just because they follow those
    labels.
    """
    if target == SensitiveDataType.name:
        return _is_name_like(quote)

    if target in {
        SensitiveDataType.account_number,
        SensitiveDataType.card_number,
        SensitiveDataType.phone_number,
        SensitiveDataType.national_id,
        SensitiveDataType.tax_id,
        SensitiveDataType.passport_number,
    }:
        return _is_identifier_like(target, quote)

    if target == SensitiveDataType.age:
        digits = re.sub(r"\D", "", quote)
        return bool(digits) and 0 <= int(digits) <= 130

    if target == SensitiveDataType.date_of_birth:
        return _digit_count(quote) > 0

    if target == SensitiveDataType.signature:
        return (
            not _is_masked_or_placeholder(quote)
            and _letter_count(quote) >= 2
        )

    return True


_ID_NAME_VALUE_RE = re.compile(
    rf"(?m)^[ \t]*({_NAME_VALUE_RE})[ \t]*[,;]?[ \t]*$"
)
_ID_DATE_VALUE_RE = re.compile(
    r"(?i)\b("
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
    r"\d{1,2}[ \t]+[A-Za-z]{3,9}[ \t]+\d{4}|"
    r"[A-Za-z]{3,9}[ \t]+\d{1,2},?[ \t]+\d{4}"
    r")\b"
)
_ID_NIN_VALUE_RE = re.compile(r"(?<!\d)((?:\d[ \t-]*){10}\d)(?!\d)")
_ID_CONTIGUOUS_NIN_VALUE_RE = re.compile(r"(?<!\d)(\d{11})(?!\d)")
_ID_ADDRESS_VALUE_RE = re.compile(r"(?m)^[ \t]*([^\r\n]{4,1024}?)[ \t]*$")

_ID_SURNAME_LABEL_RE = re.compile(
    r"(?i)\b(?:surname(?:[ \t]*/[ \t]*nom)?|last[ \t]+name)\b"
)
_ID_GIVEN_NAMES_LABEL_RE = re.compile(
    r"(?i)\b(?:given[ \t]+names?(?:[ \t]*/[ \t]*prenoms?)?|first[ \t]*names?)\b"
)
_ID_MIDDLE_NAMES_LABEL_RE = re.compile(r"(?i)\bmidd(?:le|ie)[ \t]+names?\b")
_ID_DOB_LABEL_RE = re.compile(r"(?i)\b(?:date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?)\b")
_ID_NIN_LABEL_RE = re.compile(
    r"(?i)\b(?:national[ \t]+identification[ \t]+number(?:[ \t]*\([ \t]*nin[ \t]*\))?|"
    r"nin(?:[ \t]+(?:number|no[.]?))?)\b"
)
_ID_TRACKING_LABEL_RE = re.compile(r"(?i)\btracking[ \t]+(?:id|number|no[.]?)\b")
_ID_ALPHANUMERIC_VALUE_RE = re.compile(r"(?i)(?<![A-Z0-9])([A-Z0-9]{12,64})(?![A-Z0-9])")
_ID_ADDRESS_LABEL_RE = re.compile(
    r"(?i)\b(?:residential[ \t]+address|home[ \t]+address|contact[ \t]+address|address|ddress)\b"
)
_ID_NAME_STOP_RE = re.compile(
    r"(?i)\b(?:given[ \t]+names?|first[ \t]*names?|midd(?:le|ie)[ \t]+names?|"
    r"date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?|sex|gender|address|"
    r"national[ \t]+identification|nin)\b"
)
_ID_GIVEN_NAME_STOP_RE = re.compile(
    r"(?i)\b(?:midd(?:le|ie)[ \t]+names?|date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?|"
    r"sex|gender|address|national[ \t]+identification|nin)\b"
)
_ID_MIDDLE_NAME_STOP_RE = re.compile(
    r"(?i)\b(?:date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?|sex|gender|address|"
    r"national[ \t]+identification|nin)\b"
)
_ID_DOB_STOP_RE = re.compile(
    r"(?i)\b(?:issue[ \t]+date|date[ \t]+of[ \t]+issue|sex|gender|address|"
    r"national[ \t]+identification|nin)\b"
)
_ID_ADDRESS_STOP_RE = re.compile(
    r"(?i)\b(?:date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?|sex|gender|"
    r"national[ \t]+identification|nin|issue[ \t]+date|date[ \t]+of[ \t]+issue)\b"
)
_ID_TRACKING_STOP_RE = re.compile(
    r"(?i)\b(?:surname|given[ \t]+names?|first[ \t]*names?|midd(?:le|ie)[ \t]+names?|"
    r"date[ \t]+of[ \t]+birth|d[.]?o[.]?b[.]?|sex|gender|address|"
    r"national[ \t]+identification|nin)\b"
)


def is_id_document_payload(payload: Any) -> bool:
    document_type = getattr(payload, "document_type", None)
    return str(getattr(document_type, "value", document_type) or "") == "id_document"


def _payload_target_values(payload: Any) -> set[str]:
    return {
        str(getattr(target, "value", target))
        for target in getattr(payload, "target_data", ())
    }


def _bounded_text_after_label(
    text: str,
    label: re.Pattern[str],
    stop: re.Pattern[str] | None,
) -> tuple[int, str] | None:
    label_match = label.search(text)
    if label_match is None:
        return None

    start = label_match.end()
    delimiter = re.match(r"[ \t]*[:#-]?[ \t]*", text[start:])
    if delimiter is not None:
        start += delimiter.end()
    end = len(text)
    if stop is not None:
        stop_match = stop.search(text, start)
        if stop_match is not None:
            end = stop_match.start()
    return start, text[start:end]


def _first_id_value_finding(
    *,
    text: str,
    label: re.Pattern[str],
    stop: re.Pattern[str] | None,
    value_pattern: re.Pattern[str],
    finding_label: str,
    exclusions: set[str],
) -> TextFinding | None:
    bounded = _bounded_text_after_label(text, label, stop)
    if bounded is None:
        return None

    segment_start, segment = bounded
    value_match = value_pattern.search(segment)
    if value_match is None:
        return None

    quote = value_match.group(1).strip(" \t:;,#")
    if not quote or _normalize_text_for_compare(quote) in exclusions:
        return None

    raw_start = segment_start + value_match.start(1)
    leading = len(value_match.group(1)) - len(value_match.group(1).lstrip(" \t:;,#"))
    start = raw_start + leading
    end = start + len(quote)
    return TextFinding(
        start=start,
        end=end,
        quote=quote,
        label=finding_label,
        source="id_document_rule",
    )


def id_document_field_findings(text: str, *, payload: Any) -> list[TextFinding]:
    """Return only label-anchored personal fields for compact ID layouts.

    General PERSON_NAME detection is deliberately replaced for ID documents:
    multilingual headings and OCR fragments otherwise become false names such
    as one-letter/partial-word masks. Other selected detector categories keep
    their normal Google and local coverage.
    """
    targets = _payload_target_values(payload)
    exclusions = _normalized_exclusions(getattr(payload, "review_exclusions", ()))
    findings: list[TextFinding] = []

    if SensitiveDataType.name.value in targets:
        for label, stop in (
            (_ID_SURNAME_LABEL_RE, _ID_NAME_STOP_RE),
            (_ID_GIVEN_NAMES_LABEL_RE, _ID_GIVEN_NAME_STOP_RE),
            (_ID_MIDDLE_NAMES_LABEL_RE, _ID_MIDDLE_NAME_STOP_RE),
        ):
            finding = _first_id_value_finding(
                text=text,
                label=label,
                stop=stop,
                value_pattern=_ID_NAME_VALUE_RE,
                finding_label=SensitiveDataType.name.value,
                exclusions=exclusions,
            )
            if finding is not None and _is_name_like(finding.quote):
                findings.append(finding)

    if SensitiveDataType.date_of_birth.value in targets:
        finding = _first_id_value_finding(
            text=text,
            label=_ID_DOB_LABEL_RE,
            stop=_ID_DOB_STOP_RE,
            value_pattern=_ID_DATE_VALUE_RE,
            finding_label=SensitiveDataType.date_of_birth.value,
            exclusions=exclusions,
        )
        if finding is not None:
            findings.append(finding)

    if SensitiveDataType.contact_address.value in targets:
        finding = _first_id_value_finding(
            text=text,
            label=_ID_ADDRESS_LABEL_RE,
            stop=_ID_ADDRESS_STOP_RE,
            value_pattern=_STREET_ADDRESS_LINE_RE,
            finding_label=SensitiveDataType.contact_address.value,
            exclusions=exclusions,
        )
        if finding is None:
            value_match = _STREET_ADDRESS_LINE_RE.search(text)
            if value_match is not None:
                quote = value_match.group(1).strip()
                if _normalize_text_for_compare(quote) not in exclusions:
                    finding = TextFinding(
                        start=value_match.start(1),
                        end=value_match.end(1),
                        quote=quote,
                        label=SensitiveDataType.contact_address.value,
                        source="id_document_rule",
                    )
        if finding is not None:
            findings.append(finding)

    if SensitiveDataType.national_id.value in targets:
        finding = _first_id_value_finding(
            text=text,
            label=_ID_TRACKING_LABEL_RE,
            stop=_ID_TRACKING_STOP_RE,
            value_pattern=_ID_ALPHANUMERIC_VALUE_RE,
            finding_label=SensitiveDataType.national_id.value,
            exclusions=exclusions,
        )
        if finding is None:
            for value_match in _ID_ALPHANUMERIC_VALUE_RE.finditer(text):
                quote = value_match.group(1)
                if (
                    sum(char.isdigit() for char in quote) >= 4
                    and sum(char.isalpha() for char in quote) >= 4
                    and _normalize_text_for_compare(quote) not in exclusions
                ):
                    finding = TextFinding(
                        start=value_match.start(1),
                        end=value_match.end(1),
                        quote=quote,
                        label=SensitiveDataType.national_id.value,
                        source="id_document_rule",
                    )
                    break
        if finding is not None:
            findings.append(finding)

        finding = None
        contiguous_match = _ID_CONTIGUOUS_NIN_VALUE_RE.search(text)
        if contiguous_match is not None:
            quote = contiguous_match.group(1)
            if _normalize_text_for_compare(quote) not in exclusions:
                finding = TextFinding(
                    start=contiguous_match.start(1),
                    end=contiguous_match.end(1),
                    quote=quote,
                    label=SensitiveDataType.national_id.value,
                    source="id_document_rule",
                )
        if finding is None:
            finding = _first_id_value_finding(
                text=text,
                label=_ID_NIN_LABEL_RE,
                stop=None,
                value_pattern=_ID_NIN_VALUE_RE,
                finding_label=SensitiveDataType.national_id.value,
                exclusions=exclusions,
            )
        if finding is None:
            # Nigeria's NIN is exactly eleven digits. This fallback is narrow
            # enough for an ID document even when the printed label is faint.
            value_match = _ID_NIN_VALUE_RE.search(text)
            if value_match is not None:
                quote = value_match.group(1).strip()
                if _normalize_text_for_compare(quote) not in exclusions:
                    finding = TextFinding(
                        start=value_match.start(1),
                        end=value_match.end(1),
                        quote=quote,
                        label=SensitiveDataType.national_id.value,
                        source="id_document_rule",
                    )
        if finding is not None:
            findings.append(finding)

    return _dedupe_findings(findings)


def _dedupe_findings(findings: Iterable[TextFinding]) -> list[TextFinding]:
    ordered = sorted(findings, key=lambda item: (item.start, -(item.end - item.start), item.label, item.source))
    result: list[TextFinding] = []
    for finding in ordered:
        if result and finding.start >= result[-1].start and finding.end <= result[-1].end:
            continue
        result.append(finding)
    return result


def merge_overlapping_findings(
    findings: Sequence[TextFinding],
    *,
    original_text: str | None = None,
) -> list[TextFinding]:
    ordered = sorted(findings, key=lambda item: (item.start, item.end, item.label, item.source))
    if not ordered:
        return []

    merged: list[TextFinding] = []
    current = ordered[0]

    for finding in ordered[1:]:
        if finding.start < current.end:
            start = min(current.start, finding.start)
            end = max(current.end, finding.end)
            if current.label == finding.label:
                label = current.label
            else:
                label = "sensitive_data"
            if current.source == finding.source:
                source = current.source
            else:
                source = "merged"

            quote = original_text[start:end] if original_text is not None else current.quote
            current = TextFinding(start=start, end=end, quote=quote, label=label, source=source)
        else:
            merged.append(current)
            current = finding

    merged.append(current)
    return merged


# Country-agnostic location fallback for contact_address.
#
# Google Sensitive Data Protection's STREET_ADDRESS infoType gives precise
# street-address coverage. We intentionally do not request the broad LOCATION
# infoType for contact_address because it can classify institutions, section
# headings, and countries as locations. This local fallback covers common
# resume/CV and profile formats such as "Lagos, Nigeria", "Paris, France",
# or "San Francisco, United States" while requiring a known country after a
# comma to avoid redacting ordinary capitalized words.
_COMMON_COUNTRY_ALIASES = (
    "United States",
    "United States of America",
    "USA",
    "US",
    "U.S.",
    "U.S.A.",
    "United Kingdom",
    "UK",
    "U.K.",
    "Great Britain",
    "Russia",
    "South Korea",
    "North Korea",
    "Iran",
    "Syria",
    "Vietnam",
    "Laos",
    "Moldova",
    "Tanzania",
    "Venezuela",
    "Bolivia",
    "Brunei",
    "Czech Republic",
    "UAE",
    "United Arab Emirates",
)

# pycountry is optional in this module, so keep a built-in country list for
# deployments where that package is not installed. This prevents contact
# addresses such as "Lagos, Nigeria" from being missed silently.
_FALLBACK_COUNTRY_NAMES = (
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Antigua and Barbuda",
    "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan", "Bahamas",
    "Bahrain", "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize",
    "Benin", "Bhutan", "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil",
    "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia",
    "Cameroon", "Canada", "Central African Republic", "Chad", "Chile", "China",
    "Colombia", "Comoros", "Congo", "Costa Rica", "Cote d'Ivoire", "Croatia",
    "Cuba", "Cyprus", "Czechia", "Czech Republic", "Denmark", "Djibouti",
    "Dominica", "Dominican Republic", "Ecuador", "Egypt", "El Salvador",
    "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini", "Ethiopia", "Fiji",
    "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany", "Ghana",
    "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau", "Guyana",
    "Haiti", "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran",
    "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan", "Jordan",
    "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan", "Laos", "Latvia",
    "Lebanon", "Lesotho", "Liberia", "Libya", "Liechtenstein", "Lithuania",
    "Luxembourg", "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali",
    "Malta", "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia",
    "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco", "Mozambique",
    "Myanmar", "Namibia", "Nauru", "Nepal", "Netherlands", "New Zealand",
    "Nicaragua", "Niger", "Nigeria", "North Korea", "North Macedonia", "Norway",
    "Oman", "Pakistan", "Palau", "Panama", "Papua New Guinea", "Paraguay",
    "Peru", "Philippines", "Poland", "Portugal", "Qatar", "Romania", "Russia",
    "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
    "Saint Vincent and the Grenadines", "Samoa", "San Marino",
    "Sao Tome and Principe", "Saudi Arabia", "Senegal", "Serbia", "Seychelles",
    "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Solomon Islands",
    "Somalia", "South Africa", "South Korea", "South Sudan", "Spain", "Sri Lanka",
    "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Tajikistan",
    "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga",
    "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu",
    "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom",
    "United States", "United States of America", "Uruguay", "Uzbekistan",
    "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Yemen", "Zambia",
    "Zimbabwe",
)


def _country_names_for_location_regex() -> tuple[str, ...]:
    names: set[str] = {
        name
        for name in (*_COMMON_COUNTRY_ALIASES, *_FALLBACK_COUNTRY_NAMES)
        if name.strip()
    }

    try:
        import pycountry  # type: ignore
    except Exception:
        pycountry = None  # type: ignore[assignment]

    if pycountry is not None:
        for country in pycountry.countries:
            for attr in ("name", "official_name", "common_name"):
                value = getattr(country, attr, None)
                if isinstance(value, str) and value.strip():
                    names.add(value.strip())

    # Keep only names that are useful in natural-language documents. Very short
    # aliases like "IN" are intentionally excluded because they create noisy
    # matches in normal English text.
    aliases = {name for name in _COMMON_COUNTRY_ALIASES if name.strip()}
    return tuple(
        sorted(
            {
                name
                for name in names
                if len(name.replace(".", "").strip()) >= 3 or name in aliases
            },
            key=len,
            reverse=True,
        )
    )


_COUNTRY_NAME_PATTERN = "|".join(
    re.escape(name) for name in _country_names_for_location_regex()
)
_LOCATION_WORD_RE = r"[A-Z][a-zÀ-ÖØ-öø-ÿ'’.-]+[A-Za-zÀ-ÖØ-öø-ÿ'’.-]*"
_LOCATION_PART_RE = (
    rf"(?!(?i:(?:{_COUNTRY_NAME_PATTERN}))\s*,)"
    rf"{_LOCATION_WORD_RE}(?:\s+{_LOCATION_WORD_RE}){{0,3}}"
)
_GLOBAL_CITY_REGION_COUNTRY_RE = re.compile(
    rf"(?<![A-Za-zÀ-ÖØ-öø-ÿ'’.-]\s)(?<!\w)("
    rf"{_LOCATION_PART_RE}"
    rf"(?:\s*,\s*{_LOCATION_PART_RE}){{0,1}}"
    rf"\s*,\s*(?i:(?:{_COUNTRY_NAME_PATTERN}))"
    rf")(?!\w)"
)

# Extra conservative suffix matchers for resume/CV layouts where PDF text
# extraction collapses right-aligned columns into a single space, e.g.
# "Computer Engineering Ogun, Nigeria". The broad matcher above purposely
# avoids starting immediately after another word; these recover the actual
# location suffix without redacting the preceding qualification/job text.
_LOCATION_PREFIX_WORD_RE = (
    r"Abu|Addis|Akwa|Buenos|Cape|Cross|Dar|Fort|Ho|Kuala|Las|Los|New|"
    r"Port|Rio|Saint|San|Santa|Sao|São|St\."
)
_GLOBAL_PREFIXED_PLACE_COUNTRY_RE = re.compile(
    rf"(?<!\w)("
    rf"(?!(?i:(?:{_COUNTRY_NAME_PATTERN}))\s*,)"
    rf"(?:{_LOCATION_PREFIX_WORD_RE})\s+{_LOCATION_WORD_RE}"
    rf"\s*,\s*(?i:(?:{_COUNTRY_NAME_PATTERN}))"
    rf")(?!\w)"
)
_GLOBAL_SINGLE_PLACE_COUNTRY_RE = re.compile(
    rf"(?<!\w)("
    rf"(?!(?i:(?:{_COUNTRY_NAME_PATTERN}))\s*,){_LOCATION_WORD_RE}"
    rf"\s*,\s*(?i:(?:{_COUNTRY_NAME_PATTERN}))"
    rf")(?!\w)"
)
_LOCATION_FALLBACK_RULES = (
    _GLOBAL_CITY_REGION_COUNTRY_RE,
    _GLOBAL_PREFIXED_PLACE_COUNTRY_RE,
    _GLOBAL_SINGLE_PLACE_COUNTRY_RE,
)

_LOCATION_ORGANIZATION_WORDS = {
    "board",
    "commission",
    "company",
    "corporation",
    "department",
    "government",
    "gov",
    "limited",
    "ministry",
    "plc",
    "university",
}


def _looks_like_country_name(value: str) -> bool:
    normalized = _normalize_text_for_compare(value)
    return any(
        normalized == _normalize_text_for_compare(country)
        for country in _country_names_for_location_regex()
    )


def _is_valid_city_region_country_quote(quote: str) -> bool:
    parts = [part.strip() for part in quote.split(",") if part.strip()]
    if len(parts) < 2:
        return False

    # The last component must be a known country. Earlier components should be
    # city/state/region names, not another country or an organization phrase
    # containing a country, e.g. "MTN Nigeria, Lagos, Nigeria".
    if not _looks_like_country_name(parts[-1]):
        return False

    for part in parts[:-1]:
        normalized_part = _normalize_text_for_compare(part)
        if _looks_like_country_name(part):
            return False
        if set(normalized_part.split()) & _LOCATION_ORGANIZATION_WORDS:
            return False
        if any(
            f" { _normalize_text_for_compare(country) }" in f" {normalized_part} "
            for country in _country_names_for_location_regex()
        ):
            return False

    return True


def _trim_location_organization_prefix(quote: str) -> tuple[int, str] | None:
    """Recover the location portion when OCR prepends a header/organization.

    For example, OCR can emit ``Gov Canaan Land, Ota, Nigeria`` after joining
    a logo fragment to the printed address. The organization fragment must not
    expand the visual mask, while the actual location should remain covered.
    """
    first_comma = quote.find(",")
    if first_comma < 0:
        return None

    first_part = quote[:first_comma]
    matches = list(re.finditer(r"[A-Za-zÀ-ÖØ-öø-ÿ]+", first_part))
    organization_matches = [
        match
        for match in matches
        if _normalize_text_for_compare(match.group(0)) in _LOCATION_ORGANIZATION_WORDS
    ]
    if not organization_matches:
        return None

    offset = organization_matches[-1].end()
    while offset < len(quote) and quote[offset] in " \t,;:-":
        offset += 1
    candidate = quote[offset:].strip()
    if not candidate or not _is_valid_city_region_country_quote(candidate):
        return None
    adjusted_offset = quote.find(candidate, offset)
    return adjusted_offset, candidate


# Keep each UI target mapped to explicit detectors. The broader GOVERNMENT_ID
# and FINANCIAL_ID aggregate infoTypes intentionally are not used: they include
# categories (for example, passports, driver's licenses, and card data) that a
# user might not have selected.
_GOOGLE_INFOTYPES: dict[SensitiveDataType, list[str]] = {
    SensitiveDataType.name: ["PERSON_NAME"],
    SensitiveDataType.email_address: ["EMAIL_ADDRESS"],
    SensitiveDataType.phone_number: ["PHONE_NUMBER"],
    SensitiveDataType.account_number: [
        "FINANCIAL_ACCOUNT_NUMBER",
        "IBAN_CODE",
        "CANADA_BANK_ACCOUNT",
        "JAPAN_BANK_ACCOUNT",
        "PORTUGAL_NIB_NUMBER",
    ],
    SensitiveDataType.card_number: ["CREDIT_CARD_NUMBER"],
    SensitiveDataType.national_id: [
        "ARGENTINA_DNI_NUMBER",
        "AUSTRIA_SOCIAL_SECURITY_NUMBER",
        "BELGIUM_NATIONAL_ID_CARD_NUMBER",
        "BRAZIL_RG_NUMBER",
        "CANADA_SOCIAL_INSURANCE_NUMBER",
        "CHILE_CDI_NUMBER",
        "CHINA_RESIDENT_ID_NUMBER",
        "COLOMBIA_CDC_NUMBER",
        "CROATIA_PERSONAL_ID_NUMBER",
        "CZECHIA_PERSONAL_ID_NUMBER",
        "DENMARK_CPR_NUMBER",
        "DOD_ID_NUMBER",
        "FINLAND_NATIONAL_ID_NUMBER",
        "FRANCE_CNI",
        "FRANCE_NIR",
        "GERMANY_IDENTITY_CARD_NUMBER",
        "HONG_KONG_ID_NUMBER",
        "INDIA_AADHAAR_INDIVIDUAL",
        "INDONESIA_NIK_NUMBER",
        "IRELAND_PPSN",
        "ISRAEL_IDENTITY_CARD_NUMBER",
        "JAPAN_INDIVIDUAL_NUMBER",
        "KOREA_ARN",
        "KOREA_RRN",
        "MEXICO_CURP_NUMBER",
        "NETHERLANDS_BSN_NUMBER",
        "NORWAY_NI_NUMBER",
        "PARAGUAY_CIC_NUMBER",
        "PERU_DNI_NUMBER",
        "POLAND_NATIONAL_ID_NUMBER",
        "POLAND_PESEL_NUMBER",
        "PORTUGAL_CDC_NUMBER",
        "PORTUGAL_SOCIAL_SECURITY_NUMBER",
        "SINGAPORE_NATIONAL_REGISTRATION_ID_NUMBER",
        "SOUTH_AFRICA_ID_NUMBER",
        "SPAIN_DNI_NUMBER",
        "SPAIN_NIE_NUMBER",
        "SPAIN_SOCIAL_SECURITY_NUMBER",
        "SWEDEN_NATIONAL_ID_NUMBER",
        "SWITZERLAND_SOCIAL_SECURITY_NUMBER",
        "TAIWAN_ID_NUMBER",
        "THAILAND_NATIONAL_ID_NUMBER",
        "TURKEY_ID_NUMBER",
        "UK_ELECTORAL_ROLL_NUMBER",
        "UK_NATIONAL_INSURANCE_NUMBER",
        "URUGUAY_CDI_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
        "VENEZUELA_CDI_NUMBER",
    ],
    SensitiveDataType.tax_id: [
        "VAT_NUMBER",
        "AUSTRALIA_TAX_FILE_NUMBER",
        "BRAZIL_CPF_NUMBER",
        "FINLAND_BUSINESS_ID",
        "FRANCE_TAX_IDENTIFICATION_NUMBER",
        "GERMANY_TAXPAYER_IDENTIFICATION_NUMBER",
        "INDIA_GST_INDIVIDUAL",
        "INDIA_PAN_INDIVIDUAL",
        "ITALY_FISCAL_CODE",
        "JAPAN_CORPORATE_NUMBER",
        "KOREA_BRN",
        "NEW_ZEALAND_IRD_NUMBER",
        "PARAGUAY_TAX_NUMBER",
        "SPAIN_CIF_NUMBER",
        "SPAIN_NIF_NUMBER",
        "UK_TAXPAYER_REFERENCE",
        "US_ADOPTION_TAXPAYER_IDENTIFICATION_NUMBER",
        "US_EMPLOYER_IDENTIFICATION_NUMBER",
        "US_INDIVIDUAL_TAXPAYER_IDENTIFICATION_NUMBER",
        "US_PREPARER_TAXPAYER_IDENTIFICATION_NUMBER",
    ],
    SensitiveDataType.passport_number: ["PASSPORT"],
    SensitiveDataType.contact_address: ["STREET_ADDRESS"],
    SensitiveDataType.date_of_birth: ["DATE_OF_BIRTH"],
    SensitiveDataType.age: ["AGE"],
}

_GOOGLE_IMAGE_ONLY_INFOTYPES: dict[SensitiveDataType, list[str]] = {
    SensitiveDataType.signature: ["OBJECT_TYPE/PERSON/SIGNATURE"],
}


def _build_infotype_owners(
    configurations: Sequence[dict[SensitiveDataType, list[str]]],
) -> dict[str, SensitiveDataType]:
    """Build a deterministic detector-to-UI-category mapping.

    A detector must have exactly one owner. Ambiguous ownership can make a
    finding change category solely because the caller changed target order.
    Failing at import time is safer than silently misclassifying sensitive
    data in production.
    """
    owners: dict[str, SensitiveDataType] = {}
    for configuration in configurations:
        for target, names in configuration.items():
            for name in names:
                normalized = name.upper()
                existing = owners.get(normalized)
                if existing is not None and existing != target:
                    raise RuntimeError(
                        "Google SDP infoType has multiple ReDOCX owners: "
                        f"{normalized} ({existing.value}, {target.value})."
                    )
                owners[normalized] = target
    return owners


_GOOGLE_INFOTYPE_OWNERS = _build_infotype_owners(
    (_GOOGLE_INFOTYPES, _GOOGLE_IMAGE_ONLY_INFOTYPES)
)


def _canonical_google_label(
    info_type: str,
    targets: Sequence[SensitiveDataType],
) -> str | None:
    normalized = (info_type or "").upper()
    selected = set(targets)
    owner = _GOOGLE_INFOTYPE_OWNERS.get(normalized)
    if owner is not None:
        return owner.value if owner in selected else None

    # The general PASSPORT detector can return a maintained, country-specific
    # subtype. It is safe to keep that subtype inside the separately selected
    # passport category.
    if (
        normalized.endswith("_PASSPORT")
        and SensitiveDataType.passport_number in selected
    ):
        return SensitiveDataType.passport_number.value

    # Do not leak newly returned or aggregate infoTypes into an unrelated UI
    # category. This preserves selection isolation and prevents unknown labels
    # from reaching downstream masking/redaction code.
    return None


_TITLE_CASE_NAME_WORD_RE = (
    r"[A-ZÀ-ÖØ-Þ](?:[^\W\d_]|['’.\-])*"
)

_STREET_ADDRESS_LINE_RE = re.compile(
    r"(?im)^[ \t]*("
    r"[\[(]?[ \t]*"
    r"(?:(?:no\.?|number|#)\s*)?"
    r"(?:[A-Z0-9][\w'’.,/-]*[ \t]+){1,16}"
    r"(?:street|st\.?|road|rd\.?|avenue|ave\.?|lane|drive|close|"
    r"crescent|boulevard|highway|expressway|estate|layout)\b"
    r"[^\r\n]{0,240}"
    r")[ \t]*$"
)


_LOCAL_REGEX_RULES: dict[SensitiveDataType, list[re.Pattern[str]]] = {
    SensitiveDataType.name: [
        re.compile(
            rf"\b(?i:mr|mrs|ms|miss|dr|prof)\.?\s+"
            rf"({_TITLE_CASE_NAME_WORD_RE}"
            rf"(?:[ \t]+{_TITLE_CASE_NAME_WORD_RE}){{0,{MAX_NAME_WORDS - 1}}})\b"
        ),
        re.compile(
            rf"(?im)^(?:{_NAME_LABEL_RE})\s*[:#-]\s*"
            rf"({_NAME_VALUE_RE})\s*$"
        ),
        re.compile(
            rf"(?im)^(?:{_NAME_LABEL_RE})\s*[:#-]?\s*$\r?\n"
            rf"(?:[^\r\n]{{1,3}}\r?\n)?\s*({_NAME_VALUE_RE})\s*$"
        ),
        re.compile(
            rf"(?i)\b(?:dear|attn\.?|attention)\s+"
            rf"({_NAME_VALUE_RE})(?=\s*[:,])"
        ),
        re.compile(
            rf"(?i)\bI\s*,\s*({_NAME_VALUE_RE})\s*,"
        ),
        re.compile(
            rf"(?im)^(?:(?:this|chis)\s+is\s+to\s+certify\s+that|"
            rf"this\s+certifies\s+that|awarded\s+to|presented\s+to)"
            rf"[ \t]*[:#-]?[ \t]*$\r?\n"
            rf"(?:[ \t]*\r?\n){{0,3}}(?:[^\r\n]{{1,3}}\r?\n)?"
            rf"[ \t]*({_NAME_VALUE_RE})[ \t]*$"
        ),
        re.compile(
            rf"(?im)^date\s+printed\s*:[^\r\n]*$\r?\n"
            rf"(?:[ \t]*\r?\n){{0,3}}[ \t]*({_NAME_VALUE_RE})[ \t]*$"
        ),
    ],
    SensitiveDataType.email_address: [
        re.compile(
            r"(?i)(?<![\w.+-])[A-Z0-9._%+\-]+@"
            r"[A-Z0-9.\-]+\.[A-Z]{2,63}(?![\w.-])"
        )
    ],
    SensitiveDataType.phone_number: [
        re.compile(
            r"(?i)\b(?:phone|mobile|telephone|tel\.?|cell|whatsapp|"
            r"contact\s+number)(?=\s|[:#-])\s*[:#-]?\s*"
            r"(\+?\d[\d ().\-]{5,}\d)"
        ),
        re.compile(r"(?<!\w)(\+\d[\d ().\-]{7,}\d)(?!\w)"),
        re.compile(r"(?<!\d)(\d[\d ().\-]{7,}\d)(?!\d)"),
    ],
    SensitiveDataType.account_number: [
        re.compile(
            r"(?i)\b(?:bank\s+account|account|acct|a/c|iban)"
            r"(?=\s|[:#-])\s*(?:number|no\.?|#)?\s*[:#-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]*"
            r"(?:[ \t]+[A-Z0-9][A-Z0-9./\-]*){0,7})\b"
        )
    ],
    SensitiveDataType.card_number: [
        re.compile(r"(?<!\d)(?:\d[ -]?){11,18}\d(?!\d)")
    ],
    SensitiveDataType.national_id: [
        re.compile(
            r"(?i)\b(?:"
            r"national\s+(?:identification|identity)\s+(?:number|no\.?)|"
            r"national\s+id|government\s+id|citizen(?:ship)?\s+(?:number|id)|"
            r"personal\s+identification\s+(?:number|no\.?)|"
            r"social\s+(?:security|insurance)\s+(?:number|no\.?)|"
            r"national\s+insurance\s+(?:number|no\.?)|"
            r"registration\s+(?:number|no\.?)|"
            r"examination\s+(?:number|no\.?)|"
            r"candidate\s+(?:number|no\.?)|"
            r"student\s+(?:number|no\.?)|"
            r"tracking\s+(?:id|number|no\.?)|"
            r"id\s+(?:number|no\.?)|nin|nino|ssn|sin|aadhaar|"
            r"num[eé]ro\s+(?:national|d['’]identification\s+nationale?|"
            r"de\s+s[eé]curit[eé]\s+sociale)"
            r")(?=\s|[:#-])\s*"
            r"(?:\(\s*(?:nin|nino|ssn|sin)\s*\))?\s*[:#-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]*"
            r"(?:[ \t-]+[A-Z0-9][A-Z0-9./\-]*){0,5})\b"
        ),
        re.compile(
            r"(?<!\d)("
            r"(?!(?:000|666|9\d{2})[- ]?(?:\d{2})[- ]?(?:\d{4}))"
            r"\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}"
            r")(?!\d)"
        ),
    ],
    SensitiveDataType.tax_id: [
        re.compile(
            r"(?i)\b(?:tax(?:payer)?\s+(?:identification\s+)?"
            r"(?:number|no\.?|id)|tax\s+reference|"
            r"tin|vat(?:\s+(?:number|no\.?))?|gst(?:in|\s+number)?|"
            r"ein|itin|ptin|utr|pan|cpf|nif|tfn|fiscal\s+code|"
            r"num[eé]ro\s+(?:fiscal|d['’]identification\s+fiscale?)|tva)"
            r"(?=\s|[:#-])\s*[:#-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]*"
            r"(?:[ \t-]+[A-Z0-9][A-Z0-9./\-]*){0,5})\b"
        )
    ],
    SensitiveDataType.passport_number: [
        re.compile(
            r"(?i)\b(?:passport|travel\s+document|passeport)\b"
            r"\s*(?:number|no\.?|id|num[eé]ro)?\s*[:#-]?\s*"
            r"([A-Z0-9][A-Z0-9./\-]*"
            r"(?:[ \t-]+[A-Z0-9][A-Z0-9./\-]*){0,3})\b"
        )
    ],
    SensitiveDataType.date_of_birth: [
        re.compile(
            r"(?i)\b(?:dob|date\s+of\s+birth|birth\s+date|"
            r"date\s+de\s+naissance)\b\s*[:#-]?\s*"
            r"[^\d]{0,80}?("
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
            r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
            r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|"
            r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
            r")\b"
        )
    ],
    SensitiveDataType.age: [
        re.compile(r"(?i)\bage\s*[:#-]?\s*(\d{1,3})\b"),
        re.compile(r"(?i)\b(\d{1,3})\s+years?\s+old\b"),
    ],
    SensitiveDataType.contact_address: [
        re.compile(
            r"(?i)\b(?:residential\s+address|mailing\s+address|"
            r"postal\s+address|home\s+address|contact\s+address|"
            r"address|adresse)\b\s*[:#-]?\s*(.+)"
        ),
        _STREET_ADDRESS_LINE_RE,
        _GLOBAL_CITY_REGION_COUNTRY_RE,
        _GLOBAL_PREFIXED_PLACE_COUNTRY_RE,
        _GLOBAL_SINGLE_PLACE_COUNTRY_RE,
    ],
    SensitiveDataType.signature: [
        re.compile(
            r"(?im)^(?:authorized\s+signature|customer\s+signature|"
            r"applicant\s+signature|holder(?:'s)?\s+signature|signature|"
            r"signed\s+by|signatory|signature\s+autoris[eé]e|"
            r"sign[eé]\s+par)[ \t]*[:#-]?[ \t]*"
            r"([^\n\r]{2,120})\s*$"
        ),
        re.compile(
            rf"(?im)^(?:sincerely\s+yours|yours\s+sincerely)\s*[:,]?[ \t]*$"
            rf"\r?\n[ \t]*({_NAME_VALUE_RE})[ \t]*$"
        ),
    ],
}

_CAPTURED_GROUP_ONLY = {
    SensitiveDataType.name,
    SensitiveDataType.phone_number,
    SensitiveDataType.account_number,
    SensitiveDataType.national_id,
    SensitiveDataType.tax_id,
    SensitiveDataType.passport_number,
    SensitiveDataType.date_of_birth,
    SensitiveDataType.age,
    SensitiveDataType.contact_address,
    SensitiveDataType.signature,
}


def _google_info_types_for_targets(targets: Sequence[SensitiveDataType]) -> list[dict[str, str]]:
    names: list[str] = []
    for target in targets:
        for name in _GOOGLE_INFOTYPES.get(target, []):
            if name not in names:
                names.append(name)
    return [{"name": name} for name in names]


def _google_image_info_types_for_targets(
    targets: Sequence[SensitiveDataType],
) -> list[dict[str, str]]:
    names = [item["name"] for item in _google_info_types_for_targets(targets)]
    for target in targets:
        for name in _GOOGLE_IMAGE_ONLY_INFOTYPES.get(target, []):
            if name not in names:
                names.append(name)
    return [{"name": name} for name in names]


def _trimmed_text_span(
    text: str,
    start: int,
    end: int,
) -> tuple[int, int, str] | None:
    if start < 0 or end <= start:
        return None
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    trimmed_start = start + leading
    trimmed_end = end - trailing
    if trimmed_end <= trimmed_start:
        return None
    return trimmed_start, trimmed_end, text[trimmed_start:trimmed_end]


def _contextual_name_findings(
    text: str,
    *,
    exclusions: set[str],
) -> list[TextFinding]:
    """Recover high-confidence letterhead and CV names missed by PERSON_NAME.

    A free-standing capitalized line is only classified as a name when it is
    near the document start and has nearby contact evidence. This keeps the
    fallback useful without treating ordinary title-cased headings as people.
    """
    raw_lines = [
        (match.start(), match.end(), match.group(0))
        for match in re.finditer(r"[^\r\n]+", text)
        if match.group(0).strip()
    ]
    findings: list[TextFinding] = []

    for index, (line_start, _line_end, raw_line) in enumerate(raw_lines[:12]):
        candidate = raw_line.strip(" \t|")
        if not _is_name_like(candidate, require_multiple_words=True):
            continue
        if _looks_like_country_name(candidate):
            continue

        nearby_start = raw_lines[max(0, index - 6)][0]
        nearby_end = raw_lines[min(len(raw_lines) - 1, index + 6)][1]
        if not _CONTACT_SIGNAL_RE.search(text[nearby_start:nearby_end]):
            continue

        leading = len(raw_line) - len(raw_line.lstrip(" \t|"))
        start = line_start + leading
        end = start + len(candidate)
        quote = text[start:end]
        if _normalize_text_for_compare(quote) in exclusions:
            continue
        findings.append(
            TextFinding(
                start=start,
                end=end,
                quote=quote,
                label=SensitiveDataType.name.value,
                source="local_context_rule",
            )
        )

    return findings


def _multiline_labeled_name_findings(
    text: str,
    *,
    exclusions: set[str],
) -> list[TextFinding]:
    """Detect a labeled name that wraps across an arbitrary number of lines."""
    label_lines = re.compile(
        rf"(?im)^[ \t]*(?:{_NAME_LABEL_RE})[ \t]*[:#-]?[ \t]*(?P<value>[^\r\n]*)$"
    )
    findings: list[TextFinding] = []

    for label_match in label_lines.finditer(text):
        spans: list[tuple[int, int]] = []
        inline_value = label_match.group("value")
        if inline_value.strip():
            leading = len(inline_value) - len(inline_value.lstrip())
            start = label_match.start("value") + leading
            end = label_match.end("value") - (len(inline_value) - len(inline_value.rstrip()))
            candidate = text[start:end]
            if not _is_name_like(candidate):
                continue
            spans.append((start, end))

        cursor = label_match.end()
        if cursor < len(text) and text[cursor:cursor + 2] == "\r\n":
            cursor += 2
        elif cursor < len(text) and text[cursor] in "\r\n":
            cursor += 1

        while cursor < len(text) and len(spans) < MAX_NAME_WORDS:
            line_end_match = re.search(r"\r?\n", text[cursor:])
            line_end = (
                cursor + line_end_match.start()
                if line_end_match is not None
                else len(text)
            )
            raw_line = text[cursor:line_end]
            candidate = raw_line.strip()
            if not candidate or ":" in candidate or not _is_name_like(candidate):
                break
            leading = len(raw_line) - len(raw_line.lstrip())
            trailing = len(raw_line) - len(raw_line.rstrip())
            spans.append((cursor + leading, line_end - trailing))
            cursor = line_end
            if cursor < len(text) and text[cursor:cursor + 2] == "\r\n":
                cursor += 2
            elif cursor < len(text) and text[cursor] in "\r\n":
                cursor += 1

        if not spans:
            continue
        start = spans[0][0]
        end = spans[-1][1]
        quote = text[start:end]
        if len(quote) > MAX_NAME_CHARACTERS or len(quote.split()) > MAX_NAME_WORDS:
            continue
        if not _is_name_like(quote) or _normalize_text_for_compare(quote) in exclusions:
            continue
        findings.append(
            TextFinding(
                start=start,
                end=end,
                quote=quote,
                label=SensitiveDataType.name.value,
                source="local_multiline_rule",
            )
        )

    return findings


def _local_regex_findings(
    text: str,
    targets: Sequence[SensitiveDataType],
    *,
    exclusions: set[str],
) -> list[TextFinding]:
    findings: list[TextFinding] = []
    for target in targets:
        for pattern in _LOCAL_REGEX_RULES.get(target, []):
            for match in pattern.finditer(text):
                start, end = match.span()
                if target in _CAPTURED_GROUP_ONLY and match.lastindex:
                    start, end = match.span(1)
                trimmed = _trimmed_text_span(text, start, end)
                if trimmed is None:
                    continue
                start, end, quote = trimmed
                if any(pattern is rule for rule in _LOCATION_FALLBACK_RULES):
                    if not _is_valid_city_region_country_quote(quote):
                        adjusted = _trim_location_organization_prefix(quote)
                        if adjusted is None:
                            continue
                        offset, quote = adjusted
                        start += offset
                        end = start + len(quote)
                if not _is_valid_structured_local_quote(target, quote):
                    continue
                if _normalize_text_for_compare(quote) in exclusions:
                    continue
                findings.append(
                    TextFinding(start=start, end=end, quote=quote, label=target.value, source="local_rule")
                )
    if SensitiveDataType.name in targets:
        findings.extend(_multiline_labeled_name_findings(text, exclusions=exclusions))
        findings.extend(_contextual_name_findings(text, exclusions=exclusions))
    return findings


def inspect_local_sensitive_text(
    *,
    text: str,
    targets: Sequence[SensitiveDataType],
    review_exclusions: Sequence[str] = (),
) -> list[TextFinding]:
    """Run deterministic, locally validated fallbacks without a network call."""
    return _dedupe_findings(
        _local_regex_findings(
            text,
            targets,
            exclusions=_normalized_exclusions(review_exclusions),
        )
    )


def _extract_google_span(finding: Any, original_text: str) -> Optional[tuple[int, int]]:
    location = getattr(finding, "location", None)
    if location is None:
        return None

    codepoint_range = getattr(location, "codepoint_range", None)
    if codepoint_range is not None:
        start = int(getattr(codepoint_range, "start", 0))
        end = int(getattr(codepoint_range, "end", 0))
        if end > start:
            return start, end

    byte_range = getattr(location, "byte_range", None)
    if byte_range is not None:
        start = int(getattr(byte_range, "start", 0))
        end = int(getattr(byte_range, "end", 0))
        if end > start:
            prefix = original_text.encode("utf-8")[:start].decode("utf-8", errors="ignore")
            body = original_text.encode("utf-8")[start:end].decode("utf-8", errors="ignore")
            cp_start = len(prefix)
            cp_end = cp_start + len(body)
            if cp_end > cp_start:
                return cp_start, cp_end
    return None


def _google_text_findings(
    *,
    sdp: GoogleSDPClient,
    text: str,
    targets: Sequence[SensitiveDataType],
    exclusions: set[str],
    min_likelihood: str,
) -> list[TextFinding]:
    info_types = _google_info_types_for_targets(targets)
    if not info_types:
        return []

    response = sdp.client.inspect_content(
        request={
            "parent": sdp.parent,
            "inspect_config": {
                "info_types": info_types,
                "include_quote": True,
                "min_likelihood": min_likelihood,
            },
            "item": {"value": text},
        }
    )

    findings: list[TextFinding] = []
    for finding in getattr(getattr(response, "result", None), "findings", []) or []:
        quote = (getattr(finding, "quote", "") or "").strip()
        if not quote:
            continue
        if _normalize_text_for_compare(quote) in exclusions:
            continue
        span = _extract_google_span(finding, text)
        if span is None:
            idx = text.find(quote)
            if idx < 0:
                idx = text.casefold().find(quote.casefold())
            if idx < 0:
                continue
            span = (idx, idx + len(quote))
        info_type = str(getattr(getattr(finding, "info_type", None), "name", "sensitive_data"))
        label = _canonical_google_label(info_type, targets)
        if label is None:
            continue
        findings.append(
            TextFinding(
                start=span[0],
                end=span[1],
                quote=quote,
                label=label,
                source="google_sdp",
            )
        )
    return findings


def _text_chunks(
    text: str,
    *,
    max_bytes: int = GOOGLE_TEXT_CHUNK_MAX_BYTES,
    overlap: int = GOOGLE_TEXT_CHUNK_OVERLAP,
) -> Iterable[tuple[int, str]]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero.")
    if overlap < 0:
        raise ValueError("overlap cannot be negative.")
    if len(text.encode("utf-8")) <= max_bytes:
        yield 0, text
        return

    start = 0
    text_length = len(text)
    while start < text_length:
        low = start + 1
        high = text_length
        best = start
        while low <= high:
            midpoint = (low + high) // 2
            size = len(text[start:midpoint].encode("utf-8"))
            if size <= max_bytes:
                best = midpoint
                low = midpoint + 1
            else:
                high = midpoint - 1

        if best == start:
            raise ValueError(
                "max_bytes is too small for the next UTF-8 character."
            )
        end = best
        if end < text_length:
            search_start = max(start + 1, end - max(2048, overlap * 4))
            boundary = max(
                text.rfind("\n", search_start, end),
                text.rfind(" ", search_start, end),
            )
            if boundary > start:
                end = boundary + 1

        yield start, text[start:end]
        if end >= text_length:
            break
        start = max(start + 1, end - overlap)


def _google_text_findings_chunked(
    *,
    sdp: GoogleSDPClient,
    text: str,
    targets: Sequence[SensitiveDataType],
    exclusions: set[str],
    min_likelihood: str,
) -> list[TextFinding]:
    findings: list[TextFinding] = []
    for offset, chunk in _text_chunks(text):
        for finding in _google_text_findings(
            sdp=sdp,
            text=chunk,
            targets=targets,
            exclusions=exclusions,
            min_likelihood=min_likelihood,
        ):
            findings.append(
                TextFinding(
                    start=finding.start + offset,
                    end=finding.end + offset,
                    quote=finding.quote,
                    label=finding.label,
                    source=finding.source,
                )
            )
    return _dedupe_findings(findings)


def _image_bounding_boxes(finding: Any) -> Iterable[tuple[int, int, int, int]]:
    location = getattr(finding, "location", None)
    for content_location in getattr(location, "content_locations", None) or []:
        image_location = getattr(content_location, "image_location", None)
        for box in getattr(image_location, "bounding_boxes", None) or []:
            left = int(getattr(box, "left", 0))
            top = int(getattr(box, "top", 0))
            width = int(getattr(box, "width", 0))
            height = int(getattr(box, "height", 0))
            if width > 0 and height > 0:
                yield left, top, left + width, top + height


def inspect_sensitive_image(
    *,
    image_bytes: bytes,
    targets: Sequence[SensitiveDataType],
    review_exclusions: Sequence[str] = (),
    image_type: str = "IMAGE_JPEG",
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> list[ImageFinding]:
    """Inspect image pixels and return canonical, pixel-aligned findings."""
    if not image_bytes:
        return []

    resolved = _coerce_sdp(
        sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )
    info_types = _google_image_info_types_for_targets(targets)
    if not info_types:
        return []

    response = resolved.client.inspect_content(
        request={
            "parent": resolved.parent,
            "inspect_config": {
                "info_types": info_types,
                "include_quote": True,
                "min_likelihood": min_likelihood or resolved.min_likelihood,
            },
            "item": {
                "byte_item": {
                    "type_": image_type,
                    "data": image_bytes,
                }
            },
        }
    )

    exclusions = _normalized_exclusions(review_exclusions)
    findings: list[ImageFinding] = []
    seen: set[tuple[int, int, int, int, str, str]] = set()

    for finding in getattr(getattr(response, "result", None), "findings", []) or []:
        info_type = str(
            getattr(getattr(finding, "info_type", None), "name", "sensitive_data")
        )
        label = _canonical_google_label(info_type, targets)
        if label is None:
            continue
        quote = (getattr(finding, "quote", "") or "").strip()
        if not quote and label == SensitiveDataType.signature.value:
            quote = "visual signature"
        if quote and _normalize_text_for_compare(quote) in exclusions:
            continue

        for bbox in _image_bounding_boxes(finding):
            key = (*bbox, label, quote)
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                ImageFinding(
                    bbox=bbox,
                    quote=quote,
                    label=label,
                    source="google_sdp_image",
                )
            )

    return findings


def inspect_sensitive_text(
    *,
    text: str,
    targets: Sequence[SensitiveDataType],
    review_exclusions: Sequence[str] = (),
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> list[TextFinding]:
    if not text:
        return []

    resolved = _coerce_sdp(
        sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )
    exclusions = _normalized_exclusions(review_exclusions)
    google_items = _google_text_findings_chunked(
        sdp=resolved,
        text=text,
        targets=targets,
        exclusions=exclusions,
        min_likelihood=min_likelihood or resolved.min_likelihood,
    )
    local_items = _local_regex_findings(text, targets, exclusions=exclusions)
    return _dedupe_findings([*google_items, *local_items])


def preview_candidates_from_text(
    *,
    text: str,
    targets: Sequence[SensitiveDataType],
    review_exclusions: Sequence[str] = (),
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> list[DetectionCandidate]:
    grouped: dict[tuple[str, str, str], int] = {}
    for finding in inspect_sensitive_text(
        sdp=sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
        text=text,
        targets=targets,
        review_exclusions=review_exclusions,
    ):
        key = (finding.label, finding.quote, finding.source)
        grouped[key] = grouped.get(key, 0) + 1

    return [
        DetectionCandidate(label=label, quote=quote, occurrences=count, source=source)
        for (label, quote, source), count in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    ]


__all__ = [
    "DEFAULT_DLP_LOCATION",
    "DEFAULT_MIN_LIKELIHOOD",
    "DetectionCandidate",
    "ImageFinding",
    "TextFinding",
    "GoogleSDPClient",
    "build_google_sdp_client",
    "id_document_field_findings",
    "inspect_local_sensitive_text",
    "inspect_sensitive_image",
    "inspect_sensitive_text",
    "is_id_document_payload",
    "preview_candidates_from_text",
    "merge_overlapping_findings",
]