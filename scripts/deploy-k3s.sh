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
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
FRONTEND_NODE_PORT="${FRONTEND_NODE_PORT:-30081}"
INGRESS_ENABLED="${INGRESS_ENABLED:-0}"
DEFAULT_DB_PASSWORD="${DEFAULT_DB_PASSWORD:-spending-tracker-dev}"
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
  KUBECTL=kubectl
  NAMESPACE=spending-tracker
  APP_HOST=spending.local
  APP_ENV=production
  DATABASE_HOST=postgres
  DATABASE_NAME=personal_spending_tracker
  DATABASE_PORT=5432
  DATABASE_USER=spending_tracker
  UPLOAD_DIR=/app/uploads
  POSTGRES_IMAGE=postgres:16-alpine
  POSTGRES_STORAGE_CLASS=local-path
  POSTGRES_STORAGE_SIZE=8Gi
  UPLOADS_STORAGE_SIZE=5Gi
  IMAGE_PULL_POLICY=IfNotPresent
  APP_IMAGE_PULL_POLICY=IfNotPresent  Backend/frontend only (LOCAL_DEPLOY sets Never)
  ROLLOUT_TIMEOUT=180s
  IMAGE_PULL_SECRET=
  OPENAI_API_KEY=
  FRONTEND_NODE_PORT=30081
  INGRESS_ENABLED=0   Set to 1 to also create Traefik ingress on port 80
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

init_ctr_cmd() {
  read -r -a CTR_CMD <<<"$K3S_CTR"
  if [[ "${#CTR_CMD[@]}" -eq 0 ]]; then
    echo "K3S_CTR cannot be empty" >&2
    exit 1
  fi

  require_cmd "${CTR_CMD[0]}"
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

  echo "Building backend image: $BACKEND_IMAGE"
  docker build -f "$ROOT_DIR/backend/Dockerfile" -t "$BACKEND_IMAGE" "$ROOT_DIR"

  echo "Building frontend image: $FRONTEND_IMAGE"
  docker build \
    -f "$ROOT_DIR/frontend/Dockerfile" \
    --build-arg API_PROXY_TARGET=http://backend:8000 \
    -t "$FRONTEND_IMAGE" \
    "$ROOT_DIR"

  import_image_to_k3s "$BACKEND_IMAGE"
  import_image_to_k3s "$FRONTEND_IMAGE"

  if command -v crictl >/dev/null 2>&1; then
    echo "Imported images:"
    crictl images | grep personal-spending-tracker || true
  elif [[ -x /usr/local/bin/crictl ]] || sudo -n crictl version >/dev/null 2>&1; then
    echo "Imported images:"
    sudo crictl images | grep personal-spending-tracker || true
  fi
}

init_kubectl_cmd

_using_default_db_password=0
if [[ ! -v DB_PASSWORD ]]; then
  DB_PASSWORD="$DEFAULT_DB_PASSWORD"
  _using_default_db_password=1
fi

if [[ "$_using_default_db_password" == "1" ]]; then
  echo "Using default DB_PASSWORD ($DB_PASSWORD). Set DB_PASSWORD to override."
fi

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

IMAGE_PULL_SECRETS_BLOCK=""
if [[ -n "$IMAGE_PULL_SECRET" ]]; then
  IMAGE_PULL_SECRETS_BLOCK=$(cat <<EOF
      imagePullSecrets:
        - name: $IMAGE_PULL_SECRET
EOF
)
fi

echo "Applying namespace: $NAMESPACE"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: v1
kind: Namespace
metadata:
  name: $NAMESPACE
EOF

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
  --dry-run=client -o yaml | "${KUBECTL_CMD[@]}" apply -f -

echo "Applying secrets"
secret_cmd=(
  "${KUBECTL_CMD[@]}" -n "$NAMESPACE" create secret generic spending-tracker-secrets
  "--from-literal=DATABASE_PASSWORD=$DB_PASSWORD"
  --dry-run=client
  -o
  yaml
)
if [[ -n "$OPENAI_API_KEY" ]]; then
  secret_cmd+=("--from-literal=OPENAI_API_KEY=$OPENAI_API_KEY")
fi
"${secret_cmd[@]}" | "${KUBECTL_CMD[@]}" apply -f -

echo "Applying PostgreSQL resources"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $POSTGRES_STORAGE_CLASS
  resources:
    requests:
      storage: $POSTGRES_STORAGE_SIZE
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: postgres
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: postgres
  template:
    metadata:
      labels:
        app.kubernetes.io/name: postgres
        app.kubernetes.io/part-of: personal-spending-tracker
    spec:
${IMAGE_PULL_SECRETS_BLOCK}
      containers:
        - name: postgres
          image: $POSTGRES_IMAGE
          imagePullPolicy: $IMAGE_PULL_POLICY
          ports:
            - containerPort: 5432
              name: postgres
          env:
            - name: POSTGRES_DB
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_NAME
            - name: POSTGRES_USER
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_USER
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: spending-tracker-secrets
                  key: DATABASE_PASSWORD
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
          readinessProbe:
            exec:
              command:
                - sh
                - -c
                - pg_isready -U "\$POSTGRES_USER" -d "\$POSTGRES_DB"
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            exec:
              command:
                - sh
                - -c
                - pg_isready -U "\$POSTGRES_USER" -d "\$POSTGRES_DB"
            initialDelaySeconds: 15
            periodSeconds: 20
      volumes:
        - name: postgres-data
          persistentVolumeClaim:
            claimName: postgres-data
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: postgres
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  selector:
    app.kubernetes.io/name: postgres
  ports:
    - name: postgres
      port: 5432
      targetPort: postgres
EOF

echo "Applying uploads PVC"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: uploads-data
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $POSTGRES_STORAGE_CLASS
  resources:
    requests:
      storage: $UPLOADS_STORAGE_SIZE
EOF

echo "Applying batch-import PVC"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: batch-data
  namespace: $NAMESPACE
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: $POSTGRES_STORAGE_CLASS
  resources:
    requests:
      storage: $BATCH_STORAGE_SIZE
EOF

echo "Applying backend resources"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: backend
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: backend
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: backend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: backend
        app.kubernetes.io/part-of: personal-spending-tracker
    spec:
${IMAGE_PULL_SECRETS_BLOCK}
      initContainers:
        - name: batch-dir-init
          image: busybox:1.36
          command:
            - sh
            - -c
            - mkdir -p /app/batch/data /app/batch/pdf /app/batch/text
          volumeMounts:
            - name: batch-data
              mountPath: /app/batch
      containers:
        - name: backend
          image: $BACKEND_IMAGE
          imagePullPolicy: $APP_IMAGE_PULL_POLICY
          ports:
            - containerPort: 8000
              name: http
          env:
            - name: APP_ENV
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: APP_ENV
            - name: DB_HOST
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_HOST
            - name: DB_NAME
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_NAME
            - name: DB_PORT
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_PORT
            - name: DB_USER
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: DATABASE_USER
            - name: DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: spending-tracker-secrets
                  key: DATABASE_PASSWORD
            - name: DATABASE_URL
              value: postgresql://\$(DB_USER):\$(DB_PASSWORD)@\$(DB_HOST):\$(DB_PORT)/\$(DB_NAME)
            - name: UPLOAD_DIR
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: UPLOAD_DIR
            - name: BATCH_DATA_DIR
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: BATCH_DATA_DIR
            - name: BATCH_PDF_SOURCE_DIR
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: BATCH_PDF_SOURCE_DIR
            - name: BATCH_TEXT_SOURCE_DIR
              valueFrom:
                configMapKeyRef:
                  name: spending-tracker-config
                  key: BATCH_TEXT_SOURCE_DIR
          volumeMounts:
            - name: uploads-data
              mountPath: $UPLOAD_DIR
            - name: batch-data
              mountPath: /app/batch
          readinessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /health
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
      volumes:
        - name: uploads-data
          persistentVolumeClaim:
            claimName: uploads-data
        - name: batch-data
          persistentVolumeClaim:
            claimName: batch-data
---
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: backend
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  selector:
    app.kubernetes.io/name: backend
  ports:
    - name: http
      port: 8000
      targetPort: http
EOF

echo "Applying frontend resources"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: frontend
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: frontend
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: frontend
  template:
    metadata:
      labels:
        app.kubernetes.io/name: frontend
        app.kubernetes.io/part-of: personal-spending-tracker
    spec:
${IMAGE_PULL_SECRETS_BLOCK}
      containers:
        - name: frontend
          image: $FRONTEND_IMAGE
          imagePullPolicy: $APP_IMAGE_PULL_POLICY
          ports:
            - containerPort: 3000
              name: http
          readinessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /
              port: http
            initialDelaySeconds: 15
            periodSeconds: 20
---
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: frontend
    app.kubernetes.io/part-of: personal-spending-tracker
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: frontend
  ports:
    - name: http
      port: 3000
      targetPort: http
      nodePort: $FRONTEND_NODE_PORT
EOF

if [[ "$INGRESS_ENABLED" == "1" ]]; then
echo "Applying ingress"
cat <<EOF | "${KUBECTL_CMD[@]}" apply -f -
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: strip-api-prefix
  namespace: $NAMESPACE
spec:
  stripPrefix:
    prefixes:
      - /api
    forceSlash: false
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: spending-tracker
  namespace: $NAMESPACE
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
    traefik.ingress.kubernetes.io/router.middlewares: ${NAMESPACE}-strip-api-prefix@kubernetescrd
spec:
  rules:
    - host: $APP_HOST
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend:
              service:
                name: backend
                port:
                  number: 8000
          - path: /
            pathType: Prefix
            backend:
              service:
                name: frontend
                port:
                  number: 3000
EOF
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
