import os
from datetime import date, datetime, timedelta
from typing import Literal, TypedDict

import psycopg
from backend.parser import Transaction, categorize_merchant


class TransactionsSummary(TypedDict):
    total_spending: float
    number_of_transactions: int
    average_transaction_amount: float
    number_of_statements: int


class RecentUpload(TypedDict):
    file_name: str
    display_name: str
    transaction_count: int
    total_amount: float
    uploaded_at: str


class ExistingUpload(TypedDict):
    file_name: str
    display_name: str
    uploaded_at: str


class StoredTransaction(TypedDict):
    date: str
    merchant: str
    amount: float
    category: str
    currency: str
    payment_method: str | None
    file_name: str
    original_file_name: str


TimeRange = Literal["week", "month", "quarter", "year", "5years", "all"]


def _normalize_category_for_storage(
    category: str | None, amount: float
) -> str:
    raw = (category or "").strip()
    if raw and raw.lower() != "miscellaneous":
        return raw
    return "Refunds & Credits" if amount < 0 else "Other Spending"


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")

    return database_url


def init_db(database_url: str | None = None) -> str:
    resolved_database_url = database_url or get_database_url()

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id BIGSERIAL PRIMARY KEY,
                    date TEXT NOT NULL,
                    merchant TEXT NOT NULL,
                    amount NUMERIC(12, 2) NOT NULL,
                    file_name TEXT NOT NULL,
                    original_file_name TEXT
                )
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ALTER COLUMN date TYPE TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ
                NOT NULL DEFAULT NOW()
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS original_file_name TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS category TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS file_fingerprint TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS payment_method TEXT
                """
            )
            cursor.execute(
                """
                ALTER TABLE transactions
                ADD COLUMN IF NOT EXISTS currency TEXT
                NOT NULL DEFAULT 'USD'
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_transactions_file_fingerprint
                ON transactions (file_fingerprint)
                """
            )
        connection.commit()

    return resolved_database_url


def insert_transactions(
    transactions: list[Transaction],
    file_name: str,
    file_fingerprint: str | None = None,
    original_file_name: str | None = None,
    database_url: str | None = None,
) -> int:
    if not transactions:
        return 0

    resolved_database_url = init_db(database_url)
    display_name = original_file_name or file_name

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO transactions (
                    date,
                    merchant,
                    amount,
                    file_name,
                    original_file_name,
                    category,
                    file_fingerprint,
                    payment_method,
                    currency
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        transaction["date"],
                        transaction["merchant"],
                        transaction["amount"],
                        file_name,
                        display_name,
                        _normalize_category_for_storage(
                            transaction.get("category"), transaction["amount"]
                        ),
                        file_fingerprint,
                        None,
                        transaction.get("currency", "USD"),
                    )
                    for transaction in transactions
                ],
            )
        connection.commit()

    return len(transactions)


def insert_manual_transaction(
    transaction_date: str,
    merchant: str,
    amount: float,
    category: str | None = None,
    payment_method: str | None = None,
    database_url: str | None = None,
) -> StoredTransaction:
    resolved_database_url = init_db(database_url)
    normalized_merchant = " ".join(merchant.split())
    inferred_category = (
        category.strip()
        if category and category.strip()
        else categorize_merchant(normalized_merchant, amount)
    )
    normalized_category = _normalize_category_for_storage(
        inferred_category, amount
    )
    normalized_payment_method = (
        payment_method.strip()
        if payment_method and payment_method.strip()
        else None
    )
    file_name = "manual-entry"
    original_file_name = (
        f"Manual entry ({normalized_payment_method})"
        if normalized_payment_method
        else "Manual entry"
    )

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO transactions (
                    date,
                    merchant,
                    amount,
                    file_name,
                    original_file_name,
                    category,
                    file_fingerprint,
                    payment_method,
                    currency
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    transaction_date,
                    normalized_merchant,
                    amount,
                    file_name,
                    original_file_name,
                    normalized_category,
                    None,
                    normalized_payment_method,
                    "USD",
                ),
            )
        connection.commit()

    return {
        "date": transaction_date,
        "merchant": normalized_merchant,
        "amount": amount,
        "category": normalized_category,
        "currency": "USD",
        "payment_method": normalized_payment_method,
        "file_name": file_name,
        "original_file_name": original_file_name,
    }


def _since_date_for_range(range_key: TimeRange) -> date | None:
    today = date.today()
    if range_key == "week":
        return today - timedelta(days=7)
    if range_key == "month":
        return today - timedelta(days=30)
    if range_key == "quarter":
        return today - timedelta(days=90)
    if range_key == "year":
        return today - timedelta(days=365)
    if range_key == "5years":
        return today - timedelta(days=365 * 5)
    return None


def _build_date_filter_clause(
    range_key: TimeRange,
) -> tuple[str, tuple[str, ...]]:
    since_date = _since_date_for_range(range_key)
    if since_date is None:
        return "", ()
    return (
        "WHERE date ~ '^\\d{4}-\\d{2}-\\d{2}$' AND date::date >= %s",
        (since_date.isoformat(),),
    )


def get_transactions_summary(
    range_key: TimeRange = "all",
    database_url: str | None = None,
) -> TransactionsSummary:
    resolved_database_url = init_db(database_url)
    date_filter_clause, query_params = _build_date_filter_clause(range_key)
    summary_query = f"""
                SELECT
                    COALESCE(SUM(amount), 0),
                    COUNT(*),
                    COALESCE(AVG(amount), 0),
                    COUNT(DISTINCT file_name)
                FROM transactions
                {date_filter_clause}
                """

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(summary_query, query_params)
            (
                total_spending,
                transaction_count,
                average_amount,
                statement_count,
            ) = cursor.fetchone()

    return {
        "total_spending": float(total_spending),
        "number_of_transactions": int(transaction_count),
        "average_transaction_amount": float(average_amount),
        "number_of_statements": int(statement_count),
    }


def get_recent_uploads(
    limit: int = 5, database_url: str | None = None
) -> list[RecentUpload]:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    file_name,
                    COALESCE(MAX(original_file_name), file_name)
                    AS display_name,
                    COUNT(*) AS transaction_count,
                    COALESCE(SUM(amount), 0) AS total_amount,
                    MAX(uploaded_at) AS uploaded_at
                FROM transactions
                GROUP BY file_name
                ORDER BY MAX(uploaded_at) DESC, MAX(id) DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()

    recent_uploads: list[RecentUpload] = []

    for (
        file_name,
        display_name,
        transaction_count,
        total_amount,
        uploaded_at,
    ) in rows:
        uploaded_at_value = (
            uploaded_at.isoformat()
            if isinstance(uploaded_at, datetime)
            else str(uploaded_at)
        )
        recent_uploads.append(
            {
                "file_name": str(file_name),
                "display_name": str(display_name),
                "transaction_count": int(transaction_count),
                "total_amount": float(total_amount),
                "uploaded_at": uploaded_at_value,
            }
        )

    return recent_uploads


def get_transactions(
    range_key: TimeRange = "all",
    limit: int = 5000,
    database_url: str | None = None,
) -> list[StoredTransaction]:
    resolved_database_url = init_db(database_url)
    date_filter_clause, range_params = _build_date_filter_clause(range_key)
    transaction_query = f"""
                SELECT
                    date,
                    merchant,
                    amount,
                    COALESCE(category, '') AS category,
                    COALESCE(currency, 'USD') AS currency,
                    payment_method,
                    file_name,
                    COALESCE(
                        original_file_name, file_name
                    ) AS original_file_name
                FROM transactions
                {date_filter_clause}
                ORDER BY
                    CASE
                        WHEN date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                            THEN date::date
                        ELSE NULL
                    END DESC,
                    id DESC
                LIMIT %s
                """
    query_params: tuple[str | int, ...] = (*range_params, limit)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(transaction_query, query_params)
            rows = cursor.fetchall()

    return [
        {
            "date": str(row[0]),
            "merchant": str(row[1]),
            "amount": float(row[2]),
            "category": _normalize_category_for_storage(
                str(row[3]), float(row[2])
            ),
            "currency": str(row[4]),
            "payment_method": str(row[5]) if row[5] is not None else None,
            "file_name": str(row[6]),
            "original_file_name": str(row[7]),
        }
        for row in rows
    ]


def get_existing_upload_by_fingerprint(
    file_fingerprints: list[str], database_url: str | None = None
) -> ExistingUpload | None:
    if not file_fingerprints:
        return None

    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    file_name,
                    COALESCE(MAX(original_file_name), file_name)
                    AS display_name,
                    MAX(uploaded_at) AS uploaded_at
                FROM transactions
                WHERE file_fingerprint = ANY(%s)
                GROUP BY file_name
                ORDER BY MAX(uploaded_at) DESC, MAX(id) DESC
                LIMIT 1
                """,
                (file_fingerprints,),
            )
            row = cursor.fetchone()

    if not row:
        return None

    file_name, display_name, uploaded_at = row
    uploaded_at_value = (
        uploaded_at.isoformat()
        if isinstance(uploaded_at, datetime)
        else str(uploaded_at)
    )
    return {
        "file_name": str(file_name),
        "display_name": str(display_name),
        "uploaded_at": uploaded_at_value,
    }
