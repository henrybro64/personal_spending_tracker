import os
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal, TypedDict

import psycopg
from psycopg.types.json import Json

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
    parse_status: str


class ExistingUpload(TypedDict):
    id: int
    file_name: str
    display_name: str
    uploaded_at: str
    parse_status: str


class StoredTransaction(TypedDict):
    id: int
    date: str
    merchant: str
    amount: float
    category: str
    currency: str
    payment_method: str | None
    file_name: str
    original_file_name: str


class UserRecord(TypedDict):
    id: int
    email: str
    display_name: str
    created_at: str


class UserAuthRecord(UserRecord):
    password_hash: str


class StatementUploadRecord(TypedDict):
    id: int
    file_name: str
    display_name: str
    uploaded_at: str
    parse_status: str


TimeRange = Literal["week", "month", "quarter", "6months", "year", "5years", "all"]


def _normalize_category_for_storage(category: str | None, amount: float) -> str:
    raw = (category or "").strip()
    if raw and raw.lower() != "miscellaneous":
        return raw
    return "Refunds & Credits" if amount < 0 else "Other Spending"


def _iso_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return database_url


def init_db(database_url: str | None = None) -> str:
    resolved_database_url = database_url or get_database_url()

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            _apply_schema(cursor)
        connection.commit()

    return resolved_database_url


