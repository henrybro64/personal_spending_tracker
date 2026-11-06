import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, NamedTuple, TypedDict

import psycopg
from psycopg.types.json import Json

from backend.db import db_connection

from backend.currency import (
    DEFAULT_BASE_CURRENCY,
    derive_base_money_fields,
    normalize_supported_currency_code,
)
from backend.parser import Transaction, categorize_merchant
from backend.transaction_source import (
    legacy_source_keys_for_transactions,
    normalize_merchant_for_source_key,
    source_keys_for_transactions,
)


class DuplicateUsernameError(Exception):
    """The users.email unique constraint rejected this registration."""


class DuplicateStatementUploadError(Exception):
    """This user already imported a statement with the same fingerprint."""


class InsertTransactionsResult(NamedTuple):
    """How many rows were newly inserted, and which source_keys they used."""

    inserted_count: int
    inserted_source_keys: frozenset[str]


class StatementImport(NamedTuple):
    """One statement to save inside a multi-statement merge transaction."""

    original_file_name: str
    stored_file_name: str
    file_fingerprint_sha256: str
    parse_status: str
    currency: str | None
    transactions: list[Transaction]
    source_keys: list[str] | None
    statement_scope: str


# Serializes registration with the first-user legacy claim. Two signups that
# both pass the application lookup must not both observe "I am the only user".
_REGISTRATION_LOCK_KEY = 872_341_221
# Two-key advisory lock class for AI provider updates. Held until commit so
# "deselect all, then insert selected" cannot overlap for one user.
_AI_PROVIDER_LOCK_CLASS = 41_207


class TransactionsSummary(TypedDict):
    total_spending: float
    number_of_transactions: int
    average_transaction_amount: float
    number_of_statements: int
    unconverted_transaction_count: int
    unconverted_currencies: list[str]
    statement_count_scope: Literal["all_time"]


class RecentUploadCurrencyTotal(TypedDict):
    currency: str
    total_amount: float
    transaction_count: int


class RecentUpload(TypedDict):
    file_name: str
    display_name: str
    transaction_count: int
    total_amount: float
    currency_totals: list[RecentUploadCurrencyTotal]
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
    original_amount: float
    original_currency: str
    base_amount: float | None
    base_currency: str
    exchange_rate: float | None
    exchange_rate_date: str | None
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


AiProvider = Literal["openai", "perplexity"]


class StatementUploadRecord(TypedDict):
    id: int
    file_name: str
    display_name: str
    uploaded_at: str
    parse_status: str


class UserAiProviderSettingRecord(TypedDict):
    provider: AiProvider
    api_key_last4: str
    model: str | None
    is_selected: bool
    created_at: str
    updated_at: str


class UserAiProviderSecretRecord(UserAiProviderSettingRecord):
    api_key: str


TimeRange = Literal["week", "month", "quarter", "6months", "year", "5years", "all"]


def _normalize_category_for_storage(category: str | None, amount: float) -> str:
    raw = (category or "").strip()
    if raw and raw.lower() != "miscellaneous":
        return raw
    return "Refunds & Credits" if amount < 0 else "Other Spending"


STORED_TRANSACTION_SELECT_COLUMNS = """
    id,
    date,
    merchant,
    COALESCE(original_amount, amount) AS amount,
    COALESCE(category, '') AS category,
    COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD') AS currency,
    payment_method,
    file_name,
    COALESCE(original_file_name, file_name) AS original_file_name,
    COALESCE(original_amount, amount) AS original_amount,
    COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD') AS original_currency,
    base_amount,
    COALESCE(NULLIF(base_currency, ''), 'TWD') AS base_currency,
    exchange_rate,
    exchange_rate_date
"""


def _stored_transaction_from_row(row: Any) -> StoredTransaction:
    amount = float(row[3])
    return {
        "id": int(row[0]),
        "date": str(row[1]),
        "merchant": str(row[2]),
        "amount": amount,
        "category": _normalize_category_for_storage(str(row[4]), amount),
        "currency": str(row[5]),
        "payment_method": str(row[6]) if row[6] is not None else None,
        "file_name": str(row[7]),
        "original_file_name": str(row[8]),
        "original_amount": float(row[9]),
        "original_currency": str(row[10]),
        "base_amount": float(row[11]) if row[11] is not None else None,
        "base_currency": str(row[12]),
        "exchange_rate": float(row[13]) if row[13] is not None else None,
        "exchange_rate_date": str(row[14]) if row[14] is not None else None,
    }


def _iso_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def get_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is required")
    return database_url


def _resolve_database_url(database_url: str | None = None) -> str:
    return database_url or get_database_url()


def get_llm_settings_encryption_key() -> str:
    configured = os.getenv("LLM_SETTINGS_ENCRYPTION_KEY", "").strip()
    if not configured:
        raise RuntimeError(
            "LLM_SETTINGS_ENCRYPTION_KEY environment variable is required for AI settings"
        )
    return configured


def init_db(database_url: str | None = None) -> str:
    from backend.migrate import migrate_database

    resolved_database_url = _resolve_database_url(database_url)
    migrate_database(resolved_database_url)
    return resolved_database_url


def create_user(
    email: str,
    password_hash: str,
    display_name: str,
    database_url: str | None = None,
) -> UserRecord:
    resolved_database_url = _resolve_database_url(database_url)
    normalized_email = email.strip().lower()
    normalized_display_name = display_name.strip()

    with db_connection(resolved_database_url) as connection:
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


def _claim_legacy_rows(cursor: psycopg.Cursor[Any], user_id: int) -> dict[str, int]:
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = int(cursor.fetchone()[0])
    if user_count != 1:
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

    # Backfill missing statement_upload rows for legacy single-user data that
    # predates statement_upload_id, so claimed upload counts stay accurate.
    cursor.execute(
        """
        WITH legacy_groups AS (
            SELECT
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
            WHERE user_id = %s
              AND statement_upload_id IS NULL
              AND file_name <> 'manual-entry'
            GROUP BY file_name
        )
        INSERT INTO statement_uploads (
            user_id,
            original_file_name,
            stored_file_name,
            file_fingerprint_sha256,
            currency,
            parse_status,
            uploaded_at
        )
        SELECT
            %s,
            legacy_groups.display_name,
            legacy_groups.file_name,
            CASE
                WHEN legacy_groups.fingerprint_sha256 <> ''
                    THEN legacy_groups.fingerprint_sha256
                ELSE md5(
                    'legacy:' || %s::text || ':' || legacy_groups.file_name
                ) || md5(
                    'legacy-fallback:' || %s::text || ':' || legacy_groups.file_name
                )
            END,
            legacy_groups.currency,
            'parsed',
            legacy_groups.uploaded_at
        FROM legacy_groups
        LEFT JOIN statement_uploads
            ON statement_uploads.user_id = %s
           AND statement_uploads.stored_file_name = legacy_groups.file_name
        WHERE statement_uploads.id IS NULL
        RETURNING id, stored_file_name
        """,
        (user_id, user_id, user_id, user_id, user_id),
    )
    created_statement_upload_rows = cursor.fetchall()
    for upload_id, file_name in created_statement_upload_rows:
        cursor.execute(
            """
            UPDATE transactions
            SET statement_upload_id = %s
            WHERE user_id = %s
              AND statement_upload_id IS NULL
              AND file_name = %s
            """,
            (int(upload_id), user_id, str(file_name)),
        )
    created_statement_count = len(created_statement_upload_rows)

    cursor.execute(
        """
        UPDATE statement_uploads
        SET user_id = %s
        WHERE user_id IS NULL
        RETURNING id
        """,
        (user_id,),
    )
    claimed_statement_count = created_statement_count + cursor.rowcount

    return {
        "claimed_transaction_count": claimed_transaction_count,
        "claimed_statement_count": claimed_statement_count,
    }


