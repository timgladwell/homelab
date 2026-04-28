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

This runs six steps in order:
1. **YAML lint** — `yamllint` against all files (ignores `flux-system/` and `*.sops.yaml`)
2. **Kustomize build** — `kustomize build ./clusters/homelab-validation` → `$TMPDIR/k3s-built.yaml`
3. **Schema validation** — `kubeconform -summary` against the built output
4. **Best practices** — `kube-score score` against the built output
5. **Security scan** — `trivy config ./ --severity HIGH,CRITICAL`
6. **Variable references** — every `${VAR}` in the build output must be defined in `cluster-vars.yaml`

Steps 3, 4, and 6 are skipped if step 2 fails. All other steps run independently.

You can also check a specific kustomization in isolation:

```bash
kustomize build infrastructure/homelab/
kustomize build infrastructure/homelab/monitoring/
kustomize build infrastructure-config/homelab/
```

## Secrets

All secrets follow the `*secret.sops.yaml` naming convention and must be SOPS-encrypted before committing. The `.sops.yaml` rule encrypts `data` and `stringData` fields in any file matching `.*secret\.sops\.yaml$` using an age key.

To create or edit a secret:

```bash
# Edit (decrypt → edit → re-encrypt in place)
./scripts/secrets-helper.sh edit infrastructure/homelab/monitoring/grafana-secret.sops.yaml

# Encrypt a plaintext file in place
./scripts/secrets-helper.sh encrypt <file>

# View without saving
./scripts/secrets-helper.sh view <file>
```

Requires `SOPS_AGE_KEY_FILE` to point to the age private key (defaults to `~/.config/sops/age/keys.txt`).

## Architecture

- This is a single-node K3s homelab managed with **Flux CD + Kustomize + Helm**.
- The local development machine is not connected to the homelab server. All commands are executed on the server via SSH session.

### Directory layout

```
clusters/homelab/               # Flux entry point — managed by the flux-system Kustomization
  flux-system/                  # Flux's own manifests (managed by flux bootstrap, do not edit)
  flux-system-local/            # Patches applied over flux-system/ (kube-score ignores, etc.)
  infrastructure-config/        # Flux Kustomization object for infrastructure-config/homelab/
  infrastructure.yaml           # Flux Kustomization object for infrastructure/homelab/
  apps.yaml                     # Flux Kustomization object for apps/homelab/
  cluster-vars.yaml             # ConfigMap injected into all manifests via postBuild.substituteFrom

clusters/homelab-validation/    # Validation-only kustomize entry point (not reconciled by Flux)
  kustomization.yaml            # Includes all four resource layers for kubeconform/kube-score/trivy

infrastructure/homelab/         # Cluster infrastructure (namespaces, Helm releases, CRDs)
  kustomization.yaml            # Add new infrastructure subdirs here
  dns/                          # PiHole + Unbound
  traefik/                      # Ingress controller
  metallb/                      # L2 load balancer
  monitoring/                   # Prometheus + Grafana + Loki + OTel (in progress)
  system-upgrade-controller/

infrastructure-config/homelab/  # Post-infrastructure config (depends on CRDs from infrastructure/)
  kustomization.yaml            # Add new config subdirs here
  metallb-config/               # MetalLB IP pools (IPAddressPool + L2Advertisement)

apps/homelab/                   # User-facing applications (deployed after infrastructure)
```

### Why there are two `clusters/homelab*` directories

`clusters/homelab/kustomization.yaml` is processed by the `flux-system` Flux Kustomization (path: `./clusters/homelab`). It must only contain **Flux bootstrap objects**: the Flux controller overlay and the four Flux Kustomization definitions. It must not reference raw workload resources (HelmReleases, namespaces, IPAddressPools, etc.) directly.

If workload resources were included here, the `flux-system` Kustomization would apply them without variable substitution (it has no `postBuild.substituteFrom`) and without the `dependsOn` ordering that ensures MetalLB CRDs exist before IPAddressPool resources are applied. It would also create duplicate resource ownership between `flux-system` and the dedicated `infrastructure`/`infrastructure-config` Kustomizations — both with `prune: true` — causing reconciliation conflicts.

`clusters/homelab-validation/kustomization.yaml` exists solely as a kustomize entry point for the local validation pipeline. It includes all four resource layers so `kubeconform`, `kube-score`, and `trivy` see the complete cluster manifest. It is not on any Flux reconciliation path.

