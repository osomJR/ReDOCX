"""Request-local reuse and conservative language selection for Redaction only.

Only language models are process-cached. Document pixels, recognised text,
coordinates and findings live in a private temporary request directory, which
is removed on both success and failure. No recognition result crosses requests.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import pickle
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import threading
from typing import Any, Callable, Iterator, Sequence, TypeVar
import unicodedata

from PIL import Image

T = TypeVar("T")
_CURRENT: ContextVar[RedactionAnalysis | None] = ContextVar("redocx_redaction_analysis", default=None)


class RedactionAnalysis:
    """Disk-backed memoization avoids retaining full-page bitmaps in RAM."""

    def __init__(self) -> None:
        try:
            self._directory = TemporaryDirectory(prefix="redocx-redaction-analysis-")
        except OSError:
            self._directory = None
        self._paths: dict[tuple[str, str], str] = {}
        self._lock = threading.RLock()

    def cached(self, namespace: str, key: Any, produce: Callable[[], T]) -> T:
        if self._directory is None:
            return produce()
        digest = hashlib.sha256(pickle.dumps(key, protocol=4)).hexdigest()
        cache_key = namespace, digest
        with self._lock:
            path = self._paths.get(cache_key)
            if path is not None:
                try:
                    with open(path, "rb") as stream:
                        return pickle.load(stream)
                except (OSError, EOFError, pickle.UnpicklingError):
                    self._paths.pop(cache_key, None)
            # A failed computation is never cached or converted into success.
            value = produce()
            path = f"{self._directory.name}/{namespace}-{digest}.cache"
            try:
                with open(path, "wb") as stream:
                    pickle.dump(value, stream, protocol=4)
                self._paths[cache_key] = path
            except (OSError, pickle.PickleError, TypeError):
                # Reuse is an optimization, never a prerequisite for redaction.
                pass
            return value

    def close(self) -> None:
        self._paths.clear()
        if self._directory is not None:
            self._directory.cleanup()


@contextmanager
def redaction_analysis_session() -> Iterator[RedactionAnalysis]:
    existing = _CURRENT.get()
    if existing is not None:
        yield existing
        return
    analysis = RedactionAnalysis()
    token = _CURRENT.set(analysis)
    try:
        yield analysis
    finally:
        _CURRENT.reset(token)
        analysis.close()


def analysis_cached(namespace: str, key: Any, produce: Callable[[], T]) -> T:
    analysis = _CURRENT.get()
    return produce() if analysis is None else analysis.cached(namespace, key, produce)


def analysis_active() -> bool:
    analysis = _CURRENT.get()
    return analysis is not None and analysis._directory is not None


def original_page_key(page: Any) -> tuple | None:
    """Identity for a prepared original page, never an in-memory masked page.

    Callers opt in only before content removal. File changes and page-geometry
    changes invalidate the snapshot. Temporary in-memory documents use ordinary
    pixel-based recognition reuse instead.
    """
    try:
        source = Path(page.parent.name).resolve()
        stat = source.stat()
        if not source.is_file():
            return None
        identity = (str(source), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        def digest_source() -> str:
            digest = hashlib.sha256()
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        digest = analysis_cached("source-identity", identity, digest_source)
        return digest, page.number, tuple(page.mediabox), tuple(page.cropbox), page.rotation
    except (OSError, AttributeError, TypeError):
        return None


def image_fingerprint(image: Image.Image) -> tuple[str, tuple[int, int], str]:
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    return rgb.mode, rgb.size, hashlib.sha256(rgb.tobytes()).hexdigest()


_LATIN = frozenset({"eng", "fra", "deu", "spa", "ita", "nld", "pol", "por", "swa", "tur", "yor", "hau", "ibo"})
_SCRIPT_CODES = {
    "CYRILLIC": frozenset({"rus", "ukr"}),
    "ARABIC": frozenset({"ara", "fas"}),
    "DEVANAGARI": frozenset({"hin"}),
    "HIRAGANA": frozenset({"jpn"}),
    "KATAKANA": frozenset({"jpn"}),
    "HANGUL": frozenset({"kor"}),
    "CJK": frozenset({"chi_sim", "chi_tra", "jpn"}),
    "LATIN": _LATIN,
}

# Multilingual discovery can spell an otherwise Latin token with one visually
# identical Cyrillic glyph. That is not evidence of a Russian/Ukrainian section.
# Genuine Cyrillic words, and distinct letters such as Я or Ж, remain evidence.
_CYRILLIC_LATIN_LOOKALIKES = frozenset("АВЕКМНОРСТХаеорсхуІіЈјЅѕӀ")


def _observed_scripts(text: str) -> set[str]:
    observed: set[str] = set()
    for word in re.findall(r"[^\W\d_]+", text):
        cyrillic = [char for char in word if "CYRILLIC" in unicodedata.name(char, "")]
        ignore_cyrillic = (
            len(cyrillic) <= 2
            and all(char in _CYRILLIC_LATIN_LOOKALIKES for char in cyrillic)
        )
        for char in word:
            name = unicodedata.name(char, "")
            for script in _SCRIPT_CODES:
                if script in name:
                    if script != "CYRILLIC" or not ignore_cyrillic:
                        observed.add(script)
                    break
    return observed


@lru_cache(maxsize=8)
def _language_detector(available: tuple[str, ...]):
    # This cache contains immutable language models, never document text.
    from lingua import Language, LanguageDetectorBuilder
    supported = {language.iso_code_639_3.name.lower(): language for language in Language.all()}
    codes = {"zho" if code in {"chi_sim", "chi_tra"} else code for code in available}
    languages = [supported[code] for code in sorted(codes) if code in supported]
    if len(languages) < 2:
        return None
    return LanguageDetectorBuilder.from_languages(*languages).build()


def _matching_codes(language: Any, available: set[str]) -> set[str]:
    code = language.iso_code_639_3.name.lower()
    return ({"chi_sim", "chi_tra"} if code == "zho" else {code}) & available


def detect_text_languages(text: str, available: Sequence[str]) -> tuple[str, ...]:
    """Find languages in individual sections, including minority sections.

    Weak/short evidence retains compatible installed models. It must never
    force an English default or remove a script because another is dominant.
    """
    installed = set(available) - {"osd", "equ"}
    fallback = tuple(sorted(installed))
    if len(installed) <= 1 or not text.strip():
        return fallback
    try:
        detector = _language_detector(fallback)
    except (ImportError, ValueError):
        return fallback
    if detector is None:
        return fallback
    selected: set[str] = set()
    observed_scripts = _observed_scripts(text)

    # Bound each language-classification input, not the number of sections.
    # Every section is visited; a later-page language cannot be truncated away.
    sections = re.split(r"[\r\n]+|(?<=[.!?。！？])\s+", text)
    sections.append(text)
    for section in sections:
        for start in range(0, len(section), 4000):
            sample = section[start:start + 4000]
            if sum(char.isalpha() for char in sample) < 16:
                continue
            for result in detector.detect_multiple_languages_of(sample):
                part = sample[result.start_index:result.end_index]
                if sum(char.isalpha() for char in part) < 16:
                    continue
                confidence = detector.compute_language_confidence_values(part)
                if not confidence:
                    continue
                second = confidence[1].value if len(confidence) > 1 else 0.0
                if confidence[0].value >= .70 and confidence[0].value - second >= .15:
                    selected.update(_matching_codes(confidence[0].language, installed))

    # Script evidence protects short sections that text classification cannot
    # distinguish reliably (e.g. an Arabic label on an English identity card).
    for script in observed_scripts:
        compatible = _SCRIPT_CODES[script] & installed
        if not compatible:
            return fallback
        if not selected.intersection(compatible):
            selected.update(compatible)
    return tuple(sorted(selected)) if selected else fallback


def select_image_languages(
    image: Image.Image, *, requested: str, available: Sequence[str],
    probe: Callable[[Image.Image, str], tuple[str, float, int]],
) -> str:
    """A bounded discovery pass selects the full-resolution recognition models.

    Explicit subsets remain authoritative. Automatic discovery is once per
    distinct source surface; poor evidence keeps all installed text models.
    OSD is an orientation model and is not added as a recognition language.
    """
    installed = tuple(sorted(set(available) - {"osd", "equ"}))
    requested_codes = tuple(dict.fromkeys(code for code in requested.split("+") if code not in {"osd", "equ"}))
    if set(requested_codes) != set(installed) or len(installed) <= 1:
        return "+".join(requested_codes)

    def select() -> str:
        # Prefer English for the probe when installed, while retaining every
        # installed script in the discovery pass itself.
        probe_codes = (("eng",) if "eng" in installed else ()) + tuple(code for code in installed if code != "eng")
        try:
            text, confidence, tokens = probe(image, "+".join(probe_codes))
        except (RuntimeError, OSError):
            return "+".join(installed)
        if confidence < 60 or tokens < 8:
            return "+".join(installed)
        return "+".join(detect_text_languages(text, installed))

    return analysis_cached("languages", (image_fingerprint(image), installed), select)


def finding_context(sdp: Any, payload: Any, custom_redactions: Sequence[str] | None) -> tuple:
    """Provider and policy changes must never reuse incompatible findings."""
    return (
        getattr(sdp, "project_id", None), getattr(sdp, "location", None),
        getattr(sdp, "min_likelihood", None), payload.model_dump_json(),
        tuple(custom_redactions or ()),
    )
