import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _register_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": "batch@example.com",
            "password": "password123",
            "display_name": "Batch User",
        },
    )
    assert response.status_code == 201


@pytest.fixture
def batch_client(client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    batch_root = tmp_path / "batch"
    pdf_dir = batch_root / "pdf"
    text_dir = batch_root / "text"
    data_dir = batch_root / "data"
    pdf_dir.mkdir(parents=True)
    text_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    monkeypatch.setenv("BATCH_DATA_DIR", str(data_dir))
    monkeypatch.setenv("BATCH_PDF_SOURCE_DIR", str(pdf_dir))
    monkeypatch.setenv("BATCH_TEXT_SOURCE_DIR", str(text_dir))

    fixture_dir = Path(__file__).parent / "fixtures" / "batch_import"
    (text_dir / "2026-01-29.txt").write_text(
        (fixture_dir / "amex_statement.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    _register_user(client)
    return client


def test_batch_import_rejects_paths_outside_allowed_roots(batch_client: TestClient) -> None:
    response = batch_client.post(
        "/batch-imports/run",
        json={"pdf_source_dir": "/etc"},
    )

    assert response.status_code == 400
    assert "outside allowed batch-import roots" in response.json()["detail"]


def test_batch_import_run_and_merge_use_sandboxed_paths(
    batch_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merged: list[dict[str, object]] = []

    def fake_insert_transactions(transactions, file_name, **kwargs):
        merged.extend(transactions)
        return len(transactions)

    monkeypatch.setattr(
        "backend.batch_import.merge.insert_transactions",
        fake_insert_transactions,
    )

    run_response = batch_client.post("/batch-imports/run", json={})
    assert run_response.status_code == 201
    run_payload = run_response.json()["run"]
    assert run_payload["statement_count"] == 1
    assert run_payload["transaction_count"] == 2

    merge_response = batch_client.post(
        "/batch-imports/merge",
        json={"run_id": run_payload["run_id"]},
    )
    assert merge_response.status_code == 200
    merge_payload = merge_response.json()
    assert merge_payload["merged_count"] == 1
    assert merge_payload["skipped_count"] == 0
    assert len(merged) == 1
    assert merged[0]["merchant"] == "STARBUCKS"

    second_merge = batch_client.post(
        "/batch-imports/merge",
        json={"run_id": run_payload["run_id"]},
    )
    assert second_merge.status_code == 200
    assert second_merge.json()["merged_count"] == 0
    assert second_merge.json()["skipped_count"] == 1
