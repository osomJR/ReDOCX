from __future__ import annotations
import base64
import binascii
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from backend.src.schema import SensitiveDataType

DEFAULT_MIN_LIKELIHOOD = "POSSIBLE"
DEFAULT_DLP_LOCATION = "global"


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

    credentials_path = Path("/tmp/google-service-account.json")
    credentials_path.write_text(credentials_content, encoding="utf-8")
    credentials_path.chmod(0o600)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)


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


def _is_valid_structured_local_quote(target: SensitiveDataType, quote: str) -> bool:
    """Reject context-only false positives such as "Account Management".

    Local rules use nearby words like Account, ID, and TIN as context. The
    captured value still needs identifier-like structure; plain alphabetic
    business terms must not become findings just because they follow those
    labels.
    """
    digits = _digit_count(quote)

    if target == SensitiveDataType.account_number:
        return digits >= 6

    if target in {
        SensitiveDataType.national_id,
        SensitiveDataType.tax_id,
        SensitiveDataType.passport_number,
    }:
        return digits >= 4

    if target in {SensitiveDataType.date_of_birth, SensitiveDataType.age}:
        return digits > 0

    return True


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
        if any(
            f" { _normalize_text_for_compare(country) }" in f" {normalized_part} "
            for country in _country_names_for_location_regex()
        ):
            return False

    return True


_GOOGLE_INFOTYPES: dict[SensitiveDataType, list[str]] = {
    SensitiveDataType.name: ["PERSON_NAME"],
    SensitiveDataType.email_address: ["EMAIL_ADDRESS"],
    SensitiveDataType.phone_number: ["PHONE_NUMBER"],
    SensitiveDataType.card_number: ["CREDIT_CARD_NUMBER"],
    SensitiveDataType.contact_address: ["STREET_ADDRESS"],
    SensitiveDataType.passport_number: ["PASSPORT"],
}

_GOOGLE_INFOTYPE_LABELS: dict[str, str] = {
    "PERSON_NAME": SensitiveDataType.name.value,
    "EMAIL_ADDRESS": SensitiveDataType.email_address.value,
    "PHONE_NUMBER": SensitiveDataType.phone_number.value,
    "CREDIT_CARD_NUMBER": SensitiveDataType.card_number.value,
    "STREET_ADDRESS": SensitiveDataType.contact_address.value,
    # LOCATION is deliberately not requested for contact_address because it is too broad.
    "PASSPORT": SensitiveDataType.passport_number.value,
}

_LOCAL_REGEX_RULES: dict[SensitiveDataType, list[re.Pattern[str]]] = {
    SensitiveDataType.account_number: [
        re.compile(r"(?i)\b(?:account|acct|a/c)\s*(?:number|no\.?)?\s*[:#-]?\s*([A-Z0-9\-]{6,34})\b")
    ],
    SensitiveDataType.card_number: [
        re.compile(r"\b(?:\d[ -]*?){13,19}\b")
    ],
    SensitiveDataType.national_id: [
        re.compile(r"(?i)\b(?:national\s+id|id\s+number|nin)\s*[:#-]?\s*([A-Z0-9\-]{5,30})\b")
    ],
    SensitiveDataType.tax_id: [
        re.compile(r"(?i)\b(?:tax\s+id|tin|vat(?:\s+number)?)\s*[:#-]?\s*([A-Z0-9\-]{5,30})\b")
    ],
    SensitiveDataType.date_of_birth: [
        re.compile(
            r"(?i)\b(?:dob|date\s+of\s+birth)\s*[:#-]?\s*("
            r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
            r"\d{4}[/-]\d{1,2}[/-]\d{1,2}|"
            r"[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}"
            r")\b"
        )
    ],
    SensitiveDataType.age: [
        re.compile(r"(?i)\bage\s*[:#-]?\s*(\d{1,3})\b"),
        re.compile(r"(?i)\b(\d{1,3})\s+years?\s+old\b"),
    ],
    SensitiveDataType.contact_address: [
        re.compile(r"(?i)\b(?:address|contact\s+address)\s*[:#-]?\s*(.+)"),
        _GLOBAL_CITY_REGION_COUNTRY_RE,
        _GLOBAL_PREFIXED_PLACE_COUNTRY_RE,
        _GLOBAL_SINGLE_PLACE_COUNTRY_RE,
    ],
    SensitiveDataType.signature: [
        re.compile(r"(?i)\b(?:signature|signed\s+by|signatory)\b[:#-]?\s*([A-Za-z][^\n\r]*)?")
    ],
}

_CAPTURED_GROUP_ONLY = {
    SensitiveDataType.account_number,
    SensitiveDataType.national_id,
    SensitiveDataType.tax_id,
    SensitiveDataType.date_of_birth,
    SensitiveDataType.age,
}


def _google_info_types_for_targets(targets: Sequence[SensitiveDataType]) -> list[dict[str, str]]:
    names: list[str] = []
    for target in targets:
        for name in _GOOGLE_INFOTYPES.get(target, []):
            if name not in names:
                names.append(name)
    return [{"name": name} for name in names]


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
                quote = match.group(0)
                if target in _CAPTURED_GROUP_ONLY and match.lastindex:
                    start, end = match.span(1)
                    quote = match.group(1)
                quote = quote.strip()
                if not quote:
                    continue
                if any(pattern is rule for rule in _LOCATION_FALLBACK_RULES) and not _is_valid_city_region_country_quote(quote):
                    continue
                if not _is_valid_structured_local_quote(target, quote):
                    continue
                if _normalize_text_for_compare(quote) in exclusions:
                    continue
                findings.append(
                    TextFinding(start=start, end=end, quote=quote, label=target.value, source="local_rule")
                )
    return findings


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
        label = _GOOGLE_INFOTYPE_LABELS.get(info_type.upper(), info_type.lower())
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


def inspect_sensitive_text(
    *,
    text: str,
    targets: Sequence[SensitiveDataType],
    review_exclusions: Sequence[str],
    sdp: GoogleSDPClient | None = None,
    project_id: str | None = None,
    location: str = DEFAULT_DLP_LOCATION,
    min_likelihood: str = DEFAULT_MIN_LIKELIHOOD,
    client: Any | None = None,
) -> list[TextFinding]:
    resolved = _coerce_sdp(
        sdp,
        project_id=project_id,
        location=location,
        min_likelihood=min_likelihood,
        client=client,
    )
    exclusions = _normalized_exclusions(review_exclusions)
    google_items = _google_text_findings(
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
    review_exclusions: Sequence[str],
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
    "TextFinding",
    "GoogleSDPClient",
    "build_google_sdp_client",
    "inspect_sensitive_text",
    "preview_candidates_from_text",
    "merge_overlapping_findings",
]
