from __future__ import annotations
"""
V1 translation processing.

Purpose:
- hold translation-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free
- schema-agnostic: this module returns processed text only
- provider-backed by default via the shared LLM client
- prompt construction is separated from runtime execution 
"""
from collections import Counter
from dataclasses import dataclass
import os
import re
from typing import Optional, Protocol

from .llm_client import AIClient, AIClientConfig
from backend.src.inline_text_security import (
    build_untrusted_content_block,
    validate_language_identifier,
)


# -------------------------
# Contract-aligned prompt rules
# -------------------------

BASE_CONSTRAINTS = """
You are a professional document processing AI.
NON-NEGOTIABLE RULES:
- Automatically detect source language when source_language is set to auto
- Translate the full document into the target language
- Preserve voice, tone, and structure
- Maintain paragraph-to-paragraph alignment
- No localization or cultural adaptation
- Output must mirror original structure
- Return only the translated document content
- Do not include introductions, explanations, summaries, apologies, markdown fences, or comments
- Do not say "Certainly", "Here is", "Translated document", or similar assistant preambles
- Do not return the original source text except for names, emails, URLs, technical tool names, product names, company names, and proper nouns that should remain unchanged
- Preserve the original paragraph order exactly
- Preserve the original sentence order exactly inside each paragraph
- Do not split one source sentence into separate disconnected lines
- Preserve personal names exactly as written
- Preserve company names exactly as written unless the user explicitly requests transliteration
- Preserve emails, URLs, phone numbers, dates, and technical identifiers exactly
- For Arabic output, use natural Modern Standard Arabic
- Do not reverse phone numbers, email addresses, URLs, dates, or Latin-script names
""".strip()

TRANSLATE_RULES = """
TASK: LANGUAGE TRANSLATION
RULES:
- Automatically detect source language when source_language is set to auto
- Translate meaning, grammatical relationships, terminology, and register accurately
- Resolve idioms by meaning rather than translating them word-for-word
- Keep technical terminology consistent throughout the document
- Do not omit, invent, weaken, strengthen, or contradict any source statement
- Preserve voice, tone, and structure
- Maintain paragraph-to-paragraph alignment
- No localization or cultural adaptation
- Output must mirror original structure
- Before returning the translation, silently compare every source sentence with
  its translated counterpart and correct semantic, grammatical, spelling,
  script, terminology, omission, and hallucination errors
""".strip()

DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS = int(
    os.getenv("AI_TRANSLATION_MAX_OUTPUT_TOKENS", "5000")
)
DEFAULT_TRANSLATION_REQUEST_TIMEOUT_SECONDS = float(
    os.getenv("AI_TRANSLATION_TIMEOUT_SECONDS", "90")
)
DEFAULT_TRANSLATION_PROVIDER_TIMEOUT_SECONDS = float(
    os.getenv("AI_TRANSLATION_PROVIDER_TIMEOUT_SECONDS", "75")
)

# This registry is deliberately finite. Every entry is selectable in the UI,
# has a canonical BCP-47 tag, and has an explicit output-language contract.
TARGET_LANGUAGE_PROFILES: dict[str, tuple[str, str]] = {
    "en": ("English", "Use natural, grammatically correct English."),
    "fr": ("French", "Use natural standard French."),
    "es": ("Spanish", "Use natural neutral standard Spanish."),
    "de": ("German", "Use natural standard German."),
    "pt-PT": ("European Portuguese", "Use European Portuguese vocabulary and grammar."),
    "pt-BR": ("Brazilian Portuguese", "Use Brazilian Portuguese vocabulary and grammar."),
    "ar": ("Arabic", "Use natural Modern Standard Arabic in Arabic script."),
    "zh-Hans": ("Simplified Chinese", "Use natural Simplified Chinese and simplified Han characters."),
    "zh-Hant": ("Traditional Chinese", "Use natural Traditional Chinese and traditional Han characters."),
    "ja": ("Japanese", "Use natural standard Japanese with appropriate kanji and kana."),
    "ko": ("Korean", "Use natural standard Korean in Hangul."),
    "hi": ("Hindi", "Use natural standard Hindi in Devanagari script."),
    "yo": (
        "Yoruba",
        "Use standard written Yoruba and preserve meaningful tone marks and "
        "underdotted letters such as ẹ, ọ, and ṣ.",
    ),
    "ha": (
        "Hausa",
        "Use standard written Hausa in the Latin Boko script, including Hausa "
        "letters such as ɓ, ɗ, and ƙ where linguistically required.",
    ),
    "ig": (
        "Igbo",
        "Use standard written Igbo with correct orthography and dotted vowels "
        "such as ị, ọ, and ụ where linguistically required.",
    ),
    "sw": ("Swahili", "Use natural standard Swahili."),
    "tr": ("Turkish", "Use natural standard Turkish with correct Turkish orthography."),
    "ru": ("Russian", "Use natural standard Russian in Cyrillic script."),
    "it": ("Italian", "Use natural standard Italian."),
    "nl": ("Dutch", "Use natural standard Dutch."),
}

