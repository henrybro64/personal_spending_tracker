"""Detect credit-card bill payments that should not count as spending."""

from __future__ import annotations

PAYMENT_CATEGORY = "Payment / Credit"

EXCLUDED_SPENDING_CATEGORIES = frozenset(
    {
        PAYMENT_CATEGORY,
    }
)


def is_credit_card_bill_payment(merchant: str) -> bool:
    """True for Taiwan statement lines that pay the prior month's card balance."""
    normalized = merchant.strip()
    if not normalized:
        return False
    return "繳款" in normalized


def should_exclude_from_spending(
    merchant: str,
    *,
    category: str | None = None,
) -> bool:
    if is_credit_card_bill_payment(merchant):
        return True
    if category and category.strip() in EXCLUDED_SPENDING_CATEGORIES:
        return True
    return False
