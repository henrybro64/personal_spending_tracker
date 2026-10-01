"""Shared Postgres connections for request handlers.

Ordinary reads borrow from a small pool and fail within a few seconds when
Postgres is unreachable, busy, or stuck. The statement cap is applied when
the connection opens, so the first statement cannot wait forever. Migrations
use the same connect timeout and skip that cap, because schema changes can
legitimately run longer than a dashboard query.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

logger = logging.getLogger("backend.db")


class _RedactingFilter(logging.Filter):
    """Drop passwords and query text from database log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Format first so a secret passed as a %s argument is still removed.
        record.msg = sanitize_db_text(record.getMessage())
        record.args = ()
        return True


def _install_log_filter() -> None:
    redactor = _RedactingFilter()
    for name in ("backend.db", "psycopg.pool"):
        target = logging.getLogger(name)
        already_installed = any(
            isinstance(item, _RedactingFilter) for item in target.filters
        )
        if already_installed:
            continue
        target.addFilter(redactor)


_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()
_request_id: ContextVar[str] = ContextVar("pst_db_request_id", default="")
_endpoint: ContextVar[str] = ContextVar("pst_db_endpoint", default="")

_PASSWORD_PARAM = re.compile(r"(password=)[^&\s]+", re.IGNORECASE)
_USERINFO = re.compile(r"(://[^:/@\s]+:)[^@/\s]+@")

_CONNECT_TIMEOUT_SECONDS = 3
_POOL_TIMEOUT_SECONDS = 3.0
_STATEMENT_TIMEOUT_MS = 12_000
_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 5
_READINESS_STATEMENT_TIMEOUT_MS = 2_000
_DEFAULT_STATEMENT_TIMEOUT = object()


@dataclass(frozen=True)
class _PoolSettings:
    min_size: int
    max_size: int
    pool_timeout: float
    connect_timeout: int
    statement_timeout_ms: int


def bind_request_context(*, request_id: str, endpoint: str) -> None:
    _request_id.set(request_id)
    _endpoint.set(endpoint)


def clear_request_context() -> None:
    _request_id.set("")
    _endpoint.set("")


def sanitize_db_text(text: str) -> str:
    """Remove passwords from a driver message before it is logged."""
    return _USERINFO.sub(r"\1***@", _PASSWORD_PARAM.sub(r"\1***", text))


def failure_category(exc: BaseException) -> str:
    if isinstance(exc, PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return "query_timeout"
    message = str(exc).lower()
    if (
        "password authentication failed" in message
        or "authentication failed" in message
    ):
        return "auth_failure"
    if "statement timeout" in message or "canceling statement" in message:
        return "query_timeout"
    if "timeout" in message or "timed out" in message:
        return "connect_timeout"
    if _is_network_failure(message):
        return "network"
    return "database_error"


def pool_stats(database_url: str | None = None) -> dict[str, int]:
    configured = os.environ.get("DATABASE_URL", "")
    url = database_url if database_url is not None else configured
    pool = _pools.get(url)
    if pool is None or pool.closed:
        return {"pool_size": 0, "pool_available": 0}
    stats = pool.get_stats()
    return {
        "pool_size": int(stats.get("pool_size", 0)),
        "pool_available": int(stats.get("pool_available", 0)),
    }


def close_all_pools() -> None:
    with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        if pool.closed:
            continue
        pool.close(timeout=1)


@contextmanager
def migration_connection(
    database_url: str,
) -> Iterator[psycopg.Connection]:
    """Use the connect timeout and skip the short query cap."""
    settings = _settings()
    with psycopg.connect(
        database_url,
        connect_timeout=settings.connect_timeout,
    ) as connection:
        yield connection


@contextmanager
def db_connection(
    database_url: str,
    operation: str = "query",
    statement_timeout_ms: int | None | object = _DEFAULT_STATEMENT_TIMEOUT,
) -> Iterator[psycopg.Connection]:
    """Check out a pooled connection.

    The session statement cap is set when the connection opens. Pass None
    to lift it for one transaction, such as a statement import.
    """
    started = time.perf_counter()
    session_timeout_ms = _settings().statement_timeout_ms
    timeout_ms = (
        session_timeout_ms
        if statement_timeout_ms is _DEFAULT_STATEMENT_TIMEOUT
        else statement_timeout_ms
    )
    try:
        with get_pool(database_url).connection() as connection:
            # The session cap is set at connect time, so this adjustment
            # cannot wait longer than that cap.
            if timeout_ms is None:
                connection.execute(
                    "SELECT set_config('statement_timeout', '0', true)"
                )
            elif timeout_ms != session_timeout_ms:
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(timeout_ms),),
                )
            yield connection
    except Exception as exc:
        _log_failure(exc, operation=operation, started=started)
        raise


def ping_database(database_url: str | None = None) -> float:
    """Return the readiness round-trip in milliseconds."""
    url = os.environ["DATABASE_URL"] if database_url is None else database_url
    started = time.perf_counter()
    with db_connection(
        url,
        operation="readiness",
        statement_timeout_ms=_READINESS_STATEMENT_TIMEOUT_MS,
    ) as connection:
        connection.execute("SELECT 1")
    return (time.perf_counter() - started) * 1000


def get_pool(database_url: str) -> ConnectionPool:
    with _pools_lock:
        existing = _pools.get(database_url)
        if existing is not None and not existing.closed:
            return existing
        settings = _settings()
        pool = ConnectionPool(
            database_url,
            min_size=settings.min_size,
            max_size=settings.max_size,
            timeout=settings.pool_timeout,
            kwargs=_connection_kwargs(settings),
            check=ConnectionPool.check_connection,
            name="spending-db",
            open=False,
        )
        pool.open(wait=False)
        _pools[database_url] = pool
        return pool


def _connection_kwargs(settings: _PoolSettings) -> dict[str, object]:
    """Bound connect and every statement, including the first one."""
    return {
        "connect_timeout": settings.connect_timeout,
        "options": f"-c statement_timeout={settings.statement_timeout_ms}",
    }


def _settings() -> _PoolSettings:
    return _PoolSettings(
        min_size=_env_int("DB_POOL_MIN_SIZE", _POOL_MIN_SIZE),
        max_size=_env_int("DB_POOL_MAX_SIZE", _POOL_MAX_SIZE),
        pool_timeout=_env_float(
            "DB_POOL_TIMEOUT_SECONDS",
            _POOL_TIMEOUT_SECONDS,
        ),
        connect_timeout=_env_int(
            "DB_CONNECT_TIMEOUT_SECONDS",
            _CONNECT_TIMEOUT_SECONDS,
        ),
        statement_timeout_ms=_env_int(
            "DB_STATEMENT_TIMEOUT_MS",
            _STATEMENT_TIMEOUT_MS,
        ),
    )


def _log_failure(
    exc: BaseException,
    *,
    operation: str,
    started: float,
) -> None:
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    # The exception text can contain SQL, parameters, or a connection string.
    logger.warning(
        "database failure category=%s operation=%s elapsed_ms=%s "
        "request_id=%s endpoint=%s error_type=%s",
        failure_category(exc),
        operation,
        elapsed_ms,
        _request_id.get(),
        _endpoint.get(),
        type(exc).__name__,
    )


def _is_network_failure(message: str) -> bool:
    needles = (
        "connection refused",
        "could not translate host",
        "name or service not known",
        "temporary failure in name resolution",
        "network is unreachable",
        "no route to host",
    )
    return any(needle in message for needle in needles)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


_install_log_filter()
