from __future__ import annotations

"""ReDOCX V1 Text-to-Speech processing engine.

Purpose:
- keep Text-to-Speech processing outside analyzer.py
- consume the already validated/extracted DocumentPayload text unchanged
- delegate speech synthesis to the shared Deepgram client
- support long documents without violating provider request-size limits
- honor the schema speaking-rate and output-format contract
- persist the final owned artifact and return TextToSpeechResult

The analyzer remains responsible for request validation, orchestration, routing,
response-envelope construction, and final response validation.
"""

from dataclasses import dataclass, field
import math
import mimetypes
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Optional, Protocol

from .tts_client import (
    TTSClient,
    default_aura2_model_for_language,
    validate_deepgram_tts_model,
)

try:
    from backend.src.schema import (
        AnalyzerRequest,
        DocumentPayload,
        FeatureType,
        SpeechAudioFormat,
        TextToSpeechRequest,
        TextToSpeechResult,
    )
    from backend.src.storage.artifacts import LocalArtifactStorage
    from backend.src.validation import build_text_to_speech_result
except ImportError:  # pragma: no cover - package-relative local compatibility
    from ...schema import (
        AnalyzerRequest,
        DocumentPayload,
        FeatureType,
        SpeechAudioFormat,
        TextToSpeechRequest,
        TextToSpeechResult,
    )
    from ...storage.artifacts import LocalArtifactStorage
    from ...validation import build_text_to_speech_result


FFMPEG_TTS_TIMEOUT_SECONDS = max(
    30.0,
    float(os.getenv("TTS_FFMPEG_TIMEOUT_SECONDS", "1800")),
)
FFPROBE_TTS_TIMEOUT_SECONDS = max(
    5.0,
    float(os.getenv("TTS_FFPROBE_TIMEOUT_SECONDS", "30")),
)
DEFAULT_TTS_CHUNK_CHARACTERS = max(
    1,
    int(os.getenv("TTS_CHUNK_CHARACTERS", "1900")),
)


class TTSStorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        feature: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class TextToSpeechConfig:
    """Processing configuration that does not alter the V1 schema contract."""

    algorithm_version: Optional[str] = "deepgram-aura-2-tts-v2"
    max_chunk_characters: int = DEFAULT_TTS_CHUNK_CHARACTERS
    ffmpeg_binary: str = os.getenv("FFMPEG_BINARY", "ffmpeg")
    ffprobe_binary: str = os.getenv("FFPROBE_BINARY", "ffprobe")
    voice_map: Mapping[str, str] = field(default_factory=dict)


