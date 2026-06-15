#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

KUBECTL="${KUBECTL:-kubectl}"
NAMESPACE="${NAMESPACE:-spending-tracker}"
APP_HOST="${APP_HOST:-spending.local}"
APP_ENV="${APP_ENV:-production}"
DATABASE_HOST="${DATABASE_HOST:-postgres}"
DATABASE_NAME="${DATABASE_NAME:-personal_spending_tracker}"
DATABASE_PORT="${DATABASE_PORT:-5432}"
DATABASE_USER="${DATABASE_USER:-spending_tracker}"
UPLOAD_DIR="${UPLOAD_DIR:-/app/uploads}"
BATCH_DATA_DIR="${BATCH_DATA_DIR:-/app/batch/data}"
BATCH_PDF_SOURCE_DIR="${BATCH_PDF_SOURCE_DIR:-/app/batch/pdf}"
BATCH_TEXT_SOURCE_DIR="${BATCH_TEXT_SOURCE_DIR:-/app/batch/text}"
USD_TWD_EXCHANGE_RATE="${USD_TWD_EXCHANGE_RATE:-32}"
AUDIT_LOG_RETENTION_DAYS="${AUDIT_LOG_RETENTION_DAYS:-365}"
STATEMENT_RETENTION_DAYS="${STATEMENT_RETENTION_DAYS:-0}"
BATCH_STORAGE_SIZE="${BATCH_STORAGE_SIZE:-5Gi}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_STORAGE_CLASS="${POSTGRES_STORAGE_CLASS:-local-path}"
POSTGRES_STORAGE_SIZE="${POSTGRES_STORAGE_SIZE:-8Gi}"
UPLOADS_STORAGE_SIZE="${UPLOADS_STORAGE_SIZE:-5Gi}"
IMAGE_PULL_POLICY="${IMAGE_PULL_POLICY:-IfNotPresent}"
APP_IMAGE_PULL_POLICY="${APP_IMAGE_PULL_POLICY:-$IMAGE_PULL_POLICY}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
BUILD_IMAGES="${BUILD_IMAGES:-0}"
LOCAL_DEPLOY="${LOCAL_DEPLOY:-0}"
LOCAL_BACKEND_IMAGE="${LOCAL_BACKEND_IMAGE:-personal-spending-tracker-backend:latest}"
LOCAL_FRONTEND_IMAGE="${LOCAL_FRONTEND_IMAGE:-personal-spending-tracker-frontend:latest}"
K3S_CTR="${K3S_CTR:-sudo k3s ctr}"
BACKEND_IMAGE="${BACKEND_IMAGE:-}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-}"
FRONTEND_NODE_PORT="${FRONTEND_NODE_PORT:-30081}"
INGRESS_ENABLED="${INGRESS_ENABLED:-0}"
ENABLE_K3S_ON_BOOT="${ENABLE_K3S_ON_BOOT:-1}"
DEFAULT_DB_PASSWORD="${DEFAULT_DB_PASSWORD:-spending-tracker-dev}"
DEFAULT_LLM_SETTINGS_ENCRYPTION_KEY="${DEFAULT_LLM_SETTINGS_ENCRYPTION_KEY:-spending-tracker-llm-dev}"
KUBECTL_CMD=()
CTR_CMD=()

