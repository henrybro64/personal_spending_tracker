import json
import unittest

import pytest

from backend.bulk_import import BulkImportParseError, parse_import_content


class BulkImportTests(unittest.TestCase):
    def test_json_wrapper_object(self) -> None:
        payload = {
            "source": "CTBC May",
            "bank": "CTBC",
            "currency": "TWD",
            "transactions": [
                {"date": "2026-05-13", "merchant": "IKEA", "amount": 813},
            ],
        }
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].bank, "CTBC")
        self.assertEqual(rows[0].currency, "TWD")
        self.assertEqual(rows[0].merchant_raw, "IKEA")
        self.assertEqual(rows[0].statement_file, "CTBC May.json")

    def test_json_array_shorthand(self) -> None:
        payload = [{"date": "2026-05-04", "merchant": "7-ELEVEN", "amount": 55}]
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(rows[0].transaction_date, "2026-05-04")
        self.assertEqual(rows[0].amount, 55.0)

    def test_text_tab_separated(self) -> None:
        content = "# comments ok\n2026-05-04\t全聯\t736.00\n"
        rows = parse_import_content(content, format="text", bank="CTBC", currency="TWD")
        self.assertEqual(rows[0].merchant_raw, "全聯")
        self.assertEqual(rows[0].amount, 736.0)

    def test_text_space_separated(self) -> None:
        content = "2026-05-13 IKEA 813.00\n"
        rows = parse_import_content(content, format="text")
        self.assertEqual(rows[0].merchant_raw, "IKEA")

    def test_text_space_separated_multi_word_merchant(self) -> None:
        content = "2026-05-13 IKEA Taipei 813.00\n"
        rows = parse_import_content(content, format="text")
        self.assertEqual(rows[0].merchant_raw, "IKEA Taipei")
        self.assertEqual(rows[0].amount, 813.0)

    def test_text_tab_separated_with_category(self) -> None:
        content = "2026-05-13\tIKEA\tShopping / Online Retail\t813.00\n"
        rows = parse_import_content(content, format="text")
        self.assertEqual(rows[0].merchant_raw, "IKEA")
        self.assertEqual(rows[0].category, "Shopping / Online Retail")

    def test_rejects_invalid_date(self) -> None:
        payload = [{"date": "05/13/2026", "merchant": "IKEA", "amount": 10}]
        with self.assertRaises(BulkImportParseError):
            parse_import_content(json.dumps(payload))

    def test_explicit_category_skips_needs_review(self) -> None:
        payload = [
            {
                "date": "2026-05-13",
                "merchant": "IKEA",
                "amount": 813,
                "category": "Shopping / Online Retail",
            }
        ]
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(rows[0].category, "Shopping / Online Retail")
        self.assertFalse(rows[0].needs_review)


    def test_preserves_negative_refund_amount(self) -> None:
        payload = [
            {
                "date": "2026-05-13",
                "merchant": "MERCHANT REFUND",
                "amount": -577,
                "category": "Other Spending",
            }
        ]
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(rows[0].amount, -577.0)

    def test_card_counts_match_transactions(self) -> None:
        payload = {
            "source": "ctbc-import",
            "bank": "CTBC",
            "currency": "TWD",
            "card_counts": {"0051": 1, "6232": 1, "6764": 0, "3037": 0},
            "transactions": [
                {
                    "date": "2026-05-13",
                    "posted_date": "2026-05-15",
                    "merchant": "Spotify",
                    "amount": 198,
                    "card_last4": "0051",
                },
                {
                    "date": "2026-05-14",
                    "posted_date": "2026-05-16",
                    "merchant": "國外交易手續費",
                    "amount": 3,
                    "card_last4": "6232",
                },
            ],
        }
        rows = parse_import_content(json.dumps(payload))
        self.assertEqual(len(rows), 2)

    def test_card_count_mismatch_is_rejected(self) -> None:
        payload = {
            "source": "ctbc-import",
            "bank": "CTBC",
            "currency": "TWD",
            "card_counts": {"0051": 2, "6232": 0, "6764": 0, "3037": 0},
            "transactions": [
                {
                    "date": "2026-05-13",
                    "merchant": "Spotify",
                    "amount": 198,
                    "card_last4": "0051",
                }
            ],
        }
        with self.assertRaisesRegex(BulkImportParseError, "ERROR_CARD_COUNT_MISMATCH"):
            parse_import_content(json.dumps(payload))


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize(
    "content",
    [
        "2026-05-13,Cafe,NaN",
        "2026-05-13,Cafe,inf",
        "2026-05-13\tCafe\t-Infinity",
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": NaN}]',
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": Infinity}]',
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 1e400}]',
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": "nan"}]',
        "2026-05-13,Cafe,4111111111111111",
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 1000000000.01}]',
        # An integer this long does not fit a float at all.
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 1' + "0" * 400 + "}]",
    ],
)
def test_bulk_import_rejects_non_finite_or_huge_amounts(content: str) -> None:
    with pytest.raises(BulkImportParseError):
        parse_import_content(content)


def test_bulk_import_names_the_row_and_value_of_a_non_finite_amount() -> None:
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content("2026-05-13,Cafe,10\n2026-05-14,Cafe,NaN")
    assert str(raised.value) == "Row 2: amount must be a finite number, got 'NaN'."

    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content("2026-05-13,Cafe,4111111111111111")
    assert str(raised.value) == "Row 1: Amount is too large."


@pytest.mark.parametrize(
    ("literal", "quoted"),
    [("NaN", "'NaN'"), ("Infinity", "'Infinity'"), ("-Infinity", "'-Infinity'"), ("1e400", "'1e400'")],
)
def test_bulk_import_json_error_quotes_the_amount_as_typed(literal: str, quoted: str) -> None:
    content = (
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 12.5},'
        f' {{"date": "2026-05-14", "merchant": "Cafe", "amount": {literal}}}]'
    )
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content(content)
    assert str(raised.value) == f"Row 2: amount must be a finite number, got {quoted}."


@pytest.mark.parametrize("amount", ["0.001", "-0.004", "0.0049"])
def test_bulk_import_rejects_amounts_that_round_to_zero_cents(amount: str) -> None:
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content(f"2026-05-13,Cafe,{amount}")
    assert str(raised.value) == "Row 1: amount must be non-zero."


def test_bulk_import_keeps_half_a_cent_and_normal_json_floats() -> None:
    rows = parse_import_content(
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 0.005},'
        ' {"date": "2026-05-14", "merchant": "Cafe", "amount": 12.5}]'
    )
    assert [r.amount for r in rows] == [0.005, 12.5]


def test_bulk_import_still_accepts_normal_amounts() -> None:
    rows = parse_import_content("2026-05-13,Cafe,-12.50")
    assert [r.amount for r in rows] == [-12.5]
    rows = parse_import_content(
        '[{"date": "2026-05-13", "merchant": "Cafe", "amount": 1000000000}]'
    )
    assert [r.amount for r in rows] == [1_000_000_000.0]
