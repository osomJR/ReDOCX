from __future__ import annotations
"""
V1 summarization processing.

Purpose:
- hold summarization-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free
- schema-agnostic: this module returns processed text only
- provider-backed by default via the shared LLM client
- prompt construction is separated from runtime execution
"""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
import os
import re
import unicodedata
from typing import Mapping, Optional, Protocol

from backend.src.inline_text_security import build_untrusted_content_block

from .llm_client import AIClient


# -------------------------
# Contract-aligned prompt rules
# -------------------------

BASE_CONSTRAINTS = """
You are a professional document processing AI.
NON-NEGOTIABLE RULES:
- Preserve the original tone, formality, and voice
- Preserve original document structure and paragraph order
- Do NOT reorder headings, paragraphs, or bullet points
- Do NOT paraphrase creatively
- Do NOT embellish, expand, or add ideas
- Do NOT simplify beyond the author's intent
- Avoid generic or "AI-style" phrasing
- Maintain original sentence rhythm where practical after compression
- Act as a neutral, invisible processor
- Output must strictly comply with formatting constraints
""".strip()

SUMMARIZE_RULES = """
TASK: COMPRESSION-ONLY SUMMARIZATION
RULES:
- Reduce length only
- Preserve argument flow
- Preserve logical paragraph structure and source order
- Cover the complete document from beginning to end; every major source section must contribute meaningful content to the summary
- Do not concentrate only on the opening, closing, or any single section
- Keep every existing heading unchanged and in its original position
- Keep list markers, numbering, and list-item order unchanged; summarize within each item instead of merging items
- Do not merge distinct headings, logical paragraphs, or list items
- PDF extraction can contain hard line-wraps inside one paragraph; treat those wraps as formatting rather than separate ideas
- Remove redundancy, not meaning
- No stylistic paraphrasing
- No rewording unless required for compression
- Preserve all material facts, names, dates, numbers, units, conditions, qualifications, negations, and conclusions
- Preserve mathematical expressions, equations, inequalities, functions, operators, variables, constants, units, calculation steps, and stated results exactly when they appear in the source
- Treat numeric ranges (for example 120-160, 120–160, or 120 to 160) as factual ranges rather than subtraction unless the source context clearly makes them arithmetic; preserve their endpoints and range meaning
- Never recompute, simplify, normalize, approximate, or rewrite a mathematical expression unless the source itself provides that exact alternate form
- Preserve LaTeX / mathematical notation verbatim, including subscripts, superscripts, delimiters, signs, and operator symbols
- Do not invent, infer, speculate, or introduce facts that are not present in the source
- Preserve uncertainty and attribution exactly when the source is uncertain or attributes a claim to someone else
- Preserve the source language; do not translate the content
- Do not add a preface, commentary, disclaimer, title, or explanation unless one already exists in the source
- The summary must not be longer than the source
""".strip()


MIN_COMPRESSION_CHECK_WORDS = 80
MAX_PROTECTED_MATH_FRAGMENTS = 512
# Two bounded repair attempts let one integrity correction expose and then fix
# another (for example, restoring document coverage can make an otherwise valid
# summary too long). The loop remains strictly capped to protect latency/cost.
MAX_INTEGRITY_REPAIR_ATTEMPTS = 2

# ``compression ratio`` here means summary words / source words. Production
# summaries are constrained to 25-35% of the source for documents large enough
# to measure reliably. The target moves within that band based on content
# density; the environment can tune the default but cannot weaken the contract.
SUMMARIZE_MIN_COMPRESSION_RATIO = 0.25
SUMMARIZE_MAX_COMPRESSION_RATIO = 0.35

# Summarization can require substantially more than the shared client default when
# a long, structure-heavy source must retain headings, lists, facts, and equations.
# These limits apply only to summarization; other AI features retain the existing
# AIClient default unless they explicitly request a larger budget.
SUMMARIZE_MIN_OUTPUT_TOKENS = max(256, int(os.getenv("AI_SUMMARIZE_MIN_OUTPUT_TOKENS", "512")))
SUMMARIZE_MAX_OUTPUT_TOKENS = max(
    SUMMARIZE_MIN_OUTPUT_TOKENS,
    int(os.getenv("AI_SUMMARIZE_MAX_OUTPUT_TOKENS", "6000")),
)
SUMMARIZE_TARGET_COMPRESSION_RATIO = min(
    SUMMARIZE_MAX_COMPRESSION_RATIO,
    max(
        SUMMARIZE_MIN_COMPRESSION_RATIO,
        float(os.getenv("AI_SUMMARIZE_TARGET_COMPRESSION_RATIO", "0.30")),
    ),
)
SUMMARIZE_TOKEN_PER_WORD_ESTIMATE = min(
    3.0,
    max(1.0, float(os.getenv("AI_SUMMARIZE_TOKEN_PER_WORD_ESTIMATE", "1.6"))),
)
SUMMARIZE_OUTPUT_TOKEN_HEADROOM = max(64, int(os.getenv("AI_SUMMARIZE_OUTPUT_TOKEN_HEADROOM", "256")))

