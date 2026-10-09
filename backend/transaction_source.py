"""Stable identity for an imported purchase.

The same charge found again, including through a second batch run or an
upload of the same statement, keeps one dashboard row. Two identical
charges on one statement stay two rows because each gets its own
occurrence number.
"""

from __future__ import annotations

import re
from decimal import Decimal
from hashlib import sha256

from backend.currency import DEFAULT_BASE_CURRENCY, normalize_supported_currency_code
from backend.parser import Transaction

# Shared across upload stamping and every batch parser so the same statement
# text yields the same card last4 on every import path.
CARD_LAST4_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Card ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"Account ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"ending in\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"Ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"statements-(?P<last4>\d{4})-", re.IGNORECASE),
    re.compile(r"末四碼\s*(?P<last4>\d{4})"),
    re.compile(r"卡號末四碼\s*(?P<last4>\d{4})"),
    re.compile(
        r"(?P<last4>\d{4})\s*[-－]\s*XXXX\s*[-－]\s*XXXX\s*[-－]\s*\d{4}",
        re.IGNORECASE,
    ),
)


def _normalize_merchant(merchant: str) -> str:
    # Same folding as batch category keys: case, spacing, and punctuation.
    merchant_upper = " ".join(merchant.upper().split())
    return re.sub(r"[^0-9A-Z\u4e00-\u9fff]+", " ", merchant_upper).strip()


def card_last4_from_statement(text: str, *, file_name: str = "") -> str:
    """Return the first card last4 found in statement text (and optional file name)."""
    haystack = f"{file_name}\n{text}" if file_name else text
    for pattern in CARD_LAST4_PATTERNS:
        match = pattern.search(haystack)
        if match:
            return match.group("last4")
    return ""


def with_statement_card_last4(
    transactions: list[Transaction],
    statement_text: str,
    *,
    file_name: str = "",
) -> list[Transaction]:
    """Copy a statement's card last4 onto rows that do not already have one."""
    last4 = card_last4_from_statement(statement_text, file_name=file_name)
    if not last4:
        return transactions
    stamped: list[Transaction] = []
    for transaction in transactions:
        if str(transaction.get("card_last4") or "").strip():
            stamped.append(transaction)
            continue
        stamped.append({**transaction, "card_last4": last4})
    return stamped


def _amount_text(amount: float) -> str:
    quantized = Decimal(str(amount)).quantize(Decimal("0.01"))
    return f"{quantized:.2f}"


def _currency_text(transaction: Transaction) -> str:
    # Match insert_transactions persistence so upload/batch keys agree.
    return normalize_supported_currency_code(
        transaction.get("currency"),
        default=DEFAULT_BASE_CURRENCY,
    )


def _identity(
    transaction: Transaction,
) -> tuple[str, str, str, str, str]:
    return (
        transaction["date"].strip(),
        _normalize_merchant(transaction["merchant"]),
        _amount_text(float(transaction["amount"])),
        _currency_text(transaction),
        str(transaction.get("card_last4") or "").strip(),
    )


def source_keys_for_transactions(transactions: list[Transaction]) -> list[str]:
    """One key per row. Occurrence counts identical rows in this statement."""
    seen: dict[tuple[str, str, str, str, str], int] = {}
    keys: list[str] = []
    for transaction in transactions:
        identity = _identity(transaction)
        seen[identity] = seen.get(identity, 0) + 1
        payload = "|".join((*identity, str(seen[identity])))
        keys.append(sha256(payload.encode("utf-8")).hexdigest())
    return keys
