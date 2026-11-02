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


SPLIT_AMOUNT_HINT = (
    'the amount looks like it was split at a comma. Quote it ("1,234") or remove '
    "the thousands comma (1234); use a dot for decimals (12.50)."
)


def test_quoted_amount_with_thousands_separator() -> None:
    rows = parse_import_content('2026-05-13,IKEA,"1,234"', format="text")
    assert [(r.merchant_raw, r.amount) for r in rows] == [("IKEA", 1234.0)]


def test_quoted_merchant_and_amount_with_category() -> None:
    rows = parse_import_content(
        '2026-05-13,"IKEA, Taipei",Shopping,"1,234.50"', format="text"
    )
    assert [(r.merchant_raw, r.category, r.amount) for r in rows] == [
        ("IKEA, Taipei", "Shopping", 1234.5)
    ]


@pytest.mark.parametrize(
    "content",
    [
        "2026-05-13,IKEA,1,234",
        "2026-05-13,IKEA,Shopping,1,234",
        "2026-05-13,IKEA,1,234.00",
        "2026-05-13,IKEA,-1,234",
        "2026-05-13,IKEA,$1,234",
        "2026-05-13,IKEA,1,234,567",
        # A decimal comma or other split must not become category + amount.
        "2026-05-13,Cafe,12,50",
        "2026-05-13,Cafe,1,5",
        "2026-05-13,Cafe,1,2345",
        "2026-05-13,Cafe,1.234,56",
    ],
)
def test_unquoted_thousands_separator_is_rejected(content: str) -> None:
    with pytest.raises(BulkImportParseError, match="(?i)quote"):
        parse_import_content(content, format="text")


def test_thousands_error_names_the_pasted_line_and_the_fix() -> None:
    content = "# my card\n\n2026-05-12,Cafe,80\n2026-05-13,IKEA,1,234"
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content(content, format="text")
    assert str(raised.value) == f"Line 4: {SPLIT_AMOUNT_HINT}"


def test_tab_and_space_separated_thousands_still_work() -> None:
    assert [
        r.amount for r in parse_import_content("2026-05-13\tIKEA\t1,234", format="text")
    ] == [1234.0]
    assert [
        r.amount for r in parse_import_content("2026-05-13 IKEA 1,234", format="text")
    ] == [1234.0]


def test_plain_category_and_amount_still_work() -> None:
    rows = parse_import_content("2026-05-13,IKEA,Shopping,234", format="text")
    assert [(r.category, r.amount) for r in rows] == [("Shopping", 234.0)]
    rows = parse_import_content("2026-05-13, IKEA , Food , 1234", format="text")
    assert [(r.merchant_raw, r.category, r.amount) for r in rows] == [
        ("IKEA", "Food", 1234.0)
    ]


@pytest.mark.parametrize(
    "header",
    ["date,merchant,category,amount", '"date","merchant","amount"', "Date, Merchant, Amount"],
)
def test_comma_header_line_is_still_skipped(header: str) -> None:
    rows = parse_import_content(f"{header}\n2026-05-13,IKEA,234", format="text")
    assert [(r.merchant_raw, r.amount) for r in rows] == [("IKEA", 234.0)]


def test_escaped_quotes_inside_a_quoted_field() -> None:
    rows = parse_import_content('2026-05-13,"IKEA ""Store""",10', format="text")
    assert [r.merchant_raw for r in rows] == ['IKEA "Store"']


@pytest.mark.parametrize(
    "line",
    ['2026-05-13,"IKEA,10', '2026-05-13,"IK"EA",10', '2026-05-13,IKEA,"1,234"x'],
)
def test_a_misplaced_quote_is_an_error_not_a_rewritten_field(line: str) -> None:
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content(f"2026-05-12,Cafe,80\n{line}", format="text")
    assert str(raised.value).startswith("Line 2 has a misplaced or unmatched quote.")



@pytest.mark.parametrize(
    ("content", "content_format"),
    [
        ('2026-05-13,Cafe,"12,50"', "text"),
        ("2026-05-13\tCafe\t12,50", "text"),
        ("2026-05-13 Cafe 12,50", "text"),
        ('[{"date": "2026-05-13", "merchant": "Cafe", "amount": "12,50"}]', "json"),
    ],
)
def test_a_decimal_comma_is_rejected_not_multiplied_by_100(
    content: str, content_format: str
) -> None:
    with pytest.raises(BulkImportParseError) as raised:
        parse_import_content(content, format=content_format)
    assert str(raised.value) == (
        "Row 1: amount '12,50' has a comma that is not a thousands separator. "
        "Use a dot for decimals (12.50)."
    )


@pytest.mark.parametrize("amount", ["1,23,456", "12,3456", "1,234,56", ",123"])
def test_a_comma_that_is_not_thousands_grouping_is_rejected(amount: str) -> None:
    with pytest.raises(BulkImportParseError, match="not a thousands separator"):
        parse_import_content(f'2026-05-13,Cafe,"{amount}"', format="text")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("1,234", 1234.0), ("-1,234.50", -1234.5), ("1,234,567.89", 1234567.89)],
)
def test_valid_thousands_grouping_still_parses_in_every_format(
    amount: str, expected: float
) -> None:
    for content, content_format in (
        (f'2026-05-13,Cafe,"{amount}"', "text"),
        (f"2026-05-13\tCafe\t{amount}", "text"),
        (f"2026-05-13 Cafe {amount}", "text"),
        (
            f'[{{"date": "2026-05-13", "merchant": "Cafe", "amount": "{amount}"}}]',
            "json",
        ),
    ):
        rows = parse_import_content(content, format=content_format)
        assert [r.amount for r in rows] == [expected], content


@pytest.mark.parametrize(
    ("category", "expected"),
    [("Groceries", "Groceries"), ("Food 2", "Food 2"), ("7-Eleven", "7-Eleven")],
)
def test_categories_containing_digits_are_not_treated_as_split_amounts(
    category: str, expected: str
) -> None:
    rows = parse_import_content(f"2026-05-13,Cafe,{category},85", format="text")
    assert [(r.category, r.amount) for r in rows] == [(expected, 85.0)]