def maybe_claim_legacy_data_for_first_user(
    user_id: int,
    database_url: str | None = None,
) -> dict[str, int]:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_REGISTRATION_LOCK_KEY,),
            )
            claimed = _claim_legacy_rows(cursor, user_id)
        connection.commit()

    return claimed


def register_new_user(
    email: str,
    password_hash: str,
    display_name: str,
    database_url: str | None = None,
) -> tuple[UserRecord, dict[str, int]]:
    """Create a user and claim legacy rows in one transaction.

    An advisory lock is held from before the insert until commit, so concurrent
    first registrations cannot both skip the claim, and a unique email conflict
    is raised as DuplicateUsernameError instead of an uncaught constraint error.
    """
    resolved_database_url = _resolve_database_url(database_url)
    normalized_email = email.strip().lower()
    normalized_display_name = display_name.strip()

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (_REGISTRATION_LOCK_KEY,),
            )
            try:
                cursor.execute(
                    """
                    INSERT INTO users (email, password_hash, display_name)
                    VALUES (%s, %s, %s)
                    RETURNING id, email, display_name, created_at
                    """,
                    (normalized_email, password_hash, normalized_display_name),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise DuplicateUsernameError(normalized_email) from exc
            user_id, stored_email, stored_display_name, created_at = cursor.fetchone()
            claimed = _claim_legacy_rows(cursor, int(user_id))
        connection.commit()

    user: UserRecord = {
        "id": int(user_id),
        "email": str(stored_email),
        "display_name": str(stored_display_name),
        "created_at": _iso_datetime(created_at),
    }
    return user, claimed


def get_user_auth_by_email(
    email: str,
    database_url: str | None = None,
) -> UserAuthRecord | None:
    resolved_database_url = _resolve_database_url(database_url)
    normalized_email = email.strip().lower()

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
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
    resolved_database_url = _resolve_database_url(database_url)

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
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
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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



# Stored instead of a scrypt hash for users created by an external sign-in.
# verify_password rejects it, so such a user can never sign in locally.
EXTERNAL_SIGN_IN_ONLY_MARKER = "!external-sign-in-only"
_EXTERNAL_USERNAME_ATTEMPTS = 50


class OidcLoginState(TypedDict):
    browser_binding_hash: str
    nonce: str
    code_verifier: str
    return_to: str


def save_oidc_login_state(
    *,
    state_hash: str,
    browser_binding_hash: str,
    nonce: str,
    code_verifier: str,
    return_to: str,
    expires_at: datetime,
    max_pending: int,
    database_url: str | None = None,
) -> bool:
    """Store a login state. False, and nothing stored, at ``max_pending``.

    Starting a sign-in needs no account, so the cap keeps repeated calls from
    growing the table without limit.
    """
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            # Abandoned logins never reach the callback; drop them here.
            cursor.execute("DELETE FROM oidc_login_states WHERE expires_at <= NOW()")
            cursor.execute(
                """
                INSERT INTO oidc_login_states (
                    state_hash,
                    browser_binding_hash,
                    nonce,
                    code_verifier,
                    return_to,
                    expires_at
                )
                SELECT %s, %s, %s, %s, %s, %s
                WHERE (SELECT COUNT(*) FROM oidc_login_states) < %s
                """,
                (
                    state_hash,
                    browser_binding_hash,
                    nonce,
                    code_verifier,
                    return_to,
                    expires_at,
                    max_pending,
                ),
            )
            stored = cursor.rowcount == 1
        connection.commit()
    return stored


def consume_oidc_login_state(
    state_hash: str,
    database_url: str | None = None,
) -> OidcLoginState | None:
    """Delete and return an unexpired login state. Each state works once."""
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM oidc_login_states
                WHERE state_hash = %s
                RETURNING
                    browser_binding_hash,
                    nonce,
                    code_verifier,
                    return_to,
                    expires_at > NOW()
                """,
                (state_hash,),
            )
            row = cursor.fetchone()
        connection.commit()

    if not row or not row[4]:
        return None
    browser_binding_hash, nonce, code_verifier, return_to, _unexpired = row
    return {
        "browser_binding_hash": str(browser_binding_hash),
        "nonce": str(nonce),
        "code_verifier": str(code_verifier),
        "return_to": str(return_to),
    }


def _user_record_from_row(row: tuple[Any, ...]) -> UserRecord:
    user_id, email, display_name, created_at = row
    return {
        "id": int(user_id),
        "email": str(email),
        "display_name": str(display_name),
        "created_at": _iso_datetime(created_at),
    }


def _user_for_identity(
    cursor: psycopg.Cursor[Any],
    *,
    provider: str,
    issuer: str,
    subject: str,
) -> UserRecord | None:
    cursor.execute(
        """
        UPDATE user_identities
        SET last_login_at = NOW()
        FROM users
        WHERE users.id = user_identities.user_id
          AND user_identities.provider = %s
          AND user_identities.issuer = %s
          AND user_identities.subject = %s
        RETURNING users.id, users.email, users.display_name, users.created_at
        """,
        (provider, issuer, subject),
    )
    row = cursor.fetchone()
    return _user_record_from_row(row) if row else None


def get_user_for_identity(
    *,
    provider: str,
    issuer: str,
    subject: str,
    database_url: str | None = None,
) -> UserRecord | None:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            user = _user_for_identity(
                cursor, provider=provider, issuer=issuer, subject=subject
            )
        connection.commit()
    return user


def create_user_with_identity(
    *,
    username_base: str,
    display_name: str,
    provider: str,
    issuer: str,
    subject: str,
    tenant_id: str | None,
    object_id: str | None,
    database_url: str | None = None,
) -> UserRecord:
    """Create a new, empty user for an external identity in one transaction.

    Never attaches to an existing user and never claims legacy rows. If the
    same identity was created concurrently, returns that user instead.
    """
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            existing = _user_for_identity(
                cursor, provider=provider, issuer=issuer, subject=subject
            )
            if existing:
                connection.commit()
                return existing

            row = None
            for attempt in range(_EXTERNAL_USERNAME_ATTEMPTS):
                suffix = "" if attempt == 0 else f"-{attempt + 1}"
                username = f"{username_base[: 80 - len(suffix)]}{suffix}"
                cursor.execute(
                    """
                    INSERT INTO users (email, password_hash, display_name)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                    RETURNING id, email, display_name, created_at
                    """,
                    (username, EXTERNAL_SIGN_IN_ONLY_MARKER, display_name),
                )
                row = cursor.fetchone()
                if row:
                    break
            if not row:
                raise DuplicateUsernameError(username_base)
            user = _user_record_from_row(row)
            try:
                cursor.execute(
                    """
                    INSERT INTO user_identities (
                        user_id, provider, issuer, subject,
                        tenant_id, object_id, last_login_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (user["id"], provider, issuer, subject, tenant_id, object_id),
                )
            except psycopg.errors.UniqueViolation:
                # Another request created this identity first; keep that user.
                connection.rollback()
                existing = _user_for_identity(
                    cursor, provider=provider, issuer=issuer, subject=subject
                )
                connection.commit()
                if existing is None:
                    raise
                return existing
        connection.commit()
    return user


def user_has_identity(
    user_id: int,
    *,
    provider: str,
    database_url: str | None = None,
) -> bool:
    resolved_database_url = _resolve_database_url(database_url)

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT 1 FROM user_identities
            WHERE user_id = %s AND provider = %s
            LIMIT 1
            """,
            (user_id, provider),
        )
        return cursor.fetchone() is not None


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
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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


def list_user_ai_provider_settings(
    user_id: int,
    database_url: str | None = None,
) -> list[UserAiProviderSettingRecord]:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    provider,
                    api_key_last4,
                    model,
                    is_selected,
                    created_at,
                    updated_at
                FROM user_ai_provider_settings
                WHERE user_id = %s
                ORDER BY provider ASC
                """,
                (user_id,),
            )
            rows = cursor.fetchall()
        connection.commit()

    return [
        {
            "provider": str(row[0]),
            "api_key_last4": str(row[1]),
            "model": str(row[2]) if row[2] is not None else None,
            "is_selected": bool(row[3]),
            "created_at": _iso_datetime(row[4]),
            "updated_at": _iso_datetime(row[5]),
        }
        for row in rows
    ]