usage() {
  cat <<'EOF'
Deploy Personal Spending Tracker to a single-node k3s cluster.

Required environment variables:
  BACKEND_IMAGE    Full backend container image reference to deploy
  FRONTEND_IMAGE   Full frontend container image reference to deploy

Optional environment variables:
  DB_PASSWORD=spending-tracker-dev   Override for production or existing PVCs
  DEFAULT_DB_PASSWORD=spending-tracker-dev
  LLM_SETTINGS_ENCRYPTION_KEY=...  Override AI provider key encryption secret
  DEFAULT_LLM_SETTINGS_ENCRYPTION_KEY=spending-tracker-llm-dev
  KUBECTL=kubectl
  NAMESPACE=spending-tracker
  APP_HOST=spending.local
  APP_ENV=production
  DATABASE_HOST=postgres
  DATABASE_NAME=personal_spending_tracker
  DATABASE_PORT=5432
  DATABASE_USER=spending_tracker
  UPLOAD_DIR=/app/uploads
  USD_TWD_EXCHANGE_RATE=32
  AUDIT_LOG_RETENTION_DAYS=365
  STATEMENT_RETENTION_DAYS=0
  POSTGRES_IMAGE=postgres:16-alpine
  POSTGRES_STORAGE_CLASS=local-path
  POSTGRES_STORAGE_SIZE=8Gi
  UPLOADS_STORAGE_SIZE=5Gi
  IMAGE_PULL_POLICY=IfNotPresent
  APP_IMAGE_PULL_POLICY=IfNotPresent  Backend/frontend only (LOCAL_DEPLOY sets Never)
  ROLLOUT_TIMEOUT=180s
  IMAGE_PULL_SECRET=
  FRONTEND_NODE_PORT=30081
  INGRESS_ENABLED=0   Set to 1 to also create Traefik ingress on port 80
  ENABLE_K3S_ON_BOOT=1   Run systemctl enable k3s so the cluster starts on reboot
  BUILD_IMAGES=0   Set to 1 to build and push BACKEND_IMAGE/FRONTEND_IMAGE first
  LOCAL_DEPLOY=0   Set to 1 for single-node k3s: build, import into k3s, deploy
  LOCAL_BACKEND_IMAGE=personal-spending-tracker-backend:latest
  LOCAL_FRONTEND_IMAGE=personal-spending-tracker-frontend:latest
  K3S_CTR='sudo k3s ctr'

Examples:
  LOCAL_DEPLOY=1 KUBECTL='sudo k3s kubectl' bash scripts/deploy-k3s.sh

  DB_PASSWORD='change-me' \
  BACKEND_IMAGE='ghcr.io/acme/personal-spending-tracker-backend:2026-05-17' \
  FRONTEND_IMAGE='ghcr.io/acme/personal-spending-tracker-frontend:2026-05-17' \
  APP_HOST='spending.example.internal' \
  bash scripts/deploy-k3s.sh

  BUILD_IMAGES=1 \
  DB_PASSWORD='change-me' \
  BACKEND_IMAGE='ghcr.io/acme/personal-spending-tracker-backend:2026-05-17' \
  FRONTEND_IMAGE='ghcr.io/acme/personal-spending-tracker-frontend:2026-05-17' \
  bash scripts/deploy-k3s.sh
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: $name" >&2
    exit 1
  fi
}

init_kubectl_cmd() {
  read -r -a KUBECTL_CMD <<<"$KUBECTL"
  if [[ "${#KUBECTL_CMD[@]}" -eq 0 ]]; then
    echo "KUBECTL cannot be empty" >&2
    exit 1
  fi

  require_cmd "${KUBECTL_CMD[0]}"
}

decode_base64() {
  if base64 --help 2>&1 | grep -q -- '--decode'; then
    base64 --decode
  else
    base64 -D
  fi
}

resolve_llm_settings_encryption_key() {
  if [[ -n "${LLM_SETTINGS_ENCRYPTION_KEY:-}" ]]; then
    return
  fi

  local existing_encoded=""
  if "${KUBECTL_CMD[@]}" -n "$NAMESPACE" get secret spending-tracker-secrets >/dev/null 2>&1; then
    existing_encoded="$("${KUBECTL_CMD[@]}" -n "$NAMESPACE" get secret spending-tracker-secrets \
      -o jsonpath='{.data.LLM_SETTINGS_ENCRYPTION_KEY}' 2>/dev/null || true)"
  fi

  if [[ -n "$existing_encoded" ]]; then
    LLM_SETTINGS_ENCRYPTION_KEY="$(printf '%s' "$existing_encoded" | decode_base64)"
    echo "Reusing LLM_SETTINGS_ENCRYPTION_KEY from existing spending-tracker-secrets."
    return
  fi

  LLM_SETTINGS_ENCRYPTION_KEY="$DEFAULT_LLM_SETTINGS_ENCRYPTION_KEY"
  _using_default_llm_settings_encryption_key=1
}

