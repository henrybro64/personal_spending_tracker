import json
from io import BytesIO
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.requests import Request


def _register_user(
    client: TestClient,
    *,
    email: str = "alice@example.com",
    password: str = "password123",
    display_name: str = "Alice",
) -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": display_name,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_summary_requires_authentication(client: TestClient) -> None:
    response = client.get("/summary")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication is required."}


def test_ai_settings_round_trip_and_selection_switch(client: TestClient) -> None:
    _register_user(client)

    initial = client.get("/ai/settings")
    assert initial.status_code == 200
    initial_providers = {
        item["provider"]: item for item in initial.json()["providers"]
    }
    assert initial_providers["openai"]["configured"] is False
    assert initial_providers["perplexity"]["configured"] is False

    first_save = client.put(
        "/ai/settings/openai",
        json={
            "api_key": "sk-openai-test-1234",
            "model": "gpt-4.1",
            "selected": True,
        },
    )
    assert first_save.status_code == 200
    first_payload = first_save.json()
    assert first_payload["selected_provider"] == "openai"
    openai_settings = next(
        item for item in first_payload["providers"] if item["provider"] == "openai"
    )
    assert openai_settings["configured"] is True
    assert openai_settings["selected"] is True
    assert openai_settings["masked_api_key"] == "••••1234"

    second_save = client.put(
        "/ai/settings/perplexity",
        json={
            "api_key": "pplx-test-9876",
            "model": "sonar-pro",
            "selected": True,
        },
    )
    assert second_save.status_code == 200
    second_payload = second_save.json()
    assert second_payload["selected_provider"] == "perplexity"

    switch_back = client.put(
        "/ai/settings/openai",
        json={"api_key": "", "model": "gpt-4.1", "selected": True},
    )
    assert switch_back.status_code == 200
    switch_payload = switch_back.json()
    openai_settings = next(
        item for item in switch_payload["providers"] if item["provider"] == "openai"
    )
    perplexity_settings = next(
        item for item in switch_payload["providers"] if item["provider"] == "perplexity"
    )
    assert openai_settings["selected"] is True
    assert perplexity_settings["selected"] is False
    assert openai_settings["masked_api_key"] == "••••1234"
    assert perplexity_settings["masked_api_key"] == "••••9876"

    deleted = client.delete("/ai/settings/openai")
    assert deleted.status_code == 204

    after_delete = client.get("/ai/settings")
    assert after_delete.status_code == 200
    delete_payload = after_delete.json()
    assert delete_payload["selected_provider"] == "perplexity"
    openai_settings = next(
        item for item in delete_payload["providers"] if item["provider"] == "openai"
    )
    assert openai_settings["configured"] is False
    assert openai_settings["masked_api_key"] is None


