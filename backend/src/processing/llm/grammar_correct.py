from __future__ import annotations
"""
V1 grammar-correction processing.

Purpose:
- hold grammar-correction-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free
- schema-agnostic: this module returns processed text only
- provider-backed by default via the shared LLM client
- prompt construction is separated from runtime execution
"""
from .llm_client import AIClient, AIClientConfig, DEFAULT_MAX_OUTPUT_TOKENS
from backend.src.inline_text_security import build_untrusted_content_block
from dataclasses import dataclass
import os
from typing import Optional, Protocol



GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS = max(
    DEFAULT_MAX_OUTPUT_TOKENS,
    int(os.getenv("AI_GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS", "10000")),
)

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
- Maintain original sentence rhythm
- Act as a neutral, invisible processor
- Output must strictly comply with formatting constraints
""".strip()

GRAMMAR_CORRECT_RULES = """
TASK: GRAMMAR CORRECTION ONLY
RULES:
- Correct grammar, syntax, subject-verb agreement, verb tense consistency, punctuation, capitalization, and clear typographical errors
- Preserve the exact meaning, facts, names, numbers, citations, and technical terminology unless a grammatical correction requires punctuation around them
- Preserve the original language; never translate the text
- Preserve tone, formality, voice, paragraph order, headings, list structure, and line breaks as closely as possible
- Make the smallest correction necessary for each error; do not paraphrase, summarize, expand, simplify, or creatively rewrite
- Do not upgrade vocabulary or replace correct wording merely for style
- If a sentence is already grammatically correct, leave it unchanged
- Return only the corrected document text with no commentary, labels, explanations, or markdown wrapper
""".strip()


class GrammarCorrectionBackend(Protocol):
    """Provider interface for grammar-correction runtime."""

    def correct(self, *, prompt: str, source_text: str) -> str:
        ...


class LLMGrammarCorrectionBackend:

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient(
            AIClientConfig(max_output_tokens=GRAMMAR_CORRECT_MAX_OUTPUT_TOKENS)
        )

    def correct(self, *, prompt: str, source_text: str) -> str:
        del source_text
        return self.ai_client.generate(prompt)
    
@dataclass(frozen=True)
class GrammarCorrectConfig:
    """Optional knobs for future provider-backed grammar correction."""

    algorithm_version: Optional[str] = None


class GrammarCorrectProcessor:
    """
    Stateless grammar-correction processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned grammar-correction prompt
    - delegate the actual text transformation to a backend
    - return corrected text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    """

    def __init__(
        self,
        backend: Optional[GrammarCorrectionBackend] = None,
        config: Optional[GrammarCorrectConfig] = None,
    ) -> None:
        self.backend = backend or LLMGrammarCorrectionBackend()
        self.config = config or GrammarCorrectConfig()

    def correct(self, text: str) -> str:
        normalized = _normalize_text(text)
        prompt = build_grammar_correct_prompt(normalized)
        output = self.backend.correct(prompt=prompt, source_text=normalized)
        return _normalize_text(output)


def build_grammar_correct_prompt(text: str) -> str:
    """
    Build the contract-aligned prompt for grammar correction only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized = _normalize_text(text)
    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GRAMMAR_CORRECT_RULES}\n\n"
        + build_untrusted_content_block(normalized, label="DOCUMENT CONTENT")
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
    "GRAMMAR_CORRECT_RULES",
    "GrammarCorrectionBackend",
    "LLMGrammarCorrectionBackend",
    "GrammarCorrectConfig",
    "GrammarCorrectProcessor",
    "build_grammar_correct_prompt",
    "grammar_correct_text",
]
