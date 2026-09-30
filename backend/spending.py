"""One spending population for dashboard totals and summaries.

Model rows are the transactions that pass the bill-payment exclusion and can
be shown in USD or TWD. Summary totals and ``model_total_in_active_currency``
both use that set. Search, merchant, and amount slicers narrow a selection
on top of the model; they do not change the model total.

Rows in other currencies are included only when they already have a USD or
TWD base amount. Otherwise they stay in ``unconverted_transactions`` and out
of the money totals.
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

from backend.currency import (
    DEFAULT_BASE_CURRENCY,
    DisplayCurrency,
    convert_amount,
    normalize_supported_currency_code,
)
from backend.database import StoredTransaction, TransactionsSummary
from backend.transaction_exclusions import should_exclude_from_spending

STATEMENT_COUNT_SCOPE: Literal["all_time"] = "all_time"
UNCONVERTED_REASON: Literal["unsupported_display_conversion"] = (
    "unsupported_display_conversion"
)
_DISPLAY_CURRENCIES = frozenset({"USD", "TWD"})


class DisplayTransaction(StoredTransaction):
    original_amount: float
    original_currency: str
    display_amount: float
    display_currency: DisplayCurrency
    is_converted: bool


class UnconvertedTransaction(TypedDict):
    id: int
    date: str
    merchant: str
    original_amount: float
    original_currency: str
    reason: Literal["unsupported_display_conversion"]


def partition_spending_transactions(
    transactions: list[StoredTransaction],
    display_currency: DisplayCurrency,
) -> tuple[list[DisplayTransaction], list[UnconvertedTransaction]]:
    model_rows: list[DisplayTransaction] = []
    unconverted: list[UnconvertedTransaction] = []
    for transaction in transactions:
        if should_exclude_from_spending(
            transaction["merchant"],
            category=transaction.get("category"),
        ):
            continue
        projected = project_display_transaction(transaction, display_currency)
        if projected is None:
            original_amount, original_currency = _original_money(transaction)
            unconverted.append(
                {
                    "id": transaction["id"],
                    "date": transaction["date"],
                    "merchant": transaction["merchant"],
                    "original_amount": original_amount,
                    "original_currency": original_currency,
                    "reason": UNCONVERTED_REASON,
                }
            )
            continue
        model_rows.append(projected)
    return model_rows, unconverted


def convertible_home_amount(
    transaction: StoredTransaction,
) -> tuple[DisplayCurrency, float] | None:
    """USD or TWD amount for a row that belongs in the spending model.

    Bill payments and rows without a USD or TWD base return None. A stored
    USD or TWD base amount wins over the original amount.
    """
    if should_exclude_from_spending(
        transaction["merchant"],
        category=transaction.get("category"),
    ):
        return None
    base_amount = transaction.get("base_amount")
    if base_amount is not None:
        base_currency = _display_base_currency(transaction.get("base_currency"))
        if base_currency is not None:
            return base_currency, float(base_amount)
    original_amount, original_currency = _original_money(transaction)
    if original_currency in _DISPLAY_CURRENCIES:
        return cast(DisplayCurrency, original_currency), original_amount
    return None


def project_display_transaction(
    transaction: StoredTransaction,
    display_currency: DisplayCurrency,
) -> DisplayTransaction | None:
    original_amount, original_currency = _original_money(transaction)
    display_amount = _display_amount(
        transaction,
        original_amount=original_amount,
        original_currency=original_currency,
        display_currency=display_currency,
    )
    if display_amount is None:
        return None
    projected = {
        **transaction,
        "amount": display_amount,
        "currency": display_currency,
        "original_amount": original_amount,
        "original_currency": original_currency,
        "display_amount": display_amount,
        "display_currency": display_currency,
        "is_converted": original_currency != display_currency,
    }
    return cast(DisplayTransaction, projected)


def build_spending_summary(
    model_rows: list[DisplayTransaction],
    *,
    statement_count: int,
    unconverted_count: int,
) -> TransactionsSummary:
    total_spending = sum(float(row["amount"]) for row in model_rows)
    transaction_count = len(model_rows)
    average_amount = (
        total_spending / transaction_count if transaction_count else 0.0
    )
    return {
        "total_spending": float(total_spending),
        "number_of_transactions": transaction_count,
        "average_transaction_amount": float(average_amount),
        "number_of_statements": statement_count,
        "unconverted_transaction_count": unconverted_count,
        "statement_count_scope": STATEMENT_COUNT_SCOPE,
    }


def _original_money(transaction: StoredTransaction) -> tuple[float, str]:
    original_amount = float(
        transaction.get("original_amount", transaction["amount"])
    )
    raw_currency = (
        transaction.get("original_currency")
        or transaction.get("currency")
        or DEFAULT_BASE_CURRENCY
    )
    try:
        original_currency = normalize_supported_currency_code(str(raw_currency))
    except ValueError:
        original_currency = str(raw_currency).strip().upper() or "UNKNOWN"
    return original_amount, original_currency


def _display_amount(
    transaction: StoredTransaction,
    *,
    original_amount: float,
    original_currency: str,
    display_currency: DisplayCurrency,
) -> float | None:
    base_amount = transaction.get("base_amount")
    if base_amount is not None:
        base_currency = _display_base_currency(transaction.get("base_currency"))
        if base_currency is not None:
            return convert_amount(float(base_amount), base_currency, display_currency)
    if original_currency in _DISPLAY_CURRENCIES:
        return convert_amount(original_amount, original_currency, display_currency)
    return None


def _display_base_currency(base_currency: object) -> DisplayCurrency | None:
    try:
        normalized = normalize_supported_currency_code(
            str(base_currency) if base_currency is not None else None,
            default=DEFAULT_BASE_CURRENCY,
        )
    except ValueError:
        return None
    if normalized in _DISPLAY_CURRENCIES:
        return cast(DisplayCurrency, normalized)
    return None
