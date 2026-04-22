# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Design Principles

- **Enterprise best practices always.** Treat this as a high-scale production K8s environment in terms of design, structure, and operational patterns, even though it runs on a single node. This means proper namespace isolation, resource limits, health checks, RBAC, and GitOps workflows.
- **GitOps is the single source of truth.** All cluster state is declared in this repository. Manual `kubectl apply` or imperative changes are not acceptable. Everything flows through Flux CD reconciliation.
- **Security by default.** No secrets in the repo (use SOPS/Age encryption). Pre-commit hooks enforce this. All manifests should follow least-privilege principles.
- **Keep it simple.** Avoid over-engineering. The RPi has limited resources (300m CPU / 150Mi memory is a typical ceiling for a single workload). Don't add abstractions, features, or tooling that aren't needed yet.

## Development Principles

- All changes to this repo go through PRs - do not work on the `main` branch directly

### Validation

After any change to manifests, run the full validation pipeline from the repo root:

```bash
./scripts/validate-k3s.sh
```

This runs five steps in order:
1. **YAML lint** — `yamllint` against all files (ignores `flux-system/` and `*.sops.yaml`)
2. **Kustomize build** — `kustomize build ./clusters/homelab/flux-system` → `/tmp/k3s-built.yaml`
3. **Schema validation** — `kubeconform -summary` against the built output
4. **Best practices** — `kube-score score` against the built output
5. **Security scan** — `trivy config ./ --severity HIGH,CRITICAL`

Steps 3 and 4 are skipped if step 2 fails. All steps run independently.

You can also check a specific kustomization in isolation:

```bash
kustomize build infrastructure/homelab/
kustomize build infrastructure/homelab/monitoring/
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

This is a single-node K3s homelab managed with **Flux CD + Kustomize + Helm**.

### Directory layout

```
clusters/homelab/          # Flux entry point — bootstraps everything
  flux-system/             # Flux's own manifests (managed by flux bootstrap)
  infrastructure.yaml      # Flux Kustomization: reconciles infrastructure/homelab/
  apps.yaml                # Flux Kustomization: reconciles apps/homelab/ (depends on infrastructure)
  cluster-vars.yaml        # ConfigMap injected into all manifests via postBuild.substituteFrom

infrastructure/homelab/    # Cluster infrastructure (namespaces, Helm releases, CRDs)
  kustomization.yaml       # Add new infrastructure subdirs here
  dns/                     # PiHole + Unbound
  traefik/                 # Ingress controller
  metallb/                 # L2 load balancer
  metallb-config/          # MetalLB IP pools
  monitoring/              # Prometheus + Grafana + Loki + OTel (in progress)
  system-upgrade-controller/

apps/homelab/              # User-facing applications (deployed after infrastructure)
```

### Reconciliation flow

1. Flux watches the Git repo and reconciles `clusters/homelab/flux-system/`
2. `infrastructure.yaml` reconciles `infrastructure/homelab/` with SOPS decryption and `cluster-vars` substitution
3. `apps.yaml` reconciles `apps/homelab/` only after infrastructure is healthy (`dependsOn`)

### Variable substitution

`cluster-vars.yaml` defines `${DNS_DOMAIN}`, `${HOSTNAME}`, `${METALLB_ADDRESS_RANGE}`, `${METALLB_TRAEFIK_IP}`, `${METALLB_PIHOLE_IP}`. Use these placeholders directly in manifests — Flux substitutes them at reconcile time.

### Adding infrastructure components

1. Create a subdirectory under `infrastructure/homelab/<component>/`
2. Add a `kustomization.yaml` listing that component's resources
3. Add `- ./<component>` to `infrastructure/homelab/kustomization.yaml`
4. Wire Helm charts via `HelmRepository` + `HelmRelease` resources in that directory

### Ingress pattern

Apps are exposed via Traefik `IngressRoute` CRs using subdomain routing (`<app>.${HOSTNAME}`). Traefik is a MetalLB `LoadBalancer` at `${METALLB_TRAEFIK_IP}`. See `infrastructure/homelab/dns/pihole-ingressroute.yaml` for the canonical pattern.

### Dependency management

Helm chart versions are managed by **Renovate**, which runs on weekends and opens PRs for `HelmRelease` version bumps across `clusters/`, `infrastructure/`, and `apps/`.

### Hardware constraints

All images must support **ARM64** (Raspberry Pi 4B). Verify ARM64 availability before pinning any image. All workloads must declare requests and limits, and storage limits (if applicable).
