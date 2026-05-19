from __future__ import annotations

import re
from pathlib import Path

from backend.parser import parse_transactions

from .category import CategoryEngine
from .models import NormalizedTransaction

_LAST4_PATTERNS = (
    re.compile(r"末四碼\s*(?P<last4>\d{4})"),
    re.compile(r"卡號末四碼\s*(?P<last4>\d{4})"),
    re.compile(r"ending in\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"Ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"statements-(?P<last4>\d{4})-", re.IGNORECASE),
)


def _infer_card_last4(source_path: Path, statement_text: str) -> str | None:
    haystack = f"{source_path.name}\n{statement_text}"
    for pattern in _LAST4_PATTERNS:
        match = pattern.search(haystack)
        if match:
            return match.group("last4")
    return None


def transactions_from_upload_parser(
    source_path: Path,
    statement_text: str,
    category_engine: CategoryEngine,
    *,
    bank: str,
) -> list[NormalizedTransaction]:
    """Fallback when bank-specific batch parsers find no rows in PDF text."""
    card_last4 = _infer_card_last4(source_path, statement_text)
    transactions: list[NormalizedTransaction] = []

    for row in parse_transactions(statement_text):
        merchant = row["merchant"]
        raw_amount = float(row["amount"])
        category_match = category_engine.categorize(merchant, raw_amount)
        absolute_amount = abs(raw_amount)
        normalized_amount = (
            -absolute_amount
            if category_match.transaction_type == "payment_or_credit"
            else absolute_amount
        )
        transactions.append(
            NormalizedTransaction(
                statement_file=source_path.name,
                bank=bank,
                card_last4=card_last4,
                transaction_date=row["date"],
                posted_date=None,
                merchant_raw=merchant,
                amount=normalized_amount,
                currency=row["currency"],
                transaction_type=category_match.transaction_type,
                category=category_match.category,
                exclude_from_spending=category_match.exclude_from_spending,
                needs_review=category_match.needs_review,
            )
        )

    return transactions
