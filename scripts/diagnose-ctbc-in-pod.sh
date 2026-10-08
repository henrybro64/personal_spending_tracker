#!/usr/bin/env bash
# Run CTBC/Uber PDF diagnostics inside the backend pod (k3s).
# Usage on the cluster node:
#
#   bash scripts/diagnose-ctbc-in-pod.sh
#   bash scripts/diagnose-ctbc-in-pod.sh CTBC_card
#
# Optional env:
#   KUBECTL='sudo k3s kubectl'
#   NAMESPACE=spending-tracker
#   BATCH_USER_ID=1      # whose /app/batch/pdf/user-<id>/ folder to scan
#   BATCH_SQLITE=/app/batch/data/batch_imports_user_1.sqlite3
set -euo pipefail

KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"
ONLY_FILTER="${1:-}"
BATCH_USER_ID="${BATCH_USER_ID:-1}"
BATCH_SQLITE="${BATCH_SQLITE:-}"
PDF_DIR="/app/batch/pdf/user-${BATCH_USER_ID}"

KUBECTL_CMD=()
read -r -a KUBECTL_CMD <<<"$KUBECTL"

POD="$("${KUBECTL_CMD[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend \
  -o jsonpath='{.items[0].metadata.name}')"

echo "Backend pod: $POD (namespace $NS)"
echo

# Ensure diagnostic module exists in the running image; if missing, copy from repo.
if ! "${KUBECTL_CMD[@]}" -n "$NS" exec "$POD" -c backend -- \
  test -f /app/backend/diagnose_pdf_merchants.py 2>/dev/null; then
  REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  echo "Copying diagnose_pdf_merchants.py into pod (image predates this tool)..."
  "${KUBECTL_CMD[@]}" -n "$NS" cp \
    "$REPO_ROOT/backend/diagnose_pdf_merchants.py" \
    "$NS/$POD:/app/backend/diagnose_pdf_merchants.py" -c backend
fi

if [[ -z "$BATCH_SQLITE" ]]; then
  BATCH_SQLITE="$("${KUBECTL_CMD[@]}" -n "$NS" exec "$POD" -c backend -- sh -c \
    "for p in /app/batch/data/batch_imports_user_${BATCH_USER_ID}.sqlite3 /app/batch/data/batch_imports.sqlite3; do
       [ -f \"\$p\" ] && echo \"\$p\" && exit 0
     done
     exit 1" 2>/dev/null || true)"
  if [[ -z "$BATCH_SQLITE" ]]; then
    BATCH_SQLITE="/app/batch/data/batch_imports_user_${BATCH_USER_ID}.sqlite3"
    echo "Note: no batch SQLite found in pod; DB section will be skipped."
  else
    echo "Using batch SQLite: $BATCH_SQLITE"
  fi
fi

ARGS=(python3 -m backend.diagnose_pdf_merchants "$PDF_DIR" --sqlite "$BATCH_SQLITE")
if [[ -n "$ONLY_FILTER" ]]; then
  ARGS+=(--only "$ONLY_FILTER")
fi

echo "=== PDF + SQLite diagnostic ==="
"${KUBECTL_CMD[@]}" -n "$NS" exec "$POD" -c backend -- "${ARGS[@]}"