### Reconciliation flow

1. Flux watches the Git repo and reconciles `clusters/homelab/` via the `flux-system` Kustomization
2. `flux-system` applies: Flux controllers, `cluster-vars` ConfigMap, and the four Kustomization objects below
3. `infrastructure` reconciles `infrastructure/homelab/` — SOPS decryption + `cluster-vars` substitution
4. `infrastructure-config` reconciles `infrastructure-config/homelab/` — only after `infrastructure` is healthy (`dependsOn`)
5. `apps` reconciles `apps/homelab/` — only after both `infrastructure` and `infrastructure-config` are healthy (`dependsOn`)

### Variable substitution

`cluster-vars.yaml` defines `${DNS_DOMAIN}`, `${HOSTNAME}`, `${METALLB_ADDRESS_RANGE}`, `${METALLB_TRAEFIK_IP}`, `${METALLB_PIHOLE_IP}`, `${NODE_IP}`. Use these placeholders directly in manifests — Flux substitutes them at reconcile time via `postBuild.substituteFrom`.

Plain `kustomize build` does not perform this substitution, so the validation pipeline will always contain `${VAR}` literals in its output. Validation step 6 catches any `${VAR}` reference that is not defined in `cluster-vars.yaml`.

**When adding a new variable:** add it to `cluster-vars.yaml` before (or in the same PR as) the manifest that uses it. If the variable is missing, step 6 will fail.

### Adding components to existing Kustomizations

**Infrastructure (Helm controllers, CRDs, namespaces):**
1. Create `infrastructure/homelab/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `infrastructure/homelab/kustomization.yaml`
3. Wire Helm charts via `HelmRepository` + `HelmRelease` resources in that directory
4. No changes needed to `clusters/` or `clusters/homelab-validation/`

**Post-infrastructure config (resources that require CRDs installed by infrastructure):**
1. Create `infrastructure-config/homelab/<component>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<component>` to `infrastructure-config/homelab/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/homelab-validation/`

**Apps:**
1. Create `apps/homelab/<app>/` with a `kustomization.yaml` listing its resources
2. Add `- ./<app>` to `apps/homelab/kustomization.yaml`
3. No changes needed to `clusters/` or `clusters/homelab-validation/`

### Adding a new top-level Flux Kustomization

A new top-level Kustomization is needed when resources require a different `dependsOn` ordering, SOPS configuration, or reconciliation interval from the existing four. This is rare.

1. Create the resource directory (e.g. `<type>/homelab/`) with a `kustomization.yaml` listing its contents
2. Create `clusters/homelab/<name>/` containing:
   - `kustomization.yaml` — kustomize config listing the Flux Kustomization object file
   - `<name>.yaml` — the Flux `Kustomization` object with appropriate `dependsOn`, `postBuild`, etc.
3. Add `- ./<name>` to `clusters/homelab/kustomization.yaml`
4. Add `- ../../<type>/homelab` to `clusters/homelab-validation/kustomization.yaml`

Step 4 is the only case where `clusters/homelab-validation/kustomization.yaml` needs to be updated. Resources added within an existing top-level path are automatically included in validation.

### Removing a Kustomization

- **Component within an existing Kustomization:** remove it from the parent `kustomization.yaml`. Flux's `prune: true` will delete the resources from the cluster on the next reconciliation.
- **Top-level Flux Kustomization:** remove its folder from `clusters/homelab/`, remove its entry from `clusters/homelab/kustomization.yaml`, and remove its resource path from `clusters/homelab-validation/kustomization.yaml`.

### Ingress pattern

Apps are exposed via Traefik `IngressRoute` CRs using subdomain routing (`<app>.${HOSTNAME}`). Traefik is a MetalLB `LoadBalancer` at `${METALLB_TRAEFIK_IP}`. See `infrastructure/homelab/dns/pihole-ingressroute.yaml` for the canonical pattern.

### Dependency management

Helm chart versions are managed by **Renovate**, which runs on weekends and opens PRs for `HelmRelease` version bumps across `clusters/`, `infrastructure/`, and `apps/`.

### Hardware constraints

All images must support **ARM64** (Raspberry Pi 4B). Verify ARM64 availability before pinning any image. All workloads must declare requests and limits, and storage limits (if applicable).
