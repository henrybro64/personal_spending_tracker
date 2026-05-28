#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set." >&2
  exit 1
fi

cd "$REPO_ROOT"
UPLOAD_DIR="${UPLOAD_DIR:-$REPO_ROOT/storage}" python - <<'PY'
import os
from pathlib import Path

from backend.retention import load_retention_policy, purge_expired_data

policy = load_retention_policy()
upload_root = Path(os.environ["UPLOAD_DIR"]).expanduser()
result = purge_expired_data(upload_root=upload_root)
print(result)
PY
