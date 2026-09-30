from datetime import date

import pytest
from fastapi.testclient import TestClient

from backend.database import (
    count_statement_uploads,
    create_statement_upload,
    insert_transactions,
)


def _register_user(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "secure-password-123",
            "display_name": email,
        },
    )
    assert response.status_code == 201
    return response.json()["user"]


def _store_upload(
    database_url: str,
    user_id: int,
    *,
    name: str,
    parse_status: str = "parsed",
    transactions: list[dict] | None = None,
) -> None:
    upload = create_statement_upload(
        user_id=user_id,
        original_file_name=f"{name}.pdf",
        stored_file_name=f"{name}.pdf",
        file_fingerprint_sha256=f"{name}-{user_id}",
        parse_status=parse_status,
        currency="TWD",
        database_url=database_url,
    )
    if not transactions:
        return
    insert_transactions(
        transactions,
        user_id=user_id,
        file_name=upload["file_name"],
        original_file_name=upload["display_name"],
        statement_upload_id=upload["id"],
        database_url=database_url,
    )


def test_summary_and_dashboard_share_excluded_model_population(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client, "spending-model")
    _store_upload(
        database_url,
        user["id"],
        name="spending-model",
        transactions=[
            {
                "date": "2025-04-20",
                "merchant": "COFFEE SHOP",
                "amount": 120.0,
                "category": "Dining",
                "currency": "TWD",
            },
            {
                "date": "2025-04-21",
                "merchant": "信用卡繳款",
                "amount": 9000.0,
                "category": "Payment / Credit",
                "currency": "TWD",
            },
        ],
    )

    summary = client.get("/summary?currency=TWD")
    dashboard = client.get("/dashboard/view?currency=TWD")
    assert summary.status_code == 200
    assert dashboard.status_code == 200
    summary_payload = summary.json()
    dashboard_payload = dashboard.json()

    assert summary_payload["total_spending"] == pytest.approx(120.0)
    assert summary_payload["number_of_transactions"] == 1
    assert dashboard_payload["summary"]["total_spending"] == pytest.approx(120.0)
    assert dashboard_payload["metrics"]["model_total_in_active_currency"] == pytest.approx(
        120.0
    )
    assert dashboard_payload["metrics"]["selection_net"] == pytest.approx(120.0)
    assert [row["merchant"] for row in dashboard_payload["sorted_transactions"]] == [
        "COFFEE SHOP"
    ]
    assert [
        item["category"] for item in dashboard_payload["category_breakdown"]
    ] == ["Dining"]

    filtered = client.get("/dashboard/view?currency=TWD&search=nomatch")
    assert filtered.status_code == 200
    filtered_payload = filtered.json()
    assert filtered_payload["metrics"]["selection_net"] == pytest.approx(0.0)
    assert filtered_payload["metrics"]["model_total_in_active_currency"] == pytest.approx(
        120.0
    )
    assert filtered_payload["summary"]["total_spending"] == pytest.approx(120.0)
    assert filtered_payload["category_breakdown"] == []


def test_dashboard_limit_does_not_shrink_the_model_total(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client, "limit-user")
    _store_upload(
        database_url,
        user["id"],
        name="limit-upload",
        transactions=[
            {
                "date": "2025-04-20",
                "merchant": "OLDER LUNCH",
                "amount": 80.0,
                "category": "Dining",
                "currency": "TWD",
            },
            {
                "date": "2025-04-21",
                "merchant": "NEWER LUNCH",
                "amount": 40.0,
                "category": "Dining",
                "currency": "TWD",
            },
            {
                "date": "2025-04-22",
                "merchant": "信用卡繳款",
                "amount": 9000.0,
                "category": "Payment / Credit",
                "currency": "TWD",
            },
        ],
    )

    summary = client.get("/summary?currency=TWD")
    dashboard = client.get("/dashboard/view?currency=TWD&limit=1")
    assert summary.status_code == 200
    assert dashboard.status_code == 200
    assert summary.json()["total_spending"] == pytest.approx(120.0)
    payload = dashboard.json()
    assert payload["summary"]["total_spending"] == pytest.approx(120.0)
    assert payload["metrics"]["model_total_in_active_currency"] == pytest.approx(120.0)
    assert payload["summary"]["unconverted_transaction_count"] == 0
    assert [row["merchant"] for row in payload["sorted_transactions"]] == [
        "OLDER LUNCH"
    ]
    assert [item["category"] for item in payload["category_breakdown"]] == ["Dining"]


