from datetime import date, datetime
import re
from typing import TypedDict

from backend.statement_parse_utils import (
    MERCHANT_MISSING_FROM_PDF_CATEGORY,
    MERCHANT_NOT_IN_PDF,
    extract_us_statement_date,
    extract_us_statement_period,
    infer_esun_statement_roc_year,
    infer_us_transaction_date,
    parse_amount,
    parse_ctbc_gregorian_statement_line,
    parse_ctbc_roc_statement_line,
    parse_esun_statement_line,
    parse_us_date,
    roc_date_string_to_iso,
)
from backend.transaction_exclusions import (
    PAYMENT_CATEGORY,
    is_credit_card_bill_payment,
)


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
            "AGODA",
            "BOOKING",
            "DELTA",
            "EXPEDIA",
            "HILTON",
            "HOTEL.COM",
            "HOTELS.COM",
            "HOTEL",
            "HYATT",
            "MARRIOTT",
            "SOUTHWEST",
            "TRIP.COM",
            "TRAVELODGE",
            "UNITED",
            "VRBO",
            "KLOOK",
            "兄弟大飯店",
            "長榮桂冠",
            "永豐棧",
            "加賀屋",
            "和逸飯店",
            "和逸",
            "新光影城",
            "蘭陽烏石港",
            "烏石港海景酒店",
            "巧虎夢想樂園",
            "XPARK",
            "Ｘｐａｒｋ",
            "屏東海生館",
            "臺灣鐵路管理局網路購票",
            "台灣鐵路管理局網路購票",
            "宜蘭力麗威斯汀",
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
            "優步",
            "优步",
            "55688",
            "55688APP",
            "５５６８８ＡＰＰ",
            "５５６８８",
            "台灣大車隊",
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
            "CARREFOUR",
            "大潤發",
            "好市多",
            "義美股份",
            "義美",
            "馬可先生",
            "聖德科斯",
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
            "星巴克",
            "TEA",
            "UBER EATS",
            "FOODPANDA",
            "DELIVEROO",
            "GRABFOOD",
            "英商奧天",
            "奧天股份",
            "ZADON",
            "渣等",
            "熊貓",
            "陳耀訓",
            "麵包埠",
            "麥當勞",
            "摩斯",
            "肯德基",
            "咖啡",
            "餐廳",
            "便當",
            "法朋",
            "雪坊精品",
            "朱記",
            "五花馬",
            "麥味登",
            "光鮨",
            "迴轉壽司",
            "MISS V BAKERY",
            "明星西點食品",
            "國泰餐飲",
            "興波咖啡",
            "雙月食品社",
            "好丘複合餐飲",
            "迷客夏",
            "九屯咖哩",
            "食穗",
            "芳珍蔬食",
            "空腹熊貓",
            "穗科",
            "樂麵屋",
            "PAUL法國麵",
            "善屋",
            "真芳碳烤吐司",
            "集客",
            "KOMEDA",
            "台灣壽司郎",
            "麵包廚房",
            "１０１美食街",
            "柑仔店",
            "錵鑶日本料理",
            "乾杯",
            "小食泰",
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
            "ＭＯＭＯ",
            "COUPANG",
            "ＣＯＵＰＡＮＧ",
            "酷澎",
            "SHOPEE",
            "蝦皮",
            "博客來",
            "新光三越",
            "微風",
            "優衣庫",
            "UNIQLO",
            "三民書局",
            "寶雅生活館",
            "寶雅",
            "久大文具",
            "艾比兒童眼鏡",
            "宜家家居",
            "ＩＫＥＡ",
            "麗嬰國際",
            "家樂福",
            "SHOPLINE",
            "ＳＨＯＰＬＩＮＥ",
            "安琪兒",
            "雅虎奇摩",
            "勤美",
            "誠品",
            "美麗華百樂園",
            "遠東SOGO",
            "遠東ＳＯＧＯ",
            "廣三崇光",
            "台灣艾瑪睡眠",
            "華山1914",
            "媽咪愛",
            "日商倍樂生",
            "樂購蝦皮",
            "統一時代百貨",
            "柑仔店有機健康超市",
            "奶娃的店",
            "MAMAWAY",
            "瑪黑家居",
            "老行家國際",
            "台灣大創百貨",
            "小丁婦幼",
            "三創數位",
            "生活工場",
            "黛安芬",
            "HOLA和樂家居館",
            "詩肯柚木",
            "GABEE",
            "富邦MOM",
            "富邦ｍｏｍ",
            "ＰＣＨＯＭＥ",
            "誠品線上",
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
            "國泰健康管理顧問",
            "卡多摩嬰童館",
            "卡多摩",
            "訊聯",
            "杏一醫療",
            "杏一",
            "松本清",
            "康是美",
            "三友藥妝",
            "社區照顧",
            "社團法人台灣社區照顧",
            "屈臣氏",
            "維康醫療",
            "新光醫療",
            "大樹健康",
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
            "國立台灣師範大學",
            "台灣師範大學",
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
            "CURSOR",
            "PERPLEXITY",
            "NOTION",
            "FIGMA",
            "GITHUB",
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
            "信用卡年費",
            "違約金",
            "利息",
        ),
    ),
]

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
        "Tax / Accounting",
        (
            "地價稅",
            "房屋稅",
            "INTUIT",
            "TURBOTAX",
            "TAX PREP",
            "H&R BLOCK",
            "ACCOUNTING",
        ),
    ),
    (
        "Professional Services",
        (
            "LEGAL",
            "CONSULTING",
            "AGENCY",
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
            "DISNEY PLUS",
            "DISNEY",
            "TELLO",
            "EXPRESSVPN",
            "LEETCODE",
            "MICROSOFT",
            "MSBILL",
            "MSBILL.INFO",
            "微軟",
            "台灣微軟",
            "Ｍｉｃｒｏｓｏｆｔ",
            "WHIZLABS",
            "WWW.WHIZLABS.COM",
        ),
    ),
]

