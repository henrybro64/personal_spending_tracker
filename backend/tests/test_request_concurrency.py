import asyncio
import os
import threading
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
    original_connect = psycopg.connect

    def slow_import(self) -> BatchImportResult:
        del self
        worker_threads.append(threading.get_ident())
        started.set()
        if worker_threads[0] == loop_thread:
            return _import_result()
        if not release.wait(timeout=3):
            raise TimeoutError("batch import was not released")
        return _import_result()

    def tracking_connect(*args, **kwargs):
        database_threads.append(threading.get_ident())
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(api_main.StatementImporter, "import_transactions", slow_import)
    monkeypatch.setattr(psycopg, "connect", tracking_connect)

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
