from datetime import date, datetime
import re
from typing import TypedDict


class Transaction(TypedDict):
    date: str
    merchant: str
    amount: float
    category: str


CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Income & Transfers",
        (
            "PAYROLL",
            "DIRECT DEP",
            "ACH CREDIT",
            "VENMO",
            "ZELLE",
            "CASH APP",
            "TRANSFER",
            "PAYMENT RECEIVED",
            "DEPOSIT",
        ),
    ),
    (
        "Travel",
        (
            "AIRBNB",
            "AIRLINES",
            "ALASKA AIR",
            "AMERICAN AIR",
            "BOOKING",
            "DELTA",
            "EXPEDIA",
            "HILTON",
            "HOTEL",
            "HYATT",
            "MARRIOTT",
            "SOUTHWEST",
            "TRAVELODGE",
            "UNITED",
            "VRBO",
        ),
    ),
    (
        "Transport",
        (
            "AMTRAK",
            "BART",
            "CHEVRON",
            "EXXON",
            "LYFT",
            "METRO",
            "PARKING",
            "SHELL",
            "TEXACO",
            "TRAIN",
            "UBER",
        ),
    ),
    (
        "Groceries",
        (
            "ALBERTSONS",
            "COSTCO",
            "GROCERY",
            "H MART",
            "KROGER",
            "SAFEWAY",
            "SPROUTS",
            "TRADER JOE",
            "WHOLE FOODS",
        ),
    ),
    (
        "Dining",
        (
            "BAKERY",
            "BAR",
            "BISTRO",
            "BURGER",
            "CAFE",
            "CHIPOTLE",
            "COFFEE",
            "DINER",
            "DOORDASH",
            "EATS",
            "GRILL",
            "KITCHEN",
            "MCDONALD",
            "PIZZA",
            "RESTAURANT",
            "STARBUCKS",
            "TEA",
            "UBER EATS",
        ),
    ),
    (
        "Shopping",
        (
            "AMAZON",
            "APPLE STORE",
            "BEST BUY",
            "EBAY",
            "ETSY",
            "HOME DEPOT",
            "IKEA",
            "MACY",
            "NIKE",
            "SEPHORA",
            "TARGET",
            "WALMART",
        ),
    ),
    (
        "Bills & Utilities",
        (
            "AT&T",
            "COMCAST",
            "ELECTRIC",
            "ENERGY",
            "INSURANCE",
            "INTERNET",
            "MORTGAGE",
            "PHONE",
            "RENT",
            "T MOBILE",
            "TMOBILE",
            "UTILITY",
            "VERIZON",
            "WATER",
            "WIFI",
        ),
    ),
    (
        "Entertainment",
        (
            "AMC",
            "APPLE.COM/BILL",
            "DISNEY",
            "HULU",
            "MOVIE",
            "NETFLIX",
            "PLAYSTATION",
            "SPOTIFY",
            "STEAM",
            "TICKETMASTER",
            "YOUTUBE",
        ),
    ),
    (
        "Health & Wellness",
        (
            "CLINIC",
            "CVS",
            "DENTAL",
            "FITNESS",
            "GYM",
            "HOSPITAL",
            "MEDICAL",
            "OPTICAL",
            "PHARMACY",
            "THERAPY",
            "URGENT CARE",
            "WALGREENS",
        ),
    ),
    (
        "Cash & Fees",
        (
            "ATM",
            "CASH WITHDRAWAL",
            "FEE",
            "FINANCE CHARGE",
            "INTEREST",
            "LATE FEE",
            "SERVICE CHARGE",
        ),
    ),
]

STATEMENT_PERIOD_PATTERN = re.compile(
    r"Opening/Closing Date\s+"
    r"(?P<start>\d{2}/\d{2}/\d{2,4})"
    r"\s*-\s*"
    r"(?P<end>\d{2}/\d{2}/\d{2,4})",
    re.IGNORECASE,
)

STATEMENT_DATE_PATTERN = re.compile(
    r"Statement Date:\s*(?P<date>\d{2}/\d{2}/\d{2,4})",
    re.IGNORECASE,
)


TRANSACTION_PATTERN = re.compile(
    r"^\s*"
    r"(?P<date>\d{2}/\d{2}(?:/\d{2,4})?)"
    r"\s+"
    r"(?P<merchant>.+?)"
    r"\s+"
    r"(?P<amount>\(?-?\$?\d[\d,]*(?:\.\d{2})?\)?)"
    r"\s*$",
)

TRANSACTION_PATTERN_ISO = re.compile(
    r"^\s*"
    r"(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s+"
    r"(?P<merchant>.+?)"
    r"\s+"
    r"(?P<amount>\(?-?\$?\d[\d,]*(?:\.\d{2})?\)?)"
    r"\s*$",
)

_TRANSACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    TRANSACTION_PATTERN,
    TRANSACTION_PATTERN_ISO,
)


def categorize_merchant(merchant: str, amount: float | None = None) -> str:
    normalized_merchant = re.sub(r"[^A-Z0-9]+", " ", merchant.upper()).strip()

    for category, keywords in CATEGORY_KEYWORDS:
        if any(keyword in normalized_merchant for keyword in keywords):
            return category

    # Ensure every transaction has an explicit, user-meaningful fallback.
    if amount is not None and amount < 0:
        return "Refunds & Credits"
    return "Other Spending"


def _parse_statement_date(date_text: str) -> date:
    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_text, date_format).date()
        except ValueError:
            continue

    raise ValueError(f"Unsupported statement date format: {date_text}")