def upsert_user_ai_provider_setting(
    *,
    user_id: int,
    provider: AiProvider,
    api_key: str | None,
    model: str | None,
    is_selected: bool,
    database_url: str | None = None,
) -> UserAiProviderSettingRecord:
    try:
        return _upsert_user_ai_provider_setting_once(
            user_id=user_id,
            provider=provider,
            api_key=api_key,
            model=model,
            is_selected=is_selected,
            database_url=database_url,
        )
    except psycopg.errors.UniqueViolation:
        # The partial unique index rejected a second selected provider. Retry
        # once so the lock can deselect the row that won the race.
        return _upsert_user_ai_provider_setting_once(
            user_id=user_id,
            provider=provider,
            api_key=api_key,
            model=model,
            is_selected=is_selected,
            database_url=database_url,
        )


def _upsert_user_ai_provider_setting_once(
    *,
    user_id: int,
    provider: AiProvider,
    api_key: str | None,
    model: str | None,
    is_selected: bool,
    database_url: str | None = None,
) -> UserAiProviderSettingRecord:
    resolved_database_url = _resolve_database_url(database_url)
    normalized_api_key = api_key.strip() if api_key and api_key.strip() else None
    normalized_model = model.strip() if model and model.strip() else None

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(%s::integer, hashtext(%s))",
                (_AI_PROVIDER_LOCK_CLASS, str(user_id)),
            )
            cursor.execute(
                """
                SELECT 1
                FROM user_ai_provider_settings
                WHERE user_id = %s
                  AND provider = %s
                """,
                (user_id, provider),
            )
            existing_row = cursor.fetchone()
            if existing_row is None and normalized_api_key is None:
                raise ValueError(
                    "API key is required before this provider can be selected."
                )

            if is_selected:
                cursor.execute(
                    """
                    UPDATE user_ai_provider_settings
                    SET is_selected = FALSE, updated_at = NOW()
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )

            if normalized_api_key is not None:
                cursor.execute(
                    """
                    INSERT INTO user_ai_provider_settings (
                        user_id,
                        provider,
                        encrypted_api_key,
                        api_key_last4,
                        model,
                        is_selected
                    )
                    VALUES (
                        %s,
                        %s,
                        pgp_sym_encrypt(%s, %s),
                        %s,
                        %s,
                        %s
                    )
                    ON CONFLICT (user_id, provider)
                    DO UPDATE SET
                        encrypted_api_key = EXCLUDED.encrypted_api_key,
                        api_key_last4 = EXCLUDED.api_key_last4,
                        model = EXCLUDED.model,
                        is_selected = EXCLUDED.is_selected,
                        updated_at = NOW()
                    RETURNING
                        provider,
                        api_key_last4,
                        model,
                        is_selected,
                        created_at,
                        updated_at
                    """,
                    (
                        user_id,
                        provider,
                        normalized_api_key,
                        get_llm_settings_encryption_key(),
                        normalized_api_key[-4:],
                        normalized_model,
                        is_selected,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE user_ai_provider_settings
                    SET
                        model = %s,
                        is_selected = %s,
                        updated_at = NOW()
                    WHERE user_id = %s
                      AND provider = %s
                    RETURNING
                        provider,
                        api_key_last4,
                        model,
                        is_selected,
                        created_at,
                        updated_at
                    """,
                    (
                        normalized_model,
                        is_selected,
                        user_id,
                        provider,
                    ),
                )

            row = cursor.fetchone()
        connection.commit()

    if row is None:
        raise RuntimeError("Failed to save provider settings.")

    return {
        "provider": str(row[0]),
        "api_key_last4": str(row[1]),
        "model": str(row[2]) if row[2] is not None else None,
        "is_selected": bool(row[3]),
        "created_at": _iso_datetime(row[4]),
        "updated_at": _iso_datetime(row[5]),
    }


