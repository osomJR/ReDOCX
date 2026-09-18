from __future__ import annotations

"""
Production grammar-correction processing for ReDOCX.

The grammar feature is intentionally implemented as a quality-controlled pipeline:
1. a conservative correction pass;
2. an independent QA/repair pass against the original source; and
3. deterministic integrity validation before any output is returned.

The integrity gate is not a grammar engine. Its job is to prevent a generative model
from silently changing facts, technical identifiers, document structure, or too much
of the author's wording while correcting grammar.
"""

from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
import os
import re
from typing import Optional, Protocol, Sequence

from backend.src.inline_text_security import build_untrusted_content_block

from .llm_client import AIClient, AIClientConfig, DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MODEL


logger = logging.getLogger(__name__)


GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS = max(
    DEFAULT_MAX_OUTPUT_TOKENS,
    int(os.getenv("AI_GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS", "10000")),
)
GRAMMAR_CORRECT_MODEL = (
    os.getenv("AI_GRAMMAR_CORRECT_MODEL", "").strip() or DEFAULT_MODEL
)
GRAMMAR_CORRECT_REVIEW_PASSES = max(
    0,
    min(2, int(os.getenv("AI_GRAMMAR_CORRECT_REVIEW_PASSES", "2"))),
)
GRAMMAR_CORRECT_MIN_WORD_SIMILARITY = float(
    os.getenv("AI_GRAMMAR_CORRECT_MIN_WORD_SIMILARITY", "0.72")
)
GRAMMAR_CORRECT_MIN_LENGTH_RATIO = float(
    os.getenv("AI_GRAMMAR_CORRECT_MIN_LENGTH_RATIO", "0.72")
)
GRAMMAR_CORRECT_MAX_LENGTH_RATIO = float(
    os.getenv("AI_GRAMMAR_CORRECT_MAX_LENGTH_RATIO", "1.35")
)


BASE_CONSTRAINTS = """
You are a professional document-processing proofreader.
NON-NEGOTIABLE RULES:
- Preserve the original tone, formality, voice, meaning, and factual content
- Preserve original document structure and paragraph order
- Do NOT reorder headings, paragraphs, or bullet points
- Do NOT paraphrase creatively
- Do NOT embellish, expand, summarize, or add ideas
- Do NOT simplify beyond the author's intent
- Do NOT upgrade vocabulary merely for style
- Avoid generic or "AI-style" phrasing
- Act as a neutral, invisible processor
- Output must strictly comply with the formatting constraints
""".strip()


GRAMMAR_CORRECT_RULES = """
TASK: GRAMMAR CORRECTION ONLY
RULES:
- Inspect EVERY sentence; do not stop after fixing the most obvious errors
- Correct grammar, syntax, subject-verb agreement, pronoun agreement, article/determiner agreement, verb tense and sequence-of-tense errors, punctuation, capitalization, and clear typographical errors
- Preserve the exact meaning, facts, names, numbers, dates, percentages, citations, version strings, file names, URLs, email addresses, and technical terminology unless punctuation immediately around them must change
- Preserve the original language; never translate the text
- Preserve tone, formality, voice, paragraph order, headings, list structure, and line breaks as closely as possible
- Make the smallest correction necessary for each error; do not paraphrase, summarize, expand, simplify, or creatively rewrite
- If a sentence is already grammatically correct, leave it unchanged

MANDATORY QUALITY CHECKS BEFORE RETURNING:
- Re-read the complete corrected document once from beginning to end
- Check every finite verb against its subject and time reference
- Check agreement across sentence boundaries where a pronoun refers back to a noun or collective noun
- In English, do not combine the present perfect with a definite finished-past time expression such as "yesterday", "last week", "last year", "two days ago", or a completed dated period; use the appropriate simple-past construction instead
- In formal written French, enforce grammatical number/gender agreement consistently, including collective-noun/pronoun references, and correct punctuation around linking adverbs such as "cependant" when two independent clauses are joined
- In French past-tense narration, verify sequence of tenses explicitly: when a reported fact/action clearly predates the reporting verb, use the appropriate anterior tense (for example, plus-que-parfait where required by the context)
- In French, do not introduce the subjunctive mechanically after expressions such as "vérifier que"; choose mood from the actual construction and meaning, and keep coordinated complement clauses grammatically parallel
- In French, a grammatically singular collective antecedent such as "l'équipe", "la direction", or "le groupe" must keep a singular pronoun reference unless the text explicitly introduces individual members as a new plural antecedent
- Do not leave a known error unchanged merely because the construction may occur in colloquial speech

OUTPUT:
- Return only the corrected document text
- No commentary, labels, explanations, markdown fences, or change log
""".strip()