AI_SERVICE_KEYWORDS: tuple[str, ...] = (
    "OPENAI",
    "CHATGPT",
    "CURSOR",
    "PERPLEXITY",
)


def categorize_merchant(merchant: str, amount: float | None = None) -> str:
    if is_credit_card_bill_payment(merchant):
        return PAYMENT_CATEGORY

    if merchant.strip() == MERCHANT_NOT_IN_PDF:
        return MERCHANT_MISSING_FROM_PDF_CATEGORY

    merchant_upper = " ".join(merchant.upper().split())
    normalized_merchant = re.sub(r"[^A-Z0-9]+", " ", merchant_upper).strip()
    if any(
        keyword in normalized_merchant or keyword in merchant_upper
        for keyword in AI_SERVICE_KEYWORDS
    ):
        return "Digital Services"

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
    return parse_us_date(date_text)


def _extract_statement_period(text: str) -> tuple[date, date] | None:
    return extract_us_statement_period(text)


def _extract_statement_date(text: str) -> date | None:
    return extract_us_statement_date(text)


def _infer_transaction_date(
    transaction_date_text: str,
    statement_period: tuple[date, date] | None,
    statement_date: date | None,
) -> str:
    return infer_us_transaction_date(
        transaction_date_text,
        statement_period,
        statement_date,
    )


def _parse_amount(amount_text: str) -> float:
    return parse_amount(amount_text)


def _parse_esun_tw_line(line: str, roc_year: int) -> Transaction | None:
    """玉山 ESUN: MM/DD MM/DD merchant TWD amount."""
    parsed_line = parse_esun_statement_line(line, roc_year)
    if parsed_line is None:
        return None

    merchant = parsed_line.merchant
    amount_value = parsed_line.amount
    return {
        "date": parsed_line.transaction_date,
        "merchant": merchant,
        "amount": amount_value,
        "category": categorize_merchant(merchant, amount_value),
        "currency": "TWD",
    }


def _parse_dbs_tw_line(line: str) -> Transaction | None:
    """星展台灣 / 澳盛 / CBGCC-DAILYSTMT: 消費日 入帳日 說明 ... 新台幣金額."""
    parsed_line = parse_ctbc_gregorian_statement_line(line)
    if parsed_line is None:
        return None

    amount_value = parsed_line.amount
    merchant = parsed_line.merchant
    return {
        "date": parsed_line.transaction_date,
        "merchant": merchant,
        "amount": amount_value,
        "category": categorize_merchant(merchant, amount_value),
        "currency": "TWD",
    }


def _roc_date_string_to_iso(roc_date: str) -> str | None:
    return roc_date_string_to_iso(roc_date)


def _parse_ctbc_tw_line(line: str) -> Transaction | None:
    """中國信託信用卡：消費日 入帳日 ... 新台幣金額 [MCC]."""
    parsed_line = parse_ctbc_roc_statement_line(line)
    if parsed_line is None:
        return None

    date_iso = _roc_date_string_to_iso(parsed_line.transaction_date)
    if not date_iso:
        return None

    merchant = parsed_line.merchant
    amount_value = parsed_line.amount
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

    esun_roc_year: int | None = None
    if "ESUN" in statement_upper or "玉山" in normalized:
        esun_roc_year = infer_esun_statement_roc_year(normalized, "")

    for raw_line in normalized.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if esun_roc_year is not None:
            esun_transaction = _parse_esun_tw_line(line, esun_roc_year)
            if esun_transaction:
                transactions.append(esun_transaction)
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
