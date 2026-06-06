from __future__ import annotations

"""
Shared ASR client for transcription actions.

Responsibilities:
- accept a prepared local media file path from the processing layer
- call the ASR provider
- return transcript text only
- enforce timeout / empty-response safeguards
- remain free of feature-specific business rules

Non-responsibilities:
- media extraction from video
- schema validation
- response envelope construction
- file generation or storage
"""

import concurrent.futures
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from fastapi import HTTPException


DEFAULT_MODEL = os.getenv("ASR_MODEL", "nova-3")
DEFAULT_REQUEST_TIMEOUT_SECONDS = float(os.getenv("ASR_TIMEOUT_SECONDS", "180"))
DEFAULT_PROVIDER_TIMEOUT_SECONDS = float(os.getenv("ASR_PROVIDER_TIMEOUT_SECONDS", "120"))
DEFAULT_BASE_URL = os.getenv("DEEPGRAM_BASE_URL", "https://api.deepgram.com/v1/listen")


@dataclass(frozen=True)
class ASRClientConfig:
    """
    Low-level provider configuration for ASR execution.

    Notes:
    - api_key defaults to DEEPGRAM_API_KEY
    - base_url defaults to Deepgram prerecorded /listen endpoint
    - model defaults to ASR_MODEL or nova-3
    - language_mode defaults to multilingual so single-language and mixed-language
      uploads are handled without requiring a UI toggle.
    """

    api_key: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider_timeout_seconds: float = DEFAULT_PROVIDER_TIMEOUT_SECONDS
    language_mode: str = os.getenv("ASR_LANGUAGE_MODE", "multilingual")


