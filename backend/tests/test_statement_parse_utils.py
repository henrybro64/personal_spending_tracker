from backend.statement_parse_utils import (
    MERCHANT_NOT_IN_PDF,
    infer_esun_statement_roc_year,
    parse_ctbc_gregorian_statement_line,
    parse_ctbc_roc_statement_line,
    parse_esun_statement_line,
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


def test_parse_ctbc_card_estatement_6764_table_code() -> None:
    """Older CTBC card PDFs use 6764/6232 tails instead of 0051."""
    parsed = parse_ctbc_roc_statement_line(
        "111/09/09 111/12/16 5,716 6764 TW"
    )
    assert parsed is not None
    assert parsed.merchant == MERCHANT_NOT_IN_PDF
    assert parsed.amount == 5716.0

    disney = parse_ctbc_roc_statement_line(
        "111/12/14 111/12/16 Disney PLUS 270 6764 SG TWD 270.00"
    )
    assert disney is not None
    assert disney.merchant == "Disney PLUS"
    assert disney.amount == 270.0

    code_only = parse_ctbc_roc_statement_line("112/01/05 112/01/08 500 6232")
    assert code_only is not None
    assert code_only.amount == 500.0

    assert parse_ctbc_roc_statement_line("111/12/14 111/12/16 4 6764") is None

    payment = parse_ctbc_roc_statement_line(
        "111/12/27 111/12/27 6 0928XX0739 2,325 6764 TW"
    )
    assert payment is not None
    assert payment.merchant == "0928XX0739"
    assert payment.amount == 2325.0

    credit = parse_ctbc_roc_statement_line(
        "111/12/30 112/01/03 -580 6764 TW"
    )
    assert credit is not None
    assert credit.amount == -580.0


def test_parse_ctbc_gregorian_daily_statement_line() -> None:
    parsed = parse_ctbc_gregorian_statement_line(
        "2021/08/17 2021/08/18 長庚醫療財團法人－台北長庚紀念醫院 / TW Taipei 720"
    )
    assert parsed is not None
    assert parsed.transaction_date == "2021-08-17"
    assert parsed.posted_date == "2021-08-18"
    assert "長庚醫療" in parsed.merchant
    assert parsed.amount == 720.0

    assert (
        parse_ctbc_gregorian_statement_line(
            "2021/09/13 2021/10/01 200,000 20,000/20,000 7.99%至2021/11"
        )
        is None
    )


def test_parse_esun_statement_line() -> None:
    roc_year = 115
    parsed = parse_esun_statement_line(
        "04/14 04/15 全聯實業股份有限公司－台北信義分公司 TWD 155",
        roc_year,
    )
    assert parsed is not None
    assert parsed.transaction_date == "2026-04-14"
    assert parsed.posted_date == "2026-04-15"
    assert "全聯實業" in parsed.merchant
    assert parsed.amount == 155.0

    payment = parse_esun_statement_line(
        "04/28 感謝您辦理本行自動轉帳繳款！ TWD -9,961",
        roc_year,
    )
    assert payment is not None
    assert payment.transaction_date == "2026-04-28"
    assert payment.amount == -9961.0

    assert (
        parse_esun_statement_line("04/14 04/15 500 TWD 155", roc_year) is None
    )


def test_infer_esun_statement_roc_year() -> None:
    assert infer_esun_statement_roc_year("115年04月 帳單", "") == 115
    assert (
        infer_esun_statement_roc_year("", "ESUN_Estatement_11504.pdf") == 115
    )


def test_parse_ctbc_roc_line_with_location_suffix_before_amount() -> None:
    parsed = parse_ctbc_roc_statement_line(
        "113/10/14 113/10/15 社團法人台灣社區照顧協會 / TW TAIPEI CITY 6,232.00 5411"
    )
    assert parsed is not None
    assert "社團法人台灣社區照顧協會" in parsed.merchant
    assert parsed.amount == 6232.0
    assert parsed.mcc == "5411"