_TARGET_LANGUAGE_ALIASES = {
    "english": "en",
    "french": "fr",
    "spanish": "es",
    "german": "de",
    "portuguese": "pt-PT",
    "pt": "pt-PT",
    "brazilian portuguese": "pt-BR",
    "arabic": "ar",
    "chinese": "zh-Hans",
    "simplified chinese": "zh-Hans",
    "zh-cn": "zh-Hans",
    "traditional chinese": "zh-Hant",
    "zh-tw": "zh-Hant",
    "japanese": "ja",
    "korean": "ko",
    "hindi": "hi",
    "yoruba": "yo",
    "hausa": "ha",
    "igbo": "ig",
    "swahili": "sw",
    "turkish": "tr",
    "russian": "ru",
    "italian": "it",
    "dutch": "nl",
}
_CANONICAL_TARGET_TAGS = {
    tag.casefold(): tag for tag in TARGET_LANGUAGE_PROFILES
}
_PROTECTED_TOKEN_RE = re.compile(
    r"https?://[^\s<>{}\[\]]+"
    r"|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"
    r"|(?<!\w)\+?\d(?:[\d \t().,:/%+-]*\d)?%?(?!\w)",
    re.UNICODE,
)
_TARGET_SCRIPT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "ar": ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)),
    "zh-Hans": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF)),
    "zh-Hant": ((0x3400, 0x4DBF), (0x4E00, 0x9FFF)),
    "ja": (
        (0x3040, 0x30FF),
        (0x31F0, 0x31FF),
        (0x3400, 0x4DBF),
        (0x4E00, 0x9FFF),
    ),
    "ko": ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF)),
    "hi": ((0x0900, 0x097F),),
    "ru": ((0x0400, 0x052F),),
}
_TARGET_SCRIPT_MIN_RATIO = {
    "ar": 0.35,
    "zh-Hans": 0.35,
    "zh-Hant": 0.35,
    "ja": 0.35,
    "ko": 0.35,
    "hi": 0.35,
    "ru": 0.35,
}
_SIMPLIFIED_CHINESE_MARKERS = frozenset(
    "国学体台门风书车云龙广东话语万与为这来时会开关电脑"
)
_TRADITIONAL_CHINESE_MARKERS = frozenset(
    "國學體臺門風書車雲龍廣東話語萬與為這來時會開關電腦"
)


# -------------------------
# Provider contract
# -------------------------

