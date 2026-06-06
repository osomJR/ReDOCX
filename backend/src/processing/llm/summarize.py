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
from .llm_client import AIClient
from dataclasses import dataclass
from typing import Optional, Protocol


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
- Maintain original sentence rhythm
- Act as a neutral, invisible processor
- Output must strictly comply with formatting constraints
""".strip()

SUMMARIZE_RULES = """
TASK: COMPRESSION-ONLY SUMMARIZATION
RULES:
- Reduce length only
- Preserve argument flow
- Preserve paragraph structure
- Remove redundancy, not meaning
- No stylistic paraphrasing
- No rewording unless required for compression
""".strip()


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
        f"DOCUMENT CONTENT:\n{normalized}"
    )


def summarize_text(
    text: str,
    *,
    backend: Optional[SummarizationBackend] = None,
    config: Optional[SummarizeConfig] = None,
) -> str:
    """
    Functional convenience wrapper for analyzer integration.

    Example future analyzer change:
        from src.processing.llm_processing.summarize import summarize_text
        ...
        inline_builder=summarize_text
    """
    processor = SummarizeProcessor(backend=backend, config=config)
    return processor.summarize(text)


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
    "SummarizationBackend",
    "LLMSummarizationBackend",
    "SummarizeConfig",
    "SummarizeProcessor",
    "build_summarize_prompt",
    "summarize_text",
]
