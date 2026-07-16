from __future__ import annotations

"""
ReDOCX PDF Tools - Compress PDF processing.

Compression strategy:
1. Prefer PyMuPDF's level-aware image rewriting. This keeps the three quality
   profiles consistent across development and production environments.
2. Fall back to Ghostscript when the installed PyMuPDF version cannot rewrite
   images or cannot process a particular document.
3. Never silently return a larger output unless allow_larger_output=True.
4. Validate that the generated PDF is readable and preserves the source page
   count before persisting it.

The service layer can wrap CompressedPdfArtifact into validation.build_compress_pdf_result(...).
"""

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional, Protocol
import argparse
import json
import mimetypes
import logging
import os
import re
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


class CompressionChildLimitError(RuntimeError):
    """Raised when an isolated compression child exceeds a hard resource limit."""


class StorageBackend(Protocol):
    def persist(
        self,
        *,
        source_file_path: str,
        artifact_name: str,
        content_type: Optional[str] = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class CompressedPdfArtifact:
    file_name: str
    file_extension: str
    file_size_mb: float
    file_path: str
    compression_level: str
    original_file_size_mb: float
    compressed_file_size_mb: float
    estimated_output_file_size_mb: Optional[float] = None
    compression_ratio: Optional[float] = None
    engine: str = "unknown"
    storage_key: Optional[str] = None
    download_url: Optional[str] = None


class PdfCompressionBackend(Protocol):
    def compress(
        self,
        *,
        source_path: str | Path,
        compression_level: str | Any = "balanced",
        output_filename: str = "compressed-document.pdf",
        job_id: Optional[str] = None,
    ) -> CompressedPdfArtifact:
        ...


class LocalPdfCompressionBackend:
    def __init__(
        self,
        *,
        storage_backend: Optional[StorageBackend] = None,
        artifacts_dir: str | Path = "artifacts/pdf_tools/compress",
        allow_larger_output: bool = False,
        ghostscript_binary: Optional[str] = None,
        qpdf_binary: Optional[str] = None,
    ) -> None:
        self.storage_backend = storage_backend
        self.artifacts_dir = Path(artifacts_dir)
        self.allow_larger_output = allow_larger_output
        self.ghostscript_binary = ghostscript_binary or _find_binary("gs") or _find_binary("gswin64c") or _find_binary("gswin32c")
        self.qpdf_binary = qpdf_binary or _find_binary("qpdf")

    def compress(
        self,
        *,
        source_path: str | Path,
        compression_level: str | Any = "balanced",
        output_filename: str = "compressed-document.pdf",
        job_id: Optional[str] = None,
    ) -> CompressedPdfArtifact:
        source = _require_pdf_path(source_path)
        level = _compression_level_value(compression_level)
        output_name = _normalize_pdf_filename(output_filename, default="compressed-document.pdf")
        original_size_mb = _get_file_size_mb(source)
        timeout_seconds = _nonnegative_int_env(
            "PDF_COMPRESSION_JOB_TIMEOUT_SECONDS",
            15 * 60,
        )
        deadline = time.monotonic() + timeout_seconds if timeout_seconds else None

        with TemporaryDirectory(prefix="redocx-compress-") as workdir:
            workdir_path = Path(workdir)
            output_path = workdir_path / output_name
            engine = self._compress_to_path(
                source,
                output_path=output_path,
                level=level,
                job_id=job_id,
                deadline=deadline,
            )

            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("PDF compression completed without producing a valid output file.")

            # If compression produces a larger file, use an optimized copy of the
            # original unless explicitly configured otherwise.
            if not self.allow_larger_output and output_path.stat().st_size > source.stat().st_size:
                if engine.startswith("pymupdf-"):
                    shutil.copy2(source, output_path)
                    engine = "original-preserved-no-smaller-output"
                else:
                    fallback_path = workdir_path / f"{Path(output_name).stem}-optimized.pdf"
                    try:
                        fallback_engine = self._pymupdf_optimize(
                            source,
                            output_path=fallback_path,
                            level=level,
                            job_id=job_id,
                            deadline=deadline,
                        )
                    except Exception:
                        logger.warning(
                            "Optimized fallback failed; preserving the original PDF.",
                            exc_info=True,
                        )
                        shutil.copy2(source, output_path)
                        engine = "original-preserved-no-smaller-output"
                    else:
                        if fallback_path.stat().st_size <= source.stat().st_size:
                            output_path = fallback_path
                            engine = fallback_engine
                        else:
                            shutil.copy2(source, output_path)
                            engine = "original-preserved-no-smaller-output"

            self._validate_output_pdf_isolated(
                source,
                output_path=output_path,
                job_id=job_id,
                deadline=deadline,
            )

            persisted_path, storage_key, download_url = _persist_or_copy(
                output_path,
                output_name=output_name,
                storage_backend=self.storage_backend,
                artifacts_dir=self.artifacts_dir,
            )

        compressed_size_mb = _get_file_size_mb(persisted_path)
        ratio = round(compressed_size_mb / original_size_mb, 4) if original_size_mb > 0 else None

        return CompressedPdfArtifact(
            file_name=output_name,
            file_extension="pdf",
            file_size_mb=compressed_size_mb,
            file_path=str(persisted_path),
            compression_level=level,
            original_file_size_mb=original_size_mb,
            compressed_file_size_mb=compressed_size_mb,
            estimated_output_file_size_mb=None,
            compression_ratio=ratio,
            engine=engine,
            storage_key=storage_key,
            download_url=download_url,
        )

    def _compress_to_path(
        self,
        source_path: Path,
        *,
        output_path: Path,
        level: str,
        job_id: Optional[str] = None,
        deadline: Optional[float] = None,
    ) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # PyMuPDF produced the supplied reference outputs and gives each profile
        # an explicit DPI threshold, target DPI, and image quality. Prefer it so
        # profile behavior does not change merely because Ghostscript happens to
        # be installed on one host but not another.
        try:
            return self._pymupdf_optimize(
                source_path,
                output_path=output_path,
                level=level,
                job_id=job_id,
                deadline=deadline,
            )
        except CompressionChildLimitError:
            raise
        except Exception:
            logger.warning(
                "PyMuPDF compression failed; trying Ghostscript fallback.",
                exc_info=True,
            )
            output_path.unlink(missing_ok=True)

        if self.ghostscript_binary:
            try:
                self._ghostscript_compress(
                    source_path,
                    output_path=output_path,
                    level=level,
                    deadline=deadline,
                )
                return "ghostscript"
            except Exception:
                logger.warning(
                    "Ghostscript compression fallback failed.",
                    exc_info=True,
                )
                output_path.unlink(missing_ok=True)

        # qpdf performs structural optimization but cannot honor image-quality
        # levels, so it is intentionally not used for a level-aware request.
        raise RuntimeError(
            "No level-aware PDF compression engine could process this document."
        )

    def _ghostscript_compress(
        self,
        source_path: Path,
        *,
        output_path: Path,
        level: str,
        deadline: Optional[float] = None,
    ) -> None:
        if not self.ghostscript_binary:
            raise RuntimeError("Ghostscript binary not configured.")

        settings = {
            "small_file": "/screen",
            "balanced": "/ebook",
            "high_quality": "/printer",
        }[level]

        # The image downsampling switches make the levels meaningful while keeping
        # the command deterministic and safe for server execution.
        if level == "small_file":
            image_dpi = "96"
        elif level == "balanced":
            image_dpi = "150"
        else:
            image_dpi = "300"

        cmd = [
            self.ghostscript_binary,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.6",
            f"-dPDFSETTINGS={settings}",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            "-dSAFER",
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dColorImageResolution={image_dpi}",
            f"-dGrayImageResolution={image_dpi}",
            f"-dMonoImageResolution={image_dpi}",
            f"-sOutputFile={output_path}",
            str(source_path),
        ]
        _run_managed_subprocess(cmd, deadline=deadline)

    def _qpdf_linearize(
        self,
        source_path: Path,
        *,
        output_path: Path,
        deadline: Optional[float] = None,
    ) -> None:
        if not self.qpdf_binary:
            raise RuntimeError("qpdf binary not configured.")
        cmd = [
            self.qpdf_binary,
            "--linearize",
            "--object-streams=generate",
            str(source_path),
            str(output_path),
        ]
        _run_managed_subprocess(cmd, deadline=deadline)

    @staticmethod
    def _pymupdf_optimize(
        source_path: Path,
        *,
        output_path: Path,
        level: str,
        job_id: Optional[str] = None,
        deadline: Optional[float] = None,
    ) -> str:
        """Run native PyMuPDF work in a disposable child process.

        Keeping the native image rewrite outside the API process ensures that a
        native crash, timeout, or configured RSS ceiling terminates only this
        compression attempt. The resulting PDF remains in the parent's temporary
        work directory for validation and persistence.
        """
        result_path = output_path.with_suffix(f"{output_path.suffix}.result.json")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--pymupdf-child",
            "--source",
            str(source_path),
            "--output",
            str(output_path),
            "--level",
            level,
            "--result",
            str(result_path),
        ]
        if job_id:
            command.extend(["--job-id", str(job_id)])
        command.extend(
            ["--memory-limit-mb", str(_configured_child_memory_limit_mb())]
        )

        try:
            _run_managed_subprocess(command, deadline=deadline)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if payload.get("ok") is not True:
                raise RuntimeError(
                    str(payload.get("error") or "PyMuPDF child process failed.")
                )
            if Path(str(payload.get("output_path") or "")).resolve() != output_path.resolve():
                raise RuntimeError("PyMuPDF child returned an unexpected output path.")
            engine = str(payload.get("engine") or "").strip()
            if not engine:
                raise RuntimeError("PyMuPDF child did not report its compression engine.")
            return engine
        finally:
            result_path.unlink(missing_ok=True)

    @staticmethod
    def _pymupdf_optimize_in_child(
        source_path: Path,
        *,
        output_path: Path,
        level: str,
    ) -> str:
        import fitz  # PyMuPDF is loaded only inside the disposable child.

        settings = {
            "small_file": {"dpi_threshold": 110, "dpi_target": 96, "quality": 55},
            "balanced": {"dpi_threshold": 180, "dpi_target": 150, "quality": 75},
            "high_quality": {"dpi_threshold": 330, "dpi_target": 300, "quality": 90},
        }[level]

        with fitz.open(source_path) as pdf:
            _reject_encrypted_pdf(pdf, source_path)

            rewrite_images = getattr(pdf, "rewrite_images", None)
            if not callable(rewrite_images):
                raise RuntimeError(
                    "The installed PyMuPDF version does not support level-aware image compression."
                )

            rewrite_images(
                dpi_threshold=settings["dpi_threshold"],
                dpi_target=settings["dpi_target"],
                quality=settings["quality"],
                lossy=True,
                lossless=True,
                bitonal=True,
                color=True,
                gray=True,
            )

            subset_fonts = getattr(pdf, "subset_fonts", None)
            if callable(subset_fonts):
                subset_fonts(verbose=False)

            pdf.save(
                output_path,
                garbage=4,
                deflate=True,
                deflate_images=True,
                deflate_fonts=True,
                clean=True,
            )
        return f"pymupdf-images-{level}"

    @staticmethod
    def _validate_output_pdf_isolated(
        source_path: Path,
        *,
        output_path: Path,
        job_id: Optional[str] = None,
        deadline: Optional[float] = None,
    ) -> None:
        """Validate both PDFs without loading PyMuPDF into the API process."""
        result_path = output_path.with_suffix(f"{output_path.suffix}.validation.json")
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--validate-child",
            "--source",
            str(source_path),
            "--output",
            str(output_path),
            "--result",
            str(result_path),
        ]
        if job_id:
            command.extend(["--job-id", str(job_id)])
        command.extend(
            ["--memory-limit-mb", str(_configured_child_memory_limit_mb())]
        )

        try:
            _run_managed_subprocess(command, deadline=deadline)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if payload.get("ok") is not True:
                raise RuntimeError(
                    str(payload.get("error") or "PDF validation child failed.")
                )
            if str(payload.get("job_id") or "") != str(job_id or ""):
                raise RuntimeError("PDF validation child returned an unexpected job id.")
        finally:
            result_path.unlink(missing_ok=True)