def delete_user_ai_provider_setting(
    *,
    user_id: int,
    provider: AiProvider,
    database_url: str | None = None,
) -> bool:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM user_ai_provider_settings
                WHERE user_id = %s
                  AND provider = %s
                RETURNING is_selected
                """,
                (user_id, provider),
            )
            deleted_row = cursor.fetchone()
            if deleted_row and bool(deleted_row[0]):
                cursor.execute(
                    """
                    WITH next_provider AS (
                        SELECT id
                        FROM user_ai_provider_settings
                        WHERE user_id = %s
                        ORDER BY updated_at DESC, id DESC
                        LIMIT 1
                    )
                    UPDATE user_ai_provider_settings
                    SET is_selected = TRUE, updated_at = NOW()
                    WHERE id IN (SELECT id FROM next_provider)
                    """,
                    (user_id,),
                )
        connection.commit()

    return deleted_row is not None


def get_user_ai_provider_secret(
    *,
    user_id: int,
    provider: AiProvider | None = None,
    database_url: str | None = None,
) -> UserAiProviderSecretRecord | None:
    resolved_database_url = _resolve_database_url(database_url)
    encryption_key = get_llm_settings_encryption_key()

    query = """
        SELECT
            provider,
            pgp_sym_decrypt(encrypted_api_key, %s)::text AS api_key,
            api_key_last4,
            model,
            is_selected,
            created_at,
            updated_at
        FROM user_ai_provider_settings
        WHERE user_id = %s
    """
    params: tuple[object, ...]
    if provider is None:
        query += """
        ORDER BY is_selected DESC, updated_at DESC, id DESC
        LIMIT 1
        """
        params = (encryption_key, user_id)
    else:
        query += """
          AND provider = %s
        LIMIT 1
        """
        params = (encryption_key, user_id, provider)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
        connection.commit()

    if row is None:
        return None

    return {
        "provider": str(row[0]),
        "api_key": str(row[1]),
        "api_key_last4": str(row[2]),
        "model": str(row[3]) if row[3] is not None else None,
        "is_selected": bool(row[4]),
        "created_at": _iso_datetime(row[5]),
        "updated_at": _iso_datetime(row[6]),
    }


def _insert_statement_upload_row(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    original_file_name: str,
    stored_file_name: str,
    file_fingerprint_sha256: str,
    parse_status: str,
    parse_reason: str | None,
    parse_error: str | None,
    currency: str | None,
) -> StatementUploadRecord:
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
        RETURNING
            id,
            stored_file_name,
            original_file_name,
            uploaded_at,
            parse_status
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
    row = cursor.fetchone()
    if row is None:
        raise DuplicateStatementUploadError(file_fingerprint_sha256)
    return _statement_upload_record(row)


def _statement_upload_record(row: tuple[Any, ...]) -> StatementUploadRecord:
    upload_id, file_name, display_name, uploaded_at, parse_status = row
    return {
        "id": int(upload_id),
        "file_name": str(file_name),
        "display_name": str(display_name),
        "uploaded_at": _iso_datetime(uploaded_at),
        "parse_status": str(parse_status),
    }


def _load_statement_upload_by_fingerprint(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    file_fingerprint_sha256: str,
) -> StatementUploadRecord | None:
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
        """,
        (user_id, file_fingerprint_sha256),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return _statement_upload_record(row)


def _reuse_statement_upload_row(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    original_file_name: str,
    stored_file_name: str,
    file_fingerprint_sha256: str,
    parse_status: str,
    parse_reason: str | None,
    parse_error: str | None,
    currency: str | None,
) -> StatementUploadRecord:
    """Insert the upload, or return the row this user already has for it."""
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
        ON CONFLICT (user_id, file_fingerprint_sha256) DO NOTHING
        RETURNING
            id,
            stored_file_name,
            original_file_name,
            uploaded_at,
            parse_status
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
    row = cursor.fetchone()
    if row is not None:
        return _statement_upload_record(row)
    existing = _load_statement_upload_by_fingerprint(
        cursor,
        user_id=user_id,
        file_fingerprint_sha256=file_fingerprint_sha256,
    )
    if existing is None:
        raise DuplicateStatementUploadError(file_fingerprint_sha256)
    return existing


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
    resolved_database_url = _resolve_database_url(database_url)

    try:
        with db_connection(
            resolved_database_url,
            operation="create_statement_upload",
            statement_timeout_ms=None,
        ) as connection:
            with connection.cursor() as cursor:
                record = _insert_statement_upload_row(
                    cursor,
                    user_id=user_id,
                    original_file_name=original_file_name,
                    stored_file_name=stored_file_name,
                    file_fingerprint_sha256=file_fingerprint_sha256,
                    parse_status=parse_status,
                    parse_reason=parse_reason,
                    parse_error=parse_error,
                    currency=currency,
                )
            connection.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateStatementUploadError(file_fingerprint_sha256) from exc

    return record


def _quantized_amount(amount: float) -> str:
    return f"{Decimal(str(amount)).quantize(Decimal('0.01')):.2f}"


def _legacy_row_matches_transaction(
    *,
    legacy_date: str,
    legacy_merchant: str,
    legacy_amount: float,
    legacy_currency: str,
    transaction: Transaction,
    original_currency: str,
) -> bool:
    if legacy_date != transaction["date"].strip():
        return False
    merchants_match = normalize_merchant_for_source_key(
        legacy_merchant
    ) == normalize_merchant_for_source_key(transaction["merchant"])
    amounts_match = _quantized_amount(legacy_amount) == _quantized_amount(
        float(transaction["amount"])
    )
    currencies_match = (
        normalize_supported_currency_code(
            legacy_currency,
            default=DEFAULT_BASE_CURRENCY,
        )
        == original_currency
    )
    return merchants_match and amounts_match and currencies_match


def _fetch_legacy_null_key_rows(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    dates: list[str],
) -> list[tuple[Any, ...]]:
    cursor.execute(
        """
        SELECT
            id,
            date,
            merchant,
            COALESCE(original_amount, amount) AS amount,
            COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
                AS currency
        FROM transactions
        WHERE user_id = %s
          AND source_key IS NULL
          AND COALESCE(file_name, '') <> 'manual-entry'
          AND date = ANY(%s)
        ORDER BY date ASC, id ASC
        """,
        (user_id, dates),
    )
    return list(cursor.fetchall())


def _try_claim_legacy_row(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    source_key: str,
    legacy_id: int,
) -> bool:
    cursor.execute(
        """
        UPDATE transactions
        SET source_key = %s
        WHERE id = %s
          AND user_id = %s
          AND source_key IS NULL
        """,
        (source_key, legacy_id, user_id),
    )
    return int(cursor.rowcount or 0) > 0


def _backfill_legacy_source_keys(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    transactions: list[Transaction],
    source_keys: list[str],
) -> set[str]:
    """Attach keys to pre-migration NULL rows that match; return claimed keys."""

    dates = sorted({transaction["date"].strip() for transaction in transactions})
    if not dates:
        return set()

    legacy_rows = _fetch_legacy_null_key_rows(
        cursor, user_id=user_id, dates=dates
    )
    if not legacy_rows:
        return set()

    claimed_keys: set[str] = set()
    claimed_legacy_ids: set[int] = set()
    for transaction, source_key in zip(transactions, source_keys, strict=True):
        if source_key in claimed_keys:
            continue
        original_currency = normalize_supported_currency_code(
            transaction.get("currency"),
            default=DEFAULT_BASE_CURRENCY,
        )
        for row in legacy_rows:
            legacy_id = int(row[0])
            if legacy_id in claimed_legacy_ids:
                continue
            if not _legacy_row_matches_transaction(
                legacy_date=str(row[1]),
                legacy_merchant=str(row[2]),
                legacy_amount=float(row[3]),
                legacy_currency=str(row[4]),
                transaction=transaction,
                original_currency=original_currency,
            ):
                continue
            if _try_claim_legacy_row(
                cursor,
                user_id=user_id,
                source_key=source_key,
                legacy_id=legacy_id,
            ):
                claimed_keys.add(source_key)
                claimed_legacy_ids.add(legacy_id)
            break
    return claimed_keys


