from __future__ import annotations

"""
Deterministic compliance evaluators for ReDOCX compliance screening.

The evaluator is intentionally evidence-first and conservative. It does not make
legal approval claims. It classifies source-only signals into product-safe
screening states:
- evidence_found
- evidence_missing
- risk_detected
- warning
- requires_review

Every exported report still requires human review before reliance or final use.
"""

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    from backend.src.schema import (
        ComplianceCheckStatus,
        ComplianceCounts,
        ComplianceJurisdiction,
        ComplianceOverallStatus,
        ComplianceRuleResult,
        ComplianceSectorPack,
        SystemLanguage,
    )
except ImportError:  # pragma: no cover
    from backend.src.schema import (
        ComplianceCheckStatus,
        ComplianceCounts,
        ComplianceJurisdiction,
        ComplianceOverallStatus,
        ComplianceRuleResult,
        ComplianceSectorPack,
        SystemLanguage,
    )

try:
    from .evidence import EvidenceDocument, collect_evidence_references
    from .registry import ComplianceRuleDefinition, LoadedRulePack
except ImportError:  # pragma: no cover
    from backend.src.processing.compliance.evidence import EvidenceDocument, collect_evidence_references
    from backend.src.processing.compliance.registry import ComplianceRuleDefinition, LoadedRulePack


class ComplianceEvaluationError(RuntimeError):
    """Raised when a rule definition cannot be evaluated safely."""


@dataclass(frozen=True)
class EvaluatedRule:
    result: ComplianceRuleResult
    matched_required_signals: tuple[str, ...]
    matched_optional_signals: tuple[str, ...]
    matched_prohibited_signals: tuple[str, ...] = ()
    missing_required_signals: tuple[str, ...] = ()


def evaluate_rule_packs(
    documents: Sequence[EvidenceDocument],
    packs: Sequence[LoadedRulePack],
    *,
    system_language: SystemLanguage | str | None = None,
) -> list[ComplianceRuleResult]:
    results: list[ComplianceRuleResult] = []
    for pack in packs:
        for rule in pack.rules:
            evaluated = evaluate_rule(
                documents,
                rule,
                jurisdiction=pack.jurisdiction,
                sector_pack=pack.sector_pack,
                pack_metadata=pack.metadata,
                system_language=system_language,
            )
            results.append(evaluated.result)
    return results