def _apply_schema(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_login_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS statement_uploads (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
            original_file_name TEXT NOT NULL,
            stored_file_name TEXT NOT NULL,
            file_fingerprint_sha256 TEXT NOT NULL,
            bank_name TEXT,
            statement_period_start TEXT,
            statement_period_end TEXT,
            currency TEXT,
            parse_status TEXT NOT NULL,
            parse_reason TEXT,
            parse_error TEXT,
            uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id TEXT,
            status TEXT NOT NULL,
            request_id TEXT,
            ip_address TEXT,
            user_agent TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
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
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS user_id BIGINT
        """
    )
    cursor.execute(
        """
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS statement_upload_id BIGINT
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_file_fingerprint
        ON transactions (file_fingerprint)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_user_uploaded_at
        ON transactions (user_id, uploaded_at DESC, id DESC)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_user_date
        ON transactions (user_id, date)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_transactions_statement_upload
        ON transactions (statement_upload_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_statement_uploads_user_uploaded_at
        ON statement_uploads (user_id, uploaded_at DESC, id DESC)
        """
    )
    cursor.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_statement_uploads_user_fingerprint
        ON statement_uploads (user_id, file_fingerprint_sha256)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash
        ON user_sessions (token_hash)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_logs_user_occurred_at
        ON audit_logs (user_id, occurred_at DESC, id DESC)
        """
    )
    _backfill_statement_uploads(cursor)


def _backfill_statement_uploads(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        """
        SELECT
            user_id,
            file_name,
            COALESCE(MAX(original_file_name), file_name) AS display_name,
            COALESCE(
                MAX(
                    CASE
                        WHEN LENGTH(COALESCE(file_fingerprint, '')) = 64
                            THEN file_fingerprint
                        ELSE NULL
                    END
                ),
                ''
            ) AS fingerprint_sha256,
            COALESCE(MAX(currency), 'USD') AS currency,
            MAX(uploaded_at) AS uploaded_at
        FROM transactions
        WHERE statement_upload_id IS NULL
          AND file_name <> 'manual-entry'
        GROUP BY user_id, file_name
        ORDER BY MAX(uploaded_at) ASC, file_name ASC
        """
    )
    legacy_groups = cursor.fetchall()

    for (
        user_id,
        file_name,
        display_name,
        fingerprint_sha256,
        currency,
        uploaded_at,
    ) in legacy_groups:
        cursor.execute(
            """
            SELECT id
            FROM statement_uploads
            WHERE stored_file_name = %s
              AND user_id IS NOT DISTINCT FROM %s
            ORDER BY id ASC
            LIMIT 1
            """,
            (file_name, user_id),
        )
        existing_row = cursor.fetchone()

        if existing_row:
            upload_id = int(existing_row[0])
        else:
            resolved_fingerprint = (
                str(fingerprint_sha256)
                if fingerprint_sha256
                else sha256(
                    f"legacy:{user_id or 'unassigned'}:{file_name}".encode("utf-8")
                ).hexdigest()
            )
            cursor.execute(
                """
                INSERT INTO statement_uploads (
                    user_id,
                    original_file_name,
                    stored_file_name,
                    file_fingerprint_sha256,
                    currency,
                    parse_status,
                    uploaded_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    user_id,
                    display_name,
                    file_name,
                    resolved_fingerprint,
                    currency,
                    "parsed",
                    uploaded_at,
                ),
            )
            upload_id = int(cursor.fetchone()[0])

        cursor.execute(
            """
            UPDATE transactions
            SET statement_upload_id = %s
            WHERE statement_upload_id IS NULL
              AND file_name = %s
              AND user_id IS NOT DISTINCT FROM %s
            """,
            (upload_id, file_name, user_id),
        )


def create_user(
    email: str,
    password_hash: str,
    display_name: str,
    database_url: str | None = None,
) -> UserRecord:
    resolved_database_url = init_db(database_url)
    normalized_email = email.strip().lower()
    normalized_display_name = display_name.strip()

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES (%s, %s, %s)
                RETURNING id, email, display_name, created_at
                """,
                (normalized_email, password_hash, normalized_display_name),
            )
            user_id, stored_email, stored_display_name, created_at = cursor.fetchone()
        connection.commit()

    return {
        "id": int(user_id),
        "email": str(stored_email),
        "display_name": str(stored_display_name),
        "created_at": _iso_datetime(created_at),
    }


def maybe_claim_legacy_data_for_first_user(
    user_id: int,
    database_url: str | None = None,
) -> dict[str, int]:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM users")
            user_count = int(cursor.fetchone()[0])
            if user_count != 1:
                connection.commit()
                return {
                    "claimed_transaction_count": 0,
                    "claimed_statement_count": 0,
                }

            cursor.execute(
                """
                UPDATE transactions
                SET user_id = %s
                WHERE user_id IS NULL
                RETURNING id
                """,
                (user_id,),
            )
            claimed_transaction_count = cursor.rowcount

            cursor.execute(
                """
                UPDATE statement_uploads
                SET user_id = %s
                WHERE user_id IS NULL
                RETURNING id
                """,
                (user_id,),
            )
            claimed_statement_count = cursor.rowcount
        connection.commit()

    return {
        "claimed_transaction_count": claimed_transaction_count,
        "claimed_statement_count": claimed_statement_count,
    }


def get_user_auth_by_email(
    email: str,
    database_url: str | None = None,
) -> UserAuthRecord | None:
    resolved_database_url = init_db(database_url)
    normalized_email = email.strip().lower()

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, display_name, created_at, password_hash
                FROM users
                WHERE email = %s
                LIMIT 1
                """,
                (normalized_email,),
            )
            row = cursor.fetchone()

    if not row:
        return None

    user_id, stored_email, display_name, created_at, password_hash = row
    return {
        "id": int(user_id),
        "email": str(stored_email),
        "display_name": str(display_name),
        "created_at": _iso_datetime(created_at),
        "password_hash": str(password_hash),
    }


def get_user_by_id(
    user_id: int,
    database_url: str | None = None,
) -> UserRecord | None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, email, display_name, created_at
                FROM users
                WHERE id = %s
                LIMIT 1
                """,
                (user_id,),
            )
            row = cursor.fetchone()

    if not row:
        return None

    resolved_user_id, email, display_name, created_at = row
    return {
        "id": int(resolved_user_id),
        "email": str(email),
        "display_name": str(display_name),
        "created_at": _iso_datetime(created_at),
    }


def update_user_last_login(
    user_id: int,
    database_url: str | None = None,
) -> None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE users
                SET last_login_at = NOW(), updated_at = NOW()
                WHERE id = %s
                """,
                (user_id,),
            )
        connection.commit()


def create_session(
    user_id: int,
    token_hash: str,
    expires_at: datetime,
    database_url: str | None = None,
) -> None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO user_sessions (user_id, token_hash, expires_at)
                VALUES (%s, %s, %s)
                """,
                (user_id, token_hash, expires_at),
            )
        connection.commit()


