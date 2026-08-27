# Runbooks

Operational procedures for tasks that fall outside the normal GitOps flow.

- [Helm Chart Upgrades with CRD Changes](runbooks/helm-crd-upgrades.md)
- [Traefik](runbooks/traefik-upgrades.md)
- [Flux Upgrades](runbooks/flux-upgrades.md)
- [Rotating the GitHub PAT for Flux](runbooks/github-pat-rotation.md)
- [Migrating Akron to the base/ + sites/ Layout](runbooks/base-sites-restructure.md)
- [Bootstrapping a New Remote Site](runbooks/bootstrap-new-remote-site.md)
- [One-Time Reset of `stable`, and Switching to Fast-Forward Promotion](runbooks/stable-promotion-reset.md)
- [Standing Up a New Headless Box (Flash + Cloud-Init)](runbooks/new-box-standup.md)
- [Periodic Cluster Security Audit with trivy-operator](runbooks/trivy-operator-audit.md)
- [Renaming the K3s Node](runbooks/node-rename.md)
- [Reaching PiHole When Traefik Is Not Routing](runbooks/pihole-access.md)
- [Read-Only Grafana Access for Claude Code](runbooks/grafana-query-access.md)
- [Let's Encrypt Certificates on a UniFi Console](runbooks/unifi-tls.md)

See also [State That Is Not In Git](host-state.md) — the inventory of everything a reconcile will not restore.

