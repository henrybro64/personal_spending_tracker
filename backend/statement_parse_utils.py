"""Shared statement text normalization and parsing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from backend.merchant_noise import merchant_raw_is_parse_noise

US_STATEMENT_PERIOD_PATTERN = re.compile(
    r"Opening/Closing Date\s+"
    r"(?P<start>\d{2}/\d{2}/\d{2,4})"
    r"\s*-\s*"
    r"(?P<end>\d{2}/\d{2}/\d{2,4})",
    re.IGNORECASE,
)

US_STATEMENT_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"Statement Date:\s*(?P<date>\d{2}/\d{2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"Closing Date\s*(?P<date>\d{2}/\d{2}/\d{2,4})",
        re.IGNORECASE,
    ),
)


def normalize_statement_text(text: str) -> str:
    return (
        text.replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u2009", " ")
        .replace("\u202f", " ")
    )


def parse_us_date(date_text: str) -> date:
    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_text, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported US date format: {date_text}")


def parse_amount(amount_text: str) -> float:
    normalized = amount_text.replace("$", "").replace(",", "").strip()
    is_negative = normalized.startswith("(") and normalized.endswith(")")

    if is_negative:
        normalized = normalized[1:-1]

    amount = float(normalized)
    return -amount if is_negative else amount


def roc_date_string_to_iso(roc_date: str) -> str | None:
    """Convert Taiwan ROC calendar Y/M/D to ISO Gregorian date string."""
    parts = roc_date.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        roc_year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
    try:
        return date(roc_year + 1911, month, day).isoformat()
    except ValueError:
        return None


def roc_date_to_iso(date_text: str) -> str:
    """Strict ROC conversion used by batch parsers."""
    iso_date = roc_date_string_to_iso(date_text)
    if iso_date is None:
        raise ValueError(f"Unsupported ROC date format: {date_text}")
    return iso_date


def extract_us_statement_period(text: str) -> tuple[date, date] | None:
    match = US_STATEMENT_PERIOD_PATTERN.search(text)
    if not match:
        return None
    return (
        parse_us_date(match.group("start")),
        parse_us_date(match.group("end")),
    )


def extract_us_statement_date(text: str) -> date | None:
    for pattern in US_STATEMENT_DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return parse_us_date(match.group("date"))
    return None


def infer_us_transaction_date(
    transaction_date_text: str,
    statement_period: tuple[date, date] | None,
    statement_date: date | None,
    *,
    strict: bool = False,
) -> str:
    stripped = transaction_date_text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        try:
            datetime.strptime(stripped, "%Y-%m-%d").date()
            return stripped
        except ValueError:
            pass

    if transaction_date_text.count("/") == 2:
        return parse_us_date(transaction_date_text).isoformat()

    month_text, day_text = transaction_date_text.split("/")
    month = int(month_text)
    day = int(day_text)

    if statement_period:
        period_start, period_end = statement_period
        for candidate_year in {period_start.year, period_end.year}:
            try:
                candidate_date = date(candidate_year, month, day)
            except ValueError:
                continue
            if period_start <= candidate_date <= period_end:
                return candidate_date.isoformat()

        fallback_year = (
            period_start.year
            if period_start.year != period_end.year and month >= period_start.month
            else period_end.year
        )
        return date(fallback_year, month, day).isoformat()

    if statement_date:
        inferred_year = (
            statement_date.year - 1
            if month > statement_date.month
            else statement_date.year
        )
        return date(inferred_year, month, day).isoformat()

    if strict:
        raise ValueError(f"Unable to infer year for US date: {transaction_date_text}")

    return transaction_date_text


CTBC_ROC_DATE_LINE = re.compile(
    r"^\s*"
    r"(?P<transaction_date>\d{2,3}/\d{1,2}/\d{1,2})\s+"
    r"(?P<posted_date>\d{2,3}/\d{1,2}/\d{1,2})\s+"
    r"(?P<rest>.+)$"
)

CTBC_GREGORIAN_DATE_LINE = re.compile(
    r"^\s*"
    r"(?P<transaction_date>\d{4}/\d{1,2}/\d{1,2})\s+"
    r"(?P<posted_date>\d{4}/\d{1,2}/\d{1,2})\s+"
    r"(?P<rest>.+)$"
)

CTBC_USD_AMOUNT_TRAILING = re.compile(
    r"USD\s+(?P<foreign_amount>[-\d,.]+)\s+"
    r"(?P<amount>-?[\d,]+(?:\.\d{1,2})?)"
    r"(?:\s+(?P<mcc>\d{4}))?"
    r"\s*$",
    re.IGNORECASE,
)
CTBC_AMOUNT_MCC_TRAILING = re.compile(
    r"\s+(?P<amount>-?[\d,]+(?:\.\d{1,2})?)"
    r"(?:\s+(?P<mcc>\d{4}))?"
    r"\s*$"
)

# CTBC card e-statement: trailing 4-digit table code (0051, 6764, 6232, …).
_CTBC_CARD_CODE = r"\d{4}"

CTBC_CARD_TWD_TRAILING = re.compile(
    r"^(?P<merchant>.+?)\s+(?P<card_amount>[\d,]+)\s+"
    rf"(?P<table_code>{_CTBC_CARD_CODE})\s+"
    r"(?P<country>[A-Z]{2})\s+TWD\s+(?P<amount>[\d,]+(?:\.\d{1,2})?)\s*$",
    re.IGNORECASE,
)
CTBC_CARD_FX_TRAILING = re.compile(
    r"^(?P<merchant>.+?)\s+(?P<card_amount>[\d,]+)\s+"
    rf"(?P<table_code>{_CTBC_CARD_CODE})\s+"
    r"(?P<country>[A-Z]{2})\b.*?\bUSD\s+(?P<amount>[\d.]+)\s*$",
    re.IGNORECASE,
)
CTBC_CARD_LEDGER_TRAILING = re.compile(
    r"^(?:\d+\s+)?(?P<merchant>[0-9A-Z][0-9A-Z*]+)\s+(?P<amount>-?[\d,]+)\s+"
    rf"(?P<table_code>{_CTBC_CARD_CODE})\s+"
    r"(?P<country>[A-Z]{2})\s*$"
)
CTBC_CARD_PLAIN_TRAILING = re.compile(
    r"^(?:(?P<merchant>[A-Za-z][A-Za-z0-9*.,\s/&-]+?)\s+)?"
    r"(?P<amount>-?[\d,]+)\s+"
    rf"(?P<table_code>{_CTBC_CARD_CODE})\s+"
    r"(?P<country>[A-Z]{2})\s*$"
)
CTBC_CARD_AMOUNT_ONLY_TRAILING = re.compile(
    rf"^(?P<amount>-?[\d,]+)\s+(?P<table_code>{_CTBC_CARD_CODE})\s*$"
)

_MIN_CARD_AMOUNT_ONLY = 10

MERCHANT_NOT_IN_PDF = "(merchant not in PDF)"
MERCHANT_MISSING_FROM_PDF_CATEGORY = "Merchant Missing From PDF"


def _parse_ctbc_line_rest(rest: str) -> tuple[str, float, str | None] | None:
    """Parse merchant + TWD amount (+ optional MCC) from the CTBC line tail."""
    normalized_rest = rest.strip()
    if not normalized_rest:
        return None
    if "20,000/20,000" in normalized_rest or "7.99%" in normalized_rest:
        return None

    twd_card = CTBC_CARD_TWD_TRAILING.match(normalized_rest)
    if twd_card:
        return (
            twd_card.group("merchant").strip(),
            parse_amount(twd_card.group("amount")),
            None,
        )

    fx_card = CTBC_CARD_FX_TRAILING.match(normalized_rest)
    if fx_card:
        return (
            fx_card.group("merchant").strip(),
            parse_amount(fx_card.group("amount")),
            None,
        )

    ledger_card = CTBC_CARD_LEDGER_TRAILING.match(normalized_rest)
    if ledger_card:
        return (
            ledger_card.group("merchant").strip(),
            parse_amount(ledger_card.group("amount")),
            None,
        )

    plain_card = CTBC_CARD_PLAIN_TRAILING.match(normalized_rest)
    if plain_card:
        merchant = (plain_card.group("merchant") or "").strip()
        if not merchant:
            merchant = MERCHANT_NOT_IN_PDF
        return (
            merchant,
            parse_amount(plain_card.group("amount")),
            None,
        )

    amount_only_card = CTBC_CARD_AMOUNT_ONLY_TRAILING.match(normalized_rest)
    if amount_only_card:
        amount = parse_amount(amount_only_card.group("amount"))
        if amount < _MIN_CARD_AMOUNT_ONLY:
            return None
        return (MERCHANT_NOT_IN_PDF, amount, None)

    usd_match = CTBC_USD_AMOUNT_TRAILING.search(normalized_rest)
    if usd_match:
        merchant = normalized_rest[: usd_match.start()].strip()
        if not merchant:
            return None
        return (
            merchant,
            parse_amount(usd_match.group("amount")),
            usd_match.group("mcc"),
        )

    amount_match = CTBC_AMOUNT_MCC_TRAILING.search(normalized_rest)
    if not amount_match:
        return None

    merchant = normalized_rest[: amount_match.start()].strip()
    if not merchant:
        merchant = MERCHANT_NOT_IN_PDF
    return (
        merchant,
        parse_amount(amount_match.group("amount")),
        amount_match.group("mcc"),
    )


@dataclass(frozen=True, slots=True)
class CtbcRocLineParts:
    transaction_date: str
    posted_date: str
    merchant: str
    amount: float
    mcc: str | None


def parse_ctbc_roc_statement_line(line: str) -> CtbcRocLineParts | None:
    """Parse CTBC 消費日/入帳日 lines, including foreign-currency Uber-style rows."""
    normalized_line = " ".join(line.split())
    if not normalized_line:
        return None

    header_match = CTBC_ROC_DATE_LINE.match(normalized_line)
    if not header_match:
        return None

    rest = header_match.group("rest").strip()
    parsed_rest = _parse_ctbc_line_rest(rest)
    if parsed_rest is None:
        return None

    merchant, amount, mcc = parsed_rest
    merchant = " ".join(merchant.split())
    if not merchant:
        merchant = "(no description)"

    if merchant != MERCHANT_NOT_IN_PDF and merchant_raw_is_parse_noise(merchant):
        return None

    return CtbcRocLineParts(
        transaction_date=header_match.group("transaction_date"),
        posted_date=header_match.group("posted_date"),
        merchant=merchant,
        amount=amount,
        mcc=mcc,
    )


def _gregorian_date_to_iso(date_text: str) -> str | None:
    parts = date_text.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_ctbc_gregorian_statement_line(line: str) -> CtbcRocLineParts | None:
    """Parse CBGCC-DAILYSTMT lines: YYYY/MM/DD YYYY/MM/DD merchant ... amount.

    *transaction_date* and *posted_date* in the result are ISO ``YYYY-MM-DD`` strings.
    """
    normalized_line = " ".join(line.split())
    if not normalized_line:
        return None

    header_match = CTBC_GREGORIAN_DATE_LINE.match(normalized_line)
    if not header_match:
        return None

    rest = header_match.group("rest").strip()
    if "20,000/20,000" in rest or "7.99%" in rest:
        return None

    amount_match = re.search(r"\s+(?P<amount>-?[\d,]+(?:\.\d{1,2})?)\s*$", rest)
    if not amount_match:
        return None

    merchant = rest[: amount_match.start()].strip()
    if not merchant:
        return None

    if merchant_raw_is_parse_noise(merchant):
        return None

    transaction_iso = _gregorian_date_to_iso(header_match.group("transaction_date"))
    posted_iso = _gregorian_date_to_iso(header_match.group("posted_date"))
    if transaction_iso is None or posted_iso is None:
        return None

    return CtbcRocLineParts(
        transaction_date=transaction_iso,
        posted_date=posted_iso,
        merchant=" ".join(merchant.split()),
        amount=parse_amount(amount_match.group("amount")),
        mcc=None,
    )


ESUN_BILLING_PERIOD = re.compile(r"(?P<roc_year>\d{2,3})年(?P<month>\d{1,2})月")
ESUN_LINE_TWO_DATES = re.compile(
    r"^(?P<spent>\d{1,2}/\d{1,2})\s+(?P<posted>\d{1,2}/\d{1,2})\s+"
    r"(?P<merchant>.+?)\s+TWD\s+(?P<amount>-?[\d,]+)\s*$"
)
ESUN_LINE_ONE_DATE = re.compile(
    r"^(?P<spent>\d{1,2}/\d{1,2})\s+"
    r"(?P<merchant>.+?)\s+TWD\s+(?P<amount>-?[\d,]+)\s*$"
)


def infer_esun_statement_roc_year(statement_text: str, file_name: str = "") -> int | None:
    """ROC calendar year for ESUN e-statements (e.g. 115 -> 2026)."""
    match = ESUN_BILLING_PERIOD.search(statement_text)
    if match:
        return int(match.group("roc_year"))
    file_match = re.search(
        r"ESUN_ESTATEMENT_(\d{2,3})\d{2}",
        file_name,
        re.IGNORECASE,
    )
    if file_match:
        return int(file_match.group(1))
    return None


def _esun_mmdd_to_iso(mmdd: str, roc_year: int, *, reference_month: int | None = None) -> str | None:
    parts = mmdd.split("/")
    if len(parts) != 2:
        return None
    try:
        month, day = int(parts[0]), int(parts[1])
        gregorian_year = roc_year + 1911
        if reference_month is not None and month < reference_month:
            gregorian_year += 1
        return date(gregorian_year, month, day).isoformat()
    except ValueError:
        return None


def parse_esun_statement_line(line: str, roc_year: int) -> CtbcRocLineParts | None:
    """Parse 玉山 ESUN lines: MM/DD MM/DD merchant TWD amount."""
    normalized_line = " ".join(line.split())
    if not normalized_line or "TWD" not in normalized_line:
        return None

    two_dates = ESUN_LINE_TWO_DATES.match(normalized_line)
    if two_dates:
        spent_parts = two_dates.group("spent").split("/")
        try:
            spent_month = int(spent_parts[0])
        except (IndexError, ValueError):
            spent_month = None
        spent_iso = _esun_mmdd_to_iso(two_dates.group("spent"), roc_year)
        posted_iso = _esun_mmdd_to_iso(
            two_dates.group("posted"),
            roc_year,
            reference_month=spent_month,
        )
        if spent_iso is None or posted_iso is None:
            return None
        merchant = " ".join(two_dates.group("merchant").split())
        amount = parse_amount(two_dates.group("amount"))
    else:
        one_date = ESUN_LINE_ONE_DATE.match(normalized_line)
        if not one_date:
            return None
        spent_iso = _esun_mmdd_to_iso(one_date.group("spent"), roc_year)
        if spent_iso is None:
            return None
        posted_iso = spent_iso
        merchant = " ".join(one_date.group("merchant").split())
        amount = parse_amount(one_date.group("amount"))

    if merchant_raw_is_parse_noise(merchant):
        return None

    return CtbcRocLineParts(
        transaction_date=spent_iso,
        posted_date=posted_iso,
        merchant=merchant,
        amount=amount,
        mcc=None,
    )


def ctbc_line_dates_to_iso(parts: CtbcRocLineParts) -> tuple[str, str]:
    """Return ISO dates for batch import from ROC or Gregorian line parts."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts.transaction_date):
        return parts.transaction_date, parts.posted_date
    return roc_date_to_iso(parts.transaction_date), roc_date_to_iso(parts.posted_date)
