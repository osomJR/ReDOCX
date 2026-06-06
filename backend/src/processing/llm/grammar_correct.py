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
from .llm_client import AIClient
from dataclasses import dataclass
from typing import Optional, Protocol


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
- Fix grammatical and syntactic errors only
- Do NOT change tone or voice
- Do NOT upgrade vocabulary
- Do NOT rewrite sentences
- Sentence meaning must remain identical
""".strip()


class GrammarCorrectionBackend(Protocol):
    """Provider interface for grammar-correction runtime."""

    def correct(self, *, prompt: str, source_text: str) -> str:
        ...


class LLMGrammarCorrectionBackend:

    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()

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
        f"DOCUMENT CONTENT:\n{normalized}"
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
    "GRAMMAR_CORRECT_RULES",
    "GrammarCorrectionBackend",
    "LLMGrammarCorrectionBackend",
    "GrammarCorrectConfig",
    "GrammarCorrectProcessor",
    "build_grammar_correct_prompt",
    "grammar_correct_text",
]