def test_dashboard_limit_caps_unconverted_rows_and_keeps_the_full_count(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client, "unconverted-limit-user")
    _store_upload(
        database_url,
        user["id"],
        name="unconverted-limit",
        transactions=[
            {
                "date": "2025-04-22",
                "merchant": "JPY CAFE",
                "amount": 500.0,
                "category": "Dining",
                "currency": "JPY",
            },
            {
                "date": "2025-04-21",
                "merchant": "EUR BOOKS",
                "amount": 8.0,
                "category": "Shopping",
                "currency": "EUR",
            },
            {
                "date": "2025-04-20",
                "merchant": "GBP RAIL",
                "amount": 12.0,
                "category": "Transit",
                "currency": "GBP",
            },
            {
                "date": "2025-04-19",
                "merchant": "TWD LUNCH",
                "amount": 100.0,
                "category": "Dining",
                "currency": "TWD",
            },
        ],
    )

    dashboard = client.get("/dashboard/view?currency=TWD&limit=1")
    summary = client.get("/summary?currency=TWD")
    assert dashboard.status_code == 200
    assert summary.status_code == 200
    payload = dashboard.json()
    assert payload["summary"]["total_spending"] == pytest.approx(100.0)
    assert payload["summary"]["unconverted_transaction_count"] == 3
    assert payload["summary"]["unconverted_currencies"] == ["EUR", "GBP", "JPY"]
    assert len(payload["unconverted_transactions"]) == 1
    assert payload["unconverted_transactions"][0]["original_currency"] == "JPY"
    assert summary.json()["unconverted_transaction_count"] == 3
    assert summary.json()["unconverted_currencies"] == ["EUR", "GBP", "JPY"]


def test_statement_count_is_all_time_and_includes_zero_row_uploads(
    client: TestClient,
    database_url: str,
) -> None:
    owner = _register_user(client, "statement-owner")
    _store_upload(
        database_url,
        owner["id"],
        name="empty-upload",
        parse_status="no_transactions",
    )
    _store_upload(
        database_url,
        owner["id"],
        name="old-upload",
        transactions=[
            {
                "date": "2020-01-01",
                "merchant": "OLD LUNCH",
                "amount": 80.0,
                "category": "Dining",
                "currency": "TWD",
            }
        ],
    )
    _store_upload(
        database_url,
        owner["id"],
        name="recent-upload",
        transactions=[
            {
                "date": date.today().isoformat(),
                "merchant": "TODAY LUNCH",
                "amount": 90.0,
                "category": "Dining",
                "currency": "TWD",
            }
        ],
    )

    full_range = client.get("/summary?currency=TWD&range=all")
    week_range = client.get("/summary?currency=TWD&range=week")
    assert full_range.status_code == 200
    assert week_range.status_code == 200
    assert full_range.json()["number_of_transactions"] == 2
    assert week_range.json()["number_of_transactions"] == 1
    assert full_range.json()["number_of_statements"] == 3
    assert week_range.json()["number_of_statements"] == 3
    assert full_range.json()["statement_count_scope"] == "all_time"
    assert week_range.json()["total_spending"] == pytest.approx(90.0)

    week_dashboard = client.get("/dashboard/view?currency=TWD&range=week")
    assert week_dashboard.status_code == 200
    assert week_dashboard.json()["summary"]["number_of_statements"] == 3
    assert week_dashboard.json()["summary"]["total_spending"] == pytest.approx(90.0)

    other = _register_user(client, "statement-other")
    _store_upload(
        database_url,
        other["id"],
        name="other-empty",
        parse_status="no_transactions",
    )
    other_summary = client.get("/summary?currency=TWD")
    assert other_summary.status_code == 200
    assert other_summary.json()["number_of_statements"] == 1
    assert other_summary.json()["number_of_transactions"] == 0
    assert count_statement_uploads(
        user_id=owner["id"],
        database_url=database_url,
    ) == 3


