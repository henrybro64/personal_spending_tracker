from dataclasses import asdict, dataclass
from typing import Any, Literal


TransactionType = Literal[
    "purchase",
    "fee_or_interest",
    "payment_or_credit",
]


@dataclass(slots=True)
class NormalizedTransaction:
    statement_file: str
    bank: str
    card_last4: str | None
    transaction_date: str
    posted_date: str | None
    merchant_raw: str
    amount: float
    currency: str
    transaction_type: TransactionType
    category: str
    exclude_from_spending: bool
    needs_review: bool
    statement_content_sha256: str = ""

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BatchImportResult:
    run_id: int
    imported_at: str
    statement_count: int
    transaction_count: int
    needs_review_count: int
    csv_output_path: str
    sqlite_output_path: str
    parsed_files: list[str]
    skipped_files: list[dict[str, str]]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)
