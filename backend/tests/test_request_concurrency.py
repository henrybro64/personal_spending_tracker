import asyncio
import os
import threading
from contextlib import contextmanager
from io import BytesIO

import psycopg
import pytest
from fastapi import UploadFile
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import Headers
from starlette.requests import Request

from backend.batch_import.models import BatchImportResult


def _import_result() -> BatchImportResult:
    return BatchImportResult(
        run_id=1,
        imported_at="2026-09-29T00:00:00+00:00",
        statement_count=0,
        transaction_count=0,
        needs_review_count=0,
        csv_output_path="",
        sqlite_output_path="",
        parsed_files=[],
        skipped_files=[],
    )


def _upload_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )
    request.state.request_id = "concurrency-test"
    return request


@pytest.mark.anyio
async def test_batch_import_leaves_health_and_reads_responsive(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.get_ident()
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    database_threads: list[int] = []
    def slow_import(self) -> BatchImportResult:
        del self
        worker_threads.append(threading.get_ident())
        started.set()
        if worker_threads[0] == loop_thread:
            return _import_result()
        if not release.wait(timeout=3):
            raise TimeoutError("batch import was not released")
        return _import_result()

    import backend.database as database

    original_connection = database.db_connection

    @contextmanager
    def tracking_connection(*args, **kwargs):
        database_threads.append(threading.get_ident())
        with original_connection(*args, **kwargs) as connection:
            yield connection

    monkeypatch.setattr(api_main.StatementImporter, "import_transactions", slow_import)
    monkeypatch.setattr(database, "db_connection", tracking_connection)

    transport = ASGITransport(app=api_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        registered = await client.post(
            "/auth/register",
            json={
                "email": "concurrency@example.com",
                "password": "password123",
                "display_name": "Concurrency",
            },
        )
        assert registered.status_code == 201

        import_task = asyncio.create_task(client.post("/batch-imports/run", json={}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert worker_threads
            assert worker_threads[0] != loop_thread

            database_threads.clear()
            health, transactions, dashboard = await asyncio.gather(
                client.get("/health"),
                client.get("/transactions"),
                client.get("/dashboard/view"),
            )
            assert not import_task.done()
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
            assert transactions.status_code == 200
            assert dashboard.status_code == 200
            assert database_threads
            assert all(thread_id != loop_thread for thread_id in database_threads)
        finally:
            release.set()

        finished = await import_task
        assert finished.status_code == 201


@pytest.mark.anyio
async def test_pdf_parsing_runs_off_the_event_loop(
    client,
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_load(path, password=None):
        del path, password
        return "synthetic statement", [
            {
                "date": "2024-04-20",
                "merchant": "Cafe",
                "amount": 12.0,
                "category": "Dining",
                "currency": "USD",
            }
        ]

    monkeypatch.setattr(api_main, "_load_uploaded_statement", fake_load)

    registered = client.post(
        "/auth/register",
        json={
            "email": "pdf-thread@example.com",
            "password": "password123",
            "display_name": "PDF Thread",
        },
    )
    assert registered.status_code == 201

    result = await api_main.upload_pdf(
        request=_upload_request(),
        file=UploadFile(
            file=BytesIO(b"%PDF-1.4 synthetic"),
            filename="statement.pdf",
            headers=Headers({"content-type": "application/pdf"}),
        ),
        password=None,
        current_user=registered.json()["user"],
    )

    assert result["original_file_name"] == "statement.pdf"
    assert result["transactions"][0]["merchant"] == "Cafe"


def test_pdf_cpu_work_is_scheduled_in_the_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[str] = []

    def fake_run(func, *args):
        del args
        scheduled.append(func.__name__)
        if func.__name__ == "_read_statement_pdf_in_process":
            return "statement", []
        return "statement"

    monkeypatch.setattr("backend.process_pool.run_cpu_bound", fake_run)
    from backend.pdf_utils import extract_pdf_text, read_statement_pdf

    assert extract_pdf_text("statement.pdf") == "statement"
    assert read_statement_pdf("statement.pdf", None) == ("statement", [])
    assert scheduled == [
        "_extract_pdf_text_in_process",
        "_read_statement_pdf_in_process",
    ]


@pytest.mark.anyio
async def test_cpu_bound_pdf_work_does_not_stall_health(api_main) -> None:
    import time

    from backend.process_pool import occupy_child_process, run_cpu_bound

    parent_pid = os.getpid()
    task = asyncio.create_task(
        asyncio.to_thread(run_cpu_bound, occupy_child_process, 1.2)
    )
    await asyncio.sleep(0.2)
    assert not task.done()

    transport = ASGITransport(app=api_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        started = time.perf_counter()
        health = await client.get("/health")
        elapsed = time.perf_counter() - started
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        # CPU work on the event loop would delay /health for the whole burn.
        # Finish the burn before this client exits and shuts the pool down.
        assert elapsed < 0.5
        assert not task.done()
        child_pid = await task

    assert child_pid != parent_pid


def _password_failure_probe():
    from backend.pdf_utils import _PdfWorkerFailure

    return _PdfWorkerFailure("password", True, "")


def test_password_error_round_trips_without_breaking_the_process_pool() -> None:
    import pickle

    from backend.pdf_utils import PdfPasswordProtectedError, _raise_pdf_worker_failure
    from backend.process_pool import run_cpu_bound

    restored = pickle.loads(
        pickle.dumps(PdfPasswordProtectedError(had_password=True))
    )
    assert restored.had_password is True

    first_failure = run_cpu_bound(_password_failure_probe)
    with pytest.raises(PdfPasswordProtectedError) as first:
        _raise_pdf_worker_failure(first_failure)
    assert first.value.had_password is True

    second_failure = run_cpu_bound(_password_failure_probe)
    with pytest.raises(PdfPasswordProtectedError) as second:
        _raise_pdf_worker_failure(second_failure)
    assert second.value.had_password is True


def test_pdf_worker_recovers_after_the_child_process_dies() -> None:
    from backend import process_pool

    process_pool.shutdown_process_pool()
    try:
        with pytest.raises(process_pool.PdfWorkerError):
            process_pool.run_cpu_bound(os._exit, 137)  # the worker is killed
        assert isinstance(process_pool.run_cpu_bound(os.getpid), int)
    finally:
        process_pool.shutdown_process_pool()


def test_pdf_worker_replaces_a_worker_that_died_while_idle() -> None:
    import signal
    import time

    from backend import process_pool

    process_pool.shutdown_process_pool()
    try:
        first_pid = process_pool.run_cpu_bound(os.getpid)
        os.kill(first_pid, signal.SIGKILL)
        time.sleep(0.5)  # let the pool notice the dead worker
        second_pid = process_pool.run_cpu_bound(os.getpid)
        assert second_pid != first_pid
    finally:
        process_pool.shutdown_process_pool()


def test_pdf_worker_times_out_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from backend import process_pool

    monkeypatch.setenv("PDF_PARSE_TIMEOUT_SECONDS", "1")
    process_pool.shutdown_process_pool()
    try:
        started = time.perf_counter()
        with pytest.raises(process_pool.PdfWorkerTimeout):
            process_pool.run_cpu_bound(time.sleep, 30)
        assert time.perf_counter() - started < 10
        assert isinstance(process_pool.run_cpu_bound(os.getpid), int)
    finally:
        process_pool.shutdown_process_pool()


def test_pdf_worker_time_limit_does_not_count_time_spent_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from backend import process_pool

    # Each task runs 1.2 s. The second waits for the first, so 2.4 s pass
    # from its submission, but only its own run time counts toward 2 s.
    monkeypatch.setenv("PDF_PARSE_TIMEOUT_SECONDS", "2")
    process_pool.shutdown_process_pool()
    try:
        process_pool.run_cpu_bound(os.getpid)  # start the worker up front
        with ThreadPoolExecutor(max_workers=2) as threads:
            first = threads.submit(
                process_pool.run_cpu_bound, process_pool.occupy_child_process, 1.2
            )
            second = threads.submit(
                process_pool.run_cpu_bound, process_pool.occupy_child_process, 1.2
            )
            assert isinstance(first.result(timeout=10), int)
            assert isinstance(second.result(timeout=10), int)
    finally:
        process_pool.shutdown_process_pool()


def test_pdf_parse_timeout_falls_back_to_default_for_bad_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend import process_pool

    for raw in ("", "abc", "0", "-5", "nan", "inf"):
        monkeypatch.setenv("PDF_PARSE_TIMEOUT_SECONDS", raw)
        assert process_pool.pdf_parse_timeout_seconds() == 120.0
    monkeypatch.setenv("PDF_PARSE_TIMEOUT_SECONDS", "45")
    assert process_pool.pdf_parse_timeout_seconds() == 45.0


@pytest.mark.parametrize(
    ("error_name", "status_code", "detail_snippet", "reason"),
    [
        ("PdfWorkerTimeout", 504, "took too long", "timeout"),
        ("PdfWorkerError", 422, "could not be read", "worker_error"),
    ],
)
def test_pdf_worker_failure_rejects_upload_and_removes_the_file(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
    error_name: str,
    status_code: int,
    detail_snippet: str,
    reason: str,
) -> None:
    from fastapi import HTTPException

    from backend import process_pool

    audits: list[dict] = []
    monkeypatch.setattr(api_main, "record_audit_event", lambda **kwargs: audits.append(kwargs))

    def failing_load(path, password):
        del path, password
        raise getattr(process_pool, error_name)("worker failed")

    monkeypatch.setattr(api_main, "_load_uploaded_statement", failing_load)

    audit_context = api_main.get_audit_request_context(_upload_request())
    with pytest.raises(HTTPException) as caught:
        api_main._store_uploaded_pdf(
            upload_stream=BytesIO(b"%PDF-1.4 synthetic"),
            original_name="statement.pdf",
            content_type="application/pdf",
            password=None,
            user_id=1,
            audit_context=audit_context,
        )

    assert caught.value.status_code == status_code
    assert detail_snippet in caught.value.detail
    assert list(api_main._get_upload_dir(1).glob("*")) == []
    assert audits[-1]["action"] == "statement_upload_parse_failure"
    assert audits[-1]["metadata"]["reason"] == reason


def test_oversized_upload_is_rejected_without_keeping_the_file(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "MAX_STATEMENT_UPLOAD_BYTES", 8)
    monkeypatch.setattr(api_main, "record_audit_event", lambda **kwargs: None)

    from fastapi import HTTPException

    audit_context = api_main.get_audit_request_context(_upload_request())
    upload_stream = BytesIO(b"%PDF-1.4 too large")
    try:
        api_main._store_uploaded_pdf(
            upload_stream=upload_stream,
            original_name="statement.pdf",
            content_type="application/pdf",
            password=None,
            user_id=1,
            audit_context=audit_context,
        )
    except HTTPException as caught:
        assert caught.status_code == 413
    else:
        raise AssertionError("expected the oversized upload to be rejected")
    upload_dir = api_main._get_upload_dir(1)
    assert list(upload_dir.glob("*")) == []


def test_duplicate_fingerprint_conflict_removes_the_saved_pdf(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from backend.database import DuplicateStatementUploadError

    lookups = {"count": 0}

    def lookup(**kwargs):
        del kwargs
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None
        return {
            "id": 4,
            "file_name": "saved.pdf",
            "display_name": "Card.pdf",
            "uploaded_at": "2026-01-02T00:00:00+00:00",
            "parse_status": "parsed",
        }

    monkeypatch.setattr(api_main, "record_audit_event", lambda **kwargs: None)
    monkeypatch.setattr(api_main, "get_existing_upload_by_fingerprint", lookup)
    monkeypatch.setattr(
        api_main,
        "_load_uploaded_statement",
        lambda path, password: ("statement", []),
    )
    def reject_duplicate(**kwargs):
        del kwargs
        raise DuplicateStatementUploadError("fp")

    monkeypatch.setattr(api_main, "create_statement_upload", reject_duplicate)

    audit_context = api_main.get_audit_request_context(_upload_request())
    upload_stream = BytesIO(b"%PDF-1.4 synthetic")
    try:
        api_main._store_uploaded_pdf(
            upload_stream=upload_stream,
            original_name="Card.pdf",
            content_type="application/pdf",
            password=None,
            user_id=1,
            audit_context=audit_context,
        )
    except HTTPException as caught:
        assert caught.status_code == 409
        assert "Card.pdf" in caught.detail
    else:
        raise AssertionError("expected the duplicate upload to be rejected")
    upload_dir = api_main._get_upload_dir(1)
    assert list(upload_dir.glob("*")) == []


def test_concurrent_duplicate_username_is_a_conflict(api_main, database_url: str) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from backend.database import DuplicateUsernameError, register_new_user

    del api_main
    barrier = threading.Barrier(2)

    def attempt() -> str:
        barrier.wait(timeout=5)
        try:
            register_new_user(
                email="same-user@example.com",
                password_hash="hash",
                display_name="Same",
                database_url=database_url,
            )
        except DuplicateUsernameError:
            return "duplicate"
        return "created"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(lambda _: attempt(), (0, 1)))

    assert results == ["created", "duplicate"]


def test_concurrent_registrations_claim_legacy_data_once(
    api_main,
    database_url: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from backend.database import register_new_user

    del api_main
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (
                    date, merchant, amount, file_name, original_file_name,
                    uploaded_at, category, file_fingerprint, currency
                )
                VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s, %s)
                """,
                (
                    "2024-04-20",
                    "Legacy Coffee",
                    9.50,
                    "legacy-statement.pdf",
                    "Legacy Statement.pdf",
                    "Dining",
                    "legacy-sha1",
                    "USD",
                ),
            )
        connection.commit()

    barrier = threading.Barrier(2)

    def attempt(email: str) -> int:
        barrier.wait(timeout=5)
        _user, claimed = register_new_user(
            email=email,
            password_hash="hash",
            display_name=email,
            database_url=database_url,
        )
        return int(claimed["claimed_transaction_count"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed_counts = sorted(
            pool.map(
                attempt,
                ("first-owner@example.com", "second-owner@example.com"),
            )
        )

    assert claimed_counts == [0, 1]


def test_duplicate_statement_fingerprint_raises(
    api_main,
    database_url: str,
) -> None:
    from backend.database import (
        DuplicateStatementUploadError,
        create_statement_upload,
        register_new_user,
    )

    del api_main
    user, _claimed = register_new_user(
        email="uploader@example.com",
        password_hash="hash",
        display_name="Uploader",
        database_url=database_url,
    )
    payload = {
        "user_id": user["id"],
        "original_file_name": "Card.pdf",
        "stored_file_name": "stored-a.pdf",
        "file_fingerprint_sha256": "ab" * 32,
        "parse_status": "parsed",
        "database_url": database_url,
    }
    create_statement_upload(**payload)
    with pytest.raises(DuplicateStatementUploadError):
        create_statement_upload(
            **{**payload, "stored_file_name": "stored-b.pdf"}
        )


def test_concurrent_provider_selection_keeps_one_selected(
    api_main,
    database_url: str,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from backend.database import (
        list_user_ai_provider_settings,
        register_new_user,
        upsert_user_ai_provider_setting,
    )

    del api_main
    user, _claimed = register_new_user(
        email="ai-owner@example.com",
        password_hash="hash",
        display_name="AI Owner",
        database_url=database_url,
    )
    barrier = threading.Barrier(2)

    def save(provider: str) -> None:
        barrier.wait(timeout=5)
        upsert_user_ai_provider_setting(
            user_id=user["id"],
            provider=provider,  # type: ignore[arg-type]
            api_key="sk-test-key-value",
            model="model",
            is_selected=True,
            database_url=database_url,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(save, ("openai", "perplexity")))

    selected = [
        row
        for row in list_user_ai_provider_settings(user["id"], database_url)
        if row["is_selected"]
    ]
    assert len(selected) == 1
