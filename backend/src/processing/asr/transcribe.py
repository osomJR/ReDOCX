from __future__ import annotations

"""
V1 transcription processing.

Purpose:
- hold transcription-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free at the processor layer
- schema-agnostic: this module returns transcript text only
- provider-backed by default via the shared ASR client
- separates media preparation, ASR execution, and transcript cleanup
- for video inputs, transcription is modeled as: extract audio -> ASR -> minimal cleanup
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Protocol
import subprocess

from .asr_client import ASRClient


BASE_TRANSCRIBE_RULES = """
TASK: AUDIO/VIDEO TRANSCRIPTION
RULES:
- Convert spoken content into written text only
- Preserve original language and speech style
- Do NOT paraphrase, summarize, or interpret
- Keep output as inline text only
- Speaker separation is allowed only when acoustically detectable
- Background-noise removal must be minimal and optional
- Filler-word preservation must follow the caller-provided toggle
""".strip()

AUDIO_EXTRACTION_RULES = """
VIDEO PREPARATION RULES:
- If the input is video, first extract the audio track
- Do not alter semantic speech content during extraction
- Use extraction only as a preparation step before ASR
""".strip()

POST_PROCESSING_RULES = """
POST-PROCESSING RULES:
- Preserve wording exactly as recognized by ASR
- Do not rewrite for style
- Do not translate
- Do not summarize
- Only apply minimal cleanup requested by the caller
""".strip()


class AudioPreparationBackend(Protocol):
    """Provider interface for media preparation before ASR."""

    def prepare_audio(
        self,
        *,
        media_type: str,
        media_format: str,
        file_reference: str,
        remove_background_noise: bool,
    ) -> str:
        """Return an audio file path suitable for ASR."""
        ...


class ASRBackend(Protocol):
    """Provider interface for speech-to-text runtime."""

    def transcribe(
        self,
        *,
        audio_reference: str,
        media_format: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        """Return only the transcript text content."""
        ...


class TranscriptPostProcessor(Protocol):
    """Provider interface for minimal transcript cleanup after ASR."""

    def finalize(
        self,
        *,
        transcript_text: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        """Return the finalized transcript text content."""
        ...


class FFmpegAudioPreparationBackend:
    """
    Real media-preparation backend.

    - Audio inputs are passed through unchanged when background-noise removal is off.
    - Audio inputs are converted to a temporary mono 16k WAV when background-noise removal is on.
    - Video inputs are always converted into a temporary mono 16k WAV suitable for ASR.
    - Optional background-noise removal uses conservative FFmpeg filters only.
    """

    def __init__(self) -> None:
        self._tmpdir = TemporaryDirectory(prefix="asr-prep-")

    def prepare_audio(
        self,
        *,
        media_type: str,
        media_format: str,
        file_reference: str,
        remove_background_noise: bool,
    ) -> str:
        normalized_media_type = _normalize_media_type(media_type)
        normalized_remove_background_noise = _normalize_bool(
            remove_background_noise,
            field_name="remove_background_noise",
        )

        input_path = Path(_normalize_file_reference(file_reference))
        if not input_path.exists():
            raise FileNotFoundError(f"Media file not found: {input_path}")

        # If this is plain audio and the user did not request noise cleanup,
        # send the original file directly to ASR.
        if normalized_media_type == "audio" and not normalized_remove_background_noise:
            return str(input_path)

        output_path = Path(self._tmpdir.name) / f"{input_path.stem}.prepared.wav"

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
        ]

        # Video input: ignore video stream and extract audio only.
        if normalized_media_type == "video":
            cmd.append("-vn")

        # Conservative cleanup:
        # - highpass removes low-frequency rumble
        # - lowpass removes high-frequency hiss
        # - afftdn performs light frequency-domain denoising
        # - loudnorm stabilizes volume for ASR
        if normalized_remove_background_noise:
            cmd.extend(
                [
                    "-af",
                    "highpass=f=80,lowpass=f=7800,afftdn=nf=-25,loudnorm=I=-18:TP=-2:LRA=11",
                ]
            )

        cmd.extend(
            [
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output_path),
            ]
        )

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg is required for transcription audio preparation but was not found on PATH."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "ffmpeg failed while preparing audio for transcription."
            ) from exc

        if not output_path.exists():
            raise RuntimeError(
                "Audio preparation completed without producing an output file."
            )

        return str(output_path)


class ClientASRBackend:
    """
    Real ASR backend powered by the shared ASR client.

    Expected ASRClient interface:
        client.transcribe(
            file_path=<str>,
            media_format=<str>,
            diarize_speakers=<bool>,
            preserve_filler_words=<bool>,
        ) -> str

    If your asr_client.py uses a different method name or parameter shape, only
    this class should need adjustment.
    """

    def __init__(self, asr_client: ASRClient | None = None) -> None:
        self.asr_client = asr_client or ASRClient()

    def transcribe(
        self,
        *,
        audio_reference: str,
        media_format: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        return self.asr_client.transcribe(
            file_path=audio_reference,
            media_format=media_format,
            preserve_filler_words=preserve_filler_words,
            diarize_speakers=diarize_speakers,
        )


class DefaultTranscriptPostProcessor:
    """
    Minimal transcript cleanup.

    Keeps the transcript faithful to ASR output while normalizing whitespace.
    Any richer speaker formatting should happen here rather than in analyzer.py.
    """

    def finalize(
        self,
        *,
        transcript_text: str,
        preserve_filler_words: bool,
        diarize_speakers: bool,
    ) -> str:
        del preserve_filler_words
        del diarize_speakers

        lines = [line.rstrip() for line in transcript_text.splitlines()]
        cleaned_lines = []
        last_blank = False

        for line in lines:
            if line.strip():
                cleaned_lines.append(" ".join(line.split()))
                last_blank = False
            else:
                if not last_blank:
                    cleaned_lines.append("")
                last_blank = True

        cleaned = "\n".join(cleaned_lines).strip()
        return cleaned


@dataclass(frozen=True)
class TranscribeConfig:
    """Optional knobs for future provider-backed transcription."""

    algorithm_version: Optional[str] = None


class TranscribeProcessor:
    """
    Stateless transcription processor.

    Responsibilities:
    - validate local processing preconditions
    - build contract-aligned transcription instructions
    - prepare audio for ASR, including video-to-audio extraction
    - delegate ASR to a backend
    - apply minimal post-processing
    - return transcript text only

    Non-responsibilities:
    - request validation
    - response/result model construction
    - file generation or storage
    - language-field orchestration
    - media upload parsing or schema-level limit enforcement
    """

    def __init__(
        self,
        *,
        audio_preparation_backend: Optional[AudioPreparationBackend] = None,
        asr_backend: Optional[ASRBackend] = None,
        post_processor: Optional[TranscriptPostProcessor] = None,
        config: Optional[TranscribeConfig] = None,
    ) -> None:
        self.audio_preparation_backend = audio_preparation_backend or FFmpegAudioPreparationBackend()
        self.asr_backend = asr_backend or ClientASRBackend()
        self.post_processor = post_processor or DefaultTranscriptPostProcessor()
        self.config = config or TranscribeConfig()

    def transcribe(
        self,
        *,
        media_type: str,
        media_format: str,
        file_reference: str,
        preserve_filler_words: bool = True,
        remove_background_noise: bool = False,
        diarize_speakers: bool = True,
    ) -> str:
        normalized_media_type = _normalize_media_type(media_type)
        normalized_media_format = _normalize_media_format(media_format)
        normalized_file_reference = _normalize_file_reference(file_reference)
        normalized_preserve_filler_words = _normalize_bool(
            preserve_filler_words,
            field_name="preserve_filler_words",
        )
        normalized_remove_background_noise = _normalize_bool(
            remove_background_noise,
            field_name="remove_background_noise",
        )
        normalized_diarize_speakers = _normalize_bool(
            diarize_speakers,
            field_name="diarize_speakers",
        )

        _instructions = build_transcribe_instructions(
            media_type=normalized_media_type,
            media_format=normalized_media_format,
            preserve_filler_words=normalized_preserve_filler_words,
            remove_background_noise=normalized_remove_background_noise,
            diarize_speakers=normalized_diarize_speakers,
        )

        prepared_audio = self.audio_preparation_backend.prepare_audio(
            media_type=normalized_media_type,
            media_format=normalized_media_format,
            file_reference=normalized_file_reference,
            remove_background_noise=normalized_remove_background_noise,
        )

        prepared_media_format = (
    Path(prepared_audio).suffix.lstrip(".").lower() or normalized_media_format
)

        raw_transcript = self.asr_backend.transcribe(
            audio_reference=prepared_audio,
            media_format=prepared_media_format,
            preserve_filler_words=normalized_preserve_filler_words,
            diarize_speakers=normalized_diarize_speakers,
        )

        print("RAW TRANSCRIPT LENGTH:", len(raw_transcript))
        print("RAW TRANSCRIPT PREVIEW:", repr(raw_transcript[:500]))

        finalized = self.post_processor.finalize(
            transcript_text=_normalize_text(raw_transcript),
            preserve_filler_words=normalized_preserve_filler_words,
            diarize_speakers=normalized_diarize_speakers,
        )

        return _normalize_text(finalized)


def build_transcribe_instructions(
    *,
    media_type: str,
    media_format: str,
    preserve_filler_words: bool,
    remove_background_noise: bool,
    diarize_speakers: bool,
) -> str:
    """
    Build contract-aligned transcription instructions.

    This module intentionally does not import schema.py or validation.py because
    analyzer/schema already enforce the upstream contract before calling the
    processing layer.
    """
    normalized_media_type = _normalize_media_type(media_type)
    normalized_media_format = _normalize_media_format(media_format)
    normalized_preserve_filler_words = _normalize_bool(
        preserve_filler_words,
        field_name="preserve_filler_words",
    )
    normalized_remove_background_noise = _normalize_bool(
        remove_background_noise,
        field_name="remove_background_noise",
    )
    normalized_diarize_speakers = _normalize_bool(
        diarize_speakers,
        field_name="diarize_speakers",
    )

    preparation_block = (
        AUDIO_EXTRACTION_RULES
        if normalized_media_type == "video"
        else "AUDIO INPUT RULES:\n- Use the provided audio stream directly"
    )

    return (
        f"{BASE_TRANSCRIBE_RULES}\n\n"
        f"{preparation_block}\n\n"
        f"{POST_PROCESSING_RULES}\n\n"
        f"MEDIA TYPE:\n{normalized_media_type}\n\n"
        f"MEDIA FORMAT:\n{normalized_media_format}\n\n"
        f"PRESERVE FILLER WORDS:\n{normalized_preserve_filler_words}\n\n"
        f"REMOVE BACKGROUND NOISE:\n{normalized_remove_background_noise}\n\n"
        f"DIARIZE SPEAKERS:\n{normalized_diarize_speakers}"
    )


def transcribe_media(
    *,
    media_type: str,
    media_format: str,
    file_reference: str,
    preserve_filler_words: bool = True,
    remove_background_noise: bool = False,
    diarize_speakers: bool = True,
    audio_preparation_backend: Optional[AudioPreparationBackend] = None,
    asr_backend: Optional[ASRBackend] = None,
    post_processor: Optional[TranscriptPostProcessor] = None,
    config: Optional[TranscribeConfig] = None,
) -> str:
    """Functional convenience wrapper for analyzer integration."""
    processor = TranscribeProcessor(
        audio_preparation_backend=audio_preparation_backend,
        asr_backend=asr_backend,
        post_processor=post_processor,
        config=config,
    )
    return processor.transcribe(
        media_type=media_type,
        media_format=media_format,
        file_reference=file_reference,
        preserve_filler_words=preserve_filler_words,
        remove_background_noise=remove_background_noise,
        diarize_speakers=diarize_speakers,
    )


def _normalize_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError("text must be a string.")
    normalized = text.strip()
    if not normalized:
        raise ValueError("Empty content cannot be processed.")
    return normalized


def _normalize_file_reference(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("file_reference must be a string.")
    normalized = value.strip()
    if not normalized:
        raise ValueError("file_reference cannot be empty.")
    return normalized


def _normalize_media_type(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("media_type must be a string.")
    normalized = value.strip().lower()
    if normalized not in {"audio", "video"}:
        raise ValueError("media_type must be either 'audio' or 'video'.")
    return normalized


def _normalize_media_format(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("media_format must be a string.")
    normalized = value.strip().lower()
    if normalized not in {"mp3", "mp4", "mkv", "mov", "wav"}:
        raise ValueError("media_format must be one of: mp3, mp4, mkv, mov, wav.")
    return normalized


def _normalize_bool(value: bool, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool.")
    return value


__all__ = [
    "BASE_TRANSCRIBE_RULES",
    "AUDIO_EXTRACTION_RULES",
    "POST_PROCESSING_RULES",
    "AudioPreparationBackend",
    "ASRBackend",
    "TranscriptPostProcessor",
    "FFmpegAudioPreparationBackend",
    "ClientASRBackend",
    "DefaultTranscriptPostProcessor",
    "TranscribeConfig",
    "TranscribeProcessor",
    "build_transcribe_instructions",
    "transcribe_media",
]
