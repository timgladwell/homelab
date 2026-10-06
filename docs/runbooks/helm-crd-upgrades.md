# Helm Chart Upgrades with CRD Changes

## Background

Helm (and by extension Flux) never updates CRDs automatically on `helm upgrade`. CRDs are only installed on `helm install`. This is an intentional Helm safety boundary: CRDs are schema definitions for live cluster resources, and updating them incorrectly could corrupt data.

When a Helm chart ships updated CRDs (common on major and some minor version bumps), they must be applied manually **before or alongside** merging the Renovate PR that bumps the chart version.

Flux's `crds: CreateReplace` option on a `HelmRelease` can automate this, but CRD updates are irreversible — Flux cannot roll them back if something goes wrong. The manual approach is retained here intentionally so that upgrades with breaking CRD changes require explicit review.

## General process

1. **Read the chart's upgrade guide** before merging any Renovate PR for a major or minor version bump.
2. **Identify CRD changes** — look for a migration guide, CHANGELOG entry, or `crds/` directory diff between the old and new chart version.
3. **Apply updated CRDs** using server-side apply (safer than client-side for CRDs):
   ```bash
   kubectl apply --server-side --force-conflicts -f <crd-url-or-file>
   ```
   - `--server-side`: the API server computes the merge, which is more correct for complex schemas.
   - `--force-conflicts`: overwrites fields owned by another manager (e.g. a previous `kubectl apply` or Flux), preventing the update from being blocked by field ownership conflicts.
4. **Wait for the apply to settle** (see below), then **merge the Renovate PR**. Flux reconciles the `HelmRelease` and runs the equivalent of `helm upgrade` automatically. You do not need to run `helm` commands directly.

Chart-specific steps: [Traefik](traefik-upgrades.md), [kube-prometheus-stack](kube-prometheus-stack-upgrades.md).

## What a CRD apply does to the cluster

A CRD apply is not a quiet schema change. It causes real load, and the dashboards show it.

- **kube-state-metrics rebuilds its custom-resource stores.** KSM runs with `customResourceState` for the Flux `gotk_*` metrics (`base/metrics-collection/kube-state-metrics.yaml`), and it watches CRD discovery. **Every** CRD change makes it reload all four Flux stores, not only changes to the Flux CRDs. A 10-CRD apply is about ten rebuilds in a few seconds. On 2026-10-01 this raised KSM's RSS from 40 to 86 MiB, which put it at about 99% of its 128Mi limit with page cache included. That shows red on Estate overview's *Containers above 80% of their memory limit* panel. It also kept KSM's CPU throttled at its 100m limit.
- **The API server gets busy, and KSM can get killed for it.** KSM's `/livez` liveness endpoint checks the API server. While the API server was busy with the update, KSM logged `Failed to contact API server for /livez`, failed its liveness probe, and restarted three times (last terminated reason `Error`, not `OOMKilled`). The node's major page faults and CPU rose at the same time. Nearly all the faults were outside pod containers, and `k3s-server` had the most of any process. Both went back to near baseline within a few minutes of the rollout finishing.

This is why step 4 says wait. If you apply and merge together, the CRD's effects and the chart's rollout overlap on the same panels, and you cannot tell which caused what.

## Breaking changes to check beyond CRDs

Not all breaking changes are CRD-related. Also check the chart's changelog for:
- **Values restructuring** — fields moved, renamed, or re-nested under new keys (requires updating `values:` in the `HelmRelease`)
- **Provider or feature renames** — keys that silently have no effect if not updated
- **An option an accepted trivy finding says is missing** — read the release's entries in `scripts/trivy-accepted-findings.txt`. Several are accepted only because the chart had no value to fix them (Grafana's sidecar RBAC, for one). Validation step 16 already fails when a bump makes an accepted finding stop firing; it cannot tell that a finding which still fires has become fixable.
