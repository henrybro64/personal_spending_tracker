from __future__ import annotations

from hashlib import sha256

from backend.database import (
    DuplicateStatementUploadError,
    create_statement_upload,
    get_existing_upload_by_fingerprint,
    insert_transactions,
)
from backend.parser import Transaction

from .storage import BatchImportStore, BatchStoredTransaction


def _batch_statement_fingerprint(
    statement_transactions: list[BatchStoredTransaction],
) -> str:
    """Identity of one statement's contents across later imports.

    Prefer the SHA-256 of the raw statement text stored at import time. Fall
    back to a payload hash for legacy SQLite rows that predate that column.
    """
    stored = (statement_transactions[0].get("statement_content_sha256") or "").strip()
    if stored:
        return stored

    lines: list[str] = []
    for transaction in sorted(
        statement_transactions,
        key=lambda item: (
            item["transaction_date"],
            item["merchant_raw"],
            float(item["amount"]),
            item["id"],
        ),
    ):
        lines.append(
            "|".join(
                (
                    transaction["transaction_date"],
                    transaction["merchant_raw"],
                    f"{float(transaction['amount']):.2f}",
                    transaction["currency"],
                    transaction["card_last4"] or "",
                )
            )
        )
    return sha256("\n".join(lines).encode("utf-8")).hexdigest()


def batch_transaction_to_upload(transaction: BatchStoredTransaction) -> Transaction:
    return {
        "date": transaction["transaction_date"],
        "merchant": transaction["merchant_raw"],
        "amount": transaction["amount"],
        "category": transaction["category"],
        "currency": transaction["currency"],
        "card_last4": transaction["card_last4"] or "",
    }


def merge_run_to_postgres(
    store: BatchImportStore,
    *,
    user_id: int,
    run_id: int | None = None,
    include_needs_review: bool = True,
    include_excluded: bool = False,
) -> dict[str, int | None]:
    effective_run_id = run_id or store._latest_run_id()
    if effective_run_id is None:
        return {
            "run_id": None,
            "merged_count": 0,
            "skipped_count": 0,
        }

    transactions = store.list_transactions_for_merge(
        run_id=effective_run_id,
        include_needs_review=include_needs_review,
        include_excluded=include_excluded,
    )

    eligible_transactions = [
        transaction
        for transaction in transactions
        if not store.is_transaction_synced(transaction["id"])
    ]
    skipped_count = len(transactions) - len(eligible_transactions)

    transactions_by_statement: dict[str, list[BatchStoredTransaction]] = {}
    for transaction in eligible_transactions:
        transactions_by_statement.setdefault(
            transaction["statement_file"], []
        ).append(transaction)

    merged_count = 0
    synced_ids: list[int] = []

    for statement_file, statement_transactions in transactions_by_statement.items():
        upload_transactions = [
            batch_transaction_to_upload(transaction)
            for transaction in statement_transactions
        ]
        file_name = f"batch-import/{statement_file}"
        display_name = f"Batch import: {statement_file}"
        fingerprint = _batch_statement_fingerprint(statement_transactions)
        try:
            upload_record = create_statement_upload(
                user_id=user_id,
                original_file_name=display_name,
                stored_file_name=file_name,
                file_fingerprint_sha256=fingerprint,
                parse_status="parsed",
                currency=statement_transactions[0]["currency"],
            )
            statement_upload_id = upload_record["id"]
        except DuplicateStatementUploadError:
            existing_upload = get_existing_upload_by_fingerprint(
                user_id=user_id,
                file_fingerprint_sha256=fingerprint,
            )
            if existing_upload is None:
                raise
            statement_upload_id = existing_upload["id"]
        inserted = insert_transactions(
            upload_transactions,
            user_id=user_id,
            file_name=file_name,
            original_file_name=display_name,
            file_fingerprint=fingerprint,
            statement_upload_id=statement_upload_id,
        )
        merged_count += inserted
        skipped_count += len(statement_transactions) - inserted
        synced_ids.extend(
            transaction["id"] for transaction in statement_transactions
        )

    store.mark_transactions_synced(synced_ids)

    return {
        "run_id": effective_run_id,
        "merged_count": merged_count,
        "skipped_count": skipped_count,
    }
