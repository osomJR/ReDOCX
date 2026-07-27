from __future__ import annotations

"""
Scheduled account deletion jobs.

Run this from your production scheduler after applying
009_create_account_lifecycle.sql and 019_harden_account_deletion_jobs.sql, for
example once per hour. It permanently deletes accounts whose recovery window
has elapsed. Durable database leases prevent overlapping scheduler processes
from purging the same account concurrently.
"""

import argparse
import json
import os
import socket
import sys
from typing import Any
from uuid import uuid4

from backend.routes.account import delete_auth0_user, delete_local_account_data
from backend.account_lifecycle import (
    claim_purge_due_user_ids,
    mark_account_purged,
    release_account_purge_claim,
)
from backend.database import get_db


def build_worker_id() -> str:
    configured_label = os.getenv("ACCOUNT_DELETION_WORKER_ID", "").strip()
    worker_label = configured_label or socket.gethostname()
    unique_suffix = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"
    return f"{worker_label[:100]}:{unique_suffix}"[:200]


def purge_due_accounts(
    *,
    limit: int = 100,
    lease_seconds: int = 1800,
    worker_id: str | None = None,
) -> dict[str, Any]:
    purged: list[str] = []
    failed: list[dict[str, str]] = []
    resolved_worker_id = worker_id or build_worker_id()

    with get_db() as conn:
        user_ids = claim_purge_due_user_ids(
            conn,
            worker_id=resolved_worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    for user_id in user_ids:
        try:
            delete_auth0_user(user_id)
            with get_db() as conn:
                delete_local_account_data(conn, user_id=user_id)
                mark_account_purged(
                    conn,
                    user_id,
                    worker_id=resolved_worker_id,
                )
            purged.append(user_id)
        except Exception as exc:  # noqa: BLE001 - job should continue purging other accounts.
            error = str(exc)
            failed.append({"user_id": user_id, "error": error})
            try:
                with get_db() as conn:
                    release_account_purge_claim(
                        conn,
                        user_id=user_id,
                        worker_id=resolved_worker_id,
                        error=error,
                    )
            except Exception as release_exc:  # noqa: BLE001
                failed[-1]["claim_release_error"] = str(release_exc)

    return {
        "success": not failed,
        "claimed_count": len(user_ids),
        "purged_count": len(purged),
        "failed_count": len(failed),
        "purged_user_ids": purged,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge due ReDOCX accounts.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=int(os.getenv("ACCOUNT_DELETION_PURGE_LEASE_SECONDS", "1800")),
    )
    args = parser.parse_args()
    result = purge_due_accounts(
        limit=args.limit,
        lease_seconds=args.lease_seconds,
    )
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()