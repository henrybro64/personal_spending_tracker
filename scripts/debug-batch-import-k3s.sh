#!/usr/bin/env bash
# Debug batch import on a k3s deployment. Run on the cluster node.
set -euo pipefail

KUBECTL="${KUBECTL:-sudo k3s kubectl}"
NS="${NAMESPACE:-spending-tracker}"

echo "=== Pods ==="
"$KUBECTL" -n "$NS" get pods -l app.kubernetes.io/name=backend -o wide

POD="$("$KUBECTL" -n "$NS" get pods -l app.kubernetes.io/name=backend \
  -o jsonpath='{.items[0].metadata.name}')"

echo
echo "=== Backend image / restart count ==="
"$KUBECTL" -n "$NS" get pod "$POD" \
  -o jsonpath='image={.spec.containers[?(@.name=="backend")].image} restarts={.status.containerStatuses[?(@.name=="backend")].restartCount}{"\n"}'

echo
echo "=== Liveness probe (live deployment) ==="
"$KUBECTL" -n "$NS" get deployment backend -o yaml | grep -A12 'livenessProbe:'

echo
echo "=== Code markers in running container ==="
"$KUBECTL" -n "$NS" exec "$POD" -c backend -- sh -c '
  grep -n "asyncio.to_thread" /app/backend/main.py && echo "OK: to_thread present" || echo "MISSING: to_thread — rebuild image with --no-cache"
  grep -n "batch-imports/run started" /app/backend/main.py && echo "OK: import logging present" || echo "MISSING: import logging"
'

echo
echo "=== PDF count ==="
"$KUBECTL" -n "$NS" exec "$POD" -c backend -- sh -c 'ls -1 /app/batch/pdf/*.pdf 2>/dev/null | wc -l'

echo
echo "=== Recent backend logs (batch / errors) ==="
"$KUBECTL" -n "$NS" logs "$POD" -c backend --tail=80 | grep -E 'batch-import|Error|Traceback|started|finished|Shutting' || \
  echo "(no matching lines — try again right after clicking Run batch import)"

echo
echo "=== Recent cluster events (backend) ==="
"$KUBECTL" -n "$NS" get events --field-selector "involvedObject.name=$POD" --sort-by='.lastTimestamp' | tail -8

echo
echo "=== Direct import in pod (bypasses UI; may take minutes) ==="
echo "Run:"
echo "  $KUBECTL -n $NS exec -it $POD -c backend -- python3 -m backend.import_statements"