def _claim_pre_sign_fix_source_keys(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    source_keys: list[str],
    legacy_keys: list[str],
    already_claimed: set[str] | None = None,
) -> set[str]:
    """Treat pre-#74 amount hashes as duplicates of the new signed keys.

    Refunds that previously hashed ``abs(amount)`` keep that row instead of
    inserting a second copy under the signed amount. Migrates the stored key
    forward when safe.
    """
    claimed_keys: set[str] = set(already_claimed or ())
    pairs = [
        (new_key, legacy_key)
        for new_key, legacy_key in zip(source_keys, legacy_keys, strict=True)
        if new_key not in claimed_keys and new_key != legacy_key
    ]
    if not pairs:
        return set()

    candidates = list({key for new_key, legacy_key in pairs for key in (new_key, legacy_key)})
    cursor.execute(
        """
        SELECT source_key
        FROM transactions
        WHERE user_id = %s
          AND source_key = ANY(%s)
        """,
        (user_id, candidates),
    )
    existing = {str(row[0]) for row in cursor.fetchall()}
    if not existing:
        return set()

    newly_claimed: set[str] = set()
    for new_key, legacy_key in pairs:
        if new_key in newly_claimed or new_key in claimed_keys:
            continue
        if new_key in existing:
            newly_claimed.add(new_key)
            continue
        if legacy_key not in existing:
            continue
        cursor.execute(
            """
            UPDATE transactions
            SET source_key = %s
            WHERE user_id = %s
              AND source_key = %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM transactions AS other
                  WHERE other.user_id = %s
                    AND other.source_key = %s
              )
            """,
            (new_key, user_id, legacy_key, user_id, new_key),
        )
        # Claim either way: migrate succeeded, or the legacy row still
        # represents this transaction and must block a duplicate insert.
        newly_claimed.add(new_key)
        existing.discard(legacy_key)
        existing.add(new_key)
    return newly_claimed


def _prepare_transaction_rows(
    transactions: list[Transaction],
    *,
    user_id: int,
    file_name: str,
    file_fingerprint: str | None,
    original_file_name: str | None,
    statement_upload_id: int | None,
    source_keys: list[str] | None,
    statement_scope: str,
) -> tuple[
    list[str],
    list[str],
    list[tuple[Transaction, str, tuple[object, ...]]],
]:
    display_name = original_file_name or file_name
    resolved_scope = statement_scope or (file_fingerprint or "")
    resolved_keys = source_keys or source_keys_for_transactions(
        transactions,
        statement_scope=resolved_scope,
    )
    if len(resolved_keys) != len(transactions):
        raise ValueError("source_keys length must match transactions")
    legacy_keys = legacy_source_keys_for_transactions(
        transactions,
        statement_scope=resolved_scope,
    )

    prepared: list[tuple[Transaction, str, tuple[object, ...]]] = []
    for transaction, source_key in zip(transactions, resolved_keys, strict=True):
        original_amount = float(transaction["amount"])
        original_currency = normalize_supported_currency_code(
            transaction.get("currency"),
            default=DEFAULT_BASE_CURRENCY,
        )
        exchange_rate = transaction.get("exchange_rate")
        base_currency = transaction.get(
            "base_currency",
            DEFAULT_BASE_CURRENCY,
        )
        base_amount, normalized_base_currency, normalized_exchange_rate = (
            derive_base_money_fields(
                original_amount=original_amount,
                original_currency=original_currency,
                base_currency=base_currency,
                exchange_rate=(
                    float(exchange_rate) if exchange_rate is not None else None
                ),
            )
        )
        exchange_rate_date = transaction.get(
            "exchange_rate_date",
            transaction["date"] if normalized_exchange_rate is not None else None,
        )
        prepared.append(
            (
                transaction,
                source_key,
                (
                    user_id,
                    statement_upload_id,
                    transaction["date"],
                    transaction["merchant"],
                    original_amount,
                    file_name,
                    display_name,
                    _normalize_category_for_storage(
                        transaction.get("category"), original_amount
                    ),
                    file_fingerprint,
                    None,
                    original_currency,
                    original_amount,
                    original_currency,
                    base_amount,
                    normalized_base_currency,
                    normalized_exchange_rate,
                    exchange_rate_date,
                    source_key,
                ),
            )
        )
    return resolved_keys, legacy_keys, prepared


def _write_prepared_transactions(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    transactions: list[Transaction],
    resolved_keys: list[str],
    legacy_keys: list[str],
    prepared: list[tuple[Transaction, str, tuple[object, ...]]],
) -> InsertTransactionsResult:
    if not transactions:
        return InsertTransactionsResult(0, frozenset())

    claimed_keys = _backfill_legacy_source_keys(
        cursor,
        user_id=user_id,
        transactions=transactions,
        source_keys=resolved_keys,
    )
    claimed_keys |= _claim_pre_sign_fix_source_keys(
        cursor,
        user_id=user_id,
        source_keys=resolved_keys,
        legacy_keys=legacy_keys,
        already_claimed=claimed_keys,
    )
    insert_rows = [
        row
        for _transaction, source_key, row in prepared
        if source_key not in claimed_keys
    ]
    inserted_keys = _insert_transaction_rows(cursor, insert_rows)
    return InsertTransactionsResult(
        inserted_count=len(inserted_keys),
        inserted_source_keys=frozenset(inserted_keys),
    )


def insert_transactions(
    transactions: list[Transaction],
    *,
    user_id: int,
    file_name: str,
    file_fingerprint: str | None = None,
    original_file_name: str | None = None,
    statement_upload_id: int | None = None,
    source_keys: list[str] | None = None,
    statement_scope: str = "",
    database_url: str | None = None,
) -> InsertTransactionsResult:
    if not transactions:
        return InsertTransactionsResult(0, frozenset())

    resolved_keys, legacy_keys, prepared = _prepare_transaction_rows(
        transactions,
        user_id=user_id,
        file_name=file_name,
        file_fingerprint=file_fingerprint,
        original_file_name=original_file_name,
        statement_upload_id=statement_upload_id,
        source_keys=source_keys,
        statement_scope=statement_scope,
    )
    resolved_database_url = _resolve_database_url(database_url)
    with db_connection(
        resolved_database_url,
        operation="insert_transactions",
        statement_timeout_ms=None,
    ) as connection:
        with connection.cursor() as cursor:
            result = _write_prepared_transactions(
                cursor,
                user_id=user_id,
                transactions=transactions,
                resolved_keys=resolved_keys,
                legacy_keys=legacy_keys,
                prepared=prepared,
            )
        connection.commit()
    return result


