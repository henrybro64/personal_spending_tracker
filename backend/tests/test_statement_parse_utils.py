from backend.statement_parse_utils import (
    MERCHANT_NOT_IN_PDF,
    parse_ctbc_roc_statement_line,
)


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
    assert (
        parse_ctbc_roc_statement_line("114/09/16 114/09/17 -62 6,232.00 5411")
        is None
    )
    assert (
        parse_ctbc_roc_statement_line("114/09/16 114/09/17 CTBC 6,232.00 5411")
        is None
    )
    assert (
        parse_ctbc_roc_statement_line("113/02/20 113/02/21 -1117 6764.00 5411")
        is None
    )
    assert (
        parse_ctbc_roc_statement_line("113/02/20 113/02/21 -74457 6764.00")
        is None
    )
    assert (
        parse_ctbc_roc_statement_line(
            "112/12/27 112/12/28 03-03 CTBC 1,843.00 5411"
        )
        is None
    )
    assert (
        parse_ctbc_roc_statement_line(
            "112/12/27 112/12/28 02-03 CTBC 1,843.00"
        )
        is None
    )


def test_parse_ctbc_roc_line_keeps_integer_amount_without_mcc() -> None:
    """Do not treat a bare 4-digit amount as MCC (Codex P1)."""
    parsed = parse_ctbc_roc_statement_line(
        "114/4/19 114/4/20 TEST SHOP 1000"
    )
    assert parsed is not None
    assert parsed.merchant == "TEST SHOP"
    assert parsed.amount == 1000.0
    assert parsed.mcc is None


def test_parse_ctbc_card_estatement_amount_only_when_merchant_missing_from_pdf() -> None:
    """CTBC 115xx PDFs often drop merchant glyphs (Uber visible, not extractable)."""
    parsed = parse_ctbc_roc_statement_line("115/02/11 115/02/23 320 0051 TW")
    assert parsed is not None
    assert parsed.merchant == MERCHANT_NOT_IN_PDF
    assert parsed.amount == 320.0

    disney = parse_ctbc_roc_statement_line(
        "115/02/14 115/02/23 DISNEY PLUS 335 0051 SG TWD 335.00"
    )
    assert disney is not None
    assert disney.merchant == "DISNEY PLUS"
    assert disney.amount == 335.0

    assert parse_ctbc_roc_statement_line("115/02/14 115/02/23 5 0051") is None


def test_parse_ctbc_roc_line_with_location_suffix_before_amount() -> None:
    parsed = parse_ctbc_roc_statement_line(
        "113/10/14 113/10/15 社團法人台灣社區照顧協會 / TW TAIPEI CITY 6,232.00 5411"
    )
    assert parsed is not None
    assert "社團法人台灣社區照顧協會" in parsed.merchant
    assert parsed.amount == 6232.0
    assert parsed.mcc == "5411"
