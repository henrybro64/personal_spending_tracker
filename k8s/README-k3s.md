# Personal Spending Tracker on k3s

Kubernetes manifests for a single-node k3s deployment. **`scripts/deploy-k3s.sh` is the supported deploy path** — it renders these YAML files with `envsubst` and applies them.

Defaults:

- namespace: `spending-tracker` (`NAMESPACE`)
- frontend NodePort: `30081` (`FRONTEND_NODE_PORT`)
- optional ingress: `spending.local` when `INGRESS_ENABLED=1`
- Postgres + upload/batch PVCs on k3s `local-path` storage

## Quick deploy

### One-command on the k3s node (build + import + apply)

```bash
LOCAL_DEPLOY=1 \
KUBECTL='sudo k3s kubectl' \
bash scripts/deploy-k3s.sh
```

`DB_PASSWORD` defaults to `spending-tracker-dev` when unset. Set `DB_PASSWORD` if an existing Postgres PVC was created with a different password.

### Registry images

```bash
BACKEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-backend:TAG' \
FRONTEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-frontend:TAG' \
bash scripts/deploy-k3s.sh
```

Optional: `INGRESS_ENABLED=1`, `FRONTEND_NODE_PORT=30082`, `IMAGE_PULL_SECRET=...`

Open `http://<node-ip>:30081` after rollout.

## Install k3s

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes -o wide
```

Optional: copy `/etc/rancher/k3s/k3s.yaml` to `~/.kube/config` for plain `kubectl`.

## Start automatically after node reboot

Two things must be true:

1. **k3s starts when the machine boots** — `scripts/deploy-k3s.sh` runs `systemctl enable k3s` by default (`ENABLE_K3S_ON_BOOT=1`). Verify:

   ```bash
   systemctl is-enabled k3s   # should print "enabled"
   ```

   If not:

   ```bash
   sudo systemctl enable k3s
   sudo systemctl start k3s
   ```

2. **The app was deployed at least once** — Kubernetes keeps Deployments running. After reboot, pods in `spending-tracker` come back on their own (same Postgres/upload data on PVCs).

You do **not** need to run the deploy script after every reboot. Redeploy only when you change images or config.

Test after a reboot:

```bash
sudo reboot
# after the node is back:
sudo k3s kubectl -n spending-tracker get pods
curl http://<node-ip>:30081/api/health
```

## Manifest layout

| File | Purpose |
|------|---------|
| `00-namespace.yaml` | Namespace |
| `10-configmap.yaml` | Reference config (deploy script creates ConfigMap from env) |
| `11-secret.example.yaml` | Secret template |
| `20-postgres.yaml` | Postgres PVC, Deployment, Service |
| `30-backend.yaml` | Backend Deployment, Service |
| `40-frontend.yaml` | Frontend Deployment, NodePort Service |
| `50-ingress.yaml` | Traefik ingress + API strip prefix |
| `60-uploads-pvc.yaml` | Upload PDF PVC |
| `61-batch-pvc.yaml` | Batch import PVC |

YAML uses `${VAR}` placeholders (`NAMESPACE`, `BACKEND_IMAGE`, etc.). Do not `kubectl apply -f` them raw — use `scripts/deploy-k3s.sh` or export the same variables and pipe through `envsubst`.

## Batch import on the cluster

Backend mounts `batch-data` PVC at `/app/batch`:

- `/app/batch/pdf` — PDF statements
- `/app/batch/text` — text statements
- `/app/batch/data` — batch SQLite / exports

Copy files into the backend pod, review at `/batch-review`, then **Sync to dashboard** for Postgres.

## Verify and troubleshoot

```bash
kubectl -n spending-tracker get deployments,pods,services,pvc
kubectl -n spending-tracker rollout status deployment/backend
curl http://<node-ip>:30081/api/health
kubectl -n spending-tracker logs deployment/backend
bash scripts/triage-k3s-pods.sh   # pods Error / ContainerCreating
```

**Backend `Error` while postgres is `ContainerCreating`:** the API runs `init_db()` on startup and exits if Postgres is not up yet. Wait until postgres is `Running`, then `kubectl -n spending-tracker rollout restart deployment/backend`. Redeploy with `scripts/deploy-k3s.sh` applies a `wait-for-postgres` init container so this race does not recur.

**Password auth failed after changing `DB_PASSWORD`:** Postgres PVC keeps the old password. Delete the namespace and redeploy, or `ALTER USER` inside the Postgres pod.

**Image pull errors on local k3s:** use `LOCAL_DEPLOY=1` so images are built and imported on the node.

**Batch import returns HTTP 500 with no Python traceback:** check `kubectl -n spending-tracker get events` for `Liveness probe failed` on `deployment/backend`. A long import blocks the API worker; Kubernetes may kill the pod before the request finishes. Redeploy with updated probe settings in `30-backend.yaml`, or import fewer PDFs per run.

**Debug missing merchants (Uber / 優食) in CTBC PDFs:**

```bash
# On the k3s node (from repo checkout):
bash scripts/diagnose-ctbc-in-pod.sh
bash scripts/diagnose-ctbc-in-pod.sh CTBC_card   # only filenames containing CTBC_card

# Or manually:
POD=$(sudo k3s kubectl -n spending-tracker get pods -l app.kubernetes.io/name=backend -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n spending-tracker exec -it "$POD" -c backend -- \
  python3 -m backend.diagnose_pdf_merchants /app/batch/pdf \
  --sqlite /app/batch/data/batch_imports.sqlite3 --only CTBC_card
```

Paste the full output into a bug report. Compare **sample raw lines** (text in PDF) vs **parsed merchants** (what the importer stored).

## Data persistence on redeploy

**Normal redeploy keeps your data.** `scripts/deploy-k3s.sh` only runs `kubectl apply`. It does not delete the namespace, PVCs, or Postgres data.

| Action | Database & uploads |
|--------|-------------------|
| `bash scripts/deploy-k3s.sh` again (same node) | **Kept** — PVCs `postgres-data`, `uploads-data`, `batch-data` stay on disk |
| Node reboot (k3s enabled) | **Kept** — pods restart, PVCs unchanged |
| Change `DB_PASSWORD` in secret only | **Kept** — data remains; align Postgres password or backend cannot connect |
| `kubectl delete namespace spending-tracker` | **Wiped** — namespace deletion removes PVCs and all data |
| `kubectl delete pvc postgres-data` (etc.) | **Wiped** — that volume’s data is gone |

Backend startup runs `CREATE TABLE IF NOT EXISTS` only; it does not drop or truncate tables.

**Safe redeploy:**

```bash
LOCAL_DEPLOY=1 KUBECTL='sudo k3s kubectl' bash scripts/deploy-k3s.sh
```

Use the **same** `DB_PASSWORD` as when the Postgres PVC was first created (default: `spending-tracker-dev`).

## Delete

```bash
kubectl delete namespace spending-tracker
```

## Notes

- Single-node `local-path` storage — no HA.
- Back up Postgres PVC and uploads PVC if you care about recovery.
- Multi-user auth is enabled; sign in before using the app.
