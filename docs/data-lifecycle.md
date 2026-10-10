# Data Lifecycle, Retention, And Privacy

## Stored data

| Data | Location | Purpose |
|------|----------|---------|
| Statement PDFs | `UPLOAD_DIR/user-<id>/` | Original uploads |
| Transactions | PostgreSQL `transactions` | Parsed spending rows |
| Upload metadata | PostgreSQL `statement_uploads` | Duplicate detection and history |
| Sessions | PostgreSQL `user_sessions` | Auth cookies |
| Audit events | PostgreSQL `audit_logs` | Security and data-change trail |
| Batch source statements | `BATCH_PDF_SOURCE_DIR/user-<id>/`, `BATCH_TEXT_SOURCE_DIR/user-<id>/` | Files read by **Run batch import** for that user only |
| Batch review DB | `batch_imports_user_<id>.sqlite3` | Offline review before merge |

## Retention policy

Configure in the backend environment:

| Variable | Default | Behavior |
|----------|---------|----------|
| `STATEMENT_RETENTION_DAYS` | `0` (disabled) | Deletes uploads and linked transactions older than N days |
| `AUDIT_LOG_RETENTION_DAYS` | `365` | Deletes audit rows older than N days |
| `SESSION_TTL_DAYS` | `30` | Session expiry (see `backend/auth.py`) |

Run purge manually:

```bash
DATABASE_URL=postgresql://... UPLOAD_DIR=./storage \
  ./scripts/purge-expired-data.sh
```

## User deletion

- `DELETE /uploads/{upload_id}` removes one statement upload, linked
  transactions, and the stored PDF for the signed-in user.
- Account-wide deletion is not automated yet; use database restore workflows
  only in controlled environments.

## Audit logging

Audit events are recorded for authentication, statement uploads, manual
transaction creation, transaction updates/deletes, batch import runs/merges,
batch review edits, and statement upload deletion.

Audit `metadata` should not include PDF contents or passwords.

## Checking rows from older import bugs

Fixes for duplicate imports, refund signs, card-payment categories, and
E.SUN statement years do not rewrite rows that were stored before those
fixes. After taking a backup, report what is still suspicious:

```bash
DATABASE_URL=postgresql://... python scripts/check-spending-data.py
```

Add `--user-id 1` to limit the report to one account. The report is
read-only. It lists possible duplicates, batch-merged uploads, card
payments still categorized as spending, dates after today, and dates that
are not `YYYY-MM-DD`.

Repairs are separate and opt-in. Without `--apply` they only print what
would change. `--apply` is refused unless `--i-have-a-backup` is also set.
Run `scripts/backup-spending-tracker.sh` first.

| Flag | What it changes |
|------|-----------------|
| `--recategorize-payments` | Sets category `Payment / Credit` on card-payment rows |
| `--delete-batch-merges` | Deletes `batch-import/` uploads and their transactions, and clears `postgres_synced` in that user's batch SQLite file so the statements can be merged again |
| `--backfill-source-keys` | Fills `source_key` on imported rows that lack one, in date order, and stops at a key that already belongs to another row |

`--delete-batch-merges` updates Postgres first, then the user's batch SQLite
file. Those are separate databases, so they cannot share one transaction. If
clearing `postgres_synced` fails after the Postgres deletes, the repair prints
a note: re-run the same flag (deletes are safe when the uploads are already
gone), or clear that user's `batch_imports_user_<id>.sqlite3` synced flags
manually before merging again.

```bash
DATABASE_URL=postgresql://... python scripts/check-spending-data.py \
  --recategorize-payments --apply --i-have-a-backup
```