def test_jpy_and_eur_need_a_twd_base_or_stay_unconverted(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client, "fx-user")
    _store_upload(
        database_url,
        user["id"],
        name="fx-upload",
        transactions=[
            {
                "date": "2025-04-21",
                "merchant": "JPY WITH RATE",
                "amount": 1000.0,
                "category": "Dining",
                "currency": "JPY",
                "exchange_rate": 0.2,
            },
            {
                "date": "2025-04-20",
                "merchant": "EUR WITH RATE",
                "amount": 10.0,
                "category": "Shopping",
                "currency": "EUR",
                "exchange_rate": 35.0,
            },
            {
                "date": "2025-04-19",
                "merchant": "JPY WITHOUT RATE",
                "amount": 500.0,
                "category": "Dining",
                "currency": "JPY",
            },
            {
                "date": "2025-04-18",
                "merchant": "EUR WITHOUT RATE",
                "amount": 8.0,
                "category": "Shopping",
                "currency": "EUR",
            },
            {
                "date": "2025-04-17",
                "merchant": "TWD LUNCH",
                "amount": 100.0,
                "category": "Dining",
                "currency": "TWD",
            },
        ],
    )

    dashboard = client.get("/dashboard/view?currency=TWD")
    summary = client.get("/summary?currency=TWD")
    assert dashboard.status_code == 200
    assert summary.status_code == 200
    payload = dashboard.json()
    assert payload["summary"]["total_spending"] == pytest.approx(650.0)
    assert payload["metrics"]["model_total_in_active_currency"] == pytest.approx(650.0)
    assert summary.json()["total_spending"] == pytest.approx(650.0)
    assert summary.json()["number_of_transactions"] == 3
    assert summary.json()["unconverted_transaction_count"] == 2
    assert [row["merchant"] for row in payload["sorted_transactions"]] == [
        "EUR WITH RATE",
        "JPY WITH RATE",
        "TWD LUNCH",
    ]
    unconverted = {
        item["merchant"]: item for item in payload["unconverted_transactions"]
    }
    assert set(unconverted) == {"JPY WITHOUT RATE", "EUR WITHOUT RATE"}
    assert unconverted["JPY WITHOUT RATE"]["original_currency"] == "JPY"
    assert unconverted["JPY WITHOUT RATE"]["original_amount"] == pytest.approx(500.0)
    assert unconverted["JPY WITHOUT RATE"]["reason"] == "unsupported_display_conversion"
    assert unconverted["EUR WITHOUT RATE"]["original_currency"] == "EUR"

    jpy_row = next(
        row for row in payload["sorted_transactions"] if row["merchant"] == "JPY WITH RATE"
    )
    assert jpy_row["amount"] == pytest.approx(200.0)
    assert jpy_row["original_currency"] == "JPY"
    assert jpy_row["is_converted"] is True

    usd_summary = client.get("/summary?currency=USD")
    assert usd_summary.status_code == 200
    assert usd_summary.json()["total_spending"] == pytest.approx(650.0 / 32.0)
    assert usd_summary.json()["unconverted_transaction_count"] == 2


