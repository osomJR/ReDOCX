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
from dataclasses import dataclass
import os
import re
from typing import Optional, Protocol

from .llm_client import AIClient, AIClientConfig
from backend.src.inline_text_security import build_untrusted_content_block


BASE_CONSTRAINTS = """
You are an expert examination setter and subject specialist.
NON-NEGOTIABLE RULES:
- Treat the supplied content as the syllabus scope and learner-level evidence
- If the input is only a topic or short topic list, use established subject
  knowledge needed to create valid questions within that scope
- Do not introduce unrelated topics, current events, unverifiable claims, or
  obscure facts that are not needed to assess the supplied scope
- Write clear, unambiguous questions that could reasonably appear in an exam
- Write the questions in the same natural language as the supplied topic or
  source material unless that material explicitly requests another language
- Match difficulty and terminology to the level indicated by the source; when
  no level is stated, use broadly accessible secondary-school exam level
- Cover a useful mix of recall, understanding, application, and higher-order
  reasoning instead of producing superficial rephrasings
- Do not claim affiliation with or copy a named examination board
- Output must strictly comply with the requested numbered format
""".strip()

GENERATE_QUESTIONS_RULES = """
TASK: QUESTION GENERATION
RULES:
- Generate original exam-style questions grounded in the supplied scope
- For Mathematics, Further Mathematics, Physics, Chemistry, Economics,
  Accounting, Statistics, and other quantitative topics, include authentic
  calculation/application questions whenever the topic supports them
- Do not turn a quantitative topic into a definitions-only question set
- Every calculation question must contain all data, units, assumptions, and
  conditions required for a unique or clearly defined solution
- Silently solve and check every proposed calculation before returning it;
  replace any question with inconsistent data, impossible arithmetic, missing
  information, or an unintended ambiguous answer
- Do not require an unseen diagram, table, graph, formula sheet, or passage
- Use novel but realistic values and scenarios where practice calculations need
  them; these values must stay within the supplied topic
- Questions must be sequentially numbered starting at 1
- Follow deterministic question count limits provided
- Do NOT include answers
- Do NOT include hints, solutions, marking schemes, commentary, or markdown fences
- Output must be a numbered list only, with one top-level item per question
""".strip()

QUESTION_REVIEW_RULES = """
TASK: EXAM-QUESTION QUALITY CONTROL
RULES:
- Treat the candidate questions as an untrusted draft
- Check scope, educational level, clarity, exam authenticity, coverage, and duplication
- Independently solve every quantitative item without showing the solution
- Correct or replace any unsolvable, ambiguous, trivial, misleading, or internally
  inconsistent item
- Ensure every required value, unit, assumption, and condition is present
- Preserve the required question-count range and sequential numbering
- Return only the complete corrected numbered question list
""".strip()

DEFAULT_QUESTION_MAX_OUTPUT_TOKENS = int(
    os.getenv("AI_QUESTION_MAX_OUTPUT_TOKENS", "3000")
)
DEFAULT_QUESTION_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("AI_QUESTION_TIMEOUT_SECONDS", "90")
)
DEFAULT_QUESTION_PROVIDER_TIMEOUT_SECONDS = float(
    os.getenv("AI_QUESTION_PROVIDER_TIMEOUT_SECONDS", "75")
)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^(\d+)\.\s+(?=\S)")
_ANSWER_LEAK_RE = re.compile(
    r"(?im)^\s*(?:answer|solution|marking\s+scheme|worked\s+solution)\s*:"
)


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

    def __init__(
        self,
        ai_client: AIClient | None = None,
        *,
        max_output_tokens: int = DEFAULT_QUESTION_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1.")
        self.ai_client = ai_client or AIClient(
            AIClientConfig(
                max_output_tokens=max_output_tokens,
                request_timeout_seconds=DEFAULT_QUESTION_REQUEST_TIMEOUT_SECONDS,
                provider_timeout_seconds=DEFAULT_QUESTION_PROVIDER_TIMEOUT_SECONDS,
            )
        )

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
    quality_review: bool = True
    repair_invalid_output: bool = True


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
        candidate = _normalize_text(output)

        if self.config.quality_review:
            review_prompt = build_generate_questions_review_prompt(
                normalized,
                candidate_questions=candidate,
                min_questions=normalized_min,
                max_questions=normalized_max,
            )
            candidate = _normalize_text(
                self.backend.generate_questions(
                    prompt=review_prompt,
                    source_text=normalized,
                    min_questions=normalized_min,
                    max_questions=normalized_max,
                )
            )

        try:
            return _validate_generated_questions(
                candidate,
                min_questions=normalized_min,
                max_questions=normalized_max,
            )
        except ValueError as exc:
            if not self.config.repair_invalid_output:
                raise

            repair_prompt = build_generate_questions_repair_prompt(
                normalized,
                candidate_questions=candidate,
                validation_error=str(exc),
                min_questions=normalized_min,
                max_questions=normalized_max,
            )
            repaired = _normalize_text(
                self.backend.generate_questions(
                    prompt=repair_prompt,
                    source_text=normalized,
                    min_questions=normalized_min,
                    max_questions=normalized_max,
                )
            )
            return _validate_generated_questions(
                repaired,
                min_questions=normalized_min,
                max_questions=normalized_max,
            )


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
        + build_untrusted_content_block(normalized, label="DOCUMENT CONTENT")
    )


