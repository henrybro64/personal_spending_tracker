from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from backend.parser import parse_transactions

from .category import CategoryEngine
from .models import NormalizedTransaction

# PDF text for Taiwan banks (CTBC/DBS) is parsed reliably by parser.py.
# US cards need statement period metadata that batch parsers enforce.
# Therefore, do not fallback for US-card parsers.
UPLOAD_FALLBACK_PARSER_NAMES = frozenset({"ctbc_daily", "ctbc_card", "dbs"})

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_LAST4_PATTERNS = (
    re.compile(r"末四碼\s*(?P<last4>\d{4})"),
    re.compile(r"卡號末四碼\s*(?P<last4>\d{4})"),
    re.compile(r"ending in\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"Ending\s+(?P<last4>\d{4})", re.IGNORECASE),
    re.compile(r"statements-(?P<last4>\d{4})-", re.IGNORECASE),
)


def upload_fallback_supported(parser_name: str) -> bool:
    return parser_name in UPLOAD_FALLBACK_PARSER_NAMES


def _normalized_transaction_key(
    transaction: NormalizedTransaction,
) -> tuple[str, str, float]:
    return (
        transaction.transaction_date,
        transaction.merchant_raw.upper(),
        round(float(transaction.amount), 2),
    )


def merge_normalized_transactions(
    primary: list[NormalizedTransaction],
    supplemental: list[NormalizedTransaction],
) -> list[NormalizedTransaction]:
    """Union batch-parser and upload-parser rows, deduping identical charges."""
    seen = {_normalized_transaction_key(transaction) for transaction in primary}
    merged = list(primary)
    for transaction in supplemental:
        key = _normalized_transaction_key(transaction)
        if key in seen:
            continue
        merged.append(transaction)
        seen.add(key)
    return merged


def _is_valid_iso_date(date_text: str) -> bool:
    if not _ISO_DATE.fullmatch(date_text.strip()):
        return False
    try:
        date.fromisoformat(date_text.strip())
    except ValueError:
        return False
    return True


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
        if not _is_valid_iso_date(row["date"]):
            continue

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
