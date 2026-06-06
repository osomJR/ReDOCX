from __future__ import annotations
"""
V1 question-generation processing.

Purpose:
- hold question-generation-specific processing logic outside analyzer.py
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

GENERATE_QUESTIONS_RULES = """
TASK: QUESTION GENERATION
RULES:
- Generate questions strictly from document content
- Questions must be sequentially numbered starting at 1
- Follow deterministic question count limits provided
- Do NOT include answers
- Do NOT include commentary
- Output must be a numbered list only
""".strip()


class QuestionGenerationBackend(Protocol):
    """Provider interface for question-generation runtime."""

    def generate_questions(
        self,
        *,
        prompt: str,
        source_text: str,
        min_questions: int,
        max_questions: int,
    ) -> str:
        ...


class LLMQuestionGenerationBackend:
    
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()
        
    def generate_questions(
        self,
        *,
        prompt: str,
        source_text: str,
        min_questions: int,
        max_questions: int,
    ) -> str:
        del source_text
        del min_questions
        del max_questions
        return self.ai_client.generate(prompt)
    
@dataclass(frozen=True)
class GenerateQuestionsConfig:
    """Optional knobs for future provider-backed question generation."""

    algorithm_version: Optional[str] = None


class GenerateQuestionsProcessor:
    """
    Stateless question-generation processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned question-generation prompt
    - delegate the actual text transformation to a backend
    - return generated numbered-list text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    - schema-scale metadata construction
    """

    def __init__(
        self,
        backend: Optional[QuestionGenerationBackend] = None,
        config: Optional[GenerateQuestionsConfig] = None,
    ) -> None:
        self.backend = backend or LLMQuestionGenerationBackend()
        self.config = config or GenerateQuestionsConfig()

    def generate_questions(
        self,
        text: str,
        *,
        min_questions: int,
        max_questions: int,
    ) -> str:
        normalized = _normalize_text(text)
        normalized_min = _normalize_question_bound(min_questions, field_name="min_questions")
        normalized_max = _normalize_question_bound(max_questions, field_name="max_questions")

        if normalized_max < normalized_min:
            raise ValueError("max_questions must be >= min_questions.")

        prompt = build_generate_questions_prompt(
            normalized,
            min_questions=normalized_min,
            max_questions=normalized_max,
        )
        output = self.backend.generate_questions(
            prompt=prompt,
            source_text=normalized,
            min_questions=normalized_min,
            max_questions=normalized_max,
        )
        return _normalize_text(output)


def build_generate_questions_prompt(
    text: str,
    *,
    min_questions: int,
    max_questions: int,
) -> str:
    """
    Build the contract-aligned prompt for question generation only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized = _normalize_text(text)
    normalized_min = _normalize_question_bound(min_questions, field_name="min_questions")
    normalized_max = _normalize_question_bound(max_questions, field_name="max_questions")

    if normalized_max < normalized_min:
        raise ValueError("max_questions must be >= min_questions.")

    extra_constraints = (
        "DETERMINISTIC SCALING RULE:\n"
        f"- Generate between {normalized_min} and {normalized_max} questions\n"
        "- Strictly respect this range"
    )

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GENERATE_QUESTIONS_RULES}\n\n"
        f"{extra_constraints}\n\n"
        f"DOCUMENT CONTENT:\n{normalized}"
    )


def generate_questions_text(
    text: str,
    *,
    min_questions: int,
    max_questions: int,
    backend: Optional[QuestionGenerationBackend] = None,
    config: Optional[GenerateQuestionsConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = GenerateQuestionsProcessor(backend=backend, config=config)
    return processor.generate_questions(
        text,
        min_questions=min_questions,
        max_questions=max_questions,
    )


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


def _normalize_question_bound(value: int, *, field_name: str) -> int:
    if not isinstance(value, int):
        raise TypeError(f"{field_name} must be an int.")
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1.")
    return value


__all__ = [
    "BASE_CONSTRAINTS",
    "GENERATE_QUESTIONS_RULES",
    "QuestionGenerationBackend",
    "LLMQuestionGenerationBackend",
    "GenerateQuestionsConfig",
    "GenerateQuestionsProcessor",
    "build_generate_questions_prompt",
    "generate_questions_text",
]
