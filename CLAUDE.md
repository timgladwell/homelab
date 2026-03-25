# CLAUDE.md

## Full Guidelines

Read `agents.md` for comprehensive project guidelines. This file summarises the most operationally critical points.

## Validation

Before committing, run:

```bash
scripts/validate-k3s.sh
```

This runs yamllint, kustomize build, kubeconform, kube-score, and trivy in sequence.

## Architecture

K3s homelab on a single Raspberry Pi 4B, managed entirely via GitOps (Flux CD watches `main`). **No `kubectl apply` or imperative changes** — everything flows through Flux reconciliation.

**Deployment tiers:**
- `clusters/homelab/` — Flux entrypoints; `infrastructure.yaml` deploys before `apps.yaml` (enforced via `dependsOn`)
- `infrastructure/homelab/` — Ingress (Traefik via HelmRelease), DNS, and other cluster-level services
- `apps/homelab/` — Application workloads

Each tier has its own `kustomization.yaml` that lists its subdirectories.

## Routing

All apps are path-namespaced under `/<app-name>` on standard ports (80/443 only, via `hostNetwork: true` on Traefik). Three route patterns are supported: IP-based, domain-based, and subdomain-based.

**Preferred:** Configure the app to serve from its base path natively (e.g., `--api.basePath=/traefik`). Use `addPrefix` middleware for subdomain routes. Fall back to `StripPrefix` middleware only if native base path isn't available.

New apps need: `middleware.yaml`, `ingress.yaml`, and registration in `apps/homelab/kustomization.yaml`.

## Secrets

Secret files must match `*secret.sops.yaml` and be SOPS/Age-encrypted before committing. The pre-commit hook blocks unencrypted secrets — do not bypass it.

```bash
scripts/secrets-helper.sh   # encrypt / decrypt / edit / view / rotate
```

## API Versions

| API Group | Version |
|-----------|---------|
| `source.toolkit.fluxcd.io` | `v1` |
| `helm.toolkit.fluxcd.io` | `v2` |
| `kustomize.toolkit.fluxcd.io` | `v1` |
| `kustomize.config.k8s.io` | `v1beta1` |

Do not use beta API versions unless a stable version does not yet exist.

## Resource Limits (RPi 4B)

All workloads must declare requests and limits. Typical ceiling per workload:
- CPU: 100m request / 300m limit
- Memory: 50Mi request / 150Mi limit

## Key Conventions

- Each service group gets its own namespace; namespaces are created by the workload manifests, not pre-existing
- `flux-system/gotk-components.yaml` is auto-generated — do not hand-edit it
- Flux git sync interval: `1m`; reconciliation: `5m`–`30m` depending on change frequency
- Commit messages: short imperative subject, optional body explaining the "why"