def get_user_for_session(
    token_hash: str,
    database_url: str | None = None,
) -> UserRecord | None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    users.id,
                    users.email,
                    users.display_name,
                    users.created_at,
                    user_sessions.id
                FROM user_sessions
                JOIN users ON users.id = user_sessions.user_id
                WHERE user_sessions.token_hash = %s
                  AND user_sessions.revoked_at IS NULL
                  AND user_sessions.expires_at > NOW()
                LIMIT 1
                """,
                (token_hash,),
            )
            row = cursor.fetchone()
            if not row:
                connection.commit()
                return None

            (
                user_id,
                email,
                display_name,
                created_at,
                session_id,
            ) = row
            cursor.execute(
                """
                UPDATE user_sessions
                SET last_seen_at = NOW()
                WHERE id = %s
                """,
                (session_id,),
            )
        connection.commit()

    return {
        "id": int(user_id),
        "email": str(email),
        "display_name": str(display_name),
        "created_at": _iso_datetime(created_at),
    }


def revoke_session(
    token_hash: str,
    database_url: str | None = None,
) -> None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE user_sessions
                SET revoked_at = NOW()
                WHERE token_hash = %s
                  AND revoked_at IS NULL
                """,
                (token_hash,),
            )
        connection.commit()


def record_audit_event(
    *,
    action: str,
    status: str,
    user_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
    database_url: str | None = None,
) -> None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_logs (
                    user_id,
                    action,
                    target_type,
                    target_id,
                    status,
                    request_id,
                    ip_address,
                    user_agent,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    action,
                    target_type,
                    target_id,
                    status,
                    request_id,
                    ip_address,
                    user_agent,
                    Json(metadata or {}),
                ),
            )
        connection.commit()


