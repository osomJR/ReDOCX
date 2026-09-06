from __future__ import annotations

"""
V1 transcription processing.

Purpose:
- hold transcription-specific processing logic outside analyzer.py
- keep schema/validation/extraction unchanged
- keep analyzer responsible only for orchestration, routing, and response building

Design notes:
- stateless and side-effect free at the processor layer
- schema-agnostic: this module returns transcript text plus provider-timed subtitle cues
- provider-backed by default via the shared ASR client
- separates media preparation, ASR execution, and transcript cleanup
- for video inputs, transcription is modeled as: extract audio -> ASR -> minimal cleanup
"""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator, Optional, Protocol
import os
import subprocess

from .asr_client import ASRClient, ASRTranscription, ASRWord


FFMPEG_TRANSCRIBE_TIMEOUT_SECONDS = max(
    30.0,
    float(os.getenv("TRANSCRIBE_FFMPEG_TIMEOUT_SECONDS", "900")),
)
FFMPEG_PLAYBACK_TIMEOUT_SECONDS = max(
    60.0,
    float(os.getenv("TRANSCRIBE_PLAYBACK_FFMPEG_TIMEOUT_SECONDS", "1200")),
)

# Movie-style subtitle segmentation targets. Timing always comes from ASR word
# timestamps; these limits only decide where a readable cue boundary is placed.
SUBTITLE_MAX_LINE_CHARS = 42
SUBTITLE_MAX_LINES = 2
SUBTITLE_MAX_WORDS = 16
SUBTITLE_MAX_DURATION_SECONDS = 6.0
SUBTITLE_BREAK_PAUSE_SECONDS = 0.8
SUBTITLE_SENTENCE_MIN_DURATION_SECONDS = 1.0


@dataclass(frozen=True)
class SubtitleCue:
    start_seconds: float
    end_seconds: float
    text: str
    speaker_label: Optional[str] = None


@dataclass(frozen=True)
class TranscriptionOutput:
    content: str
    subtitle_cues: tuple[SubtitleCue, ...]


@dataclass(frozen=True)
class PlaybackRendition:
    file_path: str
    filename: str
    media_type: str
    media_format: str
    mime_type: str


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
    ) -> ASRTranscription:
        """Return transcript text together with provider word timestamps."""
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

    - MP3 audio is passed through unchanged when background-noise removal is off.
    - Other accepted audio containers/codecs are normalized to temporary mono 16k WAV.
    - Audio with requested noise cleanup is normalized to temporary mono 16k WAV.
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

        # Preserve the existing direct MP3 path when no cleanup is requested.
        # All other accepted audio inputs are decoded through FFmpeg even without
        # denoising so codec/container quirks are normalized before they reach ASR.
        if (
            normalized_media_type == "audio"
            and media_format.strip().lower() == "mp3"
            and not normalized_remove_background_noise
        ):
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
                timeout=FFMPEG_TRANSCRIBE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg is required for transcription audio preparation but was not found on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "ffmpeg timed out while preparing audio for transcription."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "ffmpeg failed while preparing audio for transcription."
            ) from exc

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(
                "Audio preparation completed without producing a valid output file."
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
    ) -> ASRTranscription:
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
    - return transcript text plus synchronized subtitle cues

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
    ) -> TranscriptionOutput:
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
            Path(prepared_audio).suffix.lstrip(".").lower()
            or normalized_media_format
        )

        asr_result = self.asr_backend.transcribe(
            audio_reference=prepared_audio,
            media_format=prepared_media_format,
            preserve_filler_words=normalized_preserve_filler_words,
            diarize_speakers=normalized_diarize_speakers,
        )

        if not isinstance(asr_result, ASRTranscription):
            raise TypeError(
                "ASR backend must return ASRTranscription with word-level timestamps."
            )

        finalized = self.post_processor.finalize(
            transcript_text=_normalize_text(asr_result.transcript),
            preserve_filler_words=normalized_preserve_filler_words,
            diarize_speakers=normalized_diarize_speakers,
        )

        subtitle_cues = build_subtitle_cues(
            asr_result.words,
            diarize_speakers=normalized_diarize_speakers,
        )
        if not subtitle_cues:
            raise RuntimeError(
                "Synchronized subtitles could not be created because ASR word timestamps were unavailable."
            )

        return TranscriptionOutput(
            content=_normalize_text(finalized),
            subtitle_cues=subtitle_cues,
        )


def _speaker_label(word: ASRWord, *, diarize_speakers: bool) -> Optional[str]:
    if not diarize_speakers or word.speaker_index is None:
        return None
    return f"Speaker {word.speaker_index + 1}"


def _cue_text(words: list[ASRWord]) -> str:
    return " ".join(word.text.strip() for word in words if word.text.strip()).strip()


def _subtitle_lines(text: str) -> list[str]:
    words = [part for part in text.split() if part]
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > SUBTITLE_MAX_LINE_CHARS:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fits_subtitle_frame(text: str) -> bool:
    return len(_subtitle_lines(text)) <= SUBTITLE_MAX_LINES


