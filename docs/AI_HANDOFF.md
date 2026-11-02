# AI Agent Handoff

_Last updated: 2026-11-02 by Claude Code (Opus 5.5)_

## Task objective

Implement GitHub issue **#101** on branch `fix/issue-101`, double-checking the work.
Issue #101 is "[P0] k3s Postgres: stuck at startup after node restart (gosu spins), two pods on one volume during rollout" (part of #95).
Owner's constraints:

- **Do not push** to GitHub until the owner says so (to save GitHub Actions minutes).
- Do not commit, merge, or deploy without explicit instruction.

## Current status

**PR #102 open. The first commit passed CI (backend, frontend, SonarCloud). Both Codex review comments are addressed in a follow-up commit and replied to. Waiting on CI for that commit and on on-node verification.**
Find the PR with `env -u GH_TOKEN -u GITHUB_TOKEN gh pr list --head fix/issue-101`. All 5 architect-review findings were fixed before the commit.

## Git context

- Branch `fix/issue-101`, from `main` at `d5fdbc5a` (Merge PR #93). Pushed to `origin/fix/issue-101`, with one commit containing all changes, including this file.
- Check the current state with `git log --oneline main..fix/issue-101` and `git status`.

## Reading the issue (GitHub auth gotcha)

The shell exports an **invalid `GH_TOKEN`** that overrides the valid keyring login (`henrybro64`). Run gh like this:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN gh issue view 101 --json title,body,comments
```

## Changes made

- `k8s/20-postgres.yaml` (Deployment `postgres`):
  - `strategy: Recreate`, so a rollout never runs two Postgres pods on the RWO volume.
  - Pod `securityContext.runAsUser/runAsGroup: ${POSTGRES_UID}`, so `docker-entrypoint.sh` sees a non-root uid and skips `gosu`.
  - Init container `postgres-data-owner` (`busybox:1.36`, uid/gid 0) runs `find <data> \( ! -user UID -o ! -group UID \) -exec chown -h UID:UID {} +`, then `chmod 700` on the volume root. It chowns only wrong-owner entries; see "Codex review fixes".
  - Added a `startupProbe` (period 10s, timeout 5s, failureThreshold 30 = 5 min). Readiness gets `timeoutSeconds: 5`. Liveness gets `timeoutSeconds: 5` and `failureThreshold: 6` (2 min). `initialDelaySeconds` is removed from both.
- `scripts/deploy-k3s.sh`: `POSTGRES_UID="${POSTGRES_UID:-70}"`, a usage-text line (70 for alpine, 999 for Debian images), added to `K8S_ENVSUBST_VARS`, **and to the `export` line**. The issue omitted the export; without it envsubst renders an empty value.
- `k8s/README-k3s.md`: troubleshooting entry under "Verify and troubleshoot" (symptom, `ps aux` check, what the manifest now does).

Review fixes (third session, owner said "fix all the problems"):
- `scripts/deploy-k3s.sh`:
  - Added `POSTGRES_ROLLOUT_TIMEOUT` (default `330s`, documented in the usage text). Both `rollout status deployment/postgres` calls use it; backend and frontend keep `ROLLOUT_TIMEOUT`. (Finding 2)
  - `apply_image_pull_secrets` (one loop at the end) is now `apply_image_pull_secret <deployment>`, called right after each manifest is applied: postgres before its rollout wait, backend before `rollout restart`, frontend after its apply. A first-time secret is now part of the rollout being waited on, not an extra restart later. Re-patching an existing secret is a server-side no-op. (Finding 3)
  - Validates `POSTGRES_UID` against `^[1-9][0-9]*$` before any kubectl call, so `0`, negative, non-numeric, leading-zero and whitespace values exit 1. Empty still falls back to 70. (Finding 5)
- `k8s/README-k3s.md`: "Restarting Postgres safely" (use `rollout restart` or `scale`, never `delete pod`, and why) (Finding 1), and "Brief database downtime on deploy" (Recreate means about 10–20s of DB downtime only when the Postgres pod changes; mentions `POSTGRES_ROLLOUT_TIMEOUT`) (Finding 4).

Codex review fixes (PR #102, commit after `dec0c831`):
- Codex P2 on `k8s/20-postgres.yaml`: the non-recursive chown broke a `POSTGRES_UID` change (alpine 70 ↔ Debian 999). The init container now does the targeted recursive repair described above.
- Codex P2 on `k8s/README-k3s.md`: `scale --replicas=0` then `1` back to back can overlap pods, because Recreate covers only rollouts. The README now recommends `rollout restart`, and documents `kubectl wait --for=delete pod -l app.kubernetes.io/name=postgres` before scaling back up.

## Technical decisions

- Followed the issue's suggested fix. The one deviation: the init container also sets `runAsGroup: 0`, so it doesn't inherit gid 70 from the pod.
- Ownership repair copies the official entrypoint's root-mode behaviour (`find "$PGDATA" \! -user postgres -exec chown ...`). It touches only wrong-owner entries, so a normal start changes nothing (verified: 0 entries) and a `POSTGRES_UID` change repairs every file. `chown -h` changes symlinks themselves and never follows them as root. The data dir has no symlinked tablespaces or WAL.
- `PGDATA` and the PVC are unchanged, as the issue requires. No StatefulSet (out of scope).

## Testing (all run 2026-11-02, real results)

Local throwaway cluster: `k3d cluster create pst-issue101` (k3s v1.33.6, local-path), with its own kubeconfig. It's deleted now. Note: the machine's default kubectl context belongs to an unrelated project (not this repo). All tests here used the throwaway cluster's own kubeconfig and never applied anything to that context.

| Check | Result |
|---|---|
| `bash -n scripts/deploy-k3s.sh`; `--help` lists `POSTGRES_UID` | pass |
| Step 1: render + `kubectl apply --dry-run=server` | pass |
| Step 2 (simulated): deploy the **old** `main` manifest, insert a canary row, then apply the new one | pass. RollingUpdate→Recreate changed in place via apply. The process runs as `uid=70`, the data dir is `postgres:postgres 700`, the canary row is intact, and the logs show a clean shutdown before the new start |
| Step 3: brand-new volume in a fresh namespace | pass. initdb ran as uid 70, the init container exited 0, and Postgres became ready |
| Negative control: new volume **without** the init container | fails in `Error`/restart loop at "fixing permissions on existing directory", which confirms the init container is needed |
| Step 4: `rollout restart` with `get pods -w` | pass. Old pod DELETED before the new pod was ADDED. Canary intact |
| Re-apply (idempotent redeploy) | `deployment.apps/postgres unchanged` |
| `deploy-k3s.sh` run against a stub kubectl that captures the rendered YAML | renders `runAsUser: 70` by default and `999` with `POSTGRES_UID=999` |

Review-fix verification (third session, real results; throwaway k3d `pst-issue101` recreated, then deleted):

| Check | Result |
|---|---|
| `bash -n`; usage text | pass |
| Stub kubectl, `POSTGRES_UID` = `0`, `abc`, `-5`, `07`, `" 70"` | each rejected with a clear message and **0 kubectl calls** |
| Stub kubectl, `POSTGRES_UID` = `70`, `999`, empty | accepted (empty means 70) |
| Stub kubectl call order with `IMAGE_PULL_SECRET=regcred` | patch postgres, then postgres wait `--timeout=330s`; patch backend, then `rollout restart backend`; patch frontend; no trailing patches. Without the secret: 0 patches |
| Cluster: first-time secret on running Postgres (apply + patch + wait) | one Recreate rollout (rev 1→2), old pod DELETED before new ADDED |
| Cluster: redeploy with same manifest + same secret | `unchanged` / `patched (no change)`, revision and pod name unchanged, so **no restart** |
| Cluster: old `main` manifest, then new manifest + first-time secret back to back | collapsed into a single replacement pod, **max concurrent Postgres pods = 1**, canary row intact |

Codex-fix verification (throwaway k3d, real results):

| Check | Result |
|---|---|
| Fresh volume with the new init command (busybox `find -exec {} +`) | init exit 0, Postgres ready |
| Normal restart | 0 wrong-owner entries, so nothing chowned |
| **Negative control**: committed `dec0c831` manifest, switch to `postgres:16` + `POSTGRES_UID=999` | `CrashLoopBackOff`, `could not open configuration file ... postgresql.conf: Permission denied`. Reproduces Codex's finding |
| Fixed manifest, same switch | ready as uid 999, 0 wrong-owner entries, canary intact |
| Switch back to alpine/70 | ready, canary intact |
| Old `main` manifest, then fixed manifest | ready as uid 70, canary intact |
| README `scale 0` → `wait --for=delete` → `scale 1` | max concurrent Postgres pods = 1 |

Not run: `pytest` and the frontend build, because no Python or TypeScript changed.
Side note: on macOS the script exits 1 at the final `hostname -I` (BSD hostname lacks `-I`). The same happens on `main`, and it's fine on the Linux node.

## Architect review (2026-11-02, second session)

The owner asked for an independent review of the implementation. **Verdict: approve. It meets the issue's spec, and I'd ship it.** The working tree was unchanged from the implementation session (same diff). Checks re-run in this session: `bash -n`, `--help`, and stub-kubectl rendering (default 70 and override 999, zero unresolved `${...}`). The cluster tests in the table above come from the implementation session; they were not re-run because the code is identical.

Confirmed correct:
- Every "Suggested fix" item and every "Expected behaviour" item in #101 is covered. Remaining acceptance work is the on-node output (owner only).
- The `export POSTGRES_UID` addition beyond the issue text is required. Without it the manifest gets an empty uid.
- No other script execs into the Postgres pod, so the uid 70 change (formerly root) breaks nothing. `scripts/triage-k3s-pods.sh` uses `--all-containers`, so init-container logs are included.
- `busybox:1.36` is already used by `k8s/30-backend.yaml`, so the node needs no new image pull.
- Apply works on all three starting states: a fresh namespace, an old RollingUpdate deployment (tested), and the live node's hand-patched state. Client-side 3-way apply restores the liveness probe that the incident patch removed, now behind the startup probe.

Findings (none blocking). **All five were fixed in the third session at the owner's request; see "Changes made → Review fixes".**
1. **Residual two-pod risk on manual pod delete (medium, docs-only fix).** `Recreate` only governs rollouts. `kubectl delete pod <postgres-pod>` makes the ReplicaSet start a replacement while the old pod is still terminating. Postgres's own lockfile check won't catch this across containers: both postmasters are PID 1, and Postgres treats a lockfile holding its own PID as stale. Suggest a README line: restart Postgres with `rollout restart deploy/postgres` or `scale --replicas=0/1`, never `delete pod`.
2. **`ROLLOUT_TIMEOUT` 180s < 300s startup window (low).** A deploy shuts Postgres down cleanly, so its restart is fast. The timeout only bites if a deploy runs while Postgres is still in crash recovery. Option: a separate `POSTGRES_ROLLOUT_TIMEOUT` defaulting to `330s`.
3. **Extra Postgres restart when `IMAGE_PULL_SECRET` is set (low, existing behaviour).** `apply_image_pull_secrets` patches the postgres Deployment after the "wait for Postgres" step, which causes a second rollout. Before this change that second rollout overlapped two pods; now it's a short Recreate outage. Ideally, put `imagePullSecrets` in the rendered manifest instead.
4. **Each deploy that changes the Postgres pod template now has a few seconds of DB downtime** (inherent to Recreate, and acceptable for a single node). Optional README note.
5. **No validation of `POSTGRES_UID` (nit).** A non-numeric value fails loudly at apply, which is fine. `0` would silently bring back the gosu path. Optional: reject anything that isn't a positive integer in `deploy-k3s.sh`.

## Outstanding issues

1. **Acceptance criteria need on-node output in the PR** (steps 1–4 on the real k3s node). Only the owner can run those. **Back up first:** `KUBECTL='sudo k3s kubectl' ./scripts/backup-spending-tracker.sh --k3s`. It may fail because the backend image lacks `pg_dump`. If it does, scale Postgres to 0, **wait for the pod to be deleted** (`kubectl wait --for=delete pod -l app.kubernetes.io/name=postgres`), and copy the PV directory (`kubectl get pv`).
2. The live node currently has the hand-applied patches from the incident (liveness removed). The next `deploy-k3s.sh` puts liveness back, now with a startup probe in front of it, which is intended.

## Next steps

1. Check CI: `env -u GH_TOKEN -u GITHUB_TOKEN gh pr checks 102`. Check for new review comments with `gh api repos/henrybro64/personal_spending_tracker/pulls/102/comments`. CI (`.github/workflows/ci.yml`) runs backend pytest and the frontend build plus Playwright. It does not exercise k8s manifests, so a failure there is likely unrelated to this change; investigate before changing anything.
2. Any further commits or pushes need the owner's explicit approval (each push costs GitHub Actions minutes).
3. The owner runs the issue's steps 1–4 on the node and pastes the output into the PR.

## Related GitHub issues/PRs

- #101 (this task), parent #95. PR #102 (this work).
- PR #93 (`d5fdbc5a`): latest merge on `main`. PR #92: earlier fixes.