def import_statement(
    *,
    user_id: int,
    original_file_name: str,
    stored_file_name: str,
    file_fingerprint_sha256: str,
    transactions: list[Transaction],
    parse_status: str,
    parse_reason: str | None = None,
    parse_error: str | None = None,
    currency: str | None = None,
    source_keys: list[str] | None = None,
    statement_scope: str = "",
    database_url: str | None = None,
) -> tuple[StatementUploadRecord, InsertTransactionsResult]:
    """Save one statement upload and its transactions in a single commit.

    A failure rolls both back, so the same file can be imported again.
    """
    resolved_database_url = _resolve_database_url(database_url)
    try:
        with db_connection(
            resolved_database_url,
            operation="import_statement",
            statement_timeout_ms=None,
        ) as connection:
            with connection.cursor() as cursor:
                upload_record = _insert_statement_upload_row(
                    cursor,
                    user_id=user_id,
                    original_file_name=original_file_name,
                    stored_file_name=stored_file_name,
                    file_fingerprint_sha256=file_fingerprint_sha256,
                    parse_status=parse_status,
                    parse_reason=parse_reason,
                    parse_error=parse_error,
                    currency=currency,
                )
                insert_result = _write_statement_transactions(
                    cursor,
                    user_id=user_id,
                    upload_record=upload_record,
                    stored_file_name=stored_file_name,
                    file_fingerprint_sha256=file_fingerprint_sha256,
                    original_file_name=original_file_name,
                    transactions=transactions,
                    source_keys=source_keys,
                    statement_scope=statement_scope,
                )
            connection.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise DuplicateStatementUploadError(file_fingerprint_sha256) from exc

    return upload_record, insert_result


def _write_statement_transactions(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int,
    upload_record: StatementUploadRecord,
    stored_file_name: str,
    file_fingerprint_sha256: str,
    original_file_name: str,
    transactions: list[Transaction],
    source_keys: list[str] | None,
    statement_scope: str,
) -> InsertTransactionsResult:
    if not transactions:
        return InsertTransactionsResult(0, frozenset())
    resolved_keys, legacy_keys, prepared = _prepare_transaction_rows(
        transactions,
        user_id=user_id,
        file_name=stored_file_name,
        file_fingerprint=file_fingerprint_sha256,
        original_file_name=original_file_name,
        statement_upload_id=upload_record["id"],
        source_keys=source_keys,
        statement_scope=statement_scope or file_fingerprint_sha256,
    )
    return _write_prepared_transactions(
        cursor,
        user_id=user_id,
        transactions=transactions,
        resolved_keys=resolved_keys,
        legacy_keys=legacy_keys,
        prepared=prepared,
    )


def import_statements(
    *,
    user_id: int,
    statements: list[StatementImport],
    database_url: str | None = None,
) -> list[tuple[StatementUploadRecord, InsertTransactionsResult]]:
    """Save every statement in one transaction.

    An existing upload for the same fingerprint is reused. Transaction rows
    already stored are left in place. A failure rolls the whole run back.
    """
    if not statements:
        return []

    resolved_database_url = _resolve_database_url(database_url)
    results: list[tuple[StatementUploadRecord, InsertTransactionsResult]] = []
    with db_connection(
        resolved_database_url,
        operation="import_statements",
        statement_timeout_ms=None,
    ) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                upload_record = _reuse_statement_upload_row(
                    cursor,
                    user_id=user_id,
                    original_file_name=statement.original_file_name,
                    stored_file_name=statement.stored_file_name,
                    file_fingerprint_sha256=(
                        statement.file_fingerprint_sha256
                    ),
                    parse_status=statement.parse_status,
                    parse_reason=None,
                    parse_error=None,
                    currency=statement.currency,
                )
                insert_result = _write_statement_transactions(
                    cursor,
                    user_id=user_id,
                    upload_record=upload_record,
                    stored_file_name=statement.stored_file_name,
                    file_fingerprint_sha256=(
                        statement.file_fingerprint_sha256
                    ),
                    original_file_name=statement.original_file_name,
                    transactions=statement.transactions,
                    source_keys=statement.source_keys,
                    statement_scope=statement.statement_scope,
                )
                results.append((upload_record, insert_result))
        connection.commit()
    return results


