import sqlite3

import psycopg
import pytest

from backend.batch_import.storage import BatchImportStore
from backend.check_spending_data import (
    RepairRefused,
    backfill_source_keys,
    batch_sqlite_path_for_user,
    delete_batch_merges,
    format_report,
    main,
    recategorize_payments,
    report_spending_data,
)
from backend.transaction_exclusions import PAYMENT_CATEGORY
from backend.transaction_source import source_keys_for_transactions


def _seed(database_url: str) -> dict[str, int]:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (email, password_hash, display_name)
                VALUES ('check@example.com', 'x', 'Check')
                RETURNING id
                """
            )
            user_id = int(cursor.fetchone()[0])
            uploads: dict[str, int] = {}
            for name, fingerprint in (
                ("card.pdf", "fp-card"),
                ("batch-import/jan.txt", "fp-batch"),
                ("future.pdf", "fp-future"),
                ("book.pdf", "fp-book"),
            ):
                cursor.execute(
                    """
                    INSERT INTO statement_uploads (
                        user_id,
                        original_file_name,
                        stored_file_name,
                        file_fingerprint_sha256,
                        parse_status
                    )
                    VALUES (%s, %s, %s, %s, 'parsed')
                    RETURNING id
                    """,
                    (user_id, name, name, fingerprint),
                )
                uploads[name] = int(cursor.fetchone()[0])

            def add_row(
                *,
                upload: str,
                row_date: str,
                merchant: str,
                amount: float = 10,
                category: str = "Dining",
                source_key: str | None = None,
                file_name: str | None = None,
            ) -> int:
                cursor.execute(
                    """
                    INSERT INTO transactions (
                        user_id,
                        statement_upload_id,
                        date,
                        merchant,
                        amount,
                        original_amount,
                        currency,
                        original_currency,
                        category,
                        file_name,
                        file_fingerprint,
                        source_key
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, 'TWD', 'TWD', %s, %s, %s, %s
                    )
                    RETURNING id
                    """,
                    (
                        user_id,
                        uploads[upload],
                        row_date,
                        merchant,
                        amount,
                        amount,
                        category,
                        file_name or upload,
                        {
                            "card.pdf": "fp-card",
                            "book.pdf": "fp-book",
                        }.get(upload, upload),
                        source_key,
                    ),
                )
                return int(cursor.fetchone()[0])

            duplicate_on_card = add_row(
                upload="card.pdf",
                row_date="2026-01-05",
                merchant="CAFE",
                source_key="keep-me",
            )
            duplicate_on_batch = add_row(
                upload="batch-import/jan.txt",
                row_date="2026-01-05",
                merchant="CAFE",
            )
            add_row(
                upload="card.pdf",
                row_date="2026-01-06",
                merchant="IKEA",
            )
            add_row(
                upload="card.pdf",
                row_date="2026-01-06",
                merchant="IKEA",
            )
            payment_id = add_row(
                upload="card.pdf",
                row_date="2026-02-01",
                merchant="PAYMENT THANK YOU",
                category="Dining",
            )
            future_id = add_row(
                upload="future.pdf",
                row_date="2099-01-01",
                merchant="FUTURE",
            )
            unreadable_id = add_row(
                upload="card.pdf",
                row_date="01/05/2026",
                merchant="BAD DATE",
            )
            backfill_id = add_row(
                upload="book.pdf",
                row_date="2026-03-01",
                merchant="BOOKSHOP",
                amount=20,
            )
            cursor.execute(
                """
                INSERT INTO transactions (
                    user_id, date, merchant, amount, currency,
                    category, file_name
                )
                VALUES (%s, '2026-04-01', 'CASH', 5, 'TWD', 'Dining',
                        'manual-entry')
                RETURNING id
                """,
                (user_id,),
            )
            manual_id = int(cursor.fetchone()[0])
        connection.commit()
    return {
        "user_id": user_id,
        "duplicate_on_card": duplicate_on_card,
        "duplicate_on_batch": duplicate_on_batch,
        "payment_id": payment_id,
        "future_id": future_id,
        "unreadable_id": unreadable_id,
        "backfill_id": backfill_id,
        "manual_id": manual_id,
    }


def _category(database_url: str, transaction_id: int) -> str:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT category FROM transactions WHERE id = %s",
                (transaction_id,),
            )
            return str(cursor.fetchone()[0])


def _source_key(database_url: str, transaction_id: int) -> str | None:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_key FROM transactions WHERE id = %s",
                (transaction_id,),
            )
            value = cursor.fetchone()[0]
    return None if value is None else str(value)


def test_report_finds_each_problem(database_url: str, reset_database) -> None:
    del reset_database
    seeded = _seed(database_url)
    report = report_spending_data(
        user_id=seeded["user_id"],
        database_url=database_url,
    )

    assert len(report.duplicate_groups) == 1
    assert report.batch_duplicate_groups == 1
    group = report.duplicate_groups[0]
    assert group.merchant == "CAFE"
    assert group.amount == "10.00"
    assert "batch-import/jan.txt" in group.upload_names
    assert report.batch_uploads == ("batch-import/jan.txt",)
    assert [row.transaction_id for row in report.payments_stored_as_spending] == [
        seeded["payment_id"]
    ]
    assert [row.transaction_id for row in report.future_dated_rows] == [
        seeded["future_id"]
    ]
    assert report.future_dated_rows[0].detail == "future.pdf"
    assert [row.transaction_id for row in report.unreadable_dates] == [
        seeded["unreadable_id"]
    ]
    text = format_report(report)
    assert "Possible duplicates: 1 (1 from a batch merge)" in text
    assert "Payments stored as spending: 1" in text


def test_repairs_change_nothing_without_apply(
    database_url: str,
    reset_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    del reset_database
    monkeypatch.setenv("BATCH_DATA_DIR", str(tmp_path))
    seeded = _seed(database_url)
    _mark_batch_sqlite_synced(seeded["user_id"])
    kwargs = {
        "user_id": seeded["user_id"],
        "database_url": database_url,
        "apply": False,
    }
    payments = recategorize_payments(**kwargs)
    deleted = delete_batch_merges(**kwargs)
    filled = backfill_source_keys(**kwargs)

    assert payments.changed_ids == (seeded["payment_id"],)
    assert deleted.changed_ids == (seeded["duplicate_on_batch"],)
    assert seeded["backfill_id"] in filled.changed_ids
    assert seeded["duplicate_on_card"] not in filled.changed_ids
    assert seeded["manual_id"] not in filled.changed_ids
    assert _category(database_url, seeded["payment_id"]) == "Dining"
    assert _upload_count(database_url, seeded["user_id"]) == 4
    assert _source_key(database_url, seeded["backfill_id"]) is None
    assert _sqlite_synced(seeded["user_id"]) == 1


def test_each_repair_changes_only_the_expected_rows(
    database_url: str,
    reset_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    del reset_database
    monkeypatch.setenv("BATCH_DATA_DIR", str(tmp_path))
    seeded = _seed(database_url)
    _mark_batch_sqlite_synced(seeded["user_id"])
    common = {
        "user_id": seeded["user_id"],
        "database_url": database_url,
        "apply": True,
        "i_have_a_backup": True,
    }

    payments = recategorize_payments(**common)
    assert payments.changed_ids == (seeded["payment_id"],)
    assert _category(database_url, seeded["payment_id"]) == PAYMENT_CATEGORY
    assert _category(database_url, seeded["duplicate_on_card"]) == "Dining"

    filled = backfill_source_keys(**common)
    assert seeded["backfill_id"] in filled.changed_ids
    assert _source_key(database_url, seeded["manual_id"]) is None
    assert _source_key(database_url, seeded["duplicate_on_card"]) == "keep-me"
    expected = source_keys_for_transactions(
        [
            {
                "date": "2026-03-01",
                "merchant": "BOOKSHOP",
                "amount": 20.0,
                "category": "Dining",
                "currency": "TWD",
                "card_last4": "",
            }
        ],
        statement_scope="fp-book",
    )[0]
    assert _source_key(database_url, seeded["backfill_id"]) == expected

    deleted = delete_batch_merges(**common)
    assert deleted.changed_ids == (seeded["duplicate_on_batch"],)
    assert _upload_names(database_url, seeded["user_id"]) == [
        "book.pdf",
        "card.pdf",
        "future.pdf",
    ]
    assert _sqlite_synced(seeded["user_id"]) == 0


def test_backfill_stops_on_a_source_key_conflict(
    database_url: str,
    reset_database,
) -> None:
    del reset_database
    seeded = _seed(database_url)
    blocker_key = source_keys_for_transactions(
        [
            {
                "date": "01/05/2026",
                "merchant": "BAD DATE",
                "amount": 10.0,
                "category": "Dining",
                "currency": "TWD",
                "card_last4": "",
            }
        ],
        statement_scope="fp-card",
    )[0]
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE transactions
                SET source_key = %s
                WHERE id = %s
                """,
                (blocker_key, seeded["duplicate_on_card"]),
            )
        connection.commit()

    result = backfill_source_keys(
        user_id=seeded["user_id"],
        database_url=database_url,
        apply=True,
        i_have_a_backup=True,
    )
    assert result.conflicts
    assert result.changed_ids == ()
    assert _source_key(database_url, seeded["backfill_id"]) is None


