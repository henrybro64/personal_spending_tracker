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
