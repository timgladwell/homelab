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

**CI runs this same pipeline** on every pull request and on pushes to `main` and `stable` (`.github/workflows/validate.yml`), so it is enforced regardless of who opened the PR — including Renovate and Dependabot, whose PRs never run the local git hooks. Tool versions are pinned in the workflow; `FLUX_VERSION` deliberately tracks what the clusters run rather than the newest release.

After any change to manifests, run the full validation pipeline from the repo root:

```bash
./scripts/validate-k3s.sh
```

This runs ten steps in order, **per site** (Akron, Eastbank — each reconciles a different subset of the repo, see Directory layout below). Sites and their layers are discovered automatically, see *How validation discovers what to build*:
1. **YAML lint** — `yamllint` against all files (ignores each site's `flux-system/` and `*.sops.yaml`)
2. **Flux build** — `flux build kustomization --dry-run` for each Flux Kustomization, for each site
3. **Kustomize build** — `kustomize build` of the site's entry point and each of its layers, concatenated into `$K3S_BUILD_DIR/k3s-built-<site>.yaml`
4. **Schema validation** — `kubeconform -summary` against each site's built output
5. **Best practices** — `kube-score score` against each site's built output
6. **Security scan** — `trivy config ./ --severity HIGH,CRITICAL` (whole repo, not per-site)
9. **Dependabot coverage** — every directory with a pinned `image:` must be listed in `.github/dependabot.yml`, and every listed directory must exist (whole repo, not per-site)
10. **Secrets encrypted** — every `*secret*.yaml` tracked by git must have `sops:` metadata and `ENC[]` values (whole repo, not per-site)
7. **Variable references** — every `${VAR}` in each site's build output must be defined in that site's `cluster-vars.yaml`
8. **Policy** — `conftest test` against each site's built output using policies in `policy/`

Step 2 gates steps 3–5 and 7–8. Step 3 additionally gates steps 4, 5, 7, and 8. Steps 1, 6, 9 and 10 always run independently.

You can also check a specific kustomization in isolation:

```bash
kustomize build sites/akron/infrastructure/
kustomize build sites/eastbank/infrastructure/
kustomize build sites/akron/monitoring/
kustomize build base/dns/
```

## Secrets

All secrets follow the `*secret.sops.yaml` naming convention and must be SOPS-encrypted before committing. Enforced twice: a local pre-commit hook (`scripts/setup-git-hooks.sh`) and validation step 10, which checks the whole tree in CI and cannot be skipped with `--no-verify`. `.sops.yaml` scopes keys by site directory: `sites/akron/**` encrypts to Akron's age key, `sites/eastbank/**` to Eastbank's. A site's Flux can only decrypt its own secrets, and the rule needs no per-file exceptions.

To create or edit a secret:

```bash
# Edit (decrypt → edit → re-encrypt in place)
./scripts/secrets-helper.sh edit sites/akron/monitoring/grafana-secret.sops.yaml

# Encrypt a plaintext file in place
./scripts/secrets-helper.sh encrypt <file>

# View without saving
./scripts/secrets-helper.sh view <file>
```

Requires `SOPS_AGE_KEY_FILE` to point to an age private key that can decrypt the secret (defaults to `~/.config/sops/age/keys.txt`). Each secret is encrypted to the key of the site directory it lives under: `sites/akron/**` uses Akron's key, `sites/eastbank/**` uses Eastbank's. There are no shared secrets — if you find yourself wanting one in `base/`, it's a site value in the wrong layer.

## Architecture

Three layers, composed many-to-many:

| Layer | Directory | Contains |
|---|---|---|
| **Component** | `base/<component>/` | Site-agnostic definitions. **No secrets, no site values** — anything that differs per site is a `${VAR}` placeholder. |
| **Site** | `sites/<site>/<layer>/` | This site's secrets, patches, and the list of `base/` components it wants. One subdirectory per Flux Kustomization. |
| **Entry point** | `clusters/<site>/` | Flux bootstrap manifests, `cluster-vars.yaml`, and the Flux `Kustomization` objects pointing at `sites/<site>/<layer>/`. |

The rule that makes this work: **`base/` never contains anything site-specific.** If a site would need to delete or override something in `base/`, that thing belongs in `sites/` instead. A `$patch: delete` against `base/` means the layering is wrong.

- Independent single-node K3s sites, each managed with **Flux CD + Kustomize + Helm**, sharing this one repo (Flux's standard multi-cluster monorepo pattern — no cluster federation, no shared control plane):
  - **Akron** (local, 8GB RAM) — every layer: shared infrastructure + Akron-only monitoring. Deploys first.
  - **Eastbank** (remote, 8GB RAM) — infrastructure, infrastructure-config, dns-config. No monitoring.
  - **Lottage** (remote, 2GB RAM) — **out of scope**, scaffolding removed until the hardware is upgraded. Re-add by copying `sites/eastbank/` and `clusters/eastbank/`.
- The local development machine is not connected to any site. All commands are executed on the server via SSH session.
- **Rollout gating (Akron first):** Akron's Flux `GitRepository` watches `main`. Eastbank's watches a `stable` branch. After merging to `main` and confirming Akron is healthy, promote by opening a PR from `main` into `stable`. A GitHub ruleset on `stable` blocks direct pushes and merge/squash commits — only "Rebase and merge" is permitted, so `stable`'s history stays an unaltered subset of `main`'s. There is no automatic cross-cluster gate — this is a manual, explicit step.

### Directory layout

```
base/                            # Site-agnostic components. No secrets. No site values.
  dns/                           # PiHole + Unbound
  traefik/                       # Ingress controller
  metallb/                       # L2 load balancer
  system-upgrade-controller/
  coredns-config/                # k3s CoreDNS custom stub (needs no CRDs, but grouped with config)
  metallb-config/                # MetalLB IP pools (needs MetalLB CRDs)
  traefik-routes/                # PiHole IngressRoute (needs Traefik CRDs)
  pihole-sync/                   # Syncs DNS blocklists into each site's own local PiHole

sites/<site>/                    # Everything specific to one K3s cluster
  infrastructure/                # kustomization.yaml picking base components + this site's pihole-secret
  infrastructure-config/         # kustomization.yaml picking the CRD-dependent base components
  dns-config/                    # kustomization.yaml picking base/pihole-sync + this site's pihole-clients.yaml
  monitoring/                    # Akron only: Prometheus + Grafana + Loki + Alloy + Unpoller (+ its secrets)

clusters/<site>/                 # Flux entry point — managed by the flux-system Kustomization
  flux-system/                   # Flux's own manifests (managed by flux bootstrap, do not edit)
  flux-system-local/             # Patches applied over flux-system/ (kube-score ignores, etc.)
  cluster-vars.yaml              # Per-site ConfigMap (DNS_DOMAIN, HOSTNAME, METALLB_*, NODE_IP) injected via postBuild.substituteFrom
  infrastructure.yaml            # Flux Kustomization -> sites/<site>/infrastructure
  monitoring.yaml                # Akron only -> sites/akron/monitoring
  infrastructure-config.yaml     # -> sites/<site>/infrastructure-config
  dns-config.yaml                # -> sites/<site>/dns-config

```

### Why `cluster-vars.yaml` lives in `clusters/`, not `sites/`

It is site-specific, so `sites/<site>/` looks like the obvious home. The distinction that decides it is *who reconciles it*:

- Everything under `sites/<site>/<layer>/` is reconciled **by** a layer's own Flux `Kustomization`, and is an output — manifests that get applied.
- `cluster-vars.yaml` is reconciled by the `flux-system` Kustomization itself, before any layer runs, and is an **input to** every other layer via `postBuild.substituteFrom`. A layer cannot supply the variables that layer is substituted with.

So the rule is: **`clusters/<site>/` is what Flux needs in order to reconcile this site** — its bootstrap manifests, its credentials, its entry point, and its identity. **`sites/<site>/` is what this site deploys.** `cluster-vars` is identity, not deployment.

There is also a mechanical reason. `clusters/<site>/kustomization.yaml` would have to reach into `sites/<site>/` to pick the file up, and kustomize's load restrictions only allow crossing directories via a directory that has its own `kustomization.yaml` — so `sites/<site>/` would need a top-level one whose shape differs from every per-layer one beneath it. Cost with no benefit.

**Renaming a Flux `Kustomization` object is destructive.** `flux-system` prunes the old name and cascade-deletes everything it owned, PVCs included. Change `spec.path` freely; treat `metadata.name` as load-bearing.

**Two different "per-site" concepts coexist — don't confuse them:**
- **Per-K3s-cluster** — `sites/akron/`, `sites/eastbank/`. One directory per physical cluster.
- **Per-UniFi-site** — `sites/akron/monitoring/unpoller/{akron,eastbank,lottage}/`. These are *all deployed on Akron*, polling each remote UniFi controller over the Site Magic VPN (base + overlay + `nameSuffix`). The `lottage` one is alive and correct even though Lottage's K3s cluster no longer exists here.

### How validation discovers what to build

Nothing is hand-listed. `scripts/validate/lib-sites.sh` derives:

- **the site list** from the `sites/*/` directories, and
- **each site's layers** by reading `metadata.name` and `spec.path` straight out of the Flux `Kustomization` objects in `clusters/<site>/*.yaml`.

So the pipeline validates the paths Flux will actually reconcile. A `spec.path` pointing at a directory that doesn't exist fails at step 2 instead of on the cluster after merge. Adding a site or a layer requires no changes to any validation script.

Step 3 assembles each site's complete manifest set the same way Flux does — `kustomize build clusters/<site>` for the bootstrap manifests and Kustomization objects, plus one build per layer — into `$K3S_BUILD_DIR/k3s-built-<site>.yaml` for steps 4, 5, 7 and 8 to consume.

### Reconciliation flow

**Akron** (watches `main`) — `clusters/akron/` via the `flux-system` Kustomization, then:
1. `infrastructure` → `sites/akron/infrastructure` — SOPS decryption + `cluster-vars` substitution
2. `monitoring` → `sites/akron/monitoring` — depends on `infrastructure`
3. `infrastructure-config` → `sites/akron/infrastructure-config` — depends on `infrastructure`
4. `dns-config` → `sites/akron/dns-config` — depends on `infrastructure`

**Eastbank** (watches `stable`) — `clusters/eastbank/` → `infrastructure` (`sites/eastbank/infrastructure`) → `infrastructure-config` and `dns-config`, both depending on `infrastructure`.

### Variable substitution

Each site's `cluster-vars.yaml` defines its own `${DNS_DOMAIN}`, `${HOSTNAME}`, `${METALLB_ADDRESS_RANGE}`, `${METALLB_TRAEFIK_IP}`, `${METALLB_PIHOLE_IP}`, `${NODE_IP}`. Use these placeholders directly in `base/` manifests — Flux substitutes them at reconcile time via `postBuild.substituteFrom`, from that site's own ConfigMap only (there is no cross-site fallback).

Plain `kustomize build` does not perform this substitution, so the validation pipeline will always contain `${VAR}` literals in its output. Validation step 7 catches any `${VAR}` reference not defined in that site's `cluster-vars.yaml`.

**When adding a new variable:** add it to every site's `cluster-vars.yaml` that reconciles the manifest using it, before (or in the same PR as) that manifest.

### Adding a component

Layers are grouped by **reconcile semantics, not by namespace** — `infrastructure-config` spans `metallb-system` and `kube-system`, and `dns-config` exists separately from it because a gravity rebuild needs `force: true` and a 20m timeout, not because PiHole is in the `dns` namespace.

**Shared across sites** (the usual case):
1. Create `base/<component>/` with a `kustomization.yaml` listing its resources. Use `${VAR}` for anything site-specific; put no secrets here.
2. Add `- ../../../base/<component>` to each `sites/<site>/<layer>/kustomization.yaml` that should get it — `infrastructure/` for plain resources, `infrastructure-config/` if it needs CRDs the infrastructure layer installs.
3. No changes to `clusters/` or the validation scripts.

Opting a site out is just *not adding the line* — there is no delete-patch pattern, by design.

**Specific to one site** (e.g. Akron's monitoring stack): create it under `sites/<site>/<layer>/` and add it to that layer's `kustomization.yaml`. Nothing else changes.

**There is no `apps` layer right now**, but it is expected back — NetworkOptimizer was deleted to be re-added fresh on its new multi-UniFi-site version. Restore it with the steps in *Adding a new top-level Flux Kustomization* below, creating `sites/akron/apps/` with a namespace and the app.

When it returns, **leave `dns-config`'s `dependsOn` on `infrastructure`**. It used to depend on `apps`, but that was incidental ordering — `pihole-sync` talks to `pihole-web`, which the infrastructure layer owns. It has no dependency on apps in either direction.

**If a component needs a per-site secret**, put the Secret in `sites/<site>/<layer>/` and list it alongside the base component. Never in `base/`.

**If a component needs a per-site *config file*** (as `pihole-sync` does for its client list), keep the global part in `base/` and merge the site's part into the same generated ConfigMap from `sites/<site>/<layer>/`:

```yaml
configMapGenerator:
  - name: <same-name-as-base>
    behavior: merge
    files:
      - <site-specific>.yaml
```

Kustomize's load restrictions block a `configMapGenerator` from reading files outside its own root, so this merge — not a path reference — is how a site contributes a file. Back it with a conftest policy asserting the merged key exists (see `policy/pihole_sync_clients.rego`); a silently-absent site file usually reads as "desired state is empty", which is a delete.

### Adding a new top-level Flux Kustomization

Needed only when resources require different `dependsOn` ordering, SOPS config, or reconcile interval. Rare.

1. Create `sites/<site>/<layer>/` with a `kustomization.yaml`
2. Add to `clusters/<site>/`: the Flux `Kustomization` object (use `path: ./sites/<site>/<layer>`, matching Flux's own `gotk-sync.yaml` convention), plus an entry in `clusters/<site>/kustomization.yaml`
Validation picks it up automatically — there is no list to update.

### Removing a Kustomization

- **Component within a layer:** remove its line from `sites/<site>/<layer>/kustomization.yaml`. Flux's `prune: true` deletes the resources on the next reconciliation. **Check what those resources own first** — pruning a Kustomization cascades to its PVCs.
- **Top-level Flux Kustomization:** remove its file from `clusters/<site>/` and its entry in `clusters/<site>/kustomization.yaml`. Nothing in the validation pipeline needs touching.

### Moving resources between Kustomizations

**This is not safe by default and has caused real data loss.** Each Flux `Kustomization` with `prune: true` tracks its own inventory of what it last applied. If a resource (e.g. a whole component's directory) moves from one Kustomization's manifest into a different one, the *source* Kustomization's inventory still remembers owning it from its last reconcile — on its next reconcile it sees the resource is no longer in its manifest and **prunes (deletes) it**, racing against the *destination* Kustomization trying to create it fresh. For a `Namespace`, that prune cascade-deletes everything inside it, including PVCs.

This happened when `infrastructure/homelab/monitoring/` moved into its own `infrastructure/akron-only` Kustomization: the old `infrastructure` Kustomization pruned `Namespace/monitoring` right as the new one tried to recreate it, wiping Prometheus/Loki's PVC-backed history. Grafana's dashboards survived only because they're provisioned from ConfigMaps in git, not stored in a PVC.

**A `kustomize build` diff of rendered manifests cannot catch this.** The YAML content is identical either way — same Namespace, same resources — the risk is entirely in live Flux reconciliation/inventory state, not in what's committed to git. Don't treat a manifest diff as proof that a Kustomization-boundary change is safe.

Before moving a resource across a Kustomization boundary, check whether it's a `Namespace` or anything with PVC-backed state. If so, pick one:
- Accept reprovisioning/data loss explicitly if it's acceptable (e.g. stateless resources, or data that isn't valuable).
- Temporarily set `prune: false` on the source Kustomization for the PR that does the move, then restore it once the source's inventory no longer references the moved resource (its next successful reconcile after the resource is gone from its manifest).
- Sequence the change so the source Kustomization reconciles (and updates its inventory) before the destination Kustomization's first apply, rather than merging both in a way that lets them race.

### Ingress pattern

Apps are exposed via Traefik `IngressRoute` CRs using subdomain routing (`<app>.${HOSTNAME}`). Traefik is a MetalLB `LoadBalancer` at `${METALLB_TRAEFIK_IP}`. See `base/traefik-routes/pihole-ingressroute.yaml` for the canonical pattern.

### Dependency management

Two tools, non-overlapping scopes:

- **Renovate** (`renovate.json`) — Helm chart versions in `HelmRelease` resources. Scoped to the `flux` manager, runs on weekends.
- **Dependabot** (`.github/dependabot.yml`) — container images pinned directly in manifests (`pihole`, `unbound`, `unbound-exporter`, `python`, `system-upgrade-controller`), plus GitHub Actions. Its docker ecosystem does read `image:` fields out of Kubernetes YAML.

Dependabot lists explicit directories, so **a component that moves or gains an image needs a `.github/dependabot.yml` entry**. Validation step 9 enforces this in both directions — it exists because this config silently went stale when directories last moved, and nobody noticed images had stopped being updated.

Flux's own controller images (`clusters/*/flux-system/`) are excluded from both: they are upgraded with the Flux CLI, see `docs/runbooks/flux-upgrades.md`.

### Hardware constraints

All images must support **ARM64** (Raspberry Pi 4B). Verify ARM64 availability before pinning any image. All workloads must declare requests and limits, and storage limits (if applicable).
