from backend.statement_parse_utils import parse_ctbc_roc_statement_line


def test_parse_ctbc_roc_line_accepts_any_mcc_and_foreign_currency() -> None:
    parsed = parse_ctbc_roc_statement_line(
        "114/4/19 114/4/20 UBER TRIP USD 11.25 365.00 4121"
    )
    assert parsed is not None
    assert parsed.merchant == "UBER TRIP"
    assert parsed.amount == 365.0
    assert parsed.mcc == "4121"

    local_uber = parse_ctbc_roc_statement_line(
        "114/4/19 114/4/20 UBER TRIP 350.00 4121"
    )
    assert local_uber is not None
    assert local_uber.amount == 350.0


def test_parse_ctbc_roc_line_rejects_digit_only_merchant() -> None:
    """Reject lines whose merchant span is only digits (PDF table noise)."""
    assert (
        parse_ctbc_roc_statement_line(
            "113/10/14 113/10/15 500 6,232.00 5411"
        )
        is None
    )
    assert (
        parse_ctbc_roc_statement_line("113/10/14 113/10/15 5 51.00 5411")
        is None
    )


def test_parse_ctbc_roc_line_with_location_suffix_before_amount() -> None:
    parsed = parse_ctbc_roc_statement_line(
        "113/10/14 113/10/15 社團法人台灣社區照顧協會 / TW TAIPEI CITY 6,232.00 5411"
    )
    assert parsed is not None
    assert "社團法人台灣社區照顧協會" in parsed.merchant
    assert parsed.amount == 6232.0
    assert parsed.mcc == "5411"
