from __future__ import annotations

from hashlib import sha256

from backend.database import (
    DuplicateStatementUploadError,
    create_statement_upload,
    get_existing_upload_by_fingerprint,
    insert_transactions,
)
from backend.parser import Transaction
from backend.transaction_source import source_keys_for_transactions

from .storage import BatchImportStore, BatchStoredTransaction


def _batch_statement_fingerprint(
    statement_transactions: list[BatchStoredTransaction],
) -> str:
    """Identity of one statement's contents across later imports.

    Prefer the SHA-256 of the statement text stored at import time. Fall back
    to a payload hash for legacy SQLite rows that predate that column.
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


def _passes_merge_filters(
    transaction: BatchStoredTransaction,
    *,
    include_needs_review: bool,
    include_excluded: bool,
) -> bool:
    if not include_needs_review and transaction["needs_review"]:
        return False
    if not include_excluded and transaction["exclude_from_spending"]:
        return False
    return True


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

    # Load every row in the run so occurrence numbers are assigned against the
    # full statement before include_needs_review / synced filters remove rows.
    all_transactions = store.list_transactions_for_merge(
        run_id=effective_run_id,
        include_needs_review=True,
        include_excluded=True,
    )

    transactions_by_statement: dict[str, list[BatchStoredTransaction]] = {}
    for transaction in all_transactions:
        transactions_by_statement.setdefault(
            transaction["statement_file"], []
        ).append(transaction)

    merged_count = 0
    skipped_count = 0
    synced_ids: list[int] = []
    source_keys_by_id: dict[int, str] = {}

    for statement_file, statement_transactions in transactions_by_statement.items():
        fingerprint = _batch_statement_fingerprint(statement_transactions)
        all_upload_rows = [
            batch_transaction_to_upload(transaction)
            for transaction in statement_transactions
        ]
        statement_keys = source_keys_for_transactions(
            all_upload_rows,
            statement_scope=fingerprint,
        )
        key_by_id = {
            statement_transactions[index]["id"]: statement_keys[index]
            for index in range(len(statement_transactions))
        }

        filtered_transactions = [
            transaction
            for transaction in statement_transactions
            if _passes_merge_filters(
                transaction,
                include_needs_review=include_needs_review,
                include_excluded=include_excluded,
            )
        ]
        eligible_transactions = [
            transaction
            for transaction in filtered_transactions
            if not store.is_transaction_synced(transaction["id"])
        ]
        skipped_count += len(filtered_transactions) - len(eligible_transactions)
        if not eligible_transactions:
            continue

        upload_transactions = [
            batch_transaction_to_upload(transaction)
            for transaction in eligible_transactions
        ]
        eligible_keys = [
            key_by_id[transaction["id"]] for transaction in eligible_transactions
        ]
        file_name = f"batch-import/{statement_file}"
        display_name = f"Batch import: {statement_file}"
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
        insert_result = insert_transactions(
            upload_transactions,
            user_id=user_id,
            file_name=file_name,
            original_file_name=display_name,
            file_fingerprint=fingerprint,
            statement_upload_id=statement_upload_id,
            source_keys=eligible_keys,
            statement_scope=fingerprint,
        )
        merged_count += insert_result.inserted_count
        skipped_count += len(eligible_transactions) - insert_result.inserted_count
        for transaction in eligible_transactions:
            synced_ids.append(transaction["id"])
            # Only link rows this merge actually inserted. Deduped / claimed
            # rows already exist on the dashboard and must not be deleted when
            # the user removes this batch copy (#76).
            key = key_by_id[transaction["id"]]
            if key in insert_result.inserted_source_keys:
                source_keys_by_id[transaction["id"]] = key

    store.set_postgres_source_keys(source_keys_by_id)
    store.mark_transactions_synced(synced_ids)

    return {
        "run_id": effective_run_id,
        "merged_count": merged_count,
        "skipped_count": skipped_count,
    }
