from __future__ import annotations

"""
Registry and loader for versioned compliance rule packs.

This module intentionally separates rule content from evaluation logic.
Rule files are expected to live under a rules directory such as:

    compliance/rules/<jurisdiction>/<sector_pack>/v2025_01.json

Examples:

    compliance/rules/nigeria/accounting/v2025_01.json
    compliance/rules/sa/accounting/v2025_01.json
    compliance/rules/us/banking_and_fintech/v2025_01.json

The registry is jurisdiction-agnostic. Supported jurisdictions are determined by:
1. schema.py request validation
2. available rule-pack folders under compliance/rules/
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import hashlib
import json
import re

try:
    from backend.src.schema import (
        ComplianceJurisdiction,
        ComplianceRegulatoryDomain,
        ComplianceRequest,
        ComplianceSectorPack,
    )
except ImportError:  # pragma: no cover
    from backend.src.schema import (
        ComplianceJurisdiction,
        ComplianceRegulatoryDomain,
        ComplianceRequest,
        ComplianceSectorPack,
    )

VERSION_FILE_PATTERN = re.compile(r"^v\d{4}_(?:0[1-9]|1[0-2])\.json$")
SAFE_RULE_SEGMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SAFE_RULE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SUPPORTED_EVALUATION_STRATEGIES = frozenset(
    {"any_signal_presence", "all_signal_presence", "absent_signals", "min_signal_count"}
)
SUPPORTED_SEARCH_MODES = frozenset({"substring", "exact_phrase", "word", "regex"})
MAX_RULE_PACK_BYTES = 10 * 1024 * 1024
MAX_RULES_PER_PACK = 10_000
MAX_SIGNALS_PER_RULE = 200
MAX_SIGNAL_CHARACTERS = 512
MAX_RULE_ID_CHARACTERS = 160
MAX_RULE_TITLE_CHARACTERS = 500
MAX_RULE_SUMMARY_CHARACTERS = 10_000


@dataclass(frozen=True)
class ComplianceRuleDefinition:
    rule_id: str
    rule_version: str
    title: str
    regulatory_domain: Optional[ComplianceRegulatoryDomain]
    summary: str
    evaluation: dict[str, Any]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class LoadedRulePack:
    jurisdiction: ComplianceJurisdiction
    sector_pack: ComplianceSectorPack
    version: str
    source_path: Path
    rules: tuple[ComplianceRuleDefinition, ...]
    metadata: dict[str, Any]
    checksum_sha256: str


class RuleRegistryError(RuntimeError):
    """Raised when rule-pack discovery or loading fails."""


class ComplianceRuleRegistry:
    def __init__(self, rules_root: Optional[str | Path] = None) -> None:
        self.rules_root = self._resolve_rules_root(rules_root)

    def load_request_rule_packs(
        self,
        payload: ComplianceRequest,
        *,
        version_overrides: Optional[dict[ComplianceSectorPack, str]] = None,
    ) -> list[LoadedRulePack]:
        domain_filter = set(payload.regulatory_domains)
        loaded: list[LoadedRulePack] = []

        for sector_pack in payload.sector_packs:
            requested_version = self._get_version_override(
                version_overrides,
                sector_pack,
            )

            pack = self.load_pack(
                jurisdiction=payload.jurisdiction,
                sector_pack=sector_pack,
                version=requested_version,
                regulatory_domains=domain_filter,
            )
            loaded.append(pack)

        return loaded

    def load_pack(
        self,
        *,
        jurisdiction: ComplianceJurisdiction,
        sector_pack: ComplianceSectorPack,
        version: Optional[str] = None,
        regulatory_domains: Optional[set[ComplianceRegulatoryDomain]] = None,
    ) -> LoadedRulePack:
        jurisdiction_value = _safe_rule_segment(
            _enum_value(jurisdiction),
            label="jurisdiction",
        )
        sector_pack_value = _safe_rule_segment(
            _enum_value(sector_pack),
            label="sector_pack",
        )

        pack_dir = self.rules_root / jurisdiction_value / sector_pack_value

        if not pack_dir.exists() or not pack_dir.is_dir():
            raise RuleRegistryError(
                f"Rule-pack directory does not exist: {pack_dir}. "
                f"Expected path: compliance/rules/{jurisdiction_value}/{sector_pack_value}/vYYYY_MM.json"
            )

        rule_file = self._resolve_version_file(pack_dir, version)
        try:
            raw_bytes = rule_file.read_bytes()
        except OSError as exc:
            raise RuleRegistryError(f"Rule-pack file could not be read: {rule_file}") from exc
        if not raw_bytes:
            raise RuleRegistryError(f"Rule-pack file is empty: {rule_file}")
        if len(raw_bytes) > MAX_RULE_PACK_BYTES:
            raise RuleRegistryError(
                f"Rule-pack file exceeds the {MAX_RULE_PACK_BYTES}-byte safety limit: {rule_file}"
            )
        try:
            payload = json.loads(raw_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuleRegistryError(f"Rule-pack file is not valid UTF-8 JSON: {rule_file}") from exc
        if not isinstance(payload, dict):
            raise RuleRegistryError(f"Rule-pack root must be a JSON object: {rule_file}")
        checksum_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        self._validate_pack_identity(
            payload=payload,
            rule_file=rule_file,
            expected_jurisdiction=jurisdiction_value,
            expected_sector_pack=sector_pack_value,
        )

        rules_data = payload.get("rules")
        if not isinstance(rules_data, list) or not rules_data:
            raise RuleRegistryError(
                f"Rule-pack {rule_file} does not define a non-empty 'rules' list."
            )
        if len(rules_data) > MAX_RULES_PER_PACK:
            raise RuleRegistryError(
                f"Rule-pack {rule_file} exceeds the {MAX_RULES_PER_PACK}-rule safety limit."
            )

        effective_version = str(payload.get("pack_version") or rule_file.stem).strip()
        if effective_version != rule_file.stem:
            raise RuleRegistryError(
                f"Rule-pack version mismatch in {rule_file}: "
                f"expected '{rule_file.stem}', found '{effective_version}'."
            )
        pack_metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        )

        rules: list[ComplianceRuleDefinition] = []
        seen_rule_ids: set[str] = set()
        for raw_rule in rules_data:
            rule = self._parse_rule(raw_rule)
            if rule.rule_id in seen_rule_ids:
                raise RuleRegistryError(
                    f"Rule-pack {rule_file} contains duplicate rule_id '{rule.rule_id}'."
                )
            seen_rule_ids.add(rule.rule_id)
            if (
                regulatory_domains
                and rule.regulatory_domain is not None
                and rule.regulatory_domain not in regulatory_domains
            ):
                continue
            rules.append(rule)

        if not rules:
            raise RuleRegistryError(
                f"Rule-pack {rule_file} has no rules left after applying the requested regulatory-domain filter."
            )

        return LoadedRulePack(
            jurisdiction=jurisdiction,
            sector_pack=sector_pack,
            version=effective_version,
            source_path=rule_file,
            rules=tuple(rules),
            metadata=pack_metadata,
            checksum_sha256=checksum_sha256,
        )

    def list_available_versions(
        self,
        *,
        jurisdiction: ComplianceJurisdiction,
        sector_pack: ComplianceSectorPack,
    ) -> list[str]:
        jurisdiction_value = _safe_rule_segment(
            _enum_value(jurisdiction),
            label="jurisdiction",
        )
        sector_pack_value = _safe_rule_segment(
            _enum_value(sector_pack),
            label="sector_pack",
        )

        pack_dir = self.rules_root / jurisdiction_value / sector_pack_value
        if not pack_dir.exists() or not pack_dir.is_dir():
            return []

        return sorted(
            path.stem
            for path in pack_dir.iterdir()
            if path.is_file() and VERSION_FILE_PATTERN.match(path.name)
        )

    def list_available_jurisdictions(self) -> list[str]:
        if not self.rules_root.exists() or not self.rules_root.is_dir():
            return []

        return sorted(
            path.name
            for path in self.rules_root.iterdir()
            if path.is_dir() and SAFE_RULE_SEGMENT_PATTERN.match(path.name)
        )

    def list_available_sector_packs(
        self,
        *,
        jurisdiction: ComplianceJurisdiction,
    ) -> list[str]:
        jurisdiction_value = _safe_rule_segment(
            _enum_value(jurisdiction),
            label="jurisdiction",
        )

        jurisdiction_dir = self.rules_root / jurisdiction_value
        if not jurisdiction_dir.exists() or not jurisdiction_dir.is_dir():
            return []

        return sorted(
            path.name
            for path in jurisdiction_dir.iterdir()
            if path.is_dir() and SAFE_RULE_SEGMENT_PATTERN.match(path.name)
        )

    def list_available_regulatory_domains(
        self,
        *,
        jurisdiction: ComplianceJurisdiction,
        sector_pack: ComplianceSectorPack,
    ) -> list[str]:
        """Return domains that have at least one rule in the latest pack."""
        try:
            pack = self.load_pack(
                jurisdiction=jurisdiction,
                sector_pack=sector_pack,
                regulatory_domains=None,
            )
        except RuleRegistryError:
            return []

        return sorted(
            {rule.regulatory_domain.value for rule in pack.rules if rule.regulatory_domain is not None}
        )

    def _latest_version_file(self, pack_dir: Path) -> Path:
        candidates = sorted(
            [
                path
                for path in pack_dir.iterdir()
                if path.is_file() and VERSION_FILE_PATTERN.match(path.name)
            ],
            key=lambda item: item.stem,
        )
        if not candidates:
            raise RuleRegistryError(f"No versioned rule files found in {pack_dir}.")
        return candidates[-1]

    def _resolve_version_file(self, pack_dir: Path, version: Optional[str]) -> Path:
        if version in (None, ""):
            return self._latest_version_file(pack_dir)

        normalized_stem = Path(str(version).strip()).stem
        if not normalized_stem:
            raise RuleRegistryError("Requested rule-pack version is empty.")

        candidate = pack_dir / f"{normalized_stem}.json"
        if not candidate.exists():
            raise RuleRegistryError(f"Rule-pack version file does not exist: {candidate}")

        if not VERSION_FILE_PATTERN.match(candidate.name):
            raise RuleRegistryError(
                f"Rule-pack version file must match vYYYY_MM.json: {candidate.name}"
            )

        return candidate

    def _resolve_rules_root(self, rules_root: Optional[str | Path]) -> Path:
        if rules_root is not None:
            return Path(rules_root)
        return Path(__file__).resolve().parent / "rules"

    def _parse_rule(self, raw_rule: dict[str, Any]) -> ComplianceRuleDefinition:
        if not isinstance(raw_rule, dict):
            raise RuleRegistryError("Each rule must be an object.")

        rule_id = str(raw_rule.get("rule_id") or "").strip()
        rule_version = str(raw_rule.get("rule_version") or "").strip()
        title = str(raw_rule.get("title") or "").strip()
        summary = str(raw_rule.get("summary") or "").strip()
        evaluation = (
            raw_rule.get("evaluation")
            if isinstance(raw_rule.get("evaluation"), dict)
            else {}
        )
        metadata = (
            raw_rule.get("metadata")
            if isinstance(raw_rule.get("metadata"), dict)
            else {}
        )

        if not rule_id or not rule_version or not title or not summary:
            raise RuleRegistryError(
                "Each rule must define rule_id, rule_version, title, and summary."
            )
        if len(rule_id) > MAX_RULE_ID_CHARACTERS or not SAFE_RULE_ID_PATTERN.match(rule_id):
            raise RuleRegistryError(
                f"Rule id '{rule_id[:80]}' is invalid or longer than "
                f"{MAX_RULE_ID_CHARACTERS} characters."
            )
        if len(title) > MAX_RULE_TITLE_CHARACTERS:
            raise RuleRegistryError(
                f"Rule {rule_id} title exceeds {MAX_RULE_TITLE_CHARACTERS} characters."
            )
        if len(summary) > MAX_RULE_SUMMARY_CHARACTERS:
            raise RuleRegistryError(
                f"Rule {rule_id} summary exceeds {MAX_RULE_SUMMARY_CHARACTERS} characters."
            )
        if not evaluation:
            raise RuleRegistryError(
                f"Rule {rule_id} must define a non-empty evaluation object."
            )
        self._validate_evaluation(rule_id=rule_id, evaluation=evaluation)

        domain_value = raw_rule.get("regulatory_domain")
        regulatory_domain = None
        if domain_value not in (None, ""):
            try:
                regulatory_domain = ComplianceRegulatoryDomain(str(domain_value))
            except ValueError as exc:
                raise RuleRegistryError(
                    f"Unsupported regulatory domain for rule {rule_id}: {domain_value}"
                ) from exc

        return ComplianceRuleDefinition(
            rule_id=rule_id,
            rule_version=rule_version,
            title=title,
            regulatory_domain=regulatory_domain,
            summary=summary,
            evaluation=evaluation,
            metadata=metadata,
        )

    def _validate_evaluation(
        self,
        *,
        rule_id: str,
        evaluation: dict[str, Any],
    ) -> None:
        strategy = str(evaluation.get("strategy") or "").strip()
        if strategy not in SUPPORTED_EVALUATION_STRATEGIES:
            raise RuleRegistryError(
                f"Rule {rule_id} uses unsupported evaluation strategy '{strategy}'."
            )

        search_mode = str(evaluation.get("search_mode") or "substring").strip().lower()
        if search_mode not in SUPPORTED_SEARCH_MODES:
            raise RuleRegistryError(
                f"Rule {rule_id} uses unsupported search_mode '{search_mode}'."
            )
        if "case_sensitive" in evaluation and not isinstance(
            evaluation["case_sensitive"], bool
        ):
            raise RuleRegistryError(f"Rule {rule_id} case_sensitive must be a boolean.")

        allowed_statuses = {
            "evidence_found",
            "risk_detected",
            "warning",
            "evidence_missing",
            "requires_review",
        }
        for field in ("on_missing", "on_partial", "on_prohibited_match"):
            value = evaluation.get(field)
            if value not in (None, "") and str(value) not in allowed_statuses:
                raise RuleRegistryError(
                    f"Rule {rule_id} field '{field}' contains unsupported status '{value}'."
                )

        signal_groups: dict[str, list[str]] = {}
        for key in ("signals", "required_signals", "optional_signals", "prohibited_signals"):
            raw_signals = evaluation.get(key, [])
            if raw_signals in (None, ""):
                raw_signals = []
            if not isinstance(raw_signals, list):
                raise RuleRegistryError(f"Rule {rule_id} field '{key}' must be a list.")
            if len(raw_signals) > MAX_SIGNALS_PER_RULE:
                raise RuleRegistryError(
                    f"Rule {rule_id} field '{key}' exceeds the {MAX_SIGNALS_PER_RULE}-signal limit."
                )
            normalized: list[str] = []
            seen: set[str] = set()
            for raw_signal in raw_signals:
                signal = str(raw_signal or "").strip()
                if not signal:
                    raise RuleRegistryError(f"Rule {rule_id} field '{key}' contains an empty signal.")
                if len(signal) > MAX_SIGNAL_CHARACTERS:
                    raise RuleRegistryError(
                        f"Rule {rule_id} contains a signal longer than {MAX_SIGNAL_CHARACTERS} characters."
                    )
                identity = signal if evaluation.get("case_sensitive") else signal.casefold()
                if identity in seen:
                    continue
                seen.add(identity)
                normalized.append(signal)
                if search_mode == "regex":
                    _validate_safe_regex(signal, rule_id=rule_id)
            signal_groups[key] = normalized

        required = signal_groups["required_signals"] or signal_groups["signals"]
        prohibited = signal_groups["prohibited_signals"]
        optional = signal_groups["optional_signals"]
        if strategy in {"any_signal_presence", "all_signal_presence"} and not required:
            raise RuleRegistryError(f"Rule {rule_id} requires at least one required signal.")
        if strategy == "absent_signals" and not prohibited:
            raise RuleRegistryError(f"Rule {rule_id} requires at least one prohibited signal.")
        if strategy == "min_signal_count":
            available = len(set([*required, *optional]))
            if available == 0:
                raise RuleRegistryError(f"Rule {rule_id} requires signals for min_signal_count.")
            min_count = _bounded_int(
                evaluation.get("min_count", max(1, len(required))),
                label=f"Rule {rule_id} min_count",
                minimum=1,
                maximum=available,
            )
            if min_count > available:
                raise RuleRegistryError(
                    f"Rule {rule_id} min_count cannot exceed its distinct signal count."
                )

        _bounded_int(
            evaluation.get("excerpt_window", 160),
            label=f"Rule {rule_id} excerpt_window",
            minimum=40,
            maximum=1000,
        )
        _bounded_int(
            evaluation.get("max_matches_per_signal", 5),
            label=f"Rule {rule_id} max_matches_per_signal",
            minimum=1,
            maximum=25,
        )

    def _get_version_override(
        self,
        version_overrides: Optional[dict[ComplianceSectorPack, str]],
        sector_pack: ComplianceSectorPack,
    ) -> Optional[str]:
        if not version_overrides:
            return None

        if sector_pack in version_overrides:
            return version_overrides[sector_pack]

        sector_pack_value = _enum_value(sector_pack)
        for key, value in version_overrides.items():
            if _enum_value(key) == sector_pack_value:
                return value

        return None

    def _validate_pack_identity(
        self,
        *,
        payload: dict[str, Any],
        rule_file: Path,
        expected_jurisdiction: str,
        expected_sector_pack: str,
    ) -> None:
        actual_jurisdiction = str(payload.get("jurisdiction") or "").strip()
        if actual_jurisdiction and actual_jurisdiction != expected_jurisdiction:
            raise RuleRegistryError(
                f"Rule-pack jurisdiction mismatch in {rule_file}: "
                f"expected '{expected_jurisdiction}', found '{actual_jurisdiction}'."
            )

        actual_pack_id = str(payload.get("pack_id") or "").strip()
        actual_sector_pack = str(payload.get("sector_pack") or "").strip()

        accepted_pack_ids = {
            expected_sector_pack,
            f"{expected_jurisdiction}.{expected_sector_pack}",
        }

        if actual_pack_id and actual_pack_id not in accepted_pack_ids:
            raise RuleRegistryError(
                f"Rule-pack id mismatch in {rule_file}: "
                f"expected one of {sorted(accepted_pack_ids)}, found '{actual_pack_id}'."
            )

        if actual_sector_pack and actual_sector_pack != expected_sector_pack:
            raise RuleRegistryError(
                f"Rule-pack sector_pack mismatch in {rule_file}: "
                f"expected '{expected_sector_pack}', found '{actual_sector_pack}'."
            )


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip()


def _safe_rule_segment(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise RuleRegistryError(f"Empty {label} is not allowed.")
    if not SAFE_RULE_SEGMENT_PATTERN.match(normalized):
        raise RuleRegistryError(
            f"Invalid {label} '{value}'. Only lowercase letters, numbers, underscores, and hyphens are allowed."
        )
    return normalized


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise RuleRegistryError(f"{label} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RuleRegistryError(f"{label} must be an integer.") from exc
    if parsed < minimum or parsed > maximum:
        raise RuleRegistryError(
            f"{label} must be between {minimum} and {maximum}; received {parsed}."
        )
    return parsed


def _validate_safe_regex(pattern: str, *, rule_id: str) -> None:
    """Reject constructs that are unsafe or non-portable in an untrusted rule pack."""
    if "\x00" in pattern:
        raise RuleRegistryError(f"Rule {rule_id} contains a NUL byte in a regex signal.")
    forbidden_fragments = ("(?<=", "(?<!", "(?P=", "\\g<", "(?(")
    if any(fragment in pattern for fragment in forbidden_fragments) or re.search(
        r"\\[1-9]", pattern
    ):
        raise RuleRegistryError(
            f"Rule {rule_id} regex uses look-behind, backreferences, or conditionals, "
            "which are not allowed in compliance rule packs."
        )
    # Nested and repeated broad quantifiers are the most common catastrophic-
    # backtracking shapes. Rules should express bounded documentary phrases.
    if re.search(r"\([^)]*[+*][^)]*\)[+*{]", pattern) or re.search(
        r"(?:\.\*|\.\+){2,}", pattern
    ) or re.search(r"\([^)]*\|[^)]*\)[+*{]", pattern):
        raise RuleRegistryError(
            f"Rule {rule_id} regex contains an unsafe nested or repeated quantifier."
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise RuleRegistryError(f"Rule {rule_id} contains an invalid regex: {exc}.") from exc


__all__ = [
    "ComplianceRuleDefinition",
    "ComplianceRuleRegistry",
    "LoadedRulePack",
    "RuleRegistryError",
    "VERSION_FILE_PATTERN",
]
