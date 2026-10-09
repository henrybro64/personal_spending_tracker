"""Detect credit-card bill payments that should not count as spending."""

from __future__ import annotations

import re

PAYMENT_CATEGORY = "Payment / Credit"

EXCLUDED_SPENDING_CATEGORIES = frozenset(
    {
        PAYMENT_CATEGORY,
    }
)

# Whole phrases only. "APPLE PAYMENT SERVICE" and "PAYMENTUS" must not match.
CARD_PAYMENT_PHRASES: tuple[str, ...] = (
    "PAYMENT THANK YOU",
    "THANK YOU PAYMENT",
    "AUTOPAY",
    "AUTOMATIC PAYMENT",
    "AUTO PAY",
    "ONLINE PAYMENT",
    "MOBILE PAYMENT",
    "PAYMENT RECEIVED",
    "繳款",
)

_CARD_PAYMENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"(?<![A-Z0-9]){re.escape(phrase)}(?![A-Z0-9])")
    for phrase in CARD_PAYMENT_PHRASES
)


def normalize_payment_merchant(merchant: str) -> str:
    return " ".join(merchant.upper().split())


def is_credit_card_bill_payment(merchant: str) -> bool:
    """True for Taiwan statement lines that pay the prior month's card balance."""
    normalized = merchant.strip()
    if not normalized:
        return False
    return "繳款" in normalized


def is_card_payment_merchant(merchant: str) -> bool:
    """True when the line pays the card, not a merchant named payment."""
    if is_credit_card_bill_payment(merchant):
        return True
    normalized = normalize_payment_merchant(merchant)
    if not normalized:
        return False
    return any(
        pattern.search(normalized) for pattern in _CARD_PAYMENT_PATTERNS
    )


def should_exclude_from_spending(
    merchant: str,
    *,
    category: str | None = None,
) -> bool:
    if is_card_payment_merchant(merchant):
        return True
    if category and category.strip() in EXCLUDED_SPENDING_CATEGORIES:
        return True
    return False
