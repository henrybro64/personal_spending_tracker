from datetime import date, datetime
import re
from typing import TypedDict


class Transaction(TypedDict):
    date: str
    merchant: str
    amount: float
    category: str
    currency: str


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
            "UBIKE",
            "TAXI",
            "台鐵",
            "高鐵",
            "捷運",
            "計程車",
            "中油",
            "台灣中油",
            "停車",
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
            "PX MART",
            "全聯",
            "家樂福",
            "CARREFOUR",
            "大潤發",
            "好市多",
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
            "FOODPANDA",
            "熊貓",
            "麥當勞",
            "摩斯",
            "肯德基",
            "咖啡",
            "餐廳",
            "便當",
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
            "PCHOME",
            "MOMO",
            "SHOPEE",
            "蝦皮",
            "博客來",
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
            "台電",
            "台水",
            "瓦斯",
            "中華電信",
            "遠傳",
            "台灣大哥大",
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
            "威秀",
            "秀泰",
            "KTV",
            "NET",
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
            "藥局",
            "診所",
            "醫院",
            "健身",
        ),
    ),
    (
        "Education",
        (
            "UDEMY",
            "COURSERA",
            "SKILLSHARE",
            "TUITION",
            "SCHOOL",
            "補習班",
            "學費",
            "課程",
            "書局",
        ),
    ),
    (
        "Home",
        (
            "IKEA",
            "HOLA",
            "HOMEBOX",
            "特力屋",
            "家具",
            "家居",
            "寢具",
            "家電",
        ),
    ),
    (
        "Digital Services",
        (
            "APPLE.COM/BILL",
            "GOOGLE",
            "ICLOUD",
            "OPENAI",
            "CHATGPT",
            "NOTION",
            "FIGMA",
            "GITHUB",
            "MICROSOFT",
            "MSBILL",
            "MSBILL.INFO",
            "微軟",
            "台灣微軟",
            "ADOBE",
            "CANVA",
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
            "手續費",
            "年費",
            "違約金",
            "利息",
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

TWD_STATEMENT_MARKERS: tuple[str, ...] = (
    "中國信託",
    "CTBC",
    "玉山",
    "E SUN",
    "ESUN",
    "星展",
    "DBS",
    "台新",
    "TAISHIN",
    "國泰",
    "CATHAY",
    "富邦",
    "FUBON",
    "永豐",
    "SINOPAC",
    "兆豐",
    "MEGA",
    "第一銀行",
    "FIRST BANK",
    "華南",
    "HUA NAN",
    "合作金庫",
    "土地銀行",
    "聯邦",
    "UNION BANK OF TAIWAN",
    "新光",
    "彰化銀行",
    "CHB",
)

USD_STATEMENT_MARKERS: tuple[str, ...] = (
    "CHASE",
    "JPMORGAN CHASE",
    "AMERICAN EXPRESS",
    "AMEX",
)

OTHER_SPENDING_GRANULAR_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Convenience Stores",
        (
            "7-ELEVEN",
            "7 11",
            "FAMILYMART",
            "OK MART",
            "HI-LIFE",
            "全家",
            "統一超商",
            "萊爾富",
            "OK便利商店",
        ),
    ),
    (
        "Beauty & Personal Care",
        (
            "WATSONS",
            "COSMED",
            "POYA",
            "寶雅",
            "屈臣氏",
            "康是美",
            "SALON",
            "BARBER",
            "SPA",
            "美甲",
            "美容",
            "理髮",
        ),
    ),
    (
        "Kids & Family",
        (
            "TOYS",
            "BABY",
            "MOTHERCARE",
            "KID",
            "媽咪",
            "婦幼",
            "玩具",
            "托嬰",
            "保母",
        ),
    ),
    (
        "Pets",
        (
            "PET",
            "VET",
            "ANIMAL HOSPITAL",
            "寵物",
            "獸醫",
            "毛孩",
            "飼料",
        ),
    ),
    (
        "Gifts & Donations",
        (
            "DONATION",
            "CHARITY",
            "CHURCH",
            "TEMPLE",
            "GIFT",
            "FLOWER",
            "禮物",
            "捐款",
            "香油錢",
            "花店",
        ),
    ),
    (
        "Repair & Maintenance",
        (
            "REPAIR",
            "MAINTENANCE",
            "FIX",
            "SERVICE CENTER",
            "維修",
            "保養",
            "修車",
            "機車行",
            "汽車保養",
        ),
    ),
    (
        "Professional Services",
        (
            "ACCOUNTING",
            "LEGAL",
            "CONSULTING",
            "AGENCY",
            "INTUIT",
            "TURBOTAX",
            "TAX PREP",
            "手續代辦",
            "代書",
            "律師",
            "會計",
            "顧問",
        ),
    ),
    (
        "Subscriptions",
        (
            "SUBSCRIPTION",
            "MONTHLY",
            "PATREON",
            "MEDIUM",
            "YOUTUBE PREMIUM",
            "SPOTIFY",
            "NETFLIX",
            "DISNEY+",
        ),
    ),
]


def categorize_merchant(merchant: str, amount: float | None = None) -> str:
    merchant_upper = " ".join(merchant.upper().split())
    normalized_merchant = re.sub(r"[^A-Z0-9]+", " ", merchant_upper).strip()

    for category, keywords in CATEGORY_KEYWORDS:
        if any(
            keyword in normalized_merchant or keyword in merchant_upper
            for keyword in keywords
        ):
            return category

    for category, keywords in OTHER_SPENDING_GRANULAR_KEYWORDS:
        if any(
            keyword in normalized_merchant or keyword in merchant_upper
            for keyword in keywords
        ):
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
        "currency": "TWD",
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
        "currency": "TWD",
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
    statement_upper = normalized.upper()
    has_tw_bank_marker = any(marker in statement_upper for marker in TWD_STATEMENT_MARKERS)
    has_us_bank_marker = any(marker in statement_upper for marker in USD_STATEMENT_MARKERS)
    has_twd_token = any(
        token in normalized
        for token in (
            "新台幣",
            "台幣",
            "NT$",
            "NTD",
            "TWD",
        )
    ) or any(
        token in statement_upper
        for token in (
            "NT$",
            "NTD",
            "TWD",
        )
    )
    if has_tw_bank_marker:
        statement_currency = "TWD"
    elif has_us_bank_marker:
        statement_currency = "USD"
    else:
        statement_currency = "TWD" if has_twd_token else "USD"

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
                    "currency": statement_currency,
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
