from __future__ import annotations

"""
Deterministic compliance evaluators.

Supported evaluation strategies in rule files:
- any_signal_presence
- all_signal_presence
- absent_signals
- min_signal_count

These strategies deliberately bias toward review_required when the evidence is
partial or weak, which is safer for compliance workflows than over-asserting
pass/fail on ambiguous text.
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


@dataclass(frozen=True)
class EvaluatedRule:
    result: ComplianceRuleResult
    matched_required_signals: tuple[str, ...]
    matched_optional_signals: tuple[str, ...]


class ComplianceEvaluationError(RuntimeError):
    """Raised when a rule cannot be evaluated deterministically."""


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
    missing_status = _coerce_status(evaluation.get("on_missing"), ComplianceCheckStatus.missing)
    partial_status = _coerce_status(evaluation.get("on_partial"), ComplianceCheckStatus.review_required)
    prohibited_match_status = _coerce_status(evaluation.get("on_prohibited_match"), ComplianceCheckStatus.failed)

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
            status = ComplianceCheckStatus.passed
            summary = _summary(rule.summary, "Required evidence was found.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No required evidence was found.")

    elif strategy == "all_signal_presence":
        if required_signals and len(matched_required) == len(required_signals):
            status = ComplianceCheckStatus.passed
            summary = _summary(rule.summary, "All required evidence signals were found.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        elif matched_required:
            status = partial_status
            summary = _summary(rule.summary, "Only partial required evidence was found; human review is advised.")
            evidence_references = _flatten_references(references_by_signal, matched_required)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No required evidence was found.")

    elif strategy == "absent_signals":
        if matched_prohibited:
            status = prohibited_match_status
            summary = _summary(rule.summary, "Evidence of a prohibited or non-compliant signal was found.")
            evidence_references = _flatten_references(references_by_signal, matched_prohibited)
        else:
            status = ComplianceCheckStatus.passed
            summary = _summary(rule.summary, "No prohibited evidence signals were found.")

    elif strategy == "min_signal_count":
        distinct_matches = tuple(sorted(set(matched_required) | set(matched_optional)))
        if len(distinct_matches) >= min_count:
            status = ComplianceCheckStatus.passed
            summary = _summary(rule.summary, f"Minimum evidence threshold of {min_count} signals was met.")
            evidence_references = _flatten_references(references_by_signal, distinct_matches)
        elif distinct_matches:
            status = partial_status
            summary = _summary(rule.summary, "Some evidence was found but the minimum threshold was not met.")
            evidence_references = _flatten_references(references_by_signal, distinct_matches)
        else:
            status = missing_status
            summary = _summary(rule.summary, "No qualifying evidence was found.")

    else:
        raise ComplianceEvaluationError(f"Unsupported evaluation strategy for rule {rule.rule_id}: {strategy}")

    if status == ComplianceCheckStatus.passed and not evidence_references:
        status = ComplianceCheckStatus.review_required
        summary = _summary(rule.summary, "Signals appear present but anchored evidence is too weak for a safe pass.")

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
        ComplianceCheckStatus.passed: 0,
        ComplianceCheckStatus.failed: 0,
        ComplianceCheckStatus.warning: 0,
        ComplianceCheckStatus.missing: 0,
        ComplianceCheckStatus.review_required: 0,
    }
    for result in results:
        counts[result.status] += 1
    return ComplianceCounts(
        passed=counts[ComplianceCheckStatus.passed],
        failed=counts[ComplianceCheckStatus.failed],
        warning=counts[ComplianceCheckStatus.warning],
        missing=counts[ComplianceCheckStatus.missing],
        review_required=counts[ComplianceCheckStatus.review_required],
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


def _coerce_status(value: object, default: ComplianceCheckStatus) -> ComplianceCheckStatus:
    if value in (None, ""):
        return default
    if isinstance(value, ComplianceCheckStatus):
        return value
    return ComplianceCheckStatus(str(value))


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