def source_keys_owned_by_upload(
    *,
    user_id: int,
    statement_upload_id: int,
    source_keys: list[str],
    database_url: str | None = None,
) -> frozenset[str]:
    """Return which of ``source_keys`` this merge created on the upload.

    Matches rows whose ``file_name`` is a batch-import path so a shared PDF
    upload fingerprint does not link batch review rows to PDF-owned charges
    (#76). Still works when the upload record itself is not named
    ``batch-import/…`` because an earlier PDF upload reused the fingerprint.
    """
    if not source_keys:
        return frozenset()

    resolved_database_url = _resolve_database_url(database_url)
    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT source_key
            FROM transactions
            WHERE user_id = %s
              AND statement_upload_id = %s
              AND source_key = ANY(%s)
              AND file_name LIKE 'batch-import/%%'
            """,
            (user_id, statement_upload_id, list(source_keys)),
        )
        return frozenset(str(row[0]) for row in cursor.fetchall())


_TRANSACTION_INSERT_COLUMNS = (
    "user_id",
    "statement_upload_id",
    "date",
    "merchant",
    "amount",
    "file_name",
    "original_file_name",
    "category",
    "file_fingerprint",
    "payment_method",
    "currency",
    "original_amount",
    "original_currency",
    "base_amount",
    "base_currency",
    "exchange_rate",
    "exchange_rate_date",
    "source_key",
)


def _insert_transaction_rows(
    cursor: psycopg.Cursor[Any],
    insert_rows: list[tuple[object, ...]],
) -> set[str]:
    """Batch-insert rows and return the source_keys that were newly created."""
    if not insert_rows:
        return set()

    column_count = len(_TRANSACTION_INSERT_COLUMNS)
    row_placeholder = "(" + ", ".join(["%s"] * column_count) + ")"
    values_sql = ", ".join(row_placeholder for _ in insert_rows)
    flat_params = [value for row in insert_rows for value in row]
    columns_sql = ", ".join(_TRANSACTION_INSERT_COLUMNS)
    cursor.execute(
        f"""
        INSERT INTO transactions (
            {columns_sql}
        )
        VALUES {values_sql}
        ON CONFLICT (user_id, source_key)
            WHERE source_key IS NOT NULL
        DO NOTHING
        RETURNING source_key
        """,
        flat_params,
    )
    return {str(row[0]) for row in cursor.fetchall()}


def insert_manual_transaction(
    *,
    user_id: int,
    transaction_date: str,
    merchant: str,
    original_amount: float,
    original_currency: str = DEFAULT_BASE_CURRENCY,
    base_currency: str = DEFAULT_BASE_CURRENCY,
    exchange_rate: float | None = None,
    exchange_rate_date: str | None = None,
    category: str | None = None,
    payment_method: str | None = None,
    database_url: str | None = None,
) -> StoredTransaction:
    resolved_database_url = _resolve_database_url(database_url)
    normalized_original_currency = normalize_supported_currency_code(
        original_currency,
        default=DEFAULT_BASE_CURRENCY,
    )
    base_amount, normalized_base_currency, normalized_exchange_rate = (
        derive_base_money_fields(
            original_amount=original_amount,
            original_currency=normalized_original_currency,
            base_currency=base_currency,
            exchange_rate=exchange_rate,
        )
    )
    resolved_exchange_rate_date = (
        exchange_rate_date
        if normalized_exchange_rate is not None
        else None
    )
    normalized_merchant = " ".join(merchant.split())
    inferred_category = (
        category.strip()
        if category and category.strip()
        else categorize_merchant(normalized_merchant, original_amount)
    )
    normalized_category = _normalize_category_for_storage(
        inferred_category,
        original_amount,
    )
    normalized_payment_method = (
        payment_method.strip() if payment_method and payment_method.strip() else None
    )
    file_name = "manual-entry"
    original_file_name = (
        f"Manual entry ({normalized_payment_method})"
        if normalized_payment_method
        else "Manual entry"
    )

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
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
                    currency,
                    original_amount,
                    original_currency,
                    base_amount,
                    base_currency,
                    exchange_rate,
                    exchange_rate_date
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                RETURNING
                    {STORED_TRANSACTION_SELECT_COLUMNS}
                """,
                (
                    user_id,
                    None,
                    transaction_date,
                    normalized_merchant,
                    original_amount,
                    file_name,
                    original_file_name,
                    normalized_category,
                    None,
                    normalized_payment_method,
                    normalized_original_currency,
                    original_amount,
                    normalized_original_currency,
                    base_amount,
                    normalized_base_currency,
                    normalized_exchange_rate,
                    resolved_exchange_rate_date,
                ),
            )
            row = cursor.fetchone()
        connection.commit()

    if row is None:
        raise RuntimeError("Failed to insert manual transaction.")

    return _stored_transaction_from_row(row)


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
    resolved_database_url = _resolve_database_url(database_url)
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

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    merchant,
                    COALESCE(original_amount, amount) AS amount,
                    COALESCE(category, '') AS category
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
            else:
                # A merchant rename must not replace a category the user chose.
                effective_category = str(existing[2])

            cursor.execute(
                f"""
                UPDATE transactions
                SET
                    merchant = %s,
                    category = %s
                WHERE id = %s
                  AND user_id = %s
                RETURNING
                    {STORED_TRANSACTION_SELECT_COLUMNS}
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

    return _stored_transaction_from_row(row)


def delete_transaction(
    *,
    user_id: int,
    transaction_id: int,
    database_url: str | None = None,
) -> bool:
    resolved_database_url = _resolve_database_url(database_url)
    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM transactions
                WHERE id = %s
                  AND user_id = %s
                RETURNING id
                """,
                (transaction_id, user_id),
            )
            deleted = cursor.fetchone() is not None
        connection.commit()
    return deleted


