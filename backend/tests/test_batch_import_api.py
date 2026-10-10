import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.batch_import import BatchImportStore


AMEX_STATEMENT = (
    "AMERICAN EXPRESS\n"
    "Statement Date: 01/29/2026\n"
    "Card ending 1001\n"
    "Date Description Amount\n"
    "01/05 STARBUCKS 5.75\n"
    "01/05 STARBUCKS 5.75\n"
    "01/09 IKEA 80.00\n"
)


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


def _register(client: TestClient, username: str) -> None:
    response = client.post(
        "/auth/register",
        json={"email": username, "password": "password123"},
    )
    assert response.status_code == 201, response.text


def _logout(client: TestClient) -> None:
    assert client.post("/auth/logout").status_code == 200


def _write_statement(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-01-29.txt").write_text(AMEX_STATEMENT, encoding="utf-8")


def _statement_for_user(user_id: int, text: str = AMEX_STATEMENT) -> None:
    folder = Path(os.environ["BATCH_TEXT_SOURCE_DIR"]) / f"user-{user_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-01-29.txt").write_text(text, encoding="utf-8")


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
    # The registered user is id 1, so the statement goes in their folder.
    (text_dir / "user-1").mkdir()
    (text_dir / "user-1" / "2026-01-29.txt").write_text(
        (fixture_dir / "amex_statement.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    _register_user(client)
    return client


def test_batch_import_ignores_client_supplied_source_dir(batch_client: TestClient) -> None:
    response = batch_client.post(
        "/batch-imports/run",
        json={"pdf_source_dir": "/etc", "text_source_dir": "/etc"},
    )

    assert response.status_code == 201
    assert response.json()["run"]["statement_count"] == 1
    latest = batch_client.get("/batch-imports/latest").json()["run"]
    assert latest["pdf_source_dir"].endswith(os.path.join("pdf", "user-1"))
    assert latest["text_source_dir"].endswith(os.path.join("text", "user-1"))


def test_batch_source_folder_is_per_user_even_when_env_var_is_set(
    client: TestClient,
) -> None:
    text_root = Path(os.environ["BATCH_TEXT_SOURCE_DIR"])
    # A file left in the shared root must not be imported by anyone.
    _write_statement(text_root)
    _write_statement(text_root / "user-1")

    _register(client, "alice")
    alice_run = client.post("/batch-imports/run", json={})
    assert alice_run.status_code == 201, alice_run.text
    assert alice_run.json()["run"]["statement_count"] == 1
    assert client.get("/batch-imports/latest").json()["run"]["text_source_dir"] == str(
        (text_root / "user-1").resolve()
    )
    _logout(client)

    _register(client, "mallory")
    run = client.post("/batch-imports/run", json={})
    assert run.status_code == 201, run.text
    assert run.json()["run"]["transaction_count"] == 0
    assert client.get("/batch-imports/latest").json()["run"]["text_source_dir"] == str(
        (text_root / "user-2").resolve()
    )


def test_batch_run_logs_statements_left_in_shared_root(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    _write_statement(Path(os.environ["BATCH_TEXT_SOURCE_DIR"]))
    _register(client, "alice")

    with caplog.at_level("WARNING", logger="backend.main"):
        run = client.post("/batch-imports/run")

    assert run.status_code == 201, run.text
    assert run.json()["run"]["statement_count"] == 0
    assert "1 statement file(s) in the root" in caplog.text
    assert "2026-01-29.txt" not in run.text


def test_batch_api_ignores_client_supplied_paths(api_main, tmp_path: Path) -> None:
    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        _write_statement(Path(os.environ["BATCH_TEXT_SOURCE_DIR"]) / "user-1")
        _register(client, "alice")
        alice_run = client.post("/batch-imports/run", json={})
        assert alice_run.status_code == 201, alice_run.text
        alice_db = str(api_main._default_batch_sqlite_path_for_user(1))
        alice_rows = client.get("/batch-imports/transactions").json()["transactions"]
        assert alice_rows
        _logout(client)
        _register(client, "mallory")

        # cannot read alice's rows
        r = client.get("/batch-imports/transactions", params={"sqlite_path": alice_db})
        assert r.status_code == 200
        assert r.json()["transactions"] == []
        r = client.get("/batch-imports/latest", params={"sqlite_path": alice_db})
        assert r.status_code == 200
        assert r.json()["run"] is None

        # cannot delete alice's rows
        r = client.delete(
            f"/batch-imports/transactions/{alice_rows[0]['id']}",
            params={"sqlite_path": alice_db},
        )
        assert r.status_code == 404

        # cannot overwrite alice's database
        r = client.post("/batch-imports/run", json={"csv_output_path": alice_db})
        assert r.status_code == 201
        assert Path(alice_db).read_bytes().startswith(b"SQLite format 3")
        r = client.post(
            "/batch-imports/import",
            json={
                "content": json.dumps(
                    [{"date": "2026-02-01", "merchant": "MALLORY", "amount": 1.0}]
                ),
                "format": "json",
                "sqlite_output_path": alice_db,
            },
        )
        assert r.status_code == 201, r.text
        mallory_db = api_main._default_batch_sqlite_path_for_user(2)
        assert [row["merchant_raw"] for row in BatchImportStore(mallory_db).list_transactions()] == [
            "MALLORY"
        ]
        r = client.post("/batch-imports/merge", json={"sqlite_output_path": alice_db})
        assert r.status_code == 200, r.text
        assert r.json()["merged_count"] == 1  # mallory's own row only

        # cannot create directories anywhere on disk
        outside = tmp_path / "outside" / "deep" / "x.sqlite3"
        r = client.get("/batch-imports/latest", params={"sqlite_path": str(outside)})
        assert r.status_code == 200
        assert not outside.parent.exists()

    alice_store = BatchImportStore(Path(alice_db))
    assert len(alice_store.list_transactions(limit=500)) == len(alice_rows)
    assert not any(alice_store.is_transaction_synced(row["id"]) for row in alice_rows)


def test_batch_import_run_and_merge_use_sandboxed_paths(
    batch_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merged: list[dict[str, object]] = []

    def fake_import_statements(*, statements, **kwargs):
        from backend.database import InsertTransactionsResult

        del kwargs
        results = []
        for statement in statements:
            merged.extend(statement.transactions)
            results.append(
                (
                    {
                        "id": 1,
                        "file_name": "batch.json",
                        "display_name": "Batch import",
                        "uploaded_at": "2026-01-01T00:00:00+00:00",
                        "parse_status": "parsed",
                    },
                    InsertTransactionsResult(
                        inserted_count=len(statement.transactions),
                        inserted_source_keys=frozenset(),
                    ),
                )
            )
        return results

    monkeypatch.setattr(
        "backend.batch_import.merge.import_statements",
        fake_import_statements,
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


def test_batch_endpoints_accept_no_file_or_directory_paths(api_main) -> None:
    spec = api_main.app.openapi()
    schemas = spec["components"]["schemas"]

    def body_fields(op: dict) -> list[str]:
        content = op.get("requestBody", {}).get("content", {})
        fields: list[str] = []
        for media in content.values():
            schema = media.get("schema", {})
            refs = [schema] + schema.get("anyOf", [])
            for ref in refs:
                if "$ref" in ref:
                    name = ref["$ref"].rsplit("/", 1)[-1]
                    fields.extend(schemas[name].get("properties", {}))
        return fields

    checked = 0
    offenders: list[str] = []
    for route, ops in spec["paths"].items():
        if not route.startswith("/batch-imports"):
            continue
        for method, op in ops.items():
            checked += 1
            names = [p["name"] for p in op.get("parameters", [])] + body_fields(op)
            offenders.extend(
                f"{method.upper()} {route}: {name}"
                for name in names
                if "path" in name or "dir" in name
            )

    assert checked >= 12
    assert offenders == []


def test_rerunning_batch_import_and_merging_again_does_not_duplicate(
    client: TestClient,
) -> None:
    _statement_for_user(1)
    _register(client, "alice")
    merged_counts: list[int] = []
    skipped_counts: list[int] = []
    for _ in range(2):
        run = client.post("/batch-imports/run", json={}).json()["run"]
        merged = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert merged.status_code == 200, merged.text
        merged_counts.append(merged.json()["merged_count"])
        skipped_counts.append(merged.json()["skipped_count"])
    rows = client.get("/transactions").json()["transactions"]
    assert sorted(row["merchant"] for row in rows) == [
        "IKEA",
        "STARBUCKS",
        "STARBUCKS",
    ]
    assert merged_counts == [3, 0]
    assert skipped_counts == [0, 3]


def test_remerge_after_upgrade_backfills_legacy_null_source_keys(
    client: TestClient,
    database_url: str,
) -> None:
    """Rows imported before source_key existed must not duplicate on re-merge."""
    import psycopg

    _statement_for_user(1)
    _register(client, "alice")
    run = client.post("/batch-imports/run", json={}).json()["run"]
    merged = client.post("/batch-imports/merge", json={"run_id": run["run_id"]})
    assert merged.status_code == 200, merged.text
    assert merged.json()["merged_count"] == 3

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE transactions SET source_key = NULL WHERE user_id = 1"
            )
        connection.commit()

    run_again = client.post("/batch-imports/run", json={}).json()["run"]
    rematch = client.post(
        "/batch-imports/merge",
        json={"run_id": run_again["run_id"]},
    )
    assert rematch.status_code == 200, rematch.text
    assert rematch.json()["merged_count"] == 0
    rows = client.get("/transactions").json()["transactions"]
    assert sorted(row["merchant"] for row in rows) == [
        "IKEA",
        "STARBUCKS",
        "STARBUCKS",
    ]


def test_reimport_does_not_duplicate_pre_sign_fix_refund_keys(
    client: TestClient,
    database_url: str,
) -> None:
    """Refunds hashed with abs(amount) before #74 must not insert again."""
    import psycopg

    from backend.database import create_statement_upload, insert_transactions
    from backend.transaction_source import (
        legacy_source_keys_for_transactions,
        source_keys_for_transactions,
    )

    _register(client, "alice")
    refund = {
        "date": "2026-01-09",
        "merchant": "AMAZON",
        "amount": -25.0,
        "category": "Shopping / Online Retail",
        "currency": "USD",
        "card_last4": "1001",
    }
    legacy_key = legacy_source_keys_for_transactions([refund])[0]
    new_key = source_keys_for_transactions([refund])[0]
    assert legacy_key != new_key

    upload = create_statement_upload(
        user_id=1,
        original_file_name="refund.pdf",
        stored_file_name="refund.pdf",
        file_fingerprint_sha256="pre-sign-fix-refund",
        parse_status="parsed",
        currency="USD",
        database_url=database_url,
    )
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (
                    user_id,
                    statement_upload_id,
                    date,
                    merchant,
                    amount,
                    file_name,
                    original_file_name,
                    category,
                    currency,
                    original_amount,
                    original_currency,
                    base_amount,
                    base_currency,
                    source_key
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    1,
                    upload["id"],
                    refund["date"],
                    refund["merchant"],
                    refund["amount"],
                    upload["file_name"],
                    upload["display_name"],
                    refund["category"],
                    "USD",
                    refund["amount"],
                    "USD",
                    refund["amount"],
                    "USD",
                    legacy_key,
                ),
            )
        connection.commit()

    inserted = insert_transactions(
        [refund],
        user_id=1,
        file_name=upload["file_name"],
        original_file_name=upload["display_name"],
        statement_upload_id=upload["id"],
        database_url=database_url,
    )
    assert inserted.inserted_count == 0

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_key, COUNT(*)
                FROM transactions
                WHERE user_id = 1 AND merchant = 'AMAZON'
                GROUP BY source_key
                """
            )
            rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == new_key
    assert rows[0][1] == 1


def _amex_upload_transactions() -> list[dict[str, object]]:
    return [
        {
            "date": "2026-01-05",
            "merchant": "STARBUCKS",
            "amount": 5.75,
            "category": "Dining / Food",
            "currency": "USD",
        },
        {
            "date": "2026-01-05",
            "merchant": "STARBUCKS",
            "amount": 5.75,
            "category": "Dining / Food",
            "currency": "USD",
        },
        {
            "date": "2026-01-09",
            "merchant": "IKEA",
            "amount": 80.0,
            "category": "Shopping",
            "currency": "USD",
        },
    ]


def _upload_amex_then_merge(
    client: TestClient,
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    _statement_for_user(1)
    _register(client, "alice")

    def fake_load(path, password=None, **_kwargs):
        del path, password
        return AMEX_STATEMENT, _amex_upload_transactions()

    monkeypatch.setattr(api_main, "_load_uploaded_statement", fake_load)
    uploaded = client.post(
        "/upload",
        files={
            "file": (
                "statement.pdf",
                b"%PDF-1.4 synthetic",
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    run = client.post("/batch-imports/run", json={})
    assert run.status_code == 201, run.text
    merged = client.post(
        "/batch-imports/merge",
        json={"run_id": run.json()["run"]["run_id"]},
    )
    assert merged.status_code == 200, merged.text
    return merged.json()


def test_upload_then_batch_import_keeps_one_copy_of_each_transaction(
    client: TestClient,
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merged = _upload_amex_then_merge(client, api_main, monkeypatch)
    assert merged["merged_count"] == 0
    assert merged["skipped_count"] == 3
    rows = client.get("/transactions").json()["transactions"]
    assert sorted(row["merchant"] for row in rows) == [
        "IKEA",
        "STARBUCKS",
        "STARBUCKS",
    ]
    # Shared text fingerprint means batch reuses the upload record.
    assert len(client.get("/uploads/recent").json()["uploads"]) == 1


def test_deleting_deduped_batch_row_leaves_upload_dashboard_rows(
    client: TestClient,
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merge that inserts nothing must not link batch rows for delete (#76)."""
    merged = _upload_amex_then_merge(client, api_main, monkeypatch)
    assert merged["merged_count"] == 0

    before = client.get("/transactions").json()["transactions"]
    assert len(before) == 3

    batch_rows = client.get("/batch-imports/transactions").json()[
        "transactions"
    ]
    one = next(row for row in batch_rows if row["merchant_raw"] == "STARBUCKS")
    deleted = client.delete(f"/batch-imports/transactions/{one['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["dashboard_row_deleted"] is False

    after = client.get("/transactions").json()["transactions"]
    assert len(after) == 3
    assert sorted(row["merchant"] for row in after) == [
        "IKEA",
        "STARBUCKS",
        "STARBUCKS",
    ]


def test_deleting_one_batch_row_deletes_only_its_own_dashboard_row(client):
    _statement_for_user(1)
    _register(client, "alice")
    manual = client.post(
        "/transactions/manual",
        json={
            "date": "2026-01-05",
            "merchant": "STARBUCKS",
            "amount": 5.75,
            "currency": "USD",
            "base_currency": "USD",
        },
    )
    assert manual.status_code == 201, manual.text
    run = client.post("/batch-imports/run", json={}).json()["run"]
    merged = client.post(
        "/batch-imports/merge",
        json={"run_id": run["run_id"]},
    )
    assert merged.status_code == 200, merged.text

    batch_rows = client.get("/batch-imports/transactions").json()[
        "transactions"
    ]
    one = next(row for row in batch_rows if row["merchant_raw"] == "STARBUCKS")
    deleted = client.delete(f"/batch-imports/transactions/{one['id']}")
    assert deleted.status_code == 204

    remaining = [
        row
        for row in client.get("/transactions").json()["transactions"]
        if row["merchant"] == "STARBUCKS"
    ]
    assert len(remaining) == 2


def test_deleting_a_renamed_dashboard_row_still_removes_that_row(client):
    _statement_for_user(1)
    _register(client, "alice")
    run = client.post("/batch-imports/run", json={}).json()["run"]
    merged = client.post(
        "/batch-imports/merge",
        json={"run_id": run["run_id"]},
    )
    assert merged.status_code == 200, merged.text

    starbucks = [
        row
        for row in client.get("/transactions").json()["transactions"]
        if row["merchant"] == "STARBUCKS"
    ]
    assert len(starbucks) == 2
    renamed_names = []
    for index, row in enumerate(starbucks):
        name = f"RENAMED {index}"
        renamed = client.patch(
            f"/transactions/{row['id']}",
            json={"merchant": name},
        )
        assert renamed.status_code == 200, renamed.text
        renamed_names.append(name)

    batch_rows = client.get("/batch-imports/transactions").json()[
        "transactions"
    ]
    one = next(row for row in batch_rows if row["merchant_raw"] == "STARBUCKS")
    deleted = client.delete(f"/batch-imports/transactions/{one['id']}")
    assert deleted.status_code == 204

    remaining_names = {
        row["merchant"]
        for row in client.get("/transactions").json()["transactions"]
    }
    still_renamed = [name for name in renamed_names if name in remaining_names]
    assert len(still_renamed) == 1
    assert "STARBUCKS" not in remaining_names
    assert "IKEA" in remaining_names


def test_deleting_an_unlinked_merged_row_leaves_dashboard_rows(client):
    import sqlite3

    from backend.main import _default_batch_sqlite_path_for_user

    _statement_for_user(1)
    _register(client, "alice")
    run = client.post("/batch-imports/run", json={}).json()["run"]
    merged = client.post(
        "/batch-imports/merge",
        json={"run_id": run["run_id"]},
    )
    assert merged.status_code == 200, merged.text
    before = len(client.get("/transactions").json()["transactions"])

    path = _default_batch_sqlite_path_for_user(1)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE imported_transactions SET postgres_source_key = NULL"
        )

    batch_rows = client.get("/batch-imports/transactions").json()[
        "transactions"
    ]
    one = next(row for row in batch_rows if row["merchant_raw"] == "STARBUCKS")
    deleted = client.delete(f"/batch-imports/transactions/{one['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["dashboard_row_deleted"] is False
    after = client.get("/transactions").json()["transactions"]
    assert len(after) == before


SECOND_STATEMENT = (
    "AMERICAN EXPRESS\n"
    "Statement Date: 02/02/2026\n"
    "Card ending 1002\n"
    "Date Description Amount\n"
    "02/01 CAFE 4.00\n"
)


def test_merge_can_be_retried_after_a_failure(api_main, monkeypatch) -> None:
    import sqlite3

    from backend.batch_import.storage import BatchImportStore

    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        _statement_for_user(1)
        _register(client, "alice")
        run = client.post("/batch-imports/run", json={}).json()["run"]
        real = BatchImportStore.mark_transactions_synced
        calls = {"n": 0}

        def flaky(self, ids):
            calls["n"] += 1
            if calls["n"] == 1:
                # Real SQLite failures are sqlite3.Error, not only OSError.
                raise sqlite3.OperationalError("disk hiccup")
            return real(self, ids)

        monkeypatch.setattr(
            BatchImportStore,
            "mark_transactions_synced",
            flaky,
        )
        first = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert first.status_code == 500
        assert "merged_count=" in first.json()["detail"]
        retry = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert retry.status_code == 200, retry.text
        rows = client.get("/transactions").json()["transactions"]
        assert len(rows) == 3
        assert retry.json()["merged_count"] == 0
        assert retry.json()["skipped_count"] == 3


def test_merge_retry_restores_postgres_source_key_links(
    api_main,
    monkeypatch,
) -> None:
    """Crash after Postgres commit must still link batch rows on retry (#81/#76)."""
    import sqlite3

    from backend.batch_import.paths import default_sqlite_output_path
    from backend.batch_import.storage import BatchImportStore

    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        _statement_for_user(1)
        _register(client, "alice")
        run = client.post("/batch-imports/run", json={}).json()["run"]
        real_set_keys = BatchImportStore.set_postgres_source_keys
        calls = {"n": 0}

        def flaky_set_keys(self, keys_by_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise sqlite3.OperationalError("link write failed")
            return real_set_keys(self, keys_by_id)

        monkeypatch.setattr(
            BatchImportStore,
            "set_postgres_source_keys",
            flaky_set_keys,
        )
        first = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert first.status_code == 500
        assert "merged_count=" in first.json()["detail"]

        store = BatchImportStore(
            default_sqlite_output_path().with_name("batch_imports_user_1.sqlite3")
        )
        batch_rows = store.list_transactions(run_id=run["run_id"])
        assert batch_rows
        assert all(store.get_postgres_source_key(row["id"]) is None for row in batch_rows)

        retry = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert retry.status_code == 200, retry.text
        assert len(client.get("/transactions").json()["transactions"]) == 3
        linked = [
            store.get_postgres_source_key(row["id"])
            for row in store.list_transactions(run_id=run["run_id"])
            if not row["exclude_from_spending"]
        ]
        assert linked
        assert all(key is not None for key in linked)


def test_failed_second_statement_rolls_back_the_whole_merge(
    api_main,
    monkeypatch,
) -> None:
    import backend.database as database

    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        _statement_for_user(1)
        folder = Path(os.environ["BATCH_TEXT_SOURCE_DIR"]) / "user-1"
        (folder / "2026-02-02.txt").write_text(
            SECOND_STATEMENT,
            encoding="utf-8",
        )
        _register(client, "alice")
        started = client.post("/batch-imports/run", json={})
        assert started.status_code == 201, started.text
        run = started.json()["run"]
        assert run["statement_count"] == 2

        real_insert = database._insert_transaction_rows
        calls = {"n": 0}

        def flaky_insert(cursor, insert_rows):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("second statement insert failed")
            return real_insert(cursor, insert_rows)

        monkeypatch.setattr(
            database,
            "_insert_transaction_rows",
            flaky_insert,
        )
        failed = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert failed.status_code >= 400
        assert database.count_statement_uploads(user_id=1) == 0
        assert database.get_transactions(user_id=1) == []

        retry = client.post(
            "/batch-imports/merge",
            json={"run_id": run["run_id"]},
        )
        assert retry.status_code == 200, retry.text
        rows = client.get("/transactions").json()["transactions"]
        assert len(rows) == 4
        assert calls["n"] >= 3
