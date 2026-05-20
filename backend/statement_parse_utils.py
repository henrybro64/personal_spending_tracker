"""Shared statement text normalization and parsing helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from backend.batch_import.category import merchant_raw_is_digit_only_noise

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

CTBC_AMOUNT_TRAILING = re.compile(
    r"^(?P<merchant>.+?)\s+"
    r"(?:USD\s+(?P<foreign_amount>[-\d,.]+)\s+)?"
    r"(?P<amount>-?[\d,]+(?:\.\d{2})?)"
    r"(?:\s+(?P<mcc>\d{4}))?"
    r"\s*$"
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
    trailing_match = CTBC_AMOUNT_TRAILING.match(rest)
    if not trailing_match:
        return None

    merchant = " ".join(trailing_match.group("merchant").split())
    if not merchant:
        merchant = "(no description)"

    if merchant_raw_is_digit_only_noise(merchant):
        return None

    return CtbcRocLineParts(
        transaction_date=header_match.group("transaction_date"),
        posted_date=header_match.group("posted_date"),
        merchant=merchant,
        amount=parse_amount(trailing_match.group("amount")),
        mcc=trailing_match.group("mcc"),
    )
