from io import BytesIO
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
    assert response.json() == {"detail": "Amount must be non-zero."}


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