def test_ai_insights_returns_generated_text_and_snapshot(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as api_main

    _register_user(client)
    save_settings = client.put(
        "/ai/settings/openai",
        json={
            "api_key": "sk-openai-test-1234",
            "model": "gpt-4.1",
            "selected": True,
        },
    )
    assert save_settings.status_code == 200

    today = date.today()
    for offset, merchant, amount in (
        (2, "Coffee Project", 18.5),
        (20, "OPENAI API", 25.0),
    ):
        created = client.post(
            "/transactions/manual",
            json={
                "date": (today - timedelta(days=offset)).isoformat(),
                "merchant": merchant,
                "amount": amount,
                "category": "Digital Services" if merchant == "OPENAI API" else "Dining",
                "payment_method": None,
            },
        )
        assert created.status_code == 201

    async def fake_generate_spending_insights(
        *,
        provider: str,
        api_key: str,
        model: str | None,
        snapshot: dict[str, object],
    ) -> dict[str, object]:
        assert provider == "openai"
        assert api_key == "sk-openai-test-1234"
        assert model == "gpt-4.1"
        assert snapshot["transaction_count"] == 2
        return {
            "provider": "openai",
            "model": "gpt-4.1",
            "generated_at": "2026-06-15T00:00:00+00:00",
            "insight_text": "Observations\n- Test insight",
            "citations": [],
        }

    monkeypatch.setattr(api_main, "generate_spending_insights", fake_generate_spending_insights)

    response = client.post(
        "/ai/insights",
        json={"range": "year", "language": "en"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4.1"
    assert payload["insight_text"] == "Observations\n- Test insight"
    assert payload["snapshot"]["transaction_count"] == 2
    assert payload["snapshot"]["display_currency"] == "TWD"


def test_ai_settings_requires_authentication(client: TestClient) -> None:
    response = client.get("/ai/settings")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication is required."}


def test_ai_settings_rejects_new_provider_without_api_key(client: TestClient) -> None:
    _register_user(client)

    response = client.put(
        "/ai/settings/openai",
        json={"api_key": "", "model": "gpt-4.1", "selected": True},
    )

    assert response.status_code == 400
    assert "API key is required" in response.json()["detail"]


def test_ai_insights_requires_configured_provider(client: TestClient) -> None:
    _register_user(client)

    response = client.post("/ai/insights", json={"range": "month", "language": "en"})

    assert response.status_code == 400
    assert "Configure an AI provider" in response.json()["detail"]


def test_ai_insights_rejects_empty_period(client: TestClient) -> None:
    _register_user(client)
    save_settings = client.put(
        "/ai/settings/openai",
        json={
            "api_key": "sk-openai-test-1234",
            "model": "gpt-4.1",
            "selected": True,
        },
    )
    assert save_settings.status_code == 200

    response = client.post("/ai/insights", json={"range": "year", "language": "en"})

    assert response.status_code == 400
    assert "No spending transactions are available" in response.json()["detail"]


def _request_for_upload() -> Request:
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
    request.state.request_id = "test-request-id"
    return request


@pytest.mark.anyio
async def test_upload_rejects_non_pdf_files_after_authentication(
    client: TestClient,
    api_main,
) -> None:
    user = _register_user(client)["user"]
    upload = UploadFile(
        file=BytesIO(b"not a pdf"),
        filename="statement.txt",
        headers=Headers({"content-type": "text/plain"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await api_main.upload_pdf(
            request=_request_for_upload(),
            file=upload,
            password=None,
            current_user=user,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Only PDF files are allowed"


def test_manual_transaction_rejects_empty_merchant(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "   ",
            "amount": 42.5,
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Merchant is required."}


def test_delete_dashboard_transaction(client: TestClient) -> None:
    _register_user(client)

    created = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Bad Parse Row",
            "amount": 123.45,
            "category": "Other Spending",
            "payment_method": None,
        },
    )
    assert created.status_code == 201
    transaction_id = created.json()["transaction"]["id"]

    deleted = client.delete(f"/transactions/{transaction_id}")
    assert deleted.status_code == 204

    missing = client.delete(f"/transactions/{transaction_id}")
    assert missing.status_code == 404

    summary = client.get("/summary")
    assert summary.status_code == 200
    assert summary.json()["number_of_transactions"] == 0


def test_manual_transaction_rejects_zero_amount(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "amount": 0,
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Amount must be positive."}


def test_manual_transaction_rejects_negative_amount(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": -10,
            "original_currency": "TWD",
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Amount must be positive."}


def _post_manual_raw_json(client: TestClient, payload: dict):
    # httpx refuses NaN in json=; json.dumps writes the NaN / Infinity
    # literals that a hand-crafted request would send.
    return client.post(
        "/transactions/manual",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["amount", "original_amount"])
def test_manual_transaction_rejects_non_finite_amount(
    client: TestClient, bad: float, field: str
) -> None:
    _register_user(client)
    response = _post_manual_raw_json(
        client,
        {"date": "2024-04-20", "merchant": "Coffee Project", field: bad},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Amount must be a finite number."}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_manual_transaction_rejects_non_finite_exchange_rate(
    client: TestClient, bad: float
) -> None:
    _register_user(client)
    response = _post_manual_raw_json(
        client,
        {
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": 10,
            "original_currency": "USD",
            "base_currency": "TWD",
            "exchange_rate": bad,
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Exchange rate must be a finite number."}


def test_manual_transaction_rejects_a_too_large_amount(client: TestClient) -> None:
    _register_user(client)
    for amount in (1_000_000_000.01, 4111111111111111):
        response = client.post(
            "/transactions/manual",
            json={"date": "2024-04-20", "merchant": "Coffee Project", "amount": amount},
        )
        assert response.status_code == 400
        assert response.json() == {"detail": "Amount is too large."}

    largest = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "amount": 1_000_000_000,
        },
    )
    assert largest.status_code == 201
    assert largest.json()["transaction"]["amount"] == pytest.approx(1_000_000_000)


def test_manual_transaction_rejects_a_rate_that_overflows_the_base_amount(
    client: TestClient,
) -> None:
    _register_user(client)
    too_large_rate = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": 10,
            "original_currency": "USD",
            "base_currency": "TWD",
            "exchange_rate": 1e12,
        },
    )
    assert too_large_rate.status_code == 400
    assert too_large_rate.json() == {"detail": "Exchange rate is too large."}

    too_large_base = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": 1_000_000_000,
            "original_currency": "USD",
            "base_currency": "TWD",
            "exchange_rate": 1_000_000,
        },
    )
    assert too_large_base.status_code == 400
    assert too_large_base.json() == {"detail": "Amount is too large."}


def test_manual_transaction_rejects_an_amount_that_rounds_to_zero(
    client: TestClient,
) -> None:
    _register_user(client)
    response = client.post(
        "/transactions/manual",
        json={"date": "2024-04-20", "merchant": "Coffee Project", "amount": 0.004},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Amount must be positive."}

    half_cent = client.post(
        "/transactions/manual",
        json={"date": "2024-04-20", "merchant": "Coffee Project", "amount": 0.005},
    )
    assert half_cent.status_code == 201
    assert half_cent.json()["transaction"]["amount"] == pytest.approx(0.01)


def test_dashboard_totals_stay_numeric_after_rejected_nan(client: TestClient) -> None:
    _register_user(client)
    rejected = _post_manual_raw_json(
        client,
        {"date": "2024-04-20", "merchant": "Coffee Project", "amount": float("nan")},
    )
    assert rejected.status_code == 400
    summary = client.get("/summary")
    assert summary.status_code == 200
    assert summary.json()["total_spending"] == 0
    assert summary.json()["number_of_transactions"] == 0


def test_manual_twd_transaction_sets_base_fields(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": 350,
            "original_currency": "TWD",
            "base_currency": "TWD",
            "category": "Dining",
            "payment_method": "Cash",
        },
    )

    assert response.status_code == 201
    transaction = response.json()["transaction"]
    assert transaction["amount"] == pytest.approx(350.0)
    assert transaction["currency"] == "TWD"
    assert transaction["original_amount"] == pytest.approx(350.0)
    assert transaction["original_currency"] == "TWD"
    assert transaction["base_amount"] == pytest.approx(350.0)
    assert transaction["base_currency"] == "TWD"
    assert transaction["exchange_rate"] == pytest.approx(1.0)
    assert transaction["exchange_rate_date"] == "2024-04-20"


def test_manual_usd_transaction_with_exchange_rate_calculates_base(
    client: TestClient,
) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2026-02-18",
            "merchant": "Amazon",
            "original_amount": 25.99,
            "original_currency": "USD",
            "base_currency": "TWD",
            "exchange_rate": 32.4,
            "exchange_rate_date": "2026-02-18",
            "category": "Shopping",
            "payment_method": "Chase",
        },
    )

    assert response.status_code == 201
    transaction = response.json()["transaction"]
    assert transaction["amount"] == pytest.approx(25.99)
    assert transaction["currency"] == "USD"
    assert transaction["base_amount"] == pytest.approx(842.08)
    assert transaction["base_currency"] == "TWD"
    assert transaction["exchange_rate"] == pytest.approx(32.4)
    assert transaction["exchange_rate_date"] == "2026-02-18"


def test_manual_usd_transaction_without_exchange_rate_leaves_base_null(
    client: TestClient,
) -> None:
    _register_user(client)

    created = client.post(
        "/transactions/manual",
        json={
            "date": "2026-02-18",
            "merchant": "Amazon",
            "original_amount": 25.99,
            "original_currency": "USD",
            "base_currency": "TWD",
            "category": "Shopping",
            "payment_method": "Chase",
        },
    )
    assert created.status_code == 201
    transaction = created.json()["transaction"]
    assert transaction["base_amount"] is None
    assert transaction["exchange_rate"] is None

    transactions = client.get("/transactions?range=all&limit=10")
    assert transactions.status_code == 200
    amazon = transactions.json()["transactions"][0]
    assert amazon["original_currency"] == "USD"
    assert amazon["base_amount"] is None


def test_manual_transaction_rejects_invalid_currency(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "original_amount": 42.5,
            "original_currency": "NTD",
            "category": None,
            "payment_method": None,
        },
    )

    assert response.status_code == 400
    assert "Invalid original currency" in response.json()["detail"]


def test_manual_transaction_legacy_amount_defaults_to_twd(client: TestClient) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2024-04-20",
            "merchant": "Coffee Project",
            "amount": 120,
            "category": "Dining",
            "payment_method": "Cash",
        },
    )

    assert response.status_code == 201
    transaction = response.json()["transaction"]
    assert transaction["original_amount"] == pytest.approx(120.0)
    assert transaction["original_currency"] == "TWD"
    assert transaction["base_amount"] == pytest.approx(120.0)
    assert transaction["exchange_rate"] == pytest.approx(1.0)


