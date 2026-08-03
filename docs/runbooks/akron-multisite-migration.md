# Migrating Akron to the Multi-Site Layout

## Background

The multi-site restructure PR renames `clusters/homelab/` to `clusters/akron/` and changes `infrastructure/homelab/` to `infrastructure/core/` + `infrastructure/akron-only/`. Akron's Flux instance is already live, reconciling the *old* path (`./clusters/homelab`) from its in-cluster root `Kustomization` object (named `flux-system`, namespace `flux-system`). That object's `spec.path` is stored in the cluster, not re-read from git on every reconcile — so once this PR merges and `clusters/homelab/` no longer exists in the repo, Akron's root Kustomization will fail to build (path not found) until `spec.path` is updated to `./clusters/akron`.

**Do not run `flux bootstrap` to fix this** — bootstrap pushes directly to `main`, which branch protection blocks (same reason it's banned for [Flux Upgrades](flux-upgrades.md) and [PAT rotation](github-pat-rotation.md) above). This is a one-time in-cluster object update, not a GitOps-managed change, so `kubectl apply` against the already-merged file is the correct tool.

## Process

1. **Merge the multi-site restructure PR to `main`** first — `clusters/akron/flux-system/gotk-sync.yaml` (with `path: ./clusters/akron`) must exist in git before the next step.

2. **On the Akron server**, pull the merged `main` and apply the updated root Kustomization/GitRepository directly:
   ```bash
   git pull origin main
   kubectl apply -f clusters/akron/flux-system/gotk-sync.yaml
   ```
   This updates the in-cluster `flux-system` Kustomization's `spec.path` to `./clusters/akron` immediately — do not wait for a reconcile loop to pick it up, it won't (it's still looking at the old, now-missing path until this apply happens).

3. **Confirm reconciliation recovers:**
   ```bash
   flux get kustomizations -A
   flux get sources git
   ```
   Expect `infrastructure`, `infrastructure-akron-only`, `infrastructure-config`, `apps`, `app-config` to all appear (the last four are new/renamed Kustomization names — see `clusters/akron/kustomization.yaml`) and go healthy within a couple of reconcile intervals.

4. **If `infrastructure-akron-only` or `app-config` fail on `dependsOn`,** check `flux get kustomizations -A` for the dependency chain — `apps` now depends on `infrastructure-akron-only` (PodMonitor CRD from kube-prometheus-stack) in addition to `infrastructure` and `infrastructure-config`; this is new as of the restructure.

5. **Verify no DNS disruption** — `infrastructure/core/dns/` content is unchanged from `infrastructure/homelab/dns/` (only the parent directory moved), so PiHole/Unbound should not restart or lose state during this migration. If they do restart, it's the RollingUpdate-safe path (see `feedback_pihole_recreate_strategy` guidance) — not a Recreate outage.
