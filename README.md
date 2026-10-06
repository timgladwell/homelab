# Homelab

GitOps configuration for a small estate of independent, single-node K3s
clusters on Raspberry Pi 4B hardware, one per physical site, managed with Flux CD,
Kustomize and Helm. It runs each site's DNS (Pi-hole + Unbound), ingress and TLS,
load balancing, and an observability stack that collects at every site and stores
at one.

**Scope:** everything this repository declares is reconciled by Flux. What it
cannot declare — node configuration, bootstrap secrets, UniFi and Cloudflare
settings — is inventoried in
[State That Is Not In Git](docs/current-state/host-state.md).

## Getting started

- [Setting Up a Development Environment](docs/runbooks/development-environment.md)
  — tools, git hooks, secrets, and validating a change.
- [CONTRIBUTING.md](CONTRIBUTING.md) — where each kind of information lives.
- [Architecture decisions](docs/adr/) and [runbooks](docs/runbooks.md).

## Licence

Not yet chosen; until it is, default copyright applies. Tracked in #423.
