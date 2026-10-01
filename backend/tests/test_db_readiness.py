import logging
import time

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from backend.db import (
    bind_request_context,
    close_all_pools,
    db_connection,
    ping_database,
    sanitize_db_text,
)


def _register(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": "ready@example.com",
            "password": "password123",
            "display_name": "Ready",
        },
    )
    assert response.status_code == 201


def test_pool_logs_drop_passwords_and_query_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="psycopg.pool")
    logging.getLogger("psycopg.pool").warning(
        "error connecting in %r: %s",
        "spending-db",
        psycopg.OperationalError(
            "postgresql://app:super-secret-password@db/app"
        ),
    )

    assert "super-secret-password" not in caplog.text
    assert "postgresql://app:***@db/app" in caplog.text


def test_ready_log_keeps_the_request_id_and_drops_secrets(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "2")

    def fail_ping() -> float:
        with db_connection(
            "postgresql://appuser:super-secret-password@127.0.0.1:1/none",
            operation="readiness",
        ):
            pass
        return 0.0

    monkeypatch.setattr("backend.main.ping_database", fail_ping)
    caplog.set_level(logging.WARNING)
    response = client.get("/ready", headers={"X-Request-ID": "req-header-68"})

    assert response.status_code == 503
    assert "req-header-68" in caplog.text
    assert "/ready" in caplog.text
    assert "super-secret-password" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_sanitize_db_text_strips_passwords() -> None:
    sanitized = sanitize_db_text(
        "postgresql://app:super-secret-password@db/app password=super-secret-password"
    )
    assert "super-secret-password" not in sanitized


def test_startup_stays_up_when_schema_setup_cannot_connect(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from backend.main import prepare_schema_on_startup

    monkeypatch.setattr("backend.main._schema_ready", False)

    def fail(_database_url: str | None = None) -> str:
        raise psycopg.OperationalError(
            "connection refused password=super-secret-password"
        )

    monkeypatch.setattr("backend.main.init_db", fail)
    caplog.set_level(logging.WARNING, logger="backend.main")

    prepare_schema_on_startup()

    assert "super-secret-password" not in caplog.text
    assert "connect" in caplog.text or "network" in caplog.text


def test_startup_still_raises_when_schema_sql_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.main import prepare_schema_on_startup

    monkeypatch.setattr("backend.main._schema_ready", False)

    def fail(_database_url: str | None = None) -> str:
        raise psycopg.ProgrammingError("syntax error at or near SELECT")

    monkeypatch.setattr("backend.main.init_db", fail)

    with pytest.raises(psycopg.ProgrammingError):
        prepare_schema_on_startup()


def test_ready_migrates_after_postgres_recovers(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import main

    calls = {"count": 0}
    real_init = main.init_db

    def flaky(database_url: str | None = None) -> str:
        calls["count"] += 1
        if calls["count"] == 1:
            raise psycopg.OperationalError("connection refused")
        return real_init(database_url)

    _register(client)
    monkeypatch.setattr(main, "_schema_ready", False)
    monkeypatch.setattr(main, "init_db", flaky)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    recovered = client.get("/ready")

    assert recovered.status_code == 200
    assert calls["count"] == 2
    assert client.get("/transactions").status_code == 200


def test_session_statement_timeout_is_set_when_the_connection_opens(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "400")
    close_all_pools()

    with db_connection(database_url, operation="show-timeout") as connection:
        row = connection.execute("SHOW statement_timeout").fetchone()

    assert row is not None
    assert row[0] == "400ms"
    close_all_pools()


def test_ready_when_database_is_available(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_latency_ms"] >= 0


def test_health_stays_ok_when_database_ping_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> float:
        raise psycopg.OperationalError("password=super-secret-password connection refused")

    monkeypatch.setattr("backend.main.ping_database", broken)

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 503
    assert ready.json()["detail"]["failure_category"] == "network"
    assert "super-secret-password" not in ready.text


def test_ready_recovers_and_authenticated_reads_still_work(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def flaky() -> float:
        calls["count"] += 1
        if calls["count"] == 1:
            raise psycopg.OperationalError("connection refused")
        return ping_database()

    monkeypatch.setattr("backend.main.ping_database", flaky)
    _register(client)

    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    recovered = client.get("/ready")
    assert recovered.status_code == 200
    assert client.get("/transactions").status_code == 200
    assert client.get("/dashboard/view").status_code == 200


def _checkout_and_select_one(database_url: str, operation: str) -> None:
    with db_connection(database_url, operation=operation) as connection:
        connection.execute("SELECT 1")


def _checkout_and_sleep(database_url: str, operation: str, seconds: float) -> None:
    with db_connection(database_url, operation=operation) as connection:
        connection.execute("SELECT pg_sleep(%s)", (seconds,))


def test_diagnostics_returns_unavailable_when_session_lookup_cannot_reach_postgres(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(client)

    def fail_session(_token: str, database_url: str | None = None):
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr("backend.auth.get_user_for_session", fail_session)
    response = client.get("/diagnostics")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "unavailable"
    assert body["database_ok"] is False
    assert body["failure_category"] == "network"


def test_diagnostics_hides_secrets_from_anonymous_callers(client: TestClient) -> None:
    anonymous = client.get("/diagnostics")
    assert anonymous.status_code == 401

    _register(client)
    response = client.get("/diagnostics")
    assert response.status_code == 200
    body = response.json()
    assert body["api"] == "ok"
    assert body["database_ok"] is True
    assert body["pool_size"] >= 1
    assert "postgresql://" not in response.text
    assert "password" not in response.text


def test_connect_timeout_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("DB_CONNECT_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
    close_all_pools()
    bind_request_context(request_id="req-68", endpoint="/ready")
    caplog.set_level(logging.WARNING, logger="backend.db")
    started = time.perf_counter()

    with pytest.raises((psycopg.OperationalError, PoolTimeout)):
        _checkout_and_select_one(
            "postgresql://appuser:super-secret-password@192.0.2.1:5432/none",
            "connect-timeout",
        )

    assert time.perf_counter() - started < 8
    assert "req-68" in caplog.text
    assert "/ready" in caplog.text
    assert "super-secret-password" not in caplog.text
    close_all_pools()


def test_query_timeout_is_bounded(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "400")
    close_all_pools()
    started = time.perf_counter()

    with pytest.raises(psycopg.errors.QueryCanceled):
        _checkout_and_sleep(database_url, "query-timeout", 5)

    assert time.perf_counter() - started < 3


def test_bulk_write_checkout_skips_the_read_statement_timeout(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_STATEMENT_TIMEOUT_MS", "400")
    close_all_pools()

    with db_connection(
        database_url,
        operation="bulk-write",
        statement_timeout_ms=None,
    ) as connection:
        connection.execute("SELECT pg_sleep(0.6)")

    with pytest.raises(psycopg.errors.QueryCanceled):
        _checkout_and_sleep(database_url, "query-timeout", 5)

    close_all_pools()


def test_pool_acquire_timeout_is_bounded(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "0")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "0.4")
    close_all_pools()
    started = time.perf_counter()

    with db_connection(database_url) as held:
        held.execute("SELECT 1")
        with pytest.raises(PoolTimeout):
            _checkout_and_select_one(database_url, "pool-timeout")

    assert time.perf_counter() - started < 3


def test_closed_connection_is_not_reused(database_url: str) -> None:
    close_all_pools()
    try:
        with db_connection(database_url) as connection:
            connection.close()
    except Exception:
        pass

    with db_connection(database_url) as connection:
        row = connection.execute("SELECT 1").fetchone()

    assert row == (1,)