# Public convenience API -----------------------------------------------------


def compress_pdf(
    source_path: str | Path,
    *,
    compression_level: str | Any = "balanced",
    output_filename: str = "compressed-document.pdf",
    storage_backend: Optional[StorageBackend] = None,
    artifacts_dir: str | Path = "artifacts/pdf_tools/compress",
    allow_larger_output: bool = False,
    ghostscript_binary: Optional[str] = None,
    qpdf_binary: Optional[str] = None,
    job_id: Optional[str] = None,
) -> CompressedPdfArtifact:
    backend = LocalPdfCompressionBackend(
        storage_backend=storage_backend,
        artifacts_dir=artifacts_dir,
        allow_larger_output=allow_larger_output,
        ghostscript_binary=ghostscript_binary,
        qpdf_binary=qpdf_binary,
    )
    return backend.compress(
        source_path=source_path,
        compression_level=compression_level,
        output_filename=output_filename,
        job_id=job_id,
    )


# Internal helpers -----------------------------------------------------------


def _compression_level_value(value: str | Any) -> str:
    raw = getattr(value, "value", value)
    normalized = str(raw).strip().lower()
    allowed = {"small_file", "balanced", "high_quality"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported compression level: {raw}. Expected one of: {', '.join(sorted(allowed))}.")
    return normalized


def estimate_compressed_size_mb(original_size_mb: float, compression_level: str | Any) -> float:
    """Heuristic used for UI estimates before actual worker completion."""
    level = _compression_level_value(compression_level)
    factor = {"small_file": 0.35, "balanced": 0.55, "high_quality": 0.75}[level]
    return round(max(0.01, original_size_mb * factor), 4)


def _require_pdf_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF source not found: {path}")
    if not path.is_file():
        raise ValueError(f"PDF source is not a file: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF source must end with .pdf: {path.name}")
    return path


def _reject_encrypted_pdf(document: Any, source_path: Path) -> None:
    if bool(getattr(document, "needs_pass", False)) or bool(getattr(document, "is_encrypted", False)):
        raise ValueError(f"Password-protected or encrypted PDFs are not supported yet: {source_path.name}")


def _validate_output_pdf(source_path: Path, output_path: Path) -> None:
    """Reject unreadable or structurally incomplete compression outputs."""
    import fitz  # PyMuPDF is loaded only inside the disposable child.

    try:
        with fitz.open(source_path) as source, fitz.open(output_path) as output:
            _reject_encrypted_pdf(output, output_path)
            if output.page_count < 1:
                raise RuntimeError("Compressed PDF has no pages.")
            if output.page_count != source.page_count:
                raise RuntimeError(
                    "Compressed PDF page count does not match the source document."
                )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("Compressed PDF is unreadable or invalid.") from exc


def _normalize_pdf_filename(value: str | None, *, default: str) -> str:
    raw = (value or default).strip() or default
    stem = _safe_stem(Path(raw).stem)
    return f"{stem}.pdf"


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value).strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-._")
    return cleaned or "compressed-document"


