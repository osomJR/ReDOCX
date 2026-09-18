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
import re
from typing import Optional, Protocol

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
_ARITHMETIC_RE = re.compile(
    r"(?<!\w)"
    r"(?:[-+]?\d+(?:\.\d+)?|\.\d+)"
    r"(?:\s*(?:\+|-|\*|/|\^|×|÷)\s*(?:[-+]?\d+(?:\.\d+)?|\.\d+)){1,}"
    r"(?:\s*(?:=|≈|≠|≤|≥|<|>)\s*(?:[-+]?\d+(?:\.\d+)?|\.\d+))?"
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


class SummarizationOutputError(RuntimeError):
    """Raised when generated summary output violates a deterministic safety contract."""


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
        del source_text
        return self.ai_client.generate(prompt)


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
        _validate_summary_output(source_text=normalized, summarized_text=summarized)
        return summarized


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
        preview = _canonical_math_text(missing[0])
        if len(preview) > 180:
            preview = f"{preview[:177]}..."
        raise SummarizationOutputError(
            "Summarization output changed or removed a protected mathematical "
            f"expression: {preview!r}."
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
        raise SummarizationOutputError(
            "Summarization output was not shorter than the source text."
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
    "SummarizationOutputError",
    "SummarizationBackend",
    "LLMSummarizationBackend",
    "SummarizeConfig",
    "SummarizeProcessor",
    "build_summarize_prompt",
    "summarize_text",
]
