from __future__ import annotations

from hashlib import sha256

from backend.database import create_statement_upload, insert_transactions
from backend.parser import Transaction

from .storage import BatchImportStore, BatchStoredTransaction


def _batch_fingerprint(transaction: BatchStoredTransaction) -> str:
    payload = (
        f"{transaction['import_run_id']}:"
        f"{transaction['statement_file']}:"
        f"{transaction['transaction_date']}:"
        f"{transaction['merchant_raw']}:"
        f"{transaction['amount']}:"
        f"{transaction['currency']}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def batch_transaction_to_upload(transaction: BatchStoredTransaction) -> Transaction:
    return {
        "date": transaction["transaction_date"],
        "merchant": transaction["merchant_raw"],
        "amount": transaction["amount"],
        "category": transaction["category"],
        "currency": transaction["currency"],
    }


def merge_run_to_postgres(
    store: BatchImportStore,
    *,
    user_id: int,
    run_id: int | None = None,
    include_needs_review: bool = False,
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
        fingerprint = _batch_fingerprint(statement_transactions[0])
        upload_record = create_statement_upload(
            user_id=user_id,
            original_file_name=display_name,
            stored_file_name=file_name,
            file_fingerprint_sha256=fingerprint,
            parse_status="parsed",
            currency=statement_transactions[0]["currency"],
        )
        insert_transactions(
            upload_transactions,
            user_id=user_id,
            file_name=file_name,
            original_file_name=display_name,
            file_fingerprint=fingerprint,
            statement_upload_id=upload_record["id"],
        )
        merged_count += len(statement_transactions)
        synced_ids.extend(transaction["id"] for transaction in statement_transactions)

    store.mark_transactions_synced(synced_ids)

    return {
        "run_id": effective_run_id,
        "merged_count": merged_count,
        "skipped_count": skipped_count,
    }