def _format_subtitle_text(text: str) -> str:
    return "\n".join(_subtitle_lines(text)).strip()


def build_subtitle_cues(
    words: tuple[ASRWord, ...],
    *,
    diarize_speakers: bool,
) -> tuple[SubtitleCue, ...]:
    """Build readable, timestamp-faithful subtitle cues from ASR words.

    Cue boundaries are selected for movie-style readability, but cue start/end
    times are always taken directly from the first/last provider-timed words.
    No transcript-duration interpolation or synthetic timing is used.
    """
    if not words:
        return ()

    cues: list[SubtitleCue] = []
    current: list[ASRWord] = []

    def flush() -> None:
        if not current:
            return
        text = _format_subtitle_text(_cue_text(current))
        if not text:
            current.clear()
            return
        first = current[0]
        last = current[-1]
        cues.append(
            SubtitleCue(
                start_seconds=round(float(first.start_seconds), 3),
                end_seconds=round(float(last.end_seconds), 3),
                text=text,
                speaker_label=_speaker_label(
                    first,
                    diarize_speakers=diarize_speakers,
                ),
            )
        )
        current.clear()

    for word in words:
        if not word.text.strip():
            continue

        if current:
            previous = current[-1]
            current_text = _cue_text(current)
            projected_text = f"{current_text} {word.text.strip()}".strip()
            projected_duration = float(word.end_seconds) - float(current[0].start_seconds)
            pause_seconds = max(0.0, float(word.start_seconds) - float(previous.end_seconds))
            speaker_changed = (
                diarize_speakers
                and previous.speaker_index is not None
                and word.speaker_index is not None
                and previous.speaker_index != word.speaker_index
            )
            sentence_boundary = (
                previous.text.rstrip().endswith((".", "?", "!", "…"))
                and float(previous.end_seconds) - float(current[0].start_seconds)
                >= SUBTITLE_SENTENCE_MIN_DURATION_SECONDS
            )

            if (
                speaker_changed
                or pause_seconds >= SUBTITLE_BREAK_PAUSE_SECONDS
                or projected_duration > SUBTITLE_MAX_DURATION_SECONDS
                or not _fits_subtitle_frame(projected_text)
                or len(current) >= SUBTITLE_MAX_WORDS
                or sentence_boundary
            ):
                flush()

        current.append(word)

    flush()

    return tuple(
        cue
        for cue in cues
        if cue.text and cue.start_seconds >= 0 and cue.end_seconds > cue.start_seconds
    )


@contextmanager
def build_browser_playback_rendition(
    *,
    media_type: str,
    file_reference: str,
) -> Iterator[PlaybackRendition]:
    """Create a browser-safe rendition whose timeline starts at 0 seconds.

    The same source media feeds ASR and this rendition, so subtitle cue timestamps
    remain aligned even when the original container/codec is not playable by the
    browser. The rendition is temporary; the analyzer persists it as an owned
    short-lived artifact before this context exits.
    """
    normalized_media_type = _normalize_media_type(media_type)
    input_path = Path(_normalize_file_reference(file_reference))
    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"Media file not found: {input_path}")

    with TemporaryDirectory(prefix="transcribe-playback-") as temp_dir:
        temp_root = Path(temp_dir)
        if normalized_media_type == "audio":
            output_path = temp_root / f"{input_path.stem}.playback.m4a"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:a:0",
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                "-map_metadata",
                "-1",
                str(output_path),
            ]
            media_format = "m4a"
            mime_type = "audio/mp4"
        else:
            output_path = temp_root / f"{input_path.stem}.playback.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                "-avoid_negative_ts",
                "make_zero",
                "-map_metadata",
                "-1",
                str(output_path),
            ]
            media_format = "mp4"
            mime_type = "video/mp4"

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=FFMPEG_PLAYBACK_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg is required for synchronized transcription playback but was not found on PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "ffmpeg timed out while creating the synchronized playback rendition."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "ffmpeg failed while creating the synchronized playback rendition."
            ) from exc

        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(
                "Synchronized playback rendition was not created successfully."
            )

        yield PlaybackRendition(
            file_path=str(output_path),
            filename=output_path.name,
            media_type=normalized_media_type,
            media_format=media_format,
            mime_type=mime_type,
        )


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
) -> TranscriptionOutput:
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
    if normalized not in {
        "mp3",
        "wav",
        "aac",
        "flac",
        "webm",
        "m4a",
        "ogg",
        "mp4",
        "mov",
        "avi",
        "mkv",
        "wmv",
    }:
        raise ValueError(
            "media_format must be one of: mp3, wav, aac, flac, webm, m4a, ogg, "
            "mp4, mov, avi, mkv, wmv."
        )
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
    "SubtitleCue",
    "TranscriptionOutput",
    "PlaybackRendition",
    "TranscribeConfig",
    "TranscribeProcessor",
    "build_subtitle_cues",
    "build_browser_playback_rendition",
    "build_transcribe_instructions",
    "transcribe_media",
]
