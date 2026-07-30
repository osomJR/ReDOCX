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
from dataclasses import dataclass
import os
import re
from typing import Optional, Protocol

from .llm_client import AIClient, AIClientConfig
from backend.src.inline_text_security import build_untrusted_content_block


BASE_CONSTRAINTS = """
You are an expert examination solver, teacher, and subject specialist.
NON-NEGOTIABLE RULES:
- Answer the exact supplied questions at the educational level indicated by the source
- Write explanations and solution labels in the same natural language as the
  supplied questions; retain standard symbols, formulae, and units as needed
- Use the source as the topic and evidence boundary, while applying established
  formulas, definitions, laws, and reasoning required to solve the questions
- Never invent a fact, value, assumption, quotation, or source statement
- Recompute quantitative results independently before returning them
- Prefer exact values during working and round only at the final step unless the
  question explicitly requires another convention
- Preserve units, signs, significant figures, chemical notation, and variable meanings
- State any unavoidable assumption explicitly; if essential information is
  genuinely missing, identify what is missing instead of fabricating it
- Output must strictly comply with the requested numbered format
""".strip()

GENERATE_ANSWERS_RULES = """
TASK: ANSWER GENERATION
RULES:
- Preserve one-to-one alignment with the provided numbered questions
- Output answers in the same numbered order as the input questions
- For every calculation, derivation, proof, or quantitative application, show
  the complete method rather than only the final answer
- A calculation solution must include, where applicable: Given/Required,
  Formula or Principle, Substitution, step-by-step Working with intermediate
  values, units, and a clearly labelled Final answer
- Do not number intermediate steps with top-level forms such as "1." or "2.";
  use descriptive labels or bullets so only answers have top-level numbering
- For conceptual questions, give a sufficiently detailed explanation with the
  reasoning and key points an examiner would award marks for
- Check arithmetic, algebra, calculus, dimensions, unit conversions,
  stoichiometry, signs, economic interpretation, and rounding as applicable
- Do NOT introduce extra questions
- Do NOT include commentary outside the numbered answer sections
- Do NOT use markdown fences
- Output must contain numbered answer sections only
""".strip()

ANSWER_REVIEW_RULES = """
TASK: WORKED-SOLUTION QUALITY CONTROL
RULES:
- Treat the candidate answers as an untrusted draft
- Independently solve each original question and compare the result with the draft
- Correct every factual, logical, algebraic, arithmetic, unit, sign, notation,
  significant-figure, chemistry, physics, mathematics, or economics error
- Expand any calculation answer that gives only a result or skips material working
- Ensure the final conclusion follows from the displayed working
- Ensure every original question has exactly one matching answer section
- Return the complete corrected worked solutions only
""".strip()