# Strong mathematical delimiters that are unambiguous in ordinary prose.
_DISPLAY_MATH_PATTERNS = (
    re.compile(r"\$\$[\s\S]{1,4000}?\$\$"),
    re.compile(r"\\\[[\s\S]{1,4000}?\\\]"),
    re.compile(r"\\\([\s\S]{1,2000}?\\\)"),
)

# Delimited inline TeX using a single dollar sign is accepted only when the
# enclosed content contains math-specific syntax. This avoids treating normal
# currency such as "$25" as TeX that must be reproduced verbatim.
_SINGLE_DOLLAR_MATH_RE = re.compile(r"(?<!\$)\$(?!\$)([^\n$]{1,1000}?)(?<!\$)\$(?!\$)")
_MATH_SPECIFIC_TEX_RE = re.compile(r"(?:\\[A-Za-z]+|[_^{}]|[=<>≤≥≠≈±×÷∑∫√∞])")

_STRONG_MATH_LINE_RE = re.compile(r"[=≤≥≠≈∑∫√]")
_MATH_EVIDENCE_RE = re.compile(
    r"(?:\d|[A-Za-z][A-Za-z0-9_]*\s*\([^\n)]*\)|[+\-*/^×÷±∞])"
)
_EQUATION_FRAGMENT_RE = re.compile(
    r"(?<!\w)"
    r"(?:[A-Za-z][A-Za-z0-9_]*\s*\([^\n)]{1,120}\)|[A-Za-z][A-Za-z0-9_]{0,63})"
    r"\s*(?:=|≈|≠|≤|≥|<|>)\s*"
    r".+?"
    r"(?=(?:\s+(?:to|and|where|when|which|that|for|with|because|while|so)\b)"
    r"|[,;.!?](?:\s|$)|$)",
    re.IGNORECASE,
)
# Generic arithmetic intentionally excludes a bare hyphen/minus operator. A
# numeric pair such as ``120-160`` is overwhelmingly likely to be a range in
# prose and must not become protected math merely because it contains ``-``.
_ARITHMETIC_RE = re.compile(
    r"(?<!\w)"
    r"(?:[-+]?\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*(?:\+|\*|/|\^|×|÷)\s*(?:[-+]?\d+(?:\.\d+)?|\.\d+)){1,}"
    r"(?:\s*(?:=|≈|≠|≤|≥|<|>)\s*(?:[-+]?\d+(?:\.\d+)?|\.\d+))?"
)

# Numeric subtraction is protected only when the source supplies additional
# mathematical evidence (a stated result/comparison). Symbolic subtraction such
# as ``x = y - 2`` is already covered by _EQUATION_FRAGMENT_RE, while TeX and
# formula-dominant lines are handled by the dedicated rules below.
_SUBTRACTION_WITH_RESULT_RE = re.compile(
    r"(?<!\w)"
    r"(?:[-+]?\d+(?:\.\d+)?|\.\d+)\s*-\s*"
    r"(?:[-+]?\d+(?:\.\d+)?|\.\d+)\s*"
    r"(?:=|≈|≠|≤|≥|<|>)\s*"
    r"(?:[-+]?\d+(?:\.\d+)?|\.\d+)"
)

