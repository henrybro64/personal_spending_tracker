#!/usr/bin/env bash
# Debug batch import on a k3s deployment. Run on the cluster node.
set -euo pipefail

KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"

KUBECTL_CMD=()
read -r -a KUBECTL_CMD <<<"$KUBECTL"
if [[ "${#KUBECTL_CMD[@]}" -eq 0 ]]; then
  echo "KUBECTL cannot be empty" >&2
  exit 1
fi

echo "=== Pods ==="
"${KUBECTL_CMD[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend -o wide

POD="$("${KUBECTL_CMD[@]}" -n "$NS" get pods -l app.kubernetes.io/name=backend \
  -o jsonpath='{.items[0].metadata.name}')"

echo
echo "=== Backend image / restart count ==="
"${KUBECTL_CMD[@]}" -n "$NS" get pod "$POD" \
  -o jsonpath='image={.spec.containers[?(@.name=="backend")].image} restarts={.status.containerStatuses[?(@.name=="backend")].restartCount}{"\n"}'

echo
echo "=== Liveness probe (live deployment) ==="
"${KUBECTL_CMD[@]}" -n "$NS" get deployment backend -o yaml | grep -A12 'livenessProbe:'

echo
echo "=== Code markers in running container ==="
"${KUBECTL_CMD[@]}" -n "$NS" exec "$POD" -c backend -- sh -c '
  grep -n "asyncio.to_thread" /app/backend/main.py && echo "OK: to_thread present" || echo "MISSING: to_thread — rebuild image with --no-cache"
  grep -n "batch-imports/run started" /app/backend/main.py && echo "OK: import logging present" || echo "MISSING: import logging"
'

echo
echo "=== PDF count per user (batch import reads /app/batch/pdf/user-<id>/) ==="
"${KUBECTL_CMD[@]}" -n "$NS" exec "$POD" -c backend -- sh -c '
  for d in /app/batch/pdf/user-*/; do
    [ -d "$d" ] && echo "$d $(ls -1 "$d"*.pdf 2>/dev/null | wc -l)"
  done
  n=$(ls -1 /app/batch/pdf/*.pdf 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] && echo "WARNING: $n PDF(s) in /app/batch/pdf root are ignored; move them to /app/batch/pdf/user-<id>/"
  true
'

echo
echo "=== Recent backend logs (batch / errors) ==="
"${KUBECTL_CMD[@]}" -n "$NS" logs "$POD" -c backend --tail=80 | grep -E 'batch-import|Error|Traceback|started|finished|Shutting' || \
  echo "(no matching lines — try again right after clicking Run batch import)"

echo
echo "=== Recent cluster events (backend) ==="
"${KUBECTL_CMD[@]}" -n "$NS" get events --field-selector "involvedObject.name=$POD" --sort-by='.lastTimestamp' | tail -8

echo
echo "=== Direct import in pod (bypasses UI; may take minutes) ==="
echo "Run (user-1; results appear on that user's /batch-review page):"
printf '  %q' "${KUBECTL_CMD[@]}"
echo " -n $NS exec -it $POD -c backend -- python3 -m backend.import_statements \\"
echo "    --pdf-dir /app/batch/pdf/user-1 --text-dir /app/batch/text/user-1 \\"
echo "    --sqlite-out /app/batch/data/batch_imports_user_1.sqlite3 \\"
echo "    --csv-out /app/batch/data/batch_transactions_user_1.csv"