DEFAULT_ANSWER_MAX_OUTPUT_TOKENS = int(
    os.getenv("AI_ANSWER_MAX_OUTPUT_TOKENS", "12000")
)
DEFAULT_ANSWER_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("AI_ANSWER_TIMEOUT_SECONDS", "180")
)
DEFAULT_ANSWER_PROVIDER_TIMEOUT_SECONDS = float(
    os.getenv("AI_ANSWER_PROVIDER_TIMEOUT_SECONDS", "165")
)
_NUMBERED_ITEM_RE = re.compile(r"(?m)^(\d+)\.\s+(?=\S)")
_CALCULATION_CUE_RE = re.compile(
    r"(?i)"
    r"(?:\b(?:calculate|compute|solve|evaluate|determine|obtain|estimate|"
    r"deduce|convert|simplify|factor(?:ise|ize)?|"
    r"differentiate|integrate|derive|prove|show\s+that|work\s+out|"
    r"balance\s+(?:the\s+)?(?:equation|reaction)|"
    r"find\s+the\s+(?:value|values|root|roots|magnitude|speed|velocity|"
    r"acceleration|force|mass|energy|power|current|voltage|resistance|"
    r"concentration|moles?|yield|ph|gradient|area|volume|probability|"
    r"determinant|eigenvalue|elasticity|equilibrium|profit|cost|revenue)|"
    r"calculer|résoudre|evaluer|évaluer|simplifier|factoriser|dériver|"
    r"intégrer|démontrer|trouver\s+(?:la|le|les)\s+(?:valeur|racines?|"
    r"vitesse|accélération|force|masse|énergie|puissance|concentration)|"
    r"calcular|resolver|evaluar|determinar|hallar|encontrar|simplificar|"
    r"factorizar|derivar|integrar|demostrar|"
    r"berechnen|lösen|bestimmen|ermitteln|vereinfachen|faktorisieren|"
    r"ableiten|integrieren|beweisen|"
    r"avaliar|fatorizar|demonstrar|"
    r"calcolare|risolvere|valutare|determinare|trovare|semplificare|"
    r"fattorizzare|derivare|integrare|dimostrare|"
    r"bereken|oplossen|bepalen|vinden|vereenvoudigen|factoriseren|"
    r"differentiëren|integreren|bewijzen|"
    r"hesapla(?:mak)?|çöz(?:mek)?|değerlendir(?:mek)?|belirle(?:mek)?|"
    r"bul(?:mak)?|sadeleştir(?:mek)?|kanıtla(?:mak)?|"
    r"вычислите|рассчитайте|решите|определите|найдите|упростите|"
    r"разложите|дифференцируйте|интегрируйте|докажите|"
    r"hesabu|kokotoa|tatua|amua|pata|thibitisha|"
    r"lissafa|warware|nemo|tantance|"
    r"iṣirò|ṣe\s+iṣirò|yanju|"
    r"gbakọọ|dozie|chọta|gosi)\b|"
    r"(?:احسب|حل|أوجد|حدد|قدّر|بسط|حلّل|اشتق|كامل|أثبت|"
    r"计算|計算|求解|解出|求出|确定|確定|估算|化简|化簡|因式分解|"
    r"微分|积分|積分|证明|證明|"
    r"解け|求め|決定|推定|簡単|因数分解|証明|"
    r"계산|풀어|구하|결정|추정|단순화|인수분해|미분|적분|증명|"
    r"गणना|हल\s+कर|ज्ञात|निर्धारित|अनुमान|सरल|गुणनखंड|"
    r"अवकलन|समाकलन|सिद्ध))"
    r"|(?:^|[\s(])[A-Za-z0-9)]\s*[=+\-*/^]\s*[A-Za-z0-9(]",
)
_WORKING_MARKER_RE = re.compile(
    r"(?i)\b(?:formula|principle|substitution|working|calculation|derivation|"
    r"equation|formule|principe|calcul|dérivation|équation|"
    r"fórmula|principio|sustitución|cálculo|derivación|ecuación|"
    r"formel|prinzip|einsetzen|rechnung|ableitung|gleichung|"
    r"princípio|substituição|cálculo|derivação|equação|"
    r"formula|principio|sostituzione|calcolo|derivazione|equazione|"
    r"beginsel|substitutie|berekening|afleiding|vergelijking|"
    r"formül|ilke|yerine\s+koyma|hesaplama|türetme|denklem|"
    r"формула|принцип|подстановка|вычисление|решение|уравнение|"
    r"fomula|kanuni|uingizaji|hesabu|mlinganyo|"
    r"dabara|ƙa'ida|sauyawa|lissafi|daidaito|"
    r"agbekalẹ|ìlànà|àfikún|ìṣirò|idogba|"
    r"usoro|ụkpụrụ|nnọchi|ṅgụkọ|nhatanha)\s*:"
    r"|(?:الصيغة|المبدأ|التعويض|الحساب|الاشتقاق|المعادلة|"
    r"公式|原理|代入|计算|計算|推导|推導|方程|"
    r"原則|代入|計算|導出|方程式|"
    r"공식|원리|대입|계산|유도|방정식|"
    r"सूत्र|सिद्धांत|प्रतिस्थापन|गणना|व्युत्पत्ति|समीकरण)\s*:"
    r"|(?:^|\n)\s*[^\n=]{0,80}=",
)
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![\w.])[+-]?(?:\d+(?:[.,]\d+)?|\d+\s*/\s*\d+)(?![\w.])"
)
_QUANTITATIVE_CONTEXT_RE = re.compile(
    r"(?i)(?:[%°$€£₦¥₹]|"
    r"\b(?:mm|cm|m|km|mg|g|kg|ml|l|s|min|h|hz|n|j|w|v|a|ohm|"
    r"mol|pa|kpa|mpa|m/s|m/s²|ms-?1|m\s*s\^-?1|"
    r"naira|dollar|euro|pound|percent|percentage|ratio|probability)\b)"
)
_FINAL_MARKER_RE = re.compile(
    r"(?i)\b(?:final\s+answer|therefore|hence|thus|conclusion|"
    r"réponse\s+finale|donc|ainsi|"
    r"respuesta\s+final|por\s+lo\s+tanto|"
    r"endergebnis|daher|"
    r"resposta\s+final|portanto|"
    r"risposta\s+finale|quindi|"
    r"eindantwoord|dus|"
    r"nihai\s+cevap|sonuç|"
    r"окончательный\s+ответ|следовательно|"
    r"jibu\s+la\s+mwisho|"
    r"amsa\s+ta\s+ƙarshe|"
    r"azịza\s+ikpeazụ|"
    r"ìdáhùn\s+ìkẹyìn|"
    r"الإجابة\s+النهائية|النتيجة\s+النهائية|إذن|لذلك|"
    r"最终答案|最終答案|因此|"
    r"最終(?:的な)?答え|したがって|"
    r"최종\s*답(?:변)?|따라서|"
    r"अंतिम\s+उत्तर|अतः)\b|∴"
)


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

    def __init__(
        self,
        ai_client: AIClient | None = None,
        *,
        max_output_tokens: int = DEFAULT_ANSWER_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1.")
        self.ai_client = ai_client or AIClient(
            AIClientConfig(
                max_output_tokens=max_output_tokens,
                request_timeout_seconds=DEFAULT_ANSWER_REQUEST_TIMEOUT_SECONDS,
                provider_timeout_seconds=DEFAULT_ANSWER_PROVIDER_TIMEOUT_SECONDS,
            )
        )

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
    quality_review: bool = True
    repair_invalid_output: bool = True


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
        candidate = _normalize_text(output)

        if self.config.quality_review:
            review_prompt = build_generate_answers_review_prompt(
                normalized_text,
                questions_text=normalized_questions,
                candidate_answers=candidate,
                expected_question_count=normalized_expected,
            )
            candidate = _normalize_text(
                self.backend.generate_answers(
                    prompt=review_prompt,
                    source_text=normalized_text,
                    questions_text=normalized_questions,
                    expected_question_count=normalized_expected,
                )
            )

        try:
            return _validate_generated_answers(
                candidate,
                questions_text=normalized_questions,
                expected_question_count=normalized_expected,
            )
        except ValueError as exc:
            if not self.config.repair_invalid_output:
                raise

            repair_prompt = build_generate_answers_repair_prompt(
                normalized_text,
                questions_text=normalized_questions,
                candidate_answers=candidate,
                validation_error=str(exc),
                expected_question_count=normalized_expected,
            )
            repaired = _normalize_text(
                self.backend.generate_answers(
                    prompt=repair_prompt,
                    source_text=normalized_text,
                    questions_text=normalized_questions,
                    expected_question_count=normalized_expected,
                )
            )
            return _validate_generated_answers(
                repaired,
                questions_text=normalized_questions,
                expected_question_count=normalized_expected,
            )


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
        "- Do not skip or merge items\n"
        "- Begin each answer with its top-level number and place detailed working "
        "on following labelled lines"
    )

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GENERATE_ANSWERS_RULES}\n\n"
        f"{extra_constraints}\n\n"
        f"{build_untrusted_content_block(normalized_questions, label='QUESTIONS')}\n\n"
        f"{build_untrusted_content_block(normalized_text, label='DOCUMENT CONTENT')}"
    )


