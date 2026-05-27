# Backup And Restore

Use logical backups before treating the tracker as a durable long-term system.

## What is backed up

- PostgreSQL schema and data (`pg_dump`)
- Uploaded statement PDFs under `UPLOAD_DIR` (default `./storage` locally,
  `/app/uploads` in k3s)

Batch-import SQLite review databases are not included by default. Re-run batch
import or back up `/app/batch/data` separately if you rely on in-review runs.

## Create a backup

```bash
DATABASE_URL=postgresql://... UPLOAD_DIR=./storage \
  ./scripts/backup-spending-tracker.sh
```

k3s:

```bash
KUBECTL='sudo k3s kubectl' ./scripts/backup-spending-tracker.sh --k3s
```

Backups are written to `backups/<UTC-timestamp>/` with `postgres.sql`,
`uploads.tar.gz`, and `manifest.json`.

## Restore

Stop concurrent writes first (scale backend to zero or pause uploads), then:

```bash
DATABASE_URL=postgresql://... UPLOAD_DIR=./storage \
  ./scripts/restore-spending-tracker.sh backups/<timestamp> --yes
```

Restoring overwrites the target database and replaces files in `UPLOAD_DIR`.

## Recovery expectations

- Logical dumps are suitable for single-node k3s and local development
- For production, also snapshot Postgres and uploads PVCs at the storage layer
- Test restore on a non-production database before relying on a backup set