def create_statement_upload(
    *,
    user_id: int,
    original_file_name: str,
    stored_file_name: str,
    file_fingerprint_sha256: str,
    parse_status: str,
    parse_reason: str | None = None,
    parse_error: str | None = None,
    currency: str | None = None,
    database_url: str | None = None,
) -> StatementUploadRecord:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO statement_uploads (
                    user_id,
                    original_file_name,
                    stored_file_name,
                    file_fingerprint_sha256,
                    currency,
                    parse_status,
                    parse_reason,
                    parse_error
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, stored_file_name, original_file_name, uploaded_at, parse_status
                """,
                (
                    user_id,
                    original_file_name,
                    stored_file_name,
                    file_fingerprint_sha256,
                    currency,
                    parse_status,
                    parse_reason,
                    parse_error,
                ),
            )
            upload_id, file_name, display_name, uploaded_at, resolved_parse_status = (
                cursor.fetchone()
            )
        connection.commit()

    return {
        "id": int(upload_id),
        "file_name": str(file_name),
        "display_name": str(display_name),
        "uploaded_at": _iso_datetime(uploaded_at),
        "parse_status": str(resolved_parse_status),
    }


def insert_transactions(
    transactions: list[Transaction],
    *,
    user_id: int,
    file_name: str,
    file_fingerprint: str | None = None,
    original_file_name: str | None = None,
    statement_upload_id: int | None = None,
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
                    user_id,
                    statement_upload_id,
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        user_id,
                        statement_upload_id,
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
    *,
    user_id: int,
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
    normalized_category = _normalize_category_for_storage(inferred_category, amount)
    normalized_payment_method = (
        payment_method.strip() if payment_method and payment_method.strip() else None
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
                    user_id,
                    statement_upload_id,
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    user_id,
                    None,
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
        "id": 0,
        "date": transaction_date,
        "merchant": normalized_merchant,
        "amount": amount,
        "category": normalized_category,
        "currency": "USD",
        "payment_method": normalized_payment_method,
        "file_name": file_name,
        "original_file_name": original_file_name,
    }


def update_transaction_category(
    *,
    user_id: int,
    transaction_id: int,
    category: str,
    database_url: str | None = None,
) -> StoredTransaction | None:
    return update_transaction(
        user_id=user_id,
        transaction_id=transaction_id,
        category=category,
        database_url=database_url,
    )


def update_transaction(
    *,
    user_id: int,
    transaction_id: int,
    category: str | None = None,
    merchant: str | None = None,
    database_url: str | None = None,
) -> StoredTransaction | None:
    resolved_database_url = init_db(database_url)
    normalized_category = category.strip() if category is not None else None
    if normalized_category == "":
        return None

    normalized_merchant: str | None = None
    if merchant is not None:
        normalized_merchant = " ".join(merchant.split())
        if not normalized_merchant:
            return None

    if normalized_category is None and normalized_merchant is None:
        return None

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT merchant, amount, COALESCE(category, '') AS category
                FROM transactions
                WHERE id = %s
                  AND user_id = %s
                """,
                (transaction_id, user_id),
            )
            existing = cursor.fetchone()
            if existing is None:
                return None

            effective_merchant = (
                normalized_merchant
                if normalized_merchant is not None
                else str(existing[0])
            )
            effective_amount = float(existing[1])
            if normalized_category is not None:
                effective_category = normalized_category
            elif normalized_merchant is not None:
                effective_category = categorize_merchant(
                    effective_merchant, effective_amount
                )
            else:
                effective_category = str(existing[2])

            cursor.execute(
                """
                UPDATE transactions
                SET
                    merchant = %s,
                    category = %s
                WHERE id = %s
                  AND user_id = %s
                RETURNING
                    id,
                    date,
                    merchant,
                    amount,
                    COALESCE(category, '') AS category,
                    COALESCE(currency, 'USD') AS currency,
                    payment_method,
                    file_name,
                    COALESCE(original_file_name, file_name) AS original_file_name
                """,
                (
                    effective_merchant,
                    _normalize_category_for_storage(
                        effective_category, effective_amount
                    ),
                    transaction_id,
                    user_id,
                ),
            )
            row = cursor.fetchone()
        connection.commit()

    if row is None:
        return None

    return {
        "id": int(row[0]),
        "date": str(row[1]),
        "merchant": str(row[2]),
        "amount": float(row[3]),
        "category": _normalize_category_for_storage(str(row[4]), float(row[3])),
        "currency": str(row[5]),
        "payment_method": str(row[6]) if row[6] is not None else None,
        "file_name": str(row[7]),
        "original_file_name": str(row[8]),
    }


def _since_date_for_range(range_key: TimeRange) -> date | None:
    today = date.today()
    if range_key == "week":
        return today - timedelta(days=7)
    if range_key == "month":
        return today - timedelta(days=30)
    if range_key == "quarter":
        return today - timedelta(days=90)
    if range_key == "6months":
        return today - timedelta(days=180)
    if range_key == "year":
        return today - timedelta(days=365)
    if range_key == "5years":
        return today - timedelta(days=365 * 5)
    return None


def _build_user_date_filter_clause(
    user_id: int,
    range_key: TimeRange,
) -> tuple[str, tuple[object, ...]]:
    since_date = _since_date_for_range(range_key)
    if since_date is None:
        return "WHERE user_id = %s", (user_id,)
    return (
        "WHERE user_id = %s AND date ~ '^\\d{4}-\\d{2}-\\d{2}$' AND date::date >= %s",
        (user_id, since_date.isoformat()),
    )