def _get_file_size_mb(path: str | Path) -> float:
    file_path = Path(path)
    size = file_path.stat().st_size / (1024 * 1024)
    if size <= 0:
        raise ValueError(f"Generated PDF is empty: {file_path}")
    return round(size, 4)


def _guess_content_type(path: str | Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/pdf"


def _find_binary(name: str) -> Optional[str]:
    return shutil.which(name)


def _nonnegative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return max(0, int(default))
    try:
        return max(0, int(raw))
    except ValueError:
        return max(0, int(default))


def _process_rss_bytes(process_id: int) -> Optional[int]:
    """Return Linux resident memory for one child process when available."""
    try:
        status = Path(f"/proc/{process_id}/status").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _container_memory_limit_bytes() -> Optional[int]:
    """Read a finite Linux cgroup memory limit when the runtime exposes one."""
    candidates = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    for candidate in candidates:
        try:
            raw = candidate.read_text(encoding="utf-8").strip().lower()
        except (OSError, UnicodeError):
            continue
        if not raw or raw == "max":
            continue
        try:
            limit = int(raw)
        except ValueError:
            continue
        # cgroup v1 may expose a near-int64 sentinel when no limit is applied.
        if 0 < limit < 1 << 60:
            return limit
    return None


def _default_child_memory_limit_mb() -> int:
    container_limit = _container_memory_limit_bytes()
    if container_limit is None:
        return 512
    # Reserve half the container for Uvicorn, ClamAV, SQLite, upload handling,
    # and normal application overhead. Operators can override this explicitly.
    return max(64, int(container_limit * 0.5 / (1024 * 1024)))


def _configured_child_memory_limit_mb() -> int:
    return _nonnegative_int_env(
        "PDF_COMPRESSION_CHILD_MEMORY_LIMIT_MB",
        _default_child_memory_limit_mb(),
    )


def _apply_process_memory_limit(process_id: int, memory_limit_mb: int) -> bool:
    if memory_limit_mb <= 0:
        return False
    try:
        import resource

        memory_limit_bytes = memory_limit_mb * 1024 * 1024
        resource.prlimit(
            process_id,
            resource.RLIMIT_AS,
            (memory_limit_bytes, memory_limit_bytes),
        )
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _apply_current_process_memory_limit(memory_limit_mb: int) -> None:
    if memory_limit_mb <= 0:
        return
    try:
        import resource

        memory_limit_bytes = memory_limit_mb * 1024 * 1024
        resource.setrlimit(
            resource.RLIMIT_AS,
            (memory_limit_bytes, memory_limit_bytes),
        )
    except (ImportError, OSError, ValueError) as exc:
        raise RuntimeError("Could not apply the compression child memory limit.") from exc


def _run_managed_subprocess(
    command: list[str],
    *,
    deadline: Optional[float] = None,
) -> None:
    timeout_seconds = _nonnegative_int_env(
        "PDF_COMPRESSION_JOB_TIMEOUT_SECONDS",
        15 * 60,
    )
    memory_limit_mb = _configured_child_memory_limit_mb()
    memory_limit_bytes = memory_limit_mb * 1024 * 1024
    started_at = time.monotonic()
    effective_deadline = deadline
    if effective_deadline is None and timeout_seconds:
        effective_deadline = started_at + timeout_seconds
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    _apply_process_memory_limit(process.pid, memory_limit_mb)
    termination_reason: Optional[str] = None

    try:
        while process.poll() is None:
            if effective_deadline is not None and time.monotonic() > effective_deadline:
                termination_reason = "Compression job exceeded its configured timeout."
                process.kill()
                break

            if memory_limit_bytes:
                rss_bytes = _process_rss_bytes(process.pid)
                if rss_bytes is not None and rss_bytes > memory_limit_bytes:
                    termination_reason = (
                        "Compression child exceeded its configured "
                        f"{memory_limit_mb} MB memory limit."
                    )
                    process.kill()
                    break
            time.sleep(0.1)

        _, stderr = process.communicate()
    except Exception:
        process.kill()
        process.communicate()
        raise

    if termination_reason:
        raise CompressionChildLimitError(termination_reason)
    if process.returncode != 0:
        detail = (stderr or "").strip().splitlines()
        message = detail[-1] if detail else "Compression child process failed."
        raise RuntimeError(message)


def _persist_or_copy(
    source_path: Path,
    *,
    output_name: str,
    storage_backend: Optional[StorageBackend],
    artifacts_dir: str | Path,
) -> tuple[Path, Optional[str], Optional[str]]:
    if storage_backend is None:
        storage_backend = _try_default_storage(base_dir=str(artifacts_dir))

    if storage_backend is not None:
        stored = storage_backend.persist(
            source_file_path=str(source_path),
            artifact_name=output_name,
            content_type=_guess_content_type(source_path),
        )
        stored_path = Path(getattr(stored, "stored_path", source_path)).resolve()
        return stored_path, getattr(stored, "storage_key", None), getattr(stored, "download_url", None)

    artifacts = Path(artifacts_dir).expanduser().resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    destination = artifacts / output_name
    if destination.exists():
        destination = artifacts / f"{Path(output_name).stem}-{os.urandom(4).hex()}{Path(output_name).suffix}"
    shutil.copy2(source_path, destination)
    return destination, None, None


def _try_default_storage(*, base_dir: str) -> Optional[StorageBackend]:
    try:
        from backend.src.storage.artifacts import LocalArtifactStorage  # type: ignore

        return LocalArtifactStorage(base_dir=base_dir)
    except Exception:
        return None


def _run_pymupdf_child(arguments: argparse.Namespace) -> int:
    result_path = Path(arguments.result).expanduser().resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any]
    try:
        _apply_current_process_memory_limit(int(arguments.memory_limit_mb or 0))
        output_path = Path(arguments.output).expanduser().resolve()
        engine = LocalPdfCompressionBackend._pymupdf_optimize_in_child(
            Path(arguments.source).expanduser().resolve(),
            output_path=output_path,
            level=_compression_level_value(arguments.level),
        )
        payload = {
            "ok": True,
            "job_id": str(arguments.job_id or ""),
            "engine": engine,
            "output_path": str(output_path),
        }
        exit_code = 0
    except BaseException as exc:  # The child must always provide a small verdict.
        payload = {
            "ok": False,
            "job_id": str(arguments.job_id or ""),
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1

    result_path.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )
    return exit_code


