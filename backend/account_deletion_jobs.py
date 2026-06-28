from __future__ import annotations

"""
Scheduled account deletion jobs.

Run this from your production scheduler after applying 009_create_account_lifecycle.sql,
for example once per hour. It permanently deletes accounts whose paid-period
restore window has elapsed.
"""

import argparse
from typing import Any

from backend.account import delete_auth0_user, delete_local_account_data
from backend.account_lifecycle import list_purge_due_user_ids, mark_account_purged
from backend.database import get_db


def purge_due_accounts(*, limit: int = 100) -> dict[str, Any]:
    purged: list[str] = []
    failed: list[dict[str, str]] = []

    with get_db() as conn:
        user_ids = list_purge_due_user_ids(conn, limit=limit)

    for user_id in user_ids:
        try:
            delete_auth0_user(user_id)
            with get_db() as conn:
                delete_local_account_data(conn, user_id=user_id)
                mark_account_purged(conn, user_id)
            purged.append(user_id)
        except Exception as exc:  # noqa: BLE001 - job should continue purging other accounts.
            failed.append({"user_id": user_id, "error": str(exc)})

    return {
        "success": not failed,
        "purged_count": len(purged),
        "failed_count": len(failed),
        "purged_user_ids": purged,
        "failed": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Purge due ReDOCX accounts.")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    result = purge_due_accounts(limit=args.limit)
    print(result)


if __name__ == "__main__":
    main()
