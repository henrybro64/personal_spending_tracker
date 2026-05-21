from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from backend.merchant_noise import merchant_raw_is_parse_noise

from .category import (
    CategoryEngine,
    CategoryRule,
    normalize_merchant_key,
    transaction_type_for_category,
)
from .models import NormalizedTransaction
from .paths import default_sqlite_output_path


DEFAULT_BATCH_DATABASE_PATH = default_sqlite_output_path()


class BatchStoredTransaction(TypedDict):
    id: int
    import_run_id: int
    statement_file: str
    bank: str
    card_last4: str | None
    transaction_date: str
    posted_date: str | None
    merchant_raw: str
    amount: float
    currency: str
    transaction_type: str
    category: str
    exclude_from_spending: bool
    needs_review: bool


class BatchImportRunSummary(TypedDict):
    id: int
    imported_at: str
    statement_count: int
    transaction_count: int
    needs_review_count: int
    csv_output_path: str
    sqlite_output_path: str
    pdf_source_dir: str
    text_source_dir: str


class NeedsReviewMerchantSummary(TypedDict):
    merchant_raw: str
    transaction_count: int
    sample_category: str
    sample_excluded: bool
    suggested_category: str
    can_auto_apply: bool


class BatchImportStore:
    def __init__(self, database_path: str | Path = DEFAULT_BATCH_DATABASE_PATH):
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS import_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    imported_at TEXT NOT NULL,
                    pdf_source_dir TEXT NOT NULL,
                    text_source_dir TEXT NOT NULL,
                    csv_output_path TEXT NOT NULL,
                    sqlite_output_path TEXT NOT NULL,
                    statement_count INTEGER NOT NULL,
                    transaction_count INTEGER NOT NULL,
                    needs_review_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS imported_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_run_id INTEGER NOT NULL REFERENCES import_runs(id),
                    statement_file TEXT NOT NULL,
                    bank TEXT NOT NULL,
                    card_last4 TEXT,
                    transaction_date TEXT NOT NULL,
                    posted_date TEXT,
                    merchant_raw TEXT NOT NULL,
                    merchant_key TEXT NOT NULL,
                    amount REAL NOT NULL,
                    currency TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    exclude_from_spending INTEGER NOT NULL,
                    needs_review INTEGER NOT NULL,
                    postgres_synced INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS merchant_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    merchant_pattern TEXT NOT NULL,
                    category TEXT NOT NULL,
                    exclude_from_spending INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_imported_transactions_run_id
                ON imported_transactions (import_run_id, needs_review);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_merchant_rules_unique
                ON merchant_rules (
                    merchant_pattern,
                    category,
                    exclude_from_spending,
                    priority
                );
                """
            )
            self._ensure_postgres_synced_column(connection)

    def _ensure_postgres_synced_column(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(imported_transactions)")
        }
        if "postgres_synced" not in columns:
            connection.execute(
                """
                ALTER TABLE imported_transactions
                ADD COLUMN postgres_synced INTEGER NOT NULL DEFAULT 0
                """
            )

    def load_learned_rules(self) -> list[CategoryRule]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    merchant_pattern,
                    category,
                    exclude_from_spending,
                    priority
                FROM merchant_rules
                ORDER BY priority ASC, id ASC
                """
            ).fetchall()

        return [
            CategoryRule(
                patterns=(str(row["merchant_pattern"]),),
                category=str(row["category"]),
                exclude_from_spending=bool(row["exclude_from_spending"]),
                transaction_type=transaction_type_for_category(
                    str(row["category"]),
                    bool(row["exclude_from_spending"]),
                ),
                priority=int(row["priority"]),
            )
            for row in rows
        ]

    def record_import_run(
        self,
        transactions: list[NormalizedTransaction],
        *,
        pdf_source_dir: str,
        text_source_dir: str,
        csv_output_path: str,
        statement_count: int,
    ) -> tuple[int, str, int]:
        imported_at = datetime.now(timezone.utc).isoformat()
        needs_review_count = sum(
            1 for transaction in transactions if transaction.needs_review
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_runs (
                    imported_at,
                    pdf_source_dir,
                    text_source_dir,
                    csv_output_path,
                    sqlite_output_path,
                    statement_count,
                    transaction_count,
                    needs_review_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    imported_at,
                    pdf_source_dir,
                    text_source_dir,
                    csv_output_path,
                    str(self.database_path),
                    statement_count,
                    len(transactions),
                    needs_review_count,
                ),
            )
            run_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO imported_transactions (
                    import_run_id,
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    merchant_key,
                    amount,
                    currency,
                    transaction_type,
                    category,
                    exclude_from_spending,
                    needs_review
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        transaction.statement_file,
                        transaction.bank,
                        transaction.card_last4,
                        transaction.transaction_date,
                        transaction.posted_date,
                        transaction.merchant_raw,
                        normalize_merchant_key(transaction.merchant_raw),
                        transaction.amount,
                        transaction.currency,
                        transaction.transaction_type,
                        transaction.category,
                        int(transaction.exclude_from_spending),
                        int(transaction.needs_review),
                    )
                    for transaction in transactions
                ],
            )

        return run_id, imported_at, needs_review_count

    def export_csv(self, run_id: int, csv_path: str | Path) -> str:
        resolved_path = Path(csv_path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    amount,
                    currency,
                    transaction_type,
                    category,
                    exclude_from_spending,
                    needs_review
                FROM imported_transactions
                WHERE import_run_id = ?
                ORDER BY statement_file, transaction_date, merchant_raw, id
                """,
                (run_id,),
            ).fetchall()

        with resolved_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "statement_file",
                    "bank",
                    "card_last4",
                    "transaction_date",
                    "posted_date",
                    "merchant_raw",
                    "amount",
                    "currency",
                    "transaction_type",
                    "category",
                    "exclude_from_spending",
                    "needs_review",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {
                        "statement_file": row["statement_file"],
                        "bank": row["bank"],
                        "card_last4": row["card_last4"],
                        "transaction_date": row["transaction_date"],
                        "posted_date": row["posted_date"],
                        "merchant_raw": row["merchant_raw"],
                        "amount": float(row["amount"]),
                        "currency": row["currency"],
                        "transaction_type": row["transaction_type"],
                        "category": row["category"],
                        "exclude_from_spending": bool(
                            row["exclude_from_spending"]
                        ),
                        "needs_review": bool(row["needs_review"]),
                    }
                )

        return str(resolved_path)

    def get_latest_run(self) -> BatchImportRunSummary | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    imported_at,
                    statement_count,
                    transaction_count,
                    needs_review_count,
                    csv_output_path,
                    sqlite_output_path,
                    pdf_source_dir,
                    text_source_dir
                FROM import_runs
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()

        if row is None:
            return None

        return {
            "id": int(row["id"]),
            "imported_at": str(row["imported_at"]),
            "statement_count": int(row["statement_count"]),
            "transaction_count": int(row["transaction_count"]),
            "needs_review_count": int(row["needs_review_count"]),
            "csv_output_path": str(row["csv_output_path"]),
            "sqlite_output_path": str(row["sqlite_output_path"]),
            "pdf_source_dir": str(row["pdf_source_dir"]),
            "text_source_dir": str(row["text_source_dir"]),
        }

    def list_transactions(
        self,
        *,
        needs_review_only: bool = False,
        run_id: int | None = None,
        limit: int = 500,
    ) -> list[BatchStoredTransaction]:
        effective_run_id = run_id or self._latest_run_id()
        if effective_run_id is None:
            return []

        review_clause = "AND needs_review = 1" if needs_review_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    import_run_id,
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    amount,
                    currency,
                    transaction_type,
                    category,
                    exclude_from_spending,
                    needs_review
                FROM imported_transactions
                WHERE import_run_id = ?
                {review_clause}
                ORDER BY needs_review DESC, transaction_date DESC, id DESC
                LIMIT ?
                """,
                (effective_run_id, limit),
            ).fetchall()

        return [self._row_to_transaction(row) for row in rows]

    def list_transactions_for_merge(
        self,
        *,
        run_id: int,
        include_needs_review: bool = True,
        include_excluded: bool = False,
    ) -> list[BatchStoredTransaction]:
        filters = ["import_run_id = ?"]
        params: list[object] = [run_id]

        if not include_needs_review:
            filters.append("needs_review = 0")
        if not include_excluded:
            filters.append("exclude_from_spending = 0")

        where_clause = " AND ".join(filters)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    import_run_id,
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    amount,
                    currency,
                    transaction_type,
                    category,
                    exclude_from_spending,
                    needs_review
                FROM imported_transactions
                WHERE {where_clause}
                ORDER BY statement_file, transaction_date, id
                """,
                params,
            ).fetchall()

        return [self._row_to_transaction(row) for row in rows]

    def list_needs_review_merchants(
        self,
        *,
        run_id: int | None = None,
        limit: int = 20,
    ) -> list[NeedsReviewMerchantSummary]:
        effective_run_id = run_id or self._latest_run_id()
        if effective_run_id is None:
            return []

        # No SQL LIMIT: digit-only CTBC noise can be hundreds of distinct
        # merchant_raw values; capping the query before Python filtering
        # would drop real merchants from the bulk-review panel.
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    merchant_raw,
                    COUNT(*) AS transaction_count,
                    MIN(category) AS sample_category,
                    MIN(exclude_from_spending) AS sample_excluded
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND needs_review = 1
                GROUP BY merchant_raw
                ORDER BY COUNT(*) DESC, merchant_raw ASC
                """,
                (effective_run_id,),
            ).fetchall()

        summaries: list[NeedsReviewMerchantSummary] = []
        for row in rows:
            raw = str(row["merchant_raw"])
            if merchant_raw_is_parse_noise(raw):
                continue
            summaries.append(
                {
                    "merchant_raw": raw,
                    "transaction_count": int(row["transaction_count"]),
                    "sample_category": str(row["sample_category"]),
                    "sample_excluded": bool(row["sample_excluded"]),
                }
            )
            if len(summaries) >= limit:
                break

        return summaries

    def bulk_update_merchant_review(
        self,
        *,
        run_id: int,
        merchant_raw: str,
        category: str,
        exclude_from_spending: bool,
        save_rule: bool,
    ) -> int:
        transaction_type = transaction_type_for_category(
            category,
            exclude_from_spending,
        )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, amount
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND merchant_raw = ?
                  AND needs_review = 1
                """,
                (run_id, merchant_raw),
            ).fetchall()
            if not rows:
                return 0

            updates: list[tuple[float, str, int, int, str, int]] = []
            for row in rows:
                normalized_amount = (
                    -abs(float(row["amount"]))
                    if transaction_type == "payment_or_credit"
                    else abs(float(row["amount"]))
                )
                updates.append(
                    (
                        normalized_amount,
                        category,
                        int(exclude_from_spending),
                        0,
                        transaction_type,
                        int(row["id"]),
                    )
                )

            connection.executemany(
                """
                UPDATE imported_transactions
                SET
                    amount = ?,
                    category = ?,
                    exclude_from_spending = ?,
                    needs_review = ?,
                    transaction_type = ?
                WHERE id = ?
                """,
                updates,
            )

            if save_rule:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO merchant_rules (
                        merchant_pattern,
                        category,
                        exclude_from_spending,
                        priority,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        normalize_merchant_key(merchant_raw),
                        category,
                        int(exclude_from_spending),
                        10,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

        return len(rows)

    def count_digit_only_noise_pending(self, *, run_id: int | None = None) -> int:
        effective_run_id = run_id or self._latest_run_id()
        if effective_run_id is None:
            return 0

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT merchant_raw
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND needs_review = 1
                """,
                (effective_run_id,),
            ).fetchall()

        return sum(
            1
            for row in rows
            if merchant_raw_is_parse_noise(str(row["merchant_raw"]))
        )

    def bulk_dismiss_digit_only_noise(self, *, run_id: int) -> int:
        """Exclude and clear review on rows with junk parsed merchant names."""
        junk_category = "Other Spending"

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, amount, merchant_raw
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND needs_review = 1
                """,
                (run_id,),
            ).fetchall()

            updates: list[tuple[float, str, int, int, str, int]] = []
            for row in rows:
                if not merchant_raw_is_parse_noise(str(row["merchant_raw"])):
                    continue
                amount = abs(float(row["amount"]))
                updates.append(
                    (
                        amount,
                        junk_category,
                        1,
                        0,
                        "purchase",
                        int(row["id"]),
                    )
                )

            if not updates:
                return 0

            connection.executemany(
                """
                UPDATE imported_transactions
                SET
                    amount = ?,
                    category = ?,
                    exclude_from_spending = ?,
                    needs_review = ?,
                    transaction_type = ?
                WHERE id = ?
                """,
                updates,
            )

        return len(updates)

    def count_needs_review(self, *, run_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS pending_count
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND needs_review = 1
                """,
                (run_id,),
            ).fetchone()
        return int(row["pending_count"]) if row else 0

    def auto_apply_default_categories(
        self,
        *,
        run_id: int,
        category_engine: CategoryEngine,
    ) -> int:
        """Apply built-in category rules to rows still flagged for review."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, merchant_raw, amount
                FROM imported_transactions
                WHERE import_run_id = ?
                  AND needs_review = 1
                """,
                (run_id,),
            ).fetchall()

            updates: list[tuple[float, str, int, int, str, int]] = []
            for row in rows:
                merchant_raw = str(row["merchant_raw"])
                if merchant_raw_is_parse_noise(merchant_raw):
                    continue

                amount = float(row["amount"])
                category_match = category_engine.categorize(merchant_raw, amount)
                if category_match.needs_review:
                    continue

                transaction_type = transaction_type_for_category(
                    category_match.category,
                    category_match.exclude_from_spending,
                )
                normalized_amount = (
                    -abs(amount)
                    if transaction_type == "payment_or_credit"
                    else abs(amount)
                )
                updates.append(
                    (
                        normalized_amount,
                        category_match.category,
                        int(category_match.exclude_from_spending),
                        0,
                        transaction_type,
                        int(row["id"]),
                    )
                )

            if not updates:
                return 0

            connection.executemany(
                """
                UPDATE imported_transactions
                SET
                    amount = ?,
                    category = ?,
                    exclude_from_spending = ?,
                    needs_review = ?,
                    transaction_type = ?
                WHERE id = ?
                """,
                updates,
            )

        with self._connect() as connection:
            pending = self.count_needs_review(run_id=run_id)
            connection.execute(
                """
                UPDATE import_runs
                SET needs_review_count = ?
                WHERE id = ?
                """,
                (pending, run_id),
            )

        return len(updates)

    def is_transaction_synced(self, transaction_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT postgres_synced
                FROM imported_transactions
                WHERE id = ?
                """,
                (transaction_id,),
            ).fetchone()
        if row is None:
            return False
        return bool(row["postgres_synced"])

    def mark_transactions_synced(self, transaction_ids: list[int]) -> None:
        if not transaction_ids:
            return

        placeholders = ", ".join("?" for _ in transaction_ids)
        with self._connect() as connection:
            connection.execute(
                f"""
                UPDATE imported_transactions
                SET postgres_synced = 1
                WHERE id IN ({placeholders})
                """,
                transaction_ids,
            )

    def update_transaction_review(
        self,
        transaction_id: int,
        *,
        category: str,
        exclude_from_spending: bool,
        save_rule: bool,
    ) -> BatchStoredTransaction:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    id,
                    import_run_id,
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    amount,
                    currency
                FROM imported_transactions
                WHERE id = ?
                """,
                (transaction_id,),
            ).fetchone()

            if row is None:
                raise KeyError(transaction_id)

            transaction_type = transaction_type_for_category(
                category,
                exclude_from_spending,
            )
            normalized_amount = (
                -abs(float(row["amount"]))
                if transaction_type == "payment_or_credit"
                else abs(float(row["amount"]))
            )
            needs_review = False

            connection.execute(
                """
                UPDATE imported_transactions
                SET
                    amount = ?,
                    category = ?,
                    exclude_from_spending = ?,
                    needs_review = ?,
                    transaction_type = ?
                WHERE id = ?
                """,
                (
                    normalized_amount,
                    category,
                    int(exclude_from_spending),
                    int(needs_review),
                    transaction_type,
                    transaction_id,
                ),
            )

            if save_rule:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO merchant_rules (
                        merchant_pattern,
                        category,
                        exclude_from_spending,
                        priority,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        normalize_merchant_key(str(row["merchant_raw"])),
                        category,
                        int(exclude_from_spending),
                        10,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

            updated = connection.execute(
                """
                SELECT
                    id,
                    import_run_id,
                    statement_file,
                    bank,
                    card_last4,
                    transaction_date,
                    posted_date,
                    merchant_raw,
                    amount,
                    currency,
                    transaction_type,
                    category,
                    exclude_from_spending,
                    needs_review
                FROM imported_transactions
                WHERE id = ?
                """,
                (transaction_id,),
            ).fetchone()

        return self._row_to_transaction(updated)

    def _latest_run_id(self) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM import_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _row_to_transaction(
        self, row: sqlite3.Row
    ) -> BatchStoredTransaction:
        return {
            "id": int(row["id"]),
            "import_run_id": int(row["import_run_id"]),
            "statement_file": str(row["statement_file"]),
            "bank": str(row["bank"]),
            "card_last4": (
                str(row["card_last4"]) if row["card_last4"] is not None else None
            ),
            "transaction_date": str(row["transaction_date"]),
            "posted_date": (
                str(row["posted_date"]) if row["posted_date"] is not None else None
            ),
            "merchant_raw": str(row["merchant_raw"]),
            "amount": float(row["amount"]),
            "currency": str(row["currency"]),
            "transaction_type": str(row["transaction_type"]),
            "category": str(row["category"]),
            "exclude_from_spending": bool(row["exclude_from_spending"]),
            "needs_review": bool(row["needs_review"]),
        }
