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
from typing import Iterable, Sequence

try:
    from backend.src.schema import ComplianceCheckStatus, ComplianceCounts, ComplianceRuleResult
except ImportError:  # pragma: no cover
    from backend.src.schema import ComplianceCheckStatus, ComplianceCounts, ComplianceRuleResult

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


def evaluate_rule_packs(
    documents: Sequence[EvidenceDocument],
    packs: Sequence[LoadedRulePack],
) -> list[ComplianceRuleResult]:
    results: list[ComplianceRuleResult] = []
    for pack in packs:
        for rule in pack.rules:
            evaluated = evaluate_rule(documents, rule)
            results.append(evaluated.result)
    return results


def evaluate_rule(
    documents: Sequence[EvidenceDocument],
    rule: ComplianceRuleDefinition,
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

    result = ComplianceRuleResult(
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        title=rule.title,
        status=status,
        summary=summary,
        evidence_references=evidence_references,
    )
    return EvaluatedRule(
        result=result,
        matched_required_signals=matched_required,
        matched_optional_signals=matched_optional,
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
    "evaluate_rule",
    "evaluate_rule_packs",
]
