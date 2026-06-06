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

VERSION_FILE_PATTERN = re.compile(r"^v\d{4}_\d{2}\.json$")
SAFE_RULE_SEGMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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
        payload = json.loads(rule_file.read_text(encoding="utf-8-sig"))

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

        effective_version = str(payload.get("pack_version") or rule_file.stem)
        pack_metadata = (
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        )

        rules: list[ComplianceRuleDefinition] = []
        for raw_rule in rules_data:
            rule = self._parse_rule(raw_rule)
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
        if not evaluation:
            raise RuleRegistryError(
                f"Rule {rule_id} must define a non-empty evaluation object."
            )

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


__all__ = [
    "ComplianceRuleDefinition",
    "ComplianceRuleRegistry",
    "LoadedRulePack",
    "RuleRegistryError",
    "VERSION_FILE_PATTERN",
]