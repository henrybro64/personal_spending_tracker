from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path
from typing import Any

import psycopg

from backend.db import migration_connection

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_NAME_PATTERN = re.compile(r"^(\d+)_.+\.sql$")


def _migration_files() -> list[Path]:
    files = [
        path
        for path in MIGRATIONS_DIR.glob("*.sql")
        if _MIGRATION_NAME_PATTERN.match(path.name)
    ]
    return sorted(files, key=lambda path: int(_MIGRATION_NAME_PATTERN.match(path.name).group(1)))


def _ensure_schema_migrations_table(cursor: psycopg.Cursor[Any]) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _applied_versions(cursor: psycopg.Cursor[Any]) -> set[str]:
    cursor.execute("SELECT version FROM schema_migrations")
    return {str(row[0]) for row in cursor.fetchall()}


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


def migrate_database(database_url: str) -> list[str]:
    applied_now: list[str] = []

    with migration_connection(database_url) as connection:
        with connection.cursor() as cursor:
            _ensure_schema_migrations_table(cursor)
            applied_versions = _applied_versions(cursor)

            for migration_path in _migration_files():
                version = migration_path.stem
                if version in applied_versions:
                    continue

                sql = migration_path.read_text(encoding="utf-8")
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied_now.append(version)

            if "002_backfill_statement_uploads" not in applied_versions:
                _backfill_statement_uploads(cursor)
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version)
                    VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    ("002_backfill_statement_uploads",),
                )
                applied_now.append("002_backfill_statement_uploads")

        connection.commit()

    return applied_now


def main() -> None:
    from backend.database import get_database_url

    applied = migrate_database(get_database_url())
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database schema is up to date.")


if __name__ == "__main__":
    main()
