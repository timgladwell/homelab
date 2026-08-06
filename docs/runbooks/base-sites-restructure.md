# Migrating Akron to the base/ + sites/ Layout

One-time procedure for the PR that splits the repo into `base/` (site-agnostic
components) and `sites/<site>/` (per-site composition).

Most of this is a no-op on the cluster: the built manifests are byte-identical
to the previous layout apart from eight Flux `spec.path` strings, which Flux
updates in place without touching a resource.

Two changes are not no-ops:

1. **The monitoring Kustomization rename destroys data** (below).
2. **The `pihole-sync` ConfigMap changed**, so its Job re-runs and gravity
   rebuilds — about 10 minutes per site, no DNS impact.

## What the rename does

The Flux `Kustomization` object `infrastructure-akron-only` is renamed to
`monitoring`. Flux has no concept of renaming an object — `flux-system` sees
the old name disappear from git and prunes it, and because it reconciles with
`prune: true`, that cascade-deletes **everything it owned**:

- Prometheus TSDB PVC — all historical metrics
- Loki PVC — all historical logs
- Grafana PVC — anything not declared in git

Then the new `monitoring` Kustomization reconciles and rebuilds the stack
empty. Dashboards, datasources, alert rules and the Grafana admin credential
all live in `sites/akron/monitoring/`, so they come back automatically. Only
accumulated time-series and log history is lost.

This was accepted deliberately: a permanently misleading Kustomization name is
a worse long-term cost than a one-time loss of homelab metrics history.

## Steps

1. **Merge to `main`.** Akron reconciles within ~10 minutes.

2. **Watch the prune and rebuild:**
   ```bash
   flux get kustomizations -A --watch
   ```
   Expect `infrastructure-akron-only` to vanish, `monitoring` to appear, and
   the monitoring namespace's pods to be recreated. `apps` depends on
   `monitoring` (PodMonitor CRD), so it will go `NotReady` until the rename
   settles — that is expected, not a failure.

3. **Confirm the stack is back:**
   ```bash
   kubectl get pods,pvc -n monitoring
   flux get kustomizations -A
   ```
   All Kustomizations should report `Applied revision`. Grafana should be
   reachable at `grafana.${HOSTNAME}` with its dashboards intact and its
   graphs empty.

4. **PiHole and Unbound must not restart.** The `infrastructure` Kustomization
   keeps its name, so its PVC is untouched and no DNS outage is expected:
   ```bash
   kubectl get pods -n dns
   ```
   If PiHole restarted anyway, it is the RollingUpdate-safe path, not a
   Recreate outage — but check `kubectl get pvc -n dns` immediately to confirm
   the PiHole PVC still exists.

5. **Expect one PiHole sync Job re-run per site.** The `pihole-sync` ConfigMap
   changed (client definitions moved into a per-site file), so Flux
   force-recreates the Job and gravity rebuilds — roughly 10 minutes. DNS
   resolution is unaffected while it runs.

   Eastbank's `sites/eastbank/app-config/pihole-clients.yaml` is intentionally
   empty. If Eastbank's PiHole currently has clients defined, this sync
   **removes them** — it was previously syncing Akron's client list, so it had
   Akron's Roku defined and nothing else. Fill the file in before promoting if
   Eastbank needs its own client groups.

6. **Promote to Eastbank** only after Akron is confirmed healthy — open a PR
   from `main` into `stable` as usual. Eastbank has no monitoring layer, so
   nothing is pruned there — it sees the `spec.path` changes and the
   `pihole-sync` Job re-run, and nothing else is recreated.

## If you want to keep the metrics history instead

Detach the old Kustomization from Flux's inventory *before* merging, so the
prune has nothing to cascade to, then let the new name adopt the resources:

```bash
flux delete kustomization infrastructure-akron-only --no-prune
```

Run this on Akron immediately before the merge. The workloads and PVCs stay
running, orphaned, until the `monitoring` Kustomization reconciles and takes
ownership of them. Skipped here because the data was not worth the extra
manual step.