GRAMMAR_REVIEW_RULES = """
TASK: FINAL GRAMMAR QA AND MINIMAL REPAIR
You are the final quality gate for a grammar-correction feature.
Compare the ORIGINAL SOURCE with the CANDIDATE CORRECTION and return the best final corrected document.

REVIEW REQUIREMENTS:
- Audit EVERY sentence independently for grammar, syntax, subject-verb agreement, pronoun agreement, verb tense, sequence of tenses, punctuation, capitalization, and clear typographical errors
- Fix any grammatical error that survived the first pass
- Restore any fact, name, number, date, percentage, citation, version string, file name, URL, email address, or technical term that the candidate changed unnecessarily
- Restore any missing sentence, heading, paragraph, or list item
- Preserve the original language and meaning
- Preserve structure and wording except where a grammatical correction is required
- If the candidate is already fully correct, return it unchanged

SPECIFIC TRAPS TO CHECK:
- English present perfect + finished-past time expressions (for example "has reviewed ... last week")
- English subject-verb agreement in coordinated, collective, neither/nor, each/every, and plural-subject constructions
- French subject-verb, noun/adjective, participle, determiner, and pronoun agreement
- Formal French collective-noun references (for example a singular "l'équipe" should not switch to plural "ils/elles" unless the source explicitly introduces a new plural antecedent)
- French sequence of tenses in past narration, especially an event already completed before a past reporting verb
- French mood after verification/reporting constructions: do not use subjunctive merely because a subordinate clause follows "vérifier que"; use the mood required by the meaning and keep coordinated clauses parallel
- Cross-sentence antecedent coherence: resolve each pronoun to the intended grammatical antecedent before returning the document
- Comma splices and punctuation around linking adverbs such as "however" and "cependant"

OUTPUT:
- Return only the final corrected document text
- No analysis, explanations, labels, markdown, or change log
""".strip()


class GrammarCorrectionBackend(Protocol):
    """Provider interface for grammar-correction runtime."""

    def correct(self, *, prompt: str, source_text: str) -> str:
        ...


class LLMGrammarCorrectionBackend:
    """LLM-backed grammar runtime with grammar-specific model configuration."""

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient(
            AIClientConfig(
                model=GRAMMAR_CORRECT_MODEL,
                max_output_tokens=GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS,
            )
        )

    def correct(self, *, prompt: str, source_text: str) -> str:
        # source_text is part of the backend contract for alternate providers and
        # test doubles. The OpenAI-backed implementation receives it inside the
        # collision-resistant untrusted-data block already embedded in prompt.
        del source_text
        return self.ai_client.generate(prompt)


class GrammarCorrectionIntegrityError(RuntimeError):
    """Raised when no generated candidate can safely pass output-integrity checks."""


@dataclass(frozen=True)
class GrammarCorrectConfig:
    """Quality and integrity controls for grammar correction."""

    algorithm_version: Optional[str] = "grammar-correct-v3"
    review_passes: int = GRAMMAR_CORRECT_REVIEW_PASSES
    strict_integrity: bool = True
    min_word_similarity: float = GRAMMAR_CORRECT_MIN_WORD_SIMILARITY
    min_length_ratio: float = GRAMMAR_CORRECT_MIN_LENGTH_RATIO
    max_length_ratio: float = GRAMMAR_CORRECT_MAX_LENGTH_RATIO

    def __post_init__(self) -> None:
        if not 0 <= self.review_passes <= 2:
            raise ValueError("review_passes must be between 0 and 2.")
        if not 0.0 <= self.min_word_similarity <= 1.0:
            raise ValueError("min_word_similarity must be between 0 and 1.")
        if self.min_length_ratio <= 0.0:
            raise ValueError("min_length_ratio must be greater than zero.")
        if self.max_length_ratio < self.min_length_ratio:
            raise ValueError("max_length_ratio must be >= min_length_ratio.")