def test_apply_without_a_backup_flag_is_refused(
    database_url: str,
    reset_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del reset_database
    monkeypatch.setenv("DATABASE_URL", database_url)
    seeded = _seed(database_url)
    with pytest.raises(RepairRefused):
        recategorize_payments(
            user_id=seeded["user_id"],
            database_url=database_url,
            apply=True,
        )
    assert _category(database_url, seeded["payment_id"]) == "Dining"
    assert main(["--recategorize-payments", "--apply"]) == 2
    assert _category(database_url, seeded["payment_id"]) == "Dining"


def test_delete_batch_merges_notes_when_sqlite_clear_fails(
    database_url: str,
    reset_database,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    del reset_database
    monkeypatch.setenv("BATCH_DATA_DIR", str(tmp_path))
    seeded = _seed(database_url)
    _mark_batch_sqlite_synced(seeded["user_id"])

    def boom(user_id: int) -> str:
        del user_id
        raise sqlite3.OperationalError("sqlite locked")

    monkeypatch.setattr(
        "backend.check_spending_data._clear_postgres_synced",
        boom,
    )
    result = delete_batch_merges(
        user_id=seeded["user_id"],
        database_url=database_url,
        apply=True,
        i_have_a_backup=True,
    )
    assert result.changed_ids == (seeded["duplicate_on_batch"],)
    assert _upload_names(database_url, seeded["user_id"]) == [
        "book.pdf",
        "card.pdf",
        "future.pdf",
    ]
    assert any(
        "clearing postgres_synced failed" in note for note in result.notes
    )
    assert any("Re-run --delete-batch-merges" in note for note in result.notes)


def _upload_count(database_url: str, user_id: int) -> int:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM statement_uploads
                WHERE user_id = %s
                """,
                (user_id,),
            )
            return int(cursor.fetchone()[0])


def _upload_names(database_url: str, user_id: int) -> list[str]:
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT stored_file_name
                FROM statement_uploads
                WHERE user_id = %s
                ORDER BY stored_file_name
                """,
                (user_id,),
            )
            return [str(row[0]) for row in cursor.fetchall()]


def _mark_batch_sqlite_synced(user_id: int) -> None:
    path = batch_sqlite_path_for_user(user_id)
    store = BatchImportStore(path)
    store.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO import_runs (
                imported_at, pdf_source_dir, text_source_dir,
                csv_output_path, sqlite_output_path,
                statement_count, transaction_count, needs_review_count
            )
            VALUES ('2026-01-01T00:00:00+00:00', '', '', '', '', 1, 1, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO imported_transactions (
                import_run_id, statement_file, bank, transaction_date,
                merchant_raw, merchant_key, amount, currency,
                transaction_type, category, exclude_from_spending,
                needs_review, postgres_synced
            )
            VALUES (
                1, 'jan.txt', 'AMEX', '2026-01-05', 'CAFE', 'CAFE',
                10, 'TWD', 'purchase', 'Dining', 0, 0, 1
            )
            """
        )


def _sqlite_synced(user_id: int) -> int:
    path = batch_sqlite_path_for_user(user_id)
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT postgres_synced FROM imported_transactions"
        ).fetchone()
    return int(row[0])