class TextToSpeechEngine:
    """Provider-backed implementation of analyzer.TextToSpeechEngine."""

    def __init__(
        self,
        *,
        client: Optional[TTSClient] = None,
        storage_backend: Optional[TTSStorageBackend] = None,
        config: Optional[TextToSpeechConfig] = None,
    ) -> None:
        self.client = client or TTSClient()
        self.storage_backend = storage_backend or LocalArtifactStorage()
        self.config = config or TextToSpeechConfig()
        if self.config.max_chunk_characters < 1:
            raise ValueError("max_chunk_characters must be >= 1.")

    def process(self, request: AnalyzerRequest) -> TextToSpeechResult:
        """Synthesize one validated ReDOCX text_to_speech request."""
        if request.action != FeatureType.text_to_speech:
            raise ValueError("TextToSpeechEngine only handles text_to_speech requests.")
        if not isinstance(request.payload, TextToSpeechRequest):
            raise ValueError("text_to_speech requires TextToSpeechRequest payload.")
        if not isinstance(request.input, DocumentPayload):
            raise ValueError("text_to_speech requires DocumentPayload input.")

        source_text = request.input.text
        if not isinstance(source_text, str) or not source_text.strip():
            raise ValueError("text_to_speech requires extracted source text.")

        payload = request.payload
        output_filename = _validate_output_filename(payload.output_filename)
        synthesis_language = payload.synthesis_language.value
        provider_model = self._resolve_voice_model(
            payload.voice_id,
            synthesis_language=synthesis_language,
        )
        provider_speed = _provider_speed_for_model(
            provider_model,
            requested_rate=float(payload.speaking_rate),
            synthesis_language=synthesis_language,
        )
        effective_provider_speed = provider_speed or 1.0
        residual_rate = float(payload.speaking_rate) / effective_provider_speed
        if not 0.5 <= residual_rate <= 2.0:
            raise RuntimeError(
                "Unable to realize the requested speaking_rate within the configured "
                "provider/FFmpeg rate envelope."
            )

        max_chunk_chars = min(
            int(self.config.max_chunk_characters),
            int(self.client.max_input_characters),
        )
        chunks = split_text_for_tts(source_text, max_characters=max_chunk_chars)
        if not chunks or "".join(chunks) != source_text:
            raise RuntimeError("Text-to-Speech chunking did not preserve the source text exactly.")

        with TemporaryDirectory(prefix="redocx-tts-") as temp_dir:
            temp_root = Path(temp_dir)
            raw_pcm_path = temp_root / "synthesis.pcm"

            for index, chunk in enumerate(chunks):
                synthesis = self.client.synthesize_to_file(
                    text=chunk,
                    output_path=str(raw_pcm_path),
                    model=provider_model,
                    speed=provider_speed,
                    append=index > 0,
                )
                if synthesis.characters_submitted != len(chunk):
                    raise RuntimeError(
                        "Text-to-Speech provider client reported an inconsistent character count."
                    )
                if synthesis.model != provider_model:
                    raise RuntimeError("Text-to-Speech provider model changed unexpectedly.")

            if not raw_pcm_path.exists() or raw_pcm_path.stat().st_size <= 0:
                raise RuntimeError("Text-to-Speech synthesis produced no PCM audio.")

            final_path = temp_root / output_filename
            self._encode_final_audio(
                raw_pcm_path=raw_pcm_path,
                output_path=final_path,
                output_format=payload.output_format,
                residual_rate=residual_rate,
            )

            if not final_path.exists() or final_path.stat().st_size <= 0:
                raise RuntimeError(
                    "Text-to-Speech processing completed without a valid output file."
                )

            duration_seconds = self._probe_duration_seconds(final_path)
            file_size_mb = round(final_path.stat().st_size / (1024 * 1024), 6)
            content_type = _content_type_for(payload.output_format)

            stored = self.storage_backend.persist(
                source_file_path=str(final_path),
                artifact_name=output_filename,
                content_type=content_type,
                feature=FeatureType.text_to_speech.value,
            )

        storage_key = getattr(stored, "storage_key", None)
        download_url = getattr(stored, "download_url", None)
        if not storage_key and not download_url:
            raise RuntimeError(
                "Text-to-Speech artifact storage returned neither storage_key nor download_url."
            )

        return build_text_to_speech_result(
            filename=output_filename,
            output_format=payload.output_format,
            file_size_mb=file_size_mb,
            synthesis_language=payload.synthesis_language,
            voice_id=payload.voice_id,
            source_character_count=len(source_text),
            duration_seconds=duration_seconds,
            storage_key=storage_key,
            download_url=download_url,
            algorithm_version=self.config.algorithm_version,
        )

    def _resolve_voice_model(
        self,
        voice_id: str,
        *,
        synthesis_language: str,
    ) -> str:
        normalized = str(voice_id).strip()
        if not normalized:
            raise ValueError("voice_id cannot be empty.")

        if normalized == "default":
            configured_default = str(self.client.config.default_model or "").strip()
            try:
                return validate_deepgram_tts_model(
                    configured_default,
                    language=synthesis_language,
                    allow_configured_legacy=True,
                )
            except ValueError:
                return default_aura2_model_for_language(synthesis_language)

        if normalized in self.config.voice_map:
            model = str(self.config.voice_map[normalized]).strip()
            if not model:
                raise ValueError(f"Configured TTS voice mapping for '{normalized}' is empty.")
            return validate_deepgram_tts_model(
                model,
                language=synthesis_language,
                allow_configured_legacy=True,
            )

        return validate_deepgram_tts_model(
            normalized,
            language=synthesis_language,
        )

    def _encode_final_audio(
        self,
        *,
        raw_pcm_path: Path,
        output_path: Path,
        output_format: SpeechAudioFormat,
        residual_rate: float,
    ) -> None:
        cmd = [
            self.config.ffmpeg_binary,
            "-y",
            "-f",
            "s16le",
            "-ar",
            str(self.client.sample_rate),
            "-ac",
            "1",
            "-i",
            str(raw_pcm_path),
            "-vn",
        ]

        if not math.isclose(residual_rate, 1.0, rel_tol=0.0, abs_tol=1e-9):
            cmd.extend(["-filter:a", f"atempo={residual_rate:.8f}"])

        cmd.extend(_ffmpeg_codec_args(output_format))
        cmd.extend(["-map_metadata", "-1", str(output_path)])

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=FFMPEG_TTS_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg is required for Text-to-Speech output processing but was not found on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "ffmpeg timed out while encoding Text-to-Speech output."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ffmpeg failed while encoding Text-to-Speech output as {output_format.value}."
            ) from exc

        try:
            output_path.chmod(0o600)
        except OSError:
            pass

    def _probe_duration_seconds(self, output_path: Path) -> Optional[float]:
        cmd = [
            self.config.ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(output_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=FFPROBE_TTS_TIMEOUT_SECONDS,
            )
            duration = float(result.stdout.strip())
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
            return None
        if not math.isfinite(duration) or duration <= 0:
            return None
        return round(duration, 3)


def split_text_for_tts(text: str, *, max_characters: int) -> tuple[str, ...]:
    """Partition text without deleting, normalizing, trimming, or reordering characters."""
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    if not text:
        raise ValueError("text cannot be empty.")
    if max_characters < 1:
        raise ValueError("max_characters must be >= 1.")
    if len(text) <= max_characters:
        return (text,)

    chunks: list[str] = []
    start = 0
    length = len(text)
    preferred_floor = max(1, int(max_characters * 0.55))

    while start < length:
        hard_end = min(start + max_characters, length)
        if hard_end == length:
            chunks.append(text[start:hard_end])
            break

        window = text[start:hard_end]
        cut = _preferred_cut_index(window, preferred_floor=preferred_floor)
        if cut <= 0:
            cut = len(window)
        end = start + cut
        chunks.append(text[start:end])
        start = end

    if any(not chunk for chunk in chunks) or "".join(chunks) != text:
        raise RuntimeError("Exact Text-to-Speech chunk partitioning failed.")
    if any(len(chunk) > max_characters for chunk in chunks):
        raise RuntimeError("Text-to-Speech chunk exceeds provider character limit.")
    return tuple(chunks)


def _preferred_cut_index(window: str, *, preferred_floor: int) -> int:
    """Choose a natural boundary while returning the exact slice length to retain."""
    lower = min(max(1, preferred_floor), len(window))

    for index in range(len(window) - 1, lower - 1, -1):
        char = window[index]
        previous = window[index - 1] if index > 0 else ""
        if char == "\n":
            return index + 1
        if char.isspace() and previous in ".!?…;:":
            return index + 1

    for index in range(len(window) - 1, lower - 1, -1):
        if window[index].isspace():
            return index + 1

    return len(window)


def _provider_speed_for_model(
    model: str,
    *,
    requested_rate: float,
    synthesis_language: str,
) -> Optional[float]:
    """Use native speed only where the selected Deepgram model supports it.

    Flux accepts 0.5..1.5 in 0.05 increments. Aura-2 native speed is currently
    available for English and Spanish at 0.7..1.5. ReDOCX's 0.5..2.0 contract is
    completed by one residual FFmpeg atempo pass after exact PCM concatenation.
    """
    if model.startswith("flux-"):
        clamped = min(1.5, max(0.5, float(requested_rate)))
        steps = round(clamped / 0.05)
        quantized = round(steps * 0.05, 2)
        return min(1.5, max(0.5, quantized))

    if model.startswith("aura-2-") and synthesis_language in {"en", "es"}:
        return min(1.5, max(0.7, float(requested_rate)))

    return None


def _validate_output_filename(value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("output_filename cannot be empty.")
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise ValueError("output_filename must be a plain filename without path components.")
    if Path(normalized).name != normalized:
        raise ValueError("output_filename must not contain path traversal.")
    return normalized


def _ffmpeg_codec_args(output_format: SpeechAudioFormat) -> list[str]:
    if output_format == SpeechAudioFormat.wav:
        return ["-c:a", "pcm_s16le"]
    if output_format == SpeechAudioFormat.mp3:
        return ["-c:a", "libmp3lame", "-b:a", "128k"]
    if output_format == SpeechAudioFormat.opus:
        return ["-c:a", "libopus", "-b:a", "64k"]
    if output_format == SpeechAudioFormat.aac:
        return ["-c:a", "aac", "-b:a", "128k"]
    if output_format == SpeechAudioFormat.flac:
        return ["-c:a", "flac"]
    raise ValueError(f"Unsupported Text-to-Speech output format: {output_format!r}")


def _content_type_for(output_format: SpeechAudioFormat) -> str:
    mapping = {
        SpeechAudioFormat.mp3: "audio/mpeg",
        SpeechAudioFormat.wav: "audio/wav",
        SpeechAudioFormat.opus: "audio/ogg",
        SpeechAudioFormat.aac: "audio/aac",
        SpeechAudioFormat.flac: "audio/flac",
    }
    content_type = mapping.get(output_format)
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(f"audio.{output_format.value}")
    return guessed or "application/octet-stream"


__all__ = [
    "TextToSpeechConfig",
    "TextToSpeechEngine",
    "TTSStorageBackend",
    "split_text_for_tts",
]