def get_transactions_summary(
    *,
    user_id: int,
    range_key: TimeRange = "all",
    database_url: str | None = None,
) -> TransactionsSummary:
    resolved_database_url = init_db(database_url)
    date_filter_clause, query_params = _build_user_date_filter_clause(user_id, range_key)
    summary_query = f"""
        SELECT
            COALESCE(SUM(amount), 0),
            COUNT(*),
            COALESCE(AVG(amount), 0),
            COUNT(DISTINCT statement_upload_id)
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
    *,
    user_id: int,
    limit: int = 5,
    database_url: str | None = None,
) -> list[RecentUpload]:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    statement_uploads.stored_file_name,
                    statement_uploads.original_file_name,
                    COUNT(transactions.id) AS transaction_count,
                    COALESCE(SUM(transactions.amount), 0) AS total_amount,
                    statement_uploads.uploaded_at,
                    statement_uploads.parse_status
                FROM statement_uploads
                LEFT JOIN transactions
                    ON transactions.statement_upload_id = statement_uploads.id
                WHERE statement_uploads.user_id = %s
                GROUP BY statement_uploads.id
                ORDER BY statement_uploads.uploaded_at DESC, statement_uploads.id DESC
                LIMIT %s
                """,
                (user_id, limit),
            )
            rows = cursor.fetchall()

    return [
        {
            "file_name": str(file_name),
            "display_name": str(display_name),
            "transaction_count": int(transaction_count),
            "total_amount": float(total_amount),
            "uploaded_at": _iso_datetime(uploaded_at),
            "parse_status": str(parse_status),
        }
        for (
            file_name,
            display_name,
            transaction_count,
            total_amount,
            uploaded_at,
            parse_status,
        ) in rows
    ]


def get_transactions(
    *,
    user_id: int,
    range_key: TimeRange = "all",
    limit: int = 5000,
    database_url: str | None = None,
) -> list[StoredTransaction]:
    resolved_database_url = init_db(database_url)
    date_filter_clause, range_params = _build_user_date_filter_clause(user_id, range_key)
    transaction_query = f"""
        SELECT
            id,
            date,
            merchant,
            amount,
            COALESCE(category, '') AS category,
            COALESCE(currency, 'USD') AS currency,
            payment_method,
            file_name,
            COALESCE(original_file_name, file_name) AS original_file_name
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
    query_params: tuple[object, ...] = (*range_params, limit)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(transaction_query, query_params)
            rows = cursor.fetchall()

    return [
        {
            "id": int(row[0]),
            "date": str(row[1]),
            "merchant": str(row[2]),
            "amount": float(row[3]),
            "category": _normalize_category_for_storage(str(row[4]), float(row[3])),
            "currency": str(row[5]),
            "payment_method": str(row[6]) if row[6] is not None else None,
            "file_name": str(row[7]),
            "original_file_name": str(row[8]),
        }
        for row in rows
    ]


def get_existing_upload_by_fingerprint(
    *,
    user_id: int,
    file_fingerprint_sha256: str,
    database_url: str | None = None,
) -> ExistingUpload | None:
    resolved_database_url = init_db(database_url)

    with psycopg.connect(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    stored_file_name,
                    original_file_name,
                    uploaded_at,
                    parse_status
                FROM statement_uploads
                WHERE user_id = %s
                  AND file_fingerprint_sha256 = %s
                ORDER BY uploaded_at DESC, id DESC
                LIMIT 1
                """,
                (user_id, file_fingerprint_sha256),
            )
            row = cursor.fetchone()

    if not row:
        return None

    upload_id, file_name, display_name, uploaded_at, parse_status = row
    return {
        "id": int(upload_id),
        "file_name": str(file_name),
        "display_name": str(display_name),
        "uploaded_at": _iso_datetime(uploaded_at),
        "parse_status": str(parse_status),
    }