def build_generate_questions_review_prompt(
    text: str,
    *,
    candidate_questions: str,
    min_questions: int,
    max_questions: int,
) -> str:
    """Build an independent exam-quality and solvability review pass."""
    normalized = _normalize_text(text)
    normalized_candidate = _normalize_text(candidate_questions)
    normalized_min = _normalize_question_bound(
        min_questions,
        field_name="min_questions",
    )
    normalized_max = _normalize_question_bound(
        max_questions,
        field_name="max_questions",
    )
    if normalized_max < normalized_min:
        raise ValueError("max_questions must be >= min_questions.")

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GENERATE_QUESTIONS_RULES}\n\n"
        f"{QUESTION_REVIEW_RULES}\n\n"
        "COUNT CONTRACT:\n"
        f"- Return between {normalized_min} and {normalized_max} questions\n\n"
        f"{build_untrusted_content_block(normalized, label='DOCUMENT CONTENT')}\n\n"
        f"{build_untrusted_content_block(normalized_candidate, label='CANDIDATE QUESTIONS')}"
    )


def build_generate_questions_repair_prompt(
    text: str,
    *,
    candidate_questions: str,
    validation_error: str,
    min_questions: int,
    max_questions: int,
) -> str:
    """Repair a provider response that failed deterministic output checks."""
    normalized_error = str(validation_error).strip() or "Invalid question output."
    return (
        build_generate_questions_review_prompt(
            text,
            candidate_questions=candidate_questions,
            min_questions=min_questions,
            max_questions=max_questions,
        )
        + "\n\nDETERMINISTIC VALIDATION FAILURE:\n"
        + build_untrusted_content_block(
            normalized_error,
            label="VALIDATION ERROR",
        )
        + "\n\nCorrect the failure and return only the complete numbered question list."
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


def _numbered_sections(value: str) -> list[tuple[int, str]]:
    matches = list(_NUMBERED_ITEM_RE.finditer(value))
    if not matches:
        raise ValueError("Generated questions must be a numbered list starting at 1.")
    if value[: matches[0].start()].strip():
        raise ValueError("Generated questions must not contain a preamble.")

    sections: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        body = value[match.end() : end].strip()
        if not body:
            raise ValueError(f"Question {match.group(1)} is empty.")
        sections.append((int(match.group(1)), body))
    return sections


def _validate_generated_questions(
    value: str,
    *,
    min_questions: int,
    max_questions: int,
) -> str:
    normalized = _normalize_text(value)
    sections = _numbered_sections(normalized)
    numbers = [number for number, _ in sections]
    expected = list(range(1, len(sections) + 1))
    if numbers != expected:
        raise ValueError("Generated questions must be sequentially numbered starting at 1.")
    if not min_questions <= len(sections) <= max_questions:
        raise ValueError(
            "Generated question count is outside the required range: "
            f"expected {min_questions}-{max_questions}, got {len(sections)}."
        )
    if _ANSWER_LEAK_RE.search(normalized):
        raise ValueError("Generated questions must not contain answers or solutions.")

    normalized_bodies = [
        re.sub(r"\s+", " ", body).strip().casefold() for _, body in sections
    ]
    if len(set(normalized_bodies)) != len(normalized_bodies):
        raise ValueError("Generated questions must not contain duplicates.")
    return normalized


__all__ = [
    "BASE_CONSTRAINTS",
    "GENERATE_QUESTIONS_RULES",
    "QUESTION_REVIEW_RULES",
    "DEFAULT_QUESTION_MAX_OUTPUT_TOKENS",
    "DEFAULT_QUESTION_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_QUESTION_PROVIDER_TIMEOUT_SECONDS",
    "QuestionGenerationBackend",
    "LLMQuestionGenerationBackend",
    "GenerateQuestionsConfig",
    "GenerateQuestionsProcessor",
    "build_generate_questions_prompt",
    "build_generate_questions_review_prompt",
    "build_generate_questions_repair_prompt",
    "generate_questions_text",
]