def evaluate_rule(
    documents: Sequence[EvidenceDocument],
    rule: ComplianceRuleDefinition,
    *,
    jurisdiction: ComplianceJurisdiction | None = None,
    sector_pack: ComplianceSectorPack | None = None,
    pack_metadata: Optional[Mapping[str, Any]] = None,
    system_language: SystemLanguage | str | None = None,
) -> EvaluatedRule:
    evaluation = rule.evaluation
    strategy = str(evaluation.get("strategy") or "").strip()
    if not strategy:
        raise ComplianceEvaluationError(f"Rule {rule.rule_id} does not define an evaluation strategy.")

    required_signals = _normalize_signal_list(evaluation.get("required_signals") or evaluation.get("signals") or [])
    optional_signals = _normalize_signal_list(evaluation.get("optional_signals") or [])
    prohibited_signals = _normalize_signal_list(evaluation.get("prohibited_signals") or [])
    search_mode = str(evaluation.get("search_mode") or "substring")
    case_sensitive = bool(evaluation.get("case_sensitive", False))
    excerpt_window = int(evaluation.get("excerpt_window", 160))
    max_matches_per_signal = int(evaluation.get("max_matches_per_signal", 5))
    min_count = int(evaluation.get("min_count", max(1, len(required_signals))))
    missing_status = _coerce_status(evaluation.get("on_missing"), ComplianceCheckStatus.evidence_missing)
    partial_status = _coerce_status(evaluation.get("on_partial"), ComplianceCheckStatus.requires_review)
    prohibited_match_status = _coerce_status(
        evaluation.get("on_prohibited_match"),
        ComplianceCheckStatus.risk_detected,
    )

    references_by_signal = {
        signal: collect_evidence_references(
            documents,
            signals=[signal],
            search_mode=search_mode,
            case_sensitive=case_sensitive,
            excerpt_window=excerpt_window,
            max_matches_per_signal=max_matches_per_signal,
        )
        for signal in [*required_signals, *optional_signals, *prohibited_signals]
    }

    matched_required = tuple(signal for signal in required_signals if references_by_signal.get(signal))
    matched_optional = tuple(signal for signal in optional_signals if references_by_signal.get(signal))
    matched_prohibited = tuple(signal for signal in prohibited_signals if references_by_signal.get(signal))
    missing_required = tuple(signal for signal in required_signals if signal not in matched_required)

    status: ComplianceCheckStatus
    summary: str
    evidence_references = []

    if strategy == "any_signal_presence":
        if matched_required:
            status = ComplianceCheckStatus.evidence_found
            summary = _summary(rule.summary, "Required source evidence was found. Human review is still required.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No required source evidence was found.")

    elif strategy == "all_signal_presence":
        if required_signals and len(matched_required) == len(required_signals):
            status = ComplianceCheckStatus.evidence_found
            summary = _summary(rule.summary, "All required source evidence signals were found. Human review is still required.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        elif matched_required:
            status = partial_status
            summary = _summary(rule.summary, "Only partial required source evidence was found; human review is required.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No required source evidence was found.")

    elif strategy == "absent_signals":
        if matched_prohibited:
            status = prohibited_match_status
            summary = _summary(rule.summary, "Evidence of a prohibited or risky signal was found.")
            evidence_references = _flatten_references(references_by_signal, matched_prohibited)
        else:
            status = ComplianceCheckStatus.requires_review
            summary = _summary(rule.summary, "No prohibited signal was found by deterministic screening; human review is required to confirm absence.")

    elif strategy == "min_signal_count":
        distinct_matches = tuple(sorted(set(matched_required) | set(matched_optional)))
        if len(distinct_matches) >= min_count:
            status = ComplianceCheckStatus.evidence_found
            summary = _summary(rule.summary, f"Minimum source-evidence threshold of {min_count} signals was met. Human review is still required.")
            evidence_references = _flatten_references(references_by_signal, distinct_matches)
        elif distinct_matches:
            status = partial_status
            summary = _summary(rule.summary, "Some source evidence was found but the minimum threshold was not met.")
            evidence_references = _flatten_references(references_by_signal, distinct_matches)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No qualifying source evidence was found.")

    else:
        raise ComplianceEvaluationError(f"Unsupported evaluation strategy for rule {rule.rule_id}: {strategy}")

    if status == ComplianceCheckStatus.evidence_found and not evidence_references:
        status = ComplianceCheckStatus.requires_review
        summary = _summary(rule.summary, "Signals appear present, but anchored evidence is too weak for a reliable screening finding.")

    plain_language_summary = _plain_language_rule_summary(
        status=status,
        system_language=system_language,
    )
    recommended_actions = _recommended_actions(
        rule=rule,
        status=status,
        jurisdiction=jurisdiction,
        sector_pack=sector_pack,
        matched_prohibited=matched_prohibited,
        missing_required=missing_required,
        pack_metadata=pack_metadata or {},
        system_language=system_language,
    )

    result = ComplianceRuleResult(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        title=rule.title,
        status=status,
        summary=summary,
        sector_pack=sector_pack,
        regulatory_domain=rule.regulatory_domain,
        plain_language_summary=plain_language_summary,
        recommended_actions=recommended_actions,
        matched_signals=list(
            dict.fromkeys(
                [*matched_required, *matched_optional, *matched_prohibited]
            )
        ),
        missing_signals=list(missing_required),
        evidence_references=evidence_references,
    )
    return EvaluatedRule(
        result=result,
        matched_required_signals=matched_required,
        matched_optional_signals=matched_optional,
        matched_prohibited_signals=matched_prohibited,
        missing_required_signals=missing_required,
    )


def build_counts(results: Iterable[ComplianceRuleResult]) -> ComplianceCounts:
    counts = {
        ComplianceCheckStatus.evidence_found: 0,
        ComplianceCheckStatus.risk_detected: 0,
        ComplianceCheckStatus.warning: 0,
        ComplianceCheckStatus.evidence_missing: 0,
        ComplianceCheckStatus.requires_review: 0,
    }
    for result in results:
        counts[result.status] += 1
    return ComplianceCounts(
        evidence_found=counts[ComplianceCheckStatus.evidence_found],
        risk_detected=counts[ComplianceCheckStatus.risk_detected],
        warning=counts[ComplianceCheckStatus.warning],
        evidence_missing=counts[ComplianceCheckStatus.evidence_missing],
        requires_review=counts[ComplianceCheckStatus.requires_review],
    )


def build_report_guidance(
    counts: ComplianceCounts,
    results: Sequence[ComplianceRuleResult],
    *,
    system_language: SystemLanguage | str | None = None,
) -> tuple[ComplianceOverallStatus, str, list[str]]:
    french = _is_french(system_language)
    needs_changes = (
        counts.risk_detected > 0
        or counts.warning > 0
        or counts.evidence_missing > 0
    )

    if needs_changes:
        overall_status = ComplianceOverallStatus.changes_recommended
        if french:
            summary = (
                "Des modifications sont recommandées avant l’examen final. "
                f"ReDOCX a relevé {counts.risk_detected} problème(s) potentiel(s), "
                f"{counts.evidence_missing} élément(s) introuvable(s) et "
                f"{counts.warning} avertissement(s)."
            )
        else:
            summary = (
                "Changes are recommended before final review. "
                f"ReDOCX found {counts.risk_detected} potential issue(s), "
                f"{counts.evidence_missing} missing item(s), and "
                f"{counts.warning} warning(s)."
            )
    elif counts.requires_review > 0:
        overall_status = ComplianceOverallStatus.manual_review_needed
        summary = (
            "Aucune modification automatique n’est certaine, mais une personne qualifiée "
            f"doit examiner {counts.requires_review} point(s) indécis."
            if french
            else
            "No automatic change is certain, but a qualified person must review "
            f"{counts.requires_review} undecided check(s)."
        )
    else:
        overall_status = ComplianceOverallStatus.ready_for_final_review
        summary = (
            "ReDOCX a trouvé les éléments recherchés. Le document est prêt pour la "
            "validation humaine finale; ce résultat n’est pas une certification juridique."
            if french
            else
            "ReDOCX found the expected information. The document is ready for final "
            "human review; this result is not a legal certification."
        )

    actions: list[str] = []
    prioritized_results = [
        *[item for item in results if item.status == ComplianceCheckStatus.risk_detected],
        *[item for item in results if item.status == ComplianceCheckStatus.evidence_missing],
        *[item for item in results if item.status == ComplianceCheckStatus.warning],
        *[item for item in results if item.status == ComplianceCheckStatus.requires_review],
    ]
    for result in prioritized_results:
        for action in result.recommended_actions:
            if action not in actions:
                actions.append(action)
            if len(actions) >= 8:
                break
        if len(actions) >= 8:
            break

    final_review_action = (
        "Après les corrections, faites valider le document et les preuves par une "
        "personne qualifiée pour la juridiction sélectionnée."
        if french
        else
        "After making changes, have a qualified person validate the document and "
        "supporting evidence for the selected jurisdiction."
    )
    if final_review_action not in actions:
        actions.append(final_review_action)
    return overall_status, summary, actions


def _flatten_references(references_by_signal: dict[str, list], signals: Sequence[str]) -> list:
    deduped = []
    seen = set()
    for signal in signals:
        for reference in references_by_signal.get(signal, []):
            key = (
                reference.source_document_index,
                reference.page_number,
                reference.section_label,
                reference.locator_text,
                reference.excerpt,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(reference)
    return deduped


def _normalize_signal_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _is_french(system_language: SystemLanguage | str | None) -> bool:
    value = getattr(system_language, "value", system_language)
    return str(value or "").strip().lower() in {"fr", "french"}


def _plain_language_rule_summary(
    *,
    status: ComplianceCheckStatus,
    system_language: SystemLanguage | str | None,
) -> str:
    french = _is_french(system_language)
    if french:
        return {
            ComplianceCheckStatus.evidence_found:
                "ReDOCX a trouvé les informations recherchées pour ce contrôle. Une personne doit encore confirmer qu’elles sont exactes et complètes.",
            ComplianceCheckStatus.risk_detected:
                "ReDOCX a trouvé un contenu qui peut être contraire à cette règle ou nécessiter une correction.",
            ComplianceCheckStatus.warning:
                "Une partie de ce contrôle est incertaine ou incomplète et doit être vérifiée.",
            ComplianceCheckStatus.evidence_missing:
                "ReDOCX n’a pas trouvé toutes les informations attendues pour ce contrôle.",
            ComplianceCheckStatus.requires_review:
                "Le contrôle automatique ne peut pas trancher ce point de manière sûre. Une personne qualifiée doit le vérifier.",
        }[status]
    return {
        ComplianceCheckStatus.evidence_found:
            "ReDOCX found the information this check looks for. A person should still confirm that it is accurate and complete.",
        ComplianceCheckStatus.risk_detected:
            "ReDOCX found content that may conflict with this rule or may need correction.",
        ComplianceCheckStatus.warning:
            "Part of this check is unclear or incomplete and should be reviewed.",
        ComplianceCheckStatus.evidence_missing:
            "ReDOCX could not find all the information this check expects.",
        ComplianceCheckStatus.requires_review:
            "The automated check cannot decide this point safely. A qualified person should review it.",
    }[status]


def _recommended_actions(
    *,
    rule: ComplianceRuleDefinition,
    status: ComplianceCheckStatus,
    jurisdiction: ComplianceJurisdiction | None,
    sector_pack: ComplianceSectorPack | None,
    matched_prohibited: Sequence[str],
    missing_required: Sequence[str],
    pack_metadata: Mapping[str, Any],
    system_language: SystemLanguage | str | None,
) -> list[str]:
    for container in (rule.metadata, rule.evaluation, pack_metadata):
        configured = _configured_actions(
            container,
            status=status,
            system_language=system_language,
        )
        if configured:
            return configured

    french = _is_french(system_language)
    actions: list[str] = []
    visible_missing = [
        signal for signal in (_display_signal(item) for item in missing_required) if signal
    ][:3]
    visible_prohibited = [
        signal for signal in (_display_signal(item) for item in matched_prohibited) if signal
    ][:3]

    if status == ComplianceCheckStatus.evidence_found:
        actions.append(
            "Conservez ces informations à jour et gardez les justificatifs pour la validation finale."
            if french
            else
            "Keep this information current and retain the supporting record for final review."
        )
    elif status == ComplianceCheckStatus.risk_detected:
        if visible_prohibited:
            for signal in visible_prohibited:
                actions.append(
                    (
                        f"Examinez le passage signalé concernant « {signal} ». Corrigez-le ou "
                        "supprimez-le s’il n’est pas autorisé; sinon, consignez l’exception approuvée."
                    )
                    if french
                    else
                    (
                        f"Review the highlighted text about “{signal}”. Correct or remove it "
                        "if the rule does not allow it; otherwise record the approved exception."
                    )
                )
        else:
            actions.append(
                "Examinez le passage signalé, corrigez toute information interdite ou inexacte et consignez toute exception approuvée."
                if french
                else
                "Review the highlighted passage, correct any prohibited or inaccurate content, and record any approved exception."
            )
    elif status in {
        ComplianceCheckStatus.evidence_missing,
        ComplianceCheckStatus.warning,
        ComplianceCheckStatus.requires_review,
    }:
        if visible_missing:
            for signal in visible_missing:
                actions.append(
                    (
                        f"Confirmez si le document doit couvrir « {signal} ». Si oui, ajoutez "
                        "l’information requise et son justificatif."
                    )
                    if french
                    else
                    (
                        f"Confirm whether the document must cover “{signal}”. If it does, "
                        "add the required detail and supporting evidence."
                    )
                )
        else:
            actions.append(
                "Demandez à une personne qualifiée de vérifier cette exigence et d’ajouter ou clarifier les informations manquantes."
                if french
                else
                "Ask a qualified reviewer to verify this requirement and add or clarify any missing information."
            )

    jurisdiction_label = _friendly_enum_label(jurisdiction)
    pack_label = _friendly_enum_label(sector_pack)
    if jurisdiction_label and pack_label:
        actions.append(
            (
                f"Vérifiez de nouveau le document modifié selon « {rule.title} » dans le pack "
                f"« {pack_label} » pour {jurisdiction_label}."
            )
            if french
            else
            (
                f"Recheck the revised document against “{rule.title}” in the "
                f"{pack_label} rule pack for {jurisdiction_label}."
            )
        )
    return _dedupe_strings(actions)


def _configured_actions(
    container: Mapping[str, Any],
    *,
    status: ComplianceCheckStatus,
    system_language: SystemLanguage | str | None,
) -> list[str]:
    if not isinstance(container, Mapping):
        return []
    keys = (
        f"{status.value}_actions",
        "recommended_actions",
        "remediation_actions",
        "remediation",
        "next_steps",
    )
    for key in keys:
        if key not in container:
            continue
        actions = _coerce_action_list(
            container[key],
            status=status,
            system_language=system_language,
        )
        if actions:
            return actions
    return []


def _coerce_action_list(
    value: Any,
    *,
    status: ComplianceCheckStatus,
    system_language: SystemLanguage | str | None,
) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        actions: list[str] = []
        for item in value:
            actions.extend(
                _coerce_action_list(
                    item,
                    status=status,
                    system_language=system_language,
                )
            )
        return _dedupe_strings(actions)
    if not isinstance(value, Mapping):
        return []

    status_aliases = {
        ComplianceCheckStatus.evidence_found: ("evidence_found", "passed", "pass"),
        ComplianceCheckStatus.risk_detected: ("risk_detected", "failed", "fail"),
        ComplianceCheckStatus.warning: ("warning",),
        ComplianceCheckStatus.evidence_missing: ("evidence_missing", "missing"),
        ComplianceCheckStatus.requires_review: ("requires_review", "review_required"),
    }[status]
    language_keys = ("fr", "french") if _is_french(system_language) else ("en", "english")
    for key in (*status_aliases, *language_keys, "default", "all"):
        if key in value:
            actions = _coerce_action_list(
                value[key],
                status=status,
                system_language=system_language,
            )
            if actions:
                return actions
    return []


def _display_signal(value: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 100:
        return ""
    if re.search(r"[\[\]{}()*+?^$\\]", text):
        return ""
    return re.sub(r"[_\s-]+", " ", text).strip(" .,:;")


def _friendly_enum_label(value: Any) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or "").strip()
    if not text:
        return ""
    labels = {
        "us": "United States",
        "uk": "United Kingdom",
        "sa": "South Africa",
    }
    return labels.get(text, text.replace("_", " ").title())


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in output:
            output.append(normalized)
    return output


_STATUS_ALIASES = {
    "passed": ComplianceCheckStatus.evidence_found,
    "pass": ComplianceCheckStatus.evidence_found,
    "evidence_found": ComplianceCheckStatus.evidence_found,
    "failed": ComplianceCheckStatus.risk_detected,
    "fail": ComplianceCheckStatus.risk_detected,
    "risk_detected": ComplianceCheckStatus.risk_detected,
    "warning": ComplianceCheckStatus.warning,
    "missing": ComplianceCheckStatus.evidence_missing,
    "evidence_missing": ComplianceCheckStatus.evidence_missing,
    "review_required": ComplianceCheckStatus.requires_review,
    "requires_review": ComplianceCheckStatus.requires_review,
}


def _coerce_status(value: object, default: ComplianceCheckStatus) -> ComplianceCheckStatus:
    if value in (None, ""):
        return default
    if isinstance(value, ComplianceCheckStatus):
        return value
    key = str(value).strip().lower()
    if key in _STATUS_ALIASES:
        return _STATUS_ALIASES[key]
    return ComplianceCheckStatus(key)


def _summary(base_summary: str, suffix: str) -> str:
    base = base_summary.strip().rstrip(".")
    tail = suffix.strip()
    return f"{base}. {tail}" if base else tail


__all__ = [
    "ComplianceEvaluationError",
    "EvaluatedRule",
    "build_counts",
    "build_report_guidance",
    "evaluate_rule",
    "evaluate_rule_packs",
]