class TranslationBackend(Protocol):
    """
    Provider interface for translation runtime.

    Implementations may call an LLM, a local model, or any other processing backend.
    They must return only the translated text content.
    """

    def translate(
        self,
        *,
        prompt: str,
        source_text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        ...


class LLMTranslationBackend:
    def __init__(
        self,
        ai_client: AIClient | None = None,
        *,
        max_output_tokens: int = DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS,
    ) -> None:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be >= 1.")
        self.ai_client = ai_client or AIClient(
            AIClientConfig(
                max_output_tokens=max_output_tokens,
                request_timeout_seconds=DEFAULT_TRANSLATION_REQUEST_TIMEOUT_SECONDS,
                provider_timeout_seconds=DEFAULT_TRANSLATION_PROVIDER_TIMEOUT_SECONDS,
            )
        )

    def translate(
        self,
        *,
        prompt: str,
        source_text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        del source_text
        del source_language
        del target_language
        return self.ai_client.generate(prompt)


# -------------------------
# Service façade
# -------------------------

@dataclass(frozen=True)
class TranslateConfig:
    """
    Optional knobs for future provider-backed translation.

    algorithm_version is kept here for forward compatibility if you later want the
    processing layer to expose or log its own runtime version. analyzer.py remains
    the owner of response metadata construction.
    """

    algorithm_version: Optional[str] = None
    quality_review: bool = True
    repair_invalid_output: bool = True


class TranslateProcessor:
    """
    Stateless translation processor.

    Responsibilities:
    - validate local processing preconditions
    - build a contract-aligned translation prompt
    - delegate the actual text transformation to a backend
    - return translated text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    """

    def __init__(
        self,
        backend: Optional[TranslationBackend] = None,
        config: Optional[TranslateConfig] = None,
    ) -> None:
        self.backend = backend or LLMTranslationBackend()
        self.config = config or TranslateConfig()

    def translate(
        self,
        text: str,
        *,
        source_language: str = "auto",
        target_language: str,
    ) -> str:
        normalized = _normalize_text(text)
        normalized_source = _normalize_language_tag(source_language, allow_auto=True)
        normalized_target = _normalize_language_tag(target_language, allow_auto=False)

        prompt = build_translate_prompt(
            normalized,
            source_language=normalized_source,
            target_language=normalized_target,
        )
        output = self.backend.translate(
            prompt=prompt,
            source_text=normalized,
            source_language=normalized_source,
            target_language=normalized_target,
        )

        translated = _normalize_text(output)

        if self.config.quality_review:
            review_prompt = build_translate_review_prompt(
                normalized,
                candidate_translation=translated,
                source_language=normalized_source,
                target_language=normalized_target,
            )
            translated = _normalize_text(
                self.backend.translate(
                    prompt=review_prompt,
                    source_text=normalized,
                    source_language=normalized_source,
                    target_language=normalized_target,
                )
            )

        try:
            _validate_translation_result(
                normalized,
                translated,
                target_language=normalized_target,
            )
            return translated
        except ValueError as exc:
            if not self.config.repair_invalid_output:
                raise

            repair_prompt = build_translate_repair_prompt(
                normalized,
                candidate_translation=translated,
                source_language=normalized_source,
                target_language=normalized_target,
                validation_error=str(exc),
            )
            repaired = _normalize_text(
                self.backend.translate(
                    prompt=repair_prompt,
                    source_text=normalized,
                    source_language=normalized_source,
                    target_language=normalized_target,
                )
            )
            _validate_translation_result(
                normalized,
                repaired,
                target_language=normalized_target,
            )
            return repaired


# -------------------------
# Pure helpers
# -------------------------

def build_translate_prompt(
    text: str,
    *,
    source_language: str = "auto",
    target_language: str,
) -> str:
    """
    Build the contract-aligned prompt for translation only.

    This is intentionally feature-specific and does not import schema.py or
    validation.py because analyzer/extraction already enforce the upstream
    request contract before calling the processing layer.
    """
    normalized = _normalize_text(text)
    normalized_source = _normalize_language_tag(source_language, allow_auto=True)
    normalized_target = _normalize_language_tag(target_language, allow_auto=False)

    source_block = (
        "SOURCE LANGUAGE:\nauto\nRULE:\n"
        "- Detect the source language from the provided document content"
        if normalized_source == "auto"
        else build_untrusted_content_block(
            normalized_source,
            label="SOURCE LANGUAGE",
        )
    )
    target_block = build_untrusted_content_block(
        normalized_target,
        label="TARGET LANGUAGE",
    )
    target_profile = _target_language_profile(normalized_target)

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{TRANSLATE_RULES}\n\n"
        f"{source_block}\n\n"
        f"{target_block}\n\n"
        f"{target_profile}\n\n"
        + build_untrusted_content_block(normalized, label="DOCUMENT CONTENT")
    )


def build_translate_review_prompt(
    text: str,
    *,
    candidate_translation: str,
    source_language: str = "auto",
    target_language: str,
) -> str:
    """Build the mandatory bilingual quality-control pass."""
    normalized = _normalize_text(text)
    normalized_candidate = _normalize_text(candidate_translation)
    normalized_source = _normalize_language_tag(source_language, allow_auto=True)
    normalized_target = _normalize_language_tag(target_language, allow_auto=False)
    missing_tokens = _missing_protected_tokens(normalized, normalized_candidate)
    missing_token_rule = (
        "- Restore these protected source values exactly: "
        + ", ".join(missing_tokens)
        if missing_tokens
        else "- Reconfirm that all names, numbers, dates, emails, URLs, and identifiers are preserved."
    )

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        "TASK: TRANSLATION QUALITY ASSURANCE AND CORRECTION\n"
        "The candidate is an untrusted draft, not an authoritative answer.\n"
        "RULES:\n"
        "- Compare the source and candidate sentence by sentence.\n"
        "- Independently correct meaning, grammar, spelling, script, terminology, "
        "register, omissions, additions, contradictions, and untranslated ordinary text.\n"
        "- Ensure the entire final output is in the required target language, except "
        "for protected names and identifiers.\n"
        f"{missing_token_rule}\n"
        "- Return the complete corrected translation only; do not return an audit, "
        "score, explanation, preamble, or markdown fence.\n\n"
        f"SOURCE LANGUAGE: {normalized_source}\n\n"
        f"{_target_language_profile(normalized_target)}\n\n"
        f"{build_untrusted_content_block(normalized, label='SOURCE DOCUMENT')}\n\n"
        f"{build_untrusted_content_block(normalized_candidate, label='CANDIDATE TRANSLATION')}"
    )


