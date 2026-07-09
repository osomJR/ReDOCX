from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg_pool import ConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured.")

# For Neon/Railway, keep this conservative.
_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
_POOL_TIMEOUT_SECONDS = float(os.getenv("DB_POOL_TIMEOUT_SECONDS", "30"))

# Recycle connections before the platform/database is likely to kill them.
_POOL_MAX_LIFETIME_SECONDS = float(os.getenv("DB_POOL_MAX_LIFETIME_SECONDS", "1800"))
_POOL_MAX_IDLE_SECONDS = float(os.getenv("DB_POOL_MAX_IDLE_SECONDS", "300"))
_POOL_RECONNECT_TIMEOUT_SECONDS = float(os.getenv("DB_POOL_RECONNECT_TIMEOUT_SECONDS", "30"))

pool: ConnectionPool[Connection] = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=_POOL_MIN_SIZE,
    max_size=_POOL_MAX_SIZE,
    timeout=_POOL_TIMEOUT_SECONDS,
    max_lifetime=_POOL_MAX_LIFETIME_SECONDS,
    max_idle=_POOL_MAX_IDLE_SECONDS,
    reconnect_timeout=_POOL_RECONNECT_TIMEOUT_SECONDS,
    check=ConnectionPool.check_connection,
    kwargs={"autocommit": False},
    open=True,
)


@contextmanager
def get_db() -> Iterator[Connection]:
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