def test_manual_transaction_rejects_non_positive_exchange_rate(
    client: TestClient,
) -> None:
    _register_user(client)

    response = client.post(
        "/transactions/manual",
        json={
            "date": "2026-02-18",
            "merchant": "Amazon",
            "original_amount": 25.99,
            "original_currency": "USD",
            "base_currency": "TWD",
            "exchange_rate": 0,
            "category": "Shopping",
            "payment_method": "Chase",
        },
    )

    assert response.status_code == 400
    assert "Exchange rate must be greater than zero" in response.json()["detail"]


def test_dashboard_view_preserves_original_currency_and_recent_uploads_group_totals(
    client: TestClient,
    database_url: str,
) -> None:
    from backend.database import create_statement_upload, insert_transactions

    user = _register_user(client)["user"]
    upload = create_statement_upload(
        user_id=user["id"],
        original_file_name="Mixed Statement.pdf",
        stored_file_name="mixed-statement.pdf",
        file_fingerprint_sha256="mixed-sha256",
        parse_status="parsed",
        currency="USD",
        database_url=database_url,
    )
    insert_transactions(
        [
            {
                "date": "2025-04-21",
                "merchant": "OPENAI API",
                "amount": 20.0,
                "category": "Digital Services",
                "currency": "USD",
                "exchange_rate": 32.0,
                "exchange_rate_date": "2025-04-21",
            },
            {
                "date": "2025-04-20",
                "merchant": "UBER TRIP",
                "amount": 605.0,
                "category": "Transport",
                "currency": "TWD",
            },
        ],
        user_id=user["id"],
        file_name=upload["file_name"],
        original_file_name=upload["display_name"],
        statement_upload_id=upload["id"],
        database_url=database_url,
    )

    dashboard = client.get("/dashboard/view?currency=TWD&limit=10")
    assert dashboard.status_code == 200

    dashboard_rows = dashboard.json()["sorted_transactions"]
    openai_row = next(
        row for row in dashboard_rows if row["merchant"] == "OPENAI API"
    )
    assert openai_row["currency"] == "TWD"
    assert openai_row["display_currency"] == "TWD"
    assert openai_row["amount"] == 640.0
    assert openai_row["original_currency"] == "USD"
    assert openai_row["original_amount"] == 20.0
    assert openai_row["is_converted"] is True

    uber_row = next(row for row in dashboard_rows if row["merchant"] == "UBER TRIP")
    assert uber_row["currency"] == "TWD"
    assert uber_row["display_currency"] == "TWD"
    assert uber_row["amount"] == 605.0
    assert uber_row["original_currency"] == "TWD"
    assert uber_row["original_amount"] == 605.0
    assert uber_row["is_converted"] is False

    uploads = client.get("/uploads/recent?limit=5")
    assert uploads.status_code == 200
    upload_payload = uploads.json()["uploads"][0]
    currency_totals = {
        item["currency"]: {
            "total_amount": item["total_amount"],
            "transaction_count": item["transaction_count"],
        }
        for item in upload_payload["currency_totals"]
    }
    assert currency_totals == {
        "USD": {"total_amount": 20.0, "transaction_count": 1},
        "TWD": {"total_amount": 605.0, "transaction_count": 1},
    }