def build_translate_repair_prompt(
    text: str,
    *,
    candidate_translation: str,
    source_language: str = "auto",
    target_language: str,
    validation_error: str,
) -> str:
    """Repair a reviewed translation that failed deterministic safeguards."""
    normalized_error = str(validation_error).strip() or "Invalid translation output."
    return (
        build_translate_review_prompt(
            text,
            candidate_translation=candidate_translation,
            source_language=source_language,
            target_language=target_language,
        )
        + "\n\nDETERMINISTIC VALIDATION FAILURE:\n"
        + build_untrusted_content_block(
            normalized_error,
            label="VALIDATION ERROR",
        )
        + "\n\nCorrect the failure and return only the complete translation."
    )


def translate_text(
    text: str,
    *,
    source_language: str = "auto",
    target_language: str,
    backend: Optional[TranslationBackend] = None,
    config: Optional[TranslateConfig] = None,
) -> str:
    """
    Functional convenience wrapper for analyzer integration.

    Example future analyzer change:
        from src.processing.llm_processing.translate import translate_text
        ...
        inline_builder=lambda text: translate_text(
            text,
            source_language=req.payload.source_language,
            target_language=req.payload.target_language,
        )
    """
    processor = TranslateProcessor(backend=backend, config=config)
    return processor.translate(
        text,
        source_language=source_language,
        target_language=target_language,
    )


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


def _normalize_language_tag(value: str, *, allow_auto: bool) -> str:
    if not isinstance(value, str):
        raise TypeError("language value must be a string.")

    raw = value.strip()
    if allow_auto and raw.casefold() == "auto":
        return "auto"

    candidate = _TARGET_LANGUAGE_ALIASES.get(
        raw.replace("_", "-").casefold(),
        raw,
    )
    normalized = validate_language_identifier(
        candidate,
        field_name="Language value",
        allow_auto=allow_auto,
    )

    if allow_auto and normalized.lower() == "auto":
        return "auto"

    if allow_auto:
        return normalized

    canonical = _CANONICAL_TARGET_TAGS.get(
        normalized.replace("_", "-").casefold()
    )
    if canonical is None:
        supported = ", ".join(TARGET_LANGUAGE_PROFILES)
        raise ValueError(
            f"Unsupported target language '{value}'. Supported target tags: {supported}."
        )
    return canonical