def _extract_statement_period(text: str) -> tuple[date, date] | None:
    match = STATEMENT_PERIOD_PATTERN.search(text)

    if not match:
        return None

    return (
        _parse_statement_date(match.group("start")),
        _parse_statement_date(match.group("end")),
    )


def _extract_statement_date(text: str) -> date | None:
    match = STATEMENT_DATE_PATTERN.search(text)

    if not match:
        return None

    return _parse_statement_date(match.group("date"))


def _infer_transaction_date(
    transaction_date_text: str,
    statement_period: tuple[date, date] | None,
    statement_date: date | None,
) -> str:
    stripped = transaction_date_text.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stripped):
        try:
            datetime.strptime(stripped, "%Y-%m-%d").date()
            return stripped
        except ValueError:
            pass

    if transaction_date_text.count("/") == 2:
        return _parse_statement_date(transaction_date_text).isoformat()

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
        inferred_year = statement_date.year - 1 if month > statement_date.month else statement_date.year
        return date(inferred_year, month, day).isoformat()

    return transaction_date_text


def _parse_amount(amount_text: str) -> float:
    normalized = amount_text.replace("$", "").replace(",", "").strip()
    is_negative = normalized.startswith("(") and normalized.endswith(")")

    if is_negative:
        normalized = normalized[1:-1]

    amount = float(normalized)
    return -amount if is_negative else amount


def _parse_dbs_tw_line(line: str) -> Transaction | None:
    """星展台灣 / 澳盛 style: 消費日 入帳日 說明 ... 新台幣金額."""
    match = re.match(
        r"^\s*"
        r"(?P<d1>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<d2>\d{4}/\d{2}/\d{2})\s+"
        r"(?P<rest>.+)$",
        line,
    )
    if not match:
        return None

    rest = match.group("rest").strip()
    # Amount at line end; leading whitespace before it avoids matching `/05` in dates.
    amount_m = re.search(r"\s+(?P<am>-?[\d,]+(?:\.\d{1,2})?)\s*$", rest)
    if not amount_m:
        return None

    merchant = rest[: amount_m.start()].strip()
    if len(merchant) < 1:
        return None

    # Skip credit-limit / APR banner rows (not single NTD amounts).
    if "20,000/20,000" in rest or "7.99%" in rest:
        return None

    try:
        spent_date = datetime.strptime(match.group("d1"), "%Y/%m/%d").date()
    except ValueError:
        return None

    amount_value = _parse_amount(amount_m.group("am"))
    return {
        "date": spent_date.isoformat(),
        "merchant": " ".join(merchant.split()),
        "amount": amount_value,
        "category": categorize_merchant(merchant, amount_value),
    }


def _roc_date_string_to_iso(roc_date: str) -> str | None:
    """Convert Taiwan ROC calendar Y/M/D (民國) to ISO Gregorian date string."""
    parts = roc_date.strip().split("/")
    if len(parts) != 3:
        return None
    try:
        roc_year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
    try:
        gregorian_year = roc_year + 1911
        return date(gregorian_year, month, day).isoformat()
    except ValueError:
        return None


def _parse_ctbc_tw_line(line: str) -> Transaction | None:
    """中國信託信用卡：消費日 入帳日 ... 新台幣金額 MCC(6764/6232/3037)."""
    match = re.match(
        r"^\s*"
        r"(?P<d1>\d{2,3}/\d{1,2}/\d{1,2})\s+"
        r"(?P<d2>\d{2,3}/\d{1,2}/\d{1,2})\s+"
        r"(?P<rest>.+)$",
        line,
    )
    if not match:
        return None

    rest = match.group("rest")
    amount_m = re.search(
        r"(?P<amount>-?[\d,]+(?:\.\d{2})?)\s+(?P<mcc>6764|6232|3037)\b",
        rest,
    )
    if not amount_m:
        return None

    merchant = rest[: amount_m.start()].strip()
    merchant = " ".join(merchant.split())
    if not merchant:
        merchant = "(no description)"

    date_iso = _roc_date_string_to_iso(match.group("d1"))
    if not date_iso:
        return None

    amount_value = _parse_amount(amount_m.group("amount"))
    return {
        "date": date_iso,
        "merchant": merchant,
        "amount": amount_value,
        "category": categorize_merchant(merchant, amount_value),
    }


def parse_transactions(text: str) -> list[Transaction]:
    transactions: list[Transaction] = []
    normalized = (
        text.replace("\ufeff", "")
        .replace("\xa0", " ")
        .replace("\u2009", " ")
        .replace("\u202f", " ")
    )
    statement_period = _extract_statement_period(normalized)
    statement_date = _extract_statement_date(normalized)

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match: re.Match[str] | None = None
        for pattern in _TRANSACTION_PATTERNS:
            match = pattern.match(line)
            if match:
                break

        if match:
            merchant = " ".join(match.group("merchant").split())
            amount_value = _parse_amount(match.group("amount"))
            transactions.append(
                {
                    "date": _infer_transaction_date(
                        match.group("date"), statement_period, statement_date
                    ),
                    "merchant": merchant,
                    "amount": amount_value,
                    "category": categorize_merchant(merchant, amount_value),
                }
            )
            continue

        dbs_transaction = _parse_dbs_tw_line(line)
        if dbs_transaction:
            transactions.append(dbs_transaction)
            continue

        ctbc_transaction = _parse_ctbc_tw_line(line)
        if ctbc_transaction:
            transactions.append(ctbc_transaction)

    return transactions