def test_first_registered_user_claims_legacy_single_user_rows(
    client: TestClient,
    database_url: str,
) -> None:
    import psycopg
    from backend.database import init_db

    init_db(database_url)
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (
                    date,
                    merchant,
                    amount,
                    file_name,
                    original_file_name,
                    uploaded_at,
                    category,
                    file_fingerprint,
                    currency
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

    register_payload = _register_user(
        client,
        email="legacy-owner@example.com",
        display_name="Legacy Owner",
    )
    assert register_payload["claimed_legacy_transactions"] == 1
    assert register_payload["claimed_legacy_uploads"] == 1

    summary = client.get("/summary")
    assert summary.status_code == 200
    assert summary.json()["number_of_transactions"] == 1

    uploads = client.get("/uploads/recent?limit=5")
    assert uploads.status_code == 200
    assert uploads.json()["uploads"][0]["display_name"] == "Legacy Statement.pdf"
    assert Path(uploads.json()["uploads"][0]["file_name"]).name == "legacy-statement.pdf"


def test_dashboard_view_does_not_return_the_unused_transactions_list(
    client: TestClient,
) -> None:
    _register_user(client)
    created = client.post(
        "/transactions/manual",
        json={"date": "2026-01-05", "merchant": "A", "amount": 10, "currency": "TWD"},
    )
    assert created.status_code == 201, created.text

    view = client.get("/dashboard/view", params={"currency": "TWD"})

    assert view.status_code == 200
    payload = view.json()
    assert "transactions" not in payload
    assert len(payload["sorted_transactions"]) == 1
    assert payload["summary"]["number_of_transactions"] == 1


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
    request.state.request_id = "upload-retry-test"
    return request


def test_renaming_merchant_keeps_hand_chosen_category(
    client: TestClient,
) -> None:
    registered = client.post(
        "/auth/register",
        json={"email": "alice", "password": "password123"},
    )
    assert registered.status_code == 201
    created = client.post(
        "/transactions/manual",
        json={
            "date": "2026-01-05",
            "merchant": "Starbuks",
            "amount": 100,
            "category": "Gifts & Donations",
        },
    ).json()["transaction"]
    patched = client.patch(
        f"/transactions/{created['id']}",
        json={"merchant": "Starbucks"},
    )
    assert patched.status_code == 200
    assert patched.json()["transaction"]["category"] == "Gifts & Donations"


def test_upload_can_be_retried_after_a_failed_database_write(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    from backend.database import (
        _insert_transaction_rows,
        count_statement_uploads,
        get_transactions,
    )

    with TestClient(api_main.app) as http:
        registered = http.post(
            "/auth/register",
            json={"email": "alice", "password": "password123"},
        )
    assert registered.status_code == 201

    def fake_load(path, password=None, **_kwargs):
        del path, password
        return "synthetic statement", [
            {
                "date": "2026-01-05",
                "merchant": "Cafe",
                "amount": 12.0,
                "category": "Dining",
                "currency": "USD",
            }
        ]

    calls = {"n": 0}

    def flaky_insert(cursor, insert_rows):
        calls["n"] += 1
        if calls["n"] == 1:
            raise psycopg.OperationalError("connection lost")
        return _insert_transaction_rows(cursor, insert_rows)

    monkeypatch.setattr(
        "backend.database._insert_transaction_rows",
        flaky_insert,
    )
    monkeypatch.setattr(api_main, "_load_uploaded_statement", fake_load)
    audit_context = api_main.get_audit_request_context(_upload_request())
    upload_dir = api_main._get_upload_dir(1)

    def store_once() -> None:
        api_main._store_uploaded_pdf(
            upload_stream=BytesIO(b"%PDF-1.4 synthetic"),
            original_name="statement.pdf",
            content_type="application/pdf",
            password=None,
            user_id=1,
            audit_context=audit_context,
        )

    with pytest.raises(psycopg.OperationalError):
        store_once()
    assert count_statement_uploads(user_id=1) == 0
    assert list(upload_dir.glob("*.pdf")) == []
    assert list(upload_dir.glob(".tmp-*")) == []

    store_once()
    assert count_statement_uploads(user_id=1) == 1
    assert len(get_transactions(user_id=1)) == 1


def test_parse_failure_removes_the_saved_pdf(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_load(path, password=None, **_kwargs):
        del path, password
        raise RuntimeError("ValueError: day is out of range for month")

    monkeypatch.setattr(api_main, "_load_uploaded_statement", failing_load)
    audit_context = api_main.get_audit_request_context(_upload_request())
    with TestClient(api_main.app) as http:
        registered = http.post(
            "/auth/register",
            json={"email": "alice", "password": "password123"},
        )
    assert registered.status_code == 201

    with pytest.raises(Exception) as caught:
        api_main._store_uploaded_pdf(
            upload_stream=BytesIO(b"%PDF-1.4 synthetic"),
            original_name="statement.pdf",
            content_type="application/pdf",
            password=None,
            user_id=1,
            audit_context=audit_context,
        )

    assert isinstance(caught.value, HTTPException)
    assert caught.value.status_code == 422
    assert caught.value.detail == "This PDF could not be read"
    assert list(api_main._get_upload_dir(1).glob("*")) == []


def test_audit_failure_after_success_does_not_fail_the_request(
    api_main,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import psycopg

    def fail_audit(**kwargs):
        del kwargs
        raise psycopg.OperationalError("audit down")

    monkeypatch.setattr(api_main, "record_audit_event", fail_audit)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        registered = client.post(
            "/auth/register",
            json={"email": "alice", "password": "password123"},
        )
        assert registered.status_code == 201
        created = client.post(
            "/transactions/manual",
            json={
                "date": "2026-01-05",
                "merchant": "Cafe",
                "amount": 10,
            },
        )
        assert created.status_code == 201, created.text
        listed = client.get("/transactions")
    assert listed.status_code == 200
    assert len(listed.json()["transactions"]) == 1


def test_bulk_import_to_dashboard_rejects_duplicates_and_bad_currency_cleanly(
    api_main,
) -> None:
    client = TestClient(api_main.app, raise_server_exceptions=False)
    with client:
        registered = client.post(
            "/auth/register",
            json={"email": "alice", "password": "password123"},
        )
        assert registered.status_code == 201
        body = {
            "content": "2026-01-05 Cafe 10.00",
            "destination": "dashboard",
            "currency": "TWD",
        }
        first = client.post("/batch-imports/import", json=body)
        assert first.status_code == 201, first.text
        second = client.post("/batch-imports/import", json=body)
        assert second.status_code == 409
        assert second.json()["detail"].startswith(
            "This statement was already imported as 'import' on "
        )
        bad = {
            "content": "2026-01-06 Cafe 12.00",
            "destination": "dashboard",
            "currency": "AUD",
        }
        rejected = client.post("/batch-imports/import", json=bad)
        assert rejected.status_code == 400
        detail = rejected.json()["detail"]
        assert detail.startswith("Row 1: Currency must be one of: ")
        assert "TWD" in detail
        assert "AUD" not in detail
        uploads = client.get("/uploads/recent")
        assert uploads.status_code == 200
        assert len(uploads.json()["uploads"]) == 1
        fixed = client.post(
            "/batch-imports/import",
            json={**bad, "currency": "USD"},
        )
        assert fixed.status_code == 201, fixed.text
