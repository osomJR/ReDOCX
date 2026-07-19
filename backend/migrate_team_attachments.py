from __future__ import annotations

"""One-time migration for legacy plaintext team-message attachments.

Run from the backend project root after migration 013 and after configuring the
team attachment encryption key. Plaintext deletion is opt-in and occurs only
after the encrypted database transaction commits successfully.
"""

import argparse
import logging
import os
from pathlib import Path
import sys

from psycopg.types.json import Jsonb

from backend.database import get_db
from backend.team_attachment_security import (
    TeamAttachmentSecurityError,
    prepare_team_attachment_from_stream,
)


logger = logging.getLogger("migrate_team_attachments")


def _legacy_root() -> Path:
    configured = os.getenv("TEAM_ATTACHMENT_STORAGE_DIR", "storage/team_attachments").strip()
    root = Path(configured or "storage/team_attachments").expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"Legacy team attachment root does not exist: {root}")
    return root


def _safe_legacy_path(root: Path, storage_key: str) -> Path:
    relative = Path(storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("Legacy attachment path is missing or unsafe.")
    unresolved = root / relative
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimeError("Legacy attachment path is missing or unsafe.")
    candidate = unresolved.resolve()
    if root not in candidate.parents or not candidate.is_file():
        raise RuntimeError("Legacy attachment path is missing or unsafe.")
    return candidate


def _next_legacy_rows(limit: int, attachment_id: int | None) -> list[dict]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, message_id, conversation_id, organization_id,
                       uploaded_by_user_id, original_filename, storage_key
                FROM conversation_message_attachments
                WHERE security_status = 'legacy_unverified'
                  AND (%s::BIGINT IS NULL OR id = %s::BIGINT)
                ORDER BY id ASC
                LIMIT %s
                """,
                (attachment_id, attachment_id, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "message_id": row[1],
            "conversation_id": row[2],
            "organization_id": row[3],
            "uploaded_by_user_id": row[4],
            "original_filename": row[5],
            "storage_key": row[6],
        }
        for row in rows
    ]


def _persist_secured_legacy_attachment(row: dict, prepared) -> bool:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE conversation_message_attachments
                SET kind = %s,
                    original_filename = %s,
                    stored_filename = %s,
                    storage_key = %s,
                    content_type = %s,
                    file_size_bytes = %s,
                    checksum_sha256 = %s,
                    storage_backend = 'postgres_encrypted',
                    security_status = 'secured',
                    malware_scan_status = 'clean',
                    malware_scanner = %s,
                    malware_scanner_version = %s,
                    scan_completed_at = %s,
                    detected_content_type = %s,
                    validation_version = %s,
                    encryption_algorithm = %s,
                    encryption_key_id = %s,
                    encryption_nonce = %s,
                    encryption_aad_version = %s,
                    encrypted_content = %s,
                    secured_at = %s,
                    security_metadata = %s
                WHERE id = %s
                  AND security_status = 'legacy_unverified'
                """,
                (
                    prepared.kind,
                    prepared.original_filename,
                    prepared.stored_filename,
                    prepared.storage_key,
                    prepared.content_type,
                    prepared.file_size_bytes,
                    prepared.checksum_sha256,
                    prepared.malware_scanner,
                    prepared.malware_scanner_version,
                    prepared.scan_completed_at,
                    prepared.detected_content_type,
                    prepared.validation_version,
                    prepared.encryption_algorithm,
                    prepared.encryption_key_id,
                    prepared.encryption_nonce,
                    prepared.encryption_aad_version,
                    prepared.encrypted_content,
                    prepared.scan_completed_at,
                    Jsonb(prepared.security_metadata),
                    row["id"],
                ),
            )
            if cur.rowcount != 1:
                return False

            cur.execute(
                """
                INSERT INTO team_attachment_security_events (
                    organization_id, conversation_id, message_id,
                    attachment_id, actor_user_id, action, outcome,
                    reason_code, request_id, details
                )
                VALUES (%s, %s, %s, %s, %s, 'legacy_migration',
                        'succeeded', 'secured', %s, %s)
                """,
                (
                    row["organization_id"],
                    row["conversation_id"],
                    row["message_id"],
                    row["id"],
                    row["uploaded_by_user_id"],
                    f"legacy-migration:{row['id']}",
                    Jsonb(
                        {
                            "file_size_bytes": prepared.file_size_bytes,
                            "validation_version": prepared.validation_version,
                        }
                    ),
                ),
            )
    return True


def migrate_one(root: Path, row: dict, *, delete_plaintext: bool) -> str:
    legacy_path = _safe_legacy_path(root, str(row["storage_key"]))
    with legacy_path.open("rb") as stream:
        prepared = prepare_team_attachment_from_stream(
            stream=stream,
            filename=str(row["original_filename"]),
            organization_id=int(row["organization_id"]),
            conversation_id=int(row["conversation_id"]),
            uploaded_by_user_id=str(row["uploaded_by_user_id"]),
        )

    if not _persist_secured_legacy_attachment(row, prepared):
        return "skipped"

    if delete_plaintext:
        # The explicit, fully resolved path was validated above; deletion occurs
        # only after the encrypted DB transaction committed.
        legacy_path.unlink()
    return "migrated"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--attachment-id", type=int)
    parser.add_argument(
        "--delete-plaintext-after-commit",
        action="store_true",
        help="Delete each legacy plaintext file only after its encrypted row commits.",
    )
    args = parser.parse_args()
    limit = max(1, min(args.limit, 10_000))
    root = _legacy_root()
    rows = _next_legacy_rows(limit, args.attachment_id)

    migrated = 0
    skipped = 0
    failed = 0
    for row in rows:
        try:
            result = migrate_one(
                root,
                row,
                delete_plaintext=args.delete_plaintext_after_commit,
            )
            if result == "migrated":
                migrated += 1
            else:
                skipped += 1
        except TeamAttachmentSecurityError as exc:
            failed += 1
            logger.error("Attachment %s was not migrated: %s", row["id"], exc.code)
        except Exception:
            failed += 1
            logger.exception("Attachment %s migration failed.", row["id"])

    logger.info(
        "Legacy team attachment migration finished: migrated=%s skipped=%s failed=%s",
        migrated,
        skipped,
        failed,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