def delete_transaction_by_source_key(
    *,
    user_id: int,
    source_key: str,
    database_url: str | None = None,
) -> bool:
    """Remove the one dashboard row created from a merged batch row."""
    if not source_key.strip():
        return False
    resolved_database_url = _resolve_database_url(database_url)
    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM transactions
                WHERE user_id = %s
                  AND source_key = %s
                RETURNING id
                """,
                (user_id, source_key),
            )
            deleted = cursor.fetchone() is not None
        connection.commit()
    return deleted


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


def count_statement_uploads(
    *,
    user_id: int,
    database_url: str | None = None,
) -> int:
    """All statement uploads for this user, including rows with no transactions."""
    resolved_database_url = _resolve_database_url(database_url)
    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT COUNT(*) FROM statement_uploads WHERE user_id = %s",
            (user_id,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


def get_transactions_summary(
    *,
    user_id: int,
    range_key: TimeRange = "all",
    display_currency: str | None = None,
    database_url: str | None = None,
) -> TransactionsSummary:
    from backend.spending import iter_spending_partition, summary_from_totals

    requested = (display_currency or "USD").strip().upper()
    target_currency: Literal["USD", "TWD"] = (
        "TWD" if requested == "TWD" else "USD"
    )
    total_spending = 0.0
    transaction_count = 0
    unconverted_count = 0
    unconverted_currencies: set[str] = set()
    for kind, row in iter_spending_partition(
        iter_transactions(
            user_id=user_id,
            range_key=range_key,
            database_url=database_url,
        ),
        target_currency,
    ):
        if kind == "model":
            total_spending += float(row["amount"])
            transaction_count += 1
        else:
            unconverted_count += 1
            unconverted_currencies.add(str(row["original_currency"]))
    return summary_from_totals(
        total_spending=total_spending,
        transaction_count=transaction_count,
        statement_count=count_statement_uploads(
            user_id=user_id,
            database_url=database_url,
        ),
        unconverted_count=unconverted_count,
        unconverted_currencies=sorted(unconverted_currencies),
    )


def get_recent_uploads(
    *,
    user_id: int,
    limit: int = 5,
    database_url: str | None = None,
) -> list[RecentUpload]:
    resolved_database_url = _resolve_database_url(database_url)

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT
                statement_uploads.id,
                statement_uploads.stored_file_name,
                statement_uploads.original_file_name,
                COUNT(transactions.id) AS transaction_count,
                COALESCE(SUM(COALESCE(transactions.original_amount, transactions.amount)), 0)
                    AS total_amount,
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

        upload_ids = [int(row[0]) for row in rows]
        currency_totals_by_upload: dict[int, list[RecentUploadCurrencyTotal]] = {
            upload_id: [] for upload_id in upload_ids
        }
        if upload_ids:
            cursor.execute(
                """
                SELECT
                    statement_upload_id,
                    COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
                        AS currency,
                    COALESCE(SUM(COALESCE(original_amount, amount)), 0)
                        AS total_amount,
                    COUNT(*) AS transaction_count
                FROM transactions
                WHERE statement_upload_id = ANY(%s)
                GROUP BY
                    statement_upload_id,
                    COALESCE(NULLIF(original_currency, ''), NULLIF(currency, ''), 'TWD')
                ORDER BY statement_upload_id ASC, currency ASC
                """,
                (upload_ids,),
            )
            for (
                statement_upload_id,
                currency,
                total_amount,
                transaction_count,
            ) in cursor.fetchall():
                currency_totals_by_upload[int(statement_upload_id)].append(
                    {
                        "currency": str(currency),
                        "total_amount": float(total_amount),
                        "transaction_count": int(transaction_count),
                    }
                )

    return [
        {
            "file_name": str(file_name),
            "display_name": str(display_name),
            "transaction_count": int(transaction_count),
            "total_amount": float(total_amount),
            "currency_totals": currency_totals_by_upload.get(int(upload_id), []),
            "uploaded_at": _iso_datetime(uploaded_at),
            "parse_status": str(parse_status),
        }
        for (
            upload_id,
            file_name,
            display_name,
            transaction_count,
            total_amount,
            uploaded_at,
            parse_status,
        ) in rows
    ]


def _transactions_query(
    *,
    user_id: int,
    range_key: TimeRange,
    limit: int | None,
    date_order: Literal["asc", "desc"] = "desc",
) -> tuple[str, tuple[object, ...]]:
    # Backend streaming consumers retain newest-first; the Transactions API
    # explicitly requests oldest-first ordering before applying LIMIT.
    direction = "ASC" if date_order == "asc" else "DESC"
    date_filter_clause, range_params = _build_user_date_filter_clause(user_id, range_key)
    transaction_query = f"""
        SELECT
            {STORED_TRANSACTION_SELECT_COLUMNS}
        FROM transactions
        {date_filter_clause}
        ORDER BY
            CASE
                WHEN date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                    THEN date::date
                ELSE NULL
            END {direction} NULLS LAST,
            id {direction}
        """
    query_params: tuple[object, ...] = range_params
    if limit is not None:
        transaction_query += "\n        LIMIT %s"
        query_params = (*range_params, limit)
    return transaction_query, query_params


def iter_transactions(
    *,
    user_id: int,
    range_key: TimeRange = "all",
    limit: int | None = None,
    database_url: str | None = None,
):
    """Stream rows. A named cursor keeps the result out of one Python list."""
    resolved_database_url = _resolve_database_url(database_url)
    transaction_query, query_params = _transactions_query(
        user_id=user_id,
        range_key=range_key,
        limit=limit,
    )
    with db_connection(resolved_database_url, operation="iter_transactions") as connection:
        cursor = connection.cursor(name="transaction_scan")
        cursor.itersize = 200
        try:
            cursor.execute(transaction_query, query_params)
            for row in cursor:
                yield _stored_transaction_from_row(row)
        finally:
            cursor.close()


def get_transactions(
    *,
    user_id: int,
    range_key: TimeRange = "all",
    limit: int | None = 5000,
    database_url: str | None = None,
) -> list[StoredTransaction]:
    resolved_database_url = _resolve_database_url(database_url)
    transaction_query, query_params = _transactions_query(
        user_id=user_id,
        range_key=range_key,
        limit=limit,
        date_order="asc",
    )
    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(transaction_query, query_params)
        rows = cursor.fetchall()

    return [_stored_transaction_from_row(row) for row in rows]


def get_existing_upload_by_fingerprint(
    *,
    user_id: int,
    file_fingerprint_sha256: str,
    database_url: str | None = None,
) -> ExistingUpload | None:
    resolved_database_url = _resolve_database_url(database_url)

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
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


class StatementUploadDetail(ExistingUpload):
    stored_file_name: str


def get_statement_upload_for_user(
    *,
    user_id: int,
    upload_id: int,
    database_url: str | None = None,
) -> StatementUploadDetail | None:
    resolved_database_url = _resolve_database_url(database_url)

    with (
        db_connection(resolved_database_url) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT
                id,
                stored_file_name,
                original_file_name,
                uploaded_at,
                parse_status
            FROM statement_uploads
            WHERE id = %s
              AND user_id = %s
            """,
            (upload_id, user_id),
        )
        row = cursor.fetchone()

    if not row:
        return None

    upload_id_value, stored_file_name, display_name, uploaded_at, parse_status = row
    return {
        "id": int(upload_id_value),
        "file_name": str(stored_file_name),
        "stored_file_name": str(stored_file_name),
        "display_name": str(display_name),
        "uploaded_at": _iso_datetime(uploaded_at),
        "parse_status": str(parse_status),
    }


def delete_statement_upload_for_user(
    *,
    user_id: int,
    upload_id: int,
    database_url: str | None = None,
) -> StatementUploadDetail | None:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
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
                WHERE id = %s
                  AND user_id = %s
                """,
                (upload_id, user_id),
            )
            row = cursor.fetchone()
            if not row:
                return None

            cursor.execute(
                """
                DELETE FROM transactions
                WHERE user_id = %s
                  AND statement_upload_id = %s
                """,
                (user_id, upload_id),
            )
            cursor.execute(
                """
                DELETE FROM statement_uploads
                WHERE id = %s
                  AND user_id = %s
                """,
                (upload_id, user_id),
            )
        connection.commit()

    upload_id_value, stored_file_name, display_name, uploaded_at, parse_status = row
    return {
        "id": int(upload_id_value),
        "file_name": str(stored_file_name),
        "stored_file_name": str(stored_file_name),
        "display_name": str(display_name),
        "uploaded_at": _iso_datetime(uploaded_at),
        "parse_status": str(parse_status),
    }


def purge_expired_audit_logs(
    *,
    retention_days: int,
    database_url: str | None = None,
) -> int:
    resolved_database_url = _resolve_database_url(database_url)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM audit_logs
                WHERE occurred_at < NOW() - make_interval(days => %s)
                """,
                (retention_days,),
            )
            deleted_count = int(cursor.rowcount)
        connection.commit()

    return deleted_count


def purge_expired_statement_uploads(
    *,
    retention_days: int,
    database_url: str | None = None,
) -> list[StatementUploadDetail]:
    resolved_database_url = _resolve_database_url(database_url)
    cutoff_days = max(retention_days, 1)

    with db_connection(resolved_database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    user_id,
                    stored_file_name,
                    original_file_name,
                    uploaded_at,
                    parse_status
                FROM statement_uploads
                WHERE uploaded_at < NOW() - make_interval(days => %s)
                ORDER BY uploaded_at ASC, id ASC
                """,
                (cutoff_days,),
            )
            expired_rows = cursor.fetchall()

            deleted_uploads: list[StatementUploadDetail] = []
            for (
                upload_id,
                user_id,
                stored_file_name,
                display_name,
                uploaded_at,
                parse_status,
            ) in expired_rows:
                cursor.execute(
                    """
                    DELETE FROM transactions
                    WHERE statement_upload_id = %s
                      AND user_id IS NOT DISTINCT FROM %s
                    """,
                    (upload_id, user_id),
                )
                cursor.execute(
                    """
                    DELETE FROM statement_uploads
                    WHERE id = %s
                    """,
                    (upload_id,),
                )
                deleted_uploads.append(
                    {
                        "id": int(upload_id),
                        "file_name": str(stored_file_name),
                        "stored_file_name": str(stored_file_name),
                        "display_name": str(display_name),
                        "uploaded_at": _iso_datetime(uploaded_at),
                        "parse_status": str(parse_status),
                    }
                )
        connection.commit()

    return deleted_uploads