_NUMERIC_RANGE_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<start>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*"
    r"(?P<separator>[-–—]|\bto\b)\s*"
    r"(?P<end>[+-]?(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?P<percent>\s*%)?"
    r"(?![\w.])",
    re.IGNORECASE,
)
_FUNCTION_EXPRESSION_RE = re.compile(
    r"\b(?:f|g|h|sin|cos|tan|asin|acos|atan|sinh|cosh|tanh|log|ln|exp|sqrt|abs|min|max|floor|ceil|round|mod|det|lim)"
    r"\s*\([^\n)]{1,200}\)",
    re.IGNORECASE,
)
_SYMBOLIC_EXPRESSION_RE = re.compile(
    r"(?<!\w)"
    r"(?:[A-Za-zΑ-Ωα-ω][A-Za-z0-9_Α-Ωα-ω₀-₉⁰-⁹]{0,12}|[-+]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:\+|\*|/|\^|×|÷|±|·)\s*"
    r"(?:[A-Za-zΑ-Ωα-ω][A-Za-z0-9_Α-Ωα-ω₀-₉⁰-⁹]{0,12}|[-+]?\d+(?:\.\d+)?)){1,}"
)
# A slash between ordinary words is linguistically ambiguous (for example,
# ``and/or``, ``input/output``, or ``CV/Resume``) and is not sufficient
# evidence of mathematics. Explicit equations containing such a division are
# still protected by ``_EQUATION_FRAGMENT_RE``; formula-style identifiers with
# digits or underscores remain protected by ``_SYMBOLIC_EXPRESSION_RE``.
_WORD_DIVISION_RE = re.compile(
    r"^[^\W\d_]+(?:\s*/\s*[^\W\d_]+)+$",
    re.UNICODE,
)
_SYMBOLIC_SUBTRACTION_RE = re.compile(
    r"(?<!\w)"
    r"(?:[A-Za-zΑ-Ωα-ω](?:_?\d+|[₀-₉⁰-⁹]+)?|[-+]?\d+(?:\.\d+)?)"
    r"\s*-\s*"
    r"(?:[A-Za-zΑ-Ωα-ω](?:_?\d+|[₀-₉⁰-⁹]+)?|[-+]?\d+(?:\.\d+)?)"
)
_MATH_PROSE_WORD_RE = re.compile(r"\b[A-Za-z]{4,}\b")
_MATH_WORDS = frozenset({
    "asin", "acos", "atan", "cosh", "sinh", "tanh", "sqrt", "log", "exp",
    "sin", "cos", "tan", "min", "max", "abs", "floor", "ceil", "mod",
})

_STRUCTURED_LINE_RE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)]|[A-Z][A-Z0-9 &/,:;()'’-]{3,}|.{1,80}:)\s*"
)
_COVERAGE_TOKEN_RE = re.compile(
    r"[^\W_]+(?:[’'\-][^\W_]+)*",
    re.UNICODE,
)
_COVERAGE_STOPWORDS = frozenset(
    {
        # English
        "about", "after", "again", "against", "also", "because", "before",
        "being", "between", "could", "document", "during", "each", "from",
        "have", "into", "more", "most", "other", "over", "same", "should",
        "than", "that", "their", "there", "these", "they", "this", "those",
        "through", "under", "very", "were", "what", "when", "where", "which",
        "while", "with", "would", "your",
        # French
        "ainsi", "alors", "après", "avant", "avec", "cette", "comme", "dans",
        "depuis", "document", "elle", "elles", "entre", "leurs", "mais", "même",
        "notre", "nous", "pour", "sans", "selon", "sont", "sous", "tandis",
        "tous", "toutes", "très", "vous",
    }
)


class SummarizationQualityError(Exception):
    """Deterministic summary-integrity failure with privacy-safe diagnostics.

    Source fragments may be retained transiently for the single repair pass, but
    ``str(exc)`` never includes document content. If the error escapes the
    processor, ``sanitized()`` removes the source fragments entirely.
    """

    def __init__(
        self,
        reason: str,
        *,
        protected_fragments: tuple[str, ...] = (),
        fragment_digests: tuple[str, ...] = (),
        metadata: Mapping[str, int | float | str] | None = None,
    ) -> None:
        self.reason = str(reason or "quality_validation_failed").strip()
        self.protected_fragments = tuple(protected_fragments)
        self.fragment_digests = tuple(fragment_digests) or tuple(
            hashlib.sha256(fragment.encode("utf-8")).hexdigest()[:16]
            for fragment in self.protected_fragments
        )
        self.metadata = dict(metadata or {})

        diagnostic_parts = [f"reason={self.reason}"]
        if self.fragment_digests:
            diagnostic_parts.append(f"fragment_count={len(self.fragment_digests)}")
            diagnostic_parts.append(
                "fragment_digests=" + ",".join(self.fragment_digests[:8])
            )
        for key, value in sorted(self.metadata.items()):
            diagnostic_parts.append(f"{key}={value}")

        super().__init__(
            "Summarization quality validation failed ("
            + "; ".join(diagnostic_parts)
            + ")."
        )

    def sanitized(self) -> "SummarizationQualityError":
        return SummarizationQualityError(
            self.reason,
            fragment_digests=self.fragment_digests,
            metadata=self.metadata,
        )


# Backward-compatible export for any existing imports/tests. The new class name
# gives the global error layer a precise, non-infrastructure classification.
SummarizationOutputError = SummarizationQualityError


# -------------------------
# Provider contract
# -------------------------

