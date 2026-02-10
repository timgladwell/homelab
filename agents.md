# Homelab - Agent Guidelines

## Project Overview

This is a K3s-based homelab deployed on a single Raspberry Pi 4B (8GB RAM, 256GB SSD, Raspberry Pi OS Lite 64-bit). The project serves as a learning platform for enterprise-scope Kubernetes design and deployment, CI/CD tooling, and Linux administration.

**Current scope:** Internal-only deployment. If/when external access is needed, the project will go through a formal security revision before exposing anything externally.

**Use cases:**
- Internal services (PiHole/Unbound DNS, etc.)
- Testing and staging environment for custom apps and websites
- Kubernetes and DevOps skill development

## Design Principles

- **Enterprise best practices always.** Treat this as a high-scale production K8s environment in terms of design, structure, and operational patterns, even though it runs on a single node. This means proper namespace isolation, resource limits, health checks, RBAC, and GitOps workflows.
- **GitOps is the single source of truth.** All cluster state is declared in this repository. Manual `kubectl apply` or imperative changes are not acceptable. Everything flows through Flux CD reconciliation.
- **Security by default.** No secrets in the repo (use SOPS/Age encryption). Pre-commit hooks enforce this. All manifests should follow least-privilege principles.
- **Keep it simple.** Avoid over-engineering. The RPi has limited resources (300m CPU / 150Mi memory is a typical ceiling for a single workload). Don't add abstractions, features, or tooling that aren't needed yet.

## Repository Structure

```
clusters/homelab/           # Cluster-level Flux entrypoints
  flux-system/              # Flux bootstrap (DO NOT hand-edit gotk-components.yaml)
  infrastructure.yaml       # Flux Kustomization: deploys infrastructure/homelab/
  apps.yaml                 # Flux Kustomization: deploys apps/homelab/ (depends on infrastructure)

infrastructure/homelab/     # Infrastructure-tier workloads (ingress, DNS, etc.)
  traefik/                  # Ingress controller (Helm-based)

apps/homelab/               # Application-tier workloads
  podinfo/                  # Demo/test application

scripts/                    # Operational scripts (validation, secrets, git hooks)
docs/                       # Project documentation and specifications
```

**Deployment order is enforced:** Flux deploys `infrastructure` first (with `wait: true`), then `apps` only after infrastructure is healthy.

## Technology Stack

| Component | Tool | Notes |
|-----------|------|-------|
| Container orchestration | K3s | Lightweight Kubernetes |
| GitOps / CD | Flux CD v2 | Watches `main` branch |
| Ingress | Traefik | Deployed via HelmRelease |
| Configuration | Kustomize + Helm | Kustomize for structure, Helm for upstream charts |
| Secrets encryption | SOPS + Age | Encrypts `data`/`stringData` fields in `*secret.sops.yaml` files |
| Validation | yamllint, kubeconform, kube-score, trivy | Run via `scripts/validate-k3s.sh` |

## API Versions

Use the latest stable API versions at all times:

| API Group | Version |
|-----------|---------|
| `source.toolkit.fluxcd.io` | `v1` |
| `helm.toolkit.fluxcd.io` | `v2` |
| `kustomize.toolkit.fluxcd.io` | `v1` |
| `kustomize.config.k8s.io` | `v1beta1` |
| Core Kubernetes APIs | `v1`, `apps/v1`, `networking.k8s.io/v1`, etc. |

Do not use beta API versions unless the stable version does not yet exist for that resource.

## Routing Strategy

All web apps are namespaced under `/<app-name>` to avoid route collisions. Standard ports only (80/443) — no custom ports to remember.

| Route Type | Pattern | Example |
|------------|---------|---------|
| IP-based | `http://<IP>/<app-name>` | `http://192.168.1.100/traefik` |
| Domain-based | `http://<DOMAIN>/<app-name>` | `http://homelab.local/traefik` |
| Subdomain-based | `http://<app-name>.<DOMAIN>` | `http://traefik.homelab.local` |

**Implementation requirements for each app:**

1. **Middleware** (`middleware.yaml`): StripPrefix middleware to remove `/<app-name>` prefix before forwarding to the backend
2. **Ingress/IngressRoute**: Route `/<app-name>` to the service, applying the strip-prefix middleware
3. **Default behavior preserved**: Apps should show their default UI/behavior at the namespaced route (e.g., Traefik redirects to `/dashboard`, PiHole shows 403 directing to `/admin`)

**Example files for a new app:**
```
apps/homelab/myapp/
  kustomization.yaml      # Lists all resources
  middleware.yaml         # StripPrefix for /myapp
  ingress.yaml            # Routes /myapp to service
  ...                     # App-specific resources
```

## Adding a New Application

1. Create a directory under `apps/homelab/<app-name>/`
2. Add a `kustomization.yaml` (apiVersion: `kustomize.config.k8s.io/v1beta1`) listing all resources
3. Add the app's source (GitRepository or HelmRepository) and deployment (Kustomization or HelmRelease)
4. **Add routing:** Create `middleware.yaml` (StripPrefix) and `ingress.yaml` following the routing strategy above
5. Register the new directory in `apps/homelab/kustomization.yaml`
6. If the app needs infrastructure (e.g., a new namespace, CRDs), add those under `infrastructure/homelab/`

## Adding New Infrastructure

1. Create a directory under `infrastructure/homelab/<component>/`
2. Add a `kustomization.yaml` listing all resources
3. Register the new directory in `infrastructure/homelab/kustomization.yaml`
4. Infrastructure should create its own namespace rather than relying on pre-existing ones

## Working with Secrets

- Secret files must match the pattern `*secret.sops.yaml` and be encrypted with SOPS/Age before committing
- The pre-commit hook will block unencrypted secrets — do not bypass it
- Use `scripts/secrets-helper.sh` for encrypt/decrypt/edit/view/rotate operations
- Never store plaintext secrets, internal IPs, domains, or credentials in the repository

## Validation

Before committing changes, run `scripts/validate-k3s.sh` to check:
- YAML syntax (yamllint)
- Kustomize build success
- Kubernetes schema conformance (kubeconform)
- Best-practice scoring (kube-score)
- Security vulnerabilities (trivy)

## Resource Constraints

This runs on a Raspberry Pi 4B. All workloads must specify resource requests and limits. Typical ceilings per workload:
- CPU: 100m request / 300m limit
- Memory: 50Mi request / 150Mi limit
- Total storage allocation must not exceed 150GB (local-path provisioner)

## Conventions

- **Namespaces:** Each logical service group gets its own namespace (e.g., `traefik`, `networking`)
- **Labels:** Follow Kubernetes recommended labels (`app.kubernetes.io/name`, `app.kubernetes.io/part-of`, etc.)
- **YAML style:** Max line length 120 chars (see `.yamllint`)
- **Commit messages:** Short imperative subject line, optional body explaining the "why"
- **Flux intervals:** Use `1m` for git sync, `5m`-`30m` for reconciliation depending on how frequently the source changes