class ASRClient:
    """
    Shared low-level ASR execution layer.

    Public contract expected by transcribe.py:
        ASRClient().transcribe(
            file_path: str,
            media_format: str,
            preserve_filler_words: bool,
            diarize_speakers: bool,
        ) -> str
    """

    def __init__(self, config: Optional[ASRClientConfig] = None) -> None:
        self.config = config or ASRClientConfig()

        if not self.config.api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is not configured. Set it in the environment before using ASRClient."
            )

    def transcribe(
        self,
        *,
        file_path: str,
        media_format: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        normalized_file_path = self._normalize_file_path(file_path)
        normalized_media_format = self._normalize_media_format(media_format)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self._call_provider,
                normalized_file_path,
                normalized_media_format,
                preserve_filler_words,
                diarize_speakers,
            )
            try:
                result = future.result(timeout=self.config.request_timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail={
                        "error": "asr_timeout",
                        "message": "ASR provider did not respond in time.",
                    },
                ) from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"ASR provider error: {exc}",
                ) from exc

        if not result or not result.strip():
            raise HTTPException(
                status_code=502,
                detail="ASR provider returned empty output.",
            )

        return result.strip()

    def _call_provider(
        self,
        file_path: str,
        media_format: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        query_params = {
            "model": self.config.model,
            "smart_format": "true",
            "punctuate": "true",
            "paragraphs": "true",
            "filler_words": "true" if preserve_filler_words else "false",
            "diarize": "true" if diarize_speakers else "false",
        }

        language_mode = self._normalize_language_mode(self.config.language_mode)

        if language_mode == "multilingual":
            query_params["language"] = "multi"
        else:
            query_params["detect_language"] = "true"

        if diarize_speakers:
            query_params["utterances"] = "true"

        url = f"{self.config.base_url}?{urlencode(query_params)}"
        headers = {
            "Authorization": f"Token {self.config.api_key}",
            "Content-Type": self._content_type_for(media_format, file_path),
        }

        with open(file_path, "rb") as media_file:
            response = requests.post(
                url,
                headers=headers,
                data=media_file,
                timeout=self.config.provider_timeout_seconds,
            )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "asr_provider_http_error",
                    "provider_status": response.status_code,
                    "provider_response": self._safe_json_or_text(response),
                },
            )

        payload = response.json()

        results = payload.get("results") or {}
        utterances = results.get("utterances") or []
        channels = results.get("channels") or []
        alt_transcript = ""

        if channels:
            alternatives = channels[0].get("alternatives") or []
            if alternatives:
                alt_transcript = str(alternatives[0].get("transcript") or "").strip()
                
        transcript = self._extract_transcript(payload, diarize_speakers=diarize_speakers)

        if len(transcript.strip()) < 80:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "insufficient_speech_detected",
                    "message": "The uploaded media appears to contain little or no intelligible spoken content.",
                },
            )
        return transcript

    def _extract_transcript(self, payload: dict[str, Any], *, diarize_speakers: bool) -> str:
        results = payload.get("results") or {}

        diarized_text = ""
        if diarize_speakers:
            utterances = results.get("utterances") or []
            speaker_blocks: list[str] = []

            current_speaker_label: str | None = None
            current_parts: list[str] = []

            def flush_current_block() -> None:
                nonlocal current_speaker_label, current_parts

                text = " ".join(part.strip() for part in current_parts if part.strip()).strip()
                if not text:
                    current_speaker_label = None
                    current_parts = []
                    return

                if current_speaker_label:
                    speaker_blocks.append(f"{current_speaker_label}: {text}")
                else:
                    speaker_blocks.append(text)

                current_speaker_label = None
                current_parts = []

            for utterance in utterances:
                transcript = str(utterance.get("transcript") or "").strip()
                if not transcript:
                    continue

                raw_speaker = utterance.get("speaker")
                speaker_label = None

                if raw_speaker is not None:
                    try:
                        # Deepgram speaker ids are zero-based; display them as one-based.
                        speaker_label = f"Speaker {int(raw_speaker) + 1}"
                    except (TypeError, ValueError):
                        speaker_label = f"Speaker {raw_speaker}"

                if speaker_label != current_speaker_label:
                    flush_current_block()
                    current_speaker_label = speaker_label

                current_parts.append(transcript)

            flush_current_block()
            diarized_text = "\n".join(speaker_blocks).strip()

        channel_text = ""
        channels = results.get("channels") or []
        if channels:
            alternatives = channels[0].get("alternatives") or []
            if alternatives:
                channel_text = str(alternatives[0].get("transcript") or "").strip()

        if diarized_text and channel_text:
            return diarized_text if len(diarized_text) >= len(channel_text) else channel_text

        return diarized_text or channel_text or ""

    @staticmethod
    def _safe_json_or_text(response: requests.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return response.text

    @staticmethod
    def _normalize_file_path(file_path: str) -> str:
        if not isinstance(file_path, str):
            raise TypeError("file_path must be a string.")
        normalized = file_path.strip()
        if not normalized:
            raise HTTPException(status_code=500, detail="Empty file_path passed to ASR client.")

        path = Path(normalized)
        if not path.exists():
            raise FileNotFoundError(f"Media file not found: {path}")

        return str(path)

    @staticmethod
    def _normalize_media_format(media_format: str) -> str:
        if not isinstance(media_format, str):
            raise TypeError("media_format must be a string.")
        normalized = media_format.strip().lower()
        if normalized not in {"mp3", "mp4", "mkv", "mov", "wav"}:
            raise ValueError("media_format must be one of: mp3, mp4, mkv, mov, wav.")
        return normalized
    @staticmethod
    def _normalize_language_mode(language_mode: str) -> str:
        if not isinstance(language_mode, str):
            raise TypeError("language_mode must be a string.")

        normalized = language_mode.strip().lower().replace("-", "_")

        aliases = {
            "multi": "multilingual",
            "multilingual": "multilingual",
            "code_switching": "multilingual",
            "codeswitching": "multilingual",
            "auto": "auto_detect",
            "auto_detect": "auto_detect",
            "detect": "auto_detect",
            "detect_language": "auto_detect",
        }

        resolved = aliases.get(normalized)
        if resolved is None:
            raise ValueError(
                "language_mode must be either 'multilingual' or 'auto_detect'."
            )

        return resolved

    @staticmethod
    def _content_type_for(media_format: str, file_path: str) -> str:
        guessed, _ = mimetypes.guess_type(file_path)
        if guessed:
            return guessed

        mapping = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "mp4": "video/mp4",
            "mkv": "video/x-matroska",
            "mov": "video/quicktime",
        }
        return mapping.get(media_format, "application/octet-stream")


__all__ = [
    "ASRClientConfig",
    "ASRClient",
]
