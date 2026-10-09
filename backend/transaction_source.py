"""Stable identity for an imported purchase.

The same charge found again, including through a second batch run or an
upload of the same statement, keeps one dashboard row. Two identical
charges on one statement stay two rows because each gets its own
occurrence number.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from hashlib import sha256

from backend.currency import DEFAULT_BASE_CURRENCY, normalize_supported_currency_code
from backend.parser import Transaction
from backend.statement_parse_utils import normalize_statement_text
from backend.transaction_exclusions import PAYMENT_CATEGORY

AmountTextFn = Callable[..., str]

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

def statement_content_fingerprint(statement_text: str) -> str:
    """Canonical fingerprint for upload and batch statement identity."""
    normalized = normalize_statement_text(statement_text)
    return sha256(normalized.encode("utf-8")).hexdigest()


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


# Amount folding used before refund signs were kept in source keys (pre-#74).
_LEGACY_CREDIT_CATEGORIES = frozenset(
    {
        PAYMENT_CATEGORY,
        "Refunds & Credits",
    }
)
_LEGACY_UNKNOWN_OR_OTHER = frozenset(
    {"Unknown / Needs Review", "Other Spending"}
)


def _quantize_amount_text(signed: float) -> str:
    quantized = Decimal(str(signed)).quantize(Decimal("0.01"))
    return f"{quantized:.2f}"


def _amount_text(amount: float, *, category: str) -> str:
    """Keep the statement sign. Card payments are always negative.

    A refund must not share a key with the purchase it reverses. Payments
    stay negative so a positive upload amount and a negative batch amount
    for the same payment still match.
    """
    raw = float(amount)
    signed = -abs(raw) if category == PAYMENT_CATEGORY else raw
    return _quantize_amount_text(signed)


def _legacy_amount_text(amount: float, *, category: str) -> str:
    """Reproduce pre-#74 amount folding so existing hashed keys still match."""
    raw = float(amount)
    absolute = abs(raw)
    is_payment_or_credit = category in _LEGACY_CREDIT_CATEGORIES or (
        category in _LEGACY_UNKNOWN_OR_OTHER and raw < 0
    )
    signed = -absolute if is_payment_or_credit else absolute
    return _quantize_amount_text(signed)


def _currency_text(transaction: Transaction) -> str:
    # Match insert_transactions persistence so upload/batch keys agree.
    return normalize_supported_currency_code(
        transaction.get("currency"),
        default=DEFAULT_BASE_CURRENCY,
    )


def _statement_scope(transaction: Transaction, statement_scope: str) -> str:
    """Scope unknown-card rows to one statement so unrelated charges stay distinct.

    When card_last4 is known, scope is empty so the same purchase on the same
    card can collapse across daily/monthly statements (issue #73 intent).
    """
    if str(transaction.get("card_last4") or "").strip():
        return ""
    explicit = str(transaction.get("statement_scope") or "").strip()
    return explicit or statement_scope.strip()


def _identity(
    transaction: Transaction,
    *,
    statement_scope: str = "",
    amount_text_fn: AmountTextFn = _amount_text,
) -> tuple[str, str, str, str, str, str]:
    category = str(transaction.get("category") or "")
    return (
        transaction["date"].strip(),
        _normalize_merchant(transaction["merchant"]),
        amount_text_fn(float(transaction["amount"]), category=category),
        _currency_text(transaction),
        str(transaction.get("card_last4") or "").strip(),
        _statement_scope(transaction, statement_scope),
    )


def _keys_for_transactions(
    transactions: list[Transaction],
    *,
    statement_scope: str = "",
    amount_text_fn: AmountTextFn = _amount_text,
) -> list[str]:
    seen: dict[tuple[str, str, str, str, str, str], int] = {}
    keys: list[str] = []
    for transaction in transactions:
        identity = _identity(
            transaction,
            statement_scope=statement_scope,
            amount_text_fn=amount_text_fn,
        )
        seen[identity] = seen.get(identity, 0) + 1
        payload = "|".join((*identity, str(seen[identity])))
        keys.append(sha256(payload.encode("utf-8")).hexdigest())
    return keys


def source_keys_for_transactions(
    transactions: list[Transaction],
    *,
    statement_scope: str = "",
) -> list[str]:
    """One key per row. Occurrence counts identical rows in this statement list."""
    return _keys_for_transactions(
        transactions,
        statement_scope=statement_scope,
        amount_text_fn=_amount_text,
    )


def legacy_source_keys_for_transactions(
    transactions: list[Transaction],
    *,
    statement_scope: str = "",
) -> list[str]:
    """Keys as hashed before refund signs were kept; used only for dedupe."""
    return _keys_for_transactions(
        transactions,
        statement_scope=statement_scope,
        amount_text_fn=_legacy_amount_text,
    )


def normalize_merchant_for_source_key(merchant: str) -> str:
    """Public helper for matching legacy NULL-key rows during insert."""
    return _normalize_merchant(merchant)