init_ctr_cmd() {
  read -r -a CTR_CMD <<<"$K3S_CTR"
  if [[ "${#CTR_CMD[@]}" -eq 0 ]]; then
    echo "K3S_CTR cannot be empty" >&2
    exit 1
  fi

  require_cmd "${CTR_CMD[0]}"
}

K8S_ENVSUBST_VARS='$NAMESPACE $APP_HOST $POSTGRES_IMAGE $POSTGRES_STORAGE_CLASS $POSTGRES_STORAGE_SIZE $UPLOADS_STORAGE_SIZE $BATCH_STORAGE_SIZE $IMAGE_PULL_POLICY $BACKEND_IMAGE $FRONTEND_IMAGE $APP_IMAGE_PULL_POLICY $FRONTEND_NODE_PORT $UPLOAD_DIR'

apply_k8s_manifest() {
  local manifest_path="$1"
  if [[ ! -f "$manifest_path" ]]; then
    echo "Missing manifest: $manifest_path" >&2
    exit 1
  fi
  envsubst "$K8S_ENVSUBST_VARS" <"$manifest_path" | "${KUBECTL_CMD[@]}" apply -f -
}

systemctl_cmd() {
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo systemctl "$@"
  else
    systemctl "$@"
  fi
}

ensure_k3s_starts_on_boot() {
  if [[ "$ENABLE_K3S_ON_BOOT" != "1" ]]; then
    return
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    echo "Note: systemctl not found; ensure k3s is configured to start on boot."
    return
  fi

  if ! systemctl_cmd list-unit-files k3s.service >/dev/null 2>&1; then
    echo "Note: k3s.service not found; skip boot enable (is k3s installed?)"
    return
  fi

  if systemctl_cmd is-enabled --quiet k3s 2>/dev/null; then
    echo "k3s is enabled to start on boot."
    return
  fi

  echo "Enabling k3s to start on boot..."
  if systemctl_cmd enable k3s >/dev/null 2>&1; then
    echo "k3s will start automatically when the node reboots."
  else
    echo "Warning: could not enable k3s on boot. Run: sudo systemctl enable k3s" >&2
  fi
}

apply_image_pull_secrets() {
  if [[ -z "$IMAGE_PULL_SECRET" ]]; then
    return
  fi

  local deployment
  for deployment in postgres backend frontend; do
    "${KUBECTL_CMD[@]}" -n "$NAMESPACE" patch deployment "$deployment" --type merge -p "$(cat <<EOF
{
  "spec": {
    "template": {
      "spec": {
        "imagePullSecrets": [
          { "name": "$IMAGE_PULL_SECRET" }
        ]
      }
    }
  }
}
EOF
)"
  done
}