@dataclass(frozen=True)
class GrammarIntegrityReport:
    ok: bool
    issues: tuple[str, ...]
    word_similarity: float
    length_ratio: float


class GrammarCorrectProcessor:
    """
    Stateless, fail-closed grammar-correction processor.

    A candidate is never returned solely because an LLM produced it. The final
    output must also pass deterministic source-preservation checks. When the QA
    pass itself introduces unsafe drift, a targeted recovery pass is attempted;
    if that still fails, the most recent integrity-safe candidate is returned.
    If no safe candidate exists, processing fails rather than returning a
    corrupted document.
    """

    def __init__(
        self,
        backend: Optional[GrammarCorrectionBackend] = None,
        config: Optional[GrammarCorrectConfig] = None,
    ) -> None:
        self.backend = backend or LLMGrammarCorrectionBackend()
        self.config = config or GrammarCorrectConfig()

    def correct(self, text: str) -> str:
        source = _normalize_text(text)

        initial_prompt = build_grammar_correct_prompt(source)
        candidate = _normalize_text(
            self.backend.correct(prompt=initial_prompt, source_text=source)
        )

        report = validate_grammar_output_integrity(
            source,
            candidate,
            config=self.config,
        )
        last_safe_candidate: Optional[str] = candidate if report.ok else None
        if not report.ok:
            logger.warning(
                "Grammar correction initial candidate failed integrity checks: %s",
                ", ".join(report.issues),
            )

        for review_index in range(self.config.review_passes):
            review_prompt = build_grammar_review_prompt(
                source,
                candidate,
                integrity_issues=report.issues,
                review_index=review_index,
            )
            reviewed = _normalize_text(
                self.backend.correct(prompt=review_prompt, source_text=source)
            )
            reviewed_report = validate_grammar_output_integrity(
                source,
                reviewed,
                config=self.config,
            )

            candidate = reviewed
            report = reviewed_report
            if report.ok:
                last_safe_candidate = reviewed
            else:
                logger.warning(
                    "Grammar correction QA candidate failed integrity checks on pass %d: %s",
                    review_index + 1,
                    ", ".join(report.issues),
                )

        if report.ok:
            return candidate

        # The model may have fixed grammar while accidentally changing a protected
        # fact or structure. One targeted recovery call is safer than silently
        # accepting that drift or immediately falling back to a weaker draft.
        recovery_prompt = build_integrity_recovery_prompt(
            source,
            candidate,
            integrity_issues=report.issues,
        )
        recovered = _normalize_text(
            self.backend.correct(prompt=recovery_prompt, source_text=source)
        )
        recovered_report = validate_grammar_output_integrity(
            source,
            recovered,
            config=self.config,
        )
        if recovered_report.ok:
            return recovered

        logger.error(
            "Grammar correction recovery candidate failed integrity checks: %s",
            ", ".join(recovered_report.issues),
        )

        if last_safe_candidate is not None:
            logger.warning(
                "Grammar correction is returning the last integrity-safe candidate after QA recovery failure."
            )
            return last_safe_candidate

        if self.config.strict_integrity:
            raise GrammarCorrectionIntegrityError(
                "Grammar correction output failed source-integrity validation."
            )

        return recovered


def build_grammar_correct_prompt(text: str) -> str:
    """Build the first-pass grammar-correction prompt."""
    normalized = _normalize_text(text)
    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GRAMMAR_CORRECT_RULES}\n\n"
        + build_untrusted_content_block(normalized, label="ORIGINAL DOCUMENT CONTENT")
    )


