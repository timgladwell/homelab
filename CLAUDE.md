# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Design Principles

- **Enterprise best practices always.** Treat this as a high-scale production K8s environment in terms of design, structure, and operational patterns, even though it runs on a single node. This means proper namespace isolation, resource limits, health checks, RBAC, and GitOps workflows.
- **GitOps is the single source of truth.** All cluster state is declared in this repository. Manual `kubectl apply` or imperative changes are not acceptable. Everything flows through Flux CD reconciliation.
- **Security by default.** No secrets in the repo (use SOPS/Age encryption). Pre-commit hooks enforce this. All manifests should follow least-privilege principles.
- **Keep it simple.** Avoid over-engineering. The RPi has limited resources (300m CPU / 150Mi memory is a typical ceiling for a single workload). Don't add abstractions, features, or tooling that aren't needed yet.

## Development Principles

- All changes to this repo go through PRs - do not work on the `main` branch directly
- **Do NOT push to merged PRs.** Any deployment feedback (pod logs, Helm errors, `flux get` output) means the relevant PR is already merged. Always start a new branch for the fix.
- **Always branch from `origin/main`.** Run `git fetch origin` then `git checkout -b <branch> origin/main` before starting any new change. Never branch from a previous feature branch — it will carry commits that are already merged and cause conflicts.

### Validation

After any change to manifests, run the full validation pipeline from the repo root:

```bash
./scripts/validate-k3s.sh
```

