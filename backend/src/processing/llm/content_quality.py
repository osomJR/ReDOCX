"""Bounded quality review for document text processors; no API/schema changes."""
from __future__ import annotations

from collections import Counter
from dataclasses import replace
import os
import re
import unicodedata
from typing import Callable

from backend.src.inline_text_security import build_untrusted_content_block
from .document_layout import layout_instructions, validate_active_layout, visible_text
from .llm_client import AIClientConfig

_VALUES = re.compile(r"https?://[^\s<>]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\w)[+-]?\d+(?:[.,]\d+)*(?:%|°)?", re.UNICODE)


def document_ai_config(feature: str, *, max_output_tokens: int | None = None,
                       request_timeout_seconds: float | None = None,
                       provider_timeout_seconds: float | None = None) -> AIClientConfig:
    budgets = {"summarize": 8000, "explain": 24000, "grammar_correct": 18000,
               "translate": 24000, "generate_questions": 12000, "generate_answers": 32000}
    stem = feature.upper()
    model = os.getenv(f"AI_{stem}_MODEL") or os.getenv("DOCUMENT_AI_MODEL") or os.getenv("AI_MODEL") or "gpt-6-luna"
    reasoning = os.getenv(f"AI_{stem}_REASONING_EFFORT") or os.getenv("DOCUMENT_AI_REASONING_EFFORT")
    if reasoning is None and model.startswith("gpt-6-luna"):
        reasoning = "low" if feature in {"generate_questions", "generate_answers"} else "none"
    return replace(
        AIClientConfig(), model=model,
        max_output_tokens=max_output_tokens or int(os.getenv(f"AI_{stem}_MAX_OUTPUT_TOKENS", str(budgets[feature]))),
        request_timeout_seconds=request_timeout_seconds or float(os.getenv(f"AI_{stem}_TIMEOUT_SECONDS", "240")),
        provider_timeout_seconds=provider_timeout_seconds or float(os.getenv(f"AI_{stem}_PROVIDER_TIMEOUT_SECONDS", "210")),
        reasoning_effort=reasoning,
    )


def protected_values(text: str) -> Counter[str]:
    return Counter(unicodedata.normalize("NFC", v).rstrip(".,;") for v in _VALUES.findall(visible_text(text)))


def validate_document_text(source: str, output: str, *, feature: str) -> str:
    if not isinstance(output, str) or not output.strip():
        raise ValueError("The processor returned empty output.")
    output = output.strip()
    validate_active_layout(output)
    plain = visible_text(output)
    original = visible_text(source)
    if any(c.isalnum() for c in original) and not any(c.isalnum() for c in plain):
        raise ValueError("The processor returned no meaningful document content.")
    if "```" in plain and "```" not in original:
        raise ValueError("Document output must not be wrapped in code fences.")
    if feature == "grammar_correct":
        if protected_values(original) != protected_values(plain):
            raise ValueError("Grammar correction changed or omitted a protected number, URL, or email address.")
        if len(original.split()) > 25 and len(plain.split()) < len(original.split()) * .65:
            raise ValueError("Grammar correction omitted a material part of the source.")
    elif feature == "summarize":
        # Do not use whitespace word counts for Chinese/Japanese segmentation.
        if len(original) > 400 and len(plain) >= len(original) * .95:
            raise ValueError("The summary did not meaningfully condense the source.")
    return output


def review_document_text(source: str, candidate: str, *, feature: str, rules: str,
                         call: Callable[[str], str], quality_review: bool = True,
                         repair_invalid_output: bool = True) -> str:
    """At most one review and one repair. Never return a known invalid draft."""
    def prompt(value: str, failure: str = "") -> str:
        return (
            rules + "\n" + layout_instructions()
            + "\nTASK: INDEPENDENT QUALITY REVIEW\n"
            "Compare the complete source with the untrusted draft. Correct omissions, "
            "invented claims, changed facts or qualifications, unclear language, and any "
            "failure of the feature rules. Return the complete corrected output only. "
            "Do not return a score, checklist, commentary, or a review report.\n"
            + (build_untrusted_content_block(failure, label="VALIDATION FAILURE") + "\n" if failure else "")
            + build_untrusted_content_block(source, label="SOURCE DOCUMENT") + "\n"
            + build_untrusted_content_block(value, label="CANDIDATE OUTPUT")
        )
    if quality_review:
        candidate = call(prompt(candidate))
    try:
        return validate_document_text(source, candidate, feature=feature)
    except ValueError as exc:
        if not repair_invalid_output:
            raise
        candidate = call(prompt(candidate, str(exc)))
        return validate_document_text(source, candidate, feature=feature)