def build_grammar_review_prompt(
    source_text: str,
    candidate_text: str,
    *,
    integrity_issues: Sequence[str] = (),
    review_index: int = 0,
) -> str:
    """Build an independent source-vs-candidate QA/repair prompt."""
    source = _normalize_text(source_text)
    candidate = _normalize_text(candidate_text)
    issue_context = _format_integrity_issue_context(integrity_issues)

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GRAMMAR_REVIEW_RULES}\n\n"
        f"QA PASS: {review_index + 1}\n"
        f"{issue_context}\n\n"
        + build_untrusted_content_block(source, label="ORIGINAL SOURCE")
        + "\n\n"
        + build_untrusted_content_block(candidate, label="CANDIDATE CORRECTION")
    )


def build_integrity_recovery_prompt(
    source_text: str,
    candidate_text: str,
    *,
    integrity_issues: Sequence[str],
) -> str:
    """Build a targeted fail-safe prompt when deterministic integrity checks fail."""
    source = _normalize_text(source_text)
    candidate = _normalize_text(candidate_text)
    issue_context = _format_integrity_issue_context(integrity_issues)

    recovery_rules = """
TASK: SOURCE-INTEGRITY RECOVERY + FINAL GRAMMAR CHECK
The candidate failed deterministic document-integrity checks.
Return a corrected version that fixes the integrity failures while retaining all valid grammar corrections.
Use the ORIGINAL SOURCE as the authority for facts, names, numbers, dates, versions, technical identifiers, document structure, and omitted content.
Do not copy grammatical mistakes back from the source when they can be corrected without changing meaning.
Return only the final corrected document text.
""".strip()

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{recovery_rules}\n\n"
        f"{issue_context}\n\n"
        + build_untrusted_content_block(source, label="AUTHORITATIVE ORIGINAL SOURCE")
        + "\n\n"
        + build_untrusted_content_block(candidate, label="FAILED CANDIDATE")
    )


