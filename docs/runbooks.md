# Runbooks

Procedures for a specific outcome, one file each under `runbooks/`. Kinds and
conventions are in [CONTRIBUTING.md](../CONTRIBUTING.md#runbooks).

See also [State That Is Not In Git](current-state/host-state.md) — everything a
reconcile will not restore.

| Runbook | Kind |
|---|---|
| [Promoting to `stable`](runbooks/promote-to-stable.md) | Operational |
| [Reaching PiHole When Traefik Is Not Routing](runbooks/pihole-access.md) | Operational |
| [Taking a Heap Dump of the UniFi Network App](runbooks/unifi-network-heap-dump.md) | Operational |
| [Adding or Removing a Component, Layer or Variable](runbooks/adding-components.md) | Maintenance |
| [Renaming or Moving Across Flux Kustomizations](runbooks/flux-kustomization-changes.md) | Maintenance |
| [Sizing a Container's Memory Limit](runbooks/memory-sizing.md) | Maintenance |
| [Helm Chart Upgrades with CRD Changes](runbooks/helm-crd-upgrades.md) | Maintenance |
| [Traefik upgrades](runbooks/traefik-upgrades.md) | Maintenance |
| [kube-prometheus-stack upgrades](runbooks/kube-prometheus-stack-upgrades.md) | Maintenance |
| [Flux Upgrades](runbooks/flux-upgrades.md) | Maintenance |
| [Rotating the GitHub PAT for Flux](runbooks/github-pat-rotation.md) | Maintenance |
| [Periodic Cluster Security Audit with trivy-operator](runbooks/trivy-operator-audit.md) | Maintenance |
| [Renaming the K3s Node](runbooks/node-rename.md) | Maintenance |
| [TRIM on a USB-Attached SSD](runbooks/usb-trim.md) | Maintenance |
| [Setting Up a Development Environment](runbooks/development-environment.md) | Provisioning |
| [Bootstrapping a New Remote Site](runbooks/bootstrap-new-remote-site.md) | Provisioning |
| [Standing Up a New Headless Box (Flash + Cloud-Init)](runbooks/new-box-standup.md) | Provisioning |
| [Read-Only Grafana Access for Claude Code](runbooks/grafana-query-access.md) | Provisioning |
| [Let's Encrypt Certificates on a UniFi Console](runbooks/unifi-tls.md) | Provisioning |

## Retired

Removed once no longer needed. Each link is the last version of the file.

| Runbook | Summary |
|---|---|
| [Migrating Akron to the base/ + sites/ Layout](https://github.com/timgladwell/homelab/blob/70c9d44/docs/runbooks/base-sites-restructure.md) | One-time cutover to the `base/` + `sites/` layout, including the monitoring Kustomization rename that discarded metrics history. |
| [One-Time Reset of `stable`](https://github.com/timgladwell/homelab/blob/70c9d44/docs/runbooks/stable-promotion-reset.md) | One-time switch from PR promotion to workflow fast-forward; the recovery steps live on in [Promoting to `stable`](runbooks/promote-to-stable.md). |
