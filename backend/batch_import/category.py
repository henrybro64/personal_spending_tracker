import re
from dataclasses import dataclass
from typing import Iterable

from .models import TransactionType


UNKNOWN_CATEGORY = "Unknown / Needs Review"
PAYMENT_CATEGORY = "Payment / Credit"
FEE_CATEGORY = "Bank Fees / Interest"


@dataclass(frozen=True, slots=True)
class CategoryRule:
    patterns: tuple[str, ...]
    category: str
    exclude_from_spending: bool
    transaction_type: TransactionType
    priority: int


@dataclass(frozen=True, slots=True)
class CategoryMatch:
    category: str
    exclude_from_spending: bool
    transaction_type: TransactionType
    needs_review: bool


def normalize_merchant_key(merchant: str) -> str:
    merchant_upper = " ".join(merchant.upper().split())
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", " ", merchant_upper).strip()


def transaction_type_for_category(
    category: str, exclude_from_spending: bool
) -> TransactionType:
    if exclude_from_spending or category == PAYMENT_CATEGORY:
        return "payment_or_credit"
    if category == FEE_CATEGORY:
        return "fee_or_interest"
    return "purchase"


DEFAULT_RULES: tuple[CategoryRule, ...] = (
    CategoryRule(
        patterns=(
            "繳款",
            "網路繳款",
            "帳戶繳款",
            "本行帳戶繳款",
        ),
        category=PAYMENT_CATEGORY,
        exclude_from_spending=True,
        transaction_type="payment_or_credit",
        priority=50,
    ),
    CategoryRule(
        patterns=("PAYMENT", "THANK YOU", "REFUND", "CREDIT"),
        category=PAYMENT_CATEGORY,
        exclude_from_spending=True,
        transaction_type="payment_or_credit",
        priority=100,
    ),
    CategoryRule(
        patterns=(
            "MEASUREUP",
            "TUTORIALS DOJO",
            "UDEMY",
            "COURSERA",
            "PEARSON",
            "PROMETRIC",
            "國立台灣師範大學",
            "台灣師範大學",
        ),
        category="Education / Certification",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=200,
    ),
    CategoryRule(
        patterns=(
            "INTUIT",
            "TURBOTAX",
            "H&R BLOCK",
            "ACCOUNTING",
            "地價稅",
            "房屋稅",
        ),
        category="Tax / Accounting",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=300,
    ),
    CategoryRule(
        patterns=(
            "新光三越",
            "微風",
            "優衣庫",
            "UNIQLO",
        ),
        category="Shopping / Department Store",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=380,
    ),
    CategoryRule(
        patterns=(
            "三民書局",
            "寶雅生活館",
            "寶雅",
            "久大文具",
            "艾比兒童眼鏡",
            "宜家家居",
            "IKEA",
            "ＩＫＥＡ",
            "麗嬰國際",
            "家樂福",
            "SHOPLINE",
            "ＳＨＯＰＬＩＮＥ",
            "安琪兒",
            "勤美",
            "誠品",
            "美麗華百樂園",
            "遠東SOGO",
            "遠東ＳＯＧＯ",
            "廣三崇光",
            "台灣艾瑪睡眠",
            "華山1914",
            "華山１９１４",
            "媽咪愛",
            "日商倍樂生",
            "１０１文具天堂",
            "小南國婦幼館",
            "統一時代百貨",
            "柑仔店有機健康超市",
            "奶娃的店",
            "MAMAWAY",
            "Ｍａｍａｗａｙ",
            "瑪黑家居",
            "蘋果電腦",
            "老行家國際",
            "台灣大創百貨",
            "小丁婦幼",
            "三創數位",
            "生活工場",
            "黛安芬",
            "HOLA和樂家居館",
            "ＨＯＬＡ和樂家居館",
            "詩肯柚木",
            "GABEE",
            "ＧＡＢＥＥ",
        ),
        category="Shopping / Retail",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=392,
    ),
    CategoryRule(
        patterns=(
            "AMAZON",
            "AMZN",
            "MOMO",
            "ＭＯＭＯ",
            "富邦MOM",
            "富邦ｍｏｍ",
            "PCHOME",
            "ＰＣＨＯＭＥ",
            "COUPANG",
            "ＣＯＵＰＡＮＧ",
            "酷澎",
            "SHOPEE",
            "樂購蝦皮",
            "蝦皮",
            "RAKUTEN",
            "雅虎奇摩",
            "誠品線上",
            "博客來數位科技",
            "博客來",
        ),
        category="Shopping / Online Retail",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=400,
    ),
    CategoryRule(
        patterns=("OPENAI", "CHATGPT", "CURSOR", "PERPLEXITY"),
        category="Digital Services",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=450,
    ),
    CategoryRule(
        patterns=(
            "PRIME",
            "NETFLIX",
            "SPOTIFY",
            "APPLE.COM/BILL",
            "DROPBOX",
            "YOUTUBE PREMIUM",
            "DISNEY PLUS",
            "DISNEY+",
            "DISNEY",
            "TELLO",
            "EXPRESSVPN",
            "LEETCODE",
            "MICROSOFT",
            "Ｍｉｃｒｏｓｏｆｔ",
            "MSBILL",
            "WHIZLABS",
            "WWW.WHIZLABS.COM",
        ),
        category="Subscription / Membership",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=500,
    ),
    CategoryRule(
        patterns=(
            "DOSCITIZENSRVCS",
            "CONSULAR",
            "PASSPORT",
            "VISA APPLICATION",
            "AIT",
        ),
        category="Government / Consular",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=600,
    ),
    CategoryRule(
        patterns=("INS ", "INSURANCE", "保險"),
        category="Insurance",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=700,
    ),
    CategoryRule(
        patterns=(
            "全聯",
            "PX MART",
            "CARREFOUR",
            "COSTCO",
            "RT MART",
            "大潤發",
            "義美股份",
            "義美",
            "馬可先生",
            "聖德科斯",
        ),
        category="Groceries",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=800,
    ),
    CategoryRule(
        patterns=(
            "FOODPANDA",
            "DELIVEROO",
            "GRABFOOD",
            "英商奧天",
            "奧天股份",
            "UBEREATS",
            "UBER EATS",
            "STARBUCKS",
            "星巴克",
            "MCDONALD",
            "麥當勞",
            "陳耀訓",
            "麵包埠",
            "法朋",
            "雪坊精品",
            "明星西點食品",
            "朱記",
            "五花馬",
            "麥味登",
            "光鮨",
            "迴轉壽司",
            "台灣壽司郎",
            "國泰餐飲",
            "興波咖啡",
            "雙月食品社",
            "MISS V BAKERY",
            "ＭＩＳＳ Ｖ ＢＡＫＥＲＹ",
            "好丘複合餐飲",
            "迷客夏",
            "九屯咖哩",
            "食穗",
            "芳珍蔬食",
            "空腹熊貓",
            "穗科",
            "樂麵屋",
            "PAUL法國麵",
            "ＰＡＵＬ法國麵",
            "善屋",
            "真芳碳烤吐司",
            "集客",
            "KOMEDA",
            "Ｋｏｍｅｄａ",
            "PUREBREAD",
            "Ｐｕｒｅｂｒｅａｄ",
            "翠林小吃店",
            "麵包廚房",
            "１０１美食街",
            "柑仔店",
            "錵鑶日本料理",
            "乾杯",
            "小食泰",
            "ZADON",
            "渣等",
        ),
        category="Dining / Food",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=900,
    ),
    CategoryRule(
        patterns=(
            "中油",
            "台灣中油",
            "CPC",
            "台塑石油",
            "UBER",
            "優步",
            "优步",
            "55688",
            "55688APP",
            "５５６８８ＡＰＰ",
            "５５６８８",
            "台灣大車隊",
            "LYFT",
            "PARKING",
            "捷運",
            "高鐵",
            "台鐵",
            "THSR",
        ),
        category="Transportation",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1000,
    ),
    CategoryRule(
        patterns=(
            "HOTEL",
            "HOTELS.COM",
            "HOTEL.COM",
            "AIRBNB",
            "BOOKING",
            "AGODA",
            "TRIP.COM",
            "TRIP COM",
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
            "DELTA",
            "UNITED",
            "EVA AIR",
            "CHINA AIRLINES",
        ),
        category="Travel",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1100,
    ),
    CategoryRule(
        patterns=("中華電信", "遠傳", "台灣大哥大", "FET", "CHUNGHWA"),
        category="Utilities / Telecom",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1200,
    ),
    CategoryRule(
        patterns=(
            "醫院",
            "診所",
            "藥局",
            "CVS",
            "HOSPITAL",
            "CLINIC",
            "國泰健康管理顧問",
            "卡多摩嬰童館",
            "卡多摩",
            "訊聯",
            "杏一醫療",
            "杏一",
            "松本清",
            "康是美",
            "屈臣氏",
            "維康醫療",
            "新光醫療",
            "大樹健康",
            "三友藥妝",
            "社區照顧",
            "社團法人台灣社區照顧",
        ),
        category="Healthcare / Medical",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1300,
    ),
    CategoryRule(
        patterns=(
            "FEE",
            "FEES",
            "INTEREST",
            "手續費",
            "利息",
            "信用卡年費",
        ),
        category=FEE_CATEGORY,
        exclude_from_spending=False,
        transaction_type="fee_or_interest",
        priority=1400,
    ),
    CategoryRule(
        patterns=("CASH ADVANCE", "ATM"),
        category="Cash Advance / ATM",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1500,
    ),
)


class CategoryEngine:
    def __init__(self, learned_rules: Iterable[CategoryRule] | None = None):
        ordered_rules = list(learned_rules or [])
        ordered_rules.extend(DEFAULT_RULES)
        self._rules = sorted(ordered_rules, key=lambda rule: rule.priority)

    def categorize(self, merchant_raw: str, amount: float) -> CategoryMatch:
        merchant_upper = " ".join(merchant_raw.upper().split())
        merchant_key = normalize_merchant_key(merchant_raw)

        for rule in self._rules:
            if self._matches(rule, merchant_upper, merchant_key):
                return CategoryMatch(
                    category=rule.category,
                    exclude_from_spending=rule.exclude_from_spending,
                    transaction_type=rule.transaction_type,
                    needs_review=False,
                )

        return CategoryMatch(
            category="Other Spending",
            exclude_from_spending=amount < 0,
            transaction_type="payment_or_credit" if amount < 0 else "purchase",
            needs_review=True,
        )

    def _matches(
        self, rule: CategoryRule, merchant_upper: str, merchant_key: str
    ) -> bool:
        for pattern in rule.patterns:
            normalized_pattern = normalize_merchant_key(pattern)
            if normalized_pattern and normalized_pattern in merchant_key:
                return True
            upper_pattern = " ".join(pattern.upper().split())
            if upper_pattern and upper_pattern in merchant_upper:
                return True
        return False
