from __future__ import annotations
"""
V1 explanation processing.

Purpose:
- hold explanation-specific processing logic outside analyzer.py
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

EXPLAIN_RULES = """
TASK: EXPLANATION
RULES:
- Explain the document clearly
- Match explanation difficulty to source complexity
- Do NOT oversimplify
- Output explanation separately from original content
- Do NOT reinterpret intent
""".strip()


class ExplanationBackend(Protocol):
    """Provider interface for explanation runtime."""

    def explain(self, *, prompt: str, source_text: str) -> str:
        ...


class LLMExplanationBackend:
    
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()

    def explain(self, *, prompt: str, source_text: str) -> str:
        del source_text
        return self.ai_client.generate(prompt)

@dataclass(frozen=True)
class ExplainConfig:
    """Optional knobs for future provider-backed explanation."""

    algorithm_version: Optional[str] = None


class ExplainProcessor:
    """
    Stateless explanation processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned explanation prompt
    - delegate the actual text transformation to a backend
    - return explanation text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    """

    def __init__(
        self,
        backend: Optional[ExplanationBackend] = None,
        config: Optional[ExplainConfig] = None,
    ) -> None:
        self.backend = backend or LLMExplanationBackend()
        self.config = config or ExplainConfig()

    def explain(self, text: str) -> str:
        normalized = _normalize_text(text)
        prompt = build_explain_prompt(normalized)
        output = self.backend.explain(prompt=prompt, source_text=normalized)
        return _normalize_text(output)


def build_explain_prompt(text: str) -> str:
    """
    Build the contract-aligned prompt for explanation only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized = _normalize_text(text)
    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{EXPLAIN_RULES}\n\n"
        f"DOCUMENT CONTENT:\n{normalized}"
    )


def explain_text(
    text: str,
    *,
    backend: Optional[ExplanationBackend] = None,
    config: Optional[ExplainConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = ExplainProcessor(backend=backend, config=config)
    return processor.explain(text)


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


__all__ = [
    "BASE_CONSTRAINTS",
    "EXPLAIN_RULES",
    "ExplanationBackend",
    "LLMExplanationBackend",
    "ExplainConfig",
    "ExplainProcessor",
    "build_explain_prompt",
    "explain_text"
]
