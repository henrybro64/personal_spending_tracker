import pytest

from backend.ai_insights import build_spending_insight_snapshot
from backend.database import StoredTransaction


def _transaction(
    *,
    transaction_id: int,
    date: str,
    merchant: str,
    amount: float,
    category: str = "Dining / Food",
    currency: str = "USD",
) -> StoredTransaction:
    return {
        "id": transaction_id,
        "date": date,
        "merchant": merchant,
        "amount": amount,
        "category": category,
        "currency": currency,
        "payment_method": None,
        "file_name": "manual-entry",
        "original_file_name": "manual-entry",
    }


def test_build_spending_insight_snapshot_aggregates_totals_and_rankings() -> None:
    transactions = [
        _transaction(
            transaction_id=1,
            date="2026-01-05",
            merchant="Coffee Project",
            amount=20.0,
            category="Dining / Food",
        ),
        _transaction(
            transaction_id=2,
            date="2026-01-18",
            merchant="Coffee Project",
            amount=15.0,
            category="Dining / Food",
        ),
        _transaction(
            transaction_id=3,
            date="2026-02-02",
            merchant="Grocery Mart",
            amount=80.0,
            category="Groceries",
        ),
        _transaction(
            transaction_id=4,
            date="2026-02-10",
            merchant="Coffee Project",
            amount=-5.0,
            category="Dining / Food",
        ),
    ]

    snapshot = build_spending_insight_snapshot(
        transactions,
        range_key="quarter",
        language="en",
    )

    assert snapshot["transaction_count"] == 4
    assert snapshot["display_currency"] == "USD"
    assert snapshot["date_start"] == "2026-01-05"
    assert snapshot["date_end"] == "2026-02-10"
    assert snapshot["totals"]["spend"] == 115.0
    assert snapshot["totals"]["refunds"] == 5.0
    assert snapshot["totals"]["net"] == 110.0
    assert snapshot["top_categories"][0]["label"] == "Groceries"
    assert snapshot["top_merchants"][0]["label"] == "Grocery Mart"
    assert snapshot["recurring_merchants"][0]["label"] == "Coffee Project"
    assert snapshot["recurring_merchants"][0]["count"] == 3
    assert snapshot["largest_expenses"][0]["merchant"] == "Grocery Mart"
    assert snapshot["merchant_concentration"]["distinct_merchants"] == 2


def test_build_spending_insight_snapshot_uses_dominant_currency() -> None:
    transactions = [
        _transaction(
            transaction_id=1,
            date="2026-03-01",
            merchant="Local Cafe",
            amount=1200.0,
            currency="TWD",
        ),
        _transaction(
            transaction_id=2,
            date="2026-03-02",
            merchant="Coffee Project",
            amount=12.0,
            currency="USD",
        ),
    ]

    snapshot = build_spending_insight_snapshot(
        transactions,
        range_key="month",
        language="en",
    )

    assert snapshot["display_currency"] == "TWD"
    assert snapshot["totals"]["by_original_currency"] == [
        {"currency": "TWD", "absolute_total": 1200.0},
        {"currency": "USD", "absolute_total": 12.0},
    ]


def test_build_spending_insight_snapshot_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="No transactions are available"):
        build_spending_insight_snapshot(
            [],
            range_key="month",
            language="en",
        )
