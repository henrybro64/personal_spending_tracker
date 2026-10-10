from __future__ import annotations

import sqlite3
from hashlib import sha256

from backend.database import (
    StatementImport,
    import_statements,
    source_keys_owned_by_upload,
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


class MergeSyncError(Exception):
    """Postgres saved the merge; the batch file was not marked synced."""

    def __init__(
        self,
        message: str,
        *,
        merged_count: int,
        skipped_count: int,
    ) -> None:
        super().__init__(message)
        self.merged_count = merged_count
        self.skipped_count = skipped_count


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
    statements: list[StatementImport] = []
    statement_plans: list[
        tuple[list[BatchStoredTransaction], dict[int, str]]
    ] = []

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
            if not transaction["postgres_synced"]
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
        statements.append(
            StatementImport(
                original_file_name=display_name,
                stored_file_name=file_name,
                file_fingerprint_sha256=fingerprint,
                parse_status="parsed",
                currency=statement_transactions[0]["currency"],
                transactions=upload_transactions,
                source_keys=eligible_keys,
                statement_scope=fingerprint,
            )
        )
        statement_plans.append((eligible_transactions, key_by_id))

    # One Postgres transaction for the whole run. A later statement failing
    # rolls back the earlier ones, so a retry does not see a partial import.
    results = import_statements(user_id=user_id, statements=statements)
    links: dict[int, str] = {}
    synced_ids: list[int] = []
    for (eligible_transactions, key_by_id), (upload_record, insert_result) in zip(
        statement_plans,
        results,
        strict=True,
    ):
        merged_count += insert_result.inserted_count
        skipped_count += len(eligible_transactions) - insert_result.inserted_count
        # Link rows this merge inserted, or (on retry) batch-owned rows already
        # on the upload. Ownership is by transaction file_name batch-import/
        # prefix, not the upload record name, so a shared PDF fingerprint still
        # restores links without tying batch deletes to PDF-owned rows (#76).
        batch_owned_keys = source_keys_owned_by_upload(
            user_id=user_id,
            statement_upload_id=upload_record["id"],
            source_keys=[
                key_by_id[transaction["id"]]
                for transaction in eligible_transactions
            ],
        )
        for transaction in eligible_transactions:
            source_key = key_by_id[transaction["id"]]
            if (
                source_key in insert_result.inserted_source_keys
                or source_key in batch_owned_keys
            ):
                links[transaction["id"]] = source_key
        synced_ids.extend(
            transaction["id"] for transaction in eligible_transactions
        )

    # SQLite is updated only after Postgres commits. If this write fails, the
    # next merge finds the rows already stored and marks them then.
    try:
        store.set_postgres_source_keys(links)
        store.mark_transactions_synced(synced_ids)
    except (OSError, sqlite3.Error) as exc:
        raise MergeSyncError(
            "Saved the merge, but could not mark the batch rows synced. "
            "Retry the merge.",
            merged_count=merged_count,
            skipped_count=skipped_count,
        ) from exc

    return {
        "run_id": effective_run_id,
        "merged_count": merged_count,
        "skipped_count": skipped_count,
    }
