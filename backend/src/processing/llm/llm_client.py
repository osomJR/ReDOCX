from __future__ import annotations

"""
Shared LLM client for AI document actions.

Responsibilities:
- accept a fully prepared prompt from the processing layer
- call the LLM provider
- return raw generated text only
- enforce timeout / empty-response safeguards
- remain free of feature-specific business rules

Non-responsibilities:
- prompt construction
- schema validation
- response envelope construction
- file generation or storage
"""

import concurrent.futures
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException
from openai import OpenAI

DEFAULT_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "1200"))
DEFAULT_REQUEST_TIMEOUT_SECONDS = float(os.getenv("AI_TIMEOUT_SECONDS", "45"))
DEFAULT_PROVIDER_TIMEOUT_SECONDS = float(os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", "30"))
DEFAULT_SYSTEM_PROMPT = os.getenv(
    "AI_SYSTEM_PROMPT",
    "You are a strict document processing AI.",
)


@dataclass(frozen=True)
class AIClientConfig:
    """
    Low-level provider configuration.

    Notes:
    - api_key defaults to OPENAI_API_KEY
    - base_url is optional and allows use of OpenAI-compatible providers
    - model defaults to AI_MODEL or gpt-4o-mini
    """

    api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    base_url: Optional[str] = os.getenv("OPENAI_BASE_URL")
    model: str = DEFAULT_MODEL
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider_timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    system_prompt: str = DEFAULT_SYSTEM_PROMPT


class AIClient:
    """
    Shared low-level LLM execution layer.

    Public contract expected by processing modules:
        AIClient().generate(prompt: str) -> str
    """

    def __init__(self, config: Optional[AIClientConfig] = None) -> None:
        self.config = config or AIClientConfig()

        if not self.config.api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not configured. Set it in the environment before using AIClient."
            )

        client_kwargs = {
            "api_key": self.config.api_key,
            "timeout": self.config.provider_timeout_seconds,
        }
        if self.config.base_url:
            client_kwargs["base_url"] = self.config.base_url

        self._client = OpenAI(**client_kwargs)

    def generate(self, prompt: str) -> str:
        """
        Execute a single prompt and return raw generated text.

        The processing modules already construct the full prompt, so this method
        must not add feature-specific rules.
        """
        normalized_prompt = self._normalize_prompt(prompt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._call_provider, normalized_prompt)
            try:
                result = future.result(timeout=self.config.request_timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "ai_timeout",
                        "message": "LLM provider did not respond in time.",
                    },
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"LLM provider error: {exc}",
                ) from exc

        if not result or not result.strip():
            raise HTTPException(
                status_code=502,
                detail="LLM provider returned empty output.",
            )

        return result.strip()

    def _call_provider(self, prompt: str) -> str:
        """
        Isolated provider call.

        Uses the OpenAI Responses API and returns only output text.
        """
        response = self._client.responses.create(
            model=self.config.model,
            input=[
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": self.config.system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ],
            max_output_tokens=self.config.max_output_tokens,
        )

        output_text = getattr(response, "output_text", None)
        if output_text and output_text.strip():
            return output_text.strip()

        try:
            outputs = getattr(response, "output", []) or []
            chunks: list[str] = []

            for item in outputs:
                content_items = getattr(item, "content", []) or []
                for content in content_items:
                    text_value = getattr(content, "text", None)
                    if text_value:
                        chunks.append(str(text_value))

            combined = "\n".join(
                part.strip() for part in chunks if part and str(part).strip()
            ).strip()
            if combined:
                return combined
        except Exception:
            pass

        raise HTTPException(
            status_code=502,
            detail="LLM provider returned null or unreadable content.",
        )

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string.")
        normalized = prompt.strip()
        if not normalized:
            raise HTTPException(
                status_code=500,
                detail="Empty prompt passed to AI client.",
            )
        return normalized


__all__ = [
    "AIClientConfig",
    "AIClient",
]
