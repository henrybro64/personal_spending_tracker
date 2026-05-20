#!/usr/bin/env bash
# Quick triage when pods are Error / ContainerCreating.
set -euo pipefail

KUBECTL="${KUBECTL:-sudo kubectl}"
NS="${NAMESPACE:-spending-tracker}"

KUBECTL_CMD=()
read -r -a KUBECTL_CMD <<<"$KUBECTL"

echo "=== Pods ==="
"${KUBECTL_CMD[@]}" -n "$NS" get pods -o wide

echo
echo "=== PVCs ==="
"${KUBECTL_CMD[@]}" -n "$NS" get pvc

echo
echo "=== Recent events ==="
"${KUBECTL_CMD[@]}" -n "$NS" get events --sort-by='.lastTimestamp' | tail -25

for deploy in postgres backend frontend; do
  POD="$("${KUBECTL_CMD[@]}" -n "$NS" get pods -l "app.kubernetes.io/name=$deploy" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -z "$POD" ]]; then
    continue
  fi
  echo
  echo "=== describe $deploy ($POD) ==="
  "${KUBECTL_CMD[@]}" -n "$NS" describe pod "$POD" | tail -30
  echo
  echo "=== logs $deploy ($POD) ==="
  "${KUBECTL_CMD[@]}" -n "$NS" logs "$POD" --all-containers=true --tail=40 2>&1 || true
done

echo
echo "Tips:"
echo "  - Postgres ContainerCreating: check PVC Pending (local-path provisioner)."
echo "  - Backend Error at startup: often postgres not ready; redeploy after postgres is Running."
echo "  - Two backend pods Error: stale rollout; fix postgres then: kubectl -n $NS rollout restart deployment/backend"