def build_generate_answers_review_prompt(
    text: str,
    *,
    questions_text: str,
    candidate_answers: str,
    expected_question_count: int,
) -> str:
    """Build an independent correctness and worked-solution review pass."""
    normalized_text = _normalize_text(text)
    normalized_questions = _normalize_text(questions_text)
    normalized_candidate = _normalize_text(candidate_answers)
    normalized_expected = _normalize_question_count(expected_question_count)

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{GENERATE_ANSWERS_RULES}\n\n"
        f"{ANSWER_REVIEW_RULES}\n\n"
        "ALIGNMENT CONTRACT:\n"
        f"- Return exactly {normalized_expected} sequentially numbered answer sections\n"
        "- For each quantitative item, include explicit Formula/Principle, Working, "
        "and Final answer labels (or their natural equivalents in the answer language)\n\n"
        f"{build_untrusted_content_block(normalized_questions, label='ORIGINAL QUESTIONS')}\n\n"
        f"{build_untrusted_content_block(normalized_text, label='DOCUMENT CONTENT')}\n\n"
        f"{build_untrusted_content_block(normalized_candidate, label='CANDIDATE ANSWERS')}"
    )


def build_generate_answers_repair_prompt(
    text: str,
    *,
    questions_text: str,
    candidate_answers: str,
    validation_error: str,
    expected_question_count: int,
) -> str:
    """Repair a provider response that failed deterministic answer checks."""
    normalized_error = str(validation_error).strip() or "Invalid answer output."
    return (
        build_generate_answers_review_prompt(
            text,
            questions_text=questions_text,
            candidate_answers=candidate_answers,
            expected_question_count=expected_question_count,
        )
        + "\n\nDETERMINISTIC VALIDATION FAILURE:\n"
        + build_untrusted_content_block(
            normalized_error,
            label="VALIDATION ERROR",
        )
        + "\n\nCorrect the failure and return only the complete worked solutions."
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


def _numbered_sections(value: str, *, label: str) -> list[tuple[int, str]]:
    matches = list(_NUMBERED_ITEM_RE.finditer(value))
    if not matches:
        raise ValueError(f"{label} must be a numbered list starting at 1.")
    if value[: matches[0].start()].strip():
        raise ValueError(f"{label} must not contain a preamble.")

    sections: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        body = value[match.end() : end].strip()
        if not body:
            raise ValueError(f"{label} item {match.group(1)} is empty.")
        sections.append((int(match.group(1)), body))
    return sections


def _validate_generated_answers(
    value: str,
    *,
    questions_text: str,
    expected_question_count: int,
) -> str:
    normalized = _normalize_text(value)
    answers = _numbered_sections(normalized, label="Generated answers")
    questions = _numbered_sections(
        _normalize_text(questions_text),
        label="Questions",
    )

    if len(questions) != expected_question_count:
        raise ValueError(
            "expected_question_count does not match the supplied numbered questions."
        )

    numbers = [number for number, _ in answers]
    expected_numbers = list(range(1, expected_question_count + 1))
    if numbers != expected_numbers:
        raise ValueError(
            "Generated answers must exactly match the sequential question numbering."
        )

    for (question_number, question), (_, answer) in zip(questions, answers):
        if not _is_calculation_question(question):
            continue
        non_final_lines = "\n".join(
            line
            for line in answer.splitlines()
            if not _FINAL_MARKER_RE.search(line)
        )
        if not _WORKING_MARKER_RE.search(non_final_lines):
            raise ValueError(
                f"Answer {question_number} is a calculation but does not show its working."
            )
        if not _FINAL_MARKER_RE.search(answer):
            raise ValueError(
                f"Answer {question_number} is a calculation but has no clearly labelled final answer."
            )

    return normalized


def _is_calculation_question(question: str) -> bool:
    if _CALCULATION_CUE_RE.search(question):
        return True
    numeric_tokens = _NUMERIC_TOKEN_RE.findall(question)
    return len(numeric_tokens) >= 2 or (
        bool(numeric_tokens) and bool(_QUANTITATIVE_CONTEXT_RE.search(question))
    )


__all__ = [
    "BASE_CONSTRAINTS",
    "GENERATE_ANSWERS_RULES",
    "ANSWER_REVIEW_RULES",
    "DEFAULT_ANSWER_MAX_OUTPUT_TOKENS",
    "DEFAULT_ANSWER_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_ANSWER_PROVIDER_TIMEOUT_SECONDS",
    "AnswerGenerationBackend",
    "LLMAnswerGenerationBackend",
    "GenerateAnswersConfig",
    "GenerateAnswersProcessor",
    "build_generate_answers_prompt",
    "build_generate_answers_review_prompt",
    "build_generate_answers_repair_prompt",
    "generate_answers_text",
]
