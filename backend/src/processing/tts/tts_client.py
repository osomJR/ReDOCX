from __future__ import annotations

"""Shared Deepgram client for ReDOCX Text-to-Speech.

Responsibilities:
- accept validated text plus a resolved Deepgram voice model
- call the appropriate Deepgram batch TTS endpoint
- stream provider PCM output to a caller-supplied file path
- enforce provider limits, timeouts, and response-integrity safeguards
- remain free of ReDOCX schema/response construction concerns

Non-responsibilities:
- document extraction or inline-text security validation
- ReDOCX request/response model construction
- final output encoding/container selection
- artifact persistence
- authenticated owner resolution
"""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

import requests
from fastapi import HTTPException


DEFAULT_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "flux-miles-en")
DEFAULT_V1_BASE_URL = os.getenv(
    "DEEPGRAM_TTS_V1_BASE_URL",
    "https://api.deepgram.com/v1/speak",
)
DEFAULT_V2_BASE_URL = os.getenv(
    "DEEPGRAM_TTS_V2_BASE_URL",
    "https://api.deepgram.com/v2/speak",
)
DEFAULT_CONNECT_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("DEEPGRAM_TTS_CONNECT_TIMEOUT_SECONDS", "10")),
)
DEFAULT_READ_TIMEOUT_SECONDS = max(
    1.0,
    float(os.getenv("DEEPGRAM_TTS_READ_TIMEOUT_SECONDS", "180")),
)
DEFAULT_SAMPLE_RATE = int(os.getenv("DEEPGRAM_TTS_SAMPLE_RATE", "24000"))
DEFAULT_MAX_INPUT_CHARACTERS = max(
    1,
    int(os.getenv("DEEPGRAM_TTS_MAX_INPUT_CHARACTERS", "2000")),
)
DEFAULT_CHUNK_BYTES = max(
    4096,
    int(os.getenv("DEEPGRAM_TTS_RESPONSE_CHUNK_BYTES", str(64 * 1024))),
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TTSClientConfig:
    """Provider-only configuration for Deepgram batch speech synthesis."""

    api_key: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    default_model: str = DEFAULT_MODEL
    v1_base_url: str = DEFAULT_V1_BASE_URL
    v2_base_url: str = DEFAULT_V2_BASE_URL
    connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS
    read_timeout_seconds: float = DEFAULT_READ_TIMEOUT_SECONDS
    sample_rate: int = DEFAULT_SAMPLE_RATE
    max_input_characters: int = DEFAULT_MAX_INPUT_CHARACTERS
    response_chunk_bytes: int = DEFAULT_CHUNK_BYTES
    mip_opt_out: bool = _env_bool("DEEPGRAM_TTS_MIP_OPT_OUT", False)
    tag: Optional[str] = os.getenv("DEEPGRAM_TTS_TAG") or None


@dataclass(frozen=True)
class TTSSynthesis:
    """Provider telemetry for one successful Deepgram synthesis request."""

    file_path: str
    model: str
    request_id: Optional[str]
    content_type: str
    characters_submitted: int
    provider_character_count: Optional[int]
    provider_speed_used: Optional[float]
    bytes_written: int


class TTSClient:
    """Low-level Deepgram TTS REST client.

    The processing layer deliberately asks Deepgram for raw mono Linear16 PCM.
    ReDOCX then concatenates long-document chunks losslessly and performs exactly
    one final encoding step for the schema-requested output format.
    """

    def __init__(self, config: Optional[TTSClientConfig] = None) -> None:
        self.config = config or TTSClientConfig()
        if not self.config.api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is not configured. Set it in the environment "
                "before using Text-to-Speech."
            )
        if self.config.sample_rate <= 0:
            raise ValueError("Deepgram TTS sample_rate must be > 0.")
        if self.config.max_input_characters < 1:
            raise ValueError("Deepgram TTS max_input_characters must be >= 1.")

    @property
    def max_input_characters(self) -> int:
        return int(self.config.max_input_characters)

    @property
    def sample_rate(self) -> int:
        return int(self.config.sample_rate)

    def synthesize_to_file(
        self,
        *,
        text: str,
        output_path: str,
        model: Optional[str] = None,
        speed: Optional[float] = None,
        append: bool = False,
    ) -> TTSSynthesis:
        """Synthesize one provider-sized text chunk into raw Linear16 PCM."""
        normalized_text = self._normalize_text(text)
        resolved_model = self._normalize_model(model or self.config.default_model)
        resolved_speed = self._normalize_speed(resolved_model, speed)
        destination = self._normalize_output_path(output_path)

        query_params: dict[str, str] = {
            "model": resolved_model,
            "encoding": "linear16",
            "container": "none",
            "sample_rate": str(self.config.sample_rate),
        }
        if resolved_speed is not None:
            query_params["speed"] = self._format_speed(resolved_speed)
        if self.config.mip_opt_out:
            query_params["mip_opt_out"] = "true"
        if self.config.tag:
            query_params["tag"] = str(self.config.tag)

        endpoint = self._endpoint_for_model(resolved_model)
        headers = {
            "Authorization": f"Token {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/*",
        }

        try:
            response = requests.post(
                endpoint,
                params=query_params,
                json={"text": normalized_text},
                headers=headers,
                stream=True,
                timeout=(
                    self.config.connect_timeout_seconds,
                    self.config.read_timeout_seconds,
                ),
            )
        except requests.Timeout as exc:
            raise HTTPException(
                status_code=504,
                detail={
                    "error": "tts_timeout",
                    "message": "Text-to-Speech provider did not respond in time.",
                },
            ) from exc
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "tts_provider_unavailable",
                    "message": "The Text-to-Speech provider could not be reached.",
                },
            ) from exc

        try:
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "tts_provider_http_error",
                        "provider_status": response.status_code,
                        "provider_response": self._safe_json_or_text(response),
                    },
                )

            content_type = (response.headers.get("Content-Type") or "").strip()
            if not content_type.lower().startswith("audio/"):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error": "tts_provider_invalid_response",
                        "message": "Text-to-Speech provider returned a non-audio response.",
                    },
                )

            mode = "ab" if append else "wb"
            bytes_written = 0
            with destination.open(mode) as handle:
                for chunk in response.iter_content(chunk_size=self.config.response_chunk_bytes):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    bytes_written += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            try:
                destination.chmod(0o600)
            except OSError:
                pass

            if bytes_written <= 0:
                raise HTTPException(
                    status_code=502,
                    detail="Text-to-Speech provider returned empty audio output.",
                )

            return TTSSynthesis(
                file_path=str(destination),
                model=resolved_model,
                request_id=(response.headers.get("dg-request-id") or None),
                content_type=content_type,
                characters_submitted=len(normalized_text),
                provider_character_count=self._safe_int(
                    response.headers.get("dg-char-count")
                ),
                provider_speed_used=self._safe_float(
                    response.headers.get("dg-speed-used")
                ),
                bytes_written=bytes_written,
            )
        except HTTPException:
            raise
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "tts_provider_stream_error",
                    "message": "Text-to-Speech audio streaming failed.",
                },
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                "Text-to-Speech provider output could not be written to local storage."
            ) from exc
        finally:
            response.close()

    def _normalize_text(self, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("TTS text must be a string.")
        if not value:
            raise ValueError("TTS text cannot be empty.")
        if len(value) > self.config.max_input_characters:
            raise ValueError(
                "TTS provider chunk exceeds the configured maximum of "
                f"{self.config.max_input_characters:,} characters."
            )
        return value

    @staticmethod
    def _normalize_model(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("TTS model must be a string.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("TTS model cannot be empty.")
        if any(ch.isspace() for ch in normalized):
            raise ValueError("TTS model must not contain whitespace.")
        return normalized

    @staticmethod
    def _normalize_output_path(value: str) -> Path:
        if not isinstance(value, str):
            raise TypeError("output_path must be a string.")
        normalized = value.strip()
        if not normalized:
            raise ValueError("output_path cannot be empty.")
        destination = Path(normalized).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.is_dir():
            raise ValueError("output_path must reference a file, not a directory.")
        return destination

    @staticmethod
    def _normalize_speed(model: str, speed: Optional[float]) -> Optional[float]:
        if speed is None:
            return None
        resolved = float(speed)
        if model.startswith("flux-"):
            if not 0.5 <= resolved <= 1.5:
                raise ValueError("Flux TTS speed must be between 0.5 and 1.5.")
            increments = round(resolved / 0.05)
            quantized = round(increments * 0.05, 2)
            if abs(quantized - resolved) > 1e-9:
                raise ValueError("Flux TTS speed must use 0.05 increments.")
            return quantized
        if model.startswith("aura-"):
            if not 0.7 <= resolved <= 1.5:
                raise ValueError("Aura TTS speed must be between 0.7 and 1.5.")
            return resolved
        if not 0.5 <= resolved <= 1.5:
            raise ValueError("TTS provider speed must be between 0.5 and 1.5.")
        return resolved

    def _endpoint_for_model(self, model: str) -> str:
        endpoint = self.config.v2_base_url if model.startswith("flux-") else self.config.v1_base_url
        normalized = endpoint.strip().rstrip("/")
        if not normalized.startswith(("https://", "http://")):
            raise ValueError("Deepgram TTS base URL must use http:// or https://.")
        return normalized

    @staticmethod
    def _format_speed(value: float) -> str:
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @staticmethod
    def _safe_int(value: Optional[str]) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_json_or_text(response: requests.Response) -> object:
        try:
            return response.json()
        except ValueError:
            text = (response.text or "").strip()
            return text[:4096] if text else "Provider returned an empty error response."


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_V1_BASE_URL",
    "DEFAULT_V2_BASE_URL",
    "TTSClientConfig",
    "TTSSynthesis",
    "TTSClient",
]