class SummarizationBackend(Protocol):
    """
    Provider interface for summarization runtime.

    Implementations may call an LLM, a local model, or any other processing backend.
    They must return only the summarized text content.
    """

    def summarize(self, *, prompt: str, source_text: str) -> str:
        ...


class LLMSummarizationBackend:
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()

    def summarize(self, *, prompt: str, source_text: str) -> str:
        return self.ai_client.generate(
            prompt,
            max_output_tokens=_summarization_output_token_budget(source_text),
        )


# -------------------------
# Service façade
# -------------------------

@dataclass(frozen=True)
class SummarizeConfig:
    """
    Optional knobs for future provider-backed summarization.

    algorithm_version is kept here for forward compatibility if you later want the
    processing layer to expose or log its own runtime version. analyzer.py remains
    the owner of response metadata construction.
    """

    algorithm_version: Optional[str] = None


@dataclass(frozen=True)
class _SummaryLengthTarget:
    source_words: int
    minimum_words: int
    target_words: int
    maximum_words: int
    target_ratio: float


class SummarizeProcessor:
    """
    Stateless summarization processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned summarization prompt
    - delegate the actual text transformation to a backend
    - return summarized text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    """

    def __init__(
        self,
        backend: Optional[SummarizationBackend] = None,
        config: Optional[SummarizeConfig] = None,
    ) -> None:
        self.backend = backend or LLMSummarizationBackend()
        self.config = config or SummarizeConfig()

    def summarize(self, text: str) -> str:
        normalized = _normalize_text(text)
        prompt = build_summarize_prompt(normalized)
        output = self.backend.summarize(prompt=prompt, source_text=normalized)

        candidate = _normalize_text(output)
        required_math = tuple(_extract_protected_math_fragments(normalized))
        coverage_requirements = _coverage_anchor_requirements(normalized)

        for repair_attempt in range(MAX_INTEGRITY_REPAIR_ATTEMPTS + 1):
            try:
                _validate_summary_output(
                    source_text=normalized,
                    summarized_text=candidate,
                )
                return candidate
            except SummarizationQualityError as exc:
                if repair_attempt >= MAX_INTEGRITY_REPAIR_ATTEMPTS:
                    raise exc.sanitized() from None

                # Repair the measured failure, then run every postcondition again.
                # This permits a later pass to correct a secondary condition
                # without weakening compression, coverage, or math integrity.
                repair_prompt = build_summarize_repair_prompt(
                    source_text=normalized,
                    previous_summary=candidate,
                    required_math=required_math,
                    coverage_requirements=coverage_requirements,
                    failure_reason=exc.reason,
                    repair_attempt=repair_attempt + 1,
                )
                repaired_output = self.backend.summarize(
                    prompt=repair_prompt,
                    source_text=normalized,
                )
                candidate = _normalize_text(repaired_output)

        raise AssertionError("unreachable summarization repair state")


# -------------------------
# Pure helpers
# -------------------------

