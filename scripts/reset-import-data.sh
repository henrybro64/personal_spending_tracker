#!/usr/bin/env bash
# Reset batch-import SQLite and merged Postgres data for a clean re-import test.
#
# Does NOT delete PDFs in the batch folder (only database state).
# Does NOT delete users/sessions by default (you stay logged in).
#
# Local (DATABASE_URL must be set, e.g. from backend env):
#   ./scripts/reset-import-data.sh
#   ./scripts/reset-import-data.sh --dry-run
#
# k3s backend pod (same paths as production):
#   KUBECTL='sudo k3s kubectl' ./scripts/reset-import-data.sh --k3s
#   KUBECTL='sudo k3s kubectl' ./scripts/reset-import-data.sh --k3s -y
#
# Wipe login accounts too (rare):
#   ./scripts/reset-import-data.sh --full-postgres --yes
#
set -euo pipefail

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"
BATCH_DATA_DIR="${BATCH_DATA_DIR:-$REPO_ROOT/storage}"

MODE="local"
DRY_RUN=0
ASSUME_YES=0
FULL_POSTGRES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --k3s)
      MODE="k3s"
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -y | --yes)
      ASSUME_YES=1
      shift
      ;;
    --full-postgres)
      FULL_POSTGRES=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

KUBECTL_CMD=()
if [[ "$MODE" == "k3s" ]]; then
  read -r -a KUBECTL_CMD <<<"$KUBECTL"
  if [[ "${#KUBECTL_CMD[@]}" -eq 0 ]]; then
    echo "KUBECTL cannot be empty" >&2
    exit 1
  fi
fi

postgres_sql() {
  if [[ "$FULL_POSTGRES" -eq 1 ]]; then
    cat <<'SQL'
TRUNCATE TABLE
  transactions,
  statement_uploads,
  audit_logs,
  user_sessions,
  users
RESTART IDENTITY CASCADE;
SQL
  else
    cat <<'SQL'
TRUNCATE TABLE
  transactions,
  statement_uploads,
  audit_logs
RESTART IDENTITY CASCADE;
SQL
  fi
}

sqlite_glob_desc() {
  printf '%s/batch_imports*.sqlite3 %s/batch_transactions_user_*.csv %s/batch_transactions.csv' \
    "$BATCH_DATA_DIR" "$BATCH_DATA_DIR" "$BATCH_DATA_DIR"
}

run_local_postgres() {
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is not set. Export it or use --k3s." >&2
    exit 1
  fi
  if ! command -v psql >/dev/null 2>&1; then
    echo "psql is required for local Postgres reset." >&2
    exit 1
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] psql \$DATABASE_URL <<SQL"
    postgres_sql
    return 0
  fi
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "$(postgres_sql)"
}

run_local_sqlite() {
  local root="${BATCH_DATA_DIR}"
  if [[ ! -d "$root" ]]; then
    echo "Batch data dir not found: $root (nothing to delete)" >&2
    return 0
  fi
  shopt -s nullglob
  local files=(
    "$root"/batch_imports*.sqlite3
    "$root"/batch_transactions.csv
    "$root"/batch_transactions_user_*.csv
  )
  shopt -u nullglob
  if [[ "${#files[@]}" -eq 0 ]]; then
    echo "No batch SQLite/CSV files under $root"
    return 0
  fi
  for f in "${files[@]}"; do
    if [[ "$DRY_RUN" -eq 1 ]]; then
      echo "[dry-run] rm -f $f"
    else
      rm -f "$f"
      echo "Removed $f"
    fi
  done
}

run_k3s() {
  local pod
  pod="$("${KUBECTL_CMD[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend \
    -o jsonpath='{.items[0].metadata.name}')"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[dry-run] kubectl exec $pod -c backend — Postgres reset"
    postgres_sql
    echo "[dry-run] kubectl exec $pod -c backend — rm $(sqlite_glob_desc)"
    return 0
  fi

  "${KUBECTL_CMD[@]}" -n "$NS" exec "$pod" -c backend -- env \
    RESET_FULL_POSTGRES="$FULL_POSTGRES" \
    python - <<'PY'
import os
import sys

import psycopg

sql_import_only = """
TRUNCATE TABLE
  transactions,
  statement_uploads,
  audit_logs
RESTART IDENTITY CASCADE;
"""

sql_full = """
TRUNCATE TABLE
  transactions,
  statement_uploads,
  audit_logs,
  user_sessions,
  users
RESTART IDENTITY CASCADE;
"""

full = os.environ.get("RESET_FULL_POSTGRES") == "1"
database_url = os.environ["DATABASE_URL"]
sql = sql_full if full else sql_import_only

with psycopg.connect(database_url) as conn:
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()

for table in ("transactions", "statement_uploads", "audit_logs"):
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
    print(f"Postgres {table}: {count} rows")
PY

  "${KUBECTL_CMD[@]}" -n "$NS" exec "$pod" -c backend -- sh -c \
    'rm -f /app/batch/data/batch_imports*.sqlite3 \
      /app/batch/data/batch_transactions.csv \
      /app/batch/data/batch_transactions_user_*.csv 2>/dev/null; \
     ls -1 /app/batch/data/batch_imports*.sqlite3 2>/dev/null || echo "SQLite batch DBs cleared."'
}

confirm() {
  local sqlite_dir="$BATCH_DATA_DIR"
  if [[ "$MODE" == "k3s" ]]; then
    sqlite_dir="/app/batch/data"
  fi

  echo "This will reset import data for testing:"
  echo "  Mode: $MODE"
  if [[ "$FULL_POSTGRES" -eq 1 ]]; then
    echo "  Postgres: TRUNCATE transactions, statement_uploads, audit_logs, user_sessions, users"
  else
    echo "  Postgres: TRUNCATE transactions, statement_uploads, audit_logs (keep users/sessions)"
  fi
  echo "  SQLite:   delete batch_imports*.sqlite3 and batch_transactions*.csv under $sqlite_dir"
  echo "  PDFs:     NOT deleted (re-use /app/batch/pdf)"
  echo
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    y | Y | yes | YES) ;;
    *)
      echo "Aborted."
      exit 0
      ;;
  esac
}

main() {
  confirm
  if [[ "$MODE" == "k3s" ]]; then
    run_k3s
  else
    run_local_postgres
    run_local_sqlite
  fi
  echo "Done. Run batch import again from Batch Review."
}

main
