from __future__ import annotations

"""Durable, owner-scoped background queue for PDF compression jobs."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional
import json
import logging
import os
import sqlite3
import threading
import time
import uuid


logger = logging.getLogger(__name__)

CompressionProcessor = Callable[..., Any]


class PersistentCompressionJobQueue:
    """Run compression off-request while persisting pollable job state.

    SQLite provides cross-thread and cross-process visibility without adding a
    new infrastructure dependency. Job claiming is atomic, so multiple API
    workers can share the same database on the application's persistent volume.
    Interrupted ``processing`` jobs are returned to ``queued`` during startup.
    """

    def __init__(
        self,
        *,
        processor: CompressionProcessor,
        database_path: str | Path,
        algorithm_version: Optional[str] = None,
        max_workers: int = 2,
        retention_seconds: int = 24 * 60 * 60,
        lease_seconds: int = 120,
    ) -> None:
        self.processor = processor
        self.database_path = Path(database_path).expanduser().resolve()
        self.algorithm_version = algorithm_version
        self.max_workers = max(1, int(max_workers))
        self.retention_seconds = max(60, int(retention_seconds))
        self.lease_seconds = max(30, int(lease_seconds))
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._stop_event = threading.Event()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="redocx-pdf-compression",
        )
        self._recover_pending_jobs()
        self._sweeper = threading.Thread(
            target=self._sweep_expired_leases,
            name="redocx-pdf-compression-recovery",
            daemon=True,
        )
        self._sweeper.start()

    def enqueue_compress_pdf(
        self,
        *,
        request: Any,
        source_path: str,
        output_filename: str,
        compression_level: str,
        owner_id: str,
    ) -> str:
        normalized_owner = self._require_nonempty(owner_id, "owner_id")
        normalized_level = self._require_nonempty(
            compression_level,
            "compression_level",
        )
        normalized_output = self._require_nonempty(
            output_filename,
            "output_filename",
        )
        source = Path(source_path).expanduser().resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Compression source not found: {source}")

        original_size_mb = float(request.input.metadata.file_size_mb)
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO compression_jobs (
                    job_id,
                    owner_id,
                    source_path,
                    output_filename,
                    compression_level,
                    original_size_mb,
                    status,
                    message,
                    result_json,
                    algorithm_version,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, NULL, ?, ?, ?)
                """,
                (
                    job_id,
                    normalized_owner,
                    str(source),
                    normalized_output,
                    normalized_level,
                    original_size_mb,
                    "Compression job queued.",
                    self.algorithm_version,
                    now,
                    now,
                ),
            )
        self._discard_expired_jobs()
        self._submit(job_id)
        return job_id

    def get_compression_job(
        self,
        *,
        job_id: str,
        owner_id: str,
    ) -> Mapping[str, Any] | None:
        normalized_job_id = self._require_nonempty(job_id, "job_id")
        normalized_owner = self._require_nonempty(owner_id, "owner_id")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_id, status, message, result_json, algorithm_version
                FROM compression_jobs
                WHERE job_id = ? AND owner_id = ?
                """,
                (normalized_job_id, normalized_owner),
            ).fetchone()

        if row is None:
            return None

        result = json.loads(row[3]) if row[3] else None
        return {
            "job_id": row[0],
            "status": row[1],
            "message": row[2],
            "result": result,
            "meta": {
                "deterministic": True,
                "contract_version": "v1",
                "algorithm_version": row[4],
            },
        }

    def close(self, *, wait: bool = True) -> None:
        self._stop_event.set()
        self._sweeper.join(timeout=5)
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS compression_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    output_filename TEXT NOT NULL,
                    compression_level TEXT NOT NULL,
                    original_size_mb REAL NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    result_json TEXT,
                    algorithm_version TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lease_expires_at REAL
                )
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(compression_jobs)"
                ).fetchall()
            }
            if "lease_expires_at" not in columns:
                connection.execute(
                    "ALTER TABLE compression_jobs ADD COLUMN lease_expires_at REAL"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_compression_jobs_owner
                ON compression_jobs (owner_id, job_id)
                """
            )
        self._recover_expired_jobs()
        self._discard_expired_jobs()

    def _recover_pending_jobs(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT job_id FROM compression_jobs WHERE status = 'queued'"
            ).fetchall()
        for row in rows:
            self._submit(str(row[0]))

    def _submit(self, job_id: str) -> None:
        future = self._executor.submit(self._run_job, job_id)
        future.add_done_callback(self._log_unexpected_worker_failure)

    def _run_job(self, job_id: str) -> None:
        if not self._claim_job(job_id):
            return

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT source_path, output_filename, compression_level,
                       original_size_mb
                FROM compression_jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()

        if row is None:
            return

        heartbeat_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._maintain_lease,
            args=(job_id, heartbeat_stop),
            name=f"redocx-pdf-compression-lease-{job_id[:8]}",
            daemon=True,
        )
        heartbeat.start()

        try:
            result = self.processor(
                source_path=row[0],
                output_filename=row[1],
                compression_level=row[2],
                original_file_size_mb=float(row[3]),
            )
            if hasattr(result, "model_dump"):
                result = result.model_dump(mode="json")
            if not isinstance(result, Mapping):
                raise TypeError("Compression processor must return a result mapping.")
            serialized_result = json.dumps(dict(result), separators=(",", ":"))
            self._complete_job(job_id, serialized_result)
        except Exception:
            logger.exception("PDF compression job %s failed.", job_id)
            self._fail_job(job_id)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=5)

    def _claim_job(self, job_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE compression_jobs
                SET status = 'processing',
                    message = 'Compressing PDF.',
                    updated_at = ?,
                    lease_expires_at = ?
                WHERE job_id = ? AND status = 'queued'
                """,
                (time.time(), time.time() + self.lease_seconds, job_id),
            )
            return cursor.rowcount == 1

    def _complete_job(self, job_id: str, result_json: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE compression_jobs
                SET status = 'completed',
                    message = 'Compression completed.',
                    result_json = ?,
                    updated_at = ?,
                    lease_expires_at = NULL
                WHERE job_id = ?
                """,
                (result_json, time.time(), job_id),
            )

    def _fail_job(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE compression_jobs
                SET status = 'failed',
                    message = 'PDF compression failed.',
                    updated_at = ?,
                    lease_expires_at = NULL
                WHERE job_id = ?
                """,
                (time.time(), job_id),
            )

    def _discard_expired_jobs(self) -> None:
        cutoff = time.time() - self.retention_seconds
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM compression_jobs
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND updated_at < ?
                """,
                (cutoff,),
            )

    def _maintain_lease(self, job_id: str, stop_event: threading.Event) -> None:
        interval = max(10, self.lease_seconds // 3)
        while not stop_event.wait(interval):
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE compression_jobs
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'processing'
                    """,
                    (time.time() + self.lease_seconds, time.time(), job_id),
                )

    def _recover_expired_jobs(self) -> list[str]:
        now = time.time()
        recovered: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT job_id
                FROM compression_jobs
                WHERE status = 'processing'
                  AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                """,
                (now,),
            ).fetchall()
            for row in rows:
                job_id = str(row[0])
                cursor = connection.execute(
                    """
                    UPDATE compression_jobs
                    SET status = 'queued',
                        message = 'Compression job recovered after worker interruption.',
                        updated_at = ?,
                        lease_expires_at = NULL
                    WHERE job_id = ?
                      AND status = 'processing'
                      AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                    """,
                    (now, job_id, now),
                )
                if cursor.rowcount == 1:
                    recovered.append(job_id)
        return recovered

    def _sweep_expired_leases(self) -> None:
        interval = max(10, min(30, self.lease_seconds // 3))
        while not self._stop_event.wait(interval):
            try:
                for job_id in self._recover_expired_jobs():
                    self._submit(job_id)
                self._discard_expired_jobs()
            except Exception:
                logger.exception("Failed to recover expired PDF compression jobs.")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            isolation_level="DEFERRED",
        )
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _require_nonempty(value: Any, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required.")
        return normalized

    @staticmethod
    def _log_unexpected_worker_failure(future: Future[Any]) -> None:
        try:
            future.result()
        except Exception:
            logger.exception("Unexpected PDF compression worker failure.")


def compression_queue_from_environment(
    *,
    processor: CompressionProcessor,
    algorithm_version: Optional[str],
) -> PersistentCompressionJobQueue:
    default_database_path = (
        Path(os.getenv("ARTIFACT_STORAGE_DIR", "artifacts"))
        / "pdf_tools"
        / "compression_jobs.sqlite3"
    )
    return PersistentCompressionJobQueue(
        processor=processor,
        database_path=os.getenv(
            "PDF_COMPRESSION_JOB_DB",
            str(default_database_path),
        ),
        algorithm_version=algorithm_version,
        max_workers=int(os.getenv("PDF_COMPRESSION_JOB_WORKERS", "2")),
        retention_seconds=int(
            os.getenv("PDF_COMPRESSION_JOB_RETENTION_SECONDS", str(24 * 60 * 60))
        ),
        lease_seconds=int(os.getenv("PDF_COMPRESSION_JOB_LEASE_SECONDS", "120")),
    )


__all__ = [
    "PersistentCompressionJobQueue",
    "compression_queue_from_environment",
]