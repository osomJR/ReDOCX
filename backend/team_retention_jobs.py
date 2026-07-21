from __future__ import annotations

"""Automated tenant communication retention.

Run from the production scheduler (for example hourly or daily) after applying
016_team_enterprise_hardening.sql. Legal-hold tenants are skipped. Deletions are
bounded and use SKIP LOCKED so multiple schedulers cannot process the same rows.
Database triggers append immutable audit events before records disappear.
"""

import argparse
import logging
from typing import Any

from psycopg import errors as psycopg_errors

from backend.database import get_db

logger = logging.getLogger(__name__)


def _eligible_policies(limit: int) -> list[tuple[int, int, int]]:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT organization_id, message_retention_days,
                       attachment_retention_days
                FROM organization_communication_policies
                WHERE legal_hold = FALSE
                ORDER BY organization_id
                LIMIT %s
                """,
                (limit,),
            )
            return [(int(row[0]), int(row[1]), int(row[2])) for row in cur.fetchall()]


def _delete_attachment_batch(
    conn,
    *,
    organization_id: int,
    retention_days: int,
    batch_size: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
                SELECT id
                FROM conversation_message_attachments
                WHERE organization_id = %s
                  AND created_at < NOW() - MAKE_INTERVAL(days => %s)
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            DELETE FROM conversation_message_attachments attachment
            USING candidates
            WHERE attachment.id = candidates.id
            RETURNING attachment.id
            """,
            (organization_id, retention_days, batch_size),
        )
        return len(cur.fetchall())


def _delete_message_batch(
    conn,
    *,
    organization_id: int,
    retention_days: int,
    batch_size: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH candidates AS (
                SELECT id
                FROM conversation_messages
                WHERE organization_id = %s
                  AND created_at < NOW() - MAKE_INTERVAL(days => %s)
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            DELETE FROM conversation_messages message
            USING candidates
            WHERE message.id = candidates.id
            RETURNING message.id
            """,
            (organization_id, retention_days, batch_size),
        )
        return len(cur.fetchall())


def _locked_policy(conn, organization_id: int) -> tuple[bool, int, int] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT legal_hold, message_retention_days,
                   attachment_retention_days
            FROM organization_communication_policies
            WHERE organization_id = %s
            FOR SHARE
            """,
            (organization_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return bool(row[0]), int(row[1]), int(row[2])


def apply_retention_for_organization(
    organization_id: int,
    *,
    message_retention_days: int,
    attachment_retention_days: int,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Apply one tenant's policy in short, restart-safe transactions.

    The passed retention values are scheduler hints only; every batch reloads
    the authoritative database policy so a newly enabled legal hold stops the
    run before another delete transaction begins.
    """

    del message_retention_days, attachment_retention_days
    messages_deleted = 0
    attachments_deleted = 0
    run_id: int | None = None
    stopped_reason: str | None = None

    try:
        try:
            with get_db() as conn:
                policy = _locked_policy(conn, organization_id)
                if policy is None or policy[0]:
                    return {
                        "organization_id": organization_id,
                        "skipped": True,
                        "reason": "legal_hold" if policy else "policy_missing",
                    }
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE team_retention_runs
                        SET status = 'failed', completed_at = NOW(),
                            error = COALESCE(error, 'Retention worker lease expired')
                        WHERE organization_id = %s
                          AND status = 'running'
                          AND started_at < NOW() - INTERVAL '6 hours'
                        """,
                        (organization_id,),
                    )
                    cur.execute(
                        """
                        INSERT INTO team_retention_runs (organization_id)
                        VALUES (%s)
                        RETURNING id
                        """,
                        (organization_id,),
                    )
                    run_id = int(cur.fetchone()[0])
        except psycopg_errors.UniqueViolation:
            return {
                "organization_id": organization_id,
                "skipped": True,
                "reason": "retention_run_already_active",
            }

        while True:
            with get_db() as conn:
                policy = _locked_policy(conn, organization_id)
                if policy is None:
                    raise RuntimeError("Organization communication policy disappeared during retention.")
                legal_hold, _, current_attachment_days = policy
                if legal_hold:
                    stopped_reason = "legal_hold_enabled"
                    break
                deleted = _delete_attachment_batch(
                    conn,
                    organization_id=organization_id,
                    retention_days=current_attachment_days,
                    batch_size=batch_size,
                )
            attachments_deleted += deleted
            if deleted < batch_size:
                break

        if stopped_reason is None:
            while True:
                with get_db() as conn:
                    policy = _locked_policy(conn, organization_id)
                    if policy is None:
                        raise RuntimeError("Organization communication policy disappeared during retention.")
                    legal_hold, current_message_days, _ = policy
                    if legal_hold:
                        stopped_reason = "legal_hold_enabled"
                        break
                    deleted = _delete_message_batch(
                        conn,
                        organization_id=organization_id,
                        retention_days=current_message_days,
                        batch_size=batch_size,
                    )
                messages_deleted += deleted
                if deleted < batch_size:
                    break

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE team_retention_runs
                    SET completed_at = NOW(), status = 'completed',
                        messages_deleted = %s, attachments_deleted = %s,
                        error = %s
                    WHERE id = %s
                    """,
                    (
                        messages_deleted,
                        attachments_deleted,
                        stopped_reason,
                        run_id,
                    ),
                )

        return {
            "organization_id": organization_id,
            "run_id": run_id,
            "messages_deleted": messages_deleted,
            "attachments_deleted": attachments_deleted,
            "skipped": False,
            "stopped_reason": stopped_reason,
        }
    except Exception as exc:
        logger.exception("Communication retention failed for organization %s.", organization_id)
        if run_id is not None:
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE team_retention_runs
                            SET completed_at = NOW(), status = 'failed', error = %s,
                                messages_deleted = %s, attachments_deleted = %s
                            WHERE id = %s
                            """,
                            (str(exc)[:1000], messages_deleted, attachments_deleted, run_id),
                        )
            except Exception:
                logger.exception("Could not mark retention run %s failed.", run_id)
        return {
            "organization_id": organization_id,
            "run_id": run_id,
            "messages_deleted": messages_deleted,
            "attachments_deleted": attachments_deleted,
            "skipped": False,
            "error": str(exc),
        }


def purge_due_team_communications(
    *,
    organization_limit: int = 100,
    batch_size: int = 500,
) -> dict[str, Any]:
    results = []
    for organization_id, message_days, attachment_days in _eligible_policies(
        organization_limit
    ):
        results.append(
            apply_retention_for_organization(
                organization_id,
                message_retention_days=message_days,
                attachment_retention_days=attachment_days,
                batch_size=batch_size,
            )
        )
    failed = [result for result in results if result.get("error")]
    return {
        "success": not failed,
        "organizations_processed": len(results),
        "failed_count": len(failed),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply ReDOCX team retention policies.")
    parser.add_argument("--organization-limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    print(
        purge_due_team_communications(
            organization_limit=max(1, args.organization_limit),
            batch_size=max(1, min(args.batch_size, 5000)),
        )
    )


if __name__ == "__main__":
    main()
