"""Report and optionally repair rows left by earlier import bugs.

The default path only reads. Each repair is a separate flag and, with
``--apply``, one database transaction. ``--apply`` also requires
``--i-have-a-backup``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg

from backend.batch_import.paths import default_sqlite_output_path
from backend.currency import DEFAULT_BASE_CURRENCY
from backend.database import _resolve_database_url
from backend.db import db_connection
from backend.parser import Transaction
from backend.transaction_exclusions import (
    PAYMENT_CATEGORY,
    is_card_payment_merchant,
)
from backend.transaction_source import (
    card_last4_from_statement,
    source_keys_for_transactions,
)

BACKUP_REMINDER = (
    "Run scripts/backup-spending-tracker.sh before repairing data."
)
_EXAMPLE_LIMIT = 5
_DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
# A posting date the upload parser used to keep in the merchant (#98).
_DATE_PREFIXED_MERCHANT_PATTERN = (
    r"^([0-9]{2}/[0-9]{2}(/[0-9]{2,4})?|[0-9]{4}-[0-9]{2}-[0-9]{2})[[:space:]]"
)


class RepairRefused(Exception):
    """``--apply`` was set without ``--i-have-a-backup``."""


@dataclass(frozen=True)
class DuplicateGroup:
    date: str
    merchant: str
    amount: str
    currency: str
    upload_names: tuple[str, ...]
    from_batch_merge: bool


@dataclass(frozen=True)
class FlaggedRow:
    transaction_id: int
    label: str
    detail: str


@dataclass(frozen=True)
class SpendingDataReport:
    duplicate_groups: tuple[DuplicateGroup, ...]
    batch_uploads: tuple[str, ...]
    payments_stored_as_spending: tuple[FlaggedRow, ...]
    future_dated_rows: tuple[FlaggedRow, ...]
    unreadable_dates: tuple[FlaggedRow, ...]
    date_prefixed_merchants: tuple[FlaggedRow, ...]
    # (user_id, row count), ordered by user_id; None is a legacy unowned row.
    date_prefixed_merchant_counts: tuple[tuple[int | None, int], ...]

    @property
    def batch_duplicate_groups(self) -> int:
        return sum(1 for group in self.duplicate_groups if group.from_batch_merge)


@dataclass(frozen=True)
class RepairResult:
    applied: bool
    changed_ids: tuple[int, ...]
    conflicts: tuple[str, ...]
    notes: tuple[str, ...]


def batch_sqlite_path_for_user(user_id: int):
    return default_sqlite_output_path().with_name(
        f"batch_imports_user_{user_id}.sqlite3"
    )


def report_spending_data(
    *,
    user_id: int | None = None,
    database_url: str | None = None,
    today: date | None = None,
) -> SpendingDataReport:
    resolved_url = _resolve_database_url(database_url)
    as_of = today or date.today()
    with db_connection(resolved_url, operation="check_spending_data") as connection:
        with connection.cursor() as cursor:
            duplicates = _duplicate_groups(cursor, user_id=user_id)
            batch_uploads = _batch_uploads(cursor, user_id=user_id)
            payments = _payment_rows(cursor, user_id=user_id)
            future_rows = _future_rows(cursor, user_id=user_id, today=as_of)
            unreadable = _unreadable_dates(cursor, user_id=user_id)
            date_prefixed, date_prefixed_counts = _date_prefixed_merchants(
                cursor, user_id=user_id
            )
    return SpendingDataReport(
        duplicate_groups=tuple(duplicates),
        batch_uploads=tuple(batch_uploads),
        payments_stored_as_spending=tuple(payments),
        future_dated_rows=tuple(future_rows),
        unreadable_dates=tuple(unreadable),
        date_prefixed_merchants=tuple(date_prefixed),
        date_prefixed_merchant_counts=tuple(date_prefixed_counts),
    )


def format_report(report: SpendingDataReport) -> str:
    lines = [
        f"Possible duplicates: {len(report.duplicate_groups)} "
        f"({report.batch_duplicate_groups} from a batch merge)",
    ]
    for group in report.duplicate_groups[:_EXAMPLE_LIMIT]:
        names = ", ".join(group.upload_names)
        lines.append(
            f"  {group.date} {group.merchant} {group.amount} "
            f"{group.currency} uploads={names}"
        )
    lines.append(f"Batch-merged uploads: {len(report.batch_uploads)}")
    for name in report.batch_uploads[:_EXAMPLE_LIMIT]:
        lines.append(f"  {name}")
    lines.append(
        "Payments stored as spending: "
        f"{len(report.payments_stored_as_spending)}"
    )
    for row in report.payments_stored_as_spending[:_EXAMPLE_LIMIT]:
        lines.append(f"  id={row.transaction_id} {row.label} {row.detail}")
    lines.append(f"Future-dated rows: {len(report.future_dated_rows)}")
    for row in report.future_dated_rows[:_EXAMPLE_LIMIT]:
        lines.append(
            f"  id={row.transaction_id} date={row.label} upload={row.detail}"
        )
    lines.append(f"Unreadable dates: {len(report.unreadable_dates)}")
    for row in report.unreadable_dates[:_EXAMPLE_LIMIT]:
        lines.append(f"  id={row.transaction_id} date={row.label}")
    lines.append(
        "Upload rows with a date in the merchant: "
        f"{len(report.date_prefixed_merchants)}"
    )
    for row_user_id, count in report.date_prefixed_merchant_counts:
        lines.append(f"  user_id={row_user_id}: {count}")
    for row in report.date_prefixed_merchants[:_EXAMPLE_LIMIT]:
        lines.append(
            f"  id={row.transaction_id} merchant={row.label} upload={row.detail}"
        )
    return "\n".join(lines)


def recategorize_payments(
    *,
    user_id: int | None = None,
    apply: bool = False,
    i_have_a_backup: bool = False,
    database_url: str | None = None,
) -> RepairResult:
    """Set category Payment / Credit on rows the payment rule would exclude.

    Also recomputes ``source_key`` with the payment amount folding so a later
    import matches this row instead of inserting a duplicate.
    """
    _require_backup(apply=apply, i_have_a_backup=i_have_a_backup)
    resolved_url = _resolve_database_url(database_url)
    notes: list[str] = []
    with db_connection(resolved_url, operation="recategorize_payments") as connection:
        with connection.cursor() as cursor:
            flagged = _payment_rows(cursor, user_id=user_id)
            changed_ids = tuple(row.transaction_id for row in flagged)
            key_updates, key_notes = _payment_source_key_updates(
                cursor,
                transaction_ids=changed_ids,
            )
            notes.extend(key_notes)
            if apply and changed_ids:
                cursor.execute(
                    """
                    UPDATE transactions
                    SET category = %s
                    WHERE id = ANY(%s)
                    """,
                    (PAYMENT_CATEGORY, list(changed_ids)),
                )
                for row_id, source_key in key_updates:
                    cursor.execute(
                        """
                        UPDATE transactions
                        SET source_key = %s
                        WHERE id = %s
                        """,
                        (source_key, row_id),
                    )
        if apply:
            connection.commit()
    return RepairResult(
        applied=apply,
        changed_ids=changed_ids,
        conflicts=(),
        notes=tuple(notes),
    )


def delete_batch_merges(
    *,
    user_id: int | None = None,
    apply: bool = False,
    i_have_a_backup: bool = False,
    database_url: str | None = None,
) -> RepairResult:
    """Delete batch-import uploads and their transactions.

    Also clears ``postgres_synced`` in that user's batch SQLite file so the
    statements can be merged again with the fixed code.
    """
    _require_backup(apply=apply, i_have_a_backup=i_have_a_backup)
    resolved_url = _resolve_database_url(database_url)
    notes: list[str] = []
    with db_connection(resolved_url, operation="delete_batch_merges") as connection:
        with connection.cursor() as cursor:
            uploads = _batch_upload_ids(cursor, user_id=user_id)
            upload_ids = [row[0] for row in uploads]
            owner_ids = sorted({row[1] for row in uploads})
            # Keep the requested user for SQLite cleanup even when Postgres
            # uploads are already gone (retry after a failed clear).
            if user_id is not None:
                owner_ids = sorted({*owner_ids, user_id})
            if not upload_ids:
                changed_ids: tuple[int, ...] = ()
            else:
                cursor.execute(
                    """
                    SELECT id
                    FROM transactions
                    WHERE statement_upload_id = ANY(%s)
                    ORDER BY id
                    """,
                    (upload_ids,),
                )
                changed_ids = tuple(int(row[0]) for row in cursor.fetchall())
                if apply:
                    cursor.execute(
                        """
                        DELETE FROM transactions
                        WHERE statement_upload_id = ANY(%s)
                        """,
                        (upload_ids,),
                    )
                    cursor.execute(
                        """
                        DELETE FROM statement_uploads
                        WHERE id = ANY(%s)
                        """,
                        (upload_ids,),
                    )
        if apply:
            connection.commit()
    # Postgres and SQLite cannot share one transaction. If clearing synced
    # flags fails after the deletes, tell the operator how to finish.
    if apply:
        for owner_id in owner_ids:
            try:
                notes.append(_clear_postgres_synced(owner_id))
            except (OSError, sqlite3.Error) as exc:
                path = batch_sqlite_path_for_user(owner_id)
                notes.append(
                    f"Postgres batch-import rows were deleted for user "
                    f"{owner_id}, but clearing postgres_synced failed "
                    f"({exc}). Re-run --delete-batch-merges, or clear "
                    f"{path} manually before merging again."
                )
    return RepairResult(
        applied=apply,
        changed_ids=changed_ids,
        conflicts=(),
        notes=tuple(notes),
    )


def backfill_source_keys(
    *,
    user_id: int | None = None,
    apply: bool = False,
    i_have_a_backup: bool = False,
    database_url: str | None = None,
) -> RepairResult:
    """Fill source_key on imported rows that do not have one.

    Occurrence numbers are assigned against every row on the statement,
    including rows that already have a key, so a partially keyed upload
    still receives the correct remaining keys. Card last4 is taken from
    the statement file name when present so keys match a later re-import.
    """
    _require_backup(apply=apply, i_have_a_backup=i_have_a_backup)
    resolved_url = _resolve_database_url(database_url)
    with db_connection(resolved_url, operation="backfill_source_keys") as connection:
        with connection.cursor() as cursor:
            statement_rows = _imported_rows_for_source_keys(
                cursor,
                user_id=user_id,
            )
            taken = _existing_source_keys(cursor, user_id=user_id)
            updates, conflicts = _plan_source_key_updates(statement_rows, taken)
            if apply and updates:
                cursor.executemany(
                    """
                    UPDATE transactions
                    SET source_key = %s
                    WHERE id = %s
                      AND source_key IS NULL
                    """,
                    [(source_key, row_id) for row_id, source_key in updates],
                )
        if apply:
            connection.commit()
    return RepairResult(
        applied=apply,
        changed_ids=tuple(row_id for row_id, _source_key in updates),
        conflicts=tuple(conflicts),
        notes=(),
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Report and optionally repair stored spending data.",
    )
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--i-have-a-backup", action="store_true")
    parser.add_argument("--recategorize-payments", action="store_true")
    parser.add_argument("--delete-batch-merges", action="store_true")
    parser.add_argument("--backfill-source-keys", action="store_true")
    args = parser.parse_args(argv)

    print(BACKUP_REMINDER)
    if args.apply and not args.i_have_a_backup:
        print("Refusing to apply without --i-have-a-backup.")
        return 2

    report = report_spending_data(user_id=args.user_id)
    print(format_report(report))

    repairs = (
        (args.recategorize_payments, recategorize_payments, "recategorize"),
        (args.delete_batch_merges, delete_batch_merges, "delete batch merges"),
        (args.backfill_source_keys, backfill_source_keys, "backfill source keys"),
    )
    for selected, function, label in repairs:
        if not selected:
            continue
        result = function(
            user_id=args.user_id,
            apply=args.apply,
            i_have_a_backup=args.i_have_a_backup,
        )
        action = "updated" if result.applied else "would update"
        print(f"{label}: {action} {len(result.changed_ids)} row(s)")
        for conflict in result.conflicts:
            print(f"  conflict: {conflict}")
        for note in result.notes:
            print(f"  {note}")
    return 0


def _require_backup(*, apply: bool, i_have_a_backup: bool) -> None:
    if apply and not i_have_a_backup:
        raise RepairRefused(
            "Refusing to apply without --i-have-a-backup."
        )


def _user_filter(
    user_id: int | None,
    column: str,
) -> tuple[str, list[Any]]:
    if user_id is None:
        return "", []
    return f" AND {column} = %s", [user_id]


def _duplicate_groups(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[DuplicateGroup]:
    user_sql, params = _user_filter(user_id, "t.user_id")
    cursor.execute(
        f"""
        SELECT
            t.date,
            t.merchant,
            COALESCE(t.original_amount, t.amount) AS amount,
            COALESCE(
                NULLIF(t.original_currency, ''),
                NULLIF(t.currency, ''),
                'TWD'
            ) AS currency,
            array_agg(DISTINCT COALESCE(u.stored_file_name, t.file_name))
                AS upload_names
        FROM transactions t
        LEFT JOIN statement_uploads u
          ON u.id = t.statement_upload_id
        WHERE t.statement_upload_id IS NOT NULL
        {user_sql}
        GROUP BY t.user_id, t.date, t.merchant, 3, 4
        HAVING COUNT(DISTINCT t.statement_upload_id) > 1
        ORDER BY t.date, t.merchant
        """,
        params,
    )
    groups: list[DuplicateGroup] = []
    for row_date, merchant, amount, currency, upload_names in cursor.fetchall():
        names = tuple(sorted(str(name) for name in upload_names if name))
        groups.append(
            DuplicateGroup(
                date=str(row_date),
                merchant=str(merchant),
                amount=f"{amount:.2f}",
                currency=str(currency),
                upload_names=names,
                from_batch_merge=any(
                    name.startswith("batch-import/") for name in names
                ),
            )
        )
    return groups


def _batch_uploads(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[str]:
    user_sql, params = _user_filter(user_id, "user_id")
    cursor.execute(
        f"""
        SELECT stored_file_name
        FROM statement_uploads
        WHERE stored_file_name LIKE 'batch-import/%%'
        {user_sql}
        ORDER BY stored_file_name
        """,
        params,
    )
    return [str(row[0]) for row in cursor.fetchall()]


def _batch_upload_ids(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[tuple[int, int]]:
    user_sql, params = _user_filter(user_id, "user_id")
    cursor.execute(
        f"""
        SELECT id, user_id
        FROM statement_uploads
        WHERE stored_file_name LIKE 'batch-import/%%'
          AND user_id IS NOT NULL
        {user_sql}
        ORDER BY id
        """,
        params,
    )
    return [(int(row[0]), int(row[1])) for row in cursor.fetchall()]


def _payment_rows(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[FlaggedRow]:
    user_sql, params = _user_filter(user_id, "user_id")
    cursor.execute(
        f"""
        SELECT id, merchant, COALESCE(category, '')
        FROM transactions
        WHERE COALESCE(category, '') <> %s
        {user_sql}
        ORDER BY date, id
        """,
        [PAYMENT_CATEGORY, *params],
    )
    flagged: list[FlaggedRow] = []
    for row_id, merchant, category in cursor.fetchall():
        if not is_card_payment_merchant(str(merchant)):
            continue
        flagged.append(
            FlaggedRow(
                transaction_id=int(row_id),
                label=str(merchant),
                detail=f"category={category}",
            )
        )
    return flagged


def _future_rows(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
    today: date,
) -> list[FlaggedRow]:
    user_sql, params = _user_filter(user_id, "t.user_id")
    cursor.execute(
        f"""
        SELECT
            t.id,
            t.date,
            COALESCE(u.stored_file_name, t.file_name)
        FROM transactions t
        LEFT JOIN statement_uploads u
          ON u.id = t.statement_upload_id
        WHERE t.date ~ %s
          AND t.date > %s
        {user_sql}
        ORDER BY t.date, t.id
        """,
        [_DATE_PATTERN, today.isoformat(), *params],
    )
    return [
        FlaggedRow(
            transaction_id=int(row_id),
            label=str(row_date),
            detail=str(upload_name),
        )
        for row_id, row_date, upload_name in cursor.fetchall()
    ]


def _unreadable_dates(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[FlaggedRow]:
    user_sql, params = _user_filter(user_id, "user_id")
    cursor.execute(
        f"""
        SELECT id, date
        FROM transactions
        WHERE date !~ %s
        {user_sql}
        ORDER BY id
        """,
        [_DATE_PATTERN, *params],
    )
    return [
        FlaggedRow(
            transaction_id=int(row_id),
            label=str(row_date),
            detail="",
        )
        for row_id, row_date in cursor.fetchall()
    ]


def _date_prefixed_merchants(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> tuple[list[FlaggedRow], list[tuple[int | None, int]]]:
    """Upload rows stored before #98 with the posting date in the merchant.

    Report only: their merchant and source key no longer match a batch merge
    of the same statement. Batch-merged and manual rows are not upload output.
    """
    user_sql, params = _user_filter(user_id, "t.user_id")
    cursor.execute(
        f"""
        SELECT
            t.id,
            t.user_id,
            t.merchant,
            COALESCE(u.stored_file_name, t.file_name, '')
        FROM transactions t
        LEFT JOIN statement_uploads u
          ON u.id = t.statement_upload_id
        WHERE t.merchant ~ %s
          AND COALESCE(t.file_name, '') NOT LIKE 'batch-import/%%'
          AND COALESCE(t.file_name, '') <> 'manual-entry'
        {user_sql}
        ORDER BY t.user_id NULLS LAST, t.id
        """,
        [_DATE_PREFIXED_MERCHANT_PATTERN, *params],
    )
    flagged: list[FlaggedRow] = []
    counts: dict[int | None, int] = {}
    for row_id, row_user_id, merchant, upload_name in cursor.fetchall():
        flagged.append(
            FlaggedRow(
                transaction_id=int(row_id),
                label=str(merchant),
                detail=str(upload_name),
            )
        )
        owner = None if row_user_id is None else int(row_user_id)
        counts[owner] = counts.get(owner, 0) + 1
    # Rows arrive ordered by user, so insertion order is already sorted.
    return flagged, list(counts.items())


def _imported_rows_for_source_keys(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> list[dict[str, Any]]:
    """All imported rows used when assigning source_key occurrences."""
    user_sql, params = _user_filter(user_id, "t.user_id")
    cursor.execute(
        f"""
        SELECT
            t.id,
            t.user_id,
            t.date,
            t.merchant,
            COALESCE(t.original_amount, t.amount) AS amount,
            COALESCE(t.category, '') AS category,
            COALESCE(
                NULLIF(t.original_currency, ''),
                NULLIF(t.currency, ''),
                %s
            ) AS currency,
            t.statement_upload_id,
            COALESCE(
                NULLIF(t.file_fingerprint, ''),
                NULLIF(u.file_fingerprint_sha256, ''),
                ''
            ) AS statement_scope,
            t.source_key,
            COALESCE(t.original_file_name, u.original_file_name, ''),
            COALESCE(t.file_name, u.stored_file_name, '')
        FROM transactions t
        LEFT JOIN statement_uploads u
          ON u.id = t.statement_upload_id
        WHERE COALESCE(t.file_name, '') <> 'manual-entry'
          AND t.user_id IS NOT NULL
        {user_sql}
        ORDER BY t.date, t.id
        """,
        [DEFAULT_BASE_CURRENCY, *params],
    )
    rows: list[dict[str, Any]] = []
    for packed in cursor.fetchall():
        (
            row_id,
            owner_id,
            row_date,
            merchant,
            amount,
            category,
            currency,
            statement_upload_id,
            statement_scope,
            source_key,
            original_file_name,
            file_name,
        ) = packed
        card_last4 = card_last4_from_statement(
            "",
            file_name=f"{original_file_name}\n{file_name}",
        )
        rows.append(
            {
                "id": int(row_id),
                "user_id": int(owner_id),
                "date": str(row_date),
                "merchant": str(merchant),
                "amount": amount,
                "category": str(category),
                "currency": str(currency),
                "statement_upload_id": statement_upload_id,
                "statement_scope": str(statement_scope),
                "source_key": None if source_key is None else str(source_key),
                "card_last4": card_last4,
            }
        )
    return rows


def _existing_source_keys(
    cursor: psycopg.Cursor[Any],
    *,
    user_id: int | None,
) -> dict[int, dict[str, int]]:
    """Map user_id -> source_key -> transaction id that owns it."""
    user_sql, params = _user_filter(user_id, "user_id")
    cursor.execute(
        f"""
        SELECT user_id, source_key, id
        FROM transactions
        WHERE source_key IS NOT NULL
          AND user_id IS NOT NULL
        {user_sql}
        """,
        params,
    )
    taken: dict[int, dict[str, int]] = {}
    for owner_id, source_key, row_id in cursor.fetchall():
        taken.setdefault(int(owner_id), {})[str(source_key)] = int(row_id)
    return taken


def _plan_source_key_updates(
    statement_rows: list[dict[str, Any]],
    taken: dict[int, dict[str, int]],
) -> tuple[list[tuple[int, str]], list[str]]:
    by_user: dict[int, list[dict[str, Any]]] = {}
    for row in statement_rows:
        by_user.setdefault(int(row["user_id"]), []).append(row)

    updates: list[tuple[int, str]] = []
    conflicts: list[str] = []
    for owner_id, rows in by_user.items():
        planned = _keys_for_user_rows(rows)
        used = dict(taken.get(owner_id, {}))
        for row, source_key in planned:
            existing_key = row.get("source_key")
            if existing_key is not None:
                # Already keyed: keep it for occurrence counting only.
                continue
            owner_of_key = used.get(source_key)
            if owner_of_key is not None and owner_of_key != int(row["id"]):
                conflicts.append(
                    "source_key conflict for transaction "
                    f"{row['id']} on {row['date']} {row['merchant']}"
                )
                break
            updates.append((int(row["id"]), source_key))
            used[source_key] = int(row["id"])
    return updates, conflicts


def _keys_for_user_rows(
    rows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        upload_id = row["statement_upload_id"]
        group_key = (
            f"upload:{upload_id}"
            if upload_id is not None
            else f"scope:{row['statement_scope']}"
        )
        groups.setdefault(group_key, []).append(row)

    planned: list[tuple[dict[str, Any], str]] = []
    for group_rows in groups.values():
        ordered = sorted(group_rows, key=lambda item: (item["date"], item["id"]))
        transactions: list[Transaction] = [
            {
                "date": str(row["date"]),
                "merchant": str(row["merchant"]),
                "amount": float(row["amount"]),
                "category": str(row["category"]),
                "currency": str(row["currency"]),
                "card_last4": str(row.get("card_last4") or ""),
            }
            for row in ordered
        ]
        scope = str(ordered[0]["statement_scope"])
        keys = source_keys_for_transactions(
            transactions,
            statement_scope=scope,
        )
        planned.extend(zip(ordered, keys, strict=True))
    planned.sort(key=lambda item: (item[0]["date"], item[0]["id"]))
    return planned


def _payment_source_key_updates(
    cursor: psycopg.Cursor[Any],
    *,
    transaction_ids: tuple[int, ...],
) -> tuple[list[tuple[int, str | None]], list[str]]:
    """Plan source_key values after switching rows to Payment / Credit."""
    if not transaction_ids:
        return [], []

    cursor.execute(
        """
        SELECT
            t.id,
            t.user_id,
            t.date,
            t.merchant,
            COALESCE(t.original_amount, t.amount) AS amount,
            COALESCE(
                NULLIF(t.original_currency, ''),
                NULLIF(t.currency, ''),
                %s
            ) AS currency,
            COALESCE(
                NULLIF(t.file_fingerprint, ''),
                NULLIF(u.file_fingerprint_sha256, ''),
                ''
            ) AS statement_scope,
            COALESCE(t.original_file_name, u.original_file_name, ''),
            COALESCE(t.file_name, u.stored_file_name, '')
        FROM transactions t
        LEFT JOIN statement_uploads u
          ON u.id = t.statement_upload_id
        WHERE t.id = ANY(%s)
        ORDER BY t.date, t.id
        """,
        [DEFAULT_BASE_CURRENCY, list(transaction_ids)],
    )
    rows = cursor.fetchall()
    if not rows:
        return [], []

    taken = _existing_source_keys(cursor, user_id=None)
    updates: list[tuple[int, str | None]] = []
    notes: list[str] = []
    for (
        row_id,
        owner_id,
        row_date,
        merchant,
        amount,
        currency,
        statement_scope,
        original_file_name,
        file_name,
    ) in rows:
        card_last4 = card_last4_from_statement(
            "",
            file_name=f"{original_file_name}\n{file_name}",
        )
        source_key = source_keys_for_transactions(
            [
                {
                    "date": str(row_date),
                    "merchant": str(merchant),
                    "amount": float(amount),
                    "category": PAYMENT_CATEGORY,
                    "currency": str(currency),
                    "card_last4": card_last4,
                }
            ],
            statement_scope=str(statement_scope),
        )[0]
        owner_of_key = taken.get(int(owner_id), {}).get(source_key)
        if owner_of_key is not None and owner_of_key != int(row_id):
            updates.append((int(row_id), None))
            notes.append(
                f"cleared source_key on transaction {row_id}; "
                f"payment key already belongs to {owner_of_key}"
            )
            continue
        updates.append((int(row_id), source_key))
        taken.setdefault(int(owner_id), {})[source_key] = int(row_id)
    return updates, notes


def _clear_postgres_synced(user_id: int) -> str:
    path = batch_sqlite_path_for_user(user_id)
    if not path.exists():
        return f"no batch database for user {user_id}"
    with sqlite3.connect(path) as connection:
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(imported_transactions)"
            )
        }
        if "postgres_synced" not in columns:
            return (
                f"batch database for user {user_id} has no postgres_synced"
            )
        # Drop stale dashboard links so a later delete cannot remove a row
        # owned by a different upload after this repair.
        if "postgres_source_key" in columns:
            connection.execute(
                """
                UPDATE imported_transactions
                SET postgres_synced = 0,
                    postgres_source_key = NULL
                """
            )
        else:
            connection.execute(
                "UPDATE imported_transactions SET postgres_synced = 0"
            )
    return f"cleared postgres_synced for user {user_id}"
