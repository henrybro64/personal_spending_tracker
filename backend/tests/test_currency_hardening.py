from fastapi.testclient import TestClient
import pytest

from backend.database import create_statement_upload, insert_transactions


def _register_user(client: TestClient) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": "currency-user",
            "password": "secure-password-123",
            "display_name": "Currency User",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_summary_converts_mixed_currency_totals(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client)["user"]
    upload = create_statement_upload(
        user_id=user["id"],
        original_file_name="Mixed.pdf",
        stored_file_name="mixed.pdf",
        file_fingerprint_sha256="mixed-currency-sha",
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

    summary = client.get("/summary?currency=TWD")
    assert summary.status_code == 200
    assert summary.json()["total_spending"] == 1245.0
    assert summary.json()["number_of_transactions"] == 2

    dashboard = client.get("/dashboard/view?currency=TWD&limit=10")
    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["exchange_rate"]["usd_twd_rate"] == 32.0
    assert payload["exchange_rate"]["rate_source"] == "static_env"
    assert payload["summary"]["total_spending"] == 1245.0


def test_dashboard_excludes_unconverted_cross_currency_from_base_totals(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client)["user"]
    upload = create_statement_upload(
        user_id=user["id"],
        original_file_name="Unconverted.pdf",
        stored_file_name="unconverted.pdf",
        file_fingerprint_sha256="unconverted-currency-sha",
        parse_status="parsed",
        currency="USD",
        database_url=database_url,
    )
    insert_transactions(
        [
            {
                "date": "2025-04-21",
                "merchant": "USD WITHOUT RATE",
                "amount": 20.0,
                "category": "Digital Services",
                "currency": "USD",
            },
            {
                "date": "2025-04-20",
                "merchant": "TWD LUNCH",
                "amount": 605.0,
                "category": "Dining",
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
    payload = dashboard.json()
    assert payload["summary"]["total_spending"] == pytest.approx(605.0)
    assert [row["merchant"] for row in payload["sorted_transactions"]] == [
        "TWD LUNCH"
    ]


def test_summary_excludes_unconverted_cross_currency_from_display_totals(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client)["user"]
    upload = create_statement_upload(
        user_id=user["id"],
        original_file_name="Unconverted.pdf",
        stored_file_name="unconverted.pdf",
        file_fingerprint_sha256="unconverted-summary-sha",
        parse_status="parsed",
        currency="USD",
        database_url=database_url,
    )
    insert_transactions(
        [
            {
                "date": "2025-04-21",
                "merchant": "USD WITHOUT RATE",
                "amount": 20.0,
                "category": "Digital Services",
                "currency": "USD",
            },
            {
                "date": "2025-04-20",
                "merchant": "TWD LUNCH",
                "amount": 605.0,
                "category": "Dining",
                "currency": "TWD",
            },
        ],
        user_id=user["id"],
        file_name=upload["file_name"],
        original_file_name=upload["display_name"],
        statement_upload_id=upload["id"],
        database_url=database_url,
    )

    summary = client.get("/summary?currency=TWD")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["total_spending"] == pytest.approx(605.0)
    assert payload["number_of_transactions"] == 2
    assert payload["average_transaction_amount"] == pytest.approx(605.0)


def test_migrations_apply_on_empty_database(database_url: str) -> None:
    from backend.migrate import migrate_database

    applied = migrate_database(database_url)
    assert applied == []

    with __import__("psycopg").connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT version FROM schema_migrations ORDER BY version ASC"
            )
            versions = [row[0] for row in cursor.fetchall()]

    assert versions == [
        "001_initial_schema",
        "002_backfill_statement_uploads",
        "003_harden_user_foreign_keys",
        "004_ai_provider_settings",
        "005_transaction_original_base_amounts",
        "006_one_selected_ai_provider",
    ]


def test_summary_rejects_invalid_display_currency(client: TestClient) -> None:
    _register_user(client)
    response = client.get("/summary?currency=TDW")
    assert response.status_code == 400
    assert "Invalid display currency" in response.json()["detail"]


def test_dashboard_rejects_invalid_display_currency(client: TestClient) -> None:
    _register_user(client)
    response = client.get("/dashboard/view?currency=NTD")
    assert response.status_code == 400
    assert "Invalid display currency" in response.json()["detail"]
