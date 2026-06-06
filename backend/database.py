from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured.")

# Neon runtime connection should use the pooled URL.
# Example:
# postgresql://...-pooler.../neondb?sslmode=require&channel_binding=require
_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "20"))
_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
_POOL_TIMEOUT_SECONDS = float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))

pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=_POOL_MIN_SIZE,
    max_size=_POOL_MAX_SIZE,
    timeout=_POOL_TIMEOUT_SECONDS,
    kwargs={"autocommit": False},
)


@contextmanager
def get_db() -> Iterator:
    """
    Yields a PostgreSQL connection from the shared pool.

    - commits on success
    - rolls back on failure
    """
    with pool.connection() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise