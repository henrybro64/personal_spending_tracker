import pytest

from backend.ai_insights import (
    build_spending_insight_snapshot,
    generate_spending_insights,
)
from backend.database import StoredTransaction


def _transaction(
    *,
    transaction_id: int,
    date: str,
    merchant: str,
    amount: float,
    category: str = "Dining / Food",
    currency: str = "USD",
    base_amount: float | None = None,
    base_currency: str = "TWD",
) -> StoredTransaction:
    return {
        "id": transaction_id,
        "date": date,
        "merchant": merchant,
        "amount": amount,
        "category": category,
        "currency": currency,
        "original_amount": amount,
        "original_currency": currency,
        "base_amount": base_amount,
        "base_currency": base_currency,
        "exchange_rate": None,
        "exchange_rate_date": None,
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


def test_insight_snapshot_uses_twd_base_and_counts_unconverted_jpy() -> None:
    snapshot = build_spending_insight_snapshot(
        [
            _transaction(
                transaction_id=1,
                date="2026-04-01",
                merchant="JPY WITH RATE",
                amount=1000.0,
                currency="JPY",
                base_amount=200.0,
            ),
            _transaction(
                transaction_id=2,
                date="2026-04-02",
                merchant="JPY WITHOUT RATE",
                amount=500.0,
                currency="JPY",
            ),
            _transaction(
                transaction_id=3,
                date="2026-04-03",
                merchant="TWD LUNCH",
                amount=100.0,
                currency="TWD",
            ),
            _transaction(
                transaction_id=4,
                date="2026-04-04",
                merchant="信用卡繳款",
                amount=9000.0,
                category="Payment / Credit",
                currency="TWD",
            ),
        ],
        range_key="month",
        language="en",
    )

    assert snapshot["display_currency"] == "TWD"
    assert snapshot["transaction_count"] == 2
    assert snapshot["totals"]["net"] == pytest.approx(300.0)
    assert snapshot["totals"]["unconverted_transaction_count"] == 1
    assert snapshot["totals"]["unconverted_currencies"] == ["JPY"]
    assert snapshot["totals"]["by_original_currency"] == [
        {"currency": "JPY", "absolute_total": 1000.0},
        {"currency": "TWD", "absolute_total": 100.0},
    ]
    assert [item["merchant"] for item in snapshot["largest_expenses"]] == [
        "JPY WITH RATE",
        "TWD LUNCH",
    ]


def test_zero_amount_convertible_rows_still_choose_a_display_currency() -> None:
    transactions = [
        _transaction(
            transaction_id=1,
            date="2026-04-01",
            merchant="ZERO USD",
            amount=0.0,
            currency="USD",
        ),
        _transaction(
            transaction_id=2,
            date="2026-04-02",
            merchant="ZERO BASE JPY",
            amount=1000.0,
            currency="JPY",
            base_amount=0.0,
            base_currency="TWD",
        ),
    ]

    snapshot = build_spending_insight_snapshot(
        transactions,
        range_key="month",
        language="en",
    )

    assert snapshot["display_currency"] == "TWD"
    assert snapshot["transaction_count"] == 2
    assert snapshot["totals"]["net"] == pytest.approx(0.0)


def test_insight_snapshot_rejects_rows_that_cannot_convert() -> None:
    eur_only = [
        _transaction(
            transaction_id=1,
            date="2026-04-01",
            merchant="EUR CAFE",
            amount=8.0,
            currency="EUR",
        )
    ]
    with pytest.raises(ValueError, match="USD or TWD"):
        build_spending_insight_snapshot(
            eur_only,
            range_key="month",
            language="en",
        )


@pytest.mark.anyio
async def test_generate_spending_insights_does_not_call_a_live_provider() -> None:
    snapshot = build_spending_insight_snapshot(
        [
            _transaction(
                transaction_id=1,
                date="2026-04-03",
                merchant="TWD LUNCH",
                amount=100.0,
                currency="TWD",
            )
        ],
        range_key="month",
        language="en",
    )

    with pytest.raises(RuntimeError, match="must not call OpenAI or Perplexity"):
        await generate_spending_insights(
            provider="openai",
            api_key="sk-this-must-not-be-sent",
            model="gpt-4.1",
            snapshot=snapshot,
        )


def test_build_spending_insight_snapshot_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="No transactions are available"):
        build_spending_insight_snapshot(
            [],
            range_key="month",
            language="en",
        )