def _run_validation_child(arguments: argparse.Namespace) -> int:
    result_path = Path(arguments.result).expanduser().resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any]
    try:
        _apply_current_process_memory_limit(int(arguments.memory_limit_mb or 0))
        _validate_output_pdf(
            Path(arguments.source).expanduser().resolve(),
            Path(arguments.output).expanduser().resolve(),
        )
        payload = {
            "ok": True,
            "job_id": str(arguments.job_id or ""),
        }
        exit_code = 0
    except BaseException as exc:  # The child must always provide a small verdict.
        payload = {
            "ok": False,
            "job_id": str(arguments.job_id or ""),
            "error": f"{type(exc).__name__}: {exc}",
        }
        exit_code = 1

    result_path.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )
    return exit_code


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--pymupdf-child", action="store_true")
    parser.add_argument("--validate-child", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--output")
    parser.add_argument("--level")
    parser.add_argument("--result")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--memory-limit-mb", type=int, default=0)
    arguments = parser.parse_args()
    if arguments.pymupdf_child == arguments.validate_child:
        parser.error("Choose exactly one isolated compression child mode.")
    required_fields = ["source", "output", "result"]
    if arguments.pymupdf_child:
        required_fields.append("level")
    for field_name in required_fields:
        if not getattr(arguments, field_name):
            parser.error(f"--{field_name.replace('_', '-')} is required.")
    if arguments.pymupdf_child:
        return _run_pymupdf_child(arguments)
    return _run_validation_child(arguments)


__all__ = [
    "CompressedPdfArtifact",
    "LocalPdfCompressionBackend",
    "compress_pdf",
    "estimate_compressed_size_mb",
]


if __name__ == "__main__":
    raise SystemExit(_main())