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
        ),
        category="Education / Certification",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=200,
    ),
    CategoryRule(
        patterns=("INTUIT", "TURBOTAX", "H&R BLOCK", "ACCOUNTING"),
        category="Tax / Accounting",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=300,
    ),
    CategoryRule(
        patterns=("AMAZON", "AMZN", "MOMO", "PCHOME", "SHOPEE", "RAKUTEN"),
        category="Shopping / Online Retail",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=400,
    ),
    CategoryRule(
        patterns=(
            "PRIME",
            "NETFLIX",
            "SPOTIFY",
            "APPLE.COM/BILL",
            "DROPBOX",
            "YOUTUBE PREMIUM",
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
        ),
        category="Groceries",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=800,
    ),
    CategoryRule(
        patterns=(
            "FOODPANDA",
            "UBEREATS",
            "UBER EATS",
            "STARBUCKS",
            "MCDONALD",
            "麥當勞",
        ),
        category="Dining / Food",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=900,
    ),
    CategoryRule(
        patterns=("UBER", "LYFT", "PARKING", "捷運", "高鐵", "台鐵", "THSR"),
        category="Transportation",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1000,
    ),
    CategoryRule(
        patterns=(
            "HOTEL",
            "AIRBNB",
            "BOOKING",
            "AGODA",
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
        patterns=("醫院", "診所", "藥局", "CVS", "HOSPITAL", "CLINIC"),
        category="Healthcare / Medical",
        exclude_from_spending=False,
        transaction_type="purchase",
        priority=1300,
    ),
    CategoryRule(
        patterns=("FEE", "FEES", "INTEREST", "手續費", "利息"),
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
            category=UNKNOWN_CATEGORY,
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
