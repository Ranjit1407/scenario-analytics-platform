"""
Thin wrapper around a psycopg2 connection pool.

The pool is created lazily on first use. This keeps the API process startable
while the database is still coming up (common on first deploy), and lets the
unit tests import the app without a running PostgreSQL.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dsn: str, min_connections: int = 1, max_connections: int = 5) -> None:
        self._dsn = dsn
        self._min = min_connections
        self._max = max_connections
        self._pool: ThreadedConnectionPool | None = None
        self._lock = threading.Lock()

    def _get_pool(self) -> ThreadedConnectionPool:
        # Double-checked so concurrent first requests do not create two pools.
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    logger.info("Opening PostgreSQL connection pool (max %d)", self._max)
                    # Pin the session time zone so API timestamps are always UTC,
                    # whatever the server's default happens to be.
                    self._pool = ThreadedConnectionPool(
                        self._min, self._max, dsn=self._dsn, options="-c timezone=UTC"
                    )
        return self._pool

    @contextmanager
    def connection(self) -> Iterator[PgConnection]:
        """
        Borrow a connection for one unit of work.

        Commits when the block exits normally and rolls back on any exception,
        so callers never leave a transaction half-open.
        """
        pool = self._get_pool()
        conn = pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            if not conn.closed:
                conn.rollback()
            raise
        finally:
            # A connection that died mid-request must not be handed out again.
            pool.putconn(conn, close=bool(conn.closed))

    @contextmanager
    def cursor(self) -> Iterator[RealDictCursor]:
        """Cursor returning rows as dicts, inside its own transaction."""
        with self.connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur

    def ping(self) -> bool:
        try:
            with self.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except psycopg2.Error as exc:
            logger.warning("Database health check failed: %s", exc)
            return False

    def run_script(self, path: Path) -> None:
        """Execute a whole .sql file in a single transaction."""
        sql = path.read_text(encoding="utf-8")
        with self.connection() as conn, conn.cursor() as cur:
            cur.execute(sql)
        logger.info("Applied %s", path.name)

    def close(self) -> None:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
