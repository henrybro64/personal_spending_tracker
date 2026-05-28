#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Create a timestamped backup of PostgreSQL and uploaded statement files.

Local:
  DATABASE_URL=postgresql://... UPLOAD_DIR=./storage ./scripts/backup-spending-tracker.sh

k3s backend pod:
  KUBECTL='sudo k3s kubectl' ./scripts/backup-spending-tracker.sh --k3s
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"
MODE="local"
BACKUP_ROOT="${BACKUP_ROOT:-$REPO_ROOT/backups}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --k3s) MODE="k3s"; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
done

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
backup_dir="$BACKUP_ROOT/$timestamp"
mkdir -p "$backup_dir"

write_manifest() {
  local postgres_file="$1"
  local uploads_archive="$2"
  cat >"$backup_dir/manifest.json" <<EOF
{
  "created_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "mode": "$MODE",
  "postgres_dump": "$(basename "$postgres_file")",
  "uploads_archive": "$(basename "$uploads_archive")"
}
EOF
}

run_local_backup() {
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required for local backups." >&2
    exit 1
  fi
  if ! command -v pg_dump >/dev/null 2>&1; then
    echo "pg_dump is required for local backups." >&2
    exit 1
  fi

  postgres_file="$backup_dir/postgres.sql"
  uploads_archive="$backup_dir/uploads.tar.gz"
  upload_dir="${UPLOAD_DIR:-$REPO_ROOT/storage}"

  pg_dump "$DATABASE_URL" --format=plain --no-owner --no-privileges >"$postgres_file"
  if [[ -d "$upload_dir" ]]; then
    tar -czf "$uploads_archive" -C "$upload_dir" .
  else
    tar -czf "$uploads_archive" --files-from /dev/null
  fi
  write_manifest "$postgres_file" "$uploads_archive"
}

run_k3s_backup() {
  read -r -a kubectl_cmd <<<"$KUBECTL"
  pod="$("${kubectl_cmd[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend \
    -o jsonpath='{.items[0].metadata.name}')"

  postgres_file="$backup_dir/postgres.sql"
  uploads_archive="$backup_dir/uploads.tar.gz"

  "${kubectl_cmd[@]}" -n "$NS" exec "$pod" -c backend -- \
    sh -c 'pg_dump "$DATABASE_URL" --format=plain --no-owner --no-privileges' \
    >"$postgres_file"
  "${kubectl_cmd[@]}" -n "$NS" exec "$pod" -c backend -- \
    sh -c 'cd "${UPLOAD_DIR:-/app/uploads}" && tar -czf - .' >"$uploads_archive"
  write_manifest "$postgres_file" "$uploads_archive"
}

if [[ "$MODE" == "k3s" ]]; then
  run_k3s_backup
else
  run_local_backup
fi

echo "Backup written to $backup_dir"
