from hashlib import sha256

from backend.batch_import.merge import _batch_statement_fingerprint
from backend.transaction_source import (
    card_last4_from_statement,
    source_keys_for_transactions,
    with_statement_card_last4,
)


def _row(
    merchant: str,
    amount: float = 5.75,
    *,
    last4: str = "1001",
    currency: str = "USD",
) -> dict:
    return {
        "date": "2026-01-05",
        "merchant": merchant,
        "amount": amount,
        "category": "Dining / Food",
        "currency": currency,
        "card_last4": last4,
    }


def test_identical_rows_in_one_statement_stay_distinct() -> None:
    keys = source_keys_for_transactions(
        [_row("STARBUCKS"), _row("starbucks"), _row("IKEA", 80)]
    )
    assert len(set(keys[:2])) == 2
    assert len(set(keys)) == 3


def test_the_same_purchase_from_another_statement_reuses_the_key() -> None:
    first = source_keys_for_transactions([_row("STARBUCKS")])
    second = source_keys_for_transactions([_row(" STARBUCKS ")])
    assert first == second


def test_a_different_card_is_a_different_purchase() -> None:
    first = source_keys_for_transactions([_row("STARBUCKS", last4="1001")])
    second = source_keys_for_transactions([_row("STARBUCKS", last4="2002")])
    assert first != second


def test_empty_currency_matches_default_twd_persistence() -> None:
    with_default = source_keys_for_transactions(
        [_row("STARBUCKS", currency="TWD")]
    )
    missing_currency = {
        "date": "2026-01-05",
        "merchant": "STARBUCKS",
        "amount": 5.75,
        "category": "Dining / Food",
        "card_last4": "1001",
    }
    assert source_keys_for_transactions([missing_currency]) == with_default
    assert source_keys_for_transactions([_row("STARBUCKS", currency="twd")]) == (
        with_default
    )
    assert source_keys_for_transactions([_row("STARBUCKS", currency="")]) == (
        with_default
    )


def test_card_last4_patterns_are_shared_across_banks() -> None:
    assert card_last4_from_statement("Card ending 1001") == "1001"
    assert card_last4_from_statement("Account ending in 2222") == "2222"
    assert (
        card_last4_from_statement("body", file_name="statements-3456-jan.pdf")
        == "3456"
    )
    assert card_last4_from_statement("末四碼 7788") == "7788"
    assert card_last4_from_statement("1234-XXXX-XXXX-5678") == "1234"


def test_upload_stamping_uses_shared_last4_helper() -> None:
    stamped = with_statement_card_last4(
        [
            {
                "date": "2026-01-05",
                "merchant": "STARBUCKS",
                "amount": 5.75,
                "category": "Dining / Food",
                "currency": "USD",
            }
        ],
        "Purchase details\n",
        file_name="statements-9999-feb.pdf",
    )
    assert stamped[0]["card_last4"] == "9999"


def test_batch_statement_fingerprint_uses_content_not_basename() -> None:
    shared_name = "2026-01-29.txt"
    first = [
        {
            "id": 1,
            "import_run_id": 1,
            "statement_file": shared_name,
            "bank": "AMEX",
            "card_last4": "1001",
            "transaction_date": "2026-01-05",
            "posted_date": None,
            "merchant_raw": "STARBUCKS",
            "amount": 5.75,
            "currency": "USD",
            "transaction_type": "purchase",
            "category": "Dining / Food",
            "exclude_from_spending": False,
            "needs_review": False,
            "statement_content_sha256": sha256(b"amex-a").hexdigest(),
        }
    ]
    second = [
        {
            **first[0],
            "id": 2,
            "bank": "Chase",
            "merchant_raw": "TARGET",
            "statement_content_sha256": sha256(b"chase-b").hexdigest(),
        }
    ]
    assert _batch_statement_fingerprint(first) != _batch_statement_fingerprint(second)
    assert _batch_statement_fingerprint(first) == sha256(b"amex-a").hexdigest()


def test_legacy_fingerprint_falls_back_to_transaction_payload() -> None:
    rows = [
        {
            "id": 1,
            "import_run_id": 1,
            "statement_file": "2026-01-29.txt",
            "bank": "AMEX",
            "card_last4": "1001",
            "transaction_date": "2026-01-05",
            "posted_date": None,
            "merchant_raw": "STARBUCKS",
            "amount": 5.75,
            "currency": "USD",
            "transaction_type": "purchase",
            "category": "Dining / Food",
            "exclude_from_spending": False,
            "needs_review": False,
            "statement_content_sha256": "",
        }
    ]
    other = [{**rows[0], "merchant_raw": "IKEA", "amount": 80.0}]
    assert _batch_statement_fingerprint(rows) != _batch_statement_fingerprint(other)
