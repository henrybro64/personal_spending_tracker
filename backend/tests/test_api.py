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