def _target_language_profile(target_language: str) -> str:
    name, orthography = TARGET_LANGUAGE_PROFILES[target_language]
    return (
        "TARGET LANGUAGE CONTRACT:\n"
        f"- Canonical tag: {target_language}\n"
        f"- Required language: {name}\n"
        f"- Orthography: {orthography}\n"
        "- Do not drift into a related language, dialect, or writing system."
    )


def _protected_tokens(value: str) -> Counter[str]:
    tokens = []
    for match in _PROTECTED_TOKEN_RE.finditer(value):
        token = match.group(0).rstrip(".,;:!?)]}")
        if token:
            tokens.append(token)
    return Counter(tokens)


def _missing_protected_tokens(source_text: str, translated_text: str) -> list[str]:
    required = _protected_tokens(source_text)
    present = _protected_tokens(translated_text)
    missing: list[str] = []
    for token, required_count in required.items():
        if present[token] < required_count:
            missing.extend([token] * (required_count - present[token]))
    return missing


def _validate_translation_result(
    source_text: str,
    translated_text: str,
    *,
    target_language: str,
) -> None:
    missing = _missing_protected_tokens(source_text, translated_text)
    if missing:
        preview = ", ".join(missing[:5])
        suffix = "" if len(missing) <= 5 else ", …"
        raise ValueError(
            "Translation failed protected-value verification; no unverified "
            f"translation was returned. Missing: {preview}{suffix}"
        )

    ranges = _TARGET_SCRIPT_RANGES.get(target_language)
    unprotected_output = _PROTECTED_TOKEN_RE.sub(" ", translated_text)
    unprotected_source = _PROTECTED_TOKEN_RE.sub(" ", source_text)
    alphabetic = [char for char in unprotected_output if char.isalpha()]
    source_alphabetic = [char for char in unprotected_source if char.isalpha()]
    if ranges and len(source_alphabetic) >= 12 and len(alphabetic) >= 4:
        target_script_count = sum(
            any(start <= ord(char) <= end for start, end in ranges)
            for char in alphabetic
        )
        target_script_ratio = target_script_count / len(alphabetic)
        if target_script_ratio < _TARGET_SCRIPT_MIN_RATIO[target_language]:
            raise ValueError(
                "Translation failed target-script verification; no unverified "
                f"{target_language} translation was returned."
            )

    if target_language in {"zh-Hans", "zh-Hant"}:
        simplified_count = sum(
            char in _SIMPLIFIED_CHINESE_MARKERS for char in unprotected_output
        )
        traditional_count = sum(
            char in _TRADITIONAL_CHINESE_MARKERS for char in unprotected_output
        )
        if (
            target_language == "zh-Hans"
            and traditional_count >= 2
            and traditional_count > simplified_count
        ):
            raise ValueError(
                "Translation failed Simplified-Chinese orthography verification; "
                "no unverified translation was returned."
            )
        if (
            target_language == "zh-Hant"
            and simplified_count >= 2
            and simplified_count > traditional_count
        ):
            raise ValueError(
                "Translation failed Traditional-Chinese orthography verification; "
                "no unverified translation was returned."
            )


__all__ = [
    "BASE_CONSTRAINTS",
    "TRANSLATE_RULES",
    "DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS",
    "DEFAULT_TRANSLATION_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_TRANSLATION_PROVIDER_TIMEOUT_SECONDS",
    "TARGET_LANGUAGE_PROFILES",
    "TranslationBackend",
    "LLMTranslationBackend",
    "TranslateConfig",
    "TranslateProcessor",
    "build_translate_prompt",
    "build_translate_review_prompt",
    "build_translate_repair_prompt",
    "translate_text",
]
