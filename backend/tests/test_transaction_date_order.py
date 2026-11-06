"""Regression coverage for GitHub issue #107: transaction date sorting."""

from fastapi.testclient import TestClient

from backend.database import create_statement_upload, get_transactions, insert_transactions, iter_transactions


def test_transactions_api_orders_by_date_before_limit_and_breaks_ties_by_id(
    client: TestClient, database_url: str
) -> None:
    signup = client.post(
        "/auth/register",
        json={
            "email": "chronological-user",
            "password": "secure-password-123",
            "display_name": "Chronological User",
        },
    )
    assert signup.status_code == 201
    user_id = signup.json()["user"]["id"]
    upload = create_statement_upload(
        user_id=user_id,
        original_file_name="October.pdf",
        stored_file_name="october.pdf",
        file_fingerprint_sha256="chronological-test-sha",
        parse_status="parsed",
        currency="TWD",
        database_url=database_url,
    )
    insert_transactions(
        [
            {"date": "2026-10-09", "merchant": "LATER", "amount": 900.0, "category": "Shopping", "currency": "TWD"},
            {"date": "2026-10-01", "merchant": "EARLIEST", "amount": 100.0, "category": "Shopping", "currency": "TWD"},
            {"date": "2026-10-03", "merchant": "SAME DAY A", "amount": 300.0, "category": "Shopping", "currency": "TWD"},
            {"date": "2026-10-03", "merchant": "SAME DAY B", "amount": 310.0, "category": "Shopping", "currency": "TWD"},
            {"date": "2026-10-06", "merchant": "MIDDLE", "amount": 600.0, "category": "Shopping", "currency": "TWD"},
        ],
        user_id=user_id,
        file_name=upload["file_name"],
        original_file_name=upload["display_name"],
        statement_upload_id=upload["id"],
        database_url=database_url,
    )

    all_rows = client.get("/transactions?range=all&limit=10")
    assert all_rows.status_code == 200
    rows = all_rows.json()["transactions"]
    assert [item["date"] for item in rows] == [
        "2026-10-01", "2026-10-03", "2026-10-03", "2026-10-06", "2026-10-09"
    ]
    same_day = [item for item in rows if item["date"] == "2026-10-03"]
    assert [item["id"] for item in same_day] == sorted(item["id"] for item in same_day)

    limited = client.get("/transactions?range=all&limit=3")
    assert limited.status_code == 200
    assert [item["id"] for item in limited.json()["transactions"]] == [
        item["id"] for item in rows[:3]
    ]
    assert [item["id"] for item in get_transactions(
        user_id=user_id, limit=3, date_order="asc", database_url=database_url
    )] == [item["id"] for item in rows[:3]]

    # Existing backend streaming/dashboard consumers retain descending order.
    streamed = list(iter_transactions(user_id=user_id, database_url=database_url))
    assert [item["date"] for item in streamed] == list(
        reversed([item["date"] for item in rows])
    )
