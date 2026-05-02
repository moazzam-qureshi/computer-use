"""Postgres connection pool. Single pool for the whole process."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg_pool import ConnectionPool


class Database:
    """Owns the connection pool. Constructed at scheduler startup, passed to stores."""

    def __init__(self, dsn: str, min_size: int = 1, max_size: int = 10):
        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Connection]:
        """Wraps a unit-of-work in a transaction."""
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    def close(self) -> None:
        self._pool.close()
