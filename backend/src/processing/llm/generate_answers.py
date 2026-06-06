from __future__ import annotations
"""
V1 answer-generation processing.

Purpose:
- hold answer-generation-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free
- schema-agnostic: this module returns generated text only
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

GENERATE_ANSWERS_RULES = """
TASK: ANSWER GENERATION
RULES:
- Generate answers strictly from document content
- Preserve one-to-one alignment with the provided numbered questions
- Output answers in the same numbered order as the input questions
- Do NOT introduce extra questions
- Do NOT include commentary outside the numbered answers
- Output must be a numbered list only
""".strip()


class AnswerGenerationBackend(Protocol):
    """Provider interface for answer-generation runtime."""

    def generate_answers(
        self,
        *,
        prompt: str,
        source_text: str,
        questions_text: str,
        expected_question_count: int,
    ) -> str:
        ...
class LLMAnswerGenerationBackend:
    
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()

    def generate_answers(
        self,
        *,
        prompt: str,
        source_text: str,
        questions_text: str,
        expected_question_count: int,
    ) -> str:
        del source_text
        del questions_text
        del expected_question_count
        return self.ai_client.generate(prompt)

@dataclass(frozen=True)
class GenerateAnswersConfig:
    """Optional knobs for future provider-backed answer generation."""

    algorithm_version: Optional[str] = None


class GenerateAnswersProcessor:
    """
    Stateless answer-generation processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned answer-generation prompt
    - delegate the actual text transformation to a backend
    - return generated numbered-list text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    - schema answer-count metadata construction
    """

    def __init__(
        self,
        backend: Optional[AnswerGenerationBackend] = None,
        config: Optional[GenerateAnswersConfig] = None,
    ) -> None:
        self.backend = backend or LLMAnswerGenerationBackend()
        self.config = config or GenerateAnswersConfig()

    def generate_answers(
        self,
        text: str,
        *,
        questions_text: str,
        expected_question_count: int,
    ) -> str:
        normalized_text = _normalize_text(text)
        normalized_questions = _normalize_text(questions_text)
        normalized_expected = _normalize_question_count(expected_question_count)

        prompt = build_generate_answers_prompt(
            normalized_text,
            questions_text=normalized_questions,
            expected_question_count=normalized_expected,
        )
        output = self.backend.generate_answers(
            prompt=prompt,
            source_text=normalized_text,
            questions_text=normalized_questions,
            expected_question_count=normalized_expected,
        )
        return _normalize_text(output)


def build_generate_answers_prompt(
    text: str,
    *,
    questions_text: str,
    expected_question_count: int,
) -> str:
    """
    Build the contract-aligned prompt for answer generation only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized_text = _normalize_text(text)
    normalized_questions = _normalize_text(questions_text)
    normalized_expected = _normalize_question_count(expected_question_count)

    extra_constraints = (
        "ANSWER ALIGNMENT RULES:\n"
        f"- Produce exactly {normalized_expected} answers\n"
        "- Keep numbering aligned to the provided questions\n"
        "- Do not skip or merge items"
    )

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GENERATE_ANSWERS_RULES}\n\n"
        f"{extra_constraints}\n\n"
        f"QUESTIONS:\n{normalized_questions}\n\n"
        f"DOCUMENT CONTENT:\n{normalized_text}"
    )


def generate_answers_text(
    text: str,
    *,
    questions_text: str,
    expected_question_count: int,
    backend: Optional[AnswerGenerationBackend] = None,
    config: Optional[GenerateAnswersConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = GenerateAnswersProcessor(backend=backend, config=config)
    return processor.generate_answers(
        text,
        questions_text=questions_text,
        expected_question_count=expected_question_count,
    )


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


def _normalize_question_count(value: int) -> int:
    if not isinstance(value, int):
        raise TypeError("expected_question_count must be an int.")
    if value < 1:
        raise ValueError("expected_question_count must be >= 1.")
    return value


__all__ = [
    "BASE_CONSTRAINTS",
    "GENERATE_ANSWERS_RULES",
    "AnswerGenerationBackend",
    "LLMAnswerGenerationBackend",
    "GenerateAnswersConfig",
    "GenerateAnswersProcessor",
    "build_generate_answers_prompt",
    "generate_answers_text",
]