library_image_ref() {
  local image_ref="$1"
  if [[ "$image_ref" == */* ]]; then
    printf '%s\n' "$image_ref"
    return
  fi

  printf 'docker.io/library/%s\n' "$image_ref"
}

import_image_to_k3s() {
  local image_ref="$1"
  local library_ref
  library_ref="$(library_image_ref "$image_ref")"

  echo "Importing $library_ref into k3s"
  docker tag "$image_ref" "$library_ref"
  docker save "$library_ref" | "${CTR_CMD[@]}" images import -
}

build_and_import_local_images() {
  require_cmd docker
  init_ctr_cmd

  BACKEND_IMAGE="$LOCAL_BACKEND_IMAGE"
  FRONTEND_IMAGE="$LOCAL_FRONTEND_IMAGE"
  APP_IMAGE_PULL_POLICY="Never"

  local docker_build_args=()
  if [[ "${BUILD_NO_CACHE:-0}" == "1" ]]; then
    docker_build_args+=(--no-cache)
  fi

  echo "Building backend image: $BACKEND_IMAGE"
  docker build "${docker_build_args[@]}" -f "$ROOT_DIR/backend/Dockerfile" -t "$BACKEND_IMAGE" "$ROOT_DIR"

  echo "Building frontend image: $FRONTEND_IMAGE"
  docker build \
    "${docker_build_args[@]}" \
    -f "$ROOT_DIR/frontend/Dockerfile" \
    --build-arg API_PROXY_TARGET=http://backend:8000 \
    -t "$FRONTEND_IMAGE" \
    "$ROOT_DIR"

  import_image_to_k3s "$BACKEND_IMAGE"
  import_image_to_k3s "$FRONTEND_IMAGE"

  list_imported_images
}

list_imported_images() {
  # Use k3s-bundled crictl; standalone crictl often lacks CRI_CONFIG_FILE and prints:
  # FATA load config file: stat .../crictl.yaml: no such file or directory
  if command -v k3s >/dev/null 2>&1; then
    local crictl_cmd=(k3s crictl)
    if [[ "$(id -u)" -ne 0 ]]; then
      crictl_cmd=(sudo k3s crictl)
    fi
    if "${crictl_cmd[@]}" images >/dev/null 2>&1; then
      echo "Imported images:"
      "${crictl_cmd[@]}" images | grep personal-spending-tracker || true
    fi
  fi
}

init_kubectl_cmd
require_cmd envsubst
ensure_k3s_starts_on_boot

_using_default_db_password=0
if [[ -z "${DB_PASSWORD:-}" ]]; then
  DB_PASSWORD="$DEFAULT_DB_PASSWORD"
  _using_default_db_password=1
fi

if [[ "$_using_default_db_password" == "1" ]]; then
  echo "Using default DB_PASSWORD ($DB_PASSWORD). Set DB_PASSWORD to override."
fi

_using_default_llm_settings_encryption_key=0

if [[ "$LOCAL_DEPLOY" == "1" ]]; then
  build_and_import_local_images
elif [[ "$BUILD_IMAGES" == "1" ]]; then
  require_env BACKEND_IMAGE
  require_env FRONTEND_IMAGE
  require_cmd docker

  echo "Building backend image: $BACKEND_IMAGE"
  docker build -f "$ROOT_DIR/backend/Dockerfile" -t "$BACKEND_IMAGE" "$ROOT_DIR"
  docker push "$BACKEND_IMAGE"

  echo "Building frontend image: $FRONTEND_IMAGE"
  docker build \
    -f "$ROOT_DIR/frontend/Dockerfile" \
    --build-arg API_PROXY_TARGET=http://backend:8000 \
    -t "$FRONTEND_IMAGE" \
    "$ROOT_DIR"
  docker push "$FRONTEND_IMAGE"
else
  require_env BACKEND_IMAGE
  require_env FRONTEND_IMAGE
fi

export NAMESPACE APP_HOST POSTGRES_IMAGE POSTGRES_STORAGE_CLASS POSTGRES_STORAGE_SIZE
export UPLOADS_STORAGE_SIZE BATCH_STORAGE_SIZE IMAGE_PULL_POLICY UPLOAD_DIR
export BACKEND_IMAGE FRONTEND_IMAGE APP_IMAGE_PULL_POLICY FRONTEND_NODE_PORT

echo "Applying namespace: $NAMESPACE"
apply_k8s_manifest "$ROOT_DIR/k8s/00-namespace.yaml"

resolve_llm_settings_encryption_key
if [[ "$_using_default_llm_settings_encryption_key" == "1" ]]; then
  echo "Using default LLM_SETTINGS_ENCRYPTION_KEY. Set LLM_SETTINGS_ENCRYPTION_KEY to override."
fi

echo "Applying config map"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" create configmap spending-tracker-config \
  --from-literal=APP_ENV="$APP_ENV" \
  --from-literal=DATABASE_HOST="$DATABASE_HOST" \
  --from-literal=DATABASE_NAME="$DATABASE_NAME" \
  --from-literal=DATABASE_PORT="$DATABASE_PORT" \
  --from-literal=DATABASE_USER="$DATABASE_USER" \
  --from-literal=UPLOAD_DIR="$UPLOAD_DIR" \
  --from-literal=BATCH_DATA_DIR="$BATCH_DATA_DIR" \
  --from-literal=BATCH_PDF_SOURCE_DIR="$BATCH_PDF_SOURCE_DIR" \
  --from-literal=BATCH_TEXT_SOURCE_DIR="$BATCH_TEXT_SOURCE_DIR" \
  --from-literal=USD_TWD_EXCHANGE_RATE="$USD_TWD_EXCHANGE_RATE" \
  --from-literal=AUDIT_LOG_RETENTION_DAYS="$AUDIT_LOG_RETENTION_DAYS" \
  --from-literal=STATEMENT_RETENTION_DAYS="$STATEMENT_RETENTION_DAYS" \
  --dry-run=client -o yaml | "${KUBECTL_CMD[@]}" apply -f -

echo "Applying secrets"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" create secret generic spending-tracker-secrets \
  --from-literal=DATABASE_PASSWORD="$DB_PASSWORD" \
  --from-literal=LLM_SETTINGS_ENCRYPTION_KEY="$LLM_SETTINGS_ENCRYPTION_KEY" \
  --dry-run=client -o yaml | "${KUBECTL_CMD[@]}" apply -f -

echo "Applying uploads PVC"
apply_k8s_manifest "$ROOT_DIR/k8s/60-uploads-pvc.yaml"

echo "Applying batch-import PVC"
apply_k8s_manifest "$ROOT_DIR/k8s/61-batch-pvc.yaml"

echo "Applying PostgreSQL resources"
apply_k8s_manifest "$ROOT_DIR/k8s/20-postgres.yaml"
echo "Waiting for Postgres before starting backend (avoids DB connection crash)"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/postgres --timeout="$ROLLOUT_TIMEOUT"

echo "Applying backend resources"
apply_k8s_manifest "$ROOT_DIR/k8s/30-backend.yaml"
echo "Restarting backend to pick up image and probe changes"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout restart deployment/backend

echo "Applying frontend resources"
apply_k8s_manifest "$ROOT_DIR/k8s/40-frontend.yaml"

apply_image_pull_secrets

if [[ "$INGRESS_ENABLED" == "1" ]]; then
  echo "Applying ingress"
  apply_k8s_manifest "$ROOT_DIR/k8s/50-ingress.yaml"
else
  echo "Skipping ingress (NodePort access only)"
  "${KUBECTL_CMD[@]}" -n "$NAMESPACE" delete ingress spending-tracker --ignore-not-found
  "${KUBECTL_CMD[@]}" -n "$NAMESPACE" delete middleware strip-api-prefix --ignore-not-found
fi

echo "Waiting for rollouts"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/postgres --timeout="$ROLLOUT_TIMEOUT"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/backend --timeout="$ROLLOUT_TIMEOUT"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/frontend --timeout="$ROLLOUT_TIMEOUT"

echo
echo "Deployment complete."
echo "Data: Postgres/uploads/batch PVCs are preserved across redeploys (kubectl apply only)."
echo "      Do not delete namespace or PVCs unless you intend to wipe data."
echo "Boot: With k3s enabled on boot, postgres/backend/frontend pods restart automatically after a node reboot."
echo "Namespace: $NAMESPACE"
if [[ "$INGRESS_ENABLED" == "1" ]]; then
  echo "Ingress: http://$APP_HOST"
fi
NODE_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -n "$NODE_IP" ]]; then
  echo "NodePort: http://$NODE_IP:$FRONTEND_NODE_PORT"
  echo "Health: curl http://$NODE_IP:$FRONTEND_NODE_PORT/api/health"
else
  echo "NodePort: http://<node-ip>:$FRONTEND_NODE_PORT"
fi
echo
echo "Useful checks:"
echo "  $KUBECTL -n $NAMESPACE get deployments,pods,services,ingress,pvc"
echo "  $KUBECTL -n $NAMESPACE logs deployment/backend"
echo "  $KUBECTL -n $NAMESPACE logs deployment/frontend"
echo "  curl http://$APP_HOST/api/health"
