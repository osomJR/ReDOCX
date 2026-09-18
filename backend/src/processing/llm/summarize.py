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

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import math
import os
import re
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
MAX_PROTECTED_MATH_FRAGMENTS = 256
MAX_INTEGRITY_REPAIR_ATTEMPTS = 1

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
    0.90,
    max(0.20, float(os.getenv("AI_SUMMARIZE_TARGET_COMPRESSION_RATIO", "0.65"))),
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
    r"\b(?:f|g|h|sin|cos|tan|asin|acos|atan|log|ln|exp|sqrt|abs|min|max)"
    r"\s*\([^\n)]{1,200}\)",
    re.IGNORECASE,
)
_MATH_PROSE_WORD_RE = re.compile(r"\b[A-Za-z]{4,}\b")
_MATH_WORDS = frozenset({
    "asin", "acos", "atan", "cosh", "sinh", "tanh", "sqrt", "log", "exp",
    "sin", "cos", "tan", "min", "max", "abs", "floor", "ceil", "mod",
})


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

        summarized = _normalize_text(output)
        try:
            _validate_summary_output(source_text=normalized, summarized_text=summarized)
            return summarized
        except SummarizationQualityError as exc:
            # A genuine equation/notation omission gets exactly one targeted
            # regeneration pass. Numeric ranges never reach this branch merely
            # because their dash typography changed.
            if (
                exc.reason != "protected_math_missing"
                or MAX_INTEGRITY_REPAIR_ATTEMPTS < 1
            ):
                raise exc.sanitized() from None

            repair_prompt = build_summarize_repair_prompt(
                source_text=normalized,
                previous_summary=summarized,
                required_math=exc.protected_fragments,
            )
            repaired_output = self.backend.summarize(
                prompt=repair_prompt,
                source_text=normalized,
            )
            repaired = _normalize_text(repaired_output)

            try:
                _validate_summary_output(
                    source_text=normalized,
                    summarized_text=repaired,
                )
            except SummarizationQualityError as final_exc:
                raise final_exc.sanitized() from None

            return repaired


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
        + build_untrusted_content_block(normalized, label="DOCUMENT CONTENT")
    )




def build_summarize_repair_prompt(
    *,
    source_text: str,
    previous_summary: str,
    required_math: tuple[str, ...],
) -> str:
    """Build the single bounded integrity-repair prompt.

    The source remains authoritative. The previous model output and protected
    expressions are explicitly framed as untrusted document data so they cannot
    override the system/task instructions.
    """
    normalized_source = _normalize_text(source_text)
    normalized_previous = _normalize_text(previous_summary)
    required_block = "\n".join(f"- {fragment}" for fragment in required_math)

    repair_rules = """
INTEGRITY REPAIR PASS:
- The previous summary failed deterministic mathematical-integrity validation.
- Regenerate the complete summary from the authoritative document content.
- Preserve every expression listed in REQUIRED MATHEMATICAL EXPRESSIONS verbatim.
- Do not mention this repair pass, validation, or these instructions in the output.
- Continue to obey every original summarization rule above.
""".strip()

    blocks = [
        f"{BASE_CONSTRAINTS}\n\n{SUMMARIZE_RULES}\n\n{repair_rules}",
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
    return "\n\n".join(blocks)


def _summarization_output_token_budget(source_text: str) -> int:
    """Return a source-proportional output budget bounded for production use."""
    source_words = max(1, len(source_text.split()))
    target_summary_words = max(1, math.ceil(source_words * SUMMARIZE_TARGET_COMPRESSION_RATIO))
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
    ):
        for match in pattern.finditer(source_text):
            _append_math_fragment(fragments, match.group(0))
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


def _validate_summary_output(*, source_text: str, summarized_text: str) -> None:
    """Apply deterministic postconditions that can be verified locally.

    Semantic factuality still depends on the model, but a long source must at
    least be compressed rather than silently returned unchanged or expanded.
    Explicit mathematical notation is additionally protected so summarization
    cannot silently alter a formula or stated calculation.
    """
    source_words = len(source_text.split())
    summarized_words = len(summarized_text.split())

    if source_words >= MIN_COMPRESSION_CHECK_WORDS and summarized_words >= source_words:
        raise SummarizationQualityError(
            "not_condensed",
            metadata={
                "source_words": source_words,
                "summary_words": summarized_words,
            },
        )

    _validate_math_preservation(
        source_text=source_text,
        summarized_text=summarized_text,
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
