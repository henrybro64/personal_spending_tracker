# Personal Spending Tracker on k3s

This directory contains the smallest working Kubernetes setup for running the MVP on a single Linux machine with k3s.

The manifests assume:

- namespace: `spending-tracker`
- ingress hostname: `spending.local`
- default k3s `local-path` storage class
- one backend pod, one frontend pod, one PostgreSQL pod
- uploaded PDFs stored on a PVC mounted at `/app/uploads`

## Quick deploy script

If you already have a working k3s cluster and a registry your node can pull from, you can deploy without editing the YAML by hand:

```bash
DB_PASSWORD='replace-me' \
BACKEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-backend:TAG' \
FRONTEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-frontend:TAG' \
APP_HOST='spending.local' \
bash scripts/deploy-k3s.sh
```

To build and push the images first:

```bash
BUILD_IMAGES=1 \
DB_PASSWORD='replace-me' \
BACKEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-backend:TAG' \
FRONTEND_IMAGE='YOUR_REGISTRY/personal-spending-tracker-frontend:TAG' \
APP_HOST='spending.local' \
bash scripts/deploy-k3s.sh
```

If you are running directly on the k3s node and only have `sudo k3s kubectl` available, prefix the command with:

```bash
KUBECTL='sudo k3s kubectl'
```

## 1. Install k3s

Install k3s on the Linux machine:

```bash
curl -sfL https://get.k3s.io | sh -
```

Verify the cluster is healthy:

```bash
sudo k3s kubectl get nodes -o wide
sudo k3s kubectl get pods -A
```

If you want to use `kubectl` without the `sudo k3s` prefix:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$(id -u)":"$(id -g)" ~/.kube/config
kubectl get nodes
```

## 2. Build and push container images

Build the backend image from the repository root:

```bash
docker build -f backend/Dockerfile -t YOUR_REGISTRY/personal-spending-tracker-backend:TAG .
docker push YOUR_REGISTRY/personal-spending-tracker-backend:TAG
```

Build the frontend image from the repository root:

```bash
docker build -f frontend/Dockerfile -t YOUR_REGISTRY/personal-spending-tracker-frontend:TAG .
docker push YOUR_REGISTRY/personal-spending-tracker-frontend:TAG
```

Update the image names in the manifests before applying them:

```bash
sed -i 's#ghcr.io/example/personal-spending-tracker-backend:latest#YOUR_REGISTRY/personal-spending-tracker-backend:TAG#' k8s/30-backend.yaml
sed -i 's#ghcr.io/example/personal-spending-tracker-frontend:latest#YOUR_REGISTRY/personal-spending-tracker-frontend:TAG#' k8s/40-frontend.yaml
```

## 3. Create the namespace and secret

Apply the namespace and config first:

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/10-configmap.yaml
```

Create a real secret file from the example, then apply it:

```bash
cp k8s/11-secret.example.yaml /tmp/spending-tracker-secret.yaml
```

Edit `/tmp/spending-tracker-secret.yaml` and replace the placeholder password with a real PostgreSQL password. Then apply it:

```bash
kubectl apply -f /tmp/spending-tracker-secret.yaml
```

## 4. Deploy PostgreSQL, storage, backend, frontend, and ingress

Apply the remaining manifests:

```bash
kubectl apply -f k8s/20-postgres.yaml
kubectl apply -f k8s/60-uploads-pvc.yaml
kubectl apply -f k8s/30-backend.yaml
kubectl apply -f k8s/40-frontend.yaml
kubectl apply -f k8s/50-ingress.yaml
```

Check rollout and resources:

```bash
kubectl -n spending-tracker get deployments,pods,services,ingress,pvc
kubectl -n spending-tracker rollout status deployment/postgres
kubectl -n spending-tracker rollout status deployment/backend
kubectl -n spending-tracker rollout status deployment/frontend
```

## 5. Point `spending.local` at the k3s node

Add the node IP to your client machine’s `/etc/hosts`:

```text
NODE_IP spending.local
```

Then test ingress and backend health:

```bash
curl http://spending.local/api/health
curl -I http://spending.local/
```

The frontend calls the backend through `/api`, and the ingress strips the `/api` prefix before forwarding traffic to FastAPI.

## 6. Verify each component

Verify PostgreSQL:

```bash
kubectl -n spending-tracker logs deployment/postgres
```

Verify the backend:

```bash
kubectl -n spending-tracker logs deployment/backend
curl http://spending.local/api/health
```

Verify the frontend:

```bash
kubectl -n spending-tracker logs deployment/frontend
```

Then open `http://spending.local` in a browser.

Verify upload persistence:

1. Upload a test PDF through the UI at `http://spending.local`.
2. Check that the file exists on the backend volume:

```bash
kubectl -n spending-tracker exec deployment/backend -- ls -lah /app/uploads
```

## 7. Update images later

After pushing a new image tag, either update the manifest and re-apply it or patch the deployment directly:

```bash
kubectl -n spending-tracker set image deployment/backend backend=YOUR_REGISTRY/personal-spending-tracker-backend:NEW_TAG
kubectl -n spending-tracker set image deployment/frontend frontend=YOUR_REGISTRY/personal-spending-tracker-frontend:NEW_TAG
```

## 8. Troubleshooting

Check logs:

```bash
kubectl -n spending-tracker logs deployment/postgres
kubectl -n spending-tracker logs deployment/backend
kubectl -n spending-tracker logs deployment/frontend
```

Describe failing resources:

```bash
kubectl -n spending-tracker describe pods
kubectl -n spending-tracker describe ingress spending-tracker
kubectl -n spending-tracker describe pvc postgres-data
kubectl -n spending-tracker describe pvc uploads-data
```

## 9. Delete the deployment

Delete the whole namespace when you want to remove everything:

```bash
kubectl delete namespace spending-tracker
```

## Notes and tradeoffs

- `local-path` storage is tied to the single node. If the node fails, the PVC data does not follow to another machine automatically.
- This setup is single-node only. It does not handle high availability, replication, or failover.
- PostgreSQL data and uploaded PDFs need external backups if you care about recovery.
- There is no TLS automation in this first pass. Add a certificate manager or reverse proxy TLS setup before exposing this beyond a private network.
- There is no auth layer in this first pass. Treat the deployment as private and trusted-network only.
- Uploaded PDFs live on a filesystem PVC now. If storage requirements grow, the next clean step is moving uploads to MinIO or another S3-compatible object store.
