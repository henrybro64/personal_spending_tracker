"""Parse pasted JSON or line-oriented text into batch-import transactions."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Literal

from backend.batch_import.amounts import signed_amount
from backend.batch_import.category import (
    CategoryEngine,
    transaction_type_for_category,
)
from backend.batch_import.models import NormalizedTransaction, TransactionType

ImportDestination = Literal["batch", "dashboard"]
ImportFormat = Literal["auto", "json", "text"]

_MAX_ROWS = 5000
_TEXT_LINE_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<merchant>.+?)\s+"
    r"(?P<amount>-?[\d,]+(?:\.\d{2})?)$"
)
_HEADER_ALIASES = frozenset(
    {"date", "merchant", "amount", "category", "currency", "bank"}
)


class BulkImportParseError(ValueError):
    """Raised when import content cannot be parsed."""


def parse_import_content(
    content: str,
    *,
    format: ImportFormat = "auto",
    bank: str = "Manual",
    currency: str = "TWD",
    source_name: str = "import",
    category_engine: CategoryEngine | None = None,
) -> list[NormalizedTransaction]:
    normalized_content = content.strip()
    if not normalized_content:
        raise BulkImportParseError("Import content is empty.")

    resolved_format = _resolve_format(normalized_content, format)
    if resolved_format == "json":
        raw_rows, defaults = _parse_json_payload(normalized_content)
    else:
        raw_rows = _parse_text_payload(normalized_content)
        defaults = {}

    if len(raw_rows) > _MAX_ROWS:
        raise BulkImportParseError(
            f"Too many rows ({len(raw_rows)}). Maximum is {_MAX_ROWS}."
        )

    _validate_card_counts(raw_rows, defaults.get("card_counts"))

    engine = category_engine or CategoryEngine()
    default_bank = str(defaults.get("bank") or bank).strip() or "Manual"
    default_currency = str(defaults.get("currency") or currency).strip() or "TWD"
    statement_file = _normalize_source_name(
        str(defaults.get("source") or defaults.get("source_name") or source_name)
    )

    transactions: list[NormalizedTransaction] = []
    for index, raw_row in enumerate(raw_rows, start=1):
        if not isinstance(raw_row, dict):
            raise BulkImportParseError(
                f"Row {index} must be a JSON object, got {type(raw_row).__name__}."
            )
        try:
            transactions.append(
                _normalize_row(
                    raw_row,
                    statement_file=statement_file,
                    default_bank=default_bank,
                    default_currency=default_currency,
                    category_engine=engine,
                )
            )
        except BulkImportParseError as exc:
            raise BulkImportParseError(f"Row {index}: {exc}") from exc

    if not transactions:
        raise BulkImportParseError("No transactions found in import content.")

    return transactions


def import_format_example() -> dict[str, Any]:
    """Schema hint for UI and LLM prompts."""
    return {
        "source": "CTBC 2026-05",
        "bank": "CTBC",
        "currency": "TWD",
        "transactions": [
            {
                "date": "2026-05-13",
                "merchant": "IKEA 宜家家居",
                "amount": 813.0,
                "posted_date": "2026-05-15",
                "card_last4": "1234",
                "category": "Shopping / Online Retail",
            },
            {
                "date": "2026-05-04",
                "merchant": "全聯實業",
                "amount": 736.0,
            },
        ],
    }


def _resolve_format(content: str, format: ImportFormat) -> Literal["json", "text"]:
    if format == "json":
        return "json"
    if format == "text":
        return "text"
    stripped = content.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    return "text"


def _parse_json_payload(content: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise BulkImportParseError(f"Invalid JSON: {exc.msg}") from exc

    defaults: dict[str, Any] = {}
    if isinstance(payload, list):
        return [_coerce_row(item) for item in payload], defaults
    if isinstance(payload, dict):
        rows = payload.get("transactions")
        if rows is None:
            rows = payload.get("items")
        if rows is None:
            raise BulkImportParseError(
                'JSON object must include a "transactions" (or "items") array.'
            )
        if not isinstance(rows, list):
            raise BulkImportParseError('"transactions" must be a JSON array.')
        for key in ("source", "source_name", "bank", "currency", "card_counts"):
            if key in payload:
                defaults[key] = payload[key]
        return [_coerce_row(item) for item in rows], defaults
    raise BulkImportParseError("JSON must be an object or array.")


def _validate_card_counts(
    raw_rows: list[dict[str, Any]],
    card_counts: Any,
) -> None:
    if card_counts is None:
        return
    if not isinstance(card_counts, dict):
        raise BulkImportParseError("card_counts must be a JSON object.")

    normalized_counts: dict[str, int] = {}
    for card_last4, raw_count in card_counts.items():
        card = str(card_last4).strip()
        if not re.fullmatch(r"\d{4}", card):
            raise BulkImportParseError("card_counts keys must be four-digit card numbers.")
        if isinstance(raw_count, bool) or not isinstance(raw_count, int) or raw_count < 0:
            raise BulkImportParseError("card_counts values must be non-negative integers.")
        normalized_counts[card] = raw_count

    if sum(normalized_counts.values()) != len(raw_rows):
        raise BulkImportParseError("ERROR_CARD_COUNT_MISMATCH")

    actual_counts: dict[str, int] = {}
    for row in raw_rows:
        card = str(row.get("card_last4") or "").strip()
        if card:
            actual_counts[card] = actual_counts.get(card, 0) + 1

    if actual_counts != {card: count for card, count in normalized_counts.items() if count > 0}:
        raise BulkImportParseError("ERROR_CARD_COUNT_MISMATCH")


def _coerce_row(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise BulkImportParseError("Each transaction must be a JSON object.")
    return item


def _parse_text_payload(content: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _looks_like_header(line) and not rows:
            continue
        row = _parse_text_line(line, line_number)
        rows.append(row)
    return rows


def _looks_like_header(line: str) -> bool:
    parts = _split_text_fields(line)
    if len(parts) < 3:
        return False
    return parts[0].lower() in _HEADER_ALIASES


def _parse_text_line(line: str, line_number: int) -> dict[str, Any]:
    if "\t" in line:
        return _parse_tab_separated_line(line, line_number)

    match = _TEXT_LINE_PATTERN.match(line)
    if match is not None:
        return {
            "date": match.group("date"),
            "merchant": match.group("merchant").strip(),
            "amount": match.group("amount"),
        }

    if "," in line:
        return _parse_comma_separated_line(line, line_number)

    raise BulkImportParseError(
        f"Line {line_number} must be tab-separated, comma-separated "
        f"(date,merchant,amount), or 'YYYY-MM-DD merchant amount'."
    )


def _parse_tab_separated_line(line: str, line_number: int) -> dict[str, Any]:
    parts = [part.strip() for part in line.split("\t")]
    if len(parts) < 3:
        raise BulkImportParseError(
            f"Line {line_number} needs at least date, merchant, and amount columns."
        )

    date_text = parts[0]
    amount_text = parts[-1]
    row: dict[str, Any] = {
        "date": date_text,
        "merchant": parts[1] if len(parts) == 3 else " ".join(parts[1:-2]),
        "amount": amount_text,
    }
    if len(parts) == 4:
        row["merchant"] = parts[1]
        row["category"] = parts[2]
    return row


def _parse_comma_separated_line(line: str, line_number: int) -> dict[str, Any]:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 3:
        raise BulkImportParseError(
            f"Line {line_number} needs at least date, merchant, and amount columns."
        )

    row: dict[str, Any] = {
        "date": parts[0],
        "merchant": parts[1],
        "amount": parts[-1],
    }
    if len(parts) == 4:
        row["category"] = parts[2]
    return row


def _split_text_fields(line: str) -> list[str]:
    if "," not in line:
        return line.split()
    return [part.strip() for part in line.split(",")]


def _normalize_row(
    raw_row: dict[str, Any],
    *,
    statement_file: str,
    default_bank: str,
    default_currency: str,
    category_engine: CategoryEngine,
) -> NormalizedTransaction:
    date_text = _require_string(
        raw_row.get("date") or raw_row.get("transaction_date"),
        field="date",
    )
    _validate_iso_date(date_text)

    merchant = _require_string(
        raw_row.get("merchant") or raw_row.get("merchant_raw"),
        field="merchant",
    )
    merchant = " ".join(merchant.split())

    amount = _parse_amount_value(raw_row.get("amount"))
    if amount == 0:
        raise BulkImportParseError("amount must be non-zero.")

    posted_date = raw_row.get("posted_date")
    if posted_date is not None:
        posted_date = str(posted_date).strip() or None
        if posted_date:
            _validate_iso_date(posted_date)

    card_last4 = raw_row.get("card_last4")
    if card_last4 is not None:
        card_last4 = str(card_last4).strip() or None
        if card_last4 and not re.fullmatch(r"\d{4}", card_last4):
            raise BulkImportParseError("card_last4 must be four digits.")

    row_bank = str(raw_row.get("bank") or default_bank).strip() or default_bank
    row_currency = (
        str(raw_row.get("currency") or default_currency).strip() or default_currency
    )

    explicit_category = raw_row.get("category")
    exclude_from_spending = bool(raw_row.get("exclude_from_spending", False))
    explicit_type = raw_row.get("transaction_type")
    explicit_needs_review = raw_row.get("needs_review")

    if explicit_category is not None and str(explicit_category).strip():
        category = str(explicit_category).strip()
        transaction_type = (
            _coerce_transaction_type(explicit_type)
            if explicit_type
            else transaction_type_for_category(category, exclude_from_spending)
        )
        needs_review = (
            bool(explicit_needs_review)
            if explicit_needs_review is not None
            else False
        )
    else:
        category_match = category_engine.categorize(merchant, amount)
        category = category_match.category
        exclude_from_spending = category_match.exclude_from_spending
        transaction_type = (
            _coerce_transaction_type(explicit_type)
            if explicit_type
            else category_match.transaction_type
        )
        needs_review = (
            bool(explicit_needs_review)
            if explicit_needs_review is not None
            else category_match.needs_review
        )

    return NormalizedTransaction(
        statement_file=statement_file,
        bank=row_bank,
        card_last4=card_last4,
        transaction_date=date_text,
        posted_date=posted_date,
        merchant_raw=merchant,
        amount=signed_amount(amount, transaction_type),
        currency=row_currency.upper(),
        transaction_type=transaction_type,
        category=category,
        exclude_from_spending=exclude_from_spending,
        needs_review=needs_review,
        statement_amount=amount,
    )


def _coerce_transaction_type(value: Any) -> TransactionType:
    normalized = str(value).strip()
    if normalized in {"purchase", "fee_or_interest", "payment_or_credit"}:
        return normalized  # type: ignore[return-value]
    raise BulkImportParseError(
        "transaction_type must be purchase, fee_or_interest, or payment_or_credit."
    )


def _require_string(value: Any, *, field: str) -> str:
    if value is None:
        raise BulkImportParseError(f"{field} is required.")
    text = str(value).strip()
    if not text:
        raise BulkImportParseError(f"{field} is required.")
    return text


def _validate_iso_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise BulkImportParseError(
            f"date must be YYYY-MM-DD, got {value!r}."
        ) from exc


def _parse_amount_value(value: Any) -> float:
    if value is None:
        raise BulkImportParseError("amount is required.")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        raise BulkImportParseError("amount is required.")
    try:
        return float(text)
    except ValueError as exc:
        raise BulkImportParseError(f"Invalid amount: {value!r}.") from exc


def _normalize_source_name(source_name: str) -> str:
    cleaned = " ".join(source_name.split())
    if not cleaned:
        return "import.json"
    if "." not in cleaned:
        return f"{cleaned}.json"
    return cleaned