def test_insights_api_uses_the_spending_partition_and_the_ledger_keeps_every_row(
    client: TestClient,
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as api_main

    user = _register_user(client, "insight-api-user")
    today = date.today().isoformat()
    _store_upload(
        database_url,
        user["id"],
        name="insight-api",
        transactions=[
            {
                "date": today,
                "merchant": "JPY WITH RATE",
                "amount": 1000.0,
                "category": "Dining",
                "currency": "JPY",
                "exchange_rate": 0.2,
            },
            {
                "date": today,
                "merchant": "JPY WITHOUT RATE",
                "amount": 500.0,
                "category": "Dining",
                "currency": "JPY",
            },
            {
                "date": today,
                "merchant": "TWD LUNCH",
                "amount": 100.0,
                "category": "Dining",
                "currency": "TWD",
            },
            {
                "date": today,
                "merchant": "信用卡繳款",
                "amount": 9000.0,
                "category": "Payment / Credit",
                "currency": "TWD",
            },
        ],
    )
    saved = client.put(
        "/ai/settings/openai",
        json={"api_key": "sk-openai-test-1234", "model": "gpt-4.1", "selected": True},
    )
    assert saved.status_code == 200

    async def fake_generate_spending_insights(
        *,
        provider: str,
        api_key: str,
        model: str | None,
        snapshot: dict[str, object],
    ) -> dict[str, object]:
        assert provider == "openai"
        assert snapshot["transaction_count"] == 2
        assert snapshot["display_currency"] == "TWD"
        totals = snapshot["totals"]
        assert isinstance(totals, dict)
        assert totals["net"] == pytest.approx(300.0)
        assert totals["unconverted_transaction_count"] == 1
        assert totals["unconverted_currencies"] == ["JPY"]
        return {
            "provider": "openai",
            "model": model,
            "generated_at": "2026-09-30T00:00:00+00:00",
            "insight_text": "Observations\n- Converted base only",
            "citations": [],
        }

    monkeypatch.setattr(api_main, "generate_spending_insights", fake_generate_spending_insights)

    insights = client.post("/ai/insights", json={"range": "year", "language": "en"})
    assert insights.status_code == 200
    snapshot = insights.json()["snapshot"]
    assert snapshot["transaction_count"] == 2
    assert snapshot["totals"]["net"] == pytest.approx(300.0)
    assert snapshot["totals"]["unconverted_transaction_count"] == 1

    ledger = client.get("/transactions?range=all")
    assert ledger.status_code == 200
    rows = {row["merchant"]: row for row in ledger.json()["transactions"]}
    assert set(rows) == {
        "JPY WITH RATE",
        "JPY WITHOUT RATE",
        "TWD LUNCH",
        "信用卡繳款",
    }
    assert rows["JPY WITHOUT RATE"]["amount"] == pytest.approx(500.0)
    assert rows["JPY WITHOUT RATE"]["currency"] == "JPY"
    assert rows["信用卡繳款"]["amount"] == pytest.approx(9000.0)

    summary = client.get("/summary?currency=TWD&range=all")
    assert summary.status_code == 200
    assert summary.json()["total_spending"] == pytest.approx(300.0)
    assert summary.json()["number_of_transactions"] == 2
    assert summary.json()["unconverted_transaction_count"] == 1


def test_insights_api_rejects_a_period_with_only_unconverted_rows(
    client: TestClient,
    database_url: str,
) -> None:
    user = _register_user(client, "insight-unconverted-user")
    _store_upload(
        database_url,
        user["id"],
        name="eur-only",
        transactions=[
            {
                "date": date.today().isoformat(),
                "merchant": "EUR CAFE",
                "amount": 8.0,
                "category": "Dining",
                "currency": "EUR",
            }
        ],
    )
    saved = client.put(
        "/ai/settings/openai",
        json={"api_key": "sk-openai-test-1234", "model": "gpt-4.1", "selected": True},
    )
    assert saved.status_code == 200

    response = client.post("/ai/insights", json={"range": "year", "language": "en"})
    assert response.status_code == 400
    assert "USD or TWD" in response.json()["detail"]
