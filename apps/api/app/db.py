"""Postgres access: one small connection pool to the Supabase transaction pooler.

Deliberately synchronous (psycopg + FastAPI's threadpool): async psycopg cannot run on Windows'
default event loop, and our query volume does not need it. The pool is capped small because the
Supabase project is shared with another live app (role `syl_app` is limited to 10 connections).
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def build_conninfo(url: str) -> str:
    """Add the safe defaults the URL does not state: TLS for remote hosts, timeouts, a name."""
    params = conninfo_to_dict(url)
    if params.get("host", "") not in _LOCAL_HOSTS:
        params.setdefault("sslmode", "require")
    params.setdefault("connect_timeout", "10")
    params.setdefault("application_name", "syl-api")
    return make_conninfo(**params)


class Database:
    def __init__(self, url: str, *, max_size: int = 3, acquire_timeout: float = 5.0) -> None:
        self._max_size = max_size
        self._pool = ConnectionPool(
            build_conninfo(url),
            min_size=1,
            max_size=max_size,
            timeout=acquire_timeout,  # fail the request instead of queueing forever
            max_lifetime=300.0,
            max_idle=60.0,
            open=False,
            check=ConnectionPool.check_connection,  # detect connections the pooler dropped
            kwargs={
                "row_factory": dict_row,
                # The transaction pooler multiplexes server connections, so server-side prepared
                # statements would break. Disable them.
                "prepare_threshold": None,
            },
            name="syl",
        )

    def open(self) -> None:
        # Do not block boot on the database: a cold/paused DB must not stop the process from
        # serving /healthz. /readyz reports DB health instead.
        self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close(timeout=5.0)

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        """One transaction: commits on clean exit, rolls back on any exception."""
        with self._pool.connection() as conn:
            yield conn

    def ping(self) -> bool:
        try:
            with self.connection() as conn:
                conn.execute("select 1")
            return True
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            # Class name only: exception text can contain the connection string.
            log.warning("db_ping_failed error=%s", type(exc).__name__)
            return False

    def __repr__(self) -> str:  # never include the URL
        return f"Database(max_size={self._max_size})"