def grammar_correct_text(
    text: str,
    *,
    backend: Optional[GrammarCorrectionBackend] = None,
    config: Optional[GrammarCorrectConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = GrammarCorrectProcessor(backend=backend, config=config)
    return processor.correct(text)


def validate_grammar_output_integrity(
    source_text: str,
    candidate_text: str,
    *,
    config: Optional[GrammarCorrectConfig] = None,
) -> GrammarIntegrityReport:
    """
    Validate invariants that grammar correction is never allowed to violate.

    This intentionally does not try to prove grammatical correctness with regexes.
    Grammar quality is handled by the model + independent QA pass; deterministic
    code protects facts and document shape and rejects excessive rewriting.
    """
    cfg = config or GrammarCorrectConfig()
    source = _normalize_text(source_text)
    candidate = _normalize_text(candidate_text)

    issues: list[str] = []

    if _has_forbidden_output_wrapper(candidate):
        issues.append("provider added commentary or a markdown wrapper")

    if _protected_token_inventory(source) != _protected_token_inventory(candidate):
        issues.append("protected factual or technical tokens changed")

    if _list_marker_sequence(source) != _list_marker_sequence(candidate):
        issues.append("list marker sequence changed")

    source_paragraphs = _paragraph_count(source)
    candidate_paragraphs = _paragraph_count(candidate)
    if source_paragraphs > 1 and source_paragraphs != candidate_paragraphs:
        issues.append("paragraph count changed")

    source_words = _word_tokens(source)
    candidate_words = _word_tokens(candidate)
    similarity = SequenceMatcher(a=source_words, b=candidate_words, autojunk=False).ratio()
    if similarity < cfg.min_word_similarity:
        issues.append("candidate rewrites too much of the original wording")

    length_ratio = len(candidate) / max(1, len(source))
    if not cfg.min_length_ratio <= length_ratio <= cfg.max_length_ratio:
        issues.append("candidate length changed beyond the grammar-correction safety range")

    return GrammarIntegrityReport(
        ok=not issues,
        issues=tuple(issues),
        word_similarity=similarity,
        length_ratio=length_ratio,
    )


def _format_integrity_issue_context(issues: Sequence[str]) -> str:
    if not issues:
        return (
            "DETERMINISTIC INTEGRITY STATUS: no source-preservation violation was detected. "
            "Still perform the full grammar QA; deterministic checks do not prove grammatical correctness."
        )
    rendered = "; ".join(dict.fromkeys(str(issue).strip() for issue in issues if str(issue).strip()))
    return (
        "DETERMINISTIC INTEGRITY WARNINGS: "
        f"{rendered}. Restore these properties from the original source while completing grammar QA."
    )


_URL_RE = re.compile(r"\bhttps?://[^\s<>\]\[()]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_NUMBER_RE = re.compile(
    r"(?<![\w])\d+(?:[.,:/-]\d+)*(?:\s?%)?(?![\w])",
    re.UNICODE,
)
_TECH_IDENTIFIER_RE = re.compile(
    r"\b(?=[A-Za-z0-9_.:/-]*[A-Za-z])(?=[A-Za-z0-9_.:/-]*\d)"
    r"[A-Za-z0-9]+(?:[._:/-][A-Za-z0-9]+)+\b"
)
_HONORIFIC_NAME_RE = re.compile(
    r"\b(?:Dr|Dre|Mr|Mrs|Ms|Prof|Mme|Mlle|M)\.?\s+"
    r"[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’-]+\b"
)
_LIST_MARKER_RE = re.compile(r"(?m)^\s*(?P<marker>•|[-*]|\d+[.)])\s+")
_QUANTITY_WORD_RE = re.compile(
    r"\b(?:"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|"
    r"zéro|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|treize|quatorze|"
    r"quinze|seize|vingt|trente|quarante|cinquante|soixante|cent|mille|million|milliard"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)
_WORD_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]+(?:['’\-][\wÀ-ÖØ-öø-ÿ]+)*", re.UNICODE)
_OUTPUT_LABEL_RE = re.compile(
    r"^(?:corrected(?:\s+(?:text|document))?|correction|revised(?:\s+text)?|"
    r"texte\s+corrig[ée]|version\s+corrig[ée]e?)\s*:\s*",
    re.IGNORECASE | re.UNICODE,
)


def _has_forbidden_output_wrapper(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("```") or bool(_OUTPUT_LABEL_RE.match(stripped))


def _protected_token_inventory(text: str) -> Counter[str]:
    """Return exact source atoms that grammar correction must not mutate."""
    tokens: list[str] = []
    for regex, prefix in (
        (_URL_RE, "url"),
        (_EMAIL_RE, "email"),
        (_HONORIFIC_NAME_RE, "name"),
        (_TECH_IDENTIFIER_RE, "tech"),
        (_NUMBER_RE, "number"),
    ):
        tokens.extend(f"{prefix}:{match.group(0)}" for match in regex.finditer(text))

    # Common spelled-out quantities are facts too. They are compared case-insensitively
    # so a legitimate capitalization correction does not trip the integrity gate.
    tokens.extend(
        f"quantity:{match.group(0).casefold()}"
        for match in _QUANTITY_WORD_RE.finditer(text)
    )
    return Counter(tokens)


def _list_marker_sequence(text: str) -> tuple[str, ...]:
    return tuple(match.group("marker") for match in _LIST_MARKER_RE.finditer(text))


def _paragraph_count(text: str) -> int:
    return sum(1 for part in re.split(r"\n\s*\n", text) if part.strip())


def _word_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _WORD_RE.finditer(text)]


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


__all__ = [
    "BASE_CONSTRAINTS",
    "GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS",
    "GRAMMAR_CORRECT_MODEL",
    "GRAMMAR_CORRECT_REVIEW_PASSES",
    "GRAMMAR_CORRECT_RULES",
    "GRAMMAR_REVIEW_RULES",
    "GrammarCorrectionBackend",
    "LLMGrammarCorrectionBackend",
    "GrammarCorrectionIntegrityError",
    "GrammarCorrectConfig",
    "GrammarIntegrityReport",
    "GrammarCorrectProcessor",
    "build_grammar_correct_prompt",
    "build_grammar_review_prompt",
    "build_integrity_recovery_prompt",
    "validate_grammar_output_integrity",
    "grammar_correct_text",
]
