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
from .llm_client import AIClient
from dataclasses import dataclass
from typing import Optional, Protocol


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
- Preserve voice, tone, and structure
- Maintain paragraph-to-paragraph alignment
- No localization or cultural adaptation
- Output must mirror original structure
""".strip()


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
    def __init__(self, ai_client: AIClient | None = None) -> None:
        self.ai_client = ai_client or AIClient()

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
        return translated


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
        "SOURCE LANGUAGE:\n"
        "auto\n"
        "RULE:\n"
        "- Detect the source language from the provided document content"
        if normalized_source == "auto"
        else f"SOURCE LANGUAGE:\n{normalized_source}"
    )

    return (
        f"{BASE_CONSTRAINTS}\n\n"
        f"{TRANSLATE_RULES}\n\n"
        f"{source_block}\n\n"
        f"TARGET LANGUAGE:\n{normalized_target}\n\n"
        f"DOCUMENT CONTENT:\n{normalized}"
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

    normalized = value.strip()
    if not normalized:
        raise ValueError("language value cannot be empty.")

    lowered = normalized.lower()
    if allow_auto and lowered == "auto":
        return "auto"

    return normalized


__all__ = [
    "BASE_CONSTRAINTS",
    "TRANSLATE_RULES",
    "TranslationBackend",
    "LLMTranslationBackend",
    "TranslateConfig",
    "TranslateProcessor",
    "build_translate_prompt",
    "translate_text",
]
