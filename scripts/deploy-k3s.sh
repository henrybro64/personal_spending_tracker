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
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16-alpine}"
POSTGRES_STORAGE_CLASS="${POSTGRES_STORAGE_CLASS:-local-path}"
POSTGRES_STORAGE_SIZE="${POSTGRES_STORAGE_SIZE:-8Gi}"
UPLOADS_STORAGE_SIZE="${UPLOADS_STORAGE_SIZE:-5Gi}"
IMAGE_PULL_POLICY="${IMAGE_PULL_POLICY:-IfNotPresent}"
ROLLOUT_TIMEOUT="${ROLLOUT_TIMEOUT:-180s}"
BUILD_IMAGES="${BUILD_IMAGES:-0}"
BACKEND_IMAGE="${BACKEND_IMAGE:-}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
KUBECTL_CMD=()

usage() {
  cat <<'EOF'
Deploy Personal Spending Tracker to a single-node k3s cluster.

Required environment variables:
  DB_PASSWORD      PostgreSQL password for the app database user
  BACKEND_IMAGE    Full backend container image reference to deploy
  FRONTEND_IMAGE   Full frontend container image reference to deploy

Optional environment variables:
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
  ROLLOUT_TIMEOUT=180s
  IMAGE_PULL_SECRET=
  OPENAI_API_KEY=
  BUILD_IMAGES=0   Set to 1 to build and push BACKEND_IMAGE/FRONTEND_IMAGE first

Examples:
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

init_kubectl_cmd
require_env DB_PASSWORD
require_env BACKEND_IMAGE
require_env FRONTEND_IMAGE

if [[ "$BUILD_IMAGES" == "1" ]]; then
  require_cmd docker
fi

if [[ "$BUILD_IMAGES" == "1" ]]; then
  echo "Building backend image: $BACKEND_IMAGE"
  docker build -f "$ROOT_DIR/backend/Dockerfile" -t "$BACKEND_IMAGE" "$ROOT_DIR"
  docker push "$BACKEND_IMAGE"

  echo "Building frontend image: $FRONTEND_IMAGE"
  docker build -f "$ROOT_DIR/frontend/Dockerfile" -t "$FRONTEND_IMAGE" "$ROOT_DIR"
  docker push "$FRONTEND_IMAGE"
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
      containers:
        - name: backend
          image: $BACKEND_IMAGE
          imagePullPolicy: $IMAGE_PULL_POLICY
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
          volumeMounts:
            - name: uploads-data
              mountPath: $UPLOAD_DIR
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
          imagePullPolicy: $IMAGE_PULL_POLICY
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
  selector:
    app.kubernetes.io/name: frontend
  ports:
    - name: http
      port: 3000
      targetPort: http
EOF

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

echo "Waiting for rollouts"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/postgres --timeout="$ROLLOUT_TIMEOUT"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/backend --timeout="$ROLLOUT_TIMEOUT"
"${KUBECTL_CMD[@]}" -n "$NAMESPACE" rollout status deployment/frontend --timeout="$ROLLOUT_TIMEOUT"

echo
echo "Deployment complete."
echo "Namespace: $NAMESPACE"
echo "Host: http://$APP_HOST"
echo
echo "Useful checks:"
echo "  $KUBECTL -n $NAMESPACE get deployments,pods,services,ingress,pvc"
echo "  $KUBECTL -n $NAMESPACE logs deployment/backend"
echo "  $KUBECTL -n $NAMESPACE logs deployment/frontend"
echo "  curl http://$APP_HOST/api/health"
