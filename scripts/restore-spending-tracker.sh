#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Restore PostgreSQL and uploaded statement files from a backup directory.

Local:
  DATABASE_URL=postgresql://... UPLOAD_DIR=./storage \
    ./scripts/restore-spending-tracker.sh backups/20260101T120000Z

k3s:
  KUBECTL='sudo k3s kubectl' ./scripts/restore-spending-tracker.sh --k3s backups/20260101T120000Z
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"
MODE="local"
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --k3s) MODE="k3s"; shift ;;
    -y | --yes) ASSUME_YES=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *)
      if [[ -z "${BACKUP_DIR:-}" ]]; then
        BACKUP_DIR="$1"
        shift
      else
        echo "Unknown option: $1" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

if [[ -z "${BACKUP_DIR:-}" ]]; then
  echo "Backup directory is required." >&2
  usage >&2
  exit 1
fi

if [[ ! -d "$BACKUP_DIR" ]]; then
  echo "Backup directory not found: $BACKUP_DIR" >&2
  exit 1
fi

postgres_dump="$BACKUP_DIR/postgres.sql"
uploads_archive="$BACKUP_DIR/uploads.tar.gz"
if [[ ! -f "$postgres_dump" || ! -f "$uploads_archive" ]]; then
  echo "Backup must contain postgres.sql and uploads.tar.gz" >&2
  exit 1
fi

confirm() {
  echo "This will overwrite the current database and upload files."
  echo "Backup source: $BACKUP_DIR"
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  read -r -p "Continue? [y/N] " reply
  case "$reply" in
    y | Y | yes | YES) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
}

restore_local() {
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is required." >&2
    exit 1
  fi
  upload_dir="${UPLOAD_DIR:-$REPO_ROOT/storage}"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$postgres_dump"
  mkdir -p "$upload_dir"
  tar -xzf "$uploads_archive" -C "$upload_dir"
}

restore_k3s() {
  read -r -a kubectl_cmd <<<"$KUBECTL"
  pod="$("${kubectl_cmd[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend \
    -o jsonpath='{.items[0].metadata.name}')"
  "${kubectl_cmd[@]}" -n "$NS" exec -i "$pod" -c backend -- \
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <"$postgres_dump"
  "${kubectl_cmd[@]}" -n "$NS" exec -i "$pod" -c backend -- \
    sh -c 'rm -rf "${UPLOAD_DIR:-/app/uploads}"/* && mkdir -p "${UPLOAD_DIR:-/app/uploads}"'
  "${kubectl_cmd[@]}" -n "$NS" exec -i "$pod" -c backend -- \
    tar -xzf - -C "${UPLOAD_DIR:-/app/uploads}" <"$uploads_archive"
}

confirm
if [[ "$MODE" == "k3s" ]]; then
  restore_k3s
else
  restore_local
fi

echo "Restore completed from $BACKUP_DIR"
