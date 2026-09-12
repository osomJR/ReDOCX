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


DEFAULT_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")
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


SUPPORTED_TTS_LANGUAGES = frozenset({"nl", "en", "fr", "de", "it", "ja", "es"})

# Deepgram Aura-2 voice catalog. Keep this provider registry server-side so API
# callers cannot submit arbitrary model identifiers or pair a voice with the
# wrong synthesis language.
AURA2_VOICES_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "nl": (
        "aura-2-beatrix-nl",
        "aura-2-daphne-nl",
        "aura-2-cornelia-nl",
        "aura-2-sander-nl",
        "aura-2-hestia-nl",
        "aura-2-lars-nl",
        "aura-2-roman-nl",
        "aura-2-rhea-nl",
        "aura-2-leda-nl",
    ),
    "en": (
        "aura-2-amalthea-en",
        "aura-2-andromeda-en",
        "aura-2-apollo-en",
        "aura-2-arcas-en",
        "aura-2-aries-en",
        "aura-2-asteria-en",
        "aura-2-athena-en",
        "aura-2-atlas-en",
        "aura-2-aurora-en",
        "aura-2-callista-en",
        "aura-2-cora-en",
        "aura-2-cordelia-en",
        "aura-2-delia-en",
        "aura-2-draco-en",
        "aura-2-electra-en",
        "aura-2-harmonia-en",
        "aura-2-helena-en",
        "aura-2-hera-en",
        "aura-2-hermes-en",
        "aura-2-hyperion-en",
        "aura-2-iris-en",
        "aura-2-janus-en",
        "aura-2-juno-en",
        "aura-2-jupiter-en",
        "aura-2-luna-en",
        "aura-2-mars-en",
        "aura-2-minerva-en",
        "aura-2-neptune-en",
        "aura-2-odysseus-en",
        "aura-2-ophelia-en",
        "aura-2-orion-en",
        "aura-2-orpheus-en",
        "aura-2-pandora-en",
        "aura-2-phoebe-en",
        "aura-2-pluto-en",
        "aura-2-saturn-en",
        "aura-2-selene-en",
        "aura-2-thalia-en",
        "aura-2-theia-en",
        "aura-2-vesta-en",
        "aura-2-zeus-en",
    ),
    "fr": (
        "aura-2-agathe-fr",
        "aura-2-hector-fr",
    ),
    "de": (
        "aura-2-elara-de",
        "aura-2-aurelia-de",
        "aura-2-lara-de",
        "aura-2-julius-de",
        "aura-2-fabian-de",
        "aura-2-kara-de",
        "aura-2-viktoria-de",
    ),
    "it": (
        "aura-2-melia-it",
        "aura-2-elio-it",
        "aura-2-flavio-it",
        "aura-2-maia-it",
        "aura-2-cinzia-it",
        "aura-2-cesare-it",
        "aura-2-livia-it",
        "aura-2-dionisio-it",
        "aura-2-demetra-it",
    ),
    "ja": (
        "aura-2-uzume-ja",
        "aura-2-ebisu-ja",
        "aura-2-fujin-ja",
        "aura-2-izanami-ja",
        "aura-2-ama-ja",
    ),
    "es": (
        "aura-2-sirio-es",
        "aura-2-nestor-es",
        "aura-2-carina-es",
        "aura-2-celeste-es",
        "aura-2-alvaro-es",
        "aura-2-diana-es",
        "aura-2-aquila-es",
        "aura-2-selena-es",
        "aura-2-estrella-es",
        "aura-2-javier-es",
        "aura-2-agustina-es",
        "aura-2-antonia-es",
        "aura-2-gloria-es",
        "aura-2-luciano-es",
        "aura-2-olivia-es",
        "aura-2-silvia-es",
        "aura-2-valerio-es",
    ),
}

DEFAULT_AURA2_MODEL_BY_LANGUAGE: dict[str, str] = {
    "nl": "aura-2-rhea-nl",
    "en": "aura-2-thalia-en",
    "fr": "aura-2-agathe-fr",
    "de": "aura-2-julius-de",
    "it": "aura-2-livia-it",
    "ja": "aura-2-izanami-ja",
    "es": "aura-2-celeste-es",
}

AURA2_MODEL_TO_LANGUAGE = {
    model: language
    for language, models in AURA2_VOICES_BY_LANGUAGE.items()
    for model in models
}


def normalize_tts_language(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-").split("-", 1)[0]
    if normalized not in SUPPORTED_TTS_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_TTS_LANGUAGES))
        raise ValueError(
            f"Unsupported Text-to-Speech synthesis language '{value}'. Supported: {supported}."
        )
    return normalized


def default_aura2_model_for_language(language: str) -> str:
    return DEFAULT_AURA2_MODEL_BY_LANGUAGE[normalize_tts_language(language)]


def model_language(model: str) -> Optional[str]:
    normalized = str(model or "").strip()
    if normalized in AURA2_MODEL_TO_LANGUAGE:
        return AURA2_MODEL_TO_LANGUAGE[normalized]
    if normalized.startswith(("aura-", "flux-")):
        candidate = normalized.rsplit("-", 1)[-1].lower()
        if candidate in SUPPORTED_TTS_LANGUAGES:
            return candidate
    return None


def validate_deepgram_tts_model(
    model: str,
    *,
    language: str,
    allow_configured_legacy: bool = False,
) -> str:
    normalized_model = str(model or "").strip()
    normalized_language = normalize_tts_language(language)
    if not normalized_model:
        raise ValueError("TTS model cannot be empty.")

    if normalized_model.startswith("aura-2-"):
        registered_language = AURA2_MODEL_TO_LANGUAGE.get(normalized_model)
        if registered_language is None:
            raise ValueError(
                f"Unsupported Deepgram Aura-2 voice model '{normalized_model}'."
            )
        if registered_language != normalized_language:
            raise ValueError(
                f"Voice '{normalized_model}' is not valid for synthesis language "
                f"'{normalized_language}'."
            )
        return normalized_model

    # Preserve deployment-level custom/legacy models only when the caller has
    # resolved them from trusted server configuration. Public voice identifiers
    # must come from the explicit Aura-2 catalog above.
    if allow_configured_legacy and normalized_model.startswith(("flux-", "aura-")):
        registered_language = model_language(normalized_model)
        if registered_language != normalized_language:
            raise ValueError(
                f"Voice '{normalized_model}' is not valid for synthesis language "
                f"'{normalized_language}'."
            )
        return normalized_model

    raise ValueError(
        "Text-to-Speech voice must be a registered Deepgram Aura-2 model or an "
        "existing configured Deepgram aura-/flux- model."
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
                        "message": "The Text-to-Speech provider rejected the synthesis request.",
                        "provider_status": response.status_code,
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
            language = model_language(model)
            if model.startswith("aura-2-") and language not in {"en", "es"}:
                raise ValueError(
                    "Deepgram Aura-2 native speed control is currently supported only "
                    "for English and Spanish voices."
                )
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
    "AURA2_MODEL_TO_LANGUAGE",
    "AURA2_VOICES_BY_LANGUAGE",
    "DEFAULT_AURA2_MODEL_BY_LANGUAGE",
    "DEFAULT_MODEL",
    "DEFAULT_V1_BASE_URL",
    "DEFAULT_V2_BASE_URL",
    "SUPPORTED_TTS_LANGUAGES",
    "TTSClientConfig",
    "TTSSynthesis",
    "TTSClient",
    "default_aura2_model_for_language",
    "model_language",
    "normalize_tts_language",
    "validate_deepgram_tts_model",
]
