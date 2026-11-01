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

- `/app/batch/pdf/user-<id>/` — PDF statements for one user
- `/app/batch/text/user-<id>/` — text statements for one user
- `/app/batch/data` — batch SQLite / exports (`batch_imports_user_<id>.sqlite3`)

**Run batch import** reads only the signed-in user's `user-<id>` folders. Files in the root of `/app/batch/pdf` or `/app/batch/text` are ignored. The first account created is normally `user-1`.

Copy files into the backend pod, review at `/batch-review`, then **Sync to dashboard** for Postgres:

```bash
POD=$(sudo k3s kubectl -n spending-tracker get pods -l app.kubernetes.io/name=backend -o jsonpath='{.items[0].metadata.name}')
sudo k3s kubectl -n spending-tracker exec "$POD" -c backend -- mkdir -p /app/batch/pdf/user-1
sudo k3s kubectl -n spending-tracker cp ./Credit_Card_PDF/. "$POD:/app/batch/pdf/user-1/" -c backend
```

**Upgrading from a release that used a shared batch folder:** move existing statements from the folder root into the owner's folder, or the next import finds nothing:

```bash
sudo k3s kubectl -n spending-tracker exec "$POD" -c backend -- sh -c '
  for kind in pdf text; do
    mkdir -p /app/batch/$kind/user-1
    find /app/batch/$kind -maxdepth 1 -type f -exec mv {} /app/batch/$kind/user-1/ \;
  done'
```

## Verify and troubleshoot

```bash
kubectl -n spending-tracker get deployments,pods,services,pvc
kubectl -n spending-tracker rollout status deployment/backend
curl http://<node-ip>:30081/api/health
kubectl -n spending-tracker logs deployment/backend
bash scripts/triage-k3s-pods.sh   # pods Error / ContainerCreating
```

**Backend stays up while Postgres is starting:** the API process does not wait for Postgres. `/health` stays available, and `/ready` applies migrations once the database accepts connections. The pod stays out of rotation until that probe succeeds.

**Sign-in shows "API proxy to backend failed: fetch failed" after a node restart, Postgres in `CrashLoopBackOff` with empty logs** (issue #101): the backend's `/ready` returns 503 because Postgres never started, so `kubectl -n spending-tracker get endpoints backend` is empty. On 2026-11-01 the cause was the image's entrypoint hanging in `gosu` (100% CPU, nothing listening on 5432) until the liveness probe killed it, over and over. Check with:

```bash
kubectl -n spending-tracker exec deploy/postgres -- ps aux   # PID 1 is "gosu postgres ..." instead of "postgres"
```

`20-postgres.yaml` now avoids this. The pod runs as the `postgres` user (`POSTGRES_UID`, default `70` for `-alpine` images, `999` for Debian-based `postgres:16`), so the entrypoint skips `gosu`, and an init container makes the data directory owned by that user. A startup probe gives Postgres up to 5 minutes to start before it is restarted (e.g. crash recovery). The `Recreate` strategy means a rollout stops the old Postgres pod before starting the new one, so two pods never share the data volume. If you hit this on an older manifest, redeploy with `scripts/deploy-k3s.sh`.

**Restarting Postgres safely:** use `kubectl -n spending-tracker rollout restart deployment/postgres`. `Recreate` makes a rollout wait until the old pod is gone before it creates the new one. **Do not `kubectl delete pod` the Postgres pod**, and do not run `scale --replicas=0` and `--replicas=1` back to back. `Recreate` does not cover either case: the replacement can start while the old pod is still shutting down, so for a moment two Postgres processes can open the same data directory. Postgres cannot detect this across containers. If you need Postgres stopped for a while (e.g. to copy the volume), wait for the pod to be deleted before scaling back up:

```bash
kubectl -n spending-tracker scale deployment/postgres --replicas=0
kubectl -n spending-tracker wait --for=delete pod -l app.kubernetes.io/name=postgres --timeout=180s
# ... maintenance ...
kubectl -n spending-tracker scale deployment/postgres --replicas=1
```

**Brief database downtime on deploy:** because of `Recreate`, a deploy that changes the Postgres pod (image, probes, `POSTGRES_UID`, a new `IMAGE_PULL_SECRET`) stops Postgres before starting the new pod. Expect roughly 10–20 seconds of database downtime, during which the backend's `/ready` reports 503. A deploy that leaves the Postgres manifest unchanged does not restart it. `deploy-k3s.sh` waits up to `POSTGRES_ROLLOUT_TIMEOUT` (default `330s`, longer than the 5-minute startup probe) for Postgres to become ready.

**Backups:** see [docs/backup-restore.md](../docs/backup-restore.md). Use `KUBECTL='sudo k3s kubectl' ./scripts/backup-spending-tracker.sh --k3s` before major upgrades.

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
  python3 -m backend.diagnose_pdf_merchants /app/batch/pdf/user-1 \
  --sqlite /app/batch/data/batch_imports_user_1.sqlite3 --only CTBC_card
```

Paste the full output into a bug report. Compare **sample raw lines** (text in PDF) vs **parsed merchants** (what the importer stored).

## Reset import data (test batch re-import)

After deploying new parser/category code, clear merged Postgres rows and batch SQLite so the next **Run batch import** starts fresh. PDFs under `/app/batch/pdf/user-<id>/` are kept.

```bash
# On the cluster node
KUBECTL='sudo k3s kubectl' ./scripts/reset-import-data.sh --k3s -y
```

Local dev (requires `DATABASE_URL` and `psql`):

```bash
export DATABASE_URL='postgresql://...'
./scripts/reset-import-data.sh -y
```

Options: `--dry-run`, `--full-postgres` (also deletes users — you must sign up again).

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