This runs eight steps in order, **per site** (Akron, Eastbank, Lottage — each site reconciles a different subset of the repo, see Directory layout below):
1. **YAML lint** — `yamllint` against all files (ignores each site's `flux-system/` and `*.sops.yaml`)
2. **Flux build** — `flux build kustomization --dry-run` for each Flux Kustomization, for each site
3. **Kustomize build** — `kustomize build ./clusters/<site>-validation` → `$K3S_BUILD_DIR/k3s-built-<site>.yaml`, for each site
4. **Schema validation** — `kubeconform -summary` against each site's built output
5. **Best practices** — `kube-score score` against each site's built output
6. **Security scan** — `trivy config ./ --severity HIGH,CRITICAL` (whole repo, not per-site)
7. **Variable references** — every `${VAR}` in each site's build output must be defined in that site's `cluster-vars.yaml`
8. **Policy** — `conftest test` against each site's built output using policies in `policy/`

Step 2 gates steps 3–5 and 7–8. Step 3 additionally gates steps 4, 5, 7, and 8. Steps 1 and 6 always run independently.

You can also check a specific kustomization in isolation:

```bash
kustomize build infrastructure/core/
kustomize build infrastructure/akron-only/monitoring/
kustomize build infrastructure/core-overlays/lottage/
kustomize build infrastructure-config/core/
```

## Secrets

All secrets follow the `*secret.sops.yaml` naming convention and must be SOPS-encrypted before committing. `.sops.yaml` has path-scoped rules: secrets under `infrastructure/akron-only/` encrypt to Akron's age key only; everything else matching `*secret.sops.yaml` (shared infra, e.g. PiHole) encrypts to all three sites' age keys, so each site's Flux can only decrypt what it actually needs.

To create or edit a secret:

```bash
# Edit (decrypt → edit → re-encrypt in place)
./scripts/secrets-helper.sh edit infrastructure/akron-only/monitoring/grafana-secret.sops.yaml

# Encrypt a plaintext file in place
./scripts/secrets-helper.sh encrypt <file>

# View without saving
./scripts/secrets-helper.sh view <file>
```

Requires `SOPS_AGE_KEY_FILE` to point to an age private key that can decrypt the secret (defaults to `~/.config/sops/age/keys.txt`). Any site's key can decrypt shared secrets; only Akron's key can decrypt `infrastructure/akron-only/` secrets.

## Architecture

- Three independent single-node K3s sites, each managed with **Flux CD + Kustomize + Helm**, sharing this one repo (Flux's standard multi-cluster monorepo pattern — no cluster federation, no shared control plane):
  - **Akron** (local, 8GB RAM) — gets everything: shared DNS core + Akron-only monitoring/apps. Deploys first.
  - **Eastbank** (remote, 8GB RAM) — shared DNS core only, Traefik+MetalLB (matches Akron's ingress pattern).
  - **Lottage** (remote, 2GB RAM, 4Mb/s DSL) — shared DNS core only, but *without* Traefik/MetalLB — PiHole runs on `hostNetwork` instead, to avoid rollout surge-memory OOM risk. See `infrastructure/core-overlays/lottage/`.
- The local development machine is not connected to any site. All commands are executed on the server via SSH session.
- **Rollout gating (Akron first):** Akron's Flux `GitRepository` watches `main`. Eastbank's and Lottage's watch a `stable` branch. After merging to `main` and confirming Akron is healthy, fast-forward `stable` (`git push origin main:stable`) to promote to the remote sites. There is no automatic cross-cluster gate — this is a manual, explicit step.

### Directory layout

```
clusters/<site>/                 # Flux entry point for this site — managed by the flux-system Kustomization
  flux-system/                   # Flux's own manifests (managed by flux bootstrap, do not edit)
  flux-system-local/             # Patches applied over flux-system/ (kube-score ignores, etc.)
  cluster-vars.yaml              # Per-site ConfigMap (DNS_DOMAIN, HOSTNAME, METALLB_*, NODE_IP) injected via postBuild.substituteFrom
  infrastructure.yaml            # Flux Kustomization object — path varies per site (see below)
  infrastructure-akron-only.yaml # Akron only — path: infrastructure/akron-only
  infrastructure-config.yaml     # Akron + Eastbank only — path: infrastructure-config/core
  apps.yaml                      # Akron only — path: apps/homelab
  app-config.yaml                # All sites — path: app-config/core (pihole-sync)

clusters/<site>-validation/      # Validation-only kustomize entry point per site (not reconciled by Flux)
  kustomization.yaml             # Includes exactly the layers that site reconciles

infrastructure/core/             # Shared infrastructure — reconciled by Akron and Eastbank
  kustomization.yaml             # Add new shared infrastructure subdirs here
  dns/                           # PiHole + Unbound
  traefik/                       # Ingress controller
  metallb/                       # L2 load balancer
  system-upgrade-controller/

infrastructure/akron-only/       # Akron-only infrastructure — reconciled by Akron only
  kustomization.yaml
  monitoring/                    # Prometheus + Grafana + Loki + OTel + per-UniFi-site Unpoller instances

infrastructure/core-overlays/lottage/  # Lottage's variant of infrastructure/core/dns — no Traefik/MetalLB,
                                        # hostNetwork + Recreate patch on PiHole (2GB RAM constraint)

infrastructure-config/core/      # Post-infrastructure config (depends on CRDs from infrastructure/core/)
  kustomization.yaml             # Add new config subdirs here
  metallb-config/                # MetalLB IP pools (IPAddressPool + L2Advertisement)
  coredns-config/                # k3s CoreDNS custom stub

apps/homelab/                    # Akron-only user-facing applications (per-UniFi-site instances of each app)

app-config/core/                 # Post-infrastructure config, shared across all sites
  kustomization.yaml             # Add new app-config subdirs here
  pihole-sync/                   # Syncs DNS blocklists into each site's own local PiHole
```

`apps/homelab/` (network-optimizer, per-UniFi-site instances) and `infrastructure/akron-only/monitoring/unpoller/` (per-UniFi-site instances) keep the `homelab`/`akron` naming from before the multi-site split — they run centrally on Akron polling all three UniFi controllers over the Site Magic VPN, so there's no separate per-K3s-cluster concern to name around. Don't confuse that existing "per-UniFi-site app instance" convention (base + Kustomize overlay + nameSuffix, all applied on Akron) with the "per-K3s-cluster" layer above — they solve different problems and both coexist.

### Why validation has separate `clusters/<site>-validation/` directories

Each site's Flux Kustomization objects (`infrastructure.yaml`, etc.) reference different paths and a different subset of layers — Lottage has no `apps.yaml`/`infrastructure-config.yaml` at all, for instance. A single combined validation entry point couldn't accurately represent what any one site actually reconciles, so each site gets its own `clusters/<site>-validation/kustomization.yaml` listing only its own layers. These are not on any Flux reconciliation path — they exist solely for `kubeconform`/`kube-score`/`trivy`/`conftest` to see each site's complete manifest set.

### Reconciliation flow

**Akron:**
1. Flux watches the Git repo (`main` branch) and reconciles `clusters/akron/` via the `flux-system` Kustomization
2. `infrastructure` reconciles `infrastructure/core/` — SOPS decryption + `cluster-vars` substitution
3. `infrastructure-akron-only` reconciles `infrastructure/akron-only/` (monitoring) — depends on `infrastructure`
4. `infrastructure-config` reconciles `infrastructure-config/core/` — depends on `infrastructure`
5. `apps` reconciles `apps/homelab/` — depends on `infrastructure`, `infrastructure-akron-only` (PodMonitor CRD), `infrastructure-config`
6. `app-config` reconciles `app-config/core/` (pihole-sync) — depends on `apps`

**Eastbank:** Flux watches `stable` branch, reconciles `clusters/eastbank/` → `infrastructure` (`infrastructure/core/`) → `infrastructure-config` (`infrastructure-config/core/`) and `app-config` (`app-config/core/`), both depending on `infrastructure`.

**Lottage:** Flux watches `stable` branch, reconciles `clusters/lottage/` → `infrastructure` (`infrastructure/core-overlays/lottage/`) → `app-config` (`app-config/core/`), depending on `infrastructure`. No `infrastructure-config` (no MetalLB to configure).

### Variable substitution

Each site's `cluster-vars.yaml` defines its own `${DNS_DOMAIN}`, `${HOSTNAME}`, and (Akron/Eastbank only) `${METALLB_ADDRESS_RANGE}`, `${METALLB_TRAEFIK_IP}`, `${METALLB_PIHOLE_IP}`, `${NODE_IP}`. Use these placeholders directly in manifests — Flux substitutes them at reconcile time via `postBuild.substituteFrom`, from that site's own ConfigMap only (there is no cross-site fallback).

Plain `kustomize build` does not perform this substitution, so the validation pipeline will always contain `${VAR}` literals in its output. Validation step 7 catches any `${VAR}` reference that is not defined in that site's `cluster-vars.yaml`.

**When adding a new variable:** add it to every site's `cluster-vars.yaml` that reconciles the manifest using it, before (or in the same PR as) that manifest. If the variable is missing for a site that reconciles it, step 7 will fail for that site.

### Adding components to existing Kustomizations

**Infrastructure shared across sites:**
1. Create `infrastructure/core/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `infrastructure/core/kustomization.yaml`
3. If Lottage should skip this component, add a delete patch in `infrastructure/core-overlays/lottage/` (see `delete-pihole-ingressroute.yaml` for the pattern)
4. No changes needed to `clusters/` or `clusters/*-validation/`

**Infrastructure specific to Akron:**
1. Create `infrastructure/akron-only/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `infrastructure/akron-only/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/*-validation/`

**Post-infrastructure config (resources that require CRDs installed by infrastructure):**
1. Create `infrastructure-config/core/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `infrastructure-config/core/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/*-validation/` (Lottage doesn't reconcile this layer at all)

**Apps (Akron-only):**
1. Create `apps/homelab/<app>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<app>` to `apps/homelab/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/akron-validation/`

**Post-apps config (shared across sites, e.g. pihole-sync):**
1. Create `app-config/core/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `app-config/core/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/*-validation/`

### Adding a new top-level Flux Kustomization

A new top-level Kustomization is needed when resources require a different `dependsOn` ordering, SOPS configuration, or reconciliation interval from the existing ones for a site. This is rare.

1. Create the resource directory (e.g. `<type>/core/` or `<type>/akron-only/`) with a `kustomization.yaml` listing its contents
2. For each site that needs it, add to `clusters/<site>/`:
   - `<name>.yaml` — the Flux `Kustomization` object with appropriate `dependsOn`, `postBuild`, etc.
   - An entry in `clusters/<site>/kustomization.yaml`
3. Add the resource path to each affected `clusters/<site>-validation/kustomization.yaml`
4. Add a `check <site> <name> <path>` line for each site in `scripts/validate/02-flux-build.sh`

Steps 3 and 4 are the only cases where `clusters/*-validation/kustomization.yaml` and `02-flux-build.sh` need to be updated. Resources added within an existing top-level path are automatically included in validation.

### Removing a Kustomization

- **Component within an existing Kustomization:** remove it from the parent `kustomization.yaml`. Flux's `prune: true` will delete the resources from the cluster on the next reconciliation.
- **Top-level Flux Kustomization:** remove its file(s) from the affected `clusters/<site>/`, remove its entry from `clusters/<site>/kustomization.yaml`, remove its resource path from the affected `clusters/<site>-validation/kustomization.yaml`, and remove its `check` line(s) from `scripts/validate/02-flux-build.sh`.

### Ingress pattern

Apps are exposed via Traefik `IngressRoute` CRs using subdomain routing (`<app>.${HOSTNAME}`). Traefik is a MetalLB `LoadBalancer` at `${METALLB_TRAEFIK_IP}`. See `infrastructure/core/dns/pihole-ingressroute.yaml` for the canonical pattern. Not available on Lottage (no Traefik/MetalLB) — see `infrastructure/core-overlays/lottage/`.

### Dependency management

Helm chart versions are managed by **Renovate**, which runs on weekends and opens PRs for `HelmRelease` version bumps across `clusters/`, `infrastructure/`, and `apps/`.

### Hardware constraints

All images must support **ARM64** (Raspberry Pi 4B). Verify ARM64 availability before pinning any image. All workloads must declare requests and limits, and storage limits (if applicable).