def build_summarize_prompt(text: str) -> str:
    """
    Build the contract-aligned prompt for summarization only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized = _normalize_text(text)
    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{SUMMARIZE_RULES}\n\n"
        f"{_summary_length_instruction(normalized)}\n\n"
        + build_untrusted_content_block(normalized, label="DOCUMENT CONTENT")
    )




def build_summarize_repair_prompt(
    *,
    source_text: str,
    previous_summary: str,
    required_math: tuple[str, ...],
    coverage_requirements: tuple[tuple[str, ...], ...] = (),
    failure_reason: str = "quality_validation_failed",
    repair_attempt: int = 1,
) -> str:
    """Build a measured, bounded integrity-repair prompt.

    The source remains authoritative. The previous model output and protected
    expressions are explicitly framed as untrusted document data so they cannot
    override the system/task instructions.
    """
    normalized_source = _normalize_text(source_text)
    normalized_previous = _normalize_text(previous_summary)
    target = _summary_length_target(normalized_source)
    previous_words = len(normalized_previous.split())
    required_block = "\n".join(f"- {fragment}" for fragment in required_math)
    coverage_block = "\n".join(
        f"- Source segment {index}/{len(coverage_requirements)} "
        f"(retain at least {1 if len(anchors) < 4 else 2} exact terms): "
        + ", ".join(anchors)
        for index, anchors in enumerate(coverage_requirements, start=1)
        if anchors
    )

    repair_rules = _summary_repair_instruction(
        target=target,
        previous_words=previous_words,
        failure_reason=failure_reason,
        repair_attempt=repair_attempt,
    )

    blocks = [
        (
            f"{BASE_CONSTRAINTS}\n\n{SUMMARIZE_RULES}\n\n"
            f"{_summary_length_instruction(normalized_source)}\n\n{repair_rules}"
        ),
        build_untrusted_content_block(
            normalized_source,
            label="AUTHORITATIVE DOCUMENT CONTENT",
        ),
        build_untrusted_content_block(
            normalized_previous,
            label="PREVIOUS SUMMARY CANDIDATE",
        ),
    ]
    if required_block:
        blocks.append(
            build_untrusted_content_block(
                required_block,
                label="REQUIRED MATHEMATICAL EXPRESSIONS",
            )
        )
    if coverage_block:
        blocks.append(
            build_untrusted_content_block(
                coverage_block,
                label="REQUIRED DOCUMENT COVERAGE",
            )
        )
    return "\n\n".join(blocks)


def _summary_repair_instruction(
    *,
    target: _SummaryLengthTarget,
    previous_words: int,
    failure_reason: str,
    repair_attempt: int,
) -> str:
    """Return failure-aware priorities for a bounded regeneration pass."""
    attempt_number = max(1, int(repair_attempt))
    reason = str(failure_reason or "quality_validation_failed").strip()

    rules = [
        f"INTEGRITY REPAIR PASS {attempt_number}:",
        f"- Measured failure: {reason}.",
        f"- The previous candidate contains {previous_words} whitespace-separated words.",
        "- Return only the corrected summary; do not report counts, validation, or instructions.",
        "- Preserve every expression in REQUIRED MATHEMATICAL EXPRESSIONS verbatim.",
        "- Preserve the material point of every REQUIRED DOCUMENT COVERAGE segment in source order.",
    ]

    if target.source_words >= MIN_COMPRESSION_CHECK_WORDS:
        rules.extend(
            [
                f"- HARD ACCEPTANCE RANGE: {target.minimum_words}-{target.maximum_words} words inclusive.",
                f"- Produce approximately {target.target_words} words; never exceed {target.maximum_words} words.",
                "- Count words silently using whitespace-separated tokens before returning the answer.",
                "- If the draft is outside the hard range, keep editing internally and do not return it.",
            ]
        )

        if previous_words > target.maximum_words:
            excess_words = previous_words - target.maximum_words
            rules.extend(
                [
                    f"- The candidate is at least {excess_words} words over the hard maximum; substantially compress it rather than lightly editing it.",
                    "- Use the previous candidate as the compression draft and consult the source only to verify facts and complete-document coverage.",
                    "- Remove repetition, filler, ceremonial wording, and non-substantive layout text first.",
                    "- Adjacent short metadata lines, greetings, and sign-off details may be compacted into concise clauses while retaining material names, contact facts, order, and meaning.",
                    "- For this repair, the hard word limit takes priority over line-for-line formatting; source order and material meaning remain mandatory.",
                ]
            )
        elif previous_words < target.minimum_words:
            missing_words = target.minimum_words - previous_words
            rules.extend(
                [
                    f"- The candidate is at least {missing_words} words below the hard minimum; restore omitted material rather than adding filler.",
                    "- Add only source-grounded facts needed for missing document sections, protected mathematics, qualifications, or conclusions.",
                ]
            )
        else:
            rules.append(
                "- The previous length is already compliant; preserve that concision while correcting the measured integrity failure."
            )
    else:
        rules.append(
            "- Keep the result meaningfully shorter than the source while prioritizing complete meaning and exact mathematics."
        )

    rules.extend(
        [
            "- Do not invent, infer, translate, or introduce any fact absent from the authoritative source.",
            "- Continue to obey the original summarization rules except for the explicit formatting priority stated above.",
        ]
    )
    return "\n".join(rules)


def _summary_length_target(source_text: str) -> _SummaryLengthTarget:
    source_words = max(1, len(source_text.split()))
    target_ratio = _content_aware_summary_ratio(source_text)

    if source_words < MIN_COMPRESSION_CHECK_WORDS:
        minimum_words = 1
        maximum_words = max(1, source_words - 1)
    else:
        minimum_words = max(
            1,
            math.ceil(source_words * SUMMARIZE_MIN_COMPRESSION_RATIO),
        )
        maximum_words = max(
            minimum_words,
            math.floor(source_words * SUMMARIZE_MAX_COMPRESSION_RATIO),
        )

    target_words = min(
        maximum_words,
        max(minimum_words, round(source_words * target_ratio)),
    )
    return _SummaryLengthTarget(
        source_words=source_words,
        minimum_words=minimum_words,
        target_words=target_words,
        maximum_words=maximum_words,
        target_ratio=target_ratio,
    )


def _summary_length_instruction(source_text: str) -> str:
    target = _summary_length_target(source_text)
    if target.source_words < MIN_COMPRESSION_CHECK_WORDS:
        return (
            "DOCUMENT-SPECIFIC LENGTH TARGET:\n"
            f"- Source length: {target.source_words} words.\n"
            f"- Produce a meaningfully shorter summary, aiming for about {target.target_words} words.\n"
            "- For short content, completeness and exact mathematical preservation take priority over a rigid percentage."
        )

    return (
        "DOCUMENT-SPECIFIC LENGTH TARGET:\n"
        f"- Source length: {target.source_words} words.\n"
        f"- HARD ACCEPTANCE RANGE: {target.minimum_words}-{target.maximum_words} words inclusive "
        "(25%-35% of the source); output outside this range is invalid.\n"
        f"- Aim for approximately {target.target_words} words; use the lower end for repetitive prose "
        "and the upper end for dense, technical, legal, or mathematical content.\n"
        "- Count whitespace-separated words silently before returning the summary.\n"
        "- Preserve source order and complete-document coverage within the hard range; visual line-for-line fidelity is secondary, and adjacent short metadata lines may be compacted without dropping material facts.\n"
        "- Never meet the word target by omitting a source section or changing protected mathematics."
    )


def _content_aware_summary_ratio(source_text: str) -> float:
    lines = [line.strip() for line in source_text.splitlines() if line.strip()]
    normalized_lines = [
        re.sub(r"\s+", " ", line).casefold()
        for line in lines
        if len(line.split()) >= 4
    ]
    repeated_lines = sum(
        count - 1
        for count in Counter(normalized_lines).values()
        if count > 1
    )
    repetition_ratio = (
        repeated_lines / len(normalized_lines)
        if normalized_lines
        else 0.0
    )

    source_words = max(1, len(source_text.split()))
    numeric_fact_count = len(re.findall(r"(?<!\w)[+-]?(?:\d+(?:\.\d+)?|\.\d+)%?", source_text))
    structured_line_count = sum(bool(_STRUCTURED_LINE_RE.match(line)) for line in lines)
    mathematically_dense = bool(_extract_protected_math_fragments(source_text))
    structurally_dense = structured_line_count >= max(3, math.ceil(len(lines) * 0.20))
    fact_dense = numeric_fact_count >= max(4, math.ceil(source_words * 0.03))

    if mathematically_dense or structurally_dense or fact_dense:
        return SUMMARIZE_MAX_COMPRESSION_RATIO
    if repetition_ratio >= 0.20:
        return SUMMARIZE_MIN_COMPRESSION_RATIO
    return SUMMARIZE_TARGET_COMPRESSION_RATIO


def _summarization_output_token_budget(source_text: str) -> int:
    """Return a source-proportional output budget bounded for production use."""
    target_summary_words = _summary_length_target(source_text).maximum_words
    estimated_tokens = math.ceil(
        target_summary_words * SUMMARIZE_TOKEN_PER_WORD_ESTIMATE
    ) + SUMMARIZE_OUTPUT_TOKEN_HEADROOM
    return min(
        SUMMARIZE_MAX_OUTPUT_TOKENS,
        max(SUMMARIZE_MIN_OUTPUT_TOKENS, estimated_tokens),
    )


def _canonical_number(value: str) -> str:
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        return value.strip()
    if number == 0:
        return "0"
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _canonical_numeric_range_pair(value: str) -> tuple[str, str] | None:
    """Canonicalize a standalone numeric range as facts, never as arithmetic.

    ``120-160``, ``120–160``, ``120—160`` and ``120 to 160`` all map to
    ``("120", "160")``. Negative endpoints are supported as well. This helper
    deliberately does not make every source range a mandatory summary fragment.
    """
    match = _NUMERIC_RANGE_RE.fullmatch(value.strip())
    if match is None:
        return None
    return (
        _canonical_number(match.group("start")),
        _canonical_number(match.group("end")),
    )


def summarize_text(
    text: str,
    *,
    backend: Optional[SummarizationBackend] = None,
    config: Optional[SummarizeConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = SummarizeProcessor(backend=backend, config=config)
    return processor.summarize(text)


def _canonical_math_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()


def _append_math_fragment(fragments: list[str], value: str) -> None:
    candidate = value.strip()
    if not candidate:
        return
    canonical = _canonical_math_text(candidate)
    if not canonical:
        return
    if any(_canonical_math_text(existing) == canonical for existing in fragments):
        return
    fragments.append(candidate)


def _math_line_is_formula_dominant(value: str) -> bool:
    prose_words = [
        word.casefold()
        for word in _MATH_PROSE_WORD_RE.findall(value)
        if word.casefold() not in _MATH_WORDS
    ]
    return len(prose_words) <= 1


def _is_ambiguous_word_division(value: str) -> bool:
    """Return whether ``value`` is ordinary word/word slash notation.

    Single-character operands such as ``x/y`` remain unambiguous symbolic
    expressions. If any purely alphabetic operand is a word, the slash alone
    cannot safely distinguish prose shorthand from division.
    """
    if _WORD_DIVISION_RE.fullmatch(value.strip()) is None:
        return False
    operands = [part.strip() for part in value.split("/")]
    return any(len(operand) > 1 for operand in operands)


def _extract_protected_math_fragments(source_text: str) -> list[str]:
    """Return explicit source math that a compression pass must not rewrite.

    The extractor is deliberately conservative: it protects unambiguous TeX,
    equation/calculation lines, arithmetic expressions, and common function
    expressions without classifying ordinary numbers or currency as mathematics.
    """
    fragments: list[str] = []

    for pattern in _DISPLAY_MATH_PATTERNS:
        for match in pattern.finditer(source_text):
            _append_math_fragment(fragments, match.group(0))
            if len(fragments) >= MAX_PROTECTED_MATH_FRAGMENTS:
                return fragments

    for match in _SINGLE_DOLLAR_MATH_RE.finditer(source_text):
        inner = match.group(1)
        if _MATH_SPECIFIC_TEX_RE.search(inner):
            _append_math_fragment(fragments, match.group(0))
            if len(fragments) >= MAX_PROTECTED_MATH_FRAGMENTS:
                return fragments

    # Search expression fragments across the complete source so long DOCX/TXT
    # paragraphs do not bypass formula protection merely because they exceed an
    # arbitrary line length. The patterns themselves are line-bounded.
    for pattern in (
        _EQUATION_FRAGMENT_RE,
        _ARITHMETIC_RE,
        _SUBTRACTION_WITH_RESULT_RE,
        _FUNCTION_EXPRESSION_RE,
        _SYMBOLIC_EXPRESSION_RE,
        _SYMBOLIC_SUBTRACTION_RE,
    ):
        for match in pattern.finditer(source_text):
            candidate = match.group(0)
            if (
                pattern is _SYMBOLIC_EXPRESSION_RE
                and _is_ambiguous_word_division(candidate)
            ):
                continue
            # A standalone numeric dash range is a factual range, not symbolic
            # subtraction. It remains governed by the fact-preservation rules.
            if (
                pattern in {_SYMBOLIC_EXPRESSION_RE, _SYMBOLIC_SUBTRACTION_RE}
                and _canonical_numeric_range_pair(candidate) is not None
            ):
                continue
            _append_math_fragment(fragments, candidate)
            if len(fragments) >= MAX_PROTECTED_MATH_FRAGMENTS:
                return fragments

    # Formula-dominant source lines are additionally protected as complete units.
    # This covers symbolic forms using operators such as ∑, ∫, and √ that may not
    # match the more conventional equation patterns above.
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 4000:
            continue
        if (
            _STRONG_MATH_LINE_RE.search(stripped)
            and _MATH_EVIDENCE_RE.search(stripped)
            and _math_line_is_formula_dominant(stripped)
        ):
            _append_math_fragment(fragments, stripped)
            if len(fragments) >= MAX_PROTECTED_MATH_FRAGMENTS:
                return fragments

    return fragments


def _validate_math_preservation(*, source_text: str, summarized_text: str) -> None:
    protected = _extract_protected_math_fragments(source_text)
    if not protected:
        return

    normalized_summary = _canonical_math_text(summarized_text)
    missing = [
        fragment
        for fragment in protected
        if _canonical_math_text(fragment) not in normalized_summary
    ]
    if missing:
        raise SummarizationQualityError(
            "protected_math_missing",
            protected_fragments=tuple(missing),
            metadata={"protected_fragment_count": len(protected)},
        )


def _coverage_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return [token for token in _COVERAGE_TOKEN_RE.findall(normalized) if token]


def _source_coverage_segments(source_text: str) -> tuple[str, ...]:
    words = source_text.split()
    word_count = len(words)
    if word_count < MIN_COMPRESSION_CHECK_WORDS:
        return ()

    if word_count < 240:
        segment_count = 3
    elif word_count < 800:
        segment_count = 4
    elif word_count < 2_000:
        segment_count = 5
    else:
        segment_count = 6

    base_size, remainder = divmod(word_count, segment_count)
    segments: list[str] = []
    cursor = 0
    for index in range(segment_count):
        size = base_size + (1 if index < remainder else 0)
        segments.append(" ".join(words[cursor:cursor + size]))
        cursor += size
    return tuple(segments)


def _coverage_anchor_requirements(source_text: str) -> tuple[tuple[str, ...], ...]:
    """Return distinctive lexical anchors from every ordered source segment.

    Summarization is intentionally compression-only, so meaningful coverage is
    expected to retain some exact source vocabulary. Segment-rare terms, numbers,
    and longer content words are preferred over generic prose words.
    """
    segments = _source_coverage_segments(source_text)
    if not segments:
        return ()

    tokens_by_segment = [_coverage_tokens(segment) for segment in segments]
    document_frequency: Counter[str] = Counter()
    for tokens in tokens_by_segment:
        document_frequency.update(set(tokens))

    requirements: list[tuple[str, ...]] = []
    for tokens in tokens_by_segment:
        counts = Counter(tokens)
        candidates = [
            token
            for token in counts
            if (
                (len(token) >= 4 or any(character.isdigit() for character in token))
                and token not in _COVERAGE_STOPWORDS
            )
        ]
        if not candidates:
            candidates = [
                token
                for token in counts
                if len(token) >= 3 and token not in _COVERAGE_STOPWORDS
            ]

        candidates.sort(
            key=lambda token: (
                document_frequency[token],
                0 if any(character.isdigit() for character in token) else 1,
                -len(token),
                -counts[token],
                token,
            )
        )
        requirements.append(tuple(candidates[:8]))

    return tuple(requirements)


def _validate_document_coverage(*, source_text: str, summarized_text: str) -> None:
    requirements = _coverage_anchor_requirements(source_text)
    if not requirements:
        return

    summary_tokens = set(_coverage_tokens(summarized_text))
    missing_segments: list[int] = []
    for index, anchors in enumerate(requirements, start=1):
        if not anchors:
            continue
        required_matches = 1 if len(anchors) < 4 else 2
        match_count = sum(anchor in summary_tokens for anchor in anchors)
        if match_count < required_matches:
            missing_segments.append(index)

    if missing_segments:
        raise SummarizationQualityError(
            "document_coverage_incomplete",
            metadata={
                "coverage_segment_count": len(requirements),
                "missing_segment_count": len(missing_segments),
                "missing_segments": ",".join(str(index) for index in missing_segments),
            },
        )


def _validate_summary_output(*, source_text: str, summarized_text: str) -> None:
    """Apply deterministic postconditions that can be verified locally.

    Semantic factuality still depends on the model, but measurable sources must
    land within the 25-35% summary-length band and retain lexical evidence from
    every ordered source segment. Explicit mathematical notation is protected so
    summarization cannot silently omit or alter a formula or stated calculation.
    """
    _validate_math_preservation(
        source_text=source_text,
        summarized_text=summarized_text,
    )
    _validate_document_coverage(
        source_text=source_text,
        summarized_text=summarized_text,
    )

    target = _summary_length_target(source_text)
    summarized_words = len(summarized_text.split())

    if target.source_words >= MIN_COMPRESSION_CHECK_WORDS:
        ratio = summarized_words / target.source_words
        if summarized_words < target.minimum_words:
            raise SummarizationQualityError(
                "over_condensed",
                metadata={
                    "source_words": target.source_words,
                    "summary_words": summarized_words,
                    "minimum_words": target.minimum_words,
                    "summary_ratio": round(ratio, 4),
                },
            )
        if summarized_words > target.maximum_words:
            raise SummarizationQualityError(
                "under_condensed",
                metadata={
                    "source_words": target.source_words,
                    "summary_words": summarized_words,
                    "maximum_words": target.maximum_words,
                    "summary_ratio": round(ratio, 4),
                },
            )
    elif target.source_words > 1 and summarized_words >= target.source_words:
        raise SummarizationQualityError(
            "not_condensed",
            metadata={
                "source_words": target.source_words,
                "summary_words": summarized_words,
            },
        )


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


__all__ = [
    "BASE_CONSTRAINTS",
    "SUMMARIZE_RULES",
    "MIN_COMPRESSION_CHECK_WORDS",
    "SUMMARIZE_MIN_COMPRESSION_RATIO",
    "SUMMARIZE_MAX_COMPRESSION_RATIO",
    "SUMMARIZE_TARGET_COMPRESSION_RATIO",
    "MAX_PROTECTED_MATH_FRAGMENTS",
    "MAX_INTEGRITY_REPAIR_ATTEMPTS",
    "SummarizationQualityError",
    "SummarizationOutputError",
    "SummarizationBackend",
    "LLMSummarizationBackend",
    "SummarizeConfig",
    "SummarizeProcessor",
    "build_summarize_prompt",
    "build_summarize_repair_prompt",
    "summarize_text",
]
