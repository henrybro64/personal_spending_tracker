from pathlib import Path

from backend.parser import categorize_merchant, parse_transactions


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _transaction_by_merchant(
    transactions: list[dict[str, object]], merchant: str
) -> dict[str, object]:
    for transaction in transactions:
        if transaction["merchant"] == merchant:
            return transaction
    raise AssertionError(f"Transaction not found for merchant: {merchant}")


def test_parse_chase_fixture_covers_us_and_iso_lines() -> None:
    transactions = parse_transactions(_read_fixture("chase_sample.txt"))

    assert len(transactions) == 4

    starbucks = _transaction_by_merchant(transactions, "STARBUCKS STORE 1234")
    assert starbucks == {
        "date": "2024-04-19",
        "merchant": "STARBUCKS STORE 1234",
        "amount": 5.75,
        "category": "Dining",
        "currency": "USD",
    }

    payment = _transaction_by_merchant(transactions, "PAYMENT RECEIVED")
    assert payment["date"] == "2024-04-21"
    assert payment["amount"] == -100.0
    assert payment["currency"] == "USD"

    openai = _transaction_by_merchant(transactions, "OPENAI API")
    assert openai["date"] == "2024-04-22"
    assert openai["amount"] == 20.0
    assert openai["category"] == "Digital Services"
    assert openai["currency"] == "USD"


def test_parse_ctbc_fixture_converts_roc_dates_and_twd_currency() -> None:
    transactions = parse_transactions(_read_fixture("ctbc_sample.txt"))

    assert len(transactions) == 4

    foodpanda = _transaction_by_merchant(transactions, "FOODPANDA")
    assert foodpanda == {
        "date": "2025-04-19",
        "merchant": "FOODPANDA",
        "amount": 250.0,
        "category": "Dining",
        "currency": "TWD",
    }

    uber_twd = _transaction_by_merchant(transactions, "UBER TRIP")
    assert uber_twd["date"] == "2025-04-19"
    assert uber_twd["amount"] == 350.0
    assert uber_twd["category"] == "Transport"
    assert uber_twd["currency"] == "TWD"

    uber_usd = next(
        transaction
        for transaction in transactions
        if transaction["merchant"] == "UBER TRIP"
        and transaction["amount"] == 365.0
    )
    assert uber_usd["date"] == "2025-04-20"
    assert uber_usd["category"] == "Transport"

    cpc = _transaction_by_merchant(transactions, "台灣中油")
    assert cpc["date"] == "2025-04-21"
    assert cpc["amount"] == 900.0
    assert cpc["category"] == "Transport"
    assert cpc["currency"] == "TWD"


def test_parse_dbs_fixture_parses_taiwan_statement_lines_and_refunds() -> None:
    transactions = parse_transactions(_read_fixture("dbs_sample.txt"))

    assert len(transactions) == 3

    px_mart = _transaction_by_merchant(transactions, "全聯福利中心")
    assert px_mart == {
        "date": "2025-04-19",
        "merchant": "全聯福利中心",
        "amount": 1234.0,
        "category": "Groceries",
        "currency": "TWD",
    }

    netflix = _transaction_by_merchant(transactions, "NETFLIX.COM")
    assert netflix["category"] == "Entertainment"
    assert netflix["currency"] == "TWD"

    refund = _transaction_by_merchant(transactions, "MYSTERY REFUND")
    assert refund["amount"] == -500.0
    assert refund["category"] == "Refunds & Credits"


def test_categorize_merchant_handles_known_merchants() -> None:
    assert categorize_merchant("STARBUCKS RESERVE", 7.5) == "Dining"
    assert categorize_merchant("OPENAI API", 20.0) == "Digital Services"
    assert categorize_merchant("Cursor Pro Team", 20.0) == "Digital Services"
    assert categorize_merchant("Perplexity AI", 20.0) == "Digital Services"
    assert categorize_merchant("台灣中油", 900.0) == "Transport"
    assert categorize_merchant("優步 Uber *Trip", 220.0) == "Transport"
    assert categorize_merchant("５５６８８ＡＰＰ", 180.0) == "Transport"
    assert categorize_merchant("台灣大車隊", 220.0) == "Transport"
    assert categorize_merchant("新光三越 台北站前", 500.0) == "Shopping"
    assert categorize_merchant("微風廣場", 300.0) == "Shopping"
    assert categorize_merchant("優衣庫 南西店", 990.0) == "Shopping"
    assert categorize_merchant("TRIP.COM", 4500.0) == "Travel"
    assert categorize_merchant("Agoda", 2000.0) == "Travel"
    assert categorize_merchant("HOTELS.COM", 1500.0) == "Travel"
    assert categorize_merchant("UBER TRIP", 100.0) == "Transport"
    assert categorize_merchant("杏一醫療", 200.0) == "Health & Wellness"
    assert categorize_merchant("兄弟大飯店", 3000.0) == "Travel"
    assert categorize_merchant("EXPRESSVPN", 15.0) == "Subscriptions"
    assert categorize_merchant("寶雅生活館", 300.0) == "Shopping"
    assert categorize_merchant("麥味登", 80.0) == "Dining"
    assert categorize_merchant("義美股份", 120.0) == "Groceries"
    assert categorize_merchant("富邦ｍｏｍｏ", 500.0) == "Shopping"
    assert categorize_merchant("ＣＯＵＰＡＮＧ 酷澎", 500.0) == "Shopping"
    assert categorize_merchant("陳耀訓 麵包埠", 120.0) == "Dining"